"""
Views handling read (GET) requests for the Discussion tab and inline discussions.
"""

import logging
from functools import wraps
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import Http404, HttpResponseForbidden, HttpResponseServerError
from django.shortcuts import redirect, render
from django.template.context_processors import csrf
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import get_language_bidi
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods
from edx_django_utils.monitoring import function_trace
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from web_fragments.fragment import Fragment
from xmodule.modulestore.django import modulestore

import lms.djangoapps.discussion.django_comment_client.utils as utils
import openedx.core.djangoapps.django_comment_common.comment_client as cc
from openedx.core.djangoapps.django_comment_common.models import has_permission
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from common.djangoapps.util.json_request import JsonResponse, expect_json
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.courseware.views.views import CourseTabView
from lms.djangoapps.discussion.config.settings import is_forum_daily_digest_enabled
from lms.djangoapps.discussion.django_comment_client.base.views import track_thread_viewed_event
from lms.djangoapps.discussion.django_comment_client.constants import TYPE_ENTRY
from lms.djangoapps.discussion.django_comment_client.utils import (
    add_courseware_context,
    course_discussion_division_enabled,
    extract,
    get_group_id_for_comments_service,
    get_group_id_for_user,
    is_commentable_divided,
    strip_none
)
from lms.djangoapps.discussion.exceptions import TeamDiscussionHiddenFromUserException
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE
from lms.djangoapps.experiments.utils import get_experiment_user_metadata_context
from lms.djangoapps.teams import api as team_api
from openedx.core.djangoapps.discussions.utils import (
    available_division_schemes,
    get_discussion_categories_ids,
    get_divided_discussions,
    get_group_names_by_id
)
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    CourseDiscussionSettings,
    Role
)
from openedx.core.djangoapps.django_comment_common.utils import ThreadContext
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from openedx.features.course_duration_limits.access import generate_course_expired_fragment

User = get_user_model()
log = logging.getLogger("edx.discussions")

THREADS_PER_PAGE = 20
INLINE_THREADS_PER_PAGE = 20
PAGES_NEARBY_DELTA = 2

BOOTSTRAP_DISCUSSION_CSS_PATH = 'css/discussion/lms-discussion-bootstrap.css'
TEAM_PERMISSION_MESSAGE = _("Access to this discussion is restricted to team members and staff.")


def make_course_settings(course, user, include_category_map=True):
    """
    Generate a JSON-serializable model for course settings, which will be used to initialize a
    DiscussionCourseSettings object on the client.
    """
    course_discussion_settings = CourseDiscussionSettings.get(course.id)
    group_names_by_id = get_group_names_by_id(course_discussion_settings)
    course_setting = {
        'is_discussion_division_enabled': course_discussion_division_enabled(course_discussion_settings),
        'allow_anonymous': course.allow_anonymous,
        'allow_anonymous_to_peers': course.allow_anonymous_to_peers,
        'groups': [
            {"id": str(group_id), "name": group_name} for group_id, group_name in group_names_by_id.items()
        ]
    }
    if include_category_map:
        course_setting['category_map'] = utils.get_discussion_category_map(course, user)

    return course_setting


def get_threads(request, course, user_info, discussion_id=None, per_page=THREADS_PER_PAGE):
    """
    This may raise an appropriate subclass of cc.utils.CommentClientError
    if something goes wrong, or ValueError if the group_id is invalid.

    Arguments:
        request (WSGIRequest): The user request.
        course (CourseBlockWithMixins): The course object.
        user_info (dict): The comment client User object as a dict.
        discussion_id (unicode): Optional discussion id/commentable id for context.
        per_page (int): Optional number of threads per page.

    Returns:
        (tuple of list, dict): A tuple of the list of threads and a dict of the
            query parameters used for the search.

    """
    default_query_params = {
        'page': 1,
        'per_page': per_page,
        'sort_key': 'activity',
        'text': '',
        'course_id': str(course.id),
        'user_id': request.user.id,
        'context': ThreadContext.COURSE,
        'group_id': get_group_id_for_comments_service(request, course.id, discussion_id),  # may raise ValueError
    }

    # If provided with a discussion id, filter by discussion id in the
    # comments_service.
    if discussion_id is not None:
        default_query_params['commentable_id'] = discussion_id
        # Use the discussion id/commentable id to determine the context we are going to pass through to the backend.
        if team_api.get_team_by_discussion(discussion_id) is not None:
            default_query_params['context'] = ThreadContext.STANDALONE

        _check_team_discussion_access(request, course, discussion_id)

    if not request.GET.get('sort_key'):
        # If the user did not select a sort key, use their last used sort key
        default_query_params['sort_key'] = user_info.get('default_sort_key') or default_query_params['sort_key']

    elif request.GET.get('sort_key') != user_info.get('default_sort_key'):
        # If the user clicked a sort key, update their default sort key
        cc_user = cc.User.from_django_user(request.user)
        cc_user.default_sort_key = request.GET.get('sort_key')
        cc_user.save(params={"course_id": str(course.id)})

    #there are 2 dimensions to consider when executing a search with respect to group id
    #is user a moderator
    #did the user request a group

    query_params = default_query_params.copy()
    query_params.update(
        strip_none(
            extract(
                request.GET,
                [
                    'page',
                    'sort_key',
                    'text',
                    'commentable_ids',
                    'flagged',
                    'unread',
                    'unanswered',
                ]
            )
        )
    )
    paginated_results = cc.Thread.search(query_params)
    threads = paginated_results.collection

    # If not provided with a discussion id, filter threads by commentable ids
    # which are accessible to the current user.
    if discussion_id is None:
        discussion_category_ids = set(get_discussion_categories_ids(course, request.user))
        threads = [
            thread for thread in threads
            if thread.get('commentable_id') in discussion_category_ids
        ]

    for thread in threads:
        # patch for backward compatibility to comments service
        if 'pinned' not in thread:
            thread['pinned'] = False

    query_params['page'] = paginated_results.page
    query_params['num_pages'] = paginated_results.num_pages
    query_params['corrected_text'] = paginated_results.corrected_text

    return threads, query_params


def use_bulk_ops(view_func):
    """
    Wraps internal request handling inside a modulestore bulk op, significantly
    reducing redundant database calls.  Also converts the course_id parsed from
    the request uri to a CourseKey before passing to the view.
    """
    @wraps(view_func)
    def wrapped_view(request, course_id, *args, **kwargs):
        course_key = CourseKey.from_string(course_id)
        if course_key.deprecated:
            raise Http404
        with modulestore().bulk_operations(course_key):
            return view_func(request, course_key, *args, **kwargs)
    return wrapped_view


@login_required
@use_bulk_ops
def inline_discussion(request, course_key, discussion_id):
    """
    Renders JSON for DiscussionModules
    """
    with function_trace('get_course_and_user_info'):
        course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)
        cc_user = cc.User.from_django_user(request.user)
        user_info = cc_user.to_dict(course_key=str(course_key))

    try:
        with function_trace('get_threads'):
            threads, query_params = get_threads(
                request, course, user_info, discussion_id, per_page=INLINE_THREADS_PER_PAGE
            )
    except ValueError:
        return HttpResponseServerError('Invalid group_id')
    except TeamDiscussionHiddenFromUserException:
        return HttpResponseForbidden(TEAM_PERMISSION_MESSAGE)

    with function_trace('get_metadata_for_threads'):
        annotated_content_info = utils.get_metadata_for_threads(course_key, threads, request.user, user_info)

    with function_trace('determine_group_permissions'):
        is_staff = has_permission(request.user, 'openclose_thread', course.id)
        is_community_ta = utils.is_user_community_ta(request.user, course.id)
        course_discussion_settings = CourseDiscussionSettings.get(course.id)
        group_names_by_id = get_group_names_by_id(course_discussion_settings)
        course_is_divided = course_discussion_settings.division_scheme is not CourseDiscussionSettings.NONE

    with function_trace('prepare_content'):
        threads = [
            utils.prepare_content(
                thread,
                course_key,
                is_staff,
                is_community_ta,
                course_is_divided,
                group_names_by_id
            ) for thread in threads
        ]

    return utils.JsonResponse({
        'is_commentable_divided': is_commentable_divided(course_key, discussion_id),
        'discussion_data': threads,
        'user_info': user_info,
        'user_group_id': get_group_id_for_user(request.user, course_discussion_settings),
        'annotated_content_info': annotated_content_info,
        'page': query_params['page'],
        'num_pages': query_params['num_pages'],
        'roles': utils.get_role_ids(course_key),
        'course_settings': make_course_settings(course, request.user, False)
    })


def redirect_forum_url_to_new_mfe(request, course_id):
    """
    Returns the redirect link when user opens default discussion homepage
    """
    course_key = CourseKey.from_string(course_id)
    discussions_mfe_enabled = ENABLE_DISCUSSIONS_MFE.is_enabled(course_key)

    redirect_url = None
    if discussions_mfe_enabled:
        mfe_base_url = settings.DISCUSSIONS_MICROFRONTEND_URL
        redirect_url = f"{mfe_base_url}/{str(course_key)}"
    return redirect_url


@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@login_required
@use_bulk_ops
def forum_form_discussion(request, course_key):
    """
    Renders the main Discussion page, potentially filtered by a search query
    """
    course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)
    request.user.is_community_ta = utils.is_user_community_ta(request.user, course.id)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        user = cc.User.from_django_user(request.user)
        user_info = user.to_dict()

        try:
            unsafethreads, query_params = get_threads(request, course, user_info)  # This might process a search query
            is_staff = has_permission(request.user, 'openclose_thread', course.id)
            threads = [utils.prepare_content(
                thread, course_key, is_staff, request.user.is_community_ta
            ) for thread in unsafethreads]
        except cc.utils.CommentClientMaintenanceError:
            return HttpResponseServerError('Forum is in maintenance mode', status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ValueError:
            return HttpResponseServerError("Invalid group_id")

        with function_trace("get_metadata_for_threads"):
            annotated_content_info = utils.get_metadata_for_threads(course_key, threads, request.user, user_info)

        with function_trace("add_courseware_context"):
            add_courseware_context(threads, course, request.user)

        return utils.JsonResponse({
            'discussion_data': threads,   # TODO: Standardize on 'discussion_data' vs 'threads'
            'annotated_content_info': annotated_content_info,
            'num_pages': query_params['num_pages'],
            'page': query_params['page'],
            'corrected_text': query_params['corrected_text'],
        })
    else:
        redirect_url = redirect_forum_url_to_new_mfe(request, str(course.id))
        if redirect_url:
            return redirect(redirect_url)

        course_id = str(course.id)
        tab_view = CourseTabView()
        return tab_view.get(request, course_id, 'discussion')


def redirect_thread_url_to_new_mfe(request, course_id, thread_id):
    """
    Returns MFE url of the thread if the user is not privileged
    """
    course_key = CourseKey.from_string(course_id)
    discussions_mfe_enabled = ENABLE_DISCUSSIONS_MFE.is_enabled(course_key)
    redirect_url = None
    if discussions_mfe_enabled:
        mfe_base_url = settings.DISCUSSIONS_MICROFRONTEND_URL
        if thread_id:
            redirect_url = f"{mfe_base_url}/{str(course_key)}/posts/{thread_id}"
    return redirect_url


@require_GET
@login_required
@use_bulk_ops
def single_thread(request, course_key, discussion_id, thread_id):
    """
    Renders a response to display a single discussion thread.  This could either be a page refresh
    after navigating to a single thread, a direct link to a single thread, or an AJAX call from the
    discussions UI loading the responses/comments for a single thread.

    Depending on the HTTP headers, we'll adjust our response accordingly.
    """
    course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)
    request.user.is_community_ta = utils.is_user_community_ta(request.user, course.id)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        cc_user = cc.User.from_django_user(request.user)
        user_info = cc_user.to_dict(course_key=str(course_key))
        is_staff = has_permission(request.user, 'openclose_thread', course.id)

        try:
            _check_team_discussion_access(request, course, discussion_id)
        except TeamDiscussionHiddenFromUserException:
            return HttpResponseForbidden(TEAM_PERMISSION_MESSAGE)

        thread = _load_thread_for_viewing(
            request,
            course,
            discussion_id=discussion_id,
            thread_id=thread_id,
            raise_event=True,
        )

        with function_trace("get_annotated_content_infos"):
            annotated_content_info = utils.get_annotated_content_infos(
                course_key,
                thread,
                request.user,
                user_info=user_info
            )

        content = utils.prepare_content(thread.to_dict(), course_key, is_staff, request.user.is_community_ta)
        with function_trace("add_courseware_context"):
            add_courseware_context([content], course, request.user)

        return utils.JsonResponse({
            'content': content,
            'annotated_content_info': annotated_content_info,
        })
    else:
        redirect_url = redirect_thread_url_to_new_mfe(request, str(course.id), thread_id)
        if redirect_url:
            return redirect(redirect_url)

        course_id = str(course.id)
        tab_view = CourseTabView()
        return tab_view.get(request, course_id, 'discussion', discussion_id=discussion_id, thread_id=thread_id)


def _find_thread(request, course, discussion_id, thread_id):
    """
    Finds the discussion thread with the specified ID.

    Args:
        request: The Django request.
        course_id: The ID of the owning course.
        discussion_id: The ID of the owning discussion.
        thread_id: The ID of the thread.

    Returns:
        The thread in question if the user can see it, else None.
    """
    try:
        thread = cc.Thread.find(thread_id).retrieve(
            with_responses=request.headers.get('x-requested-with') == 'XMLHttpRequest',
            recursive=request.headers.get('x-requested-with') == 'XMLHttpRequest',
            user_id=request.user.id,
            response_skip=request.GET.get("resp_skip"),
            response_limit=request.GET.get("resp_limit")
        )
    except cc.utils.CommentClientRequestError:
        return None
    # Verify that the student has access to this thread if belongs to a course discussion block
    thread_context = getattr(thread, "context", "course")
    if thread_context == "course" and not utils.discussion_category_id_access(course, request.user, discussion_id):
        return None

    # verify that the thread belongs to the requesting student's group
    is_moderator = has_permission(request.user, "see_all_cohorts", course.id)
    course_discussion_settings = CourseDiscussionSettings.get(course.id)
    if is_commentable_divided(course.id, discussion_id, course_discussion_settings) and not is_moderator:
        user_group_id = get_group_id_for_user(request.user, course_discussion_settings)
        if getattr(thread, "group_id", None) is not None and user_group_id != thread.group_id:
            return None

    return thread


def _load_thread_for_viewing(request, course, discussion_id, thread_id, raise_event):
    """
    Loads the discussion thread with the specified ID and fires an
    edx.forum.thread.viewed event.

    Args:
        request: The Django request.
        course_id: The ID of the owning course.
        discussion_id: The ID of the owning discussion.
        thread_id: The ID of the thread.
        raise_event: Whether an edx.forum.thread.viewed tracking event should
                     be raised

    Returns:
        The thread in question if the user can see it.

    Raises:
        Http404 if the thread does not exist or the user cannot
        see it.
    """
    thread = _find_thread(request, course, discussion_id=discussion_id, thread_id=thread_id)
    if not thread:
        raise Http404
    if raise_event:
        track_thread_viewed_event(request, course, thread)
    return thread


def _create_base_discussion_view_context(request, course_key):
    """
    Returns the default template context for rendering any discussion view.
    """
    user = request.user
    cc_user = cc.User.from_django_user(user)
    user_info = cc_user.to_dict(course_key=str(course_key))
    course = get_course_with_access(user, 'load', course_key, check_if_enrolled=True)
    course_settings = make_course_settings(course, user)
    return {
        'csrf': csrf(request)['csrf_token'],
        'course': course,
        'user': user,
        'user_info': user_info,
        'staff_access': bool(has_access(user, 'staff', course)),
        'roles': utils.get_role_ids(course_key),
        'can_create_comment': has_permission(user, "create_comment", course.id),
        'can_create_subcomment': has_permission(user, "create_sub_comment", course.id),
        'can_create_thread': has_permission(user, "create_thread", course.id),
        'flag_moderator': bool(
            has_permission(user, 'openclose_thread', course.id) or
            has_access(user, 'staff', course)
        ),
        'course_settings': course_settings,
        'disable_courseware_js': True,
        'uses_bootstrap': True,
    }


def _get_discussion_default_topic_id(course):
    for topic, entry in course.discussion_topics.items():  # lint-amnesty, pylint: disable=unused-variable
        if entry.get('default') is True:
            return entry['id']


def _create_discussion_board_context(request, base_context, thread=None):
    """
    Returns the template context for rendering the discussion board.
    """
    context = base_context.copy()
    course = context['course']
    course_key = course.id
    thread_id = thread.id if thread else None
    discussion_id = thread.commentable_id if thread else None
    course_settings = context['course_settings']
    user = context['user']
    cc_user = cc.User.from_django_user(user)
    user_info = context['user_info']
    if thread:
        _check_team_discussion_access(request, course, discussion_id)
        # Since we're in page render mode, and the discussions UI will request the thread list itself,
        # we need only return the thread information for this one.
        threads = [thread.to_dict()]

        for thread in threads:  # lint-amnesty, pylint: disable=redefined-argument-from-local
            # patch for backward compatibility with comments service
            if "pinned" not in thread:
                thread["pinned"] = False
        thread_pages = 1
        root_url = reverse('forum_form_discussion', args=[str(course.id)])
    else:
        threads, query_params = get_threads(request, course, user_info)   # This might process a search query
        thread_pages = query_params['num_pages']
        root_url = request.path
    is_staff = has_permission(user, 'openclose_thread', course.id)
    is_community_ta = utils.is_user_community_ta(request.user, course.id)
    threads = [utils.prepare_content(thread, course_key, is_staff, is_community_ta) for thread in threads]

    with function_trace("get_metadata_for_threads"):
        annotated_content_info = utils.get_metadata_for_threads(course_key, threads, user, user_info)

    with function_trace("add_courseware_context"):
        add_courseware_context(threads, course, user)

    with function_trace("get_cohort_info"):
        course_discussion_settings = CourseDiscussionSettings.get(course_key)
        user_group_id = get_group_id_for_user(user, course_discussion_settings)

    context.update({
        'root_url': root_url,
        'discussion_id': discussion_id,
        'thread_id': thread_id,
        'threads': threads,
        'thread_pages': thread_pages,
        'annotated_content_info': annotated_content_info,
        'is_moderator': has_permission(user, "see_all_cohorts", course_key),
        'groups': course_settings["groups"],  # still needed to render _thread_list_template
        'user_group_id': user_group_id,  # read from container in NewPostView
        'sort_preference': cc_user.default_sort_key,
        'category_map': course_settings["category_map"],
        'course_settings': course_settings,
        'is_commentable_divided': is_commentable_divided(course_key, discussion_id, course_discussion_settings),
        # If the default topic id is None the front-end code will look for a topic that contains "General"
        'discussion_default_topic_id': _get_discussion_default_topic_id(course),
        'enable_daily_digest': is_forum_daily_digest_enabled(),
        'PLATFORM_NAME': settings.PLATFORM_NAME
    })
    context.update(
        get_experiment_user_metadata_context(
            course,
            user,
        )
    )
    return context


def create_user_profile_context(request, course_key, user_id):
    """ Generate a context dictionary for the user profile. """
    user = cc.User.from_django_user(request.user)
    course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)

    # If user is not enrolled in the course, do not proceed.
    django_user = User.objects.get(id=user_id)
    if not CourseEnrollment.is_enrolled(django_user, course.id):
        raise Http404

    query_params = {
        'page': request.GET.get('page', 1),
        'per_page': THREADS_PER_PAGE,   # more than threads_per_page to show more activities
    }

    group_id = get_group_id_for_comments_service(request, course_key)
    if group_id is not None:
        query_params['group_id'] = group_id
        profiled_user = cc.User(id=user_id, course_id=course_key, group_id=group_id)
    else:
        profiled_user = cc.User(id=user_id, course_id=course_key)

    threads, page, num_pages = profiled_user.active_threads(query_params)
    query_params['page'] = page
    query_params['num_pages'] = num_pages

    with function_trace("get_metadata_for_threads"):
        user_info = cc.User.from_django_user(request.user).to_dict()
        annotated_content_info = utils.get_metadata_for_threads(course_key, threads, request.user, user_info)

    is_staff = has_permission(request.user, 'openclose_thread', course.id)
    is_community_ta = utils.is_user_community_ta(request.user, course.id)
    threads = [utils.prepare_content(thread, course_key, is_staff, is_community_ta) for thread in threads]
    with function_trace("add_courseware_context"):
        add_courseware_context(threads, course, request.user)

        # TODO: LEARNER-3854: If we actually implement Learner Analytics code, this
        #   code was original protected to not run in user_profile() if is_ajax().
        #   Someone should determine if that is still necessary (i.e. was that ever
        #   called as is_ajax()) and clean this up as necessary.
        user_roles = django_user.roles.filter(
            course_id=course.id
        ).order_by("name").values_list("name", flat=True).distinct()

        with function_trace("get_cohort_info"):
            course_discussion_settings = CourseDiscussionSettings.get(course_key)
            user_group_id = get_group_id_for_user(request.user, course_discussion_settings)

        context = _create_base_discussion_view_context(request, course_key)
        context.update({
            'django_user': django_user,
            'django_user_roles': user_roles,
            'profiled_user': profiled_user.to_dict(),
            'threads': threads,
            'user_group_id': user_group_id,
            'annotated_content_info': annotated_content_info,
            'page': query_params['page'],
            'num_pages': query_params['num_pages'],
            'sort_preference': user.default_sort_key,
            'learner_profile_page_url': urljoin(settings.PROFILE_MICROFRONTEND_URL, f'/u/{django_user.username}'),
        })
        return context


@require_GET
@login_required
@use_bulk_ops
def user_profile(request, course_key, user_id):
    """
    Renders a response to display the user profile page (shown after clicking
    on a post author's username).
    """
    try:
        context = create_user_profile_context(request, course_key, user_id)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return utils.JsonResponse({
                'discussion_data': context['threads'],
                'page': context['page'],
                'num_pages': context['num_pages'],
                'annotated_content_info': context['annotated_content_info'],
            })
        else:
            discussions_mfe_enabled = ENABLE_DISCUSSIONS_MFE.is_enabled(course_key)
            if discussions_mfe_enabled:
                mfe_base_url = settings.DISCUSSIONS_MICROFRONTEND_URL
                return redirect(f"{mfe_base_url}/{str(course_key)}/learners")

            tab_view = CourseTabView()

            # To avoid mathjax loading from 'mathjax_include.html'
            # as that file causes multiple loadings of Mathjax on
            # 'user_profile' page
            context['load_mathjax'] = False

            return tab_view.get(request, str(course_key), 'discussion', profile_page_context=context)
    except User.DoesNotExist:
        raise Http404  # lint-amnesty, pylint: disable=raise-missing-from
    except ValueError:
        return HttpResponseServerError("Invalid group_id")


@login_required
@use_bulk_ops
def followed_threads(request, course_key, user_id):
    """
    Ajax-only endpoint retrieving the threads followed by a specific user.
    """
    course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)
    try:
        profiled_user = cc.User(id=user_id, course_id=course_key)

        query_params = {
            'page': 1,
            'per_page': THREADS_PER_PAGE,   # more than threads_per_page to show more activities
            'sort_key': 'date',
        }
        query_params.update(
            strip_none(
                extract(
                    request.GET,
                    [
                        'page',
                        'sort_key',
                        'flagged',
                        'unread',
                        'unanswered',
                    ]
                )
            )
        )

        try:
            group_id = get_group_id_for_comments_service(request, course_key)
        except ValueError:
            return HttpResponseServerError("Invalid group_id")
        if group_id is not None:
            query_params['group_id'] = group_id

        paginated_results = profiled_user.subscribed_threads(query_params)
        print("\n \n \n paginated results \n \n \n ")
        print(paginated_results)
        query_params['page'] = paginated_results.page
        query_params['num_pages'] = paginated_results.num_pages
        user_info = cc.User.from_django_user(request.user).to_dict()

        with function_trace("get_metadata_for_threads"):
            annotated_content_info = utils.get_metadata_for_threads(
                course_key,
                paginated_results.collection,
                request.user, user_info
            )
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            is_staff = has_permission(request.user, 'openclose_thread', course.id)
            is_community_ta = utils.is_user_community_ta(request.user, course.id)
            return utils.JsonResponse({
                'annotated_content_info': annotated_content_info,
                'discussion_data': [
                    utils.prepare_content(
                        thread, course_key, is_staff, is_community_ta
                    ) for thread in paginated_results.collection
                ],
                'page': query_params['page'],
                'num_pages': query_params['num_pages'],
            })
        #TODO remove non-AJAX support, it does not appear to be used and does not appear to work.
        else:
            context = {
                'course': course,
                'user': request.user,
                'django_user': User.objects.get(id=user_id),
                'profiled_user': profiled_user.to_dict(),
                'threads': paginated_results.collection,
                'user_info': user_info,
                'annotated_content_info': annotated_content_info,
                #                'content': content,
            }

            return render(request, 'discussion/user_profile.html', context)
    except User.DoesNotExist:
        raise Http404  # lint-amnesty, pylint: disable=raise-missing-from


def is_course_staff(course_key: CourseKey, user: User):
    """
    Check if user has course instructor or course staff role.
    """
    return CourseInstructorRole(course_key).has_user(user) or CourseStaffRole(course_key).has_user(user)


def is_privileged_user(course_key: CourseKey, user: User):
    """
    Returns True if user has one of following course role
    Administrator, Moderator, Group Moderator, Community TA,
    or Global Staff.
    """
    forum_roles = [
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_GROUP_MODERATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_ADMINISTRATOR,
    ]
    has_course_role = Role.user_has_role_for_course(user, course_key, forum_roles)
    return GlobalStaff().has_user(user) or has_course_role


class DiscussionBoardFragmentView(EdxFragmentView):
    """
    Component implementation of the discussion board.
    """

    def render_to_fragment(  # lint-amnesty, pylint: disable=arguments-differ
        self,
        request,
        course_id=None,
        discussion_id=None,
        thread_id=None,
        profile_page_context=None,
        **kwargs
    ):
        """
        Render the discussion board to a fragment.

        Args:
            request: The Django request.
            course_id: The id of the course in question.
            discussion_id: An optional discussion ID to be focused upon.
            thread_id: An optional ID of the thread to be shown.

        Returns:
            Fragment: The fragment representing the discussion board
        """
        course_key = CourseKey.from_string(course_id)
        # Force using the legacy view if a user profile is requested or the URL contains a specific topic or thread
        force_legacy_view = (profile_page_context or thread_id or discussion_id)
        is_educator_or_staff = is_course_staff(course_key, request.user) or GlobalStaff().has_user(request.user)
        try:
            base_context = _create_base_discussion_view_context(request, course_key)
            # Note:
            #   After the thread is rendered in this fragment, an AJAX
            #   request is made and the thread is completely loaded again
            #   (yes, this is something to fix). Because of this, we pass in
            #   raise_event=False to _load_thread_for_viewing avoid duplicate
            #   tracking events.
            thread = (
                _load_thread_for_viewing(
                    request,
                    base_context['course'],
                    discussion_id=discussion_id,
                    thread_id=thread_id,
                    raise_event=False,
                )
                if thread_id
                else None
            )
            context = _create_discussion_board_context(request, base_context, thread=thread)
            course_expiration_fragment = generate_course_expired_fragment(request.user, context['course'])
            context.update({
                'course_expiration_fragment': course_expiration_fragment,
            })
            if profile_page_context:
                # EDUCATOR-2119: styles are hard to reconcile if the profile page isn't also a fragment
                html = render_to_string('discussion/discussion_profile_page.html', profile_page_context)
            else:
                html = render_to_string('discussion/discussion_board_fragment.html', context)

            fragment = Fragment(html)
            self.add_fragment_resource_urls(fragment)
            inline_js = render_to_string('discussion/discussion_board_js.template', context)
            fragment.add_javascript(inline_js)
            if not settings.REQUIRE_DEBUG:
                fragment.add_javascript_url(staticfiles_storage.url('discussion/js/discussion_board_factory.js'))
            return fragment
        except cc.utils.CommentClientMaintenanceError:
            log.warning('Forum is in maintenance mode')
            html = render_to_string('discussion/maintenance_fragment.html', {
                'disable_courseware_js': True,
                'uses_bootstrap': True,
            })
            fragment = Fragment(html)
            self.add_fragment_resource_urls(fragment)
            return fragment
        except TeamDiscussionHiddenFromUserException:
            log.warning(
                'User with id={user_id} tried to view private discussion with id={discussion_id}'.format(
                    user_id=request.user.id,
                    discussion_id=discussion_id
                )
            )
            html = render_to_string('discussion/discussion_private_fragment.html', {
                'disable_courseware_js': True,
                'uses_bootstrap': True,
            })
            fragment = Fragment(html)
            self.add_fragment_resource_urls(fragment)
            return fragment

    def vendor_js_dependencies(self):
        """
        Returns list of vendor JS files that this view depends on.

        The helper function that it uses to obtain the list of vendor JS files
        works in conjunction with the Django pipeline to ensure that in development mode
        the files are loaded individually, but in production just the single bundle is loaded.
        """
        return list(dict.fromkeys(self.get_js_dependencies('discussion_vendor')))

    def js_dependencies(self):
        """
        Returns list of JS files that this view depends on.

        The helper function that it uses to obtain the list of JS files
        works in conjunction with the Django pipeline to ensure that in development mode
        the files are loaded individually, but in production just the single bundle is loaded.
        """
        return self.get_js_dependencies('discussion')

    def css_dependencies(self):
        """
        Returns list of CSS files that this view depends on.

        The helper function that it uses to obtain the list of CSS files
        works in conjunction with the Django pipeline to ensure that in development mode
        the files are loaded individually, but in production just the single bundle is loaded.
        """
        is_right_to_left = get_language_bidi()

        css_file = BOOTSTRAP_DISCUSSION_CSS_PATH
        if is_right_to_left:
            css_file = css_file.replace('.css', '-rtl.css')
        return [css_file]


@expect_json
@login_required
def discussion_topics(request, course_key_string):
    """
    The handler for divided discussion categories requests.
    This will raise 404 if user is not staff.

    Returns the JSON representation of discussion topics w.r.t categories for the course.

    Example:
        >>> example = {
        >>>               "course_wide_discussions": {
        >>>                   "entries": {
        >>>                       "General": {
        >>>                           "sort_key": "General",
        >>>                           "is_divided": True,
        >>>                           "id": "i4x-edx-eiorguegnru-course-foobarbaz"
        >>>                       }
        >>>                   }
        >>>                   "children": ["General", "entry"]
        >>>               },
        >>>               "inline_discussions" : {
        >>>                   "subcategories": {
        >>>                       "Getting Started": {
        >>>                           "subcategories": {},
        >>>                           "children": [
        >>>                               ["Working with Videos", "entry"],
        >>>                               ["Videos on edX", "entry"]
        >>>                           ],
        >>>                           "entries": {
        >>>                               "Working with Videos": {
        >>>                                   "sort_key": None,
        >>>                                   "is_divided": False,
        >>>                                   "id": "d9f970a42067413cbb633f81cfb12604"
        >>>                               },
        >>>                               "Videos on edX": {
        >>>                                   "sort_key": None,
        >>>                                   "is_divided": False,
        >>>                                   "id": "98d8feb5971041a085512ae22b398613"
        >>>                               }
        >>>                           }
        >>>                       },
        >>>                       "children": ["Getting Started", "subcategory"]
        >>>                   },
        >>>               }
        >>>          }
    """
    course_key = CourseKey.from_string(course_key_string)
    course = get_course_with_access(request.user, 'staff', course_key)

    discussion_topics = {}  # lint-amnesty, pylint: disable=redefined-outer-name
    discussion_category_map = utils.get_discussion_category_map(
        course, request.user, divided_only_if_explicit=True, exclude_unstarted=False
    )

    # We extract the data for the course wide discussions from the category map.
    course_wide_entries = discussion_category_map.pop('entries')

    course_wide_children = []
    inline_children = []

    for name, c_type in discussion_category_map['children']:
        if name in course_wide_entries and c_type == TYPE_ENTRY:
            course_wide_children.append([name, c_type])
        else:
            inline_children.append([name, c_type])

    discussion_topics['course_wide_discussions'] = {
        'entries': course_wide_entries,
        'children': course_wide_children
    }

    discussion_category_map['children'] = inline_children
    discussion_topics['inline_discussions'] = discussion_category_map

    return JsonResponse(discussion_topics)


@require_http_methods(("GET", "PATCH"))
@ensure_csrf_cookie
@expect_json
@login_required
def course_discussions_settings_handler(request, course_key_string):
    """
    The restful handler for divided discussion setting requests. Requires JSON.
    This will raise 404 if user is not staff.
    GET
        Returns the JSON representation of divided discussion settings for the course.
    PATCH
        Updates the divided discussion settings for the course. Returns the JSON representation of updated settings.
    """
    course_key = CourseKey.from_string(course_key_string)
    course = get_course_with_access(request.user, 'staff', course_key)
    discussion_settings = CourseDiscussionSettings.get(course_key)

    if request.method == 'PATCH':
        divided_course_wide_discussions, divided_inline_discussions = get_divided_discussions(
            course, discussion_settings
        )

        settings_to_change = {}

        if 'divided_course_wide_discussions' in request.json or 'divided_inline_discussions' in request.json:
            divided_course_wide_discussions = request.json.get(
                'divided_course_wide_discussions', divided_course_wide_discussions
            )
            divided_inline_discussions = request.json.get(
                'divided_inline_discussions', divided_inline_discussions
            )
            settings_to_change['divided_discussions'] = divided_course_wide_discussions + divided_inline_discussions

        if 'always_divide_inline_discussions' in request.json:
            settings_to_change['always_divide_inline_discussions'] = request.json.get(
                'always_divide_inline_discussions'
            )
        if 'division_scheme' in request.json:
            settings_to_change['division_scheme'] = request.json.get(
                'division_scheme'
            )

        if not settings_to_change:
            return JsonResponse({"error": "Bad Request"}, 400)

        try:
            if settings_to_change:
                discussion_settings.update(settings_to_change)

        except ValueError as err:
            # Note: error message not translated because it is not exposed to the user (UI prevents this state).
            return JsonResponse({"error": str(err)}, 400)

    divided_course_wide_discussions, divided_inline_discussions = get_divided_discussions(
        course, discussion_settings
    )

    return JsonResponse({
        'id': discussion_settings.id,
        'divided_inline_discussions': divided_inline_discussions,
        'divided_course_wide_discussions': divided_course_wide_discussions,
        'always_divide_inline_discussions': discussion_settings.always_divide_inline_discussions,
        'division_scheme': discussion_settings.division_scheme,
        'available_division_schemes': available_division_schemes(course_key)
    })


def _check_team_discussion_access(request, course, discussion_id):
    """
    Helper function to check if the discussion is visible to the user,
    if the user is on a team, which has the discussion set to private.
    """
    user_is_course_staff = has_access(request.user, "staff", course)
    if not user_is_course_staff and not team_api.discussion_visible_by_user(discussion_id, request.user):
        raise TeamDiscussionHiddenFromUserException()
