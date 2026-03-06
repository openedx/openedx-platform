"""
General view for the Course Home that contains metadata every page needs.
"""

from django.db import transaction
from django.utils.decorators import method_decorator
from opaque_keys.edx.keys import CourseKey
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from lms.djangoapps.certificates.api import certificates_viewable_for_course
from lms.djangoapps.course_home_api.toggles import new_discussion_sidebar_view_is_enabled
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.djangoapps.courseware_api.utils import get_celebrations_dict

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.auth import has_course_author_access
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.course_api.api import course_detail
from lms.djangoapps.course_goals.models import UserActivity
from lms.djangoapps.course_home_api.course_metadata.serializers import CourseHomeMetadataSerializer
from lms.djangoapps.courseware.access import has_access, has_cms_access
from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
from lms.djangoapps.courseware.courses import check_course_access
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.courseware.masquerade import setup_masquerade
from lms.djangoapps.courseware.tabs import get_course_tab_list


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CourseHomeMetadataView(RetrieveAPIView):
    """
    **Use Cases**

        Request Course metadata details for the Course Home MFE that every page needs.

    **Example Requests**

        GET api/course_home/v1/course_metadata/{course_key}

    **Response Values**

        Body consists of the following fields:

        course_id: (str) The Course's id (Course Run key)
        username: (str) The requesting (or masqueraded) user. Returns None for an
            unauthenticated user.
        is_enrolled: (bool) Indicates if the user is enrolled in the course
        is_self_paced: (bool) Indicates if the course is self paced
        is_staff: (bool) Indicates if the user is staff
        original_user_is_staff: (bool) Indicates if the original user has staff access
            Used for when masquerading to distinguish between the original requesting user
            and the user being masqueraded as.
        number: (str) The Course's number
        org: (str) The Course's organization
        tabs: List of Course Tabs to display. They are serialized as:
            tab_id: (str) The tab's id
            title: (str) The title of the tab to display
            url: (str) The url to view the tab
        title: (str) The Course's display title
        celebrations: (dict) a dict of celebration data
        user_timezone: (str) The timezone of the given user
        can_view_certificate: Flag to determine whether or not the learner can view their course certificate.

    **Returns**

        * 200 on success with above fields.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    serializer_class = CourseHomeMetadataSerializer

    def get(self, request, *args, **kwargs):
        course_key_string = kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)
        original_user_is_global_staff = self.request.user.is_staff
        original_user_is_staff = has_access(request.user, 'staff', course_key).has_access

        try:
            course = course_detail(request, request.user.username, course_key)
        except CourseAccessRedirect as e:
            error = e.access_error
            raise PermissionDenied(
                detail=error.user_message if error is not None else 'You do not have access to this course.',
                code=error.error_code if error is not None else None,
            ) from e

        # We must compute course load access *before* setting up masquerading,
        # else course staff (who are not enrolled) will not be able view
        # their course from the perspective of a learner.
        load_access = check_course_access(
            course,
            request.user,
            'load',
            check_if_enrolled=True,
            check_if_authenticated=True,
            apply_enterprise_checks=True,
        )

        _, request.user = setup_masquerade(
            request,
            course_key,
            staff_access=original_user_is_staff,
            reset_masquerade_data=True,
        )

        username = request.user.username if request.user.username else None
        enrollment = CourseEnrollment.get_enrollment(request.user, course_key_string)
        user_is_enrolled = bool(enrollment and enrollment.is_active)

        # User locale settings
        user_timezone_locale = user_timezone_locale_prefs(request)
        user_timezone = user_timezone_locale['user_timezone']

        browser_timezone = self.request.query_params.get('browser_timezone', None)
        celebrations = get_celebrations_dict(
            request.user, enrollment, course, user_timezone if not None else browser_timezone
        )

        # Record course goals user activity for (web) learning mfe course tabs
        UserActivity.record_user_activity(request.user, course_key)

        course_modes = CourseMode.modes_for_course(course_key, include_expired=True, only_selectable=False)

        data = {
            'course_id': course.id,
            'username': username,
            'studio_access': has_cms_access(request.user, course_key),
            'is_staff': has_access(request.user, 'staff', course_key).has_access,
            'original_user_is_staff': original_user_is_staff,
            'number': course.display_number_with_default,
            'org': course.display_org_with_default,
            'start': course.start,
            'tabs': get_course_tab_list(request.user, course),
            'title': course.display_name_with_default,
            'is_self_paced': getattr(course, 'self_paced', False),
            'is_enrolled': user_is_enrolled,
            'course_access': load_access.to_json(),
            'celebrations': celebrations,
            'user_timezone': user_timezone,
            'can_view_certificate': certificates_viewable_for_course(course),
            'course_modes': course_modes,
            'is_new_discussion_sidebar_view_enabled': new_discussion_sidebar_view_is_enabled(course_key),
            # We check the course author access in the context of CMS here because this field is used
            # to determine whether the user can access the course authoring tools in the CMS.
            # This is a temporary solution until the course author role is split into "Course Author" and
            # "Course Editor" as described in the permission matrix here:
            # https://github.com/openedx/platform-roadmap/issues/246
            'has_course_author_access': has_course_author_access(request.user, course_key, 'cms'),
        }
        context = self.get_serializer_context()
        context['course'] = course
        context['course_overview'] = course
        context['enrollment'] = enrollment
        serializer = self.get_serializer_class()(data, context=context)
        return Response(serializer.data)
