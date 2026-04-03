"""
Tests for account linking Python API.
"""

from unittest.mock import patch
from uuid import uuid4

import ddt
from django.test import TestCase
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey
from testfixtures import LogCapture

from common.djangoapps.student.api import get_course_access_role
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import CourseAccessRoleFactory, UserFactory
from lms.djangoapps.program_enrollments.tests.factories import (
    CourseAccessRoleAssignmentFactory,
    ProgramCourseEnrollmentFactory,
    ProgramEnrollmentFactory,
)
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

from ..linking import (
    NO_LMS_USER_TEMPLATE,
    NO_PROGRAM_ENROLLMENT_TEMPLATE,
    _user_already_linked_message,
    link_program_enrollments,
)

LOG_PATH = 'lms.djangoapps.program_enrollments.api.linking'


class TestLinkProgramEnrollmentsMixin:
    """ Utility methods and test data for testing linking """

    @classmethod
    def setUpTestData(cls):  # pylint: disable=missing-function-docstring
        cls.program = uuid4()
        cls.curriculum = uuid4()
        cls.other_program = uuid4()
        cls.fruit_course = CourseKey.from_string('course-v1:edX+Oranges+Apples')
        cls.animal_course = CourseKey.from_string('course-v1:edX+Cats+Dogs')
        CourseOverviewFactory.create(id=cls.fruit_course)
        CourseOverviewFactory.create(id=cls.animal_course)

    def setUp(self):
        self.user_1 = UserFactory.create()
        self.user_2 = UserFactory.create()

    def tearDown(self):
        RequestCache.clear_all_namespaces()

    def _create_waiting_enrollment(self, program_uuid, external_user_key):
        """
        Create a waiting program enrollment for the given program and external user key.
        """
        return ProgramEnrollmentFactory.create(
            user=None,
            program_uuid=program_uuid,
            curriculum_uuid=self.curriculum,
            external_user_key=external_user_key,
        )

    def _create_waiting_course_enrollment(self, program_enrollment, course_key, status='active'):
        """
        Create a waiting program course enrollment for the given program enrollment,
        course key, and optionally status.
        """
        return ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment,
            course_key=course_key,
            course_enrollment=None,
            status=status,
        )

    def _assert_no_user(self, program_enrollment, refresh=True):
        """
        Assert that the given program enrollment has no LMS user associated with it
        """
        if refresh:
            program_enrollment.refresh_from_db()
        assert program_enrollment.user is None

    def _assert_no_program_enrollment(self, user, program_uuid, refresh=True):
        """
        Assert that the given user is not enrolled in the given program
        """
        if refresh:
            user.refresh_from_db()
        assert not user.programenrollment_set.filter(program_uuid=program_uuid).exists()

    def _assert_program_enrollment(self, user, program_uuid, external_user_key, refresh=True):
        """
        Assert that the given user is enrolled in the given program with the
        given external user key.
        """
        if refresh:
            user.refresh_from_db()
        enrollment = user.programenrollment_set.get(
            program_uuid=program_uuid, external_user_key__iexact=external_user_key
        )
        assert enrollment is not None

    def _assert_user_enrolled_in_program_courses(self, user, program_uuid, *course_keys):
        """
        Assert that the given user has active enrollments in the given courses
        through the given program.
        """
        user.refresh_from_db()
        program_enrollment = user.programenrollment_set.get(
            user=user, program_uuid=program_uuid
        )
        all_course_enrollments = program_enrollment.program_course_enrollments
        program_course_enrollments = all_course_enrollments.select_related(
            'course_enrollment__course'
        ).filter(
            course_enrollment__isnull=False
        )
        course_enrollments = [
            program_course_enrollment.course_enrollment
            for program_course_enrollment in program_course_enrollments
        ]
        assert all(course_enrollment.is_active for course_enrollment in course_enrollments)
        self.assertCountEqual(
            course_keys,
            [course_enrollment.course.id for course_enrollment in course_enrollments]
        )


@ddt.ddt
class TestLinkProgramEnrollments(TestLinkProgramEnrollmentsMixin, TestCase):
    """ Tests for linking behavior """

    def test_link_only_specified_program(self):
        """
        Test that when there are two waiting program enrollments with the same external user key,
        only the specified program's program enrollment will be linked
        """
        program_enrollment = self._create_waiting_enrollment(self.program, '0001')
        self._create_waiting_course_enrollment(program_enrollment, self.fruit_course)
        self._create_waiting_course_enrollment(program_enrollment, self.animal_course)

        another_program_enrollment = self._create_waiting_enrollment(self.other_program, '0001')
        self._create_waiting_course_enrollment(another_program_enrollment, self.fruit_course)
        self._create_waiting_course_enrollment(another_program_enrollment, self.animal_course)

        link_program_enrollments(self.program, {'0001': self.user_1.username})

        self._assert_program_enrollment(self.user_1, self.program, '0001')
        self._assert_user_enrolled_in_program_courses(
            self.user_1, self.program, self.fruit_course, self.animal_course
        )

        self._assert_no_user(another_program_enrollment)

    def test_link_mixed_case_external_user_key(self):
        """
        Test that when linking the program enrollment with same external user key,
        but the casing on external_user_key is mixed, the linking is still successful
        """
        program_enrollment = self._create_waiting_enrollment(self.program, 'student-43')
        self._create_waiting_course_enrollment(program_enrollment, self.fruit_course)
        self._create_waiting_course_enrollment(program_enrollment, self.animal_course)

        link_program_enrollments(self.program, {'STUDEnt-43': self.user_1.username})

        self._assert_program_enrollment(self.user_1, self.program, 'STUDEnt-43')
        self._assert_user_enrolled_in_program_courses(
            self.user_1, self.program, self.fruit_course, self.animal_course
        )

    def test_inactive_waiting_course_enrollment(self):
        """
        Test that when a waiting program enrollment has waiting program course enrollments with a
        status of 'inactive' the course enrollment created after calling link_program_enrollments
        will be inactive.
        """
        program_enrollment = self._create_waiting_enrollment(self.program, '0001')
        active_enrollment = self._create_waiting_course_enrollment(
            program_enrollment,
            self.fruit_course
        )
        inactive_enrollment = self._create_waiting_course_enrollment(
            program_enrollment,
            self.animal_course,
            status='inactive'
        )

        link_program_enrollments(self.program, {'0001': self.user_1.username})

        self._assert_program_enrollment(self.user_1, self.program, '0001')

        active_enrollment.refresh_from_db()
        assert active_enrollment.course_enrollment is not None
        assert active_enrollment.course_enrollment.course.id == self.fruit_course
        assert active_enrollment.course_enrollment.is_active

        inactive_enrollment.refresh_from_db()
        assert inactive_enrollment.course_enrollment is not None
        assert inactive_enrollment.course_enrollment.course.id == self.animal_course
        assert not inactive_enrollment.course_enrollment.is_active

    def test_realize_course_access_roles(self):
        program_enrollment = self._create_waiting_enrollment(self.program, '0001')
        active_enrollment_1 = self._create_waiting_course_enrollment(
            program_enrollment,
            self.fruit_course,
            status='active'
        )
        active_enrollment_2 = self._create_waiting_course_enrollment(
            program_enrollment,
            self.animal_course,
            status='active'
        )
        CourseAccessRoleAssignmentFactory(enrollment=active_enrollment_1)
        CourseAccessRoleAssignmentFactory(enrollment=active_enrollment_2)
        link_program_enrollments(self.program, {'0001': self.user_1.username})

        # assert that staff CourseAccessRoles are created for the user in the courses
        fruit_course_staff_role = get_course_access_role(
            self.user_1,
            self.fruit_course.org,
            self.fruit_course,
            CourseStaffRole.ROLE
        )
        assert fruit_course_staff_role is not None

        animal_course_staff_role = get_course_access_role(
            self.user_1,
            self.animal_course.org,
            self.animal_course,
            CourseStaffRole.ROLE
        )
        assert animal_course_staff_role is not None

        # assert that all CourseAccessRoleAssignment objects are deleted
        assert not active_enrollment_1.courseaccessroleassignment_set.all().exists()
        assert not active_enrollment_2.courseaccessroleassignment_set.all().exists()

    def test_realize_course_access_roles_user_with_existing_course_access_role(self):
        """
        This test asserts that, given a user that already has a staff CourseAccessRole in a course,
        if that user has a CourseAccessRoleAssignment that describes a staff role in that same course,
        that we do not mistakenly violate the unique_together constraint on the CourseAccessRole model by
        creating a duplicate. As of now, this is handled by the CourseStaffRole code itself, which silently
        ignores such duplicates, but this test is to ensure we do not regress.
        """
        program_enrollment = self._create_waiting_enrollment(self.program, '0001')
        active_enrollment_1 = self._create_waiting_course_enrollment(
            program_enrollment,
            self.fruit_course,
            status='active'
        )
        # create an CourseAccessRole for the user
        CourseAccessRoleFactory(user=self.user_1, course_id=self.fruit_course, role=CourseStaffRole.ROLE)

        # create a corresponding CourseAccessRoleAssignmentFactory that would, theoretically, cause a
        # duplicate object to be created, violating the CourseAccessRole integrity constraints
        CourseAccessRoleAssignmentFactory(enrollment=active_enrollment_1)
        link_program_enrollments(self.program, {'0001': self.user_1.username})

        # assert that staff CourseAccessRoles remains
        fruit_course_staff_role = get_course_access_role(
            self.user_1,
            self.fruit_course.org,
            self.fruit_course,
            CourseStaffRole.ROLE
        )
        assert fruit_course_staff_role is not None

        # assert that all CourseAccessRoleAssignment objects are deleted
        assert not active_enrollment_1.courseaccessroleassignment_set.all().exists()

    @staticmethod
    def _assert_course_enrollments_in_mode(course_enrollments, course_keys_to_mode):
        """
        Assert that all program course enrollments are in the correct modes as
        described by course_keys_to_mode.

        Arguments:
            user: the user whose course enrollments we are checking
            program_uuid: the UUID of the program in which the user is enrolled
            course_keys_to_mode: a mapping from course keys to the the mode
                slug the user's enrollment should be in
        """
        assert len(course_enrollments) == len(course_keys_to_mode)

        for course_enrollment in course_enrollments:
            assert course_enrollment.mode == course_keys_to_mode[course_enrollment.course.id]

    @ddt.data(
        ('001', '001'),
        ('learner-2', 'LEArneR-2'),
    )
    @ddt.unpack
    @patch('lms.djangoapps.program_enrollments.api.linking.CourseMode.modes_for_course_dict')
    def test_update_linking_enrollment_to_another_user(
        self,
        linked_external_key,
        updated_external_key,
        mock_modes_for_course_dict,
    ):
        """
        Test that when link_program_enrollments is called with a program and an external_user_key,
        user pair and that program is already linked to a different user with the same external_user_key
        that the original user's link is removed and replaced by a link with the new user.
        """
        program_enrollment = self._create_waiting_enrollment(self.program, linked_external_key)

        self._create_waiting_course_enrollment(program_enrollment, self.fruit_course)
        self._create_waiting_course_enrollment(program_enrollment, self.animal_course)

        # in order to test what happens to a learner's enrollment in a course without an audit mode
        # (e.g. Master's only course), we need to mock out the course modes that exist for our courses;
        # doing it this way helps to avoid needing to use the modulestore when using the CourseModeFactory
        def mocked_modes_for_course_dict(course_key):
            if course_key == self.animal_course:
                return {'masters': 'masters'}
            else:
                return {'audit': 'audit'}

        mock_modes_for_course_dict.side_effect = mocked_modes_for_course_dict

        # do the initial link of user_1 to the program enrollment
        link_program_enrollments(self.program, {linked_external_key: self.user_1.username})

        self._assert_program_enrollment(self.user_1, self.program, linked_external_key, refresh=False)
        self._assert_no_program_enrollment(self.user_2, self.program, refresh=False)

        # grab the user's original course enrollment before the link between the program
        # and the course enrollments is severed
        course_enrollments_for_user_1 = [pce.course_enrollment
                                         for pce
                                         in program_enrollment.program_course_enrollments.all()]

        errors = link_program_enrollments(
            self.program,
            {
                updated_external_key: self.user_2.username,
            }
        )

        assert not errors
        self._assert_program_enrollment(self.user_2, self.program, updated_external_key)
        self._assert_no_program_enrollment(self.user_1, self.program)
        # assert that all of user_1's course enrollments as part of the program
        # are inactive
        for course_enrollment in course_enrollments_for_user_1:
            course_enrollment.refresh_from_db()
            assert not course_enrollment.is_active

        # assert that user_1's course enrollments are in the expected mode
        # after unlinking
        course_keys_to_mode = {
            self.fruit_course: 'audit',
            self.animal_course: 'masters',
        }
        self._assert_course_enrollments_in_mode(course_enrollments_for_user_1, course_keys_to_mode)

        # assert that user_2 has been successfully linked to the program
        self._assert_program_enrollment(self.user_2, self.program, updated_external_key)
        self._assert_user_enrolled_in_program_courses(self.user_2, self.program, self.fruit_course, self.animal_course)


class TestLinkProgramEnrollmentsErrors(TestLinkProgramEnrollmentsMixin, ModuleStoreTestCase):
    """ Tests for linking error behavior """

    def test_program_enrollment_not_found__nonexistant(self):
        self._create_waiting_enrollment(self.program, '0001')
        self._program_enrollment_not_found()

    def test_program_enrollment_not_found__different_program(self):
        self._create_waiting_enrollment(self.program, '0001')
        self._create_waiting_enrollment(self.other_program, '0002')
        self._program_enrollment_not_found()

    def _program_enrollment_not_found(self):
        """
        Helper for test_program_not_found_* tests.
        tries to link user_1 to '0001' and user_2 to '0002' in program
        asserts that user_2 was not linked because the enrollment was not found
        """
        with LogCapture() as logger:
            errors = link_program_enrollments(
                self.program,
                {
                    '0001': self.user_1.username,
                    '0002': self.user_2.username,
                }
            )
            expected_error_msg = NO_PROGRAM_ENROLLMENT_TEMPLATE.format(
                program_uuid=self.program,
                external_user_key='0002'
            )
            logger.check_present((LOG_PATH, 'WARNING', expected_error_msg))

        self.assertDictEqual(errors, {'0002': expected_error_msg})
        self._assert_program_enrollment(self.user_1, self.program, '0001')
        self._assert_no_program_enrollment(self.user_2, self.program)

    def test_user_not_found(self):
        self._create_waiting_enrollment(self.program, '0001')
        enrollment_2 = self._create_waiting_enrollment(self.program, '0002')

        with LogCapture() as logger:
            errors = link_program_enrollments(
                self.program,
                {
                    '0001': self.user_1.username,
                    '0002': 'nonexistant-user',
                }
            )
            expected_error_msg = NO_LMS_USER_TEMPLATE.format('nonexistant-user')
            logger.check_present((LOG_PATH, 'WARNING', expected_error_msg))

        self.assertDictEqual(errors, {'0002': expected_error_msg})
        self._assert_program_enrollment(self.user_1, self.program, '0001')
        self._assert_no_user(enrollment_2)

    def test_enrollment_already_linked_to_target_user(self):
        self._create_waiting_enrollment(self.program, '0001')
        program_enrollment = ProgramEnrollmentFactory.create(
            user=self.user_2,
            program_uuid=self.program,
            external_user_key='0002',
        )
        self._assert_no_program_enrollment(self.user_1, self.program, refresh=False)
        self._assert_program_enrollment(self.user_2, self.program, '0002', refresh=False)

        with LogCapture() as logger:
            errors = link_program_enrollments(
                self.program,
                {
                    '0001': self.user_1.username,
                    '0002': self.user_2.username
                }
            )
            expected_error_msg = _user_already_linked_message(program_enrollment, self.user_2)
            logger.check_present((LOG_PATH, 'WARNING', expected_error_msg))

        self.assertDictEqual(errors, {'0002': expected_error_msg})
        self._assert_program_enrollment(self.user_1, self.program, '0001')
        self._assert_program_enrollment(self.user_2, self.program, '0002')

    def test_error_enrolling_in_course(self):
        nonexistant_course = CourseKey.from_string('course-v1:edX+Zilch+Bupkis')

        program_enrollment_1 = self._create_waiting_enrollment(self.program, '0001')
        course_enrollment_1 = self._create_waiting_course_enrollment(
            program_enrollment_1, nonexistant_course
        )
        course_enrollment_2 = self._create_waiting_course_enrollment(
            program_enrollment_1, self.animal_course
        )

        program_enrollment_2 = self._create_waiting_enrollment(self.program, '0002')
        self._create_waiting_course_enrollment(program_enrollment_2, self.fruit_course)
        self._create_waiting_course_enrollment(program_enrollment_2, self.animal_course)

        errors = link_program_enrollments(
            self.program,
            {
                '0001': self.user_1.username,
                '0002': self.user_2.username
            }
        )
        assert errors['0001'] in 'NonExistentCourseError: '
        self._assert_no_program_enrollment(self.user_1, self.program)
        self._assert_no_user(program_enrollment_1)
        course_enrollment_1.refresh_from_db()
        assert course_enrollment_1.course_enrollment is None
        course_enrollment_2.refresh_from_db()
        assert course_enrollment_2.course_enrollment is None

        self._assert_user_enrolled_in_program_courses(
            self.user_2, self.program, self.animal_course, self.fruit_course
        )

    def test_integrity_error(self):
        existing_program_enrollment = self._create_waiting_enrollment(self.program, 'learner-0')
        existing_program_enrollment.user = self.user_1
        existing_program_enrollment.save()

        program_enrollment_1 = self._create_waiting_enrollment(self.program, '0001')
        self._create_waiting_enrollment(self.program, '0002')

        errors = link_program_enrollments(
            self.program,
            {
                '0001': self.user_1.username,
                '0002': self.user_2.username,
            }
        )

        assert len(errors) == 1
        assert 'UNIQUE constraint failed' in errors['0001']
        self._assert_no_user(program_enrollment_1)
        self._assert_program_enrollment(self.user_2, self.program, '0002')
