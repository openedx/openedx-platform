"""
Enrollments Service
"""
from functools import reduce
from operator import or_

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none

USER_MODEL = get_user_model()


class EnrollmentsService:
    """
    Enrollments service

    Provides functions related to course enrollments
    """
    def get_active_enrollments_by_course(self, course_id):
        """
        Returns a list of active enrollments for a course
        """
        return CourseEnrollment.objects.filter(course_id=course_id, is_active=True)

    def get_active_enrollments_by_course_and_user(self, course_id, user_id):
        """
        Returns a list of active enrollments for a course and user.

        Parameters:
        * course_id: course ID for the course
        * user_id: ID of User object
        """
        user = self._get_user_by_id(user_id)
        enrollment = CourseEnrollment.get_enrollment(user, course_id)
        if not enrollment or not enrollment.is_active:
            # not enrolled
            return None

        return enrollment

    def _get_user_by_id(self, user_id):
        """
        Returns s User object for give ID

        Parameters:
        * user_id: ID of User object
        """
        try:
            return USER_MODEL.objects.get(id=user_id)
        except USER_MODEL.DoesNotExist:
            return None

    def _get_enrollments_for_course_proctoring_eligible_modes(self, course_id, allow_honor_mode=False):
        """
        Return all enrollments for a course that are in a mode that makes the corresponding user
        eligible to take proctored exams.

        Parameters:
        * course_id: course ID for the course
        * allow_honor_mode: represents whether the course allows users with enrollments
        in the honor mode are eligible to take proctored exams
        """
        enrollments = CourseEnrollment.objects.filter(course_id=course_id, is_active=True)

        # We only want to get enrollments in paid modes.
        appropriate_modes = [
            CourseMode.VERIFIED,
            CourseMode.MASTERS,
            CourseMode.PROFESSIONAL,
            CourseMode.EXECUTIVE_EDUCATION,
        ]

        # If the proctoring provider allows learners in honor mode to take exams, include it in the filter.
        if allow_honor_mode:
            appropriate_modes.append(CourseMode.HONOR)

        modes_filters = reduce(or_, [Q(mode=mode) for mode in appropriate_modes])

        enrollments = enrollments.filter(modes_filters)
        return enrollments

    def get_enrollments_can_take_proctored_exams(self, course_id, text_search=None):
        """
        Return all enrollments for a course that are in a mode that makes the corresponding user
        eligible to take proctored exams.

        NOTE: Due to performance concerns, this method returns a QuerySet. Ordinarily, model implementations
        should not be exposed to clients in this way. However, the clients may need to do additional computation
        in the database to avoid performance penalties.

        Parameters:
        * course_id: course ID for the course
        * text_search: the string against which to do a match on users' username or email; optional
        """
        course_id_coursekey = CourseKey.from_string(course_id)
        course_overview = get_course_overview_or_none(course_id_coursekey)
        if not course_overview or not course_overview.enable_proctored_exams:
            return None

        allow_honor_mode = settings.PROCTORING_BACKENDS.get(
            course_overview.proctoring_provider, {}
        ).get('allow_honor_mode', False)
        enrollments = self._get_enrollments_for_course_proctoring_eligible_modes(course_id, allow_honor_mode)

        enrollments = enrollments.select_related('user')
        if text_search:
            user_filters = Q(user__username__icontains=text_search) | Q(user__email__icontains=text_search)
            enrollments = enrollments.filter(user_filters)

        return enrollments
