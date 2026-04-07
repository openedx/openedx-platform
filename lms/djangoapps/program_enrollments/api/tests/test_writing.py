"""
Tests for program enrollment writing Python API.

Currently, we do not directly unit test the functions in api/writing.py extensively.
This is okay for now because they are all used in
`rest_api.v1.views` and is thus tested through `rest_api.v1.tests.test_views`.
Eventually it would be good to directly test the Python API function and just use
mocks in the view tests.
"""


from uuid import UUID

import ddt
from django.core.cache import cache
from opaque_keys.edx.keys import CourseKey
from organizations.tests.factories import OrganizationFactory

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from lms.djangoapps.program_enrollments.constants import ProgramCourseEnrollmentRoles
from lms.djangoapps.program_enrollments.constants import ProgramCourseOperationStatuses as CourseStatuses
from lms.djangoapps.program_enrollments.constants import ProgramEnrollmentStatuses as PEStatuses
from lms.djangoapps.program_enrollments.models import (
    CourseAccessRoleAssignment,
    ProgramCourseEnrollment,
    ProgramEnrollment,
)
from lms.djangoapps.program_enrollments.tests.factories import ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory
from openedx.core.djangoapps.catalog.cache import PROGRAM_CACHE_KEY_TPL
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory, CourseRunFactory, ProgramFactory
from openedx.core.djangoapps.catalog.tests.factories import OrganizationFactory as CatalogOrganizationFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase

from ..writing import write_program_course_enrollments, write_program_enrollments


class EnrollmentTestMixin(CacheIsolationTestCase):
    """
    Test data and helper functions
    """
    ENABLED_CACHES = ['default']

    organization_key = 'test'

    program_uuid = UUID('dddddddd-5f48-493d-9910-84e1d36c657f')

    curriculum_uuid_a = UUID('aaaaaaaa-bd26-4370-94b8-b4063858210b')

    @classmethod
    def setUpClass(cls):
        """
        Set up test data
        """
        super().setUpClass()
        catalog_org = CatalogOrganizationFactory.create(key=cls.organization_key)
        cls.program = ProgramFactory.create(
            uuid=cls.program_uuid,
            authoring_organizations=[catalog_org]
        )
        organization = OrganizationFactory.create(short_name=cls.organization_key)
        SAMLProviderConfigFactory.create(organization=organization)

        catalog_course_id_str = 'course-v1:edX+ToyX'
        course_run_id_str = f'{catalog_course_id_str}+Toy_Course'
        cls.course_id = CourseKey.from_string(course_run_id_str)
        CourseOverviewFactory(id=cls.course_id)
        course_run = CourseRunFactory(key=course_run_id_str)
        cls.course = CourseFactory(key=catalog_course_id_str, course_runs=[course_run])
        cls.student_1 = UserFactory(username='student-1')
        cls.student_2 = UserFactory(username='student-2')

    def setUp(self):
        super().setUp()
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=self.program_uuid), self.program, None)

    def create_program_enrollment(self, external_user_key, user=False):
        """
        Creates and returns a ProgramEnrollment for the given external_user_key and
        user if specified.
        """
        program_enrollment = ProgramEnrollmentFactory.create(
            external_user_key=external_user_key,
            program_uuid=self.program_uuid,
        )
        if user is not False:
            program_enrollment.user = user
            program_enrollment.save()
        return program_enrollment

    def create_program_course_enrollment(self, program_enrollment, course_status=CourseStatuses.ACTIVE):
        """
        Creates and returns a ProgramCourseEnrollment for the given program_enrollment and
        self.course_key, creating a CourseEnrollment if the program enrollment has a user
        """
        course_enrollment = None
        if program_enrollment.user:
            course_enrollment = CourseEnrollmentFactory.create(
                course_id=self.course_id,
                user=program_enrollment.user,
                mode=CourseMode.MASTERS
            )
            course_enrollment.is_active = course_status == CourseStatuses.ACTIVE
            course_enrollment.save()
        return ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment,
            course_key=self.course_id,
            course_enrollment=course_enrollment,
            status=course_status,
        )

    def create_program_and_course_enrollments(self, external_user_key, user=False, course_status=CourseStatuses.ACTIVE):
        program_enrollment = self.create_program_enrollment(external_user_key, user)
        return self.create_program_course_enrollment(program_enrollment, course_status=course_status)


@ddt.ddt
class WritingProgramEnrollmentTest(EnrollmentTestMixin):
    """
    Test cases for program enrollment writing functions.
    """
    @ddt.data(
        ('learner-1', 'learner-1', PEStatuses.ENDED),
        # Test mixing the external_user_key casing
        ('learner-1', 'LEARNER-1', PEStatuses.ENROLLED),
    )
    @ddt.unpack
    def test_write_program_enrollments_status_ended(
        self,
        external_key_1,
        external_key_2,
        target_status
    ):
        """
        Successfully updates program enrollment to status if requested.
        This also validates history records are created on both create and update.
        """
        assert ProgramEnrollment.objects.count() == 0
        assert ProgramEnrollment.historical_records.count() == 0  # pylint: disable=no-member
        write_program_enrollments(self.program_uuid, [{
            'external_user_key': external_key_1,
            'status': PEStatuses.PENDING,
            'curriculum_uuid': self.curriculum_uuid_a,
        }], True, False)
        assert ProgramEnrollment.objects.count() == 1
        assert ProgramEnrollment.historical_records.count() == 1  # pylint: disable=no-member
        result = write_program_enrollments(self.program_uuid, [{
            'external_user_key': external_key_2,
            'status': target_status,
            'curriculum_uuid': self.curriculum_uuid_a,
        }], False, True)
        assert ProgramEnrollment.objects.count() == 1
        assert ProgramEnrollment.historical_records.count() == 2  # pylint: disable=no-member
        assert ProgramEnrollment.objects.filter(status=target_status).exists()


@ddt.ddt
class WriteProgramCourseEnrollmentTest(EnrollmentTestMixin):
    """ Test write_program_enrollments API """

    def course_enrollment_request(self, external_key, status=CourseStatuses.ACTIVE, course_staff=None):
        """
        Constructs a single course enrollment request object
        """
        return {
            'external_user_key': external_key,
            'status': status,
            'course_staff': course_staff
        }

    def assert_program_course_enrollment(self, external_user_key, expected_status, has_user, mode=CourseMode.MASTERS):
        """
        Convenience method to assert that a ProgramCourseEnrollment exists,
        and potentially that a CourseEnrollment also exists
        """
        enrollment = ProgramCourseEnrollment.objects.get(
            program_enrollment__external_user_key__iexact=external_user_key,
            program_enrollment__program_uuid=self.program_uuid
        )
        assert expected_status == enrollment.status
        assert self.course_id == enrollment.course_key
        course_enrollment = enrollment.course_enrollment
        if has_user:
            assert course_enrollment is not None
            assert (expected_status == 'active') == course_enrollment.is_active
            assert self.course_id == course_enrollment.course_id
            assert mode == course_enrollment.mode
        else:
            assert course_enrollment is None

    def setup_change_test_data(self, initial_statuses):
        """
        Helper method to setup initial state for update tests
        """
        self.create_program_and_course_enrollments('learner-1', course_status=initial_statuses[0])
        self.create_program_and_course_enrollments('learner-2', course_status=initial_statuses[1])
        self.create_program_and_course_enrollments('learner-3', course_status=initial_statuses[2], user=None)
        self.create_program_and_course_enrollments('learner-4', course_status=initial_statuses[3], user=None)

    def test_create_only(self):
        """
        Test creating new program course enrollments with only the create flag true
        """
        self.create_program_enrollment('learner-1')
        self.create_program_enrollment('learner-2')
        self.create_program_enrollment('learner-3', user=None)
        self.create_program_enrollment('learner-4', user=None)
        course_enrollment_requests = [
            self.course_enrollment_request('learner-1', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-2', CourseStatuses.INACTIVE),
            self.course_enrollment_request('learner-3', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-4', CourseStatuses.INACTIVE),
        ]

        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            False
        )
        self.assertDictEqual(
            {
                'learner-1': CourseStatuses.ACTIVE,
                'learner-2': CourseStatuses.INACTIVE,
                'learner-3': CourseStatuses.ACTIVE,
                'learner-4': CourseStatuses.INACTIVE,
            },
            result,
        )
        self.assert_program_course_enrollment('learner-1', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('learner-2', CourseStatuses.INACTIVE, True)
        self.assert_program_course_enrollment('learner-3', CourseStatuses.ACTIVE, False)
        self.assert_program_course_enrollment('learner-4', CourseStatuses.INACTIVE, False)

    @ddt.data(
        ('active', 'inactive', 'active', 'inactive'),
        ('inactive', 'active', 'inactive', 'active'),
        ('active', 'active', 'active', 'active'),
        ('inactive', 'inactive', 'inactive', 'inactive'),
    )
    def test_update_only(self, initial_statuses):
        """
        Test updating existing enrollments with only the update flag true
        """
        self.setup_change_test_data(initial_statuses)

        course_enrollment_requests = [
            self.course_enrollment_request('learner-1', CourseStatuses.INACTIVE),
            self.course_enrollment_request('learner-2', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-3', CourseStatuses.INACTIVE),
            self.course_enrollment_request('learner-4', CourseStatuses.ACTIVE),
        ]

        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            False,
            True,
        )
        self.assertDictEqual(
            {
                'learner-1': CourseStatuses.INACTIVE,
                'learner-2': CourseStatuses.ACTIVE,
                'learner-3': CourseStatuses.INACTIVE,
                'learner-4': CourseStatuses.ACTIVE,
            },
            result,
        )
        self.assert_program_course_enrollment('learner-1', CourseStatuses.INACTIVE, True)
        self.assert_program_course_enrollment('learner-2', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('learner-3', CourseStatuses.INACTIVE, False)
        self.assert_program_course_enrollment('learner-4', CourseStatuses.ACTIVE, False)

    def test_create_or_update(self):
        """
        Test writing enrollments with both create and update flags true.
        Existing enrollments should be updated. If no matching enrollment is found, create one.
        """
        # learners 1-4 are already enrolled in courses, 5-6 only have a program enrollment
        self.setup_change_test_data([
            CourseStatuses.ACTIVE, CourseStatuses.ACTIVE,
            CourseStatuses.ACTIVE, CourseStatuses.ACTIVE]
        )
        self.create_program_enrollment('learner-5')
        self.create_program_enrollment('learner-6', user=None)

        course_enrollment_requests = [
            self.course_enrollment_request('learner-1', CourseStatuses.INACTIVE),
            self.course_enrollment_request('learner-2', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-5', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-6', CourseStatuses.ACTIVE),
        ]

        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            True,
        )
        self.assertDictEqual(
            {
                'learner-1': CourseStatuses.INACTIVE,
                'learner-2': CourseStatuses.ACTIVE,
                'learner-5': CourseStatuses.ACTIVE,
                'learner-6': CourseStatuses.ACTIVE,
            },
            result,
        )
        self.assert_program_course_enrollment('learner-1', CourseStatuses.INACTIVE, True)
        self.assert_program_course_enrollment('learner-2', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('learner-5', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('learner-6', CourseStatuses.ACTIVE, False)

    def test_create_or_update_with_mixed_cased_external_user_key(self):
        """
        Test writing enrollments with both create and update flags true.
        However, this time, the external_user_keys are mixed cased.
        Existing enrollments should be updated. If no matching enrollment is found, create one.
        """
        # learners 1-4 are already enrolled in courses, 5-6 only have a program enrollment
        self.setup_change_test_data([
            CourseStatuses.ACTIVE, CourseStatuses.ACTIVE,
            CourseStatuses.ACTIVE, CourseStatuses.ACTIVE]
        )
        self.create_program_enrollment('LEArneR-5')
        self.create_program_enrollment('leARnER-6', user=None)

        course_enrollment_requests = [
            self.course_enrollment_request('leaRNER-1', CourseStatuses.INACTIVE),
            self.course_enrollment_request('LEarner-2', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-5', CourseStatuses.ACTIVE),
            self.course_enrollment_request('learner-6', CourseStatuses.ACTIVE),
        ]

        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            True,
        )
        self.assertDictEqual(
            {
                'leaRNER-1': CourseStatuses.INACTIVE,
                'LEarner-2': CourseStatuses.ACTIVE,
                'learner-5': CourseStatuses.ACTIVE,
                'learner-6': CourseStatuses.ACTIVE,
            },
            result,
        )
        self.assert_program_course_enrollment('learner-1', CourseStatuses.INACTIVE, True)
        self.assert_program_course_enrollment('learner-2', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('LEArneR-5', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('leARnER-6', CourseStatuses.ACTIVE, False)

    def test_create_conflicting_enrollment(self):
        """
        The program enrollments application already has a program_course_enrollment
        record for this user and course
        """
        self.create_program_and_course_enrollments('learner-1')
        course_enrollment_requests = [self.course_enrollment_request('learner-1')]
        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            False,
        )
        self.assertDictEqual({'learner-1': CourseStatuses.CONFLICT}, result)

    def test_create_conflicting_enrollment_mixed_case_external_user_key(self):
        """
        The program enrollments application already has a program_course_enrollment
        record for this user with a mixed cased external_user_key and course
        """
        self.create_program_and_course_enrollments('learner-1')
        course_enrollment_requests = [self.course_enrollment_request('LeArnER-1')]
        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            False,
        )
        self.assertDictEqual({'LeArnER-1': CourseStatuses.CONFLICT}, result)

    def test_update_nonexistent_enrollment(self):
        self.create_program_enrollment('learner-1')
        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            [self.course_enrollment_request('learner-1')],
            False,
            True,
        )
        self.assertDictEqual({'learner-1': CourseStatuses.NOT_FOUND}, result)

    def test_invalid_status(self):
        self.create_program_enrollment('learner-1')
        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            [self.course_enrollment_request('learner-1', 'not-a-status')],
            True,
            False,
        )
        self.assertDictEqual({'learner-1': CourseStatuses.INVALID_STATUS}, result)

    def test_duplicate_external_keys(self):
        self.create_program_enrollment('learner-1')
        course_enrollment_requests = [
            self.course_enrollment_request('learner-1'),
            self.course_enrollment_request('learner-1'),
        ]
        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            False,
        )
        self.assertDictEqual(
            {
                'learner-1': CourseStatuses.DUPLICATED,
            },
            result
        )

    def test_learner_not_in_program(self):
        result = write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            [self.course_enrollment_request('learner-1', CourseStatuses.ACTIVE)],
            True,
            False,
        )
        self.assertDictEqual({'learner-1': CourseStatuses.NOT_IN_PROGRAM}, result)

    @ddt.data(
        'learner',
        'LEARNer',
        'learNER',
        'LEARNER',
    )
    def test_create_enrollments_and_assign_staff(self, request_user_key_prefix):
        """
        Successfully creates both waiting and linked program course enrollments with the course staff role.
        """
        course_staff_role = CourseStaffRole(self.course_id)
        course_staff_role.add_users(self.student_1)

        self.create_program_enrollment('learner-1', user=None)
        self.create_program_enrollment('learner-2', user=self.student_1)
        self.create_program_enrollment('learner-3', user=self.student_2)

        course_enrollment_requests = [
            self.course_enrollment_request(
                '{}-1'.format(request_user_key_prefix), CourseStatuses.ACTIVE, True
            ),
            self.course_enrollment_request(
                '{}-2'.format(request_user_key_prefix), CourseStatuses.ACTIVE, True
            ),
            self.course_enrollment_request(
                '{}-3'.format(request_user_key_prefix), CourseStatuses.ACTIVE, True
            ),
        ]
        write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            True,
        )

        self.assert_program_course_enrollment('learner-1', CourseStatuses.ACTIVE, False)
        self.assert_program_course_enrollment('learner-2', CourseStatuses.ACTIVE, True)
        self.assert_program_course_enrollment('learner-3', CourseStatuses.ACTIVE, True)

        # Users linked to either enrollment are given the course staff role
        self.assertListEqual(
            [self.student_1, self.student_2],
            list(course_staff_role.users_with_role())
        )

        # CourseAccessRoleAssignment objects are created for enrollments with no linked user
        pending_role_assingments = CourseAccessRoleAssignment.objects.all()
        assert pending_role_assingments.count() == 1
        pending_role_assingments.get(
            enrollment__program_enrollment__external_user_key='learner-1',
            enrollment__course_key=self.course_id
        )

    def test_update_and_assign_or_revoke_staff(self):
        """
        Successfully updates existing enrollments to assign or revoke the CourseStaff role.
        """
        course_staff_role = CourseStaffRole(self.course_id)
        course_staff_role.add_users(self.student_2)

        self.create_program_and_course_enrollments('learner-1', user=self.student_1)
        self.create_program_and_course_enrollments('learner-2', user=self.student_2)
        self.create_program_and_course_enrollments('learner-3', user=None)
        learner_4_enrollment = self.create_program_and_course_enrollments('learner-4', user=None)
        learner_5_enrollment = self.create_program_and_course_enrollments('learner-5', user=None)
        CourseAccessRoleAssignment.objects.create(
            enrollment=learner_4_enrollment,
            role=ProgramCourseEnrollmentRoles.COURSE_STAFF,
        )
        CourseAccessRoleAssignment.objects.create(
            enrollment=learner_5_enrollment,
            role=ProgramCourseEnrollmentRoles.COURSE_STAFF,
        )
        course_enrollment_requests = [
            self.course_enrollment_request('learner-1', CourseStatuses.ACTIVE, True),
            self.course_enrollment_request('learner-2', CourseStatuses.ACTIVE, False),
            self.course_enrollment_request('learner-3', CourseStatuses.ACTIVE, True),
            self.course_enrollment_request('learner-4', CourseStatuses.ACTIVE, False),
            self.course_enrollment_request('learner-5', CourseStatuses.ACTIVE, True),
        ]
        write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            course_enrollment_requests,
            True,
            True,
        )
        # Role is revoked for user's with a linked enrollment
        self.assertListEqual(
            [self.student_1],
            list(course_staff_role.users_with_role())
        )

        # CourseAccessRoleAssignment objects are created/revoked for enrollments with no linked user
        pending_role_assingments = CourseAccessRoleAssignment.objects.all()
        assert pending_role_assingments.count() == 2
        pending_role_assingments.get(
            enrollment__program_enrollment__external_user_key='learner-3',
            enrollment__course_key=self.course_id
        )
        pending_role_assingments.get(
            enrollment__program_enrollment__external_user_key='learner-5',
            enrollment__course_key=self.course_id
        )

    def test_user_currently_enrolled_in_course(self):
        """
        If a user is already enrolled in a course through a different method
        that enrollment should be linked but not overwritten as masters.
        """
        CourseEnrollmentFactory.create(
            course_id=self.course_id,
            user=self.student_1,
            mode=CourseMode.VERIFIED
        )
        self.create_program_enrollment('learner-1', user=self.student_1)
        write_program_course_enrollments(
            self.program_uuid,
            self.course_id,
            [self.course_enrollment_request('learner-1', CourseStatuses.ACTIVE)],
            True,
            False
        )
        self.assert_program_course_enrollment('learner-1', CourseStatuses.ACTIVE, True, mode=CourseMode.VERIFIED)
