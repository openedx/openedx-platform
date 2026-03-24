"""
Outline Tab Views
"""
from datetime import datetime, timezone
from functools import cached_property

from completion.exceptions import UnavailableCompletionData  # lint-amnesty, pylint: disable=wrong-import-order
from completion.models import BlockCompletion
from completion.utilities import get_key_to_last_completed_block  # lint-amnesty, pylint: disable=wrong-import-order
from django.conf import settings  # lint-amnesty, pylint: disable=wrong-import-order
from django.core.cache import cache
from django.shortcuts import get_object_or_404  # lint-amnesty, pylint: disable=wrong-import-order
from django.urls import reverse  # lint-amnesty, pylint: disable=wrong-import-order
from django.utils.translation import gettext as _  # lint-amnesty, pylint: disable=wrong-import-order
from edx_django_utils import monitoring as monitoring_utils  # lint-amnesty, pylint: disable=wrong-import-order
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication  # lint-amnesty, pylint: disable=wrong-import-order
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser  # lint-amnesty, pylint: disable=wrong-import-order
from opaque_keys.edx.keys import CourseKey  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.decorators import api_view, authentication_classes, permission_classes  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.exceptions import APIException, ParseError  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.generics import RetrieveAPIView  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.permissions import IsAuthenticated  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.response import Response  # lint-amnesty, pylint: disable=wrong-import-order

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.views import expose_header
from lms.djangoapps.course_goals.api import (
    add_course_goal,
    get_course_goal,
)
from lms.djangoapps.course_goals.models import CourseGoal
from lms.djangoapps.course_home_api.outline.serializers import (
    CourseBlockSerializer,
    OutlineTabSerializer,
)
from lms.djangoapps.course_home_api.utils import get_course_or_403
from lms.djangoapps.course_home_api.tasks import collect_progress_for_user_in_course
from lms.djangoapps.course_home_api.toggles import send_course_progress_analytics_for_student_is_enabled
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
from lms.djangoapps.courseware.courses import get_course_date_blocks, get_course_info_section
from lms.djangoapps.courseware.date_summary import TodaysDate
from lms.djangoapps.courseware.masquerade import is_masquerading, setup_masquerade
from lms.djangoapps.courseware.tabs import DatesTab
from lms.djangoapps.courseware.toggles import courseware_disable_navigation_sidebar_blocks_caching
from lms.djangoapps.courseware.views.views import get_cert_data
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.utils import OptimizelyClient
from openedx.core.djangoapps.content.learning_sequences.api import get_user_course_outline
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_404
from openedx.core.djangoapps.course_groups.cohorts import get_cohort
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.features.course_duration_limits.access import get_access_expiration_data
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, ENABLE_COURSE_GOALS
from openedx.features.course_experience.course_tools import CourseToolsPluginManager
from openedx.features.course_experience.course_updates import (
    dismiss_current_update_for_user,
    get_current_update_for_user
)
from openedx.features.course_experience.url_helpers import get_learning_mfe_home_url
from openedx.features.course_experience.utils import get_course_outline_block_tree, get_start_block
from openedx.features.discounts.utils import generate_offer_data
from xblock.core import XBlock
from xblock.completable import XBlockCompletionMode
from xmodule.course_block import COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE  # lint-amnesty, pylint: disable=wrong-import-order


class UnableToDismissWelcomeMessage(APIException):
    status_code = 400
    default_detail = 'Unable to dismiss welcome message.'
    default_code = 'unable_to_dismiss_welcome_message'


class UnableToSaveCourseGoal(APIException):
    status_code = 400
    default_detail = 'Unable to save course goal'
    default_code = 'unable_to_save_course_goal'


class OutlineTabView(RetrieveAPIView):
    """
    **Use Cases**

        Request details for the Outline Tab

    **Example Requests**

        GET api/course_home/v1/outline/{course_key}

    **Response Values**

        Body consists of two possible shapes.

        For a good 200 response, the response will include:

        access_expiration: An object detailing when access to this course will expire
            expiration_date: (str) When the access expires, in ISO 8601 notation
            masquerading_expired_course: (bool) Whether this course is expired for the masqueraded user
            upgrade_deadline: (str) Last chance to upgrade, in ISO 8601 notation (or None if can't upgrade anymore)
            upgrade_url: (str) Upgrade link (or None if can't upgrade anymore)
        course_blocks:
            blocks: List of serialized Course Block objects. Each serialization has the following fields:
                id: (str) The usage ID of the block.
                type: (str) The type of block. Possible values the names of any
                    XBlock type in the system, including custom blocks. Examples are
                    course, chapter, sequential, vertical, html, problem, video, and
                    discussion.
                display_name: (str) The display name of the block.
                lms_web_url: (str) The URL to the navigational container of the
                    xBlock on the web LMS.
                children: (list) If the block has child blocks, a list of IDs of
                    the child blocks.
                resume_block: (bool) Whether the block is the resume block
                has_scheduled_content: (bool) Whether the block has more content scheduled for the future
        course_goals:
            selected_goal:
                days_per_week: (int) The number of days the learner wants to learn per week
                subscribed_to_reminders: (bool) Whether the learner wants email reminders about their goal
            weekly_learning_goal_enabled: Flag indicating if this feature is enabled for this call
        course_tools: List of serialized Course Tool objects. Each serialization has the following fields:
            analytics_id: (str) The unique id given to the tool.
            title: (str) The display title of the tool.
            url: (str) The link to access the tool.
        dates_banner_info: (obj)
            content_type_gating_enabled: (bool) Whether content type gating is enabled for this enrollment.
            missed_deadlines: (bool) Whether the user has missed any graded content deadlines for the given course.
            missed_gated_content: (bool) Whether the user has missed any gated content for the given course.
            verified_upgrade_link: (str) The URL to ecommerce IDA for purchasing the verified upgrade.
        dates_widget:
            course_date_blocks: List of serialized Course Dates objects. Each serialization has the following fields:
                complete: (bool) Meant to only be used by assignments. Indicates completeness for an
                assignment.
                date: (datetime) The date time corresponding for the event
                date_type: (str) The type of date (ex. course-start-date, assignment-due-date, etc.)
                description: (str) The description for the date event
                learner_has_access: (bool) Indicates if the learner has access to the date event
                link: (str) An absolute link to content related to the date event
                    (ex. verified link or link to assignment)
                title: (str) The title of the date event
            dates_tab_link: (str) The URL to the Dates Tab
            user_timezone: (str) The timezone of the given user
        enroll_alert:
            can_enroll: (bool) Whether the user can enroll in the given course
            extra_text: (str)
        enrollment_mode: (str) Current enrollment mode. Null if the user is not enrolled.
        handouts_html: (str) Raw HTML for the handouts section of the course info
        has_ended: (bool) Indicates whether course has ended
        offer: An object detailing upgrade discount information
            code: (str) Checkout code
            expiration_date: (str) Expiration of offer, in ISO 8601 notation
            original_price: (str) Full upgrade price without checkout code; includes currency symbol
            discounted_price: (str) Upgrade price with checkout code; includes currency symbol
            percentage: (int) Amount of discount
            upgrade_url: (str) Checkout URL
        resume_course:
            has_visited_course: (bool) Whether the user has ever visited the course
            url: (str) The display name of the course block to resume
        welcome_message_html: (str) Raw HTML for the course updates banner
        user_has_passing_grade: (bool) Whether the user currently is passing the course

        If the learner does not have access to the course for a specific reason and should be redirected this
        view will return a 403 and the following data:

        url: (str) The URL to which the user should be redirected
        error_code: (str) A system identifier for the reason the user is being redirected
        developer_message: (str) A message explaining why the user is being redirected,
                                 intended for developer debugging.
        user_message: (str) A message explaining why the user is being redirected, intended to be shown to the user.

    **Returns**

        * 200 on success.
        * 403 if the user does not currently have access to the course and should be redirected.
        * 404 if the course is not available or cannot be seen.

    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    serializer_class = OutlineTabSerializer

    def get(self, request, *args, **kwargs):  # pylint: disable=too-many-statements
        course_key_string = kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)

        # Enable NR tracing for this view based on course
        monitoring_utils.set_custom_attribute('course_id', course_key_string)
        monitoring_utils.set_custom_attribute('user_id', request.user.id)
        monitoring_utils.set_custom_attribute('is_staff', request.user.is_staff)

        course = get_course_or_403(request.user, 'load', course_key, check_if_enrolled=False)

        masquerade_object, request.user = setup_masquerade(
            request,
            course_key,
            staff_access=has_access(request.user, 'staff', course_key),
            reset_masquerade_data=True,
        )

        user_is_masquerading = is_masquerading(request.user, course_key, course_masquerade=masquerade_object)

        course_overview = get_course_overview_or_404(course_key)
        enrollment = CourseEnrollment.get_enrollment(request.user, course_key)
        enrollment_mode = getattr(enrollment, 'mode', None)
        allow_anonymous = COURSE_ENABLE_UNENROLLED_ACCESS_FLAG.is_enabled(course_key)
        allow_public = allow_anonymous and course.course_visibility == COURSE_VISIBILITY_PUBLIC
        allow_public_outline = allow_anonymous and course.course_visibility == COURSE_VISIBILITY_PUBLIC_OUTLINE

        # User locale settings
        user_timezone_locale = user_timezone_locale_prefs(request)
        user_timezone = user_timezone_locale['user_timezone']

        dates_tab_link = get_learning_mfe_home_url(course_key=course.id, url_fragment='dates')

        # Set all of the defaults
        access_expiration = None
        cert_data = None
        course_blocks = None
        course_goals = {
            'selected_goal': None,
            'weekly_learning_goal_enabled': False,
        }
        course_tools = CourseToolsPluginManager.get_enabled_course_tools(request, course_key)
        dates_widget = {
            'course_date_blocks': [],
            'dates_tab_link': dates_tab_link,
            'user_timezone': user_timezone,
        }
        enroll_alert = {
            'can_enroll': True,
            'extra_text': None,
        }
        handouts_html = None
        offer_data = None
        resume_course = {
            'has_visited_course': False,
            'url': None,
        }
        welcome_message_html = None

        is_enrolled = enrollment and enrollment.is_active
        is_staff = bool(has_access(request.user, 'staff', course_key))
        show_enrolled = is_enrolled or is_staff
        enable_proctored_exams = False
        if show_enrolled:
            course_blocks = get_course_outline_block_tree(request, course_key_string, request.user)
            date_blocks = get_course_date_blocks(course, request.user, request, num_assignments=1)
            course_date_blocks = (
                [block for block in date_blocks if not isinstance(block, TodaysDate)]
                if DatesTab.is_enabled(course, request.user)
                else []
            )
            dates_widget['course_date_blocks'] = course_date_blocks

            handouts_html = get_course_info_section(request, request.user, course, 'handouts')
            welcome_message_html = get_current_update_for_user(request, course)

            offer_data = generate_offer_data(request.user, course_overview)
            access_expiration = get_access_expiration_data(request.user, course_overview)
            cert_data = get_cert_data(request.user, course, enrollment.mode) if is_enrolled else None

            enable_proctored_exams = course_overview.enable_proctored_exams

            if (is_enrolled and ENABLE_COURSE_GOALS.is_enabled(course_key)):
                course_goals['weekly_learning_goal_enabled'] = True
                selected_goal = get_course_goal(request.user, course_key)
                if selected_goal:
                    course_goals['selected_goal'] = {
                        'days_per_week': selected_goal.days_per_week,
                        'subscribed_to_reminders': selected_goal.subscribed_to_reminders,
                    }

            try:
                resume_block = get_key_to_last_completed_block(request.user, course.id)
                resume_course['has_visited_course'] = True
                resume_path = reverse('jump_to', kwargs={
                    'course_id': course_key_string,
                    'location': str(resume_block)
                })
                resume_course['url'] = request.build_absolute_uri(resume_path)
            except UnavailableCompletionData:
                start_block = get_start_block(course_blocks)
                resume_course['url'] = start_block['lms_web_url']

        elif allow_public_outline or allow_public or user_is_masquerading:
            course_blocks = get_course_outline_block_tree(request, course_key_string, None)
            if allow_public or user_is_masquerading:
                handouts_html = get_course_info_section(request, request.user, course, 'handouts')

        if not is_enrolled:
            if CourseMode.is_masters_only(course_key):
                enroll_alert['can_enroll'] = False
                enroll_alert['extra_text'] = _(
                    'Please contact your degree administrator or '
                    '{platform_name} Support if you have questions.'
                ).format(platform_name=settings.PLATFORM_NAME)
            elif CourseEnrollment.is_enrollment_closed(request.user, course_overview):
                enroll_alert['can_enroll'] = False
            elif CourseEnrollment.objects.is_course_full(course_overview):
                enroll_alert['can_enroll'] = False
                enroll_alert['extra_text'] = _('Course is full')

        # Sometimes there are sequences returned by Course Blocks that we
        # don't actually want to show to the user, such as when a sequence is
        # composed entirely of units that the user can't access. The Learning
        # Sequences API knows how to roll this up, so we use it determine which
        # sequences we should remove from course_blocks.
        #
        # The long term goal is to remove the Course Blocks API call entirely,
        # so this is a tiny first step in that migration.
        if course_blocks:
            user_course_outline = get_user_course_outline(
                course_key, request.user, datetime.now(tz=timezone.utc)
            )
            available_seq_ids = {str(usage_key) for usage_key in user_course_outline.sequences}

            available_section_ids = {str(section.usage_key) for section in user_course_outline.sections}

            # course_blocks is a reference to the root of the course,
            # so we go through the chapters (sections) and keep only those
            # which are part of the outline.
            course_blocks['children'] = [
                chapter_data
                for chapter_data in course_blocks.get('children', [])
                if chapter_data['id'] in available_section_ids
            ]

            # course_blocks is a reference to the root of the course, so we go
            # through the chapters (sections) to look for sequences to remove.
            for chapter_data in course_blocks['children']:
                chapter_data['children'] = [
                    seq_data
                    for seq_data in chapter_data['children']
                    if (
                        seq_data['id'] in available_seq_ids or
                        # Edge case: Sometimes we have weird course structures.
                        # We expect only sequentials here, but if there is
                        # another type, just skip it (don't filter it out).
                        seq_data['type'] != 'sequential'
                    )
                ] if 'children' in chapter_data else []

        user_has_passing_grade = False
        if not request.user.is_anonymous:
            user_grade = CourseGradeFactory().read(request.user, course)
            if user_grade:
                user_has_passing_grade = user_grade.passed

        data = {
            'access_expiration': access_expiration,
            'cert_data': cert_data,
            'course_blocks': course_blocks,
            'course_goals': course_goals,
            'course_tools': course_tools,
            'dates_widget': dates_widget,
            'enable_proctored_exams': enable_proctored_exams,
            'enroll_alert': enroll_alert,
            'enrollment_mode': enrollment_mode,
            'handouts_html': handouts_html,
            'has_ended': course.has_ended(),
            'offer': offer_data,
            'resume_course': resume_course,
            'user_has_passing_grade': user_has_passing_grade,
            'welcome_message_html': welcome_message_html,
        }
        context = self.get_serializer_context()
        context['course_overview'] = course_overview
        context['enable_links'] = show_enrolled or allow_public
        context['enrollment'] = enrollment
        serializer = self.get_serializer_class()(data, context=context)

        if send_course_progress_analytics_for_student_is_enabled(course_key) and not user_is_masquerading:
            collect_progress_for_user_in_course.delay(course_key_string, request.user.id)

        return Response(serializer.data)

    def finalize_response(self, request, response, *args, **kwargs):
        """
        Return the final response, exposing the 'Date' header for computing relative time to the dates in the data.

        Important dates such as 'access_expiration' are enforced server-side based on correct time; client-side clocks
        are frequently substantially far off which could lead to inaccurate messaging and incorrect expectations.
        Therefore, any messaging about those dates should be based on the server time and preferably in relative terms
        (time remaining); the 'Date' header is a straightforward and generalizable way for client-side code to get this
        reference.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        # Adding this header should be moved to global middleware, not just this endpoint
        return expose_header('Date', response)


class CourseNavigationBlocksView(RetrieveAPIView):
    """
    **Use Cases**
        Request details for the sidebar navigation of the course.
    **Example Requests**
        GET api/course_home/v1/navigation/{course_key}
    **Response Values**
        For a good 200 response, the response will include:
        blocks: List of serialized Course Block objects. Each serialization has the following fields:
            id: (str) The usage ID of the block.
            type: (str) The type of block. Possible values the names of any
                XBlock type in the system, including custom blocks. Examples are
                course, chapter, sequential, vertical, html, problem, video, and
                discussion.
            display_name: (str) The display name of the block.
            lms_web_url: (str) The URL to the navigational container of the
                xBlock on the web LMS.
            children: (list) If the block has child blocks, a list of IDs of
                the child blocks.
            resume_block: (bool) Whether the block is the resume block
            has_scheduled_content: (bool) Whether the block has more content scheduled for the future
    **Returns**
        * 200 on success.
        * 403 if the user does not currently have access to the course and should be redirected.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    serializer_class = CourseBlockSerializer
    COURSE_BLOCKS_CACHE_KEY_TEMPLATE = (
        'course_sidebar_blocks_{course_key_string}_{course_version}_{user_id}_{user_cohort_id}'
        '_{enrollment_mode}_{allow_public}_{allow_public_outline}_{is_masquerading}'
    )
    COURSE_BLOCKS_CACHE_TIMEOUT = 60 * 60  # 1 hour

    def get(self, request, *args, **kwargs):
        """
        Get the visible course blocks (from course to vertical types) for the given course.
        """
        course_key_string = kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)
        course = get_course_or_403(request.user, 'load', course_key, check_if_enrolled=False)
        staff_access = has_access(request.user, 'staff', course_key)

        masquerade_object, request.user = setup_masquerade(
            request,
            course_key,
            staff_access=staff_access,
            reset_masquerade_data=True,
        )

        user_is_masquerading = is_masquerading(request.user, course_key, course_masquerade=masquerade_object)

        allow_anonymous = COURSE_ENABLE_UNENROLLED_ACCESS_FLAG.is_enabled(course_key)
        allow_public = allow_anonymous and course.course_visibility == COURSE_VISIBILITY_PUBLIC
        allow_public_outline = allow_anonymous and course.course_visibility == COURSE_VISIBILITY_PUBLIC_OUTLINE
        enrollment = CourseEnrollment.get_enrollment(request.user, course_key)

        try:
            user_cohort = get_cohort(request.user, course_key, use_cached=True)
        except ValueError:
            user_cohort = None

        cache_key = self.COURSE_BLOCKS_CACHE_KEY_TEMPLATE.format(
            course_key_string=course_key_string,
            course_version=str(course.course_version),
            user_id=request.user.id,
            enrollment_mode=getattr(enrollment, 'mode', ''),
            user_cohort_id=getattr(user_cohort, 'id', ''),
            allow_public=allow_public,
            allow_public_outline=allow_public_outline,
            is_masquerading=user_is_masquerading,
        )
        if navigation_sidebar_caching_is_disabled := courseware_disable_navigation_sidebar_blocks_caching():
            course_blocks = None
        else:
            course_blocks = cache.get(cache_key)

        if not course_blocks:
            if getattr(enrollment, 'is_active', False) or bool(staff_access):
                course_blocks = get_course_outline_block_tree(request, course_key_string, request.user)
            elif allow_public_outline or allow_public or user_is_masquerading:
                course_blocks = get_course_outline_block_tree(request, course_key_string, None)

            if not navigation_sidebar_caching_is_disabled:
                cache.set(cache_key, course_blocks, self.COURSE_BLOCKS_CACHE_TIMEOUT)

        course_blocks = self.filter_inaccessible_blocks(course_blocks, course_key)
        course_blocks = self.mark_complete_recursive(course_blocks)

        context = self.get_serializer_context()
        context.update({
            'include_vertical': True,
            'extra_fields': ['special_exam_info', 'completion_stat'],
            'enable_prerequisite_block_type': True,
        })

        serializer = self.get_serializer_class()(course_blocks, context=context)

        return Response(serializer.data)

    def filter_inaccessible_blocks(self, course_blocks, course_key):
        """
        Filter out sections and subsections that are not accessible to the current user.
        """
        if course_blocks:
            user_course_outline = get_user_course_outline(course_key, self.request.user, datetime.now(tz=timezone.utc))
            course_sections = course_blocks.get('children', [])
            course_blocks['children'] = self.get_accessible_sections(user_course_outline, course_sections)

            for section_data in course_sections:
                section_data['children'] = self.get_accessible_sequences(
                    user_course_outline,
                    section_data.get('children', [])
                )
                accessible_sequence_ids = {str(usage_key) for usage_key in user_course_outline.accessible_sequences}
                for sequence_data in section_data['children']:
                    sequence_data['accessible'] = sequence_data['id'] in accessible_sequence_ids

        return course_blocks

    def mark_complete_recursive(self, block):
        """
        Mark blocks as complete or not complete based on the completions_dict.
        """
        if not block:
            return block

        if 'children' in block and block['type'] in self.aggregator_block_types:
            block['children'] = [self.mark_complete_recursive(child) for child in block['children'] if child]
            completable_children = self.get_completable_children(block)
            block['complete'] = all(child['complete'] for child in completable_children)
            block['completion_stat'] = self.get_block_completion_stat(block, completable_children)
        else:
            # If the block is a course, chapter, sequential, or vertical, without children,
            # it should be completed by default.
            completion = self.completions_dict.get(block['id'], 0)
            block['complete'] = bool(completion) or block['type'] in ['course', 'chapter', 'sequential', 'vertical']
            block['completion_stat'] = self.get_block_completion_stat(block, completable_children=[])

        return block

    def get_block_completion_stat(self, block, completable_children):
        """
        Get the completion status of a block.

        Returns dictionary with the completion status and the number
        of completable children of a block.
        Completion is the value from 0 to 1 meaning the percentage of completion for lower-level blocks,
        and sum of the completion status of the completable children.
        """
        block_type = block['type']
        completable_children_num = len(completable_children)

        if block_type in ['course', 'sequential']:
            completion = sum(child['complete'] for child in completable_children)
        elif block_type == 'chapter':
            # For sections, we have to count the status on the number of completed units
            # and, accordingly, the number of units in it.
            completion = sum(child['completion_stat']['completion'] for child in completable_children)
            completable_children_num = sum(
                child['completion_stat']['completable_children'] for child in completable_children
            )
        elif block_type == 'vertical':
            completion = sum(child['completion_stat']['completion'] for child in completable_children)
        else:
            completion = self.completions_dict.get(block['id'], 0)

        return {
            'completion': completion,
            'completable_children': completable_children_num,
        }

    def get_completable_children(self, block):
        """
        Get the completable children of a block.
        """
        return [child for child in block.get('children', []) if child['type'] in self.completable_block_types]

    @staticmethod
    def get_accessible_sections(user_course_outline, course_sections):
        """
        Filter out sections that are not accessible to the user.
        """
        available_section_ids = set(map(lambda section: str(section.usage_key), user_course_outline.sections))
        return [
            section_data for section_data in course_sections
            if section_data['id'] in available_section_ids
        ]

    @staticmethod
    def get_accessible_sequences(user_course_outline, course_sequences):
        """
        Filter out sequences that are not accessible to the user.
        """
        available_sequence_ids = set(map(str, user_course_outline.sequences))
        return [
            seq_data for seq_data in course_sequences
            if seq_data['id'] in available_sequence_ids or seq_data['type'] != 'sequential'
        ]

    @cached_property
    def completions_dict(self):
        """
        Return a dictionary of block completions for the current user.

        Dictionary keys are block keys and values are int values
        representing the completion status of the block.
        """
        course_key_string = self.kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)
        completions = BlockCompletion.objects.filter(user=self.request.user, context_key=course_key).values_list(
            'block_key',
            'completion',
        )
        return {
            str(block_key): completion
            for block_key, completion in completions
        }

    @cached_property
    def aggregator_block_types(self) -> set[str]:
        """
        Return a set of block types that belong to XBlockCompletionMode.AGGREGATOR.

        We use this information to determine if the block completion should depend on the completion of its children:
        1. If the block is an aggregator, it should be marked as completed when all its children are completed.
        2. If the block is completable, it should be directly marked as completed - regardless of its children.
        """
        return {
            block_type for (block_type, block_cls) in XBlock.load_classes()
            if XBlockCompletionMode.get_mode(block_cls) == XBlockCompletionMode.AGGREGATOR
        }

    @cached_property
    def completable_block_types(self):
        """
        Returns a set of block types that can be marked as completed.

        In addition to the lower-level x-blocks, it also includes blocks
        that belong to XBlockCompletionMode.AGGREGATOR, because they can also be marked as complete.
        """
        return {
            block_type for (block_type, block_cls) in XBlock.load_classes()
            if XBlockCompletionMode.get_mode(block_cls) in (
                XBlockCompletionMode.COMPLETABLE,
                XBlockCompletionMode.AGGREGATOR
            )
        }


@api_view(['POST'])
@permission_classes((IsAuthenticated,))
def dismiss_welcome_message(request):  # pylint: disable=missing-function-docstring
    course_id = request.data.get('course_id', None)

    # If body doesn't contain 'course_id', return 400 to client.
    if not course_id:
        raise ParseError(_("'course_id' is required."))

    # If body contains params other than 'course_id', return 400 to client.
    if len(request.data) > 1:
        raise ParseError(_("Only 'course_id' is expected."))

    try:
        course_key = CourseKey.from_string(course_id)
        course = get_course_or_403(request.user, 'load', course_key, check_if_enrolled=True)
        dismiss_current_update_for_user(request, course)
        return Response({'message': _('Welcome message successfully dismissed.')})
    except Exception:
        raise UnableToDismissWelcomeMessage  # pylint: disable=raise-missing-from


# Another version of this endpoint exists in ../course_goals/views.py
@api_view(['POST'])
@authentication_classes((JwtAuthentication, SessionAuthenticationAllowInactiveUser,))
@permission_classes((IsAuthenticated,))
def save_course_goal(request):  # pylint: disable=missing-function-docstring
    course_id = request.data.get('course_id')
    days_per_week = request.data.get('days_per_week')
    subscribed_to_reminders = request.data.get('subscribed_to_reminders')

    # If body doesn't contain 'course_id', return 400 to client.
    if not course_id:
        raise ParseError("'course_id' is required.")

    # If body doesn't contain the required goals fields, return 400 to client.
    if subscribed_to_reminders is None:
        raise ParseError("'subscribed_to_reminders' is required.")

    try:
        add_course_goal(request.user, course_id, subscribed_to_reminders, days_per_week)
        # TODO: VAN-1052: This event is added to track the KPIs for A/B experiment.
        #  Remove it after the experiment has been paused.
        optimizely_client = OptimizelyClient.get_optimizely_client()
        if optimizely_client and request.user:
            optimizely_client.track('goal_set', str(request.user.id))
        return Response({
            'header': _('Your course goal has been successfully set.'),
            'message': _('Course goal updated successfully.'),
        })
    except Exception as exc:
        raise UnableToSaveCourseGoal from exc


@api_view(['POST'])
def unsubscribe_from_course_goal_by_token(request, token):
    """
    API calls to unsubscribe from course goal reminders.

    Note that this does not require authentication - this view may be hit from an email on a different device than
    normal or whatever. We should still be able to unsubscribe the user. Instead, we use a token in the email to
    validate that they have permission to unsubscribe.

    This endpoint is very tightly scoped (only unsubscribe: no subscribing, no PII) because it is unauthenticated.

    **Example Requests**
        POST api/course_home/v1/unsubscribe_from_course_goal/{token}

    **Example Response Data**
        {'course_title': 'Cats & Dogs In Canadian Media'}

    Returns a 404 response if the token was not found. Otherwise, returns some basic course info. But no PII.
    """
    # First update the goal
    goal = get_object_or_404(CourseGoal, unsubscribe_token=token)
    goal.subscribed_to_reminders = False
    goal.save()

    # Now generate a response
    course_overview = get_course_overview_or_404(goal.course_key)
    return Response({
        'course_title': course_overview.display_name,
    })
