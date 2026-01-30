"""
Test the access control framework
"""


import datetime
import itertools

from unittest.mock import Mock, patch
import pytest
import ddt
import pytz
from ccx_keys.locator import CCXLocator
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from milestones.tests.utils import MilestonesTestCaseMixin
from opaque_keys.edx.locator import CourseLocator

import lms.djangoapps.courseware.access as access
import lms.djangoapps.courseware.access_response as access_response
from lms.djangoapps.courseware.masquerade import CourseMasquerade
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase, masquerade_as_group_member
from lms.djangoapps.courseware.toggles import course_is_invitation_only
from lms.djangoapps.ccx.models import CustomCourseForEdX
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_experience import ENFORCE_MASQUERADE_START_DATES
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseCcxCoachRole, CourseStaffRole
from common.djangoapps.student.tests.factories import (
    AdminFactory,
    AnonymousUserFactory,
    CourseEnrollmentAllowedFactory,
    CourseEnrollmentFactory
)
from common.djangoapps.student.tests.factories import BetaTesterFactory
from common.djangoapps.student.tests.factories import GlobalStaffFactory
from common.djangoapps.student.tests.factories import InstructorFactory
from common.djangoapps.student.tests.factories import StaffFactory
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.milestones_helpers import fulfill_course_milestone, set_prerequisite_courses
from xmodule.course_block import (  # lint-amnesty, pylint: disable=wrong-import-order
    CATALOG_VISIBILITY_ABOUT,
    CATALOG_VISIBILITY_CATALOG_AND_ABOUT,
    CATALOG_VISIBILITY_NONE
)

from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    ModuleStoreTestCase,
    SharedModuleStoreTestCase
)
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import MINIMUM_UNUSED_PARTITION_ID, Group, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.features.enterprise_support.api import add_enterprise_customer_to_session
from enterprise.api.v1.serializers import EnterpriseCustomerSerializer
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerUserFactory,
    EnterpriseCustomerFactory
)
from crum import set_current_request

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES

# pylint: disable=protected-access


class CoachAccessTestCaseCCX(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test if user is coach on ccx.
    """
    @classmethod
    def setUpClass(cls):
        """
        Set up course for tests
        """
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()

        # Create ccx coach account
        self.coach = AdminFactory.create(password=self.TEST_PASSWORD)
        self.client.login(username=self.coach.username, password=self.TEST_PASSWORD)

        # assign role to coach
        role = CourseCcxCoachRole(self.course.id)
        role.add_users(self.coach)
        self.request_factory = RequestFactory()

    def make_ccx(self):
        """
        create ccx
        """
        ccx = CustomCourseForEdX(
            course_id=self.course.id,
            coach=self.coach,
            display_name="Test CCX"
        )
        ccx.save()

        ccx_locator = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        role = CourseCcxCoachRole(ccx_locator)
        role.add_users(self.coach)
        CourseEnrollment.enroll(self.coach, ccx_locator)
        return ccx_locator

    def test_has_ccx_coach_role(self):
        """
        Assert that user has coach access on ccx.
        """
        ccx_locator = self.make_ccx()

        # user have access as coach on ccx
        assert access.has_ccx_coach_role(self.coach, ccx_locator)

        # user dont have access as coach on ccx
        self.setup_user()
        assert not access.has_ccx_coach_role(self.user, ccx_locator)

    def test_ccx_coach_has_staff_role(self):
        """
        Assert that user has staff access on ccx.
        """
        ccx_locator = self.make_ccx()

        # coach user has access as staff on ccx
        assert access.has_access(self.coach, 'staff', ccx_locator)

        # basic user doesn't have staff access on ccx..
        self.setup_user()
        assert not access.has_access(self.user, 'staff', ccx_locator)

        # until we give her a staff role.
        CourseStaffRole(ccx_locator).add_users(self.user)
        assert access.has_access(self.user, 'staff', ccx_locator)

    def test_access_student_progress_ccx(self):
        """
        Assert that only a coach can see progress of student.
        """
        ccx_locator = self.make_ccx()
        student = UserFactory()

        # Enroll user
        CourseEnrollment.enroll(student, ccx_locator)

        # Test for access of a coach
        resp = self.client.get(reverse('student_progress', args=[str(ccx_locator), student.id]))
        assert resp.status_code == 200

        # Assert access of a student
        self.client.login(username=student.username, password=self.TEST_PASSWORD)
        resp = self.client.get(reverse('student_progress', args=[str(ccx_locator), self.coach.id]))
        assert resp.status_code == 404


@ddt.ddt
class AccessTestCase(LoginEnrollmentTestCase, ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Tests for the various access controls on the student dashboard
    """
    TOMORROW = 'tomorrow'
    YESTERDAY = 'yesterday'
    DATES = {
        TOMORROW: datetime.datetime.now(pytz.utc) + datetime.timedelta(days=1),
        YESTERDAY: datetime.datetime.now(pytz.utc) - datetime.timedelta(days=1),
        None: None,
    }

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='edX', course='toy', run='test_run')
        self.anonymous_user = AnonymousUserFactory()
        self.beta_user = BetaTesterFactory(course_key=self.course.id)
        self.student = UserFactory()
        self.global_staff = UserFactory(is_staff=True)
        self.course_staff = StaffFactory(course_key=self.course.id)
        self.course_instructor = InstructorFactory(course_key=self.course.id)
        self.staff = GlobalStaffFactory()

    def verify_access(self, mock_unit, student_should_have_access, expected_error_type=None):
        """ Verify the expected result from _has_access_to_block """
        response = access._has_access_to_block(self.anonymous_user, 'load', mock_unit, course_key=self.course.id)
        assert student_should_have_access == bool(response)

        if expected_error_type is not None:
            assert isinstance(response, expected_error_type)
            assert response.to_json()['error_code'] is not None

        assert access._has_access_to_block(self.course_staff, 'load', mock_unit, course_key=self.course.id)

    def test_has_staff_access_to_preview_mode(self):
        """
        Test that preview mode is only accessible by staff users.
        """
        course_key = self.course.id
        CourseEnrollmentFactory(user=self.student, course_id=self.course.id)

        for user in [self.global_staff, self.course_staff, self.course_instructor]:
            assert access.has_staff_access_to_preview_mode(user, course_key)

        assert not access.has_staff_access_to_preview_mode(self.student, course_key)

        # we don't want to restrict a staff user, masquerading as student,
        # to access preview mode.

        # Note that self.student now have access to preview mode,
        # `is_masquerading_as_student == True` means user is staff and is
        # masquerading as a student.
        with patch('lms.djangoapps.courseware.access.is_masquerading_as_student') as mock_masquerade:
            mock_masquerade.return_value = True
            for user in [self.global_staff, self.course_staff, self.course_instructor, self.student]:
                assert access.has_staff_access_to_preview_mode(user, course_key)

    def test_administrative_accesses_to_course_for_user(self):
        """
        Test types of admin accesses to a course
        """
        course_key = self.course.id

        # `administrative_accesses_to_course_for_user` returns accesses in tuple as
        # (`global_staff`, `course_staff`, `course_instructor`).
        # Order matters here, for example `True` at first index in tuple essentially means
        # given user is a global staff.
        for count, user in enumerate([self.global_staff, self.course_staff, self.course_instructor]):
            assert access.administrative_accesses_to_course_for_user(user, course_key)[count]

        assert not any(access.administrative_accesses_to_course_for_user(self.student, course_key))

    def test_student_has_access(self):
        """
        Tests course student have right access to content w/o preview.
        """
        course_key = self.course.id
        chapter = BlockFactory.create(category="chapter", parent_location=self.course.location)
        overview = CourseOverview.get_from_id(course_key)
        subsection = BlockFactory.create(category="sequential", parent_location=chapter.location)
        unit = BlockFactory.create(category="vertical", parent_location=subsection.location)

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            html_block = BlockFactory.create(
                category="html",
                parent_location=unit.location,
                display_name="Unpublished Block",
                data='<p>This block should not be published.</p>',
                publish_item=False,
            )

        # Enroll student to the course
        CourseEnrollmentFactory(user=self.student, course_id=self.course.id)

        modules = [
            self.course,
            chapter,
            overview,
            subsection,
            unit,

        ]
        # Student should have access to modules they're enrolled in
        for obj in modules:
            assert bool(access.has_access(
                self.student,
                'load',
                self.store.get_item(obj.location),
                course_key=self.course.id)
            )

        # If the document is not published yet, it should return an error when we try to fetch
        # it from the store.  This check confirms that the student would not be able to access it.
        with pytest.raises(ItemNotFoundError):
            self.store.get_item(html_block.location)

    def test_has_access_based_on_roles(self):
        """
        Tests user access to content based on their role.
        """
        assert bool(access.has_access(self.global_staff, 'staff', self.course, course_key=self.course.id))
        assert bool(access.has_access(self.course_staff, 'staff', self.course, course_key=self.course.id))
        assert bool(access.has_access(self.course_instructor, 'staff', self.course, course_key=self.course.id))
        assert not bool(access.has_access(self.student, 'staff', self.course, course_key=self.course.id))

        # Student should be able to load the course even if they don't have staff access.
        assert bool(access.has_access(self.student, 'load', self.course, course_key=self.course.id))

        # When masquerading is true, user should not be able to access staff content
        with patch('lms.djangoapps.courseware.access.is_masquerading_as_student') as mock_masquerade:
            mock_masquerade.return_value = True
            assert not bool(access.has_access(self.global_staff, 'staff', self.course, course_key=self.course.id))
            assert not bool(access.has_access(self.student, 'staff', self.course, course_key=self.course.id))

    def test_has_access_with_content_groups(self):
        """
        Test that a user masquerading as a member of a group sees appropriate content.
        """
        # Note about UserPartition and UserPartition Group IDs: these must not conflict with IDs used
        # by dynamic user partitions.
        partition_id = MINIMUM_UNUSED_PARTITION_ID
        group_0_id = MINIMUM_UNUSED_PARTITION_ID + 1
        group_1_id = MINIMUM_UNUSED_PARTITION_ID + 2
        user_partition = UserPartition(
            partition_id, 'Test User Partition', '',
            [Group(group_0_id, 'Group 1'), Group(group_1_id, 'Group 2')],
            scheme_id='cohort'
        )
        self.course.user_partitions.append(user_partition)
        self.course.cohort_config = {'cohorted': True}

        chapter = BlockFactory.create(category="chapter", parent_location=self.course.location)
        chapter.group_access = {partition_id: [group_0_id]}

        modulestore().update_item(self.course, ModuleStoreEnum.UserID.test)

        # User should not be able to preview when masquerading as student (and not in the group above).
        with patch('lms.djangoapps.courseware.access.get_user_role') as mock_user_role:
            mock_user_role.return_value = 'student'
            assert not bool(access.has_access(self.global_staff, 'load', chapter, course_key=self.course.id))

        # Should be able to preview when in staff or instructor role.
        for mocked_role in ['staff', 'instructor']:
            with patch('lms.djangoapps.courseware.access.get_user_role') as mock_user_role:
                mock_user_role.return_value = mocked_role
                assert bool(access.has_access(self.global_staff, 'load', chapter, course_key=self.course.id))

        # Now install masquerade group and set staff as a member of that.
        assert 200 == masquerade_as_group_member(self.global_staff, self.course, partition_id, group_0_id)
        # Can load the chapter since user is in the group.
        assert bool(access.has_access(self.global_staff, 'load', chapter, course_key=self.course.id))

        # Move the user to be a part of the second group.
        assert 200 == masquerade_as_group_member(self.global_staff, self.course, partition_id, group_1_id)
        # Cannot load the chapter since user is in a different group.
        assert not bool(access.has_access(self.global_staff, 'load', chapter, course_key=self.course.id))

    def test_has_access_to_course(self):
        assert not access._has_access_to_course(None, 'staff', self.course.id)

        assert not access._has_access_to_course(self.anonymous_user, 'staff', self.course.id)
        assert not access._has_access_to_course(self.anonymous_user, 'instructor', self.course.id)

        assert access._has_access_to_course(self.global_staff, 'staff', self.course.id)
        assert access._has_access_to_course(self.global_staff, 'instructor', self.course.id)

        # A user has staff access if they are in the staff group
        assert access._has_access_to_course(self.course_staff, 'staff', self.course.id)
        assert not access._has_access_to_course(self.course_staff, 'instructor', self.course.id)

        # A user has staff and instructor access if they are in the instructor group
        assert access._has_access_to_course(self.course_instructor, 'staff', self.course.id)
        assert access._has_access_to_course(self.course_instructor, 'instructor', self.course.id)

        # A user does not have staff or instructor access if they are
        # not in either the staff or the the instructor group
        assert not access._has_access_to_course(self.student, 'staff', self.course.id)
        assert not access._has_access_to_course(self.student, 'instructor', self.course.id)

        assert not access._has_access_to_course(self.student, 'not_staff_or_instructor', self.course.id)

    def test__has_access_string(self):
        user = Mock(is_staff=True)
        assert not access._has_access_string(user, 'staff', 'not_global')

        user._has_global_staff_access.return_value = True
        assert access._has_access_string(user, 'staff', 'global')

        self.assertRaises(ValueError, access._has_access_string, user, 'not_staff', 'global')

    @ddt.data(
        ('load', False, True, True),
        ('staff', False, True, True),
        ('instructor', False, False, True)
    )
    @ddt.unpack
    def test__has_access_error_block(self, action, expected_student, expected_staff, expected_instructor):
        block = Mock()

        for (user, expected_response) in (
                (self.student, expected_student),
                (self.course_staff, expected_staff),
                (self.course_instructor, expected_instructor)
        ):
            assert bool(access._has_access_error_block(user, action, block, self.course.id)) == expected_response

        with pytest.raises(ValueError):
            access._has_access_error_block(self.course_instructor, 'not_load_or_staff', block, self.course.id)

    def test__has_access_to_block(self):
        # TODO: override DISABLE_START_DATES and test the start date branch of the method
        user = Mock()
        block = Mock(user_partitions=[])
        block._class_tags = {}
        block.merged_group_access = {}

        # Always returns true because DISABLE_START_DATES is set in test.py
        assert access._has_access_to_block(user, 'load', block)
        assert access._has_access_to_block(user, 'instructor', block)
        with pytest.raises(ValueError):
            access._has_access_to_block(user, 'not_load_or_staff', block)

    @ddt.data(
        (True, None, access_response.VisibilityError),
        (False, None),
        (True, YESTERDAY, access_response.VisibilityError),
        (False, YESTERDAY),
        (True, TOMORROW, access_response.VisibilityError),
        (False, TOMORROW, access_response.StartDateError)
    )
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test__has_access_to_block_staff_lock(self, visible_to_staff_only, start, expected_error_type=None):
        """
        Tests that "visible_to_staff_only" overrides start date.
        """
        expected_access = expected_error_type is None
        mock_unit = Mock(location=self.course.location, user_partitions=[])
        mock_unit._class_tags = {}  # Needed for detached check in _has_access_to_block
        mock_unit.visible_to_staff_only = visible_to_staff_only
        mock_unit.start = self.DATES[start]
        mock_unit.merged_group_access = {}

        self.verify_access(mock_unit, expected_access, expected_error_type)

    def test__has_access_to_block_beta_user(self):
        mock_unit = Mock(user_partitions=[])
        mock_unit._class_tags = {}
        mock_unit.days_early_for_beta = 2
        mock_unit.start = self.DATES[self.TOMORROW]
        mock_unit.visible_to_staff_only = False
        mock_unit.merged_group_access = {}

        assert bool(access._has_access_to_block(self.beta_user, 'load', mock_unit, course_key=self.course.id))

    @ddt.data(
        (TOMORROW, access_response.StartDateError),
        (None, None),
        (YESTERDAY, None)
    )  # ddt throws an error if I don't put the None argument there
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test__has_access_to_block_with_start_date(self, start, expected_error_type):
        """
        Tests that block access follows start date rules.
        Access should be denied when start date is in the future and granted when
        start date is in the past or not set.
        """
        expected_access = expected_error_type is None
        mock_unit = Mock(location=self.course.location, user_partitions=[])
        mock_unit._class_tags = {}  # Needed for detached check in _has_access_to_block
        mock_unit.visible_to_staff_only = False
        mock_unit.start = self.DATES[start]
        mock_unit.merged_group_access = {}

        self.verify_access(mock_unit, expected_access, expected_error_type)

    @ddt.data(
        # Flag inactive (default)
        (False, True, None),  # Masquerading, no start date
        (False, True, YESTERDAY),  # Masquerading, past start date
        (False, False, TOMORROW),  # Not masquerading, future start date
        (False, True, TOMORROW),  # Masquerading, future start date
        # Flag active
        (True, True, None),  # Masquerading, no start date
        (True, True, YESTERDAY),  # Masquerading, past start date
        (True, False, TOMORROW),  # Not masquerading, future start date
        (True, True, TOMORROW, False),  # Masquerading, future start date - no access
    )
    @ddt.unpack
    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_START_DATES": False})
    def test_enforce_masquerade_start_dates_flag(self, flag_active, is_masquerading, start, expected_access=True):
        """
        Test that the ENFORCE_MASQUERADE_START_DATES flag controls whether masquerading bypasses start date
        restrictions.

        When the flag is disabled (default), masquerading users bypass start dates.
        When the flag is enabled, masquerading users see the same start date restrictions as regular students.
        """
        mock_unit = Mock(
            location=self.course.location,
            user_partitions=[],
            _class_tags={},
            start=self.DATES[start],
            visible_to_staff_only=False,
            merged_group_access={},
        )

        if is_masquerading:
            self.course_staff.masquerade_settings = {self.course.id: CourseMasquerade(self.course.id, role="student")}

        with override_waffle_flag(ENFORCE_MASQUERADE_START_DATES, active=flag_active):
            response = access._has_access_to_block(self.course_staff, "load", mock_unit, course_key=self.course.id)

            if expected_access:
                assert response == access.ACCESS_GRANTED
            else:
                assert isinstance(response, access_response.StartDateError)
                assert response.to_json()["error_code"] is not None
                assert str(self.DATES[start]) in response.developer_message

    def test__has_access_course_can_enroll(self):
        yesterday = datetime.datetime.now(pytz.utc) - datetime.timedelta(days=1)
        tomorrow = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=1)

        # Non-staff can enroll if authenticated and specifically allowed for that course
        # even outside the open enrollment period
        user = UserFactory.create()
        course = CourseOverviewFactory.create(id=CourseLocator('edX', 'test', '2012_Fall'))
        course.enrollment_start = tomorrow
        course.enrollment_end = tomorrow
        course.enrollment_domain = ''
        course.save()
        CourseEnrollmentAllowedFactory(email=user.email, course_id=course.id)
        assert access._has_access_course(user, 'enroll', course)

        # Staff can always enroll even outside the open enrollment period
        user = StaffFactory.create(course_key=course.id)
        assert access._has_access_course(user, 'enroll', course)

        # Non-staff cannot enroll if it is between the start and end dates and invitation only
        # and not specifically allowed
        course.enrollment_start = yesterday
        course.enrollment_end = tomorrow
        course.invitation_only = True
        course.save()
        user = UserFactory.create()
        assert not access._has_access_course(user, 'enroll', course)

        # Non-staff can enroll if it is between the start and end dates and not invitation only
        course.invitation_only = False
        course.save()
        assert access._has_access_course(user, 'enroll', course)

        # Non-staff cannot enroll outside the open enrollment period if not specifically allowed
        course.enrollment_start = tomorrow
        course.enrollment_end = tomorrow
        course.invitation_only = False
        course.save()
        assert not access._has_access_course(user, 'enroll', course)

    @override_settings(FEATURES={**settings.FEATURES, 'DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED': True})
    def test__has_access_course_with_disable_allowed_enrollment_flag(self):
        yesterday = datetime.datetime.now(pytz.utc) - datetime.timedelta(days=1)
        tomorrow = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=1)

        # Non-staff user invited to course, cannot enroll outside the open enrollment period
        # if DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED is True
        user = UserFactory.create()
        course = CourseOverviewFactory.create(id=CourseLocator('edX', 'test', '2012_Fall'))
        course.enrollment_start = tomorrow
        course.enrollment_end = tomorrow
        course.enrollment_domain = ''
        course.save()
        CourseEnrollmentAllowedFactory(email=user.email, course_id=course.id)
        assert not access._has_access_course(user, 'enroll', course)

        # Staff can always enroll even outside the open enrollment period
        user = StaffFactory.create(course_key=course.id)
        assert access._has_access_course(user, 'enroll', course)

        # Non-staff cannot enroll outside the open enrollment period if not specifically allowed
        user = UserFactory.create()
        assert not access._has_access_course(user, 'enroll', course)

        # Non-staff cannot enroll if it is between the start and end dates and invitation only
        # and not specifically allowed
        course.enrollment_start = yesterday
        course.enrollment_end = tomorrow
        course.invitation_only = True
        course.save()
        assert not access._has_access_course(user, 'enroll', course)

        # Non-staff can enroll if it is between the start and end dates and not invitation only
        course.invitation_only = False
        course.save()
        assert access._has_access_course(user, 'enroll', course)

    @override_settings(COURSES_INVITE_ONLY=False)
    def test__course_default_invite_only_flag_false(self):
        """
        Ensure that COURSES_INVITE_ONLY does not take precedence,
        if it is not set over the course invitation_only settings.
        """

        user = UserFactory.create()

        # User cannot enroll in the course if it is just invitation only.
        course = self._mock_course_with_invitation(invitation=True)
        self.assertFalse(access._has_access_course(user, 'enroll', course))

        # User can enroll in the course if it is not just invitation only.
        course = self._mock_course_with_invitation(invitation=False)
        self.assertTrue(access._has_access_course(user, 'enroll', course))

    @override_settings(COURSES_INVITE_ONLY=True)
    def test__course_default_invite_only_flag_true(self):
        """
        Ensure that COURSES_INVITE_ONLY takes precedence over the course invitation_only settings.
        """

        user = UserFactory.create()

        # User cannot enroll in the course if it is just invitation only and COURSES_INVITE_ONLY is also set.
        course = self._mock_course_with_invitation(invitation=True)
        self.assertFalse(access._has_access_course(user, 'enroll', course))

        # User cannot enroll in the course if COURSES_INVITE_ONLY is set despite of the course invitation_only value.
        course = self._mock_course_with_invitation(invitation=False)
        self.assertFalse(access._has_access_course(user, 'enroll', course))

    @ddt.data(True, False)
    def test_old_mongo_is_invite_only(self, old_mongo):
        """
        Ensure that Old Mongo courses are marked as invite only and don't allow enrollment
        """
        user = UserFactory.create()
        course = self._mock_course_with_invitation(invitation=False, deprecated=old_mongo)
        self.assertEqual(course_is_invitation_only(course), old_mongo)
        self.assertEqual(access._has_access_course(user, 'enroll', course).has_access, not old_mongo)

    def _mock_course_with_invitation(self, invitation, deprecated=False):
        yesterday = datetime.datetime.now(pytz.utc) - datetime.timedelta(days=1)
        tomorrow = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=1)
        return Mock(
            enrollment_start=yesterday, enrollment_end=tomorrow,
            id=CourseLocator('edX', 'test', '2012_Fall', deprecated=deprecated), enrollment_domain='',
            invitation_only=invitation
        )

    def test__user_passed_as_none(self):
        """Ensure has_access handles a user being passed as null"""
        access.has_access(None, 'staff', 'global', None)

    def test__catalog_visibility(self):
        """
        Tests the catalog visibility tri-states
        """
        user = UserFactory.create()
        course_id = CourseLocator('edX', 'test', '2012_Fall')
        staff = StaffFactory.create(course_key=course_id)

        course = Mock(
            id=course_id,
            catalog_visibility=CATALOG_VISIBILITY_CATALOG_AND_ABOUT
        )
        assert access._has_access_course(user, 'see_in_catalog', course)
        assert access._has_access_course(user, 'see_about_page', course)
        assert access._has_access_course(staff, 'see_in_catalog', course)
        assert access._has_access_course(staff, 'see_about_page', course)

        # Now set visibility to just about page
        course = Mock(
            id=CourseLocator('edX', 'test', '2012_Fall'),
            catalog_visibility=CATALOG_VISIBILITY_ABOUT
        )
        assert not access._has_access_course(user, 'see_in_catalog', course)
        assert access._has_access_course(user, 'see_about_page', course)
        assert access._has_access_course(staff, 'see_in_catalog', course)
        assert access._has_access_course(staff, 'see_about_page', course)

        # Now set visibility to none, which means neither in catalog nor about pages
        course = Mock(
            id=CourseLocator('edX', 'test', '2012_Fall'),
            catalog_visibility=CATALOG_VISIBILITY_NONE
        )
        assert not access._has_access_course(user, 'see_in_catalog', course)
        assert not access._has_access_course(user, 'see_about_page', course)
        assert access._has_access_course(staff, 'see_in_catalog', course)
        assert access._has_access_course(staff, 'see_about_page', course)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_PREREQUISITE_COURSES': True})
    @override_settings(MILESTONES_APP=True)
    def test_access_on_course_with_pre_requisites(self):
        """
        Test course access when a course has pre-requisite course yet to be completed
        """
        user = UserFactory.create()

        pre_requisite_course = CourseFactory.create(
            org='test_org', number='788', run='test_run'
        )

        pre_requisite_courses = [str(pre_requisite_course.id)]
        course = CourseFactory.create(
            org='test_org', number='786', run='test_run', pre_requisite_courses=pre_requisite_courses
        )
        set_prerequisite_courses(course.id, pre_requisite_courses)

        # user should not be able to load course even if enrolled
        CourseEnrollmentFactory(user=user, course_id=course.id)
        response = access._has_access_course(user, 'load', course)
        assert not response
        assert isinstance(response, access_response.MilestoneAccessError)
        # Staff can always access course
        staff = StaffFactory.create(course_key=course.id)
        assert access._has_access_course(staff, 'load', course)

        # User should be able access after completing required course
        fulfill_course_milestone(pre_requisite_course.id, user)
        assert access._has_access_course(user, 'load', course)

    @ddt.data(
        (True, True, True),
        (False, False, True)
    )
    @ddt.unpack
    def test__access_on_mobile(self, mobile_available, student_expected, staff_expected):
        """
        Test course access on mobile for staff and students.
        """
        course = CourseFactory()
        course.visible_to_staff_only = False
        course.mobile_available = mobile_available

        assert bool(access._has_access_course(self.student, 'load_mobile', course)) == student_expected
        assert bool(access._has_access_course(self.staff, 'load_mobile', course)) == staff_expected


class UserRoleTestCase(TestCase):
    """
    Tests for user roles.
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator('edX', 'toy', '2012_Fall')
        self.anonymous_user = AnonymousUserFactory()
        self.student = UserFactory()
        self.global_staff = UserFactory(is_staff=True)
        self.course_staff = StaffFactory(course_key=self.course_key)
        self.course_instructor = InstructorFactory(course_key=self.course_key)

    def _install_masquerade(self, user, role='student'):
        """
        Installs a masquerade for the specified user.
        """
        user.masquerade_settings = {
            self.course_key: CourseMasquerade(self.course_key, role=role)
        }

    def test_user_role_staff(self):
        """Ensure that user role is student for staff masqueraded as student."""
        assert 'staff' == access.get_user_role(self.course_staff, self.course_key)
        # Masquerade staff
        self._install_masquerade(self.course_staff)
        assert 'student' == access.get_user_role(self.course_staff, self.course_key)

    def test_user_role_instructor(self):
        """Ensure that user role is student for instructor masqueraded as student."""
        assert 'instructor' == access.get_user_role(self.course_instructor, self.course_key)
        # Masquerade instructor
        self._install_masquerade(self.course_instructor)
        assert 'student' == access.get_user_role(self.course_instructor, self.course_key)

    def test_user_role_anonymous(self):
        """Ensure that user role is student for anonymous user."""
        assert 'student' == access.get_user_role(self.anonymous_user, self.course_key)


@ddt.ddt
class CourseOverviewAccessTestCase(ModuleStoreTestCase):
    """
    Tests confirming that has_access works equally on CourseBlocks and
    CourseOverviews.
    """

    def setUp(self):
        super().setUp()

        today = datetime.datetime.now(pytz.UTC)
        last_week = today - datetime.timedelta(days=7)
        next_week = today + datetime.timedelta(days=7)

        self.course_default = CourseFactory.create()
        self.course_started = CourseFactory.create(start=last_week)
        self.course_not_started = CourseFactory.create(start=next_week, days_early_for_beta=10)
        self.course_staff_only = CourseFactory.create(visible_to_staff_only=True)
        self.course_mobile_available = CourseFactory.create(mobile_available=True)
        self.course_with_pre_requisite = CourseFactory.create(
            pre_requisite_courses=[str(self.course_started.id)]
        )
        self.course_with_pre_requisites = CourseFactory.create(
            pre_requisite_courses=[str(self.course_started.id), str(self.course_not_started.id)]
        )

        self.user_normal = UserFactory.create()
        self.user_beta_tester = BetaTesterFactory.create(course_key=self.course_not_started.id)
        self.user_completed_pre_requisite = UserFactory.create()
        fulfill_course_milestone(self.course_started.id, self.user_completed_pre_requisite)
        self.user_staff = UserFactory.create(is_staff=True)
        self.user_anonymous = AnonymousUserFactory.create()

    COURSE_TEST_DATA = list(itertools.product(
        ['user_normal', 'user_staff', 'user_anonymous'],
        ['enroll', 'load', 'staff', 'instructor', 'see_exists', 'see_in_catalog', 'see_about_page'],
        ['course_default', 'course_started', 'course_not_started', 'course_staff_only'],
    ))

    LOAD_MOBILE_TEST_DATA = list(itertools.product(
        ['user_normal', 'user_staff'],
        ['load_mobile'],
        ['course_default', 'course_mobile_available'],
    ))

    PREREQUISITES_TEST_DATA = list(itertools.product(
        ['user_normal', 'user_completed_pre_requisite', 'user_staff', 'user_anonymous'],
        ['load'],
        ['course_default', 'course_with_pre_requisite', 'course_with_pre_requisites'],
    ))

    @ddt.data(*(COURSE_TEST_DATA + LOAD_MOBILE_TEST_DATA + PREREQUISITES_TEST_DATA))
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_course_overview_access(self, user_attr_name, action, course_attr_name):
        """
        Check that a user's access to a course is equal to the user's access to
        the corresponding course overview.

        Instead of taking a user and course directly as arguments, we have to
        take their attribute names, as ddt doesn't allow us to reference self.

        Arguments:
            user_attr_name (str): the name of the attribute on self that is the
                User to test with.
            action (str): action to test with.
            course_attr_name (str): the name of the attribute on self that is
                the CourseBlock to test with.
        """
        user = getattr(self, user_attr_name)
        course = getattr(self, course_attr_name)

        course_overview = CourseOverview.get_from_id(course.id)
        assert bool(access.has_access(user, action, course, course_key=course.id)) ==\
               bool(access.has_access(user, action, course_overview, course_key=course.id))

    def test_course_overview_unsupported_action(self):
        """
        Check that calling has_access with an unsupported action raises a
        ValueError.
        """
        overview = CourseOverview.get_from_id(self.course_default.id)
        with pytest.raises(ValueError):
            access.has_access(self.user, '_non_existent_action', overview)

    @ddt.data(
        *itertools.product(
            ['user_normal', 'user_staff', 'user_anonymous'],
            ['see_exists', 'see_in_catalog', 'see_about_page'],
            ['course_default', 'course_started', 'course_not_started'],
        )
    )
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_course_catalog_access_num_queries_no_enterprise(self, user_attr_name, action, course_attr_name):
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime.datetime(2018, 1, 1))

        course = getattr(self, course_attr_name)

        # get a fresh user object that won't have any cached role information
        if user_attr_name == 'user_anonymous':
            user = AnonymousUserFactory()
        else:
            user = getattr(self, user_attr_name)
            user = User.objects.get(id=user.id)

        if user_attr_name == 'user_staff' and action == 'see_exists':
            # always checks staff role, and if the course has started, check the duration configuration
            if course_attr_name == 'course_started':
                num_queries = 2
            else:
                num_queries = 1
        elif user_attr_name == 'user_normal' and action == 'see_exists':
            if course_attr_name == 'course_started':
                num_queries = 4
            else:
                # checks staff role and enrollment data
                num_queries = 2
        elif user_attr_name == 'user_anonymous' and action == 'see_exists':
            if course_attr_name == 'course_started':
                num_queries = 1
            else:
                num_queries = 0
        else:
            # if the course has started, check the duration configuration
            if action == 'see_exists' and course_attr_name == 'course_started':
                num_queries = 3
            else:
                num_queries = 0

        course_overview = CourseOverview.get_from_id(course.id)
        with self.assertNumQueries(num_queries, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
            bool(access.has_access(user, action, course_overview, course_key=course.id))

    @ddt.data(
        *itertools.product(
            ['user_normal', 'user_staff', 'user_anonymous'],
            ['course_started', 'course_not_started'],
        )
    )
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False, 'ENABLE_ENTERPRISE_INTEGRATION': True})
    def test_course_catalog_access_num_queries_enterprise(self, user_attr_name, course_attr_name):
        """
        Similar to test_course_catalog_access_num_queries_no_enterprise, except enable enterprise features and make the
        basic enrollment look like an enterprise-subsidized enrollment, setting up one of each:

        * EnterpriseCustomer
        * EnterpriseCustomerUser
        * EnterpriseCourseEnrollment
        * A mock request session to pre-cache the enterprise customer data.
        """
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime.datetime(2018, 1, 1))

        course = getattr(self, course_attr_name)

        request = RequestFactory().get('/')
        request.session = {}

        # get a fresh user object that won't have any cached role information
        if user_attr_name == 'user_anonymous':
            user = AnonymousUserFactory()
            request.user = user
        else:
            user = getattr(self, user_attr_name)
            user = User.objects.get(id=user.id)
            request.user = user
            course_enrollment = CourseEnrollmentFactory(user=user, course_id=course.id)
            enterprise_customer = EnterpriseCustomerFactory(enable_learner_portal=True)
            add_enterprise_customer_to_session(request, EnterpriseCustomerSerializer(enterprise_customer).data)
            enterprise_customer_user = EnterpriseCustomerUserFactory(
                user_id=user.id,
                enterprise_customer=enterprise_customer,
            )
            EnterpriseCourseEnrollmentFactory(enterprise_customer_user=enterprise_customer_user, course_id=course.id)
        set_current_request(request)

        if user_attr_name == 'user_staff':
            if course_attr_name == 'course_started':
                # read: CourseAccessRole + django_comment_client.Role
                num_queries = 2
            else:
                # read: CourseAccessRole + EnterpriseCourseEnrollment
                num_queries = 2
        elif user_attr_name == 'user_normal':
            if course_attr_name == 'course_started':
                # read: CourseAccessRole + django_comment_client.Role + FBEEnrollmentExclusion + CourseMode
                num_queries = 4
            else:
                # read: CourseAccessRole + CourseEnrollmentAllowed + EnterpriseCourseEnrollment
                num_queries = 3
        elif user_attr_name == 'user_anonymous':
            if course_attr_name == 'course_started':
                # read: CourseMode
                num_queries = 1
            else:
                num_queries = 0

        course_overview = CourseOverview.get_from_id(course.id)
        with self.assertNumQueries(num_queries, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
            bool(access.has_access(user, 'see_exists', course_overview, course_key=course.id))
