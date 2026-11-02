"""
Test the Data Aggregation Layer for Course Enrollments.

"""

import datetime
from unittest.mock import patch

import ddt
import pytest
from zoneinfo import ZoneInfo

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from openedx.core.djangoapps.enrollments import data
from openedx.core.djangoapps.enrollments.errors import (
    CourseEnrollmentClosedError,
    CourseEnrollmentExistsError,
    CourseEnrollmentFullError,
    UserNotFoundError
)
from openedx.core.djangoapps.enrollments.serializers import CourseEnrollmentSerializer
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.core.lib.exceptions import CourseNotFoundError
from common.djangoapps.student.models import AlreadyEnrolledError, CourseEnrollment, CourseFullError, EnrollmentClosedError  # lint-amnesty, pylint: disable=line-too-long
from common.djangoapps.student.tests.factories import CourseAccessRoleFactory, UserFactory, CourseEnrollmentFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
@skip_unless_lms
class EnrollmentDataTest(ModuleStoreTestCase):
    """
    Test course enrollment data aggregation.

    """
    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"

    def setUp(self):
        """Create a course and user, then log in. """
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that users are automatically enrolled as "honor".
        ([], 'honor'),

        # Audit / Verified / Honor
        # We should always go to the "choose your course" page.
        # We should also be enrolled as "honor" by default.
        (['honor', 'verified', 'audit'], 'honor'),
    )
    @ddt.unpack
    def test_enroll(self, course_modes, enrollment_mode):
        # Create the course modes (if any) required for this test case
        self._create_course_modes(course_modes)
        enrollment = data.create_course_enrollment(
            self.user.username,
            str(self.course.id),
            enrollment_mode,
            True
        )

        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active
        assert course_mode == enrollment_mode

        # Confirm the returned enrollment and the data match up.
        assert course_mode == enrollment['mode']
        assert is_active == enrollment['is_active']
        assert self.course.display_name_with_default == enrollment['course_details']['course_name']

    def test_unenroll(self):
        # Enroll the user in the course
        CourseEnrollment.enroll(self.user, self.course.id, mode="honor")

        enrollment = data.update_course_enrollment(
            self.user.username,
            str(self.course.id),
            is_active=False
        )

        # Determine that the returned enrollment is inactive.
        assert not enrollment['is_active']

        # Expect that we're no longer enrolled
        assert not CourseEnrollment.is_enrolled(self.user, self.course.id)

    @ddt.data(
        # No course modes, no course enrollments.
        ([]),

        # Audit / Verified / Honor course modes, with three course enrollments.
        (['honor', 'verified', 'audit']),
    )
    def test_get_course_info(self, course_modes):
        self._create_course_modes(course_modes, course=self.course)
        result_course = data.get_course_enrollment_info(str(self.course.id))
        result_slugs = [mode['slug'] for mode in result_course['course_modes']]
        for course_mode in course_modes:
            assert course_mode in result_slugs

    @ddt.data(
        # No course modes, no course enrollments.
        ([], []),

        # Audit / Verified / Honor course modes, with three course enrollments.
        (['honor', 'verified', 'audit'], ['1', '2', '3']),
    )
    @ddt.unpack
    def test_get_course_enrollments(self, course_modes, course_numbers):
        # Create all the courses
        created_courses = []
        for course_number in course_numbers:
            created_courses.append(CourseFactory.create(number=course_number))

        created_enrollments = []
        for course in created_courses:
            self._create_course_modes(course_modes, course=course)
            # Create the original enrollment.
            created_enrollments.append(data.create_course_enrollment(
                self.user.username,
                str(course.id),
                'honor',
                True
            ))

        # Compare the created enrollments with the results
        # from the get enrollments request.
        results = data.get_course_enrollments(self.user.username)
        assert results == created_enrollments

        # Now create a course enrollment with some invalid course (does
        # not exist in database) for the user and check that the method
        # 'get_course_enrollments' ignores course enrollments for invalid
        # or deleted courses
        non_existent_course_id = 'InvalidOrg/InvalidCourse/InvalidRun'
        enrollement = CourseEnrollmentFactory.create(
            user=self.user,
            course_id=non_existent_course_id,
            mode='honor',
            is_active=True
        )
        enrollement.course.delete()

        updated_results = data.get_course_enrollments(self.user.username)
        assert results == updated_results

    def test_get_enrollments_including_inactive(self):
        """ Verify that if 'include_inactive' is True, all enrollments
        are returned including inactive.
        """
        course_modes, course_numbers = ['honor', 'verified', 'audit'], ['1', '2', '3']
        created_courses = []
        for course_number in course_numbers:
            created_courses.append(CourseFactory.create(number=course_number))

        created_enrollments = []
        for course in created_courses:
            self._create_course_modes(course_modes, course=course)
            # Create the original enrollment.
            created_enrollments.append(data.create_course_enrollment(
                self.user.username,
                str(course.id),
                'honor',
                True
            ))

        # deactivate one enrollment
        data.update_course_enrollment(
            self.user.username,
            str(created_courses[0].id),
            'honor',
            False
        )

        # by default in-active enrollment will be excluded.
        results = data.get_course_enrollments(self.user.username)
        assert len(results) != len(created_enrollments)

        # we can get all enrollments including inactive by passing "include_inactive"
        results = data.get_course_enrollments(self.user.username, include_inactive=True)
        assert len(results) == len(created_enrollments)

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that users are automatically enrolled as "honor".
        ([], 'honor'),

        # Audit / Verified / Honor
        # We should always go to the "choose your course" page.
        # We should also be enrolled as "honor" by default.
        (['honor', 'verified', 'audit'], 'verified'),
    )
    @ddt.unpack
    def test_get_course_enrollment(self, course_modes, enrollment_mode):
        self._create_course_modes(course_modes)

        # Try to get an enrollment before it exists.
        result = data.get_course_enrollment(self.user.username, str(self.course.id))
        assert result is None

        # Create the original enrollment.
        enrollment = data.create_course_enrollment(
            self.user.username,
            str(self.course.id),
            enrollment_mode,
            True
        )
        # Get the enrollment and compare it to the original.
        result = data.get_course_enrollment(self.user.username, str(self.course.id))
        assert self.user.username == result['user']
        assert enrollment == result

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that users are automatically enrolled as "honor".
        ([], 'honor'),

        # Audit / Verified / Honor
        # We should always go to the "choose your course" page.
        # We should also be enrolled as "honor" by default.
        (['honor', 'verified', 'audit'], 'verified'),
    )
    @ddt.unpack
    def test_get_user_enrollments(self, course_modes, enrollment_mode):
        self._create_course_modes(course_modes)

        # Try to get enrollments before they exist.
        result = data.get_user_enrollments(self.course.id)
        assert not result.exists()

        # Create 10 test users to enroll in the course
        users = []
        for i in range(10):
            users.append(UserFactory.create(
                username=self.USERNAME + str(i),
                email=self.EMAIL + str(i),
                password=self.PASSWORD + str(i)
            ))

        # Create the original enrollments.
        created_enrollments = []
        for user in users:
            created_enrollments.append(data.create_course_enrollment(
                user.username,
                str(self.course.id),
                enrollment_mode,
                True
            ))

        # Compare the created enrollments with the results
        # from the get user enrollments request.
        results = data.get_user_enrollments(
            self.course.id
        )
        assert result.exists()
        assert CourseEnrollmentSerializer(results, many=True).data == created_enrollments

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that users are automatically enrolled as "honor".
        ([], 'credit'),

        # Audit / Verified / Honor
        # We should always go to the "choose your course" page.
        # We should also be enrolled as "honor" by default.
        (['honor', 'verified', 'audit', 'credit'], 'credit'),
    )
    @ddt.unpack
    def test_add_or_update_enrollment_attr(self, course_modes, enrollment_mode):
        # Create the course modes (if any) required for this test case
        self._create_course_modes(course_modes)
        data.create_course_enrollment(self.user.username, str(self.course.id), enrollment_mode, True)
        enrollment_attributes = [
            {
                "namespace": "credit",
                "name": "provider_id",
                "value": "hogwarts",
            }
        ]

        data.add_or_update_enrollment_attr(self.user.username, str(self.course.id), enrollment_attributes)
        enrollment_attr = data.get_enrollment_attributes(self.user.username, str(self.course.id))
        assert enrollment_attr[0] == enrollment_attributes[0]

        enrollment_attributes = [
            {
                "namespace": "credit",
                "name": "provider_id",
                "value": "ASU",
            }
        ]

        data.add_or_update_enrollment_attr(self.user.username, str(self.course.id), enrollment_attributes)
        enrollment_attr = data.get_enrollment_attributes(self.user.username, str(self.course.id))
        assert enrollment_attr[0] == enrollment_attributes[0]

    def test_non_existent_course(self):
        with pytest.raises(CourseNotFoundError):
            data.get_course_enrollment_info("this/is/bananas")

    def _create_course_modes(self, course_modes, course=None):
        """Create the course modes required for a test. """
        course_id = course.id if course else self.course.id
        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=course_id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )

    def test_enrollment_for_non_existent_user(self):
        with pytest.raises(UserNotFoundError):
            data.create_course_enrollment("some_fake_user", str(self.course.id), 'honor', True)

    def test_enrollment_for_non_existent_course(self):
        with pytest.raises(CourseNotFoundError):
            data.create_course_enrollment(self.user.username, "some/fake/course", 'honor', True)

    @patch.object(CourseEnrollment, "enroll")
    def test_enrollment_for_closed_course(self, mock_enroll):
        mock_enroll.side_effect = EnrollmentClosedError("Bad things happened")
        with pytest.raises(CourseEnrollmentClosedError):
            data.create_course_enrollment(self.user.username, str(self.course.id), 'honor', True)

    @patch.object(CourseEnrollment, "enroll")
    def test_enrollment_for_full_course(self, mock_enroll):
        mock_enroll.side_effect = CourseFullError("Bad things happened")
        with pytest.raises(CourseEnrollmentFullError):
            data.create_course_enrollment(self.user.username, str(self.course.id), 'honor', True)

    @patch.object(CourseEnrollment, "enroll")
    def test_enrollment_for_enrolled_course(self, mock_enroll):
        mock_enroll.side_effect = AlreadyEnrolledError("Bad things happened")
        with pytest.raises(CourseEnrollmentExistsError):
            data.create_course_enrollment(self.user.username, str(self.course.id), 'honor', True)

    def test_update_for_non_existent_user(self):
        with pytest.raises(UserNotFoundError):
            data.update_course_enrollment("some_fake_user", str(self.course.id), is_active=False)

    def test_update_for_non_existent_course(self):
        enrollment = data.update_course_enrollment(self.user.username, "some/fake/course", is_active=False)
        assert enrollment is None

    def test_get_course_with_expired_mode_included(self):
        """Verify that method returns expired modes if include_expired
        is true."""
        modes = ['honor', 'verified', 'audit']
        self._create_course_modes(modes, course=self.course)
        self._update_verified_mode_as_expired(self.course.id)
        self.assert_enrollment_modes(modes, True)

    def test_get_course_without_expired_mode_included(self):
        """Verify that method does not returns expired modes if include_expired
        is false."""
        self._create_course_modes(['honor', 'verified', 'audit'], course=self.course)
        self._update_verified_mode_as_expired(self.course.id)
        self.assert_enrollment_modes(['audit', 'honor'], False)

    def _update_verified_mode_as_expired(self, course_id):
        """Dry method to change verified mode expiration."""
        mode = CourseMode.objects.get(course_id=course_id, mode_slug=CourseMode.VERIFIED)
        mode.expiration_datetime = datetime.datetime(year=1970, month=1, day=1, tzinfo=ZoneInfo("UTC"))
        mode.save()

    def assert_enrollment_modes(self, expected_modes, include_expired):
        """Get enrollment data and assert response with expected modes."""
        result_course = data.get_course_enrollment_info(str(self.course.id), include_expired=include_expired)
        result_slugs = [mode['slug'] for mode in result_course['course_modes']]
        for course_mode in expected_modes:
            assert course_mode in result_slugs

        if not include_expired:
            assert 'verified' not in result_slugs

    def test_get_roles(self):
        """Create a role for a user, then get it"""
        expected_role = CourseAccessRoleFactory.create(
            course_id=self.course.id, user=self.user, role="SuperCoolTestRole",
        )
        roles = data.get_user_roles(self.user.username)
        assert roles == {expected_role}

    def test_get_roles_no_roles(self):
        """Get roles for a user who has no roles"""
        roles = data.get_user_roles(self.user.username)
        assert roles == set()

    def test_get_roles_invalid_user(self):
        """Get roles for a user that doesn't exist"""
        with pytest.raises(UserNotFoundError):
            data.get_user_roles("i_dont_exist_and_should_raise_an_error")
