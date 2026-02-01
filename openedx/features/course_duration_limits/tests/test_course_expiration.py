"""
Contains tests to verify correctness of course expiration functionality
"""

from datetime import timedelta
from unittest import mock
from unittest.mock import patch

import ddt
from django.conf import settings
from django.urls import reverse
from django.utils.timezone import now
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory
from xmodule.partitions.partitions import ENROLLMENT_TRACK_PARTITION_ID

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, FBEEnrollmentExclusion
from common.djangoapps.student.roles import CourseInstructorRole
from common.djangoapps.student.tests.factories import BetaTesterFactory
from common.djangoapps.student.tests.factories import TEST_PASSWORD, CourseEnrollmentFactory, UserFactory
from common.djangoapps.student.tests.factories import GlobalStaffFactory
from common.djangoapps.student.tests.factories import InstructorFactory
from common.djangoapps.student.tests.factories import OrgInstructorFactory
from common.djangoapps.student.tests.factories import OrgStaffFactory
from common.djangoapps.student.tests.factories import StaffFactory
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_date_signals.utils import MAX_DURATION, MIN_DURATION
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR
)
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID, CONTENT_TYPE_GATE_GROUP_IDS
from openedx.features.course_duration_limits.access import get_user_course_expiration_date
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience.tests.views.helpers import add_course_mode


# pylint: disable=no-member
@ddt.ddt
class CourseExpirationTestCase(ModuleStoreTestCase, MasqueradeMixin):
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
        self.chapter = BlockFactory.create(
            category='chapter',
            parent_location=self.course.location,
            display_name='Test Chapter'
        )
        self.sequential = BlockFactory.create(
            category='sequential',
            parent_location=self.chapter.location,
            display_name='Test Sequential'
        )
        BlockFactory.create(
            category='vertical',
            parent_location=self.sequential.location,
            display_name='Test Vertical'
        )
        self.user = UserFactory()
        self.THREE_YEARS_AGO = now() - timedelta(days=(365 * 3))

        # Make this a verified course so we can test expiration date
        add_course_mode(self.course, mode_slug=CourseMode.AUDIT)
        add_course_mode(self.course)

    def tearDown(self):
        self.catalog_patch.stop()
        CourseEnrollment.unenroll(self.user, self.course.id)
        super().tearDown()  # lint-amnesty, pylint: disable=super-with-arguments

    def get_courseware(self):
        """Returns a response from a GET on a courseware section"""
        courseware_url = reverse('render_xblock', args=[str(self.sequential.location)])
        return self.client.get(courseware_url, follow=True)

    def test_enrollment_mode(self):
        """Tests that verified enrollments do not have an expiration"""
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        result = get_user_course_expiration_date(self.user, CourseOverview.get_from_id(self.course.id))
        assert result is None

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @ddt.data(
        [int(MIN_DURATION.days / 7) - 1, MIN_DURATION, False],
        [7, timedelta(weeks=7), False],
        [int(MAX_DURATION.days / 7) + 1, MAX_DURATION, False],
        [None, MIN_DURATION, False],
        [int(MIN_DURATION.days / 7) - 1, MIN_DURATION, True],
        [7, timedelta(weeks=7), True],
        [int(MAX_DURATION.days / 7) + 1, MAX_DURATION, True],
        [None, MIN_DURATION, True],
    )
    @ddt.unpack
    def test_all_courses_with_weeks_to_complete(
        self,
        weeks_to_complete,
        access_duration,
        self_paced,
        mock_get_course_run_details,
    ):
        """
        Test that access_duration for a course is equal to the value of the weeks_to_complete field in discovery.
        If weeks_to_complete is None, access_duration will be the MIN_DURATION constant.

        """
        if self_paced:
            self.course.self_paced = True
        mock_get_course_run_details.return_value = {'weeks_to_complete': weeks_to_complete}
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=self.course.start,
        )
        result = get_user_course_expiration_date(
            self.user,
            CourseOverview.get_from_id(self.course.id),
        )
        assert result == (enrollment.created + access_duration)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_content_availability_date(self, mock_get_course_run_details):
        """
        Content availability date is course start date or enrollment date, whichever is later.
        """
        access_duration = timedelta(weeks=7)
        mock_get_course_run_details.return_value = {'weeks_to_complete': 7}

        # Content availability date is enrollment date
        start_date = now() - timedelta(weeks=10)
        past_course = CourseFactory(start=start_date)
        enrollment = CourseEnrollment.enroll(self.user, past_course.id, CourseMode.AUDIT)
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(past_course.id),
            enabled_as_of=past_course.start,
        )
        result = get_user_course_expiration_date(
            self.user,
            CourseOverview.get_from_id(past_course.id),
        )
        assert result is None

        add_course_mode(past_course, mode_slug=CourseMode.AUDIT)
        add_course_mode(past_course, upgrade_deadline_expired=False)
        result = get_user_course_expiration_date(
            self.user,
            CourseOverview.get_from_id(past_course.id),
        )
        content_availability_date = enrollment.created
        assert result == (content_availability_date + access_duration)

        # Content availability date is course start date
        start_date = now() + timedelta(weeks=10)
        future_course = CourseFactory(start=start_date)
        enrollment = CourseEnrollment.enroll(self.user, future_course.id, CourseMode.AUDIT)
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(future_course.id),
            enabled_as_of=past_course.start,
        )
        result = get_user_course_expiration_date(
            self.user,
            CourseOverview.get_from_id(future_course.id),
        )
        assert result is None

        add_course_mode(future_course, mode_slug=CourseMode.AUDIT)
        add_course_mode(future_course, upgrade_deadline_expired=False)
        result = get_user_course_expiration_date(
            self.user,
            CourseOverview.get_from_id(future_course.id),
        ).replace(microsecond=0)
        content_availability_date = start_date.replace(microsecond=0)
        assert result == (content_availability_date + access_duration)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_expired_upgrade_deadline(self, mock_get_course_run_details):
        """
        The expiration date still exists if the upgrade deadline has passed
        """
        access_duration = timedelta(weeks=7)
        mock_get_course_run_details.return_value = {'weeks_to_complete': 7}

        start_date = now() - timedelta(weeks=10)
        course = CourseFactory(start=start_date)
        enrollment = CourseEnrollment.enroll(self.user, course.id, CourseMode.AUDIT)
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(course.id),
            enabled_as_of=course.start,
        )
        add_course_mode(course, mode_slug=CourseMode.AUDIT)
        add_course_mode(course, upgrade_deadline_expired=True)
        result = get_user_course_expiration_date(
            self.user,
            CourseOverview.get_from_id(course.id),
        )
        content_availability_date = enrollment.created
        assert result == (content_availability_date + access_duration)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @ddt.data(
        ({'user_partition_id': CONTENT_GATING_PARTITION_ID,
          'group_id': CONTENT_TYPE_GATE_GROUP_IDS['limited_access']}, True),
        ({'user_partition_id': CONTENT_GATING_PARTITION_ID,
          'group_id': CONTENT_TYPE_GATE_GROUP_IDS['full_access']}, False),
        ({'user_partition_id': ENROLLMENT_TRACK_PARTITION_ID,
          'group_id': settings.COURSE_ENROLLMENT_MODES['audit']['id']}, True),
        ({'user_partition_id': ENROLLMENT_TRACK_PARTITION_ID,
          'group_id': settings.COURSE_ENROLLMENT_MODES['verified']['id']}, False),
        ({'role': 'staff'}, False),
        ({'role': 'student'}, True),
        ({'username': 'audit'}, True),
        ({'username': 'verified'}, False),
    )
    @ddt.unpack
    def test_masquerade(self, masquerade_config, show_expiration_banner, mock_get_course_run_details):
        mock_get_course_run_details.return_value = {'weeks_to_complete': 12}
        audit_student = UserFactory(username='audit')
        CourseEnrollmentFactory.create(
            user=audit_student,
            course_id=self.course.id,
            mode='audit'
        )
        verified_student = UserFactory(username='verified')
        CourseEnrollmentFactory.create(
            user=verified_student,
            course_id=self.course.id,
            mode='verified'
        )
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=self.course.start,
        )

        instructor = UserFactory.create(username='instructor')
        CourseEnrollmentFactory.create(
            user=instructor,
            course_id=self.course.id,
            mode='audit'
        )
        CourseInstructorRole(self.course.id).add_users(instructor)
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)

        self.update_masquerade(**masquerade_config)

        response = self.get_courseware()
        assert response.status_code == 200
        self.assertCountEqual(response.redirect_chain, [])
        banner_text = 'You lose all access to this course, including your progress,'
        if show_expiration_banner:
            self.assertContains(response, banner_text)
        else:
            self.assertNotContains(response, banner_text)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_masquerade_in_holdback(self, mock_get_course_run_details):
        mock_get_course_run_details.return_value = {'weeks_to_complete': 12}
        audit_student = UserFactory(username='audit')
        enrollment = CourseEnrollmentFactory.create(
            user=audit_student,
            course_id=self.course.id,
            mode='audit'
        )
        FBEEnrollmentExclusion.objects.create(enrollment=enrollment)
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=self.course.start,
        )

        instructor = UserFactory.create(username='instructor')
        enrollment = CourseEnrollmentFactory.create(
            user=instructor,
            course_id=self.course.id,
            mode='audit'
        )
        CourseInstructorRole(self.course.id).add_users(instructor)
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)

        self.update_masquerade(username='audit')

        response = self.get_courseware()
        assert response.status_code == 200
        self.assertCountEqual(response.redirect_chain, [])
        banner_text = 'You lose all access to this course, including your progress,'
        self.assertNotContains(response, banner_text)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    def test_masquerade_expired(self, mock_get_course_run_details):
        mock_get_course_run_details.return_value = {'weeks_to_complete': 1}

        audit_student = UserFactory(username='audit')
        enrollment = CourseEnrollmentFactory.create(
            user=audit_student,
            course_id=self.course.id,
            mode='audit',
        )
        enrollment.created = self.course.start
        enrollment.save()
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=self.course.start,
        )

        instructor = UserFactory.create(username='instructor')
        CourseEnrollmentFactory.create(
            user=instructor,
            course_id=self.course.id,
            mode='audit'
        )
        CourseInstructorRole(self.course.id).add_users(instructor)
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)

        self.update_masquerade(username='audit')

        response = self.get_courseware()
        assert response.status_code == 200
        self.assertCountEqual(response.redirect_chain, [])
        banner_text = 'This learner does not have access to this course. Their access expired on'
        self.assertContains(response, banner_text)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @ddt.data(
        InstructorFactory,
        StaffFactory,
        BetaTesterFactory,
        OrgStaffFactory,
        OrgInstructorFactory,
        GlobalStaffFactory,
    )
    def test_no_banner_when_masquerading_as_staff(self, role_factory, mock_get_course_run_details):
        """
        When masquerading as a specific expired user, if that user has a staff role
        the expired course banner will not show up.
        """
        mock_get_course_run_details.return_value = {'weeks_to_complete': 1}

        if role_factory == GlobalStaffFactory:
            expired_staff = role_factory.create(password=TEST_PASSWORD)
        else:
            expired_staff = role_factory.create(password=TEST_PASSWORD, course_key=self.course.id)

        CourseEnrollmentFactory.create(
            mode=CourseMode.AUDIT,
            course_id=self.course.id,
            user=expired_staff,
        )
        Schedule.objects.update(start_date=self.THREE_YEARS_AGO)
        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=self.course.start,
        )

        staff_user = StaffFactory.create(password=TEST_PASSWORD, course_key=self.course.id)
        CourseEnrollmentFactory.create(
            user=staff_user,
            course_id=self.course.id,
            mode='audit'
        )

        self.client.login(username=staff_user.username, password=self.TEST_PASSWORD)

        self.update_masquerade(username=expired_staff.username)

        response = self.get_courseware()
        assert response.status_code == 200
        self.assertCountEqual(response.redirect_chain, [])
        banner_text = 'This learner does not have access to this course. Their access expired on'
        self.assertNotContains(response, banner_text)

    @mock.patch("openedx.core.djangoapps.course_date_signals.utils.get_course_run_details")
    @ddt.data(
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_GROUP_MODERATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_ADMINISTRATOR,
    )
    def test_no_banner_when_masquerading_as_forum_staff(self, role_name, mock_get_course_run_details):
        """
        When masquerading as a specific expired user, if that user has a forum staff role
        the expired course banner will not show up.
        """
        mock_get_course_run_details.return_value = {'weeks_to_complete': 1}

        expired_staff = UserFactory.create()
        role = RoleFactory(name=role_name, course_id=self.course.id)
        role.users.add(expired_staff)

        CourseEnrollmentFactory.create(
            mode=CourseMode.AUDIT,
            course_id=self.course.id,
            user=expired_staff,
        )
        Schedule.objects.update(start_date=self.THREE_YEARS_AGO)

        CourseDurationLimitConfig.objects.create(
            enabled=True,
            course=CourseOverview.get_from_id(self.course.id),
            enabled_as_of=self.course.start,
        )

        staff_user = StaffFactory.create(password=TEST_PASSWORD, course_key=self.course.id)
        CourseEnrollmentFactory.create(
            user=staff_user,
            course_id=self.course.id,
            mode='audit'
        )

        self.client.login(username=staff_user.username, password=self.TEST_PASSWORD)

        self.update_masquerade(username=expired_staff.username)

        response = self.get_courseware()
        assert response.status_code == 200
        self.assertCountEqual(response.redirect_chain, [])
        banner_text = 'This learner does not have access to this course. Their access expired on'
        self.assertNotContains(response, banner_text)
