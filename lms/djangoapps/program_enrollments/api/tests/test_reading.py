"""
Tests for program enrollment reading Python API.
"""


from uuid import UUID

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey
from organizations.tests.factories import OrganizationFactory
from social_django.models import UserSocialAuth

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from lms.djangoapps.program_enrollments.constants import ProgramCourseEnrollmentStatuses as PCEStatuses
from lms.djangoapps.program_enrollments.constants import ProgramEnrollmentStatuses as PEStatuses
from lms.djangoapps.program_enrollments.exceptions import (
    OrganizationDoesNotExistException,
    ProgramDoesNotExistException,
    ProviderDoesNotExistException,
)
from lms.djangoapps.program_enrollments.models import ProgramCourseEnrollment, ProgramEnrollment
from lms.djangoapps.program_enrollments.tests.factories import (
    CourseAccessRoleAssignmentFactory,
    ProgramCourseEnrollmentFactory,
    ProgramEnrollmentFactory,
)
from openedx.core.djangoapps.catalog.cache import PROGRAM_CACHE_KEY_TPL
from openedx.core.djangoapps.catalog.tests.factories import OrganizationFactory as CatalogOrganizationFactory
from openedx.core.djangoapps.catalog.tests.factories import ProgramFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase

from ..reading import (
    fetch_program_course_enrollments,
    fetch_program_course_enrollments_by_students,
    fetch_program_enrollments,
    fetch_program_enrollments_by_student,
    fetch_program_enrollments_by_students,
    get_external_key_by_user_and_course,
    get_program_course_enrollment,
    get_program_enrollment,
    get_provider_slug,
    get_users_by_external_keys,
    is_course_staff_enrollment,
)

User = get_user_model()


@ddt.ddt
class ProgramEnrollmentReadingTests(TestCase):
    """
    Tests for program enrollment reading functions.
    """
    program_uuid_x = UUID('dddddddd-5f48-493d-9410-84e1d36c657f')
    program_uuid_y = UUID('eeeeeeee-f803-43f6-bbf3-5ae15d393649')
    program_uuid_z = UUID('ffffffff-89eb-43df-a6b9-c144e7204fd7')  # No enrollments
    curriculum_uuid_a = UUID('aaaaaaaa-bd26-43d0-94b8-b0063858210b')
    curriculum_uuid_b = UUID('bbbbbbbb-145f-43db-ad05-f9ad65eec285')
    curriculum_uuid_c = UUID('cccccccc-4577-4559-85f0-4a83e8160a4d')
    course_key_p = CourseKey.from_string('course-v1:TestX+ProEnroll+P')
    course_key_q = CourseKey.from_string('course-v1:TestX+ProEnroll+Q')
    course_key_r = CourseKey.from_string('course-v1:TestX+ProEnroll+R')
    username_0 = 'user-0'
    username_1 = 'user-1'
    username_2 = 'user-2'
    username_3 = 'user-3'
    username_4 = 'user-4'
    ext_3 = 'student-3'
    ext_4 = 'student-4'
    ext_5 = 'student-5'
    ext_6 = 'student-6'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user_0 = UserFactory(username=cls.username_0)  # No enrollments
        cls.user_1 = UserFactory(username=cls.username_1)
        cls.user_2 = UserFactory(username=cls.username_2)
        cls.user_3 = UserFactory(username=cls.username_3)
        cls.user_4 = UserFactory(username=cls.username_4)
        CourseOverviewFactory(id=cls.course_key_p)
        CourseOverviewFactory(id=cls.course_key_q)
        CourseOverviewFactory(id=cls.course_key_r)
        enrollment_test_data = [                                                                      # ID
            (cls.user_1, None, cls.program_uuid_x, cls.curriculum_uuid_a, PEStatuses.ENROLLED),       # 1
            (cls.user_2, None, cls.program_uuid_x, cls.curriculum_uuid_a, PEStatuses.PENDING),        # 2
            (cls.user_3, cls.ext_3, cls.program_uuid_x, cls.curriculum_uuid_b, PEStatuses.ENROLLED),  # 3
            (cls.user_4, cls.ext_4, cls.program_uuid_x, cls.curriculum_uuid_b, PEStatuses.PENDING),   # 4
            (None, cls.ext_5, cls.program_uuid_x, cls.curriculum_uuid_b, PEStatuses.SUSPENDED),       # 5
            (None, cls.ext_6, cls.program_uuid_y, cls.curriculum_uuid_c, PEStatuses.CANCELED),        # 6
            (cls.user_3, cls.ext_3, cls.program_uuid_y, cls.curriculum_uuid_c, PEStatuses.CANCELED),  # 7
            (None, cls.ext_4, cls.program_uuid_y, cls.curriculum_uuid_c, PEStatuses.ENROLLED),        # 8
            (cls.user_1, None, cls.program_uuid_x, cls.curriculum_uuid_b, PEStatuses.SUSPENDED),      # 9
            (cls.user_2, None, cls.program_uuid_y, cls.curriculum_uuid_c, PEStatuses.ENDED),          # 10
        ]
        for user, external_user_key, program_uuid, curriculum_uuid, status in enrollment_test_data:
            ProgramEnrollmentFactory(
                user=user,
                external_user_key=external_user_key,
                program_uuid=program_uuid,
                curriculum_uuid=curriculum_uuid,
                status=status,
            )
        course_enrollment_test_data = [                   # ID
            (1, cls.course_key_p, PCEStatuses.ACTIVE),    # 1
            (1, cls.course_key_q, PCEStatuses.ACTIVE),    # 2
            (9, cls.course_key_r, PCEStatuses.ACTIVE),    # 3
            (2, cls.course_key_p, PCEStatuses.INACTIVE),  # 4
            (3, cls.course_key_p, PCEStatuses.ACTIVE),    # 5
            (5, cls.course_key_p, PCEStatuses.INACTIVE),  # 6
            (8, cls.course_key_p, PCEStatuses.ACTIVE),    # 7
            (8, cls.course_key_q, PCEStatuses.INACTIVE),  # 8
            (2, cls.course_key_r, PCEStatuses.INACTIVE),  # 9
            (6, cls.course_key_r, PCEStatuses.INACTIVE),  # 10
            (8, cls.course_key_r, PCEStatuses.ACTIVE),    # 11
            (7, cls.course_key_q, PCEStatuses.ACTIVE),    # 12

        ]
        for program_enrollment_id, course_key, status in course_enrollment_test_data:
            program_enrollment = ProgramEnrollment.objects.get(id=program_enrollment_id)
            course_enrollment = (
                CourseEnrollmentFactory(
                    course_id=course_key,
                    user=program_enrollment.user,
                    mode=CourseMode.MASTERS,
                )
                if program_enrollment.user
                else None
            )
            ProgramCourseEnrollmentFactory(
                program_enrollment=program_enrollment,
                course_enrollment=course_enrollment,
                course_key=course_key,
                status=status,
            )

    @ddt.data(
        # Realized enrollment, specifying only user.
        (program_uuid_x, curriculum_uuid_a, username_1, None, 1),

        # Realized enrollment, specifiying both user and external key.
        (program_uuid_x, curriculum_uuid_b, username_3, ext_3, 3),

        # Realized enrollment, specifiying only external key.
        (program_uuid_x, curriculum_uuid_b, None, ext_4, 4),

        # Waiting enrollment, specifying external key
        (program_uuid_x, curriculum_uuid_b, None, ext_5, 5),

        # Specifying no curriculum (because ext_6 only has Program Y
        # enrollments in one curriculum, so it's not ambiguous).
        (program_uuid_y, None, None, ext_6, 6),
        (program_uuid_y, None, username_2, None, 10),
        # use mixed casing for external_user_id
        (program_uuid_x, curriculum_uuid_b, None, 'STUDENT-4', 4),
        (program_uuid_x, curriculum_uuid_b, None, 'STUDent-5', 5),
        (program_uuid_y, None, None, 'STudENT-6', 6),
    )
    @ddt.unpack
    def test_get_program_enrollment(
            self,
            program_uuid,
            curriculum_uuid,
            username,
            external_user_key,
            expected_enrollment_id,
    ):
        user = User.objects.get(username=username) if username else None
        actual_enrollment = get_program_enrollment(
            program_uuid=program_uuid,
            curriculum_uuid=curriculum_uuid,
            user=user,
            external_user_key=external_user_key,
        )
        assert actual_enrollment.id == expected_enrollment_id

    @ddt.data(
        # Realized enrollment, specifying only user.
        (program_uuid_x, None, course_key_p, username_1, None, 1),

        # Realized enrollment, specifiying both user and external key.
        (program_uuid_x, None, course_key_p, username_3, ext_3, 5),

        # Realized enrollment, specifiying only external key.
        (program_uuid_y, None, course_key_p, None, ext_4, 7),

        # Waiting enrollment, specifying external key
        (program_uuid_x, None, course_key_p, None, ext_5, 6),

        # We can specify curriculum, but it shouldn't affect anything,
        # because each user-course pairing can only have one
        # program-course enrollment.
        (program_uuid_y, curriculum_uuid_c, course_key_r, None, ext_6, 10),
        # Use mixed casing for external_user_key
        (program_uuid_x, None, course_key_p, username_3, 'stuDENT-3', 5),
        (program_uuid_y, None, course_key_p, None, 'STudenT-4', 7),
        (program_uuid_x, None, course_key_p, None, 'STUDENT-5', 6),
    )
    @ddt.unpack
    def test_get_program_course_enrollment(
            self,
            program_uuid,
            curriculum_uuid,
            course_key,
            username,
            external_user_key,
            expected_enrollment_id,
    ):
        user = User.objects.get(username=username) if username else None
        actual_enrollment = get_program_course_enrollment(
            program_uuid=program_uuid,
            curriculum_uuid=curriculum_uuid,
            course_key=course_key,
            user=user,
            external_user_key=external_user_key,
        )
        assert actual_enrollment.id == expected_enrollment_id

    @ddt.data(

        # Program with no enrollments
        (
            {'program_uuid': program_uuid_z},
            set(),
        ),

        # Curriculum & status filters
        (
            {
                'program_uuid': program_uuid_x,
                'curriculum_uuids': {curriculum_uuid_a, curriculum_uuid_c},
                'program_enrollment_statuses': {PEStatuses.PENDING, PEStatuses.CANCELED},
            },
            {2},
        ),

        # User & external key filters
        (
            {
                'program_uuid': program_uuid_x,
                'usernames': {username_1, username_2, username_3, username_4},
                'external_user_keys': {ext_3, ext_4, ext_5}
            },
            {3, 4},
        ),

        # Realized-only filter
        (
            {'program_uuid': program_uuid_x, 'realized_only': True},
            {1, 2, 3, 4, 9},
        ),

        # Waiting-only filter
        (
            {'program_uuid': program_uuid_x, 'waiting_only': True},
            {5},
        ),
        # Use mixed casing on external_user_key
        (
            {
                'program_uuid': program_uuid_x,
                'usernames': {username_1, username_2, username_3, username_4},
                'external_user_keys': {'studeNT-3', 'STUdent-4', 'STudenT-5'}
            },
            {3, 4},
        ),
    )
    @ddt.unpack
    def test_fetch_program_enrollments(self, kwargs, expected_enrollment_ids):
        kwargs = self._usernames_to_users(kwargs)
        actual_enrollments = fetch_program_enrollments(**kwargs)
        actual_enrollment_ids = {enrollment.id for enrollment in actual_enrollments}
        assert actual_enrollment_ids == expected_enrollment_ids

    @ddt.data(

        # Program with no enrollments
        (
            {'program_uuid': program_uuid_z, 'course_key': course_key_p},
            set(),
        ),

        # Curriculum, status, active-only filters
        (
            {
                'program_uuid': program_uuid_x,
                'course_key': course_key_p,
                'curriculum_uuids': {curriculum_uuid_a, curriculum_uuid_c},
                'program_enrollment_statuses': {PEStatuses.ENROLLED},
                'active_only': True,
            },
            {1},
        ),

        # User and external key filters
        (
            {
                'program_uuid': program_uuid_x,
                'course_key': course_key_p,
                'usernames': {username_2, username_3},
                'external_user_keys': {ext_3, ext_5}
            },
            {5},
        ),

        # Realized-only filter
        (
            {
                'program_uuid': program_uuid_x,
                'course_key': course_key_p,
                'realized_only': True,
            },
            {1, 4, 5},
        ),

        # Waiting-only and inactive-only filters
        (
            {
                'program_uuid': program_uuid_y,
                'course_key': course_key_r,
                'waiting_only': True,
                'inactive_only': True,
            },
            {10},
        ),
        # Use mixed casing on external_user_key
        (
            {
                'program_uuid': program_uuid_x,
                'course_key': course_key_p,
                'usernames': {username_2, username_3},
                'external_user_keys': {'STudENt-3', 'stuDENt-5'}
            },
            {5},
        ),
    )
    @ddt.unpack
    def test_fetch_program_course_enrollments(self, kwargs, expected_enrollment_ids):
        kwargs = self._usernames_to_users(kwargs)
        actual_enrollments = fetch_program_course_enrollments(**kwargs)
        actual_enrollment_ids = {enrollment.id for enrollment in actual_enrollments}
        assert actual_enrollment_ids == expected_enrollment_ids

    @ddt.data(

        # User with no enrollments
        (
            {'username': username_0},
            set(),
        ),

        # Filters
        (
            {
                'username': username_3,
                'external_user_key': ext_3,
                'program_uuids': {program_uuid_x},
                'curriculum_uuids': {curriculum_uuid_b, curriculum_uuid_c},
                'program_enrollment_statuses': {PEStatuses.ENROLLED, PEStatuses.CANCELED},
            },
            {3},
        ),

        # More filters
        (
            {
                'username': username_3,
                'external_user_key': ext_3,
                'program_uuids': {program_uuid_x, program_uuid_y},
                'curriculum_uuids': {curriculum_uuid_b, curriculum_uuid_c},
                'program_enrollment_statuses': {PEStatuses.SUSPENDED, PEStatuses.CANCELED},
            },
            {7},
        ),

        # Realized-only filter
        (
            {'external_user_key': ext_4, 'realized_only': True},
            {4},
        ),

        # Waiting-only filter
        (
            {'external_user_key': ext_4, 'waiting_only': True},
            {8},
        ),
        # Use mixed casing on external_user_key
        (
            {'external_user_key': 'STudeNT-4', 'realized_only': True},
            {4},
        ),
    )
    @ddt.unpack
    def test_fetch_program_enrollments_by_student(self, kwargs, expected_enrollment_ids):
        kwargs = self._username_to_user(kwargs)
        actual_enrollments = fetch_program_enrollments_by_student(**kwargs)
        actual_enrollment_ids = {enrollment.id for enrollment in actual_enrollments}
        assert actual_enrollment_ids == expected_enrollment_ids

    @ddt.data(

        # User with no enrollments
        (
            {'usernames': [username_0]},
            set(),
        ),

        # Filters
        (
            {
                'usernames': [username_3],
            },
            {3, 7},
        ),

        # More filters
        (
            {
                'usernames': [username_3],
                'external_user_keys': [ext_3],
                'program_enrollment_statuses': {PEStatuses.SUSPENDED, PEStatuses.CANCELED},
            },
            {7},
        ),

        # Realized-only filter
        (
            {'usernames': [username_4], 'realized_only': True},
            {4},
        ),

        # Waiting-only filter
        (
            {'external_user_keys': [ext_4], 'waiting_only': True},
            {8},
        ),
        # Use mixed casing on external_user_key
        (
            {'external_user_keys': ['STUdenT-4'], 'waiting_only': True},
            {8},
        ),
    )
    @ddt.unpack
    def test_fetch_program_enrollments_by_students(self, kwargs, expected_enrollment_ids):
        kwargs = self._usernames_to_users(kwargs)
        actual_enrollments = fetch_program_enrollments_by_students(**kwargs)
        actual_enrollment_ids = {enrollment.id for enrollment in actual_enrollments}
        assert actual_enrollment_ids == expected_enrollment_ids

    @ddt.data(

        # User with no program enrollments
        (
            {'usernames': [username_0]},
            set(),
        ),

        # Course keys and active-only filters
        (
            {
                'external_user_keys': [ext_4],
                'course_keys': {course_key_p, course_key_q},
                'active_only': True,
            },
            {7},
        ),

        # Curriculum filter
        (
            {'usernames': [username_3], 'curriculum_uuids': {curriculum_uuid_b}},
            {5},
        ),

        # Program filter
        (
            {'usernames': [username_3], 'program_uuids': {program_uuid_y}},
            {12},
        ),

        # Realized-only filter
        (
            {'external_user_keys': [ext_4], 'realized_only': True},
            set(),
        ),

        # Waiting-only and inactive-only filter
        (
            {
                'external_user_keys': [ext_4],
                'waiting_only': True,
                'inactive_only': True,
            },
            {8},
        ),
        # Use mixed casing on external_user_key
        (
            {'external_user_keys': ['STUDENT-4'], 'realized_only': True},
            set(),
        ),
    )
    @ddt.unpack
    def test_fetch_program_course_enrollments_by_students(self, kwargs, expected_enrollment_ids):
        kwargs = self._usernames_to_users(kwargs)
        actual_enrollments = fetch_program_course_enrollments_by_students(**kwargs)
        actual_enrollment_ids = {enrollment.id for enrollment in actual_enrollments}
        assert actual_enrollment_ids == expected_enrollment_ids

    @staticmethod
    def _username_to_user(dictionary):
        """
        We can't access the user instances when building `ddt.data`,
        so return a dict with the username swapped out for the user themself.
        """
        result = dictionary.copy()
        if 'username' in result:
            result['user'] = User.objects.get(username=result['username'])
            del result['username']
        return result

    @staticmethod
    def _usernames_to_users(dictionary):
        """
        We can't access the user instances when building `ddt.data`,
        so return a dict with the usernames swapped out for the users themselves.
        """
        result = dictionary.copy()
        if 'usernames' in result:
            result['users'] = set(
                User.objects.filter(username__in=result['usernames'])
            )
            del result['usernames']
        return result

    @ddt.data(
        (
            {'username': username_0, 'course_key': course_key_p},
            None
        ),
        (
            {'username': username_1, 'course_key': course_key_p},
            None
        ),
        (
            {'username': username_1, 'course_key': course_key_r},
            None
        ),
        (
            {'username': username_2, 'course_key': course_key_p},
            None
        ),
        (
            {'username': username_3, 'course_key': course_key_p},
            ext_3
        ),
        (
            {'username': username_3, 'course_key': course_key_r},
            None
        ),
        (
            {'username': username_4, 'course_key': course_key_p},
            None
        )
    )
    @ddt.unpack
    def test_get_external_key_by_user_and_course(self, kwargs, expected_external_user_key):
        kwarg = self._username_to_user(kwargs)
        external_user_key = get_external_key_by_user_and_course(**kwarg)
        assert expected_external_user_key == external_user_key


class GetUsersByExternalKeysTests(CacheIsolationTestCase):
    """
    Tests for the get_users_by_external_keys function
    """
    ENABLED_CACHES = ['default']

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.program_uuid = UUID('e7a82f8d-d485-486b-b733-a28222af92bf')
        cls.organization_key = 'ufo'
        cls.external_user_id = '1234'
        cls.user_0 = UserFactory(username='user-0')
        cls.user_1 = UserFactory(username='user-1')
        cls.user_2 = UserFactory(username='user-2')

    def setUp(self):
        super().setUp()
        catalog_org = CatalogOrganizationFactory.create(key=self.organization_key)
        program = ProgramFactory.create(
            uuid=self.program_uuid,
            authoring_organizations=[catalog_org]
        )
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=self.program_uuid), program, None)

    def create_social_auth_entry(self, user, provider, external_id):
        """
        helper functio to create a user social auth entry
        """
        UserSocialAuth.objects.create(
            user=user,
            uid=f'{provider.slug}:{external_id}',
            provider=provider.backend_name,
        )

    def test_single_saml_provider(self):
        """
        Test that get_users_by_external_keys returns the expected
        mapping of external keys to users when a single saml provider
        is configured.
        """
        organization = OrganizationFactory.create(short_name=self.organization_key)
        provider = SAMLProviderConfigFactory.create(organization=organization)
        self.create_social_auth_entry(self.user_0, provider, 'ext-user-0')
        self.create_social_auth_entry(self.user_1, provider, 'ext-user-1')
        self.create_social_auth_entry(self.user_2, provider, 'ext-user-2')
        requested_keys = {'ext-user-1', 'ext-user-2', 'ext-user-3'}
        actual = get_users_by_external_keys(self.program_uuid, requested_keys)
        # ext-user-0 not requested, ext-user-3 doesn't exist
        expected = {
            'ext-user-1': self.user_1,
            'ext-user-2': self.user_2,
            'ext-user-3': None,
        }
        assert actual == expected

    def test_multiple_saml_providers(self):
        """
        Test that get_users_by_external_keys returns the expected
        mapping of external keys to users when multiple saml providers
        are configured.
        """
        organization = OrganizationFactory.create(short_name=self.organization_key)
        provider_1 = SAMLProviderConfigFactory.create(organization=organization)
        provider_2 = SAMLProviderConfigFactory.create(
            organization=organization,
            slug='test-shib-2',
            enabled=True
        )
        self.create_social_auth_entry(self.user_0, provider_1, 'ext-user-0')
        self.create_social_auth_entry(self.user_1, provider_1, 'ext-user-1')
        self.create_social_auth_entry(self.user_1, provider_2, 'ext-user-1')
        self.create_social_auth_entry(self.user_2, provider_2, 'ext-user-2')
        requested_keys = {'ext-user-1', 'ext-user-2', 'ext-user-3'}
        actual = get_users_by_external_keys(self.program_uuid, requested_keys)
        # ext-user-0 not requested, ext-user-3 doesn't exist,
        # ext-user-2 is authorized with secondary provider
        # ext-user-1 has an entry in both providers
        expected = {
            'ext-user-1': self.user_1,
            'ext-user-2': self.user_2,
            'ext-user-3': None,
        }
        assert actual == expected

    def test_empty_request(self):
        """
        Test that requesting no external keys does not cause an exception.
        """
        organization = OrganizationFactory.create(short_name=self.organization_key)
        SAMLProviderConfigFactory.create(organization=organization)
        actual = get_users_by_external_keys(self.program_uuid, set())
        assert actual == {}

    def test_catalog_program_does_not_exist(self):
        """
        Test ProgramDoesNotExistException is thrown if the program cache does
        not include the requested program uuid.
        """
        fake_program_uuid = UUID('80cc59e5-003e-4664-a582-48da44bc7e12')
        with pytest.raises(ProgramDoesNotExistException):
            get_users_by_external_keys(fake_program_uuid, [])

    def test_catalog_program_missing_org(self):
        """
        Test OrganizationDoesNotExistException is thrown if the cached program does not
        have an authoring organization.
        """
        program = ProgramFactory.create(
            uuid=self.program_uuid,
            authoring_organizations=[]
        )
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=self.program_uuid), program, None)
        with pytest.raises(OrganizationDoesNotExistException):
            get_users_by_external_keys(self.program_uuid, [])

    def test_lms_organization_not_found(self):
        """
        Test an OrganizationDoesNotExistException is thrown if the LMS has no organization
        matching the catalog program's authoring_organization
        """
        organization = OrganizationFactory.create(short_name='some_other_org')
        SAMLProviderConfigFactory.create(organization=organization)
        with pytest.raises(OrganizationDoesNotExistException):
            get_users_by_external_keys(self.program_uuid, [])

    def test_saml_provider_not_found(self):
        """
        Test that Prov exception is thrown if no SAML provider exists for this
        program's organization.
        """
        OrganizationFactory.create(short_name=self.organization_key)
        with pytest.raises(ProviderDoesNotExistException):
            get_users_by_external_keys(self.program_uuid, [])

    def test_extra_saml_provider_disabled(self):
        """
        If multiple samlprovider records exist with the same organization,
        but the extra record is disabled, no exception is raised.
        """
        organization = OrganizationFactory.create(short_name=self.organization_key)
        SAMLProviderConfigFactory.create(organization=organization)
        # create a second active config for the same organization, NOT enabled
        SAMLProviderConfigFactory.create(
            organization=organization, slug='foox', enabled=False
        )
        get_users_by_external_keys(self.program_uuid, [])


@ddt.ddt
class IsCourseStaffEnrollmentTest(TestCase):
    """
    Tests for the is_course_staff_enrollment function
    """
    program_uuid_x = UUID('dddddddd-5f48-493d-9410-84e1d36c657f')
    program_uuid_y = UUID('eeeeeeee-f803-43f6-bbf3-5ae15d393649')
    curriculum_uuid_a = UUID('aaaaaaaa-bd26-43d0-94b8-b0063858210b')
    curriculum_uuid_b = UUID('bbbbbbbb-145f-43db-ad05-f9ad65eec285')
    course_key_p = CourseKey.from_string('course-v1:TestX+ProEnroll+P')
    course_key_q = CourseKey.from_string('course-v1:TestX+ProEnroll+Q')
    username_0 = 'user-0'
    ext_3 = 'student-3'
    ext_4 = 'student-4'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user_0 = UserFactory(username=cls.username_0)  # No enrollments
        CourseOverviewFactory(id=cls.course_key_p)
        CourseOverviewFactory(id=cls.course_key_q)
        enrollment_test_data = [                                                                      # ID
            (cls.user_0, None, cls.program_uuid_x, cls.curriculum_uuid_a, PEStatuses.ENROLLED),       # 1
            (None, cls.ext_3, cls.program_uuid_x, cls.curriculum_uuid_b, PEStatuses.PENDING),         # 2
            (None, cls.ext_4, cls.program_uuid_y, cls.curriculum_uuid_a, PEStatuses.ENROLLED),        # 3
            (cls.user_0, None, cls.program_uuid_y, cls.curriculum_uuid_b, PEStatuses.SUSPENDED),      # 4
        ]
        for user, external_user_key, program_uuid, curriculum_uuid, status in enrollment_test_data:
            ProgramEnrollmentFactory(
                user=user,
                external_user_key=external_user_key,
                program_uuid=program_uuid,
                curriculum_uuid=curriculum_uuid,
                status=status,
            )
        course_enrollment_test_data = [                          # ID
            (1, cls.course_key_p, PCEStatuses.ACTIVE, True),     # 1
            (2, cls.course_key_q, PCEStatuses.ACTIVE, False),    # 2
            (3, cls.course_key_p, PCEStatuses.ACTIVE, True),     # 3
            (4, cls.course_key_q, PCEStatuses.ACTIVE, False),    # 4
        ]
        for program_enrollment_id, course_key, status, course_staff in course_enrollment_test_data:
            program_enrollment = ProgramEnrollment.objects.get(id=program_enrollment_id)
            course_enrollment = (
                CourseEnrollmentFactory(
                    course_id=course_key,
                    user=program_enrollment.user,
                    mode=CourseMode.MASTERS,
                )
                if program_enrollment.user
                else None
            )

            program_course_enrollment = ProgramCourseEnrollmentFactory(
                program_enrollment=program_enrollment,
                course_enrollment=course_enrollment,
                course_key=course_key,
                status=status,
            )
            if course_staff:
                if program_enrollment.user:
                    CourseStaffRole(course_key).add_users(program_enrollment.user)
                else:
                    CourseAccessRoleAssignmentFactory(
                        enrollment=program_course_enrollment
                    )

    @ddt.data(
        (1, True),
        (2, False),
        (3, True),
        (4, False),
    )
    @ddt.unpack
    def test_is_course_staff_enrollment(self, program_course_enrollment_id, is_course_staff):
        program_course_enrollment = ProgramCourseEnrollment.objects.get(
            id=program_course_enrollment_id
        )
        assert is_course_staff == is_course_staff_enrollment(program_course_enrollment)

    def test_get_provider_slug_correctly_strips(self):
        list_of_providers = []
        for num_provider in range(1000):
            list_of_providers.append(SAMLProviderConfigFactory(entity_id=str(num_provider)))

        for provider in list_of_providers:
            assert provider.slug == get_provider_slug(provider)
