"""
Dates Tab Views
"""

from edx_django_utils import monitoring as monitoring_utils
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.course_goals.models import UserActivity
from lms.djangoapps.course_home_api.dates.serializers import DatesTabSerializer
from lms.djangoapps.course_home_api.utils import get_course_or_403
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
from lms.djangoapps.courseware.courses import get_course_date_blocks
from lms.djangoapps.courseware.date_summary import TodaysDate
from lms.djangoapps.courseware.masquerade import setup_masquerade
from lms.djangoapps.courseware.tabs import DatesTab
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.features.content_type_gating.models import ContentTypeGatingConfig


class DatesTabView(RetrieveAPIView):
    """
    **Use Cases**

        Request details for the Dates Tab

    **Example Requests**

        GET api/course_home/v1/dates/{course_key}

    **Response Values**

        Body consists of the following fields:

        course_date_blocks: List of serialized DateSummary objects. Each serialization has the following fields:
            complete: (bool) Meant to only be used by assignments. Indicates completeness for an
                assignment.
            date: (datetime) The date time corresponding for the event
            date_type: (str) The type of date (ex. course-start-date, assignment-due-date, etc.)
            description: (str) The description for the date event
            learner_has_access: (bool) Indicates if the learner has access to the date event
            link: (str) An absolute link to content related to the date event
                (ex. verified link or link to assignment)
            title: (str) The title of the date event
        dates_banner_info: (obj)
            content_type_gating_enabled: (bool) Whether content type gating is enabled for this enrollment.
            missed_deadlines: (bool) Indicates whether the user missed any graded content deadlines
            missed_gated_content: (bool) Indicates whether the user missed gated content
            verified_upgrade_link: (str) The link for upgrading to the Verified track in a course
        has_ended: (bool) Indicates whether course has ended
        learner_is_full_access: (bool) Indicates if the user is verified in the course
        user_timezone: (str) The user's preferred timezone

    **Returns**

        * 200 on success with above fields.
        * 401 if the user is not authenticated.
        * 403 if the user does not have access to the course.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)
    serializer_class = DatesTabSerializer

    def get(self, request, *args, **kwargs):
        course_key_string = kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)
        allow_not_started_courses = request.GET.get('allow_not_started_courses', False)

        # Enable NR tracing for this view based on course
        monitoring_utils.set_custom_attribute('course_id', course_key_string)
        monitoring_utils.set_custom_attribute('user_id', request.user.id)
        monitoring_utils.set_custom_attribute('is_staff', request.user.is_staff)
        course = get_course_or_403(
            request.user,
            'load',
            course_key,
            check_if_enrolled=False,
            allow_not_started_courses=allow_not_started_courses
        )
        is_staff = bool(has_access(request.user, 'staff', course_key))

        _, request.user = setup_masquerade(
            request,
            course_key,
            staff_access=is_staff,
            reset_masquerade_data=True,
        )

        # Record user activity for tracking progress towards a user's course goals (for mobile app)
        UserActivity.record_user_activity(request.user, course.id, request=request, only_if_mobile_app=True)

        if not CourseEnrollment.is_enrolled(request.user, course_key) and not is_staff:
            return Response('User not enrolled.', status=401)

        blocks = get_course_date_blocks(course, request.user, request, include_access=True, include_past_dates=True)

        learner_is_full_access = not ContentTypeGatingConfig.enabled_for_enrollment(
            user=request.user,
            course_key=course_key,
        )

        course_date_blocks = (
            [block for block in blocks if not isinstance(block, TodaysDate)]
            if DatesTab.is_enabled(course, request.user)
            else []
        )

        # User locale settings
        user_timezone_locale = user_timezone_locale_prefs(request)
        user_timezone = user_timezone_locale['user_timezone']

        data = {
            'has_ended': course.has_ended(),
            'course_date_blocks': course_date_blocks,
            'learner_is_full_access': learner_is_full_access,
            'user_timezone': user_timezone,
        }
        context = self.get_serializer_context()
        context['learner_is_full_access'] = learner_is_full_access
        serializer = self.get_serializer_class()(data, context=context)

        return Response(serializer.data)
