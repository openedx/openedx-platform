# lint-amnesty, pylint: disable=missing-module-docstring
import datetime
import hashlib
from unittest import mock
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import ddt
from crum import set_current_request
from django.contrib.auth.models import AnonymousUser, User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.cache import cache
from django.conf import settings
from django.db.models.functions import Lower
from django.test import TestCase, override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    IS_MARKETABLE,
    AccountRecovery,
    CourseEnrollment,
    CourseEnrollmentAllowed,
    ManualEnrollmentAudit,
    PendingEmailChange,
    PendingNameChange,
    UserAttribute,
    UserCelebration,
    UserProfile
)
from common.djangoapps.student.models_api import confirm_name_change, do_name_change_request, get_name
from common.djangoapps.student.tests.factories import AccountRecoveryFactory, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.models import DynamicUpgradeDeadlineConfiguration
from lms.djangoapps.courseware.toggles import (
    COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES,
    COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class CourseEnrollmentTests(SharedModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory()

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.user_2 = UserFactory()

    def test_enrollment_status_hash_cache_key(self):
        username = 'test-user'
        user = UserFactory(username=username)
        expected = 'enrollment_status_hash_' + username
        assert CourseEnrollment.enrollment_status_hash_cache_key(user) == expected

    def assert_enrollment_status_hash_cached(self, user, expected_value):
        assert cache.get(CourseEnrollment.enrollment_status_hash_cache_key(user)) == expected_value

    def test_generate_enrollment_status_hash(self):
        """ Verify the method returns a hash of a user's current enrollments. """
        # Return None for anonymous users
        assert CourseEnrollment.generate_enrollment_status_hash(AnonymousUser()) is None

        # No enrollments
        expected = hashlib.md5(self.user.username.encode('utf-8')).hexdigest()  # lint-amnesty, pylint: disable=no-member
        assert CourseEnrollment.generate_enrollment_status_hash(self.user) == expected
        self.assert_enrollment_status_hash_cached(self.user, expected)

        # No active enrollments
        enrollment_mode = 'verified'
        course_id = self.course.id  # pylint: disable=no-member
        enrollment = CourseEnrollmentFactory.create(user=self.user, course_id=course_id, mode=enrollment_mode,
                                                    is_active=False)
        assert CourseEnrollment.generate_enrollment_status_hash(self.user) == expected
        self.assert_enrollment_status_hash_cached(self.user, expected)

        # One active enrollment
        enrollment.is_active = True
        enrollment.save()
        expected = '{username}&{course_id}={mode}'.format(
            username=self.user.username, course_id=str(course_id).lower(), mode=enrollment_mode.lower()
        )
        expected = hashlib.md5(expected.encode('utf-8')).hexdigest()
        assert CourseEnrollment.generate_enrollment_status_hash(self.user) == expected
        self.assert_enrollment_status_hash_cached(self.user, expected)

        # Multiple enrollments
        CourseEnrollmentFactory.create(user=self.user)
        enrollments = CourseEnrollment.enrollments_for_user(self.user).order_by(Lower('course_id'))
        hash_elements = [self.user.username]
        hash_elements += [
            f'{str(enrollment.course_id).lower()}={enrollment.mode.lower()}' for
            enrollment in enrollments]
        expected = hashlib.md5('&'.join(hash_elements).encode('utf-8')).hexdigest()
        assert CourseEnrollment.generate_enrollment_status_hash(self.user) == expected
        self.assert_enrollment_status_hash_cached(self.user, expected)

    def test_save_deletes_cached_enrollment_status_hash(self):
        """ Verify the method deletes the cached enrollment status hash for the user. """
        # There should be no cached value for a new user with no enrollments.
        assert cache.get(CourseEnrollment.enrollment_status_hash_cache_key(self.user)) is None

        # Generating a status hash should cache the generated value.
        status_hash = CourseEnrollment.generate_enrollment_status_hash(self.user)
        self.assert_enrollment_status_hash_cached(self.user, status_hash)

        # Modifying enrollments should delete the cached value.
        CourseEnrollmentFactory.create(user=self.user)
        assert cache.get(CourseEnrollment.enrollment_status_hash_cache_key(self.user)) is None

    def test_users_enrolled_in_active_only(self):
        """CourseEnrollment.users_enrolled_in should return only Users with active enrollments when
        `include_inactive` has its default value (False)."""
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)  # lint-amnesty, pylint: disable=no-member
        CourseEnrollmentFactory.create(user=self.user_2, course_id=self.course.id, is_active=False)  # lint-amnesty, pylint: disable=no-member

        active_enrolled_users = list(CourseEnrollment.objects.users_enrolled_in(self.course.id))  # lint-amnesty, pylint: disable=no-member
        assert [self.user] == active_enrolled_users

    def test_users_enrolled_in_all(self):
        """CourseEnrollment.users_enrolled_in should return active and inactive users when
        `include_inactive` is True."""
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id, is_active=True)  # lint-amnesty, pylint: disable=no-member
        CourseEnrollmentFactory.create(user=self.user_2, course_id=self.course.id, is_active=False)  # lint-amnesty, pylint: disable=no-member

        all_enrolled_users = list(
            CourseEnrollment.objects.users_enrolled_in(self.course.id, include_inactive=True)  # lint-amnesty, pylint: disable=no-member
        )
        self.assertListEqual([self.user, self.user_2], all_enrolled_users)

    @skip_unless_lms
    def test_upgrade_deadline(self):
        """ The property should use either the CourseMode or related Schedule to determine the deadline. """
        course = CourseFactory(self_paced=True)
        course_mode = CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=1)
        )
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        Schedule.objects.all().delete()
        assert enrollment.upgrade_deadline == course_mode.expiration_datetime

    @skip_unless_lms
    def test_upgrade_deadline_with_schedule(self):
        """ The property should use either the CourseMode or related Schedule to determine the deadline. """
        course = CourseFactory(self_paced=True)
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=30),
        )
        course_overview = CourseOverview.load_from_module_store(course.id)
        CourseEnrollmentFactory(
            course_id=course.id,
            mode=CourseMode.AUDIT,
            course=course_overview,
        )
        Schedule.objects.update(upgrade_deadline=datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=5))
        enrollment = CourseEnrollment.objects.first()

        # The schedule's upgrade deadline should be used if a schedule exists
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        assert enrollment.upgrade_deadline == enrollment.schedule.upgrade_deadline

    @skip_unless_lms
    @ddt.data(*(set(CourseMode.ALL_MODES) - set(CourseMode.AUDIT_MODES)))
    def test_upgrade_deadline_for_non_upgradeable_enrollment(self, mode):
        """ The property should return None if an upgrade cannot be upgraded. """
        enrollment = CourseEnrollmentFactory(course_id=self.course.id, mode=mode)  # lint-amnesty, pylint: disable=no-member
        assert enrollment.upgrade_deadline is None

    @skip_unless_lms
    def test_upgrade_deadline_instructor_paced(self):
        course = CourseFactory(self_paced=False)
        course_upgrade_deadline = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=1)
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=course_upgrade_deadline
        )
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        assert enrollment.schedule is not None
        assert enrollment.upgrade_deadline == course_upgrade_deadline

    @skip_unless_lms
    def test_upgrade_deadline_with_schedule_and_professional_mode(self):
        """
        Deadline should be None for courses with professional mode.

        Regression test for EDUCATOR-2419.
        """
        course = CourseFactory(self_paced=True)
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.PROFESSIONAL,
        )
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        assert enrollment.schedule is not None
        assert enrollment.upgrade_deadline is None

    @skip_unless_lms
    def test_enrollments_not_deleted(self):
        """ Recreating a CourseOverview with an outdated version should not delete the associated enrollment. """
        course = CourseFactory(self_paced=True)
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=30),
        )

        # Create a CourseOverview with an outdated version
        course_overview = CourseOverview.load_from_module_store(course.id)
        course_overview.version = CourseOverview.VERSION - 1
        course_overview.save()

        # Create an inactive enrollment with this course overview
        enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=course.id,
            mode=CourseMode.AUDIT,
            course=course_overview,
        )

        # Re-fetch the CourseOverview record.
        # As a side effect, this will recreate the record, and update the version.
        course_overview_new = CourseOverview.get_from_id(course.id)
        assert course_overview_new.version == CourseOverview.VERSION

        # Ensure that the enrollment record was unchanged during this re-creation
        enrollment_refetched = CourseEnrollment.objects.filter(id=enrollment.id)
        assert enrollment_refetched.exists()
        assert enrollment_refetched.all()[0] == enrollment


@override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES, active=True)
@override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION, active=True)
class UserCelebrationTests(SharedModuleStoreTestCase):
    """
    Tests for User Celebrations like the streak celebration
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory(default_store=ModuleStoreEnum.Type.split)
        cls.course_key = cls.course.id  # pylint: disable=no-member

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.request = mock.MagicMock()
        self.request.user = self.user
        CourseEnrollmentFactory(course_id=self.course_key)
        UserCelebration.STREAK_LENGTHS_TO_CELEBRATE = [3]
        UserCelebration.STREAK_BREAK_LENGTH = 1
        self.STREAK_LENGTH_TO_CELEBRATE = UserCelebration.STREAK_LENGTHS_TO_CELEBRATE[0]
        self.STREAK_BREAK_LENGTH = UserCelebration.STREAK_BREAK_LENGTH
        set_current_request(self.request)
        self.addCleanup(set_current_request, None)

    def test_first_check_streak_celebration(self):
        STREAK_LENGTH_TO_CELEBRATE = UserCelebration.perform_streak_updates(self.user, self.course_key)
        today = datetime.datetime.now(ZoneInfo("UTC")).date()
        assert self.user.celebration.streak_length == 1
        assert self.user.celebration.last_day_of_streak == today
        assert STREAK_LENGTH_TO_CELEBRATE is None

    # pylint: disable=line-too-long
    def test_celebrate_only_once_in_continuous_streak(self):
        """
        Sample run for a 3 day streak and 1 day break. See last column for explanation.
        +---------+---------------------+--------------------+-------------------------+------------------+------------------+
        | today   | streak_length       | last_day_of_streak | streak_length_to_celebrate | Note                             |
        +---------+---------------------+--------------------+-------------------------+------------------+------------------+
        | 2/4/21  | 1                   | 2/4/21             | None                    | Day 1 of Streak                     |
        | 2/5/21  | 2                   | 2/5/21             | None                    | Day 2 of Streak                     |
        | 2/6/21  | 3                   | 2/6/21             | 3                       | Completed 3 Day Streak so we should celebrate |
        | 2/7/21  | 4                   | 2/7/21             | None                    | Day 4 of Streak                     |
        | 2/8/21  | 5                   | 2/8/21             | None                    | Day 5 of Streak                     |
        | 2/9/21  | 6                   | 2/9/21             | None                    | Day 6 of Streak                     |
        +---------+---------------------+--------------------+-------------------------+------------------+------------------+
        """
        now = datetime.datetime.now(ZoneInfo("UTC"))
        for i in range(1, (self.STREAK_LENGTH_TO_CELEBRATE * 2) + 1):
            with freeze_time(now + datetime.timedelta(days=i)):
                STREAK_LENGTH_TO_CELEBRATE = UserCelebration.perform_streak_updates(self.user, self.course_key)
                assert bool(STREAK_LENGTH_TO_CELEBRATE) == (i == self.STREAK_LENGTH_TO_CELEBRATE)

    # pylint: disable=line-too-long
    def test_longest_streak_updates_correctly(self):
        """
        Sample run for a 3 day streak and 1 day break. See last column for explanation.
        +---------+---------------------+--------------------+-------------------------+------------------+---------------------+
        | today   | streak_length       | last_day_of_streak | streak_length_to_celebrate | Note                                |
        +---------+---------------------+--------------------+-------------------------+------------------+---------------------+
        | 2/4/21  | 1                   | 2/4/21             | None                    | longest_streak_ever is 1               |
        | 2/5/21  | 2                   | 2/5/21             | None                    | longest_streak_ever is 2               |
        | 2/6/21  | 3                   | 2/6/21             | 3                       | longest_streak_ever is 3               |
        | 2/7/21  | 4                   | 2/7/21             | None                    | longest_streak_ever is 4               |
        | 2/8/21  | 5                   | 2/8/21             | None                    | longest_streak_ever is 5               |
        | 2/9/21  | 6                   | 2/9/21             | None                    | longest_streak_ever is 6               |
        +---------+---------------------+--------------------+-------------------------+------------------+---------------------+
        """
        now = datetime.datetime.now(ZoneInfo("UTC"))
        for i in range(1, (self.STREAK_LENGTH_TO_CELEBRATE * 2) + 1):
            with freeze_time(now + datetime.timedelta(days=i)):
                UserCelebration.perform_streak_updates(self.user, self.course_key)
                assert self.user.celebration.longest_ever_streak == i

    # pylint: disable=line-too-long
    def test_celebrate_only_once_with_multiple_calls_on_the_same_day(self):
        """
        Sample run for a 3 day streak and 1 day break. See last column for explanation.
        +---------+---------------------+--------------------+-------------------------+------------------+----------------------------+
        | today   | streak_length       | last_day_of_streak | streak_length_to_celebrate | Note                                       |
        +---------+---------------------+--------------------+-------------------------+------------------+----------------------------+
        | 2/4/21  | 1                   | 2/4/21             | None                    | Day 1 of Streak                               |
        | 2/4/21  | 1                   | 2/4/21             | None                    | Day 1 of Streak                               |
        | 2/5/21  | 2                   | 2/5/21             | None                    | Day 2 of Streak                               |
        | 2/5/21  | 2                   | 2/5/21             | None                    | Day 2 of Streak                               |
        | 2/6/21  | 3                   | 2/6/21             | 3                       | Completed 3 Day Streak so we should celebrate |
        | 2/6/21  | 3                   | 2/6/21             | None                    | Already celebrated this streak.               |
        +---------+---------------------+--------------------+-------------------------+------------------+----------------------------+
        """
        now = datetime.datetime.now(ZoneInfo("UTC"))
        for i in range(1, self.STREAK_LENGTH_TO_CELEBRATE + 1):
            with freeze_time(now + datetime.timedelta(days=i)):
                streak_length_to_celebrate = UserCelebration.perform_streak_updates(self.user, self.course_key)
                assert bool(streak_length_to_celebrate) == (i == self.STREAK_LENGTH_TO_CELEBRATE)
                streak_length_to_celebrate = UserCelebration.perform_streak_updates(self.user, self.course_key)
                assert streak_length_to_celebrate is None

    def test_celebration_with_user_passed_in_timezone(self):
        """
        Check that the _get_now method uses the user's timezone from the browser if none is configured
        """
        now = UserCelebration._get_now('Asia/Tokyo')  # pylint: disable=protected-access
        assert str(now.tzinfo) == 'Asia/Tokyo'

    def test_celebration_with_user_configured_timezone(self):
        """
        Check that the _get_now method uses the user's configured timezone
        over the browser timezone that is passed in as a parameter
        """
        set_user_preference(self.user, 'time_zone', 'Asia/Tokyo')
        now = UserCelebration._get_now('America/New_York')  # pylint: disable=protected-access
        assert str(now.tzinfo) == 'Asia/Tokyo'

    # pylint: disable=line-too-long
    def test_celebrate_twice_with_broken_streak_in_between(self):
        """
        Sample run for a 3 day streak and 1 day break. See last column for explanation.
        +---------+---------------------+--------------------+-------------------------+------------------+-----------------------------------------------+
        | today   | streak_length       | last_day_of_streak | streak_length_to_celebrate | Note                                |
        +---------+---------------------+--------------------+-------------------------+------------------+-----------------------------------------------+
        | 2/4/21  | 1                   | 2/4/21             | None                    | Day 1 of Streak                               |
        | 2/5/21  | 2                   | 2/5/21             | None                    | Day 2 of Streak                               |
        | 2/6/21  | 3                   | 2/6/21             | 3                       | Completed 3 Day Streak so we should celebrate |
          No Accesses on 2/7/21
        | 2/8/21  | 1                   | 2/8/21             | None                    | Day 1 of Streak                               |
        | 2/9/21  | 2                   | 2/9/21             | None                    | Day 2 of Streak                               |
        | 2/10/21 | 3                   | 2/10/21            | 3                       | Completed 3 Day Streak so we should celebrate |
        +---------+---------------------+--------------------+-------------------------+------------------+-----------------------------------------------+
        """
        now = datetime.datetime.now(ZoneInfo("UTC"))
        for i in range(1, self.STREAK_LENGTH_TO_CELEBRATE + self.STREAK_BREAK_LENGTH + self.STREAK_LENGTH_TO_CELEBRATE + 1):
            with freeze_time(now + datetime.timedelta(days=i)):
                if self.STREAK_LENGTH_TO_CELEBRATE < i <= self.STREAK_LENGTH_TO_CELEBRATE + self.STREAK_BREAK_LENGTH:
                    # Don't make any checks during the break
                    continue
                streak_length_to_celebrate = UserCelebration.perform_streak_updates(self.user, self.course_key)
                if i <= self.STREAK_LENGTH_TO_CELEBRATE:
                    assert bool(streak_length_to_celebrate) == (i == self.STREAK_LENGTH_TO_CELEBRATE)
                else:
                    assert bool(streak_length_to_celebrate) == (i == self.STREAK_LENGTH_TO_CELEBRATE + self.STREAK_BREAK_LENGTH + self.STREAK_LENGTH_TO_CELEBRATE)

    # pylint: disable=line-too-long
    def test_streak_resets_if_day_is_missed(self):
        """
        Sample run for a 3 day streak and 1 day break with the learner coming back every other day.
        Therefore the streak keeps resetting.
        +---------+---------------------+--------------------+-------------------------+------------------+-----------------------------------------------+
        | today   | streak_length       | last_day_of_streak | streak_length_to_celebrate | Note                                          |
        +---------+---------------------+--------------------+-------------------------+------------------+-----------------------------------------------+
        | 2/4/21  | 1                   | 2/4/21             | None                    | Day 1 of Streak                               |
          No Accesses on 2/5/21
        | 2/6/21  | 1                   | 2/6/21             | None                    | Day 2 of streak was missed, so streak resets  |
          No Accesses on 2/7/21
        | 2/8/21  | 1                   | 2/8/21             | None                    | Day 2 of streak was missed, so streak resets  |
          No Accesses on 2/9/21
        | 2/10/21 | 1                   | 2/10/21            | None                    | Day 2 of streak was missed, so streak resets  |
          No Accesses on 2/11/21
        | 2/12/21 | 1                   | 2/12/21            | None                    | Day 2 of streak was missed, so streak resets  |
        +---------+---------------------+--------------------+-------------------------+------------------+-----------------------------------------------+
        """
        now = datetime.datetime.now(ZoneInfo("UTC"))
        for i in range(1, self.STREAK_LENGTH_TO_CELEBRATE * 3 + 1, 2):
            with freeze_time(now + datetime.timedelta(days=i)):
                streak_length_to_celebrate = UserCelebration.perform_streak_updates(self.user, self.course_key)
                assert self.user.celebration.last_day_of_streak == (now + datetime.timedelta(days=i)).date()
                assert streak_length_to_celebrate is None

    # pylint: disable=line-too-long
    def test_streak_does_not_reset_if_day_is_missed_with_longer_break(self):
        """
        Sample run for a 3 day streak with the learner coming back every other day.
        See last column for explanation.
        +---------+---------------------+--------------------+-------------------------+------------------+
        | today   | streak_length       | last_day_of_streak | streak_length_to_celebrate | Note          |
        +---------+---------------------+--------------------+-------------------------+------------------+
        | 2/4/21  | 1                   | 2/4/21             | None                    | Day 1 of Streak  |
          No Accesses on 2/5/21
        | 2/6/21  | 2                   | 2/6/21             | None                    | Day 2 of Streak  |
          No Accesses on 2/7/21
        | 2/8/21  | 3                   | 2/8/21             | 3                       | Day 3 of streak  |
          No Accesses on 2/9/21
        | 2/10/21 | 4                   | 2/10/21            | None                    | Day 4 of streak  |
          No Accesses on 2/11/21
        | 2/12/21 | 5                   | 2/12/21            | None                    | Day 5 of streak  |
        +---------+---------------------+--------------------+-------------------------+------------------+
        """
        UserCelebration.STREAK_BREAK_LENGTH = 2
        now = datetime.datetime.now(ZoneInfo("UTC"))
        for i in range(1, self.STREAK_LENGTH_TO_CELEBRATE * 3 + 1, 2):
            with freeze_time(now + datetime.timedelta(days=i)):
                streak_length_to_celebrate = UserCelebration.perform_streak_updates(self.user, self.course_key)
                assert bool(streak_length_to_celebrate) == (i == 5)

    def test_streak_masquerade(self):
        """ Don't update streak data when masquerading as a specific student """
        # Update streak data when not masquerading
        with mock.patch.object(UserCelebration, '_update_streak') as update_streak_mock:
            for _ in range(1, self.STREAK_LENGTH_TO_CELEBRATE + 1):
                UserCelebration.perform_streak_updates(self.user, self.course_key)
                update_streak_mock.assert_called()

        # Don't update streak data when masquerading as a specific student
        with mock.patch('lms.djangoapps.courseware.masquerade.is_masquerading_as_specific_student', return_value=True):
            with mock.patch.object(UserCelebration, '_update_streak') as update_streak_mock:
                for _ in range(1, self.STREAK_LENGTH_TO_CELEBRATE + 1):
                    UserCelebration.perform_streak_updates(self.user, self.course_key)
                    update_streak_mock.assert_not_called()

    def test_event_emit_when_streak_is_updated(self):
        """
        Ensure we call the `emit_streak_update_event` method when a streak is updated.
        """
        with mock.patch.object(UserCelebration, '_emit_streak_update_event') as emit_streak_event_mock:
            UserCelebration.perform_streak_updates(self.user, self.course_key)
            emit_streak_event_mock.assert_called_once()

    @mock.patch("common.djangoapps.student.models.user.tracker.emit")
    @mock.patch("common.djangoapps.student.models.tracker.get_tracker")
    def test_emit_streak_update_event(self, mock_get_tracker, mock_tracker):
        """
        Ensure the event emission code of the `emit_streak_update_event` method works as expected.
        """
        mock_context_manager = MagicMock()
        mock_context_manager.__enter__.return_value = None
        mock_context_manager.__exit__.return_value = None

        mock_tracker_instance = MagicMock()
        mock_tracker_instance.context.return_value = mock_context_manager
        mock_get_tracker.return_value = mock_tracker_instance

        expected_data = {
            "user_id": self.user.id,
            "current_course_id": str(self.course_key),
            "current_streak_length": 4,
        }

        celebration = UserCelebration()
        # pylint: disable=protected-access
        celebration._emit_streak_update_event(self.user, str(self.course_key), 4)
        # pylint: enable=protected-access

        mock_tracker_instance.context.assert_called_once_with(
            "edx.user.celebration.streak_updated",
            {
                "user_id": self.user.id,
                "course_id": str(self.course_key),
            }
        )
        mock_tracker.assert_called_once_with(
            "edx.user.celebration.streak_updated",
            expected_data,
        )


class PendingNameChangeTests(SharedModuleStoreTestCase):
    """
    Tests the deletion of PendingNameChange records
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory()
        cls.user2 = UserFactory()
        cls.name = cls.user.profile.name
        cls.new_name = 'New Name'
        cls.updated_name = 'Updated Name'
        cls.rationale = 'Testing name change'

    def test_do_name_change_request(self):
        """
        Test basic name change request functionality.
        """
        do_name_change_request(self.user, self.new_name, self.rationale)
        self.assertEqual(PendingNameChange.objects.count(), 1)

    def test_same_name(self):
        """
        Test that attempting a name change with the same name as the user's current profile
        name will not result in a new pending name change request.
        """
        pending_name_change = do_name_change_request(self.user, self.name, self.rationale)[0]
        self.assertIsNone(pending_name_change)

    def test_update_name_change(self):
        """
        Test that if a user already has a name change request, creating another request will
        update the current one.
        """
        do_name_change_request(self.user, self.new_name, self.rationale)
        do_name_change_request(self.user, self.updated_name, self.rationale)
        self.assertEqual(PendingNameChange.objects.count(), 1)
        pending_name_change = PendingNameChange.objects.get(user=self.user)
        self.assertEqual(pending_name_change.new_name, self.updated_name)

    def test_confirm_name_change(self):
        """
        Test that confirming a name change request updates the user's profile name and deletes
        the request.
        """
        pending_name_change = do_name_change_request(self.user, self.new_name, self.rationale)[0]
        confirm_name_change(self.user, pending_name_change)
        user_profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(user_profile.name, self.new_name)
        self.assertEqual(PendingNameChange.objects.count(), 0)

    def test_delete_by_user_removes_pending_name_change(self):
        do_name_change_request(self.user, self.new_name, self.rationale)
        record_was_deleted = PendingNameChange.delete_by_user_value(self.user, field='user')
        assert record_was_deleted
        assert 0 == len(PendingNameChange.objects.all())

    def test_delete_by_user_no_effect_for_user_with_no_name_change(self):
        do_name_change_request(self.user, self.new_name, self.rationale)
        record_was_deleted = PendingNameChange.delete_by_user_value(self.user2, field='user')
        assert not record_was_deleted
        assert 1 == len(PendingNameChange.objects.all())


class PendingEmailChangeTests(SharedModuleStoreTestCase):
    """
    Tests the deletion of PendingEmailChange records.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory()
        cls.user2 = UserFactory()

    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        self.email_change, _ = PendingEmailChange.objects.get_or_create(
            user=self.user,
            new_email='new@example.com',
            activation_key='a' * 32
        )

    def test_delete_by_user_removes_pending_email_change(self):
        record_was_deleted = PendingEmailChange.delete_by_user_value(self.user, field='user')
        assert record_was_deleted
        assert 0 == len(PendingEmailChange.objects.all())

    def test_delete_by_user_no_effect_for_user_with_no_email_change(self):
        record_was_deleted = PendingEmailChange.delete_by_user_value(self.user2, field='user')
        assert not record_was_deleted
        assert 1 == len(PendingEmailChange.objects.all())


class TestCourseEnrollmentAllowed(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def setUp(self):
        super().setUp()
        self.email = 'learner@example.com'
        self.course_key = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
        self.user = UserFactory.create(email=self.email)
        self.allowed_enrollment = CourseEnrollmentAllowed.objects.create(
            email=self.email,
            course_id=self.course_key,
            user=self.user
        )

    def test_retiring_user_deletes_record(self):
        is_successful = CourseEnrollmentAllowed.delete_by_user_value(
            value=self.email,
            field='email'
        )
        assert is_successful
        user_search_results = CourseEnrollmentAllowed.objects.filter(
            email=self.email
        )
        assert not user_search_results

    def test_retiring_nonexistent_user_doesnt_modify_records(self):
        is_successful = CourseEnrollmentAllowed.delete_by_user_value(
            value='nonexistentlearner@example.com',
            field='email'
        )
        assert not is_successful
        user_search_results = CourseEnrollmentAllowed.objects.filter(
            email=self.email
        )
        assert user_search_results.exists()

    def test_may_enroll_and_unenrolled_result_is_based_on_unmarked_user_field(self):
        """
        Make sure that if an allowed student has no assigned user in its user field,
        then it must be counted by may_enroll_and_unenrolled.
        """
        # Unmark the user field.
        self.allowed_enrollment.user = None
        self.allowed_enrollment.save()

        assert 1 == CourseEnrollmentAllowed.may_enroll_and_unenrolled(course_id=self.course_key).count()

    def test_former_allowed_student_isnt_counted_if_email_is_updated(self):
        """
        Make sure that if allowed students change their emails after being registered,
        then they are not counted.
        """
        # Once user registers, then it is enrolled in the course.
        CourseEnrollment.enroll(
            user=self.user,
            course_key=self.course_key,
        )
        # Change user's email.
        self.user.email = 'updated-learner@example.com'
        self.user.save()

        assert 0 == CourseEnrollmentAllowed.may_enroll_and_unenrolled(course_id=self.course_key).count()

    def test_may_enroll_and_unenrolled_does_not_count_unenrolled_users(self):
        """Validate that the unenrollment action has no effect on the result of may_enroll_and_unenrolled."""
        # Simulating that the users is not yet registered in the platform.
        self.allowed_enrollment.user = None
        self.allowed_enrollment.save()
        # User registers, then it is enrolled.
        CourseEnrollment.enroll(
            user=self.user,
            course_key=self.course_key,
        )
        # Unenroll user from course.
        CourseEnrollment.unenroll(
            user=self.user,
            course_id=self.course_key,
        )

        assert 0 == CourseEnrollmentAllowed.may_enroll_and_unenrolled(course_id=self.course_key).count()


class TestManualEnrollmentAudit(SharedModuleStoreTestCase):
    """
    Tests for the ManualEnrollmentAudit model.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory()
        cls.other_course = CourseFactory()
        cls.user = UserFactory()
        cls.instructor = UserFactory(username='staff', is_staff=True)

    def test_retirement(self):
        """
        Tests that calling the retirement method for a specific enrollment retires
        the enrolled_email and reason columns of each row associated with that
        enrollment.
        """
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)  # lint-amnesty, pylint: disable=no-member
        other_enrollment = CourseEnrollment.enroll(self.user, self.other_course.id)  # lint-amnesty, pylint: disable=no-member
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            self.instructor, self.user.email, ALLOWEDTOENROLL_TO_ENROLLED,
            'manually enrolling unenrolled user', enrollment
        )
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            self.instructor, self.user.email, ALLOWEDTOENROLL_TO_ENROLLED,
            'manually enrolling unenrolled user again', enrollment
        )
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            self.instructor, self.user.email, ALLOWEDTOENROLL_TO_ENROLLED,
            'manually enrolling unenrolled user', other_enrollment
        )
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            self.instructor, self.user.email, ALLOWEDTOENROLL_TO_ENROLLED,
            'manually enrolling unenrolled user again', other_enrollment
        )
        assert ManualEnrollmentAudit.objects.filter(enrollment=enrollment).exists()
        # retire the ManualEnrollmentAudit objects associated with the above enrollments
        ManualEnrollmentAudit.retire_manual_enrollments(user=self.user, retired_email="xxx")
        assert ManualEnrollmentAudit.objects.filter(enrollment=enrollment).exists()
        assert not ManualEnrollmentAudit.objects.filter(enrollment=enrollment).exclude(enrolled_email='xxx')
        assert not ManualEnrollmentAudit.objects.filter(enrollment=enrollment).exclude(reason='')


class TestAccountRecovery(TestCase):
    """
    Tests for the AccountRecovery Model
    """

    def test_retire_recovery_email(self):
        """
        Assert that Account Record for a given user is deleted when `retire_recovery_email` is called
        """
        # Create user and associated recovery email record
        user = UserFactory()
        AccountRecoveryFactory(user=user)
        assert len(AccountRecovery.objects.filter(user_id=user.id)) == 1

        # Retire recovery email
        AccountRecovery.retire_recovery_email(user_id=user.id)

        # Assert that there is no longer an AccountRecovery record for this user
        assert len(AccountRecovery.objects.filter(user_id=user.id)) == 0


@ddt.ddt
class TestUserPostSaveCallback(SharedModuleStoreTestCase):
    """
    Tests for the user post save callback.
    These tests are to ensure that user activation auto-enrolls invited users into courses without
    changing any existing course mode states.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    @ddt.data(*(set(CourseMode.ALL_MODES) - set(CourseMode.AUDIT_MODES)))
    def test_paid_user_not_downgraded_on_activation(self, mode):
        """
        Make sure that students who are already enrolled + have paid do not get downgraded to audit mode
        when their account is activated.
        """
        # fixture
        student = self._set_up_invited_student(
            course=self.course,
            active=False,
            course_mode=mode
        )

        # trigger the post_save callback
        student.is_active = True
        student.save()

        # reload values from the database + make sure they are in the expected state
        actual_course_enrollment = CourseEnrollment.objects.get(user=student, course_id=self.course.id)
        actual_student = User.objects.get(email=student.email)
        actual_cea = CourseEnrollmentAllowed.objects.get(email=student.email)

        assert actual_course_enrollment.mode == mode
        assert actual_student.is_active is True
        assert actual_cea.user == student

    def test_not_enrolled_student_is_enrolled(self):
        """
        Make sure that invited students who are not enrolled become enrolled when their account is activated.
        They should be enrolled in the course in audit mode.
        """
        # fixture
        student = self._set_up_invited_student(
            course=self.course,
            active=False,
            enrolled=False
        )

        # trigger the post_save callback
        student.is_active = True
        student.save()

        # reload values from the database + make sure they are in the expected state
        actual_course_enrollment = CourseEnrollment.objects.get(user=student, course_id=self.course.id)
        actual_student = User.objects.get(email=student.email)
        actual_cea = CourseEnrollmentAllowed.objects.get(email=student.email)

        assert actual_course_enrollment.mode == 'audit'
        assert actual_student.is_active is True
        assert actual_cea.user == student

    @ddt.data(False, True)
    def test_auto_enrollment_if_course_enrollment_closed(self, feature_enabled):
        """
        Test the following scenarios

        1. Invited students who register when enrollment is closed are not enrolled if
        DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED is True

        2. Invited students who register when enrollment is closed are enrolled if
        DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED is False
        """

        def register_and_enroll_student():
            student = self._set_up_invited_student(
                course=self.course,
                active=False,
                enrolled=False
            )
            student.is_active = True
            # trigger the post_save callback
            student.save()
            return CourseEnrollment.get_enrollment(student, self.course.id)

        # Set enrollment end date to a past date so that enrollment is ended
        enrollment_end = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=2)
        course_overview = CourseOverviewFactory.create(id=self.course.id, enrollment_end=enrollment_end)
        course_overview.save()

        if feature_enabled:
            with override_settings(
                FEATURES={**settings.FEATURES, 'DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED': True}
            ):
                assert register_and_enroll_student() is None
        else:
            assert register_and_enroll_student() is not None

    def test_verified_student_not_downgraded_when_changing_email(self):
        """
        Make sure that verified students do not get downgrade if they are active + changing their email.
        """
        # fixture
        student = self._set_up_invited_student(
            course=self.course,
            active=True,
            course_mode='verified'
        )
        old_email = student.email

        # trigger the post_save callback
        student.email = "foobar" + old_email
        student.save()

        # reload values from the database + make sure they are in the expected state
        actual_course_enrollment = CourseEnrollment.objects.get(user=student, course_id=self.course.id)
        actual_student = User.objects.get(email=student.email)

        assert actual_course_enrollment.mode == 'verified'
        assert actual_student.is_active is True

    @override_settings(MARKETING_EMAILS_OPT_IN=True)
    def test_is_marketable_set_to_false_for_user_created_via_management_command(self):
        """
        For users that are created using manage_user.py management command, set the
        is_marketable value to 'false'.
        """
        expected_traits = {
            'email': 'some.user@example.com',
            'username': 'some_user',
            'name': 'Student Person',
            'age': -1,
            'yearOfBirth': datetime.datetime.today().year,
            'education': None,
            'address': None,
            'gender': 'Male',
            'country': '',
            'is_marketable': False
        }

        user = UserFactory(
            username='some_user',
            first_name='Student',
            last_name='Person',
            email='some.user@example.com',
        )
        with mock.patch('common.djangoapps.student.models.user.segment') as mock_segment:
            user._called_by_management_command = True  # pylint: disable=protected-access
            user.save()

        attribute = UserAttribute.objects.filter(user_id=user.id, name=IS_MARKETABLE)
        assert attribute
        assert mock_segment.identify.call_count == 1
        assert mock_segment.identify.call_args[0] == (user.id, expected_traits)

    def _set_up_invited_student(self, course, active=False, enrolled=True, course_mode=''):
        """
        Helper function to create a user in the right state, invite them into the course, and update their
        course mode if needed.
        """
        email = 'robot@robot.org'
        user = UserFactory(
            username='somestudent',
            first_name='Student',
            last_name='Person',
            email=email,
            is_active=active
        )

        # invite the user to the course
        cea = CourseEnrollmentAllowed(email=email, course_id=course.id, auto_enroll=True)
        cea.save()

        if enrolled:
            CourseEnrollment.enroll(user, course.id)

            if course_mode:
                course_enrollment = CourseEnrollment.objects.get(
                    user=user, course_id=self.course.id
                )
                course_enrollment.mode = course_mode
                course_enrollment.save()

        return user


class TestProfile(SharedModuleStoreTestCase):
    """
    Tests for the user profile
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.profile = UserProfile.objects.get(user_id=self.user.id)
        self.name = self.profile.name
        self.course = CourseFactory.create()

    def test_name(self):
        """
        Test retrieval of the name
        """
        assert self.name
        name = get_name(self.user.id)
        assert name == self.name

    def test_name_missing_profile(self):
        """
        Test retrieval of the name when the user profile doesn't exist
        """
        name = get_name(None)
        assert not name
