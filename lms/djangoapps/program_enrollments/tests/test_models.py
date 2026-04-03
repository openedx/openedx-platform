"""
Unit tests for ProgramEnrollment models.
"""


from uuid import UUID

import ddt
import pytest
from django.db.utils import IntegrityError
from django.test import TestCase
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory

from ..constants import ProgramCourseEnrollmentRoles
from ..models import ProgramEnrollment
from .factories import CourseAccessRoleAssignmentFactory, ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory


class ProgramEnrollmentModelTests(TestCase):
    """
    Tests for the ProgramEnrollment model.
    """
    def setUp(self):
        """
        Set up the test data used in the specific tests
        """
        super().setUp()
        self.user = UserFactory(username="rocko")
        self.program_uuid = UUID("88888888-4444-2222-1111-000000000000")
        self.other_program_uuid = UUID("88888888-4444-3333-1111-000000000000")
        self.curriculum_uuid = UUID("77777777-4444-2222-1111-000000000000")
        self.enrollment = ProgramEnrollmentFactory(
            user=self.user,
            external_user_key='abc',
            program_uuid=self.program_uuid,
            curriculum_uuid=self.curriculum_uuid,
            status='enrolled'
        )

    def test_str_and_repr(self):
        """
        Make sure str() and repr() work correctly on instances of this model.
        """
        assert str(self.enrollment) == "[ProgramEnrollment id=1]"
        assert repr(self.enrollment) == (
            "<ProgramEnrollment id=1 user=<User: rocko> external_user_key='abc'"
            " program_uuid=UUID('88888888-4444-2222-1111-000000000000')"
            " curriculum_uuid=UUID('77777777-4444-2222-1111-000000000000')"
            " status='enrolled'>"
        )

    def test_unique_external_key_program_curriculum(self):
        """
        A record with the same (external_user_key, program_uuid, curriculum_uuid) cannot be duplicated.
        """
        with pytest.raises(IntegrityError):
            _ = ProgramEnrollmentFactory(
                user=None,
                external_user_key='abc',
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                status='pending',
            )

    def test_unique_user_program_curriculum(self):
        """
        A record with the same (user, program_uuid, curriculum_uuid) cannot be duplicated.
        """
        with pytest.raises(IntegrityError):
            _ = ProgramEnrollmentFactory(
                user=self.user,
                external_user_key=None,
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                status='suspended',
            )

    def test_user_retirement(self):
        """
        Test that the external_user_key is successfully retired for a user's program enrollments
        and history.
        """
        new_status = 'canceled'

        self.enrollment.status = new_status
        self.enrollment.save()

        # Ensure that all the records had values for external_user_key
        assert self.enrollment.external_user_key == 'abc'

        assert self.enrollment.historical_records.all()
        for record in self.enrollment.historical_records.all():
            assert record.external_user_key == 'abc'

        ProgramEnrollment.retire_user(self.user.id)
        self.enrollment.refresh_from_db()

        # Ensure those values are retired
        assert self.enrollment.external_user_key.startswith('retired_external_key')

        assert self.enrollment.historical_records.all()
        for record in self.enrollment.historical_records.all():
            assert record.external_user_key.startswith('retired_external_key')


@ddt.ddt
class ProgramCourseEnrollmentModelTests(TestCase):
    """
    Tests for the ProgramCourseEnrollment model.
    """
    def setUp(self):
        """
        Set up test data
        """
        super().setUp()
        RequestCache.clear_all_namespaces()
        self.user = UserFactory(username="rocko")
        self.program_uuid = UUID("88888888-4444-2222-1111-000000000000")
        self.curriculum_uuid = UUID("77777777-4444-2222-1111-000000000000")
        self.program_enrollment = ProgramEnrollmentFactory(
            user=self.user,
            external_user_key='abc',
            program_uuid=self.program_uuid,
            curriculum_uuid=self.curriculum_uuid,
            status='enrolled'
        )
        self.course_key = CourseKey.from_string("course-v1:blah+blah+blah")
        CourseOverviewFactory(id=self.course_key)

    def test_str_and_repr(self):
        """
        Make sure str() and repr() work correctly on instances of this model.
        """
        pce = self._create_completed_program_course_enrollment()
        assert str(pce) == "[ProgramCourseEnrollment id=1]"
        # The course enrollment contains timestamp information,
        # so to avoid dealing with that, let's just test the parts of the repr()
        # that come before that.
        assert (
            "<ProgramCourseEnrollment id=1"
            " program_enrollment=<ProgramEnrollment id=1 user=<User: rocko>"
            " external_user_key='abc'"
            " program_uuid=UUID('88888888-4444-2222-1111-000000000000')"
            " curriculum_uuid=UUID('77777777-4444-2222-1111-000000000000')"
            " status='enrolled'>"
            " course_enrollment=<[CourseEnrollment] rocko: course-v1:blah+blah+blah"
        ) in repr(pce)

    def test_duplicate_enrollments_allowed(self):
        """
        A record with the same (program_enrollment, course_enrollment)
        can be created as long as only one record is active for the
        same course_enrollment
        """
        pce = self._create_completed_program_course_enrollment()
        ProgramCourseEnrollmentFactory(
            program_enrollment=pce.program_enrollment,
            course_key="course-v1:dummy+value+101",
            course_enrollment=pce.course_enrollment,
            status="inactive",
        )

    def test_unique_waiting_enrollment(self):
        """
        A record with the same (program_enrollment, course_key)
        cannot be created.
        """
        pce = self._create_waiting_program_course_enrollment()
        with pytest.raises(IntegrityError):
            ProgramCourseEnrollmentFactory(
                program_enrollment=pce.program_enrollment,
                course_key=pce.course_key,
                course_enrollment=None,
                status="inactive",
            )

    def _create_completed_program_course_enrollment(self):
        """ helper function create program course enrollment """
        course_enrollment = CourseEnrollmentFactory.create(
            course_id=self.course_key,
            user=self.user,
            mode=CourseMode.MASTERS
        )
        program_course_enrollment = ProgramCourseEnrollmentFactory(
            program_enrollment=self.program_enrollment,
            course_key=self.course_key,
            course_enrollment=course_enrollment,
            status="active"
        )
        return program_course_enrollment

    def _create_waiting_program_course_enrollment(self):
        """ helper function create program course enrollment with no lms user """
        return ProgramCourseEnrollmentFactory(
            program_enrollment=self.program_enrollment,
            course_key=self.course_key,
            course_enrollment=None,
            status="active"
        )


class CourseAccessRoleAssignmentTests(TestCase):
    """
    Tests for the CourseAccessRoleAssignment model.
    """
    def setUp(self):
        super().setUp()
        self.program_course_enrollment = ProgramCourseEnrollmentFactory()
        self.pending_role_assignment = CourseAccessRoleAssignmentFactory(
            enrollment=self.program_course_enrollment,
            role=ProgramCourseEnrollmentRoles.COURSE_STAFF,
        )

    def test_str_and_repr(self):
        """
        Make sure str() and repr() work correctly on instances of this model.
        """
        assert str(self.pending_role_assignment) == "[CourseAccessRoleAssignment id=1]"

        # The record contains timestamp information, and a repeat of the ProgramCourseEnrollment repr()
        # already tested above, let's just test the parts of the repr()
        # that come before that.
        assert (
            "<CourseAccessRoleAssignment id=1"
            " role='staff'"
            " enrollment=<ProgramCourseEnrollment id=1"
        ) in repr(self.pending_role_assignment)

    def test_unique(self):
        """
        Multiple records with the same enrollment and role cannot be created
        """
        with pytest.raises(IntegrityError):
            CourseAccessRoleAssignmentFactory(
                enrollment=self.program_course_enrollment,
                role=ProgramCourseEnrollmentRoles.COURSE_STAFF,
            )
