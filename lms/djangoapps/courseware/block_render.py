"""
Block rendering
"""

from __future__ import annotations
import json
import logging
import textwrap
from collections import OrderedDict

from functools import partial

from completion.services import CompletionService
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.middleware.csrf import CsrfViewMiddleware
from django.template.context_processors import csrf
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from edx_django_utils.cache import DEFAULT_REQUEST_CACHE, RequestCache
from edx_django_utils.monitoring import set_custom_attributes_for_course_key, set_monitoring_transaction_name
from edx_proctoring.api import get_attempt_status_summary
from edx_proctoring.services import ProctoringService
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.permissions import JwtRestrictedApplication
from edx_when.field_data import DateLookupFieldData
from eventtracking import tracker
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework.decorators import api_view
from rest_framework.exceptions import APIException
from typing import Callable, TYPE_CHECKING
from web_fragments.fragment import Fragment
from xblock.django.request import django_to_webob_request, webob_to_django_response
from xblock.exceptions import NoSuchHandlerError, NoSuchViewError, NotFoundError, ProcessingError
from xblock.reference.plugins import FSService
from xblock.runtime import KvsFieldData

from lms.djangoapps.teams.services import TeamsService
from openedx.core.djangoapps.video_config.services import VideoConfigService
from openedx.core.djangoapps.discussions.services import DiscussionConfigService
from openedx.core.lib.xblock_services.call_to_action import CallToActionService
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError as XModuleNotFoundError
from xmodule.library_tools import LegacyLibraryToolsService
from xmodule.modulestore.django import XBlockI18nService, modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.partitions.partitions_service import PartitionService
from xmodule.util.sandboxing import SandboxService
from xmodule.services import EventPublishingService, RebindUserService, SettingsService, TeamsConfigurationService
from common.djangoapps.static_replace.services import ReplaceURLService
from common.djangoapps.static_replace.wrapper import replace_urls_wrapper
from lms.djangoapps.courseware.access import get_user_role, has_access
from lms.djangoapps.courseware.entrance_exams import user_can_skip_entrance_exam, user_has_passed_entrance_exam
from lms.djangoapps.courseware.masquerade import (
    MasqueradingKeyValueStore,
    filter_displayed_blocks,
    is_masquerading_as_specific_student,
    setup_masquerade
)
from lms.djangoapps.courseware.model_data import DjangoKeyValueStore, FieldDataCache
from lms.djangoapps.courseware.field_overrides import OverrideFieldData
from lms.djangoapps.courseware.services import UserStateService
from lms.djangoapps.grades.api import GradesUtilService
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from lms.djangoapps.lms_xblock.runtime import UserTagsService, lms_wrappers_aside, lms_applicable_aside_types
from lms.djangoapps.verify_student.services import XBlockVerificationService
from openedx.core.djangoapps.bookmarks.api import BookmarksService
from openedx.core.djangoapps.crawlers.models import CrawlersConfig
from openedx.core.djangoapps.credit.services import CreditService
from openedx.core.djangoapps.enrollments.services import EnrollmentsService
from openedx.core.djangoapps.util.user_utils import SystemUser
from openedx.core.djangolib.markup import HTML
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import view_auth_classes
from openedx.core.lib.gating.services import GatingService
from openedx.core.lib.license import wrap_with_license
from openedx.core.lib.url_utils import quote_slashes, unquote_slashes
from openedx.core.lib.xblock_utils import (
    add_staff_markup,
    get_aside_from_xblock,
    hash_resource,
    is_xblock_aside
)
from openedx.core.lib.xblock_utils import request_token as xblock_request_token
from openedx.core.lib.xblock_utils import wrap_xblock
from openedx.features.course_duration_limits.access import course_expiration_wrapper
from openedx.features.discounts.utils import offer_banner_wrapper
from openedx.features.content_type_gating.services import ContentTypeGatingService
from common.djangoapps.student.models import anonymous_id_for_user
from common.djangoapps.student.roles import CourseBetaTesterRole
from common.djangoapps.util import milestones_helpers
from common.djangoapps.util.json_request import JsonResponse
from common.djangoapps.edxmako.services import MakoService
from common.djangoapps.xblock_django.user_service import DjangoXBlockUserService
from openedx.core.lib.cache_utils import CacheService

if TYPE_CHECKING:
    from rest_framework.request import Request
    from xblock.core import XBlock
    from xblock.runtime import Runtime

    from xmodule.course_block import CourseBlock

log = logging.getLogger(__name__)

# TODO: course_id and course_key are used interchangeably in this file, which is wrong.
# Some brave person should make the variable names consistently someday, but the code's
# coupled enough that it's kind of tricky--you've been warned!


class LmsModuleRenderError(Exception):
    """
    An exception class for exceptions thrown by block_render that don't fit well elsewhere
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


def make_track_function(request):
    '''
    Make a tracking function that logs what happened.
    For use in ModuleStoreRuntime.
    '''
    from common.djangoapps.track import views as track_views

    def function(event_type, event):
        return track_views.server_track(request, event_type, event, page='x_module')
    return function


def toc_for_course(user, request, course, active_chapter, active_section, field_data_cache):
    '''
    Create a table of contents from the module store

    Return format:
    { 'chapters': [
            {'display_name': name, 'url_name': url_name, 'sections': SECTIONS, 'active': bool},
        ],
        'previous_of_active_section': {..},
        'next_of_active_section': {..}
    }

    where SECTIONS is a list
    [ {'display_name': name, 'url_name': url_name,
       'format': format, 'due': due, 'active' : bool, 'graded': bool}, ...]

    where previous_of_active_section and next_of_active_section have information on the
    next/previous sections of the active section.

    active is set for the section and chapter corresponding to the passed
    parameters, which are expected to be url_names of the chapter+section.
    Everything else comes from the xml, or defaults to "".

    chapters with name 'hidden' are skipped.

    NOTE: assumes that if we got this far, user has access to course.  Returns
    None if this is not the case.

    field_data_cache must include data from the course blocks and 2 levels of its descendants
    '''
    with modulestore().bulk_operations(course.id):
        course_block = get_block_for_descriptor(
            user, request, course, field_data_cache, course.id, course=course
        )
        if course_block is None:
            return None, None, None

        toc_chapters = []
        chapters = course_block.get_children()

        # Check for content which needs to be completed
        # before the rest of the content is made available
        required_content = milestones_helpers.get_required_content(course.id, user)

        # The user may not actually have to complete the entrance exam, if one is required
        if user_can_skip_entrance_exam(user, course):
            required_content = [content for content in required_content if not content == course.entrance_exam_id]

        previous_of_active_section, next_of_active_section = None, None
        last_processed_section, last_processed_chapter = None, None
        found_active_section = False
        for chapter in chapters:
            # Only show required content, if there is required content
            # chapter.hide_from_toc is read-only (bool)
            # xss-lint: disable=python-deprecated-display-name
            display_id = slugify(chapter.display_name_with_default_escaped)
            local_hide_from_toc = False
            if required_content:
                if str(chapter.location) not in required_content:
                    local_hide_from_toc = True

            # Skip the current chapter if a hide flag is tripped
            if chapter.hide_from_toc or local_hide_from_toc:
                continue

            sections = []
            for section in chapter.get_children():
                # skip the section if it is hidden from the user
                if section.hide_from_toc:
                    continue

                is_section_active = (chapter.url_name == active_chapter and section.url_name == active_section)
                if is_section_active:
                    found_active_section = True

                section_context = {
                    # xss-lint: disable=python-deprecated-display-name
                    'display_name': section.display_name_with_default_escaped,
                    'url_name': section.url_name,
                    'format': section.format if section.format is not None else '',
                    'due': section.due,
                    'active': is_section_active,
                    'graded': section.graded,
                }
                _add_timed_exam_info(user, course, section, section_context)

                # update next and previous of active section, if applicable
                if is_section_active:
                    if last_processed_section:
                        previous_of_active_section = last_processed_section.copy()
                        previous_of_active_section['chapter_url_name'] = last_processed_chapter.url_name
                elif found_active_section and not next_of_active_section:
                    next_of_active_section = section_context.copy()
                    next_of_active_section['chapter_url_name'] = chapter.url_name

                sections.append(section_context)
                last_processed_section = section_context
                last_processed_chapter = chapter

            toc_chapters.append({
                # xss-lint: disable=python-deprecated-display-name
                'display_name': chapter.display_name_with_default_escaped,
                'display_id': display_id,
                'url_name': chapter.url_name,
                'sections': sections,
                'active': chapter.url_name == active_chapter
            })
        return {
            'chapters': toc_chapters,
            'previous_of_active_section': previous_of_active_section,
            'next_of_active_section': next_of_active_section,
        }


def _add_timed_exam_info(user, course, section, section_context):
    """
    Add in rendering context if exam is a timed exam (which includes proctored)
    """
    section_is_time_limited = (
        getattr(section, 'is_time_limited', False) and
        settings.FEATURES.get('ENABLE_SPECIAL_EXAMS', False)
    )
    if section_is_time_limited:
        # call into edx_proctoring subsystem
        # to get relevant proctoring information regarding this
        # level of the courseware
        #
        # This will return None, if (user, course_id, content_id)
        # is not applicable
        timed_exam_attempt_context = None
        try:
            timed_exam_attempt_context = get_attempt_status_summary(
                user.id,
                str(course.id),
                str(section.location)
            )
        except Exception as ex:  # pylint: disable=broad-except
            # safety net in case something blows up in edx_proctoring
            # as this is just informational descriptions, it is better
            # to log and continue (which is safe) than to have it be an
            # unhandled exception
            log.exception(ex)

        if timed_exam_attempt_context:
            # yes, user has proctoring context about
            # this level of the courseware
            # so add to the accordion data context
            section_context.update({
                'proctoring': timed_exam_attempt_context,
            })


def get_block(user, request, usage_key, field_data_cache, position=None, log_if_not_found=True,
              wrap_xblock_display=True, grade_bucket_type=None, depth=0, static_asset_path='', course=None,
              will_recheck_access=False):
    """
    Get an instance of the XBlock class identified by location,
    setting the state based on an existing StudentModule, or creating one if none
    exists.

    Arguments:
      - user                  : User for whom we're getting the block
      - request               : current django HTTPrequest.  Note: request.user isn't used for anything--all auth
                                and such works based on user.
      - usage_key             : A UsageKey object identifying the module to load
      - field_data_cache      : a FieldDataCache
      - position              : Extra information from URL for user-specified position within module.
                                It is used to determine the active tab within the `SequenceBlock`/subsection.
                                Once the legacy course experience is removed, it should be safe to remove this, too.
      - log_if_not_found      : If this is True, we log a debug message if we cannot find the requested xmodule.
      - wrap_xblock_display   : If this is True, wrap the output display in a single div to allow for the
                                XModule javascript to be bound correctly
      - depth                 : number of levels of descendents to cache when loading this module.
                                None means cache all descendents
      - static_asset_path     : static asset path to use (overrides block's value); needed
                                by get_course_info_section, because info section modules
                                do not have a course as the parent module, and thus do not
                                inherit this lms key value.
      - will_recheck_access   : If True, the caller commits to re-checking access on each child XBlock
                                before rendering the content in order to display access error messages
                                to the user.

    Returns: XBlock instance, or None if the user does not have access to the
    block.  If there's an error, will try to return an instance of ErrorBlock
    if possible.  If not possible, return None.
    """
    try:
        block = modulestore().get_item(usage_key, depth=depth)
        return get_block_for_descriptor(user, request, block, field_data_cache, usage_key.course_key,
                                        position=position,
                                        wrap_xblock_display=wrap_xblock_display,
                                        grade_bucket_type=grade_bucket_type,
                                        static_asset_path=static_asset_path,
                                        course=course, will_recheck_access=will_recheck_access)
    except ItemNotFoundError:
        if log_if_not_found:
            log.debug("Error in get_block: ItemNotFoundError")
        return None

    except:  # pylint: disable=W0702
        # Something has gone terribly wrong, but still not letting it turn into a 500.
        log.exception("Error in get_block")
        return None


def display_access_messages(user, block, view, frag, context):  # pylint: disable=W0613
    """
    An XBlock wrapper that replaces the content fragment with a fragment or message determined by
    the has_access check.
    """
    blocked_prior_sibling = RequestCache('display_access_messages_prior_sibling')

    load_access = has_access(user, 'load', block, block.scope_ids.usage_id.course_key)
    if load_access:
        blocked_prior_sibling.delete(block.parent)
        return frag

    prior_sibling = blocked_prior_sibling.get_cached_response(block.parent)

    if prior_sibling.is_found and prior_sibling.value.error_code == load_access.error_code:
        return Fragment("")
    else:
        blocked_prior_sibling.set(block.parent, load_access)

    if load_access.user_fragment:
        msg_fragment = load_access.user_fragment
    elif load_access.user_message:
        msg_fragment = Fragment(textwrap.dedent(HTML("""\
            <div>{}</div>
        """).format(load_access.user_message)))
    else:
        msg_fragment = Fragment("")

    if load_access.developer_message and has_access(user, 'staff', block, block.scope_ids.usage_id.course_key):
        msg_fragment.content += textwrap.dedent(HTML("""\
            <div>{}</div>
        """).format(load_access.developer_message))

    return msg_fragment


# pylint: disable=too-many-statements
def get_block_for_descriptor(
    user: User | AnonymousUser,
    request: Request | None,
    block: XBlock,
    field_data_cache: FieldDataCache | None,
    course_key: CourseKey,
    position: int | None = None,
    wrap_xblock_display: bool = True,
    grade_bucket_type: str | None = None,
    static_asset_path: str = '',
    disable_staff_debug_info: bool = False,
    course: CourseBlock | None = None,
    will_recheck_access: bool = False,
    track_function: Callable[[str, dict], None] | None = None,
    student_data: KvsFieldData | None = None,
    request_token: str | None = None,
    user_location: str | None = None,
) -> XBlock | None:
    """
    Implements get_block, extracting out the request-specific functionality.

    disable_staff_debug_info : If this is True, exclude staff debug information in the rendering of the block.

    See get_block() docstring for further details.
    """
    if request:
        track_function = track_function or make_track_function(request)
        user_location = user_location or getattr(request, 'session', {}).get('country_code')
        request_token = request_token or xblock_request_token(request)

    if not student_data:
        student_kvs = DjangoKeyValueStore(field_data_cache)
        if is_masquerading_as_specific_student(user, course_key):
            student_kvs = MasqueradingKeyValueStore(student_kvs, request.session)
        student_data = KvsFieldData(student_kvs)

    # The runtime is already shared between XBlocks. If there are no wrappers, it is the first initialization.
    should_recreate_runtime = not block.runtime.wrappers

    # If the runtime was prepared for another user, it should be recreated.
    # This part can be removed if we remove all user-specific handling from the runtime services and pull this
    # information directly from XBlocks during the service initialization.
    if not should_recreate_runtime:
        # If the user service is absent (which should never happen), the runtime should be reinitialized.
        # We retrieve this service directly to bypass service declaration checks.
        if user_service := block.runtime._services.get('user'):  # pylint: disable=protected-access
            # Check the user ID bound to the runtime. This operation can run often for complex course structures, so we
            # are accessing the protected attribute of the user service to reduce the number of queries.
            should_recreate_runtime = user.id != user_service._django_user.id  # pylint: disable=protected-access
        else:
            should_recreate_runtime = True

    if should_recreate_runtime:
        prepare_runtime_for_user(
            user=user,
            student_data=student_data,  # These have implicit user bindings, the rest of args are considered not to
            runtime=block.runtime,
            course_id=course_key,
            track_function=track_function,
            wrap_xblock_display=wrap_xblock_display,
            grade_bucket_type=grade_bucket_type,
            static_asset_path=static_asset_path,
            user_location=user_location,
            request_token=request_token,
            disable_staff_debug_info=disable_staff_debug_info,
            course=course,
            will_recheck_access=will_recheck_access,
        )

    # Pass position specified in URL to runtime.
    if position is not None:
        try:
            position = int(position)
        except (ValueError, TypeError):
            log.exception('Non-integer %r passed as position.', position)
            position = None

    block.runtime.set('position', position)

    block.bind_for_student(
        user.id,
        [
            partial(DateLookupFieldData, course_id=course_key, user=user),
            partial(OverrideFieldData.wrap, user, course),
            partial(LmsFieldData, student_data=student_data),
        ],
    )

    # Do not check access when it's a noauth request.
    # Not that the access check needs to happen after the block is bound
    # for the student, since there may be field override data for the student
    # that affects xblock visibility.
    user_needs_access_check = getattr(user, 'known', True) and not isinstance(user, SystemUser)
    if user_needs_access_check:
        access = has_access(user, 'load', block, course_key)
        # A block should only be returned if either the user has access, or the user doesn't have access, but
        # the failed access has a message for the user and the caller of this function specifies it will check access
        # again. This allows blocks to show specific error message or upsells when access is denied.
        caller_will_handle_access_error = (
            not access
            and will_recheck_access
            and (access.user_message or access.user_fragment)
        )
        if access or caller_will_handle_access_error:
            block.has_access_error = bool(caller_will_handle_access_error)
            return block
        return None
    return block


def prepare_runtime_for_user(
        user: User | AnonymousUser,
        student_data: KvsFieldData,
        # Arguments preceding this comment have user binding, those following don't
        runtime: Runtime,
        course_id: CourseKey,
        track_function: Callable[[str, dict], None],
        request_token: str,
        position: int | None = None,
        wrap_xblock_display: bool = True,
        grade_bucket_type: str | None = None,
        static_asset_path: str = '',
        user_location: str | None = None,
        disable_staff_debug_info: bool = False,
        course: CourseBlock | None = None,
        will_recheck_access: bool = False,
):
    """
    Helper function that binds the given xblock to a user and student_data to a user and the block.

    The purpose of this function is to factor out everywhere a user is implicitly bound when creating a module,
    to allow an existing block to be re-bound to a user.

    The arguments fall into two categories: those that have explicit or implicit user binding, which are user
    and student_data, and those don't and are used to instantiate the service required in LMS, which
    are all the other arguments.

    Arguments:
        see arguments for get_block()
        request_token (str): A token unique to the request use by xblock initialization
    """

    def inner_get_block(block: XBlock) -> XBlock | None:
        """
        Delegate to get_block_for_descriptor() with all values except `block` set.

        Because it does an access check, it may return None.
        """
        return get_block_for_descriptor(
            user=user,
            request=None,
            field_data_cache=None,
            block=block,
            student_data=student_data,
            course_key=course_id,
            track_function=track_function,
            request_token=request_token,
            position=position,
            wrap_xblock_display=wrap_xblock_display,
            grade_bucket_type=grade_bucket_type,
            static_asset_path=static_asset_path,
            user_location=user_location,
            course=course,
            will_recheck_access=will_recheck_access,
        )

    # Build a list of wrapping functions that will be applied in order
    # to the Fragment content coming out of the xblocks that are about to be rendered.
    block_wrappers = []

    if is_masquerading_as_specific_student(user, course_id):
        block_wrappers.append(filter_displayed_blocks)

    mako_service = MakoService()
    if settings.FEATURES.get("LICENSING", False):
        block_wrappers.append(partial(wrap_with_license, mako_service=mako_service))

    # Wrap the output display in a single div to allow for the XBlock
    # javascript to be bound correctly
    if wrap_xblock_display is True:
        block_wrappers.append(partial(
            wrap_xblock,
            'LmsRuntime',
            extra_data={'course-id': str(course_id)},
            usage_id_serializer=lambda usage_id: quote_slashes(str(usage_id)),
            request_token=request_token,
        ))

    replace_url_service = partial(
        ReplaceURLService,
        static_asset_path=static_asset_path,
        jump_to_id_base_url=reverse('jump_to_id', kwargs={'course_id': str(course_id), 'module_id': ''}),
    )

    # Rewrite static urls with course-specific absolute urls
    block_wrappers.append(partial(replace_urls_wrapper, replace_url_service=replace_url_service))

    block_wrappers.append(partial(display_access_messages, user))
    block_wrappers.append(partial(course_expiration_wrapper, user))
    block_wrappers.append(partial(offer_banner_wrapper, user))

    user_is_staff = bool(has_access(user, 'staff', course_id))

    if settings.FEATURES.get('DISPLAY_DEBUG_INFO_TO_STAFF'):
        if user_is_staff or is_masquerading_as_specific_student(user, course_id):
            # When masquerading as a specific student, we want to show the debug button
            # unconditionally to enable resetting the state of the student we are masquerading as.
            # We already know the user has staff access when masquerading is active.
            block_wrappers.append(partial(add_staff_markup, user, disable_staff_debug_info))

    store = modulestore()

    services = {
        'fs': FSService(),
        'mako': mako_service,
        'user': DjangoXBlockUserService(
            user,
            user_is_beta_tester=CourseBetaTesterRole(course_id).has_user(user),
            user_is_staff=user_is_staff,
            user_is_global_staff=bool(has_access(user, 'staff', 'global')),
            user_role=get_user_role(user, course_id),
            anonymous_user_id=anonymous_id_for_user(user, course_id),
            # See the docstring of `DjangoXBlockUserService`.
            deprecated_anonymous_user_id=anonymous_id_for_user(user, None),
            request_country_code=user_location,
        ),
        'verification': XBlockVerificationService(),
        'proctoring': ProctoringService(),
        'milestones': milestones_helpers.get_service(),
        'credit': CreditService(),
        'bookmarks': BookmarksService(user=user),
        'gating': GatingService(),
        'grade_utils': GradesUtilService(course_id=course_id),
        'user_state': UserStateService(),
        'content_type_gating': ContentTypeGatingService(),
        'cache': CacheService(cache),
        'sandbox': SandboxService(contentstore=contentstore, course_id=course_id),
        'replace_urls': replace_url_service,
        # Rebind module service to deal with noauth modules getting attached to users.
        'rebind_user': RebindUserService(
            user,
            course_id,
            track_function=track_function,
            position=position,
            wrap_xblock_display=wrap_xblock_display,
            grade_bucket_type=grade_bucket_type,
            static_asset_path=static_asset_path,
            user_location=user_location,
            request_token=request_token,
            will_recheck_access=will_recheck_access,
        ),
        'completion': CompletionService(user=user, context_key=course_id) if user and user.is_authenticated else None,
        'i18n': XBlockI18nService,
        'library_tools': LegacyLibraryToolsService(store, user_id=user.id if user else None),
        'partitions': PartitionService(course_id=course_id, cache=DEFAULT_REQUEST_CACHE.data),
        'settings': SettingsService(),
        'user_tags': UserTagsService(user=user, course_id=course_id),
        'teams': TeamsService(),
        'teams_configuration': TeamsConfigurationService(),
        'call_to_action': CallToActionService(),
        'publish': EventPublishingService(user, course_id, track_function),
        'enrollments': EnrollmentsService(),
        'video_config': VideoConfigService(),
        'discussion_config_service': DiscussionConfigService(),
    }

    runtime.get_block_for_descriptor = inner_get_block

    runtime.wrappers = block_wrappers
    runtime._services.update(services)  # lint-amnesty, pylint: disable=protected-access
    runtime.request_token = request_token
    runtime.wrap_asides_override = lms_wrappers_aside
    runtime.applicable_aside_types_override = lms_applicable_aside_types


def load_single_xblock(request, user_id, course_id, usage_key_string, course=None, will_recheck_access=False):
    """
    Load a single XBlock identified by usage_key_string.
    """
    usage_key = UsageKey.from_string(usage_key_string)
    course_key = CourseKey.from_string(course_id)
    usage_key = usage_key.map_into_course(course_key)
    user = User.objects.get(id=user_id)
    field_data_cache = FieldDataCache.cache_for_block_descendents(
        course_key,
        user,
        modulestore().get_item(usage_key),
        depth=0,
    )
    instance = get_block(
        user,
        request,
        usage_key,
        field_data_cache,
        grade_bucket_type='xqueue',
        course=course,
        will_recheck_access=will_recheck_access
    )
    if instance is None:
        msg = f"No module {usage_key_string} for user {user}--access denied?"
        log.debug(msg)
        raise Http404
    return instance


@csrf_exempt
def xqueue_callback(request, course_id, userid, mod_id, dispatch):
    '''
    Entry point for graded results from the queueing system.
    '''
    data = request.POST.copy()

    # Test xqueue package, which we expect to be:
    #   xpackage = {'xqueue_header': json.dumps({'lms_key':'secretkey',...}),
    #               'xqueue_body'  : 'Message from grader'}
    for key in ['xqueue_header', 'xqueue_body']:
        if key not in data:
            raise Http404

    header = json.loads(data['xqueue_header'])
    if not isinstance(header, dict) or 'lms_key' not in header:
        raise Http404

    course_key = CourseKey.from_string(course_id)

    with modulestore().bulk_operations(course_key):
        course = modulestore().get_course(course_key, depth=0)

        instance = load_single_xblock(request, userid, course_id, mod_id, course=course)

        # Transfer 'queuekey' from xqueue response header to the data.
        # This is required to use the interface defined by 'handle_ajax'
        data.update({'queuekey': header['lms_key']})

        # We go through the "AJAX" path
        # So far, the only dispatch from xqueue will be 'score_update'
        try:
            # Can ignore the return value--not used for xqueue_callback
            instance.handle_ajax(dispatch, data)
            # Save any state that has changed to the underlying KeyValueStore
            instance.save()
        except:
            log.exception("error processing ajax call")
            raise

        return HttpResponse("")


@csrf_exempt
@xframe_options_exempt
@transaction.non_atomic_requests
def handle_xblock_callback_noauth(request, course_id, usage_id, handler, suffix=None):
    """
    Entry point for unauthenticated XBlock handlers.
    """
    request.user.known = False

    course_key = CourseKey.from_string(course_id)
    with modulestore().bulk_operations(course_key):
        course = modulestore().get_course(course_key, depth=0)
        return _invoke_xblock_handler(request, course_id, usage_id, handler, suffix, course=course)


@csrf_exempt
@xframe_options_exempt
@transaction.non_atomic_requests
def handle_xblock_callback(request, course_id, usage_id, handler, suffix=None):
    """
    Generic view for extensions. This is where AJAX calls go.

    Arguments:
        request (Request): Django request.
        course_id (str): Course containing the block
        usage_id (str)
        handler (str)
        suffix (str)

    Raises:
        HttpResponseForbidden: If the request method is not `GET` and user is not authenticated.
        Http404: If the course is not found in the modulestore.
    """
    # In this case, we are using Session based authentication, so we need to check CSRF token.
    if request.user.is_authenticated:
        error = CsrfViewMiddleware(get_response=lambda request: None).process_view(request, None, (), {})
        if error:
            return error

    # We are reusing DRF logic to provide support for JWT and Oauth2. We abandoned the idea of using DRF view here
    # to avoid introducing backwards-incompatible changes.
    # You can see https://github.com/openedx/XBlock/pull/383 for more details.
    else:
        authentication_classes = (JwtAuthentication, BearerAuthenticationAllowInactiveUser)
        authenticators = [auth() for auth in authentication_classes]

        for authenticator in authenticators:
            try:
                user_auth_tuple = authenticator.authenticate(request)
            except APIException:
                log.exception(
                    "XBlock handler %r failed to authenticate with %s", handler, authenticator.__class__.__name__
                )
            else:
                if user_auth_tuple is not None:
                    # When using JWT authentication, the second element contains the JWT token. We need it to determine
                    # whether the application that issued the token is restricted.
                    request.user, request.auth = user_auth_tuple
                    # This is verified by the `JwtRestrictedApplication` before it decodes the token.
                    request.successful_authenticator = authenticator
                    break

    # NOTE (CCB): Allow anonymous GET calls (e.g. for transcripts). Modifying this view is simpler than updating
    # the XBlocks to use `handle_xblock_callback_noauth`, which is practically identical to this view.
    # Block all request types coming from restricted applications.
    if (
        request.method != 'GET' and not (request.user and request.user.is_authenticated)
    ) or JwtRestrictedApplication().has_permission(request, None):  # type: ignore
        return HttpResponseForbidden('Unauthenticated')

    request.user.known = request.user.is_authenticated

    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError:
        raise Http404(f'{course_id} is not a valid course key')  # lint-amnesty, pylint: disable=raise-missing-from

    with modulestore().bulk_operations(course_key):
        try:
            course = modulestore().get_course(course_key)
        except ItemNotFoundError:
            raise Http404(f'{course_id} does not exist in the modulestore')  # lint-amnesty, pylint: disable=raise-missing-from

        return _invoke_xblock_handler(request, course_id, usage_id, handler, suffix, course=course)


def _get_usage_key_for_course(course_key, usage_id) -> UsageKey:
    """
    Returns UsageKey mapped into the course for a given usage_id string
    """
    try:
        return UsageKey.from_string(unquote_slashes(usage_id)).map_into_course(course_key)
    except InvalidKeyError as exc:
        raise Http404("Invalid location") from exc


def _get_block_by_usage_key(usage_key):
    """
    Gets a block instance based on a mapped-to-course usage_key

    Returns (instance, tracking_context)
    """
    try:
        block = modulestore().get_item(usage_key)
        block_orig_usage_key, block_orig_version = modulestore().get_block_original_usage(usage_key)
    except ItemNotFoundError as exc:
        log.warning(
            "Invalid location for course id %s: %s",
            usage_key.course_key,
            usage_key
        )
        raise Http404 from exc

    tracking_context = {
        'module': {
            # xss-lint: disable=python-deprecated-display-name
            'display_name': block.display_name_with_default_escaped,
            'usage_key': str(block.location),
        }
    }

    # For blocks that are inherited from a content library, we add some additional metadata:
    if block_orig_usage_key is not None:
        tracking_context['module']['original_usage_key'] = str(block_orig_usage_key)
        tracking_context['module']['original_usage_version'] = str(block_orig_version)

    return block, tracking_context


def get_block_by_usage_id(request, course_id, usage_id, disable_staff_debug_info=False, course=None,
                          will_recheck_access=False):
    """
    Gets a block instance based on its `usage_id` in a course, for a given request/user

    Returns (instance, tracking_context)
    """
    course_key = CourseKey.from_string(course_id)
    usage_key = _get_usage_key_for_course(course_key, usage_id)
    block, tracking_context = _get_block_by_usage_key(usage_key)

    _, user = setup_masquerade(request, course_key, has_access(request.user, 'staff', block, course_key))
    field_data_cache = FieldDataCache.cache_for_block_descendents(
        course_key,
        user,
        block,
        read_only=CrawlersConfig.is_crawler(request),
    )
    instance = get_block_for_descriptor(
        user,
        request,
        block,
        field_data_cache,
        usage_key.course_key,
        disable_staff_debug_info=disable_staff_debug_info,
        course=course,
        will_recheck_access=will_recheck_access,
    )
    if instance is None:
        # Either permissions just changed, or someone is trying to be clever
        # and load something they shouldn't have access to.
        log.debug("No module %s for user %s -- access denied?", usage_key, user)
        raise Http404

    return instance, tracking_context


def _invoke_xblock_handler(request, course_id, usage_id, handler, suffix, course=None):
    """
    Invoke an XBlock handler, either authenticated or not.

    Arguments:
        request (HttpRequest): the current request
        course_id (str): A string of the form org/course/run
        usage_id (str): A string of the form i4x://org/course/category/name@revision
        handler (str): The name of the handler to invoke
        suffix (str): The suffix to pass to the handler when invoked
    """

    # Check submitted files
    files = request.FILES or {}
    error_msg = _check_files_limits(files)
    if error_msg:
        return JsonResponse({'success': error_msg}, status=413)

    # Make a CourseKey from the course_id, raising a 404 upon parse error.
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError as exc:
        raise Http404 from exc

    set_custom_attributes_for_course_key(course_key)

    with modulestore().bulk_operations(course_key):
        usage_key = _get_usage_key_for_course(course_key, usage_id)
        if is_xblock_aside(usage_key):
            # Get the usage key for the block being wrapped by the aside (not the aside itself)
            block_usage_key = usage_key.usage_key
        else:
            block_usage_key = usage_key

        # Peek at the handler method to see if it actually wants to check access itself. (The handler may not want
        # inaccessible blocks stripped from the tree.) This ends up doing two modulestore lookups for the block,
        # but the blocks should be available in the request cache the second time.
        # At the time of writing, this is only used by one handler. If this usage grows, we may want to re-evaluate
        # how we do this to something more elegant. If you are the author of a third party block that decides it wants
        # to set this too, please let us know so we can consider making this easier / better-documented.
        block, _ = _get_block_by_usage_key(block_usage_key)
        handler_method = getattr(block, handler, False)
        will_recheck_access = handler_method and getattr(handler_method, 'will_recheck_access', False)

        instance, tracking_context = get_block_by_usage_id(
            request, course_id, str(block_usage_key), course=course, will_recheck_access=will_recheck_access,
        )

        # Name the transaction so that we can view XBlock handlers separately in
        # New Relic. The suffix is necessary for XBlock handlers because the
        # "handler" in those cases is always just "xmodule_handler".
        nr_tx_name = f"{instance.__class__.__name__}.{handler}"
        nr_tx_name += f"/{suffix}" if (suffix and handler == "xmodule_handler") else ""
        set_monitoring_transaction_name(nr_tx_name, group="Python/XBlock/Handler")

        tracking_context_name = 'module_callback_handler'
        req = django_to_webob_request(request)
        try:
            with tracker.get_tracker().context(tracking_context_name, tracking_context):
                if is_xblock_aside(usage_key):
                    # In this case, 'instance' is the XBlock being wrapped by the aside, so
                    # the actual aside instance needs to be retrieved in order to invoke its
                    # handler method.
                    handler_instance = get_aside_from_xblock(instance, usage_key.aside_type)
                else:
                    handler_instance = instance
                resp = handler_instance.handle(handler, req, suffix)
                if suffix == 'problem_check' \
                        and course \
                        and getattr(course, 'entrance_exam_enabled', False) \
                        and getattr(instance, 'in_entrance_exam', False):
                    ee_data = {'entrance_exam_passed': user_has_passed_entrance_exam(request.user, course)}
                    resp = append_data_to_webob_response(resp, ee_data)

        except NoSuchHandlerError:
            log.exception("XBlock %s attempted to access missing handler %r", instance, handler)
            raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

        # If we can't find the block, respond with a 404
        except (XModuleNotFoundError, NotFoundError):
            log.exception("Module indicating to user that request doesn't exist")
            raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

        # For XBlock-specific errors, we log the error and respond with an error message
        except ProcessingError as err:
            log.warning("Module encountered an error while processing AJAX call",
                        exc_info=True)
            return JsonResponse({'success': err.args[0]}, status=200)

        # If any other error occurred, re-raise it to trigger a 500 response
        except Exception:
            log.exception("error executing xblock handler")
            raise

    return webob_to_django_response(resp)


@api_view(['GET'])
@view_auth_classes(is_authenticated=True)
def xblock_view(request, course_id, usage_id, view_name):
    """
    Returns the rendered view of a given XBlock, with related resources

    Returns a json object containing two keys:
        html: The rendered html of the view
        resources: A list of tuples where the first element is the resource hash, and
            the second is the resource description
    """
    if not settings.FEATURES.get('ENABLE_XBLOCK_VIEW_ENDPOINT', False):
        log.warning("Attempt to use deactivated XBlock view endpoint -"
                    " see FEATURES['ENABLE_XBLOCK_VIEW_ENDPOINT']")
        raise Http404

    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError:
        raise Http404("Invalid location")  # lint-amnesty, pylint: disable=raise-missing-from

    with modulestore().bulk_operations(course_key):
        course = modulestore().get_course(course_key)
        instance, _ = get_block_by_usage_id(request, course_id, usage_id, course=course)

        try:
            fragment = instance.render(view_name, context=request.GET)
        except NoSuchViewError:
            log.exception("Attempt to render missing view on %s: %s", instance, view_name)
            raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

        hashed_resources = OrderedDict()
        for resource in fragment.resources:
            hashed_resources[hash_resource(resource)] = resource

        return JsonResponse({
            'html': fragment.content,
            'resources': list(hashed_resources.items()),
            'csrf_token': str(csrf(request)['csrf_token']),
        })


def _check_files_limits(files):
    """
    Check if the files in a request are under the limits defined by
    `settings.MAX_FILEUPLOADS_PER_INPUT` and
    `settings.STUDENT_FILEUPLOAD_MAX_SIZE`.

    Returns None if files are correct or an error messages otherwise.
    """
    for fileinput_id in files.keys():
        inputfiles = files.getlist(fileinput_id)

        # Check number of files submitted
        if len(inputfiles) > settings.MAX_FILEUPLOADS_PER_INPUT:
            msg = 'Submission aborted! Maximum %d files may be submitted at once' % \
                  settings.MAX_FILEUPLOADS_PER_INPUT
            return msg

        # Check file sizes
        for inputfile in inputfiles:
            if inputfile.size > settings.STUDENT_FILEUPLOAD_MAX_SIZE:  # Bytes
                msg = 'Submission aborted! Your file "%s" is too large (max size: %d MB)' % \
                      (inputfile.name, settings.STUDENT_FILEUPLOAD_MAX_SIZE / (1000 ** 2))
                return msg

    return None


def append_data_to_webob_response(response, data):
    """
    Appends data to a JSON webob response.

    Arguments:
        response (webob response object):  the webob response object that needs to be modified
        data (dict):  dictionary containing data that needs to be appended to response body

    Returns:
        (webob response object):  webob response with updated body.

    """
    if getattr(response, 'content_type', None) == 'application/json':
        json_input = response.body.decode('utf-8') if isinstance(response.body, bytes) else response.body
        response_data = json.loads(json_input)
        response_data.update(data)
        response.body = json.dumps(response_data).encode('utf-8')
    return response
