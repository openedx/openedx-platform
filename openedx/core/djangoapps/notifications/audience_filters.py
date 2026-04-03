"""
Audience based filters for notifications and Notification filters
"""

import logging
from abc import abstractmethod
from typing import List

from django.utils import timezone
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from lms.djangoapps.discussion.django_comment_client.utils import get_users_with_roles
from lms.djangoapps.teams.models import CourseTeam
from openedx.core.djangoapps.course_date_signals.utils import get_expected_duration
from openedx.core.djangoapps.course_groups.models import CourseUserGroup
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role,
)
from openedx.core.djangoapps.notifications.base_notification import COURSE_NOTIFICATION_TYPES
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from xmodule.modulestore.django import modulestore

logger = logging.getLogger(__name__)


class NotificationAudienceFilterBase:
    """
    Base class for notification audience filters
    """
    def __init__(self, course_key):
        self.course_key = course_key

    allowed_filters = []

    def is_valid_filter(self, values):
        return all(value in self.allowed_filters for value in values)

    @abstractmethod
    def filter(self, values):
        pass


class ForumRoleAudienceFilter(NotificationAudienceFilterBase):
    """
    Filter class for roles
    """
    allowed_filters = [
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_GROUP_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_STUDENT,
    ]

    def filter(self, roles):
        """
        Filter users based on their roles
        """
        if not self.is_valid_filter(roles):
            raise ValueError(f'Invalid roles {roles} passed to RoleAudienceFilter')
        return [user.id for user in get_users_with_roles(roles, self.course_key)]


class CourseRoleAudienceFilter(NotificationAudienceFilterBase):
    """
    Filter class for course roles
    """
    allowed_filters = ['staff', 'instructor']

    def filter(self, course_roles):
        """
        Filter users based on their course roles
        """
        if not self.is_valid_filter(course_roles):
            raise ValueError(f'Invalid roles {course_roles} passed to CourseRoleAudienceFilter')

        user_ids = []

        course_key = self.course_key
        if not isinstance(course_key, CourseKey):
            course_key = CourseKey.from_string(course_key)

        if 'staff' in course_roles:
            staff_users = CourseStaffRole(course_key).users_with_role().values_list('id', flat=True)
            user_ids.extend(staff_users)

        if 'instructor' in course_roles:
            instructor_users = CourseInstructorRole(course_key).users_with_role().values_list('id', flat=True)
            user_ids.extend(instructor_users)

        return user_ids


class EnrollmentAudienceFilter(NotificationAudienceFilterBase):
    """
    Filter class for enrollment modes
    """
    allowed_filters = CourseMode.ALL_MODES

    def filter(self, enrollment_modes):
        """
        Filter users based on their course enrollment modes
        """
        if not self.is_valid_filter(enrollment_modes):
            raise ValueError(f'Invalid enrollment modes {enrollment_modes} passed to EnrollmentAudienceFilter')
        return CourseEnrollment.objects.filter(
            course_id=self.course_key,
            mode__in=enrollment_modes,
            is_active=True,
        ).values_list('user_id', flat=True)


class TeamAudienceFilter(NotificationAudienceFilterBase):
    """
    Filter class for team roles
    """

    def filter(self, team_ids):
        """
        Filter users based on team id
        """
        teams = CourseTeam.objects.filter(team_id__in=team_ids, course_id=self.course_key)

        if not teams:   # invalid team ids passed
            raise ValueError(f'Invalid Team ids {team_ids} passed to TeamAudienceFilter for course {self.course_key}')

        user_ids = []
        for team in teams:
            user_ids.extend(team.users.all().values_list('id', flat=True))

        return user_ids


class CohortAudienceFilter(NotificationAudienceFilterBase):
    """
    Filter class for cohort roles
    """

    def filter(self, group_ids):
        """
        Filter users based on their cohort ids
        """
        users_in_cohort = CourseUserGroup.objects.filter(
            course_id=self.course_key, id__in=group_ids
        ).values_list('users__id', flat=True)
        return users_in_cohort


class NotificationFilter:
    """
    Filter notifications based on their type
    """

    @staticmethod
    def get_users_with_course_role(user_ids: List[int], course_id: str) -> List[int]:
        """
        Get users with a course role for the given course.
        """
        return CourseAccessRole.objects.filter(
            user_id__in=user_ids,
            course_id=course_id,
        ).values_list('user_id', flat=True)

    @staticmethod
    def get_users_with_forum_roles(user_ids: List[int], course_id: str) -> List[int]:
        """
        Get users with forum roles for the given course.
        """
        return Role.objects.filter(

            course_id=course_id,
            users__id__in=user_ids,
            name__in=[
                FORUM_ROLE_MODERATOR,
                FORUM_ROLE_COMMUNITY_TA,
                FORUM_ROLE_ADMINISTRATOR,
                FORUM_ROLE_GROUP_MODERATOR,
            ]

        ).values_list('users__id', flat=True)

    def filter_audit_expired_users_with_no_role(self, user_ids, course) -> list:
        """
        Check if the user has access to the course this would be true if the user has a course role or a forum role
        """
        verified_mode = CourseMode.verified_mode_for_course(course=course, include_expired=True)
        access_duration = get_expected_duration(course.id)
        course_time_limit = CourseDurationLimitConfig.current(course_key=course.id)
        if not verified_mode:
            logger.debug(
                "NotificationFilter: Course %s does not have a verified mode, so no users will be filtered out",
                course.id,
            )
            return user_ids

        users_with_course_role = self.get_users_with_course_role(user_ids, course.id)
        users_with_forum_roles = self.get_users_with_forum_roles(user_ids, course.id)
        enrollments = CourseEnrollment.objects.filter(
            user_id__in=user_ids,
            course_id=course.id,
            mode=CourseMode.AUDIT,
            user__is_staff=False,
        )

        if course_time_limit.enabled_for_course(course.id):
            enrollments = enrollments.filter(created__gte=course_time_limit.enabled_as_of)
        logger.debug("NotificationFilter: Number of audit enrollments for course %s: %s", course.id,
                     enrollments.count())

        for enrollment in enrollments:
            if enrollment.user_id in users_with_course_role or enrollment.user_id in users_with_forum_roles:
                logger.debug(
                    "NotificationFilter: User %s has a course or forum role for course %s, so they will not be "
                    "filtered out",
                    enrollment.user_id,
                    course.id,
                )
                continue
            content_availability_date = max(enrollment.created, course.start)
            expiration_date = content_availability_date + access_duration
            logger.debug("NotificationFilter: content_availability_date: %s and access_duration: %s",
                         content_availability_date, access_duration
                         )
            if expiration_date and timezone.now() > expiration_date:
                logger.debug("User %s has expired audit access to course %s", enrollment.user_id, course.id)
                user_ids.remove(enrollment.user_id)
        return user_ids

    def apply_filters(self, user_ids, course_key, notification_type) -> list:
        """
        Apply all the filters
        """
        notification_config = COURSE_NOTIFICATION_TYPES.get(notification_type, {})
        applicable_filters = notification_config.get('filters', [])
        course = modulestore().get_course(course_key)
        for filter_name in applicable_filters:
            logger.debug(
                "NotificationFilter: Applying filter %s for notification type %s",
                filter_name,
                notification_type,
            )
            user_ids = getattr(self, filter_name)(user_ids, course)
        return user_ids
