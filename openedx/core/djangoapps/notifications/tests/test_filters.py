"""
Test for the NotificationFilter class.
"""
from datetime import timedelta
from unittest import mock
from unittest.mock import patch

import ddt
from django.utils.timezone import now

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory, CourseEnrollmentFactory
from lms.djangoapps.teams.tests.factories import CourseTeamFactory, CourseTeamMembershipFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_STUDENT,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    Role,
)
from openedx.core.djangoapps.notifications.audience_filters import (
    EnrollmentAudienceFilter,
    ForumRoleAudienceFilter,
    CourseRoleAudienceFilter,
    CohortAudienceFilter,
    TeamAudienceFilter,
    NotificationFilter,
)
from openedx.core.djangoapps.notifications.handlers import calculate_course_wide_notification_audience
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience.tests.views.helpers import add_course_mode
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class CourseExpirationTestCase(ModuleStoreTestCase):
    """Tests to verify the get_user_course_expiration_date function is working correctly"""

    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.catalog_patch = patch(
            'openedx.core.djangoapps.catalog.models.CatalogIntegration.is_enabled',
            return_value=True
        )
        self.catalog_patch.start()

        self.course = CourseFactory(
            start=now() - timedelta(weeks=10),
        )

        self.user = UserFactory()
        self.user_1 = UserFactory()

        # Make this a verified course, so we can test expiration date
        add_course_mode(self.course, mode_slug=CourseMode.AUDIT)
        add_course_mode(self.course)
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        expired_audit = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        expired_audit.created = now() - timedelta(weeks=6)
        expired_audit.save()

    def tearDown(self):
        self.catalog_patch.stop()
        super().tearDown()

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_audit_expired_filter_with_no_role(
        self,
        mock_get_course_run_details,
    ):
        """
        Test if filter_audit_expired function is working correctly
        """

        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user_1.id], result)

        mock_get_course_run_details.return_value = {'weeks_to_complete': 7}
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user.id, self.user_1.id], result)

        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=now(),
        )
        # weeks_to_complete is set to 4 because we want to test if CourseDurationLimitConfig is working correctly.
        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user.id, self.user_1.id], result)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @mock.patch(
        "openedx.core.djangoapps.notifications.audience_filters.NotificationFilter"
        ".filter_audit_expired_users_with_no_role")
    def test_apply_filter(
        self,
        mock_filter_audit_expired,
        mock_get_course_run_details,
    ):
        """
        Test if apply_filter function is working correctly
        """
        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        mock_filter_audit_expired.return_value = [self.user.id, self.user_1.id]
        result = NotificationFilter().apply_filters(
            [self.user.id, self.user_1.id],
            self.course.id,
            'new_comment_on_response'
        )
        self.assertEqual([self.user.id, self.user_1.id], result)
        mock_filter_audit_expired.assert_called_once()

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_audit_expired_for_course_staff(
        self,
        mock_get_course_run_details,
    ):
        """
        Test if filter_audit_expired function is working correctly for course staff
        """

        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user_1.id], result)
        CourseInstructorRole(self.course.id).add_users(self.user)
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user.id, self.user_1.id], result)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @ddt.data(
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_GROUP_MODERATOR,
    )
    def test_audit_expired_for_forum_roles(
        self,
        role_name,
        mock_get_course_run_details,

    ):
        """
        Test if filter_audit_expired function is working correctly for forum roles
        """

        mock_get_course_run_details.return_value = {'weeks_to_complete': 4}
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user_1.id], result)
        role = Role.objects.get_or_create(course_id=self.course.id, name=role_name)[0]
        role.users.add(self.user.id)
        result = NotificationFilter().filter_audit_expired_users_with_no_role(
            [self.user.id, self.user_1.id],
            self.course,
        )
        self.assertEqual([self.user.id, self.user_1.id], result)


def assign_enrollment_mode_to_users(course_id, users, mode):
    """
    Helper function to create an enrollment with the given mode.
    """
    for user in users:
        enrollment = CourseEnrollmentFactory.create(user=user, course_id=course_id)
        enrollment.mode = mode
        enrollment.save()


def assign_role_to_users(course_id, users, role_name):
    """
    Helper function to assign a role to a user.
    """
    role = Role.objects.create(name=role_name, course_id=course_id)
    role.users.set(users)
    role.save()


@ddt.ddt
class TestEnrollmentAudienceFilter(ModuleStoreTestCase):
    """
    Tests for the EnrollmentAudienceFilter.
    """
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.students = [UserFactory() for _ in range(30)]

        # Create 10 audit enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[:10], CourseMode.AUDIT)

        # Create 10 honor enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[10:20], CourseMode.HONOR)

        # Create 10 verified enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[20:], CourseMode.VERIFIED)

    @ddt.data(
        (["audit"], 10),
        (["audit", "honor"], 20),
        (["audit", "honor", "verified"], 30),
        (["honor"], 10),
        (["honor", "verified"], 20),
        (["verified"], 10),
    )
    @ddt.unpack
    def test_valid_enrollment_filter(self, enrollment_modes, expected_count):
        enrollment_filter = EnrollmentAudienceFilter(self.course.id)
        filtered_users = enrollment_filter.filter(enrollment_modes)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_enrollment_filter(self):
        enrollment_filter = EnrollmentAudienceFilter(self.course.id)
        enrollment_modes = ["INVALID_MODE"]
        with self.assertRaises(ValueError):
            enrollment_filter.filter(enrollment_modes)


@ddt.ddt
class TestForumRoleAudienceFilter(ModuleStoreTestCase):
    """
    Tests for the ForumRoleAudienceFilter.
    """
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.students = [UserFactory() for _ in range(25)]

        # Assign 5 users with administrator role
        assign_role_to_users(self.course.id, self.students[:5], FORUM_ROLE_ADMINISTRATOR)

        # Assign 5 users with moderator role
        assign_role_to_users(self.course.id, self.students[5:10], FORUM_ROLE_MODERATOR)

        # Assign 5 users with student role
        assign_role_to_users(self.course.id, self.students[10:15], FORUM_ROLE_STUDENT)

        # Assign 5 users with community TA role
        assign_role_to_users(self.course.id, self.students[15:20], FORUM_ROLE_COMMUNITY_TA)

        # Assign 5 users with group moderator role
        assign_role_to_users(self.course.id, self.students[20:25], FORUM_ROLE_GROUP_MODERATOR)

    @ddt.data(
        (["Administrator"], 5),
        (["Moderator"], 5),
        (["Student"], 5),
        (["Community TA"], 5),
        (["Group Moderator"], 5),
        (["Administrator", "Moderator"], 10),
        (["Administrator", "Moderator", "Student"], 15),
        (["Moderator", "Student", "Community TA"], 15),
        (["Student", "Community TA", "Group Moderator"], 15),
        (["Community TA", "Group Moderator"], 10),
        (["Administrator", "Moderator", "Student", "Community TA", "Group Moderator"], 25),
    )
    @ddt.unpack
    def test_valid_role_filter(self, role_names, expected_count):
        role_filter = ForumRoleAudienceFilter(self.course.id)
        filtered_users = role_filter.filter(role_names)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_role_filter(self):
        role_filter = ForumRoleAudienceFilter(self.course.id)
        role_names = ["INVALID_MODE"]
        with self.assertRaises(ValueError):
            role_filter.filter(role_names)


# TODO: Cleanup this test class
@ddt.ddt
class TestCourseRoleAudienceFilter(ModuleStoreTestCase):
    """
    Tests for the CourseRoleAudienceFilter.
    """
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.students = [UserFactory() for _ in range(10)]

        # Assign 5 users with course staff role
        for student in self.students[:5]:
            CourseStaffRole(self.course.id).add_users(student)

        # Assign 5 users with course instructor role
        for student in self.students[5:10]:
            CourseInstructorRole(self.course.id).add_users(student)

    @ddt.data(
        (["instructor"], 5),
        (["staff"], 5),
        (["instructor", "staff"], 10),
    )
    @ddt.unpack
    def test_valid_role_filter(self, role_names, expected_count):
        course_role_filter = CourseRoleAudienceFilter(self.course.id)
        filtered_users = course_role_filter.filter(role_names)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_role_filter(self):
        course_role_filter = CourseRoleAudienceFilter(self.course.id)
        role_names = ["INVALID_MODE"]
        with self.assertRaises(ValueError):
            course_role_filter.filter(role_names)


@ddt.ddt
class TestCohortAudienceFilter(ModuleStoreTestCase):
    """
    Tests for the CohortAudienceFilter.
    """
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()

        self.cohort1_users = [UserFactory() for _ in range(3)]
        self.cohort2_users = [UserFactory() for _ in range(2)]

        users = self.cohort1_users + self.cohort2_users

        for user in users:
            CourseEnrollment.enroll(user, self.course.id)

        self.cohort1 = CohortFactory(course_id=self.course.id, users=self.cohort1_users)
        self.cohort2 = CohortFactory(course_id=self.course.id, users=self.cohort2_users)

    @ddt.data(
        ([1], 3),
        ([2], 2),
        ([1, 2], 5),
    )
    @ddt.unpack
    def test_valid_cohort_filter(self, cohort_ids, expected_count):
        cohort_filter = CohortAudienceFilter(self.course.id)
        filtered_users = cohort_filter.filter(cohort_ids)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_cohort_filter(self):
        cohort_filter = CohortAudienceFilter(self.course.id)
        cohort_ids = ["INVALID_MODE"]
        with self.assertRaises(ValueError):
            cohort_filter.filter(cohort_ids)


@ddt.ddt
class TestTeamAudienceFilter(ModuleStoreTestCase):
    """
    Tests for the TeamAudienceFilter.
    """
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.teams = [CourseTeamFactory(course_id=self.course.id, team_id=f"team-{i}") for i in range(2)]

        self.team1_users = [UserFactory() for _ in range(3)]
        self.team2_users = [UserFactory() for _ in range(2)]

        users = self.team1_users + self.team2_users

        for user in users:
            CourseEnrollment.enroll(user, self.course.id)

        for user in self.team1_users:
            CourseTeamMembershipFactory.create(team=self.teams[0], user=user)

        for user in self.team2_users:
            CourseTeamMembershipFactory.create(team=self.teams[1], user=user)

    @ddt.data(
        (["team-0"], 3),
        (["team-1"], 2),
        (["team-0", "team-1"], 5),
    )
    @ddt.unpack
    def test_valid_team_filter(self, team_ids, expected_count):
        team_filter = TeamAudienceFilter(self.course.id)
        filtered_users = team_filter.filter(team_ids)
        self.assertEqual(len(filtered_users), expected_count)

    def test_invalid_team_filter(self):
        team_filter = TeamAudienceFilter(self.course.id)
        team_ids = ["INVALID_MODE"]
        with self.assertRaises(ValueError):
            team_filter.filter(team_ids)


@ddt.ddt
class TestAudienceFilter(ModuleStoreTestCase):
    """
    Tests for audience filtration based on different filters.
    """
    def setUp(self):
        super().setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        self.course = CourseFactory()
        self.students = [UserFactory() for _ in range(30)]

        # Create 10 audit enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[:10], CourseMode.AUDIT)

        # Create 10 honor enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[10:20], CourseMode.HONOR)

        # Create 10 verified enrollments
        assign_enrollment_mode_to_users(self.course.id, self.students[20:], CourseMode.VERIFIED)

        # Assign 5 users with administrator role
        assign_role_to_users(self.course.id, self.students[:5], FORUM_ROLE_ADMINISTRATOR)

        # Assign 5 users with moderator role
        assign_role_to_users(self.course.id, self.students[5:10], FORUM_ROLE_MODERATOR)

        # Assign 5 users with student role
        assign_role_to_users(self.course.id, self.students[10:15], FORUM_ROLE_STUDENT)

        # Assign 5 users with community TA role
        assign_role_to_users(self.course.id, self.students[15:20], FORUM_ROLE_COMMUNITY_TA)

        # Assign 5 users with group moderator role
        assign_role_to_users(self.course.id, self.students[20:25], FORUM_ROLE_GROUP_MODERATOR)

    @ddt.data(
        ({
            "enrollments": ["verified"],
            "discussion_roles": ["Moderator"],
        }, 15),
        ({
            "enrollments": ["audit", "verified"],
            "discussion_roles": ["Administrator", "Student"],
        }, 30),
        ({
            "enrollments": ["audit", "honor", "verified"],
            "discussion_roles": ["Administrator", "Moderator", "Student", "Community TA"],
        }, 30),
    )
    @ddt.unpack
    def test_combination_of_audience_filters(self, audience_filters, expected_count):
        user_ids = calculate_course_wide_notification_audience(self.course.id, audience_filters)
        self.assertEqual(len(user_ids), expected_count)

    def test_invalid_audience_filter(self):
        audience_filters = {
            "invalid_filter": ["invalid_filter_type"],
        }
        with self.assertRaises(ValueError):
            calculate_course_wide_notification_audience(self.course.id, audience_filters)
