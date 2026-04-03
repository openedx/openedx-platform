"""
Unit tests for ProgramEnrollment views.
"""

import json
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from unittest import mock
from uuid import UUID, uuid4

import ddt
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey
from organizations.tests.factories import OrganizationFactory as LMSOrganizationFactory
from rest_framework import status
from rest_framework.test import APITestCase
from social_django.models import UserSocialAuth

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    InstructorFactory,
    UserFactory,
)
from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from lms.djangoapps.bulk_email.models import BulkEmailFlag, Optout
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.program_enrollments.constants import ProgramCourseOperationStatuses as CourseStatuses
from lms.djangoapps.program_enrollments.constants import ProgramOperationStatuses as ProgramStatuses
from lms.djangoapps.program_enrollments.exceptions import ProviderDoesNotExistException
from lms.djangoapps.program_enrollments.models import ProgramCourseEnrollment, ProgramEnrollment
from lms.djangoapps.program_enrollments.tests.factories import (
    CourseAccessRoleAssignmentFactory,
    ProgramCourseEnrollmentFactory,
    ProgramEnrollmentFactory,
)
from openedx.core.djangoapps.catalog.cache import PROGRAM_CACHE_KEY_TPL, PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL
from openedx.core.djangoapps.catalog.tests.factories import (
    CourseFactory,
    CourseRunFactory,
    CurriculumFactory,
    OrganizationFactory,
    ProgramFactory,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import CacheIsolationMixin
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory
from xmodule.modulestore.tests.factories import CourseFactory as ModulestoreCourseFactory

from .. import views
from ..constants import (
    ENABLE_ENROLLMENT_RESET_FLAG,
    MAX_ENROLLMENT_RECORDS,
    REQUEST_STUDENT_KEY,
    CourseRunProgressStatuses,
)

_DJANGOAPP_PATCH_FORMAT = 'lms.djangoapps.program_enrollments.{}'
_REST_API_PATCH_FORMAT = _DJANGOAPP_PATCH_FORMAT.format('rest_api.v1.{}')
_VIEW_PATCH_FORMAT = _REST_API_PATCH_FORMAT.format('views.{}')
_UTILS_PATCH_FORMAT = _REST_API_PATCH_FORMAT.format('utils.{}')


_get_users_patch_path = _DJANGOAPP_PATCH_FORMAT.format('api.writing.get_users_by_external_keys')
_patch_get_users = mock.patch(
    _get_users_patch_path,
    autospec=True,
    return_value=defaultdict(lambda: None),
)


class ProgramCacheMixin(CacheIsolationMixin):
    """
    Mixin for using program cache in tests
    """
    ENABLED_CACHES = ['default']

    def set_program_in_catalog_cache(self, program_uuid, program):
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=program_uuid), program, None)

    def set_org_in_catalog_cache(self, organization, program_uuids):
        cache.set(PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL.format(org_key=organization.short_name), program_uuids)


class EnrollmentsDataMixin(ProgramCacheMixin):
    """
    Mixin to define some shared test data objects for program/course enrollment
    view tests.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    view_name = 'SET-ME-IN-SUBCLASS'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_cache_isolation()
        cls.organization_key = "testorg"
        cls.catalog_org = OrganizationFactory(key=cls.organization_key)
        cls.lms_org = LMSOrganizationFactory(short_name=cls.organization_key)
        cls.program_uuid = UUID('00000000-1111-2222-3333-444444444444')
        cls.program_uuid_tmpl = '00000000-1111-2222-3333-4444444444{0:02d}'
        cls.curriculum_uuid = UUID('aaaaaaaa-1111-2222-3333-444444444444')
        cls.other_curriculum_uuid = UUID('bbbbbbbb-1111-2222-3333-444444444444')
        inactive_curriculum_uuid = UUID('cccccccc-1111-2222-3333-444444444444')

        catalog_course_id_str = 'course-v1:edX+ToyX'
        course_run_id_str = f'{catalog_course_id_str}+Toy_Course'
        cls.course_id = CourseKey.from_string(course_run_id_str)
        CourseOverviewFactory(id=cls.course_id)
        course_run = CourseRunFactory(key=course_run_id_str)
        cls.course = CourseFactory(key=catalog_course_id_str, course_runs=[course_run])
        inactive_curriculum = CurriculumFactory(uuid=inactive_curriculum_uuid, is_active=False)
        cls.curriculum = CurriculumFactory(uuid=cls.curriculum_uuid, courses=[cls.course])
        cls.program = ProgramFactory(
            uuid=cls.program_uuid,
            authoring_organizations=[cls.catalog_org],
            curricula=[inactive_curriculum, cls.curriculum],
        )

        cls.course_not_in_program = CourseFactory()
        cls.course_not_in_program_id = CourseKey.from_string(
            cls.course_not_in_program["course_runs"][0]["key"]
        )

        cls.password = 'password'
        cls.student = UserFactory(username='student', password=cls.password)
        cls.global_staff = GlobalStaffFactory(username='global-staff', password=cls.password)

    def setUp(self):
        super().setUp()
        self.set_program_in_catalog_cache(self.program_uuid, self.program)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.end_cache_isolation()

    def get_url(self, program_uuid=None, course_id=None):
        """ Returns the primary URL requested by the test case. """
        kwargs = {'program_uuid': program_uuid or self.program_uuid}
        if course_id:
            kwargs['course_id'] = course_id

        return reverse(self.view_name, kwargs=kwargs)

    def log_in_non_staff(self):
        self.client.login(username=self.student.username, password=self.password)

    def log_in_staff(self):
        self.client.login(username=self.global_staff.username, password=self.password)

    def learner_enrollment(self, student_key, enrollment_status="active", course_staff=None):
        """
        Convenience method to create a learner enrollment record
        """
        enrollment_record = {"student_key": student_key, "status": enrollment_status}
        if course_staff is not None:
            enrollment_record["course_staff"] = course_staff
        return enrollment_record

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

    def create_program_course_enrollment(self, program_enrollment, course_status='active'):
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
            course_enrollment.is_active = course_status == "active"
            course_enrollment.save()
        return ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment,
            course_key=self.course_id,
            course_enrollment=course_enrollment,
            status=course_status,
        )

    def create_program_and_course_enrollments(self, external_user_key, user=False, course_status='active'):
        program_enrollment = self.create_program_enrollment(external_user_key, user)
        return self.create_program_course_enrollment(program_enrollment, course_status=course_status)


class ProgramEnrollmentsGetTests(EnrollmentsDataMixin, APITestCase):
    """
    Tests for GET calls to the Program Enrollments API.
    """
    view_name = 'programs_api:v1:program_enrollments'

    def create_program_enrollments(self):
        """
        Helper method for creating program enrollment records.
        """
        for i in range(2):
            user_key = f'user-{i}'
            ProgramEnrollmentFactory.create(
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                user=None,
                status='pending',
                external_user_key=user_key,
            )

        for i in range(2, 4):
            user_key = f'user-{i}'
            ProgramEnrollmentFactory.create(
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                user=UserFactory.create(username=f'student-{i}', email=f'email-{i}'),
                external_user_key=user_key,
            )

        self.addCleanup(self.destroy_program_enrollments)

    def destroy_program_enrollments(self):
        """
        Deletes program enrollments associated with this test case's program_uuid.
        """
        ProgramEnrollment.objects.filter(program_uuid=self.program_uuid).delete()

    def test_404_if_no_program_with_key(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        fake_program_uuid = UUID(self.program_uuid_tmpl.format(88))
        response = self.client.get(self.get_url(fake_program_uuid))
        assert status.HTTP_404_NOT_FOUND == response.status_code

    def test_403_if_not_staff(self):
        self.client.login(username=self.student.username, password=self.password)
        response = self.client.get(self.get_url())
        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_401_if_anonymous(self):
        response = self.client.get(self.get_url())
        assert status.HTTP_401_UNAUTHORIZED == response.status_code

    def test_200_empty_results(self):
        self.client.login(username=self.global_staff.username, password=self.password)

        response = self.client.get(self.get_url())

        assert status.HTTP_200_OK == response.status_code
        expected = {
            'next': None,
            'previous': None,
            'results': [],
        }
        assert expected == response.data

    def test_200_many_results(self):
        self.client.login(username=self.global_staff.username, password=self.password)

        self.create_program_enrollments()
        response = self.client.get(self.get_url())

        assert status.HTTP_200_OK == response.status_code
        expected = {
            'next': None,
            'previous': None,
            'results': [
                {
                    'student_key': 'user-0', 'status': 'pending', 'account_exists': False,
                    'curriculum_uuid': str(self.curriculum_uuid), 'username': "", 'email': ""
                },
                {
                    'student_key': 'user-1', 'status': 'pending', 'account_exists': False,
                    'curriculum_uuid': str(self.curriculum_uuid), 'username': "", 'email': ""
                },
                {
                    'student_key': 'user-2', 'status': 'enrolled', 'account_exists': True,
                    'curriculum_uuid': str(self.curriculum_uuid), 'username': "student-2", 'email': "email-2"
                },
                {
                    'student_key': 'user-3', 'status': 'enrolled', 'account_exists': True,
                    'curriculum_uuid': str(self.curriculum_uuid), 'username': "student-3", 'email': "email-3"
                },
            ],
        }
        assert expected == response.data

    def test_200_many_pages(self):
        self.client.login(username=self.global_staff.username, password=self.password)

        self.create_program_enrollments()
        url = self.get_url() + '?page_size=2'
        response = self.client.get(url)

        assert status.HTTP_200_OK == response.status_code
        expected_results = [
            {
                'student_key': 'user-0', 'status': 'pending', 'account_exists': False,
                'curriculum_uuid': str(self.curriculum_uuid), 'username': "", 'email': ""
            },
            {
                'student_key': 'user-1', 'status': 'pending', 'account_exists': False,
                'curriculum_uuid': str(self.curriculum_uuid), 'username': "", 'email': ""
            },
        ]
        assert expected_results == response.data['results']
        # there's going to be a 'cursor' query param, but we have no way of knowing it's value
        assert response.data['next'] is not None
        assert self.get_url() in response.data['next']
        assert '?cursor=' in response.data['next']
        assert response.data['previous'] is None

        next_response = self.client.get(response.data['next'])
        assert status.HTTP_200_OK == next_response.status_code
        next_expected_results = [
            {
                'student_key': 'user-2', 'status': 'enrolled', 'account_exists': True,
                'curriculum_uuid': str(self.curriculum_uuid), 'username': "student-2", 'email': "email-2"
            },
            {
                'student_key': 'user-3', 'status': 'enrolled', 'account_exists': True,
                'curriculum_uuid': str(self.curriculum_uuid), 'username': "student-3", 'email': "email-3"
            },
        ]
        assert next_expected_results == next_response.data['results']
        assert next_response.data['next'] is None
        # there's going to be a 'cursor' query param, but we have no way of knowing it's value
        assert next_response.data['previous'] is not None
        assert self.get_url() in next_response.data['previous']
        assert '?cursor=' in next_response.data['previous']


@ddt.ddt
class ProgramEnrollmentsWriteMixin(EnrollmentsDataMixin):
    """ Mixin class that defines common tests for program enrollment write endpoints """
    add_uuid = False

    view_name = 'programs_api:v1:program_enrollments'

    def student_enrollment(self, enrollment_status, external_user_key=None, prepare_student=False):
        """ Convenience method to create a student enrollment record """
        enrollment = {
            REQUEST_STUDENT_KEY: external_user_key or str(uuid4().hex[0:10]),
            'status': enrollment_status,
        }
        if self.add_uuid:
            enrollment['curriculum_uuid'] = str(uuid4())
        if prepare_student:
            self.prepare_student(enrollment[REQUEST_STUDENT_KEY])
        return enrollment

    def prepare_student(self, key):
        pass

    def test_unauthenticated(self):
        self.client.logout()
        request_data = [self.student_enrollment('enrolled')]
        response = self.request(self.get_url(), json.dumps(request_data), content_type='application/json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_enrollment_payload_limit(self):
        request_data = [self.student_enrollment('enrolled') for _ in range(MAX_ENROLLMENT_RECORDS + 1)]
        response = self.request(self.get_url(), json.dumps(request_data), content_type='application/json')
        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    def test_duplicate_enrollment(self):
        request_data = [
            self.student_enrollment('enrolled', '001'),
            self.student_enrollment('enrolled', '001'),
        ]

        response = self.request(self.get_url(), json.dumps(request_data), content_type='application/json')

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data == {'001': 'duplicated'}

    def test_unprocessable_enrollment(self):
        response = self.request(
            self.get_url(),
            json.dumps([{'status': 'enrolled'}]),
            content_type='application/json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_program_unauthorized(self):
        student = UserFactory.create(password='password')
        self.client.login(username=student.username, password='password')

        request_data = [self.student_enrollment('enrolled')]
        response = self.request(self.get_url(), json.dumps(request_data), content_type='application/json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_program_not_found(self):
        post_data = [self.student_enrollment('enrolled')]
        nonexistant_uuid = uuid4()
        response = self.request(
            self.get_url(program_uuid=nonexistant_uuid),
            json.dumps(post_data),
            content_type='application/json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @ddt.data(
        [{'status': 'pending'}],
        [{'status': 'not-a-status'}],
        [{'status': 'pending'}, {'status': 'pending'}],
    )
    def test_no_student_key(self, bad_records):
        url = self.get_url()
        enrollments = [self.student_enrollment('enrolled', '001', True)]
        enrollments.extend(bad_records)

        response = self.request(url, json.dumps(enrollments), content_type='application/json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_extra_field(self):
        self.student_enrollment('pending', 'learner-01', prepare_student=True)
        enrollment = self.student_enrollment('enrolled', 'learner-01')
        enrollment['favorite_pokemon'] = 'bulbasaur'
        enrollments = [enrollment]
        with _patch_get_users:
            url = self.get_url()
            response = self.request(url, json.dumps(enrollments), content_type='application/json')
        assert 200 == response.status_code
        self.assertDictEqual(
            response.data,
            {'learner-01': 'enrolled'}
        )


@ddt.ddt
class ProgramEnrollmentsPostTests(ProgramEnrollmentsWriteMixin, APITestCase):
    """
    Tests for the ProgramEnrollment view POST method.
    """
    add_uuid = True

    def setUp(self):
        super().setUp()
        self.request = self.client.post
        self.client.login(username=self.global_staff.username, password='password')

    def tearDown(self):
        super().tearDown()
        ProgramEnrollment.objects.all().delete()

    def test_successful_program_enrollments_no_existing_user(self):
        statuses = ['pending', 'enrolled', 'pending', 'ended']
        external_user_keys = ['abc1', 'efg2', 'hij3', 'klm4']
        curriculum_uuids = [self.curriculum_uuid, self.curriculum_uuid, uuid4(), uuid4()]
        post_data = [
            {
                REQUEST_STUDENT_KEY: e,
                'status': s,
                'curriculum_uuid': str(c)
            }
            for e, s, c in zip(external_user_keys, statuses, curriculum_uuids)
        ]

        url = self.get_url(program_uuid=0)
        with _patch_get_users:
            response = self.client.post(url, json.dumps(post_data), content_type='application/json')

        assert response.status_code == 200

        for i in range(4):
            enrollment = ProgramEnrollment.objects.get(external_user_key=external_user_keys[i])

            assert enrollment.external_user_key == external_user_keys[i]
            assert enrollment.program_uuid == self.program_uuid
            assert enrollment.status == statuses[i]
            assert enrollment.curriculum_uuid == curriculum_uuids[i]
            assert enrollment.user is None

    def test_successful_program_enrollments_existing_user(self):
        post_data = [
            {
                'status': 'enrolled',
                REQUEST_STUDENT_KEY: 'abc1',
                'curriculum_uuid': str(self.curriculum_uuid)
            }
        ]
        user = UserFactory.create(username='test_user', email='test@example.com', password='password')
        url = self.get_url()
        with mock.patch(
                _get_users_patch_path,
                autospec=True,
                return_value={'abc1': user},
        ):
            response = self.client.post(
                url, json.dumps(post_data), content_type='application/json'
            )
        assert response.status_code == 200
        enrollment = ProgramEnrollment.objects.get(external_user_key='abc1')
        assert enrollment.external_user_key == 'abc1'
        assert enrollment.program_uuid == self.program_uuid
        assert enrollment.status == 'enrolled'
        assert enrollment.curriculum_uuid == self.curriculum_uuid
        assert enrollment.user == user

    def test_program_enrollments_no_idp(self):
        post_data = [
            {
                'status': 'enrolled',
                REQUEST_STUDENT_KEY: f'abc{i}',
                'curriculum_uuid': str(self.curriculum_uuid)
            } for i in range(3)
        ]

        url = self.get_url()
        with mock.patch(
                _get_users_patch_path,
                autospec=True,
                side_effect=ProviderDoesNotExistException(None),
        ):
            response = self.client.post(url, json.dumps(post_data), content_type='application/json')

        assert response.status_code == 200

        for i in range(3):
            enrollment = ProgramEnrollment.objects.get(external_user_key=f'abc{i}')

            assert enrollment.program_uuid == self.program_uuid
            assert enrollment.status == 'enrolled'
            assert enrollment.curriculum_uuid == self.curriculum_uuid
            assert enrollment.user is None


@ddt.ddt
class ProgramEnrollmentsPatchTests(ProgramEnrollmentsWriteMixin, APITestCase):
    """
    Tests for the ProgramEnrollment view PATCH method.
    """
    add_uuid = False

    def setUp(self):
        super().setUp()
        self.request = self.client.patch
        self.client.login(username=self.global_staff.username, password=self.password)

    def prepare_student(self, key):
        ProgramEnrollment.objects.create(
            program_uuid=self.program_uuid,
            curriculum_uuid=self.curriculum_uuid,
            user=None,
            status='pending',
            external_user_key=key,
        )

    def test_successfully_patched_program_enrollment(self):
        enrollments = {}
        for i in range(4):
            user_key = f'user-{i}'
            instance = ProgramEnrollment.objects.create(
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                user=None,
                status='pending',
                external_user_key=user_key,
            )
            enrollments[user_key] = instance

        post_data = [
            {REQUEST_STUDENT_KEY: 'user-1', 'status': 'canceled'},
            {REQUEST_STUDENT_KEY: 'user-2', 'status': 'suspended'},
            {REQUEST_STUDENT_KEY: 'user-3', 'status': 'enrolled'},
        ]

        url = self.get_url()
        response = self.client.patch(url, json.dumps(post_data), content_type='application/json')

        for enrollment in enrollments.values():
            enrollment.refresh_from_db()

        expected_statuses = {
            'user-0': 'pending',
            'user-1': 'canceled',
            'user-2': 'suspended',
            'user-3': 'enrolled',
        }
        for user_key, enrollment in enrollments.items():
            assert expected_statuses[user_key] == enrollment.status

        expected_response = {
            'user-1': 'canceled',
            'user-2': 'suspended',
            'user-3': 'enrolled',
        }
        assert status.HTTP_200_OK == response.status_code
        assert expected_response == response.data

    def test_duplicate_enrollment_record_changed(self):
        enrollments = {}
        for i in range(4):
            user_key = f'user-{i}'
            instance = ProgramEnrollment.objects.create(
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                user=None,
                status='pending',
                external_user_key=user_key,
            )
            enrollments[user_key] = instance

        patch_data = [
            self.student_enrollment('enrolled', 'user-1'),
            self.student_enrollment('enrolled', 'user-2'),
            self.student_enrollment('enrolled', 'user-1'),
        ]

        url = self.get_url()
        response = self.client.patch(url, json.dumps(patch_data), content_type='application/json')

        for enrollment in enrollments.values():
            enrollment.refresh_from_db()

        expected_statuses = {
            'user-0': 'pending',
            'user-1': 'pending',
            'user-2': 'enrolled',
            'user-3': 'pending',
        }
        for user_key, enrollment in enrollments.items():
            assert expected_statuses[user_key] == enrollment.status

        assert response.status_code == status.HTTP_207_MULTI_STATUS
        assert response.data == {'user-1': 'duplicated', 'user-2': 'enrolled'}

    def test_partially_valid_enrollment_record_changed(self):
        enrollments = {}
        for i in range(4):
            user_key = f'user-{i}'
            instance = ProgramEnrollment.objects.create(
                program_uuid=self.program_uuid,
                curriculum_uuid=self.curriculum_uuid,
                user=None,
                status='pending',
                external_user_key=user_key,
            )
            enrollments[user_key] = instance

        patch_data = [
            self.student_enrollment('new', 'user-1'),
            self.student_enrollment('canceled', 'user-3'),
            self.student_enrollment('enrolled', 'user-who-is-not-in-program'),
        ]

        url = self.get_url()
        response = self.client.patch(url, json.dumps(patch_data), content_type='application/json')

        for enrollment in enrollments.values():
            enrollment.refresh_from_db()

        expected_statuses = {
            'user-0': 'pending',
            'user-1': 'pending',
            'user-2': 'pending',
            'user-3': 'canceled',
        }
        for user_key, enrollment in enrollments.items():
            assert expected_statuses[user_key] == enrollment.status

        assert response.status_code == status.HTTP_207_MULTI_STATUS
        assert response.data ==\
               {'user-1': 'invalid-status', 'user-3': 'canceled', 'user-who-is-not-in-program': 'not-in-program'}


@ddt.ddt
class ProgramEnrollmentsPutTests(ProgramEnrollmentsWriteMixin, APITestCase):
    """
    Tests for the ProgramEnrollment view PATCH method.
    """
    add_uuid = True

    def setUp(self):
        super().setUp()
        self.request = self.client.put
        self.client.login(username=self.global_staff.username, password='password')

    def prepare_student(self, key):
        ProgramEnrollment.objects.create(
            program_uuid=self.program_uuid,
            curriculum_uuid=self.curriculum_uuid,
            user=None,
            status='pending',
            external_user_key=REQUEST_STUDENT_KEY,
        )

    @ddt.data(True, False)
    def test_all_create_or_modify(self, create_users):
        request_data = [
            self.student_enrollment(ProgramStatuses.ENROLLED)
            for _ in range(5)
        ]
        if create_users:
            for enrollment in request_data:
                ProgramEnrollmentFactory(
                    program_uuid=self.program_uuid,
                    status=ProgramStatuses.PENDING,
                    external_user_key=enrollment[REQUEST_STUDENT_KEY],
                )

        url = self.get_url()
        with _patch_get_users:
            response = self.client.put(
                url, json.dumps(request_data), content_type='application/json'
            )
        assert 200 == response.status_code
        assert 5 == len(response.data)
        for response_status in response.data.values():
            assert response_status == ProgramStatuses.ENROLLED

    def test_half_create_modify(self):
        request_data = [
            self.student_enrollment(ProgramStatuses.ENROLLED, 'learner-01'),
            self.student_enrollment(ProgramStatuses.ENROLLED, 'learner-02'),
            self.student_enrollment(ProgramStatuses.ENROLLED, 'learner-03'),
            self.student_enrollment(ProgramStatuses.ENROLLED, 'learner-04'),
        ]
        ProgramEnrollmentFactory(
            program_uuid=self.program_uuid,
            status=ProgramStatuses.PENDING,
            external_user_key='learner-03',
        )
        ProgramEnrollmentFactory(
            program_uuid=self.program_uuid,
            status=ProgramStatuses.PENDING,
            external_user_key='learner-04',
        )

        url = self.get_url()
        with _patch_get_users:
            response = self.client.put(
                url, json.dumps(request_data), content_type='application/json'
            )
        assert 200 == response.status_code
        assert 4 == len(response.data)
        for response_status in response.data.values():
            assert response_status == ProgramStatuses.ENROLLED


@ddt.ddt
class ProgramCourseEnrollmentsMixin(EnrollmentsDataMixin):
    """
    A base for tests for course enrollment.
    Children should override self.request()
    """
    view_name = 'programs_api:v1:program_course_enrollments'
    write_enrollments_function = ''

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_cache_isolation()

    @classmethod
    def tearDownClass(cls):
        cls.end_cache_isolation()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.default_url = self.get_url(course_id=self.course_id)
        self.log_in_staff()

    def test_401_not_logged_in(self):
        self.client.logout()
        request_data = [self.learner_enrollment("learner-1")]
        response = self.request(self.default_url, request_data)
        assert 401 == response.status_code

    def test_403_forbidden(self):
        self.client.logout()
        self.log_in_non_staff()
        request_data = [self.learner_enrollment("learner-1")]
        response = self.request(self.default_url, request_data)
        assert 403 == response.status_code

    def test_413_payload_too_large(self):
        request_data = [self.learner_enrollment(str(i)) for i in range(30)]
        response = self.request(self.default_url, request_data)
        assert 413 == response.status_code

    def test_404_not_found(self):
        nonexistant_course_key = CourseKey.from_string("course-v1:fake+fake+fake")
        paths = [
            self.get_url(uuid4(), self.course_id),  # program not found
            self.get_url(course_id=nonexistant_course_key),  # course not found
            self.get_url(course_id=self.course_not_in_program_id),  # course not in program
        ]
        request_data = [self.learner_enrollment("learner-1")]
        for path_404 in paths:
            response = self.request(path_404, request_data)
            assert 404 == response.status_code

    def test_404_no_curriculum(self):
        with mock.patch.dict(self.program, curricula=[]):
            self.set_program_in_catalog_cache(self.program_uuid, self.program)
            request_data = [self.learner_enrollment("learner-1")]
            response = self.request(self.default_url, request_data)
            assert 404 == response.status_code

    @ddt.data(
        [{'status': 'active'}],
        [{'student_key': '000'}],
        ["this isn't even a dict!"],
        [{'student_key': '000', 'status': 'active'}, "bad_data"],
        "not a list",
    )
    def test_422_unprocessable_entity_bad_data(self, request_data):
        response = self.request(self.default_url, request_data)
        assert response.status_code == 400

    @ddt.data(
        [{'status': 'pending'}],
        [{'status': 'not-a-status'}],
        [{'status': 'pending'}, {'status': 'pending'}],
    )
    def test_no_student_key(self, bad_records):
        request_data = [self.learner_enrollment('learner-1')]
        request_data.extend(bad_records)
        response = self.request(self.default_url, request_data)
        assert response.status_code == 400

    def test_extra_field(self):
        enrollment = self.learner_enrollment('learner-1', 'inactive')
        enrollment['favorite_author'] = 'Hemingway'
        request_data = [enrollment]
        mock_write_response = {'learner-1': 'inactive'}
        with mock.patch(
            _VIEW_PATCH_FORMAT.format('write_program_course_enrollments'),
            autospec=True,
            return_value=mock_write_response,
        ):
            response = self.request(self.default_url, request_data)
            assert 200 == response.status_code
            self.assertDictEqual(
                mock_write_response,
                response.data,
            )

    def test_207_multistatus(self):
        """
        If errors occur but some requests succeed return a 207
        """
        request_data = [self.learner_enrollment("learner-1"), self.learner_enrollment("learner-2")]
        mock_write_response = {
            'learner-1': CourseStatuses.ACTIVE,
            'learner-2': CourseStatuses.NOT_IN_PROGRAM,
        }
        with mock.patch(
            _VIEW_PATCH_FORMAT.format('write_program_course_enrollments'),
            autospec=True,
            return_value=mock_write_response,
        ):
            response = self.request(self.default_url, request_data)
            assert 207 == response.status_code
            self.assertDictEqual(
                {'learner-1': CourseStatuses.ACTIVE, 'learner-2': CourseStatuses.NOT_IN_PROGRAM},
                response.data
            )


class ProgramCourseEnrollmentsGetTests(EnrollmentsDataMixin, APITestCase):
    """
    Tests for GET calls to the Program Course Enrollments API.
    """
    view_name = 'programs_api:v1:program_course_enrollments'

    def create_course_enrollments(self):
        """ Helper method for creating ProgramCourseEnrollments. """
        program_enrollment_1 = ProgramEnrollmentFactory.create(
            program_uuid=self.program_uuid, curriculum_uuid=self.curriculum_uuid, external_user_key='user-0',
        )
        program_enrollment_2 = ProgramEnrollmentFactory.create(
            program_uuid=self.program_uuid,
            curriculum_uuid=self.other_curriculum_uuid,
            external_user_key='user-0',
            user=None
        )

        ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment_1,
            course_key=self.course_id,
            status='active',
        )
        CourseStaffRole(self.course_id).add_users(program_enrollment_1.user)

        program_course_enrollment_2 = ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment_2,
            course_key=self.course_id,
            status='inactive',
        )
        CourseAccessRoleAssignmentFactory.create(
            enrollment=program_course_enrollment_2
        )

        self.addCleanup(self.destroy_course_enrollments)

    def destroy_course_enrollments(self):
        """ Helper method for tearing down ProgramCourseEnrollments. """
        ProgramCourseEnrollment.objects.filter(
            program_enrollment__program_uuid=self.program_uuid,
            course_key=self.course_id
        ).delete()

    def test_404_if_no_program_with_key(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        fake_program_uuid = UUID(self.program_uuid_tmpl.format(88))
        response = self.client.get(self.get_url(fake_program_uuid, self.course_id))
        assert status.HTTP_404_NOT_FOUND == response.status_code

    def test_404_if_course_does_not_exist(self):
        other_course_key = CourseKey.from_string('course-v1:edX+ToyX+Other_Course')
        self.client.login(username=self.global_staff.username, password=self.password)
        response = self.client.get(self.get_url(course_id=other_course_key))
        assert status.HTTP_404_NOT_FOUND == response.status_code

    def test_403_if_not_staff(self):
        self.client.login(username=self.student.username, password=self.password)
        response = self.client.get(self.get_url(course_id=self.course_id))
        assert status.HTTP_403_FORBIDDEN == response.status_code

    def test_401_if_anonymous(self):
        response = self.client.get(self.get_url(course_id=self.course_id))
        assert status.HTTP_401_UNAUTHORIZED == response.status_code

    def test_200_empty_results(self):
        self.client.login(username=self.global_staff.username, password=self.password)

        response = self.client.get(self.get_url(course_id=self.course_id))

        assert status.HTTP_200_OK == response.status_code
        expected = {
            'next': None,
            'previous': None,
            'results': [],
        }
        assert expected == response.data

    def test_200_many_results(self):
        self.client.login(username=self.global_staff.username, password=self.password)

        self.create_course_enrollments()
        response = self.client.get(self.get_url(course_id=self.course_id))

        assert status.HTTP_200_OK == response.status_code
        expected = {
            'next': None,
            'previous': None,
            'results': [
                {
                    'student_key': 'user-0', 'status': 'active', 'account_exists': True,
                    'curriculum_uuid': str(self.curriculum_uuid), 'course_staff': True
                },
                {
                    'student_key': 'user-0', 'status': 'inactive', 'account_exists': False,
                    'curriculum_uuid': str(self.other_curriculum_uuid), 'course_staff': True
                },
            ],
        }
        assert expected == response.data

    def test_200_many_pages(self):
        self.client.login(username=self.global_staff.username, password=self.password)

        self.create_course_enrollments()
        url = self.get_url(course_id=self.course_id) + '?page_size=1'
        response = self.client.get(url)

        assert status.HTTP_200_OK == response.status_code
        expected_results = [
            {
                'student_key': 'user-0', 'status': 'active', 'account_exists': True,
                'curriculum_uuid': str(self.curriculum_uuid), 'course_staff': True
            },
        ]
        assert expected_results == response.data['results']
        # there's going to be a 'cursor' query param, but we have no way of knowing it's value
        assert response.data['next'] is not None
        assert self.get_url(course_id=self.course_id) in response.data['next']
        assert '?cursor=' in response.data['next']
        assert response.data['previous'] is None

        next_response = self.client.get(response.data['next'])
        assert status.HTTP_200_OK == next_response.status_code
        next_expected_results = [
            {
                'student_key': 'user-0', 'status': 'inactive', 'account_exists': False,
                'curriculum_uuid': str(self.other_curriculum_uuid), 'course_staff': True
            },
        ]
        assert next_expected_results == next_response.data['results']

        # there's going to be a 'cursor' query param, but we have no way of knowing it's value
        assert next_response.data['previous'] is not None
        assert self.get_url(course_id=self.course_id) in next_response.data['previous']
        assert '?cursor=' in next_response.data['previous']


class ProgramCourseEnrollmentsPostTests(ProgramCourseEnrollmentsMixin, APITestCase):
    """ Tests for course enrollment POST """

    def request(self, path, data, **kwargs):
        return self.client.post(path, data, format='json', **kwargs)

    def test_create_enrollments(self):
        mock_write_response = {
            "learner-1": "active",
            "learner-2": "inactive",
            "learner-3": "active",
            "learner-4": "inactive",
        }
        post_data = [
            self.learner_enrollment("learner-1", "active"),
            self.learner_enrollment("learner-2", "inactive"),
            self.learner_enrollment("learner-3", "active", True),
            self.learner_enrollment("learner-4", "inactive", False),
        ]

        with mock.patch(
            _VIEW_PATCH_FORMAT.format('write_program_course_enrollments'),
            autospec=True,
            return_value=mock_write_response,
        ) as mock_write:
            response = self.request(self.default_url, post_data)
            assert 200 == response.status_code
            self.assertDictEqual(
                mock_write_response,
                response.data,
            )
        mock_write.assert_called_once_with(
            str(self.program_uuid),
            self.course_id,
            [
                OrderedDict([('external_user_key', 'learner-1'), ('status', 'active'), ('course_staff', None)]),
                OrderedDict([('external_user_key', 'learner-2'), ('status', 'inactive'), ('course_staff', None)]),
                OrderedDict([('external_user_key', 'learner-3'), ('status', 'active'), ('course_staff', True)]),
                OrderedDict([('external_user_key', 'learner-4'), ('status', 'inactive'), ('course_staff', False)]),
            ],
            create=True,
            update=False,
        )

    def test_no_successful_enrollments(self):
        self.create_program_and_course_enrollments('learner-1')
        post_data = [self.learner_enrollment("learner-1")]
        mock_write_response = {'learner-1': CourseStatuses.CONFLICT}
        with mock.patch(
            _VIEW_PATCH_FORMAT.format('write_program_course_enrollments'),
            autospec=True,
            return_value=mock_write_response,
        ):
            response = self.request(self.default_url, post_data)
            assert 422 == response.status_code
            self.assertDictEqual({'learner-1': CourseStatuses.CONFLICT}, response.data)


class ProgramCourseEnrollmentsModifyMixin(ProgramCourseEnrollmentsMixin):
    """
    Base class for both the PATCH and PUT endpoints for Course Enrollment API
    """
    def test_update_enrollment(self):
        request_data = [self.learner_enrollment('learner-1', 'active')]
        mock_write_response = {'learner-1': 'active'}
        with mock.patch(
            _VIEW_PATCH_FORMAT.format('write_program_course_enrollments'),
            autospec=True,
            return_value=mock_write_response,
        ) as mock_write:
            response = self.request(self.default_url, request_data)
            assert 200 == response.status_code
            self.assertDictEqual(
                mock_write_response,
                response.data,
            )
        mock_write.assert_called_once_with(
            str(self.program_uuid),
            self.course_id,
            [
                OrderedDict([('external_user_key', 'learner-1'), ('status', 'active'), ('course_staff', None)])
            ],
            create=self.create,
            update=self.update,
        )


class ProgramCourseEnrollmentsPatchTests(ProgramCourseEnrollmentsModifyMixin, APITestCase):
    """ Test PATCH ProgramCourseEnrollments """
    update = True
    create = False

    def request(self, path, data, **kwargs):
        return self.client.patch(path, data, format='json', **kwargs)


class ProgramCourseEnrollmentsPutTests(ProgramCourseEnrollmentsModifyMixin, APITestCase):
    """ Test PUT ProgramCourseEnrollments """
    update = True
    create = True

    def request(self, path, data, **kwargs):
        return self.client.put(path, data, format='json', **kwargs)


@ddt.ddt
class MultiprogramEnrollmentsTest(EnrollmentsDataMixin, APITestCase):
    """ Tests for the Multiple Program with same course scenario """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.another_curriculum_uuid = UUID('bbbbbbbb-8888-9999-7777-666666666666')
        cls.another_curriculum = CurriculumFactory(
            uuid=cls.another_curriculum_uuid,
            courses=[cls.course]
        )
        cls.another_program_uuid = UUID(cls.program_uuid_tmpl.format(99))
        cls.another_program = ProgramFactory(
            uuid=cls.another_program_uuid,
            authoring_organizations=[cls.catalog_org],
            curricula=[cls.another_curriculum]
        )
        cls.external_user_key = 'aabbcc'
        cls.user = UserFactory.create(username='multiprogram_user')

    def setUp(self):
        super().setUp()
        self.set_program_in_catalog_cache(self.another_program_uuid, self.another_program)
        self.client.login(username=self.global_staff.username, password=self.password)

    def get_program_url(self, program_uuid):
        return reverse('programs_api:v1:program_enrollments', kwargs={
            'program_uuid': program_uuid
        })

    def get_program_course_url(self, program_uuid, course_id):
        return reverse('programs_api:v1:program_course_enrollments', kwargs={
            'program_uuid': program_uuid,
            'course_id': course_id
        })

    def write_program_enrollment(
        self,
        method,
        program_uuid,
        curriculum_uuid,
        enrollment_status,
        existing_user
    ):
        """ Create or update the program enrollment through API """
        write_data = [{
            'status': enrollment_status,
            REQUEST_STUDENT_KEY: self.external_user_key,
            'curriculum_uuid': str(curriculum_uuid)
        }]
        url = self.get_program_url(program_uuid=program_uuid)
        mock_user = defaultdict(lambda: None)
        if existing_user:
            mock_user = {self.external_user_key: self.user}
        with mock.patch(
            _get_users_patch_path,
            autospec=True,
            return_value=mock_user,
        ):
            response = getattr(self.client, method)(
                url,
                json.dumps(write_data),
                content_type='application/json'
            )
            return response

    def write_program_course_enrollment(
        self,
        method,
        program_uuid,
        course_id,
        enrollment_status
    ):
        """ Create or update the program course enrollment through API """
        course_post_data = [{
            'student_key': self.external_user_key,
            'status': enrollment_status
        }]
        course_url = self.get_program_course_url(program_uuid, course_id)
        response = getattr(self.client, method)(
            course_url,
            json.dumps(course_post_data),
            content_type='application/json'
        )
        return response

    def link_user_social_auth(self):
        """ Create the UserSocialAuth record to trigger the linkage django signal """
        SAMLProviderConfigFactory(
            organization=self.lms_org,
            slug=self.organization_key
        )
        UserSocialAuth.objects.create(
            user=self.user,
            uid=f'{self.organization_key}:{self.external_user_key}',
            provider=self.organization_key
        )

    @ddt.data(True, False)
    def test_enrollment_in_same_course_multi_program(self, existing_user):
        response = self.write_program_enrollment(
            'post', self.program_uuid, self.curriculum_uuid, 'enrolled', existing_user
        )
        assert response.status_code == status.HTTP_200_OK
        response = self.write_program_course_enrollment(
            'post', self.program_uuid, self.course_id, 'active'
        )
        assert response.status_code == status.HTTP_200_OK

        response = self.write_program_enrollment(
            'put', self.program_uuid, self.curriculum_uuid, 'canceled', existing_user
        )
        assert response.status_code == status.HTTP_200_OK
        response = self.write_program_course_enrollment(
            'put', self.program_uuid, self.course_id, 'inactive'
        )
        assert response.status_code == status.HTTP_200_OK

        response = self.write_program_enrollment(
            'post', self.another_program_uuid, self.another_curriculum_uuid, 'enrolled', existing_user
        )
        assert response.status_code == status.HTTP_200_OK
        response = self.write_program_course_enrollment(
            'post', self.another_program_uuid, self.course_id, 'active')
        assert response.status_code == status.HTTP_200_OK

        if not existing_user:
            self.link_user_social_auth()
            program_course_enrollment = ProgramCourseEnrollment.objects.get(
                program_enrollment__external_user_key=self.external_user_key,
                program_enrollment__program_uuid=self.another_program_uuid
            )
            assert program_course_enrollment.program_enrollment.user is not None

    @ddt.data(True, False)
    @mock.patch('lms.djangoapps.program_enrollments.api.writing.logger')
    def test_enrollment_in_same_course_both_program_enrollments_active(self, existing_user, mock_log):
        response = self.write_program_enrollment(
            'post', self.program_uuid, self.curriculum_uuid, 'enrolled', existing_user
        )
        assert response.status_code == status.HTTP_200_OK
        response = self.write_program_course_enrollment(
            'post', self.program_uuid, self.course_id, 'active'
        )
        assert response.status_code == status.HTTP_200_OK

        response = self.write_program_enrollment(
            'post', self.another_program_uuid, self.another_curriculum_uuid, 'enrolled', existing_user
        )
        assert response.status_code == status.HTTP_200_OK
        response = self.write_program_course_enrollment(
            'post', self.another_program_uuid, self.course_id, 'active'
        )
        assert response.status_code == 422
        mock_log.error.assert_called_with(
            'Detected conflicting active ProgramCourseEnrollment. This is happening on'
            ' The program_uuid [{}] with course_key [{}] for external_user_key [{}]'.format(
                self.another_program_uuid,
                self.course_id,
                self.external_user_key
            )
        )
        expected_results = {self.external_user_key: CourseStatuses.CONFLICT}
        self.assertDictEqual(expected_results, response.data)


class ProgramCourseGradesGetTests(EnrollmentsDataMixin, APITestCase):
    """
    Tests for GET calls to the Program Course Grades API.
    """
    view_name = 'programs_api:v1:program_course_grades'

    def test_401_if_unauthenticated(self):
        url = self.get_url(course_id=self.course_id)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_403_if_not_staff(self):
        self.log_in_non_staff()
        url = self.get_url(course_id=self.course_id)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_404_not_found(self):
        fake_program_uuid = UUID(self.program_uuid_tmpl.format(99))
        self.log_in_staff()
        url = self.get_url(program_uuid=fake_program_uuid, course_id=self.course_id)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_204_no_grades_to_return(self):
        self.log_in_staff()
        url = self.get_url(course_id=self.course_id)
        with self.patch_grades_with({}):
            response = self.client.get(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.data['results'] == []

    def test_200_grades_with_no_exceptions(self):
        other_student = UserFactory.create(username='other_student')
        self.create_program_and_course_enrollments('student-key', user=self.student)
        self.create_program_and_course_enrollments('other-student-key', user=other_student)
        mock_grades_by_user = {
            self.student: (
                self.mock_grade(),
                None
            ),
            other_student: (
                self.mock_grade(percent=40.0, passed=False, letter_grade='F'),
                None
            ),
        }
        self.log_in_staff()
        url = self.get_url(course_id=self.course_id)
        with self.patch_grades_with(mock_grades_by_user):
            response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        expected_results = [
            {
                'student_key': 'student-key',
                'passed': True,
                'percent': 75.0,
                'letter_grade': 'B',
            },
            {
                'student_key': 'other-student-key',
                'passed': False,
                'percent': 40.0,
                'letter_grade': 'F',
            },
        ]
        assert response.data['results'] == expected_results

    def test_207_grades_with_some_exceptions(self):
        other_student = UserFactory.create(username='other_student')
        self.create_program_and_course_enrollments('student-key', user=self.student)
        self.create_program_and_course_enrollments('other-student-key', user=other_student)
        mock_grades_by_user = {
            self.student: (None, Exception('Bad Data')),
            other_student: (
                self.mock_grade(percent=40.0, passed=False, letter_grade='F'),
                None,
            ),
        }
        self.log_in_staff()
        url = self.get_url(course_id=self.course_id)
        with self.patch_grades_with(mock_grades_by_user):
            response = self.client.get(url)
        assert response.status_code == status.HTTP_207_MULTI_STATUS
        expected_results = [
            {
                'student_key': 'student-key',
                'error': 'Bad Data',
            },
            {
                'student_key': 'other-student-key',
                'passed': False,
                'percent': 40.0,
                'letter_grade': 'F',
            },
        ]
        assert response.data['results'] == expected_results

    def test_422_grades_with_only_exceptions(self):
        other_student = UserFactory.create(username='other_student')
        self.create_program_and_course_enrollments('student-key', user=self.student)
        self.create_program_and_course_enrollments('other-student-key', user=other_student)
        mock_grades_by_user = {
            self.student: (None, Exception('Bad Data')),
            other_student: (None, Exception('Timeout')),
        }
        self.log_in_staff()
        url = self.get_url(course_id=self.course_id)
        with self.patch_grades_with(mock_grades_by_user):
            response = self.client.get(url)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        expected_results = [
            {
                'student_key': 'student-key',
                'error': 'Bad Data',
            },
            {
                'student_key': 'other-student-key',
                'error': 'Timeout',
            },
        ]
        assert response.data['results'] == expected_results

    @staticmethod
    def patch_grades_with(grades_by_user):
        """
        Create a patcher the CourseGradeFactory to use the `grades_by_user`
        to determine the grade for each user.

        Arguments:
            grades_by_user: dict[User: (CourseGrade, Exception)]
        """
        def patched_iter(self, users, course_key):  # pylint: disable=unused-argument
            return [
                (user, grades_by_user[user][0], grades_by_user[user][1])
                for user in users
            ]
        return mock.patch.object(CourseGradeFactory, 'iter', new=patched_iter)

    @staticmethod
    def mock_grade(percent=75.0, passed=True, letter_grade='B'):
        return mock.MagicMock(percent=percent, passed=passed, letter_grade=letter_grade)


@ddt.ddt
class UserProgramReadOnlyAccessGetTests(EnrollmentsDataMixin, APITestCase):
    """
    Tests for the UserProgramReadonlyAccess view class
    """
    view_name = 'programs_api:v1:user_program_readonly_access'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.mock_program_data = [
            {'uuid': cls.program_uuid_tmpl.format(11), 'marketing_slug': 'garbage-program', 'type': 'masters'},
            {'uuid': cls.program_uuid_tmpl.format(22), 'marketing_slug': 'garbage-study', 'type': 'micromaster'},
            {'uuid': cls.program_uuid_tmpl.format(33), 'marketing_slug': 'garbage-life', 'type': 'masters'},
        ]

        cls.course_staff = InstructorFactory.create(password=cls.password, course_key=cls.course_id)
        cls.date = timezone.make_aware(datetime(2013, 1, 22))
        CourseEnrollmentFactory(
            course_id=cls.course_id,
            user=cls.course_staff,
            created=cls.date,
        )

    def test_401_if_anonymous(self):
        response = self.client.get(reverse(self.view_name))
        assert status.HTTP_401_UNAUTHORIZED == response.status_code

    @ddt.data(
        ('masters', 2),
        ('micromaster', 1)
    )
    @ddt.unpack
    def test_global_staff(self, program_type, expected_data_size):
        self.client.login(username=self.global_staff.username, password=self.password)
        mock_return_value = [program for program in self.mock_program_data if program['type'] == program_type]

        with mock.patch(
            _VIEW_PATCH_FORMAT.format('get_programs_by_type'),
            autospec=True,
            return_value=mock_return_value
        ) as mock_get_programs_by_type:
            response = self.client.get(reverse(self.view_name) + '?type=' + program_type)

        assert status.HTTP_200_OK == response.status_code
        assert len(response.data) == expected_data_size
        mock_get_programs_by_type.assert_called_once_with(response.wsgi_request.site, program_type)

    def test_course_staff(self):
        self.client.login(username=self.course_staff.username, password=self.password)

        with mock.patch(
            _VIEW_PATCH_FORMAT.format('get_programs'),
            autospec=True,
            side_effect=[[self.mock_program_data[0]], []]
        ) as mock_get_programs:
            response = self.client.get(reverse(self.view_name) + '?type=masters')

        assert status.HTTP_200_OK == response.status_code
        assert len(response.data) == 1
        mock_get_programs.assert_has_calls([
            mock.call(course=self.course_id),
            mock.call(uuids=[]),
        ], any_order=True)

    def _enroll_user_into_course_as_course_staff(self, user, course_key_string):
        """
        This is a helper function to create a course run based on the course key string,
        then enroll the user to the course run as a course staff.
        """
        course_key_to_create = CourseKey.from_string(course_key_string)
        CourseOverviewFactory(id=course_key_to_create)
        CourseRunFactory.create(key=str(course_key_to_create))
        CourseEnrollmentFactory.create(course_id=course_key_to_create, user=user)
        CourseStaffRole(course_key_to_create).add_users(user)
        return course_key_to_create

    @ddt.data(
        (
            ['garbage-program'],
            ['garbage-life']
        ),
        (
            ['garbage-program', 'garbage-life'],
            ['garbage-program', 'garbage-life']
        )
    )
    @ddt.unpack
    def test_course_staff_of_multiple_courses(
        self,
        program_slugs_to_return_first,
        program_slugs_to_return_second
    ):
        def find_program_by_marketing_slug(slug, program_list):
            for program in program_list:
                if program['marketing_slug'] == slug:
                    return program
            return None

        other_course_key = self._enroll_user_into_course_as_course_staff(
            self.course_staff,
            'course-v1:edX+ToyX+Other_Course'
        )

        self.client.login(username=self.course_staff.username, password=self.password)

        programs_to_return_first = [
            find_program_by_marketing_slug(
                p_slug,
                self.mock_program_data
            ) for p_slug in program_slugs_to_return_first
        ]
        programs_to_return_second = [
            find_program_by_marketing_slug(
                p_slug,
                self.mock_program_data
            ) for p_slug in program_slugs_to_return_second
        ]

        with mock.patch(
            _VIEW_PATCH_FORMAT.format('get_programs'),
            autospec=True,
            side_effect=[[], programs_to_return_first, programs_to_return_second]
        ) as mock_get_programs:
            response = self.client.get(reverse(self.view_name) + '?type=masters')

        assert status.HTTP_200_OK == response.status_code
        assert len(response.data) == 2
        mock_get_programs.assert_has_calls([
            mock.call(course=self.course_id),
            mock.call(course=other_course_key),
        ], any_order=True)

    def test_course_staff_of_non_program_course(self):
        created_course_key = self._enroll_user_into_course_as_course_staff(
            self.student,
            'course-v1:edX+ToyX+Other_Course'
        )

        program_to_enroll = self.mock_program_data[0]
        ProgramEnrollmentFactory.create(
            program_uuid=program_to_enroll['uuid'],
            curriculum_uuid=self.curriculum_uuid,
            user=self.student,
            status='enrolled',
            external_user_key=f'user-{self.student.id}',
        )

        self.client.login(username=self.student.username, password=self.password)

        with mock.patch(
            _VIEW_PATCH_FORMAT.format('get_programs'),
            autospec=True,
            side_effect=[[], [program_to_enroll]]
        ) as mock_get_programs:
            response = self.client.get(reverse(self.view_name))

        assert status.HTTP_200_OK == response.status_code
        assert len(response.data) == 1
        mock_get_programs.assert_has_calls([
            mock.call(course=created_course_key),
            mock.call(uuids=[UUID(program_to_enroll['uuid'])]),
        ])

    @mock.patch(_VIEW_PATCH_FORMAT.format('get_programs'), autospec=True, return_value=None)
    def test_learner_200_if_no_programs_enrolled(self, mock_get_programs):
        self.client.login(username=self.student.username, password=self.password)
        response = self.client.get(reverse(self.view_name))

        assert status.HTTP_200_OK == response.status_code
        assert response.data == []
        mock_get_programs.assert_called_once_with(uuids=[])

    def test_learner_200_many_programs(self):
        for program in self.mock_program_data:
            ProgramEnrollmentFactory.create(
                program_uuid=program['uuid'],
                curriculum_uuid=self.curriculum_uuid,
                user=self.student,
                status='pending',
                external_user_key=f'user-{self.student.id}',
            )
        self.client.login(username=self.student.username, password=self.password)

        with mock.patch(
            _VIEW_PATCH_FORMAT.format('get_programs'),
            autospec=True,
            return_value=self.mock_program_data
        ) as mock_get_programs:
            response = self.client.get(reverse(self.view_name))

        assert status.HTTP_200_OK == response.status_code
        assert len(response.data) == 3
        mock_get_programs.assert_called_once_with(uuids=[UUID(item['uuid']) for item in self.mock_program_data])


@ddt.ddt
class ProgramCourseEnrollmentOverviewGetTests(
        ProgramCacheMixin,
        SharedModuleStoreTestCase,
        APITestCase
):
    """
    Tests for the ProgramCourseEnrollmentOverview view GET method.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    patch_resume_url = mock.patch(
        _UTILS_PATCH_FORMAT.format('get_resume_urls_for_enrollments'),
        autospec=True,
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.program_uuid = '00000000-1111-2222-3333-444444444444'
        cls.curriculum_uuid = 'aaaaaaaa-1111-2222-3333-444444444444'
        cls.other_curriculum_uuid = 'bbbbbbbb-1111-2222-3333-444444444444'

        cls.course_id = CourseKey.from_string('course-v1:edX+ToyX+Toy_Course')
        cls.course_run = CourseRunFactory.create(key=str(cls.course_id))
        cls.course = CourseFactory.create(course_runs=[cls.course_run])

        cls.username = 'student'
        cls.password = 'password'
        cls.student = UserFactory.create(username=cls.username, password=cls.password)

        # only freeze time when defining these values and not on the whole test case
        # as test_multiple_enrollments_all_enrolled relies on actual differences in modified datetimes
        with freeze_time('2019-01-01'):
            cls.yesterday = timezone.now() - timedelta(1)
            cls.tomorrow = timezone.now() + timedelta(1)

        cls.modulestore_course = ModulestoreCourseFactory.create(
            org="edX",
            course="ToyX",
            run="Toy_Course",
            start=cls.yesterday,
            end=cls.tomorrow,
        )
        cls.relative_certificate_download_url = '/download-the-certificates'
        cls.absolute_certificate_download_url = 'http://www.certificates.com/'

        # create program enrollment
        cls.program_enrollment = ProgramEnrollmentFactory.create(
            program_uuid=cls.program_uuid,
            curriculum_uuid=cls.curriculum_uuid,
            user=cls.student,
        )

        # create course overview
        cls.course_overview = CourseOverviewFactory.create(
            id=cls.course_id,
            start=cls.yesterday,
            end=cls.tomorrow,
        )

        # create course enrollment
        cls.course_enrollment = CourseEnrollmentFactory.create(
            course=cls.course_overview,
            user=cls.student,
            mode=CourseMode.MASTERS,
        )

        # create program course enrollment
        cls.program_course_enrollment = ProgramCourseEnrollmentFactory.create(
            program_enrollment=cls.program_enrollment,
            course_enrollment=cls.course_enrollment,
            course_key=cls.course_id,
            status='active',
        )

        # create program
        catalog_org = OrganizationFactory(key='organization_key')
        cls.program = ProgramFactory(
            uuid=cls.program_uuid,
            authoring_organizations=[catalog_org],
        )
        cls.program['curricula'][0]['courses'].append(cls.course)

    def setUp(self):
        super().setUp()
        self.set_program_in_catalog_cache(self.program_uuid, self.program)

    def create_generated_certificate(self, download_url=None):
        return GeneratedCertificateFactory.create(
            user=self.student,
            course_id=self.course_id,
            status=CertificateStatuses.downloadable,
            mode='verified',
            download_url=(download_url or self.relative_certificate_download_url),
            grade="0.88",
            verify_uuid=uuid4(),
        )

    def log_in(self, user=None):
        """
        Log in `self.client` as `user` if provided or `self.student` otherwise.
        """
        return self.client.login(
            username=(user or self.student).username,
            password=self.password,
        )

    def get_url(self, program_uuid=None):
        """
        Returns the primary URL requested by the test case.

        May be overriden by subclasses of this test case.
        """
        kwargs = {'program_uuid': program_uuid or self.program_uuid}

        return reverse('programs_api:v1:program_course_enrollments_overview', kwargs=kwargs)

    def get_status_and_course_runs(self):
        """
        GETs the endpoint at `self.get_url`.

        May be overriden by subclasses of this test case.

        Returns: (status, course_runs)
            * status (int): HTTP status code.
            * course_runs (list[dict]|None): List of dicts if 200 OK; else, None.
        """
        url = self.get_url()
        response = self.client.get(url)
        return (
            response.status_code,
            response.data['course_runs'] if response.status_code == 200 else None
        )

    def test_401_if_anonymous(self):
        response_status_code, _ = self.get_status_and_course_runs()
        assert status.HTTP_401_UNAUTHORIZED == response_status_code

    def test_404_if_no_program_with_key(self):
        self.log_in()
        self.set_program_in_catalog_cache(self.program_uuid, None)

        response_status_code, _ = self.get_status_and_course_runs()
        assert status.HTTP_404_NOT_FOUND == response_status_code

    def test_403_if_not_enrolled_in_program(self):
        # delete program enrollment
        ProgramEnrollment.objects.all().delete()
        self.log_in()
        response_status_code, _ = self.get_status_and_course_runs()
        assert status.HTTP_403_FORBIDDEN == response_status_code

    def _add_new_course_to_program(self, course_run_key, program):
        """
        Helper method to create another course, an overview for it,
        add it to the program, and re-load the cache.
        """
        other_course_run = CourseRunFactory.create(key=str(course_run_key))
        other_course = CourseFactory.create(course_runs=[other_course_run])
        program['courses'].append(other_course)
        self.set_program_in_catalog_cache(program['uuid'], program)
        CourseOverviewFactory.create(
            id=course_run_key,
            start=self.yesterday,
        )

    @ddt.data(False, True)
    def test_multiple_enrollments_all_enrolled(self, other_enrollment_active):
        other_course_key = CourseKey.from_string('course-v1:edX+ToyX+Other_Course')
        self._add_new_course_to_program(other_course_key, self.program)

        # add a second course enrollment, which doesn't need a ProgramCourseEnrollment
        # to be returned.
        other_enrollment = CourseEnrollmentFactory.create(
            course_id=other_course_key,
            user=self.student,
            mode=CourseMode.VERIFIED,
        )
        if not other_enrollment_active:
            other_enrollment.deactivate()

        self.log_in()
        response_status_code, response_course_runs = self.get_status_and_course_runs()

        assert status.HTTP_200_OK == response_status_code
        actual_course_run_ids = {run['course_run_id'] for run in response_course_runs}
        expected_course_run_ids = {str(self.course_id)}
        if other_enrollment_active:
            expected_course_run_ids.add(str(other_course_key))
        assert expected_course_run_ids == actual_course_run_ids

    @patch_resume_url
    def test_blank_resume_url_omitted(self, mock_get_resume_urls):
        self.log_in()
        mock_get_resume_urls.return_value = {self.course_id: ''}
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert 'resume_course_run_url' not in response_course_runs[0]

    @patch_resume_url
    def test_relative_resume_url_becomes_absolute(self, mock_get_resume_urls):
        self.log_in()
        resume_url = '/resume-here'
        mock_get_resume_urls.return_value = {self.course_id: resume_url}
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        response_resume_url = response_course_runs[0]['resume_course_run_url']
        assert response_resume_url.startswith('http://testserver')
        assert response_resume_url.endswith(resume_url)

    @patch_resume_url
    def test_absolute_resume_url_stays_absolute(self, mock_get_resume_urls):
        self.log_in()
        resume_url = 'http://www.resume.com/'
        mock_get_resume_urls.return_value = {self.course_id: resume_url}
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        response_resume_url = response_course_runs[0]['resume_course_run_url']
        assert response_resume_url == resume_url

    def test_no_url_without_certificate(self):
        self.log_in()
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert 'certificate_download_url' not in response_course_runs[0]

    def test_relative_certificate_url_becomes_absolute(self):
        self.log_in()
        self.create_generated_certificate(
            download_url=self.relative_certificate_download_url
        )
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        response_url = response_course_runs[0]['certificate_download_url']
        assert response_url.startswith('http://testserver')
        assert response_url.endswith(self.relative_certificate_download_url)

    def test_absolute_certificate_url_stays_absolute(self):
        self.log_in()
        self.create_generated_certificate(
            download_url=self.absolute_certificate_download_url
        )
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        response_url = response_course_runs[0]['certificate_download_url']
        assert response_url == self.absolute_certificate_download_url

    def test_no_due_dates(self):
        self.log_in()

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert [] == response_course_runs[0]['due_dates']

    @ddt.data(
        ('2018-12-01', False),
        ('2019-01-01', True),
        ('2019-01-09', False),
    )
    @ddt.unpack
    def test_due_dates(self, now_time, course_in_progress):
        section_1 = BlockFactory.create(
            category='chapter',
            start=self.yesterday,
            due=self.tomorrow,
            parent=self.modulestore_course,
            display_name='section 1'
        )

        subsection_1 = BlockFactory.create(
            category='sequential',
            due=self.tomorrow,
            parent=section_1,
            display_name='subsection 1'
        )

        subsection_2 = BlockFactory.create(
            category='sequential',
            due=self.tomorrow - timedelta(1),
            parent=section_1,
            display_name='subsection 2'
        )

        subsection_3 = BlockFactory.create(
            category='sequential',
            parent=section_1,
            display_name='subsection 3'
        )

        unit_1 = BlockFactory.create(
            category='vertical',
            due=self.tomorrow + timedelta(2),
            parent=subsection_3,
            display_name='unit_1'
        )

        mock_path = 'lms.djangoapps.course_api.api.get_dates_for_course'
        with mock.patch(mock_path) as mock_get_dates, freeze_time(now_time):
            mock_get_dates.return_value = {
                (section_1.location, 'due'): section_1.due,
                (section_1.location, 'start'): section_1.start,
                (subsection_1.location, 'due'): subsection_1.due,
                (subsection_2.location, 'due'): subsection_2.due,
                (unit_1.location, 'due'): unit_1.due,
            }

            self.log_in()
            response_status_code, response_course_runs = self.get_status_and_course_runs()
            assert status.HTTP_200_OK == response_status_code

            block_data = [
                {
                    'name': section_1.display_name,
                    'url': ('http://testserver/courses/course-v1:edX+ToyX+Toy_Course/'
                            'jump_to/block-v1:edX+ToyX+Toy_Course+type@chapter+block@section_1'),
                    'date': '2019-01-02T00:00:00Z',
                },
                {
                    'name': subsection_1.display_name,
                    'url': ('http://testserver/courses/course-v1:edX+ToyX+Toy_Course/'
                            'jump_to/block-v1:edX+ToyX+Toy_Course+type@sequential+block@subsection_1'),
                    'date': '2019-01-02T00:00:00Z',
                },
                {
                    'name': subsection_2.display_name,
                    'url': ('http://testserver/courses/course-v1:edX+ToyX+Toy_Course/'
                            'jump_to/block-v1:edX+ToyX+Toy_Course+type@sequential+block@subsection_2'),
                    'date': '2019-01-01T00:00:00Z',
                },
                {
                    'name': unit_1.display_name,
                    'url': ('http://testserver/courses/course-v1:edX+ToyX+Toy_Course/'
                            'jump_to/block-v1:edX+ToyX+Toy_Course+type@vertical+block@unit_1'),
                    'date': '2019-01-04T00:00:00Z',
                },
            ]
            due_dates = response_course_runs[0]['due_dates']

            if course_in_progress:
                for block in block_data:
                    assert block in due_dates
            else:
                assert due_dates == []

    @mock.patch.object(CourseOverview, 'has_ended')
    def test_course_run_status_instructor_paced_completed(self, mock_has_ended):
        self.log_in()

        # set as instructor paced
        self.course_overview.self_paced = False
        self.course_overview.save()

        mock_has_ended.return_value = True

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.COMPLETED == response_course_runs[0]['course_run_status']

    @mock.patch.object(CourseOverview, 'has_ended')
    @mock.patch.object(CourseOverview, 'has_started')
    def test_course_run_status_instructor_paced_in_progress(self, mock_has_started, mock_has_ended):
        self.log_in()

        # set as instructor paced
        self.course_overview.self_paced = False
        self.course_overview.save()

        mock_has_started.return_value = True
        mock_has_ended.return_value = False

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.IN_PROGRESS == response_course_runs[0]['course_run_status']

    @mock.patch.object(CourseOverview, 'has_ended')
    @mock.patch.object(CourseOverview, 'has_started')
    def test_course_run_status_instructor_paced_upcoming(self, mock_has_started, mock_has_ended):
        self.log_in()

        # set as instructor paced
        self.course_overview.self_paced = False
        self.course_overview.save()

        mock_has_started.return_value = False
        mock_has_ended.return_value = False

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.UPCOMING == response_course_runs[0]['course_run_status']

    @mock.patch.object(CourseOverview, 'has_ended')
    def test_course_run_status_self_paced_completed(self, mock_has_ended):
        self.log_in()

        # set as self paced
        self.course_overview.self_paced = True
        self.course_overview.save()

        # course run has ended
        mock_has_ended.return_value = True

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.COMPLETED == response_course_runs[0]['course_run_status']

        # course run has not ended and user has earned a passing certificate more than 30 days ago
        certificate = self.create_generated_certificate()
        certificate.created_date = timezone.now() - timedelta(30)
        certificate.save()
        mock_has_ended.return_value = False

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.COMPLETED == response_course_runs[0]['course_run_status']

        # course run has ended and user has earned a passing certificate
        mock_has_ended.return_value = True

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.COMPLETED == response_course_runs[0]['course_run_status']

    @mock.patch.object(CourseOverview, 'has_ended')
    @mock.patch.object(CourseOverview, 'has_started')
    def test_course_run_status_self_paced_in_progress(self, mock_has_started, mock_has_ended):
        self.log_in()

        # set as self paced
        self.course_overview.self_paced = True
        self.course_overview.save()

        # course run has started and has not ended
        mock_has_started.return_value = True
        mock_has_ended.return_value = False

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.IN_PROGRESS == response_course_runs[0]['course_run_status']

        # course run has not ended and user has earned a passing certificate fewer than 30 days ago
        certificate = self.create_generated_certificate()
        certificate.created_date = timezone.now() - timedelta(5)
        certificate.save()

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.IN_PROGRESS == response_course_runs[0]['course_run_status']

    @mock.patch.object(CourseOverview, 'has_ended')
    @mock.patch.object(CourseOverview, 'has_started')
    def test_course_run_status_self_paced_upcoming(self, mock_has_started, mock_has_ended):
        self.log_in()

        # set as self paced
        self.course_overview.self_paced = True
        self.course_overview.save()

        # course run has not started and has not ended
        mock_has_started.return_value = False
        mock_has_ended.return_value = False

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert CourseRunProgressStatuses.UPCOMING == response_course_runs[0]['course_run_status']

    def test_course_run_url(self):
        self.log_in()

        course_run_url = f'http://learning-mfe/course/{str(self.course_id)}/home'

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert course_run_url == response_course_runs[0]['course_run_url']

    def test_course_run_dates(self):
        self.log_in()

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code

        course_run_overview = response_course_runs[0]

        assert course_run_overview['start_date'] == '2018-12-31T00:00:00Z'
        assert course_run_overview['end_date'] == '2019-01-02T00:00:00Z'

        # course run end date may not exist
        self.course_overview.end = None
        self.course_overview.save()

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert response_course_runs[0]['end_date'] is None

    def test_course_run_id_and_display_name(self):
        self.log_in()

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code

        course_run_overview = response_course_runs[0]

        assert course_run_overview['course_run_id'] == str(self.course_id)
        assert course_run_overview['display_name'] == f'{str(self.course_id)} Course'

    def test_emails_enabled(self):
        self.log_in()

        # by default, BulkEmailFlag is not enabled, so 'emails_enabled' won't be in the response
        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert 'emails_enabled' not in response_course_runs[0]

        with mock.patch.object(BulkEmailFlag, 'feature_enabled', return_value=True):
            response_status_code, response_course_runs = self.get_status_and_course_runs()
            assert status.HTTP_200_OK == response_status_code
            assert response_course_runs[0]['emails_enabled']

            Optout.objects.create(
                user=self.student,
                course_id=self.course_id
            )
            response_status_code, response_course_runs = self.get_status_and_course_runs()
            assert status.HTTP_200_OK == response_status_code
            assert not response_course_runs[0]['emails_enabled']

    def test_micromasters_title(self):
        self.log_in()

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert 'micromasters_title' not in response_course_runs[0]

        self.program['type'] = 'MicroMasters'
        # update the program in the catalog cache
        self.set_program_in_catalog_cache(self.program_uuid, self.program)

        response_status_code, response_course_runs = self.get_status_and_course_runs()
        assert status.HTTP_200_OK == response_status_code
        assert 'micromasters_title' in response_course_runs[0]


@ddt.ddt
class UserProgramCourseEnrollmentViewGetTests(ProgramCourseEnrollmentOverviewGetTests):
    """
    Tests for UserProgramCourseEnrollmentViewGetTests.

    For now, we just subclass ProgramCourseEnrollmentOverviewGetTests
    because there are so many shared test cases.

    TODO: When the old, non-paginated ProgramCourseEnrollmentOverview endpoint
    is removed, these two test cases should be collapsed into one test case.
    """

    # pylint: disable=test-inherits-tests

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.student_many_enrollments = UserFactory(
            username='student_many_enrollments',
            password=cls.password,
        )
        cls.program_enrollment = ProgramEnrollmentFactory(
            program_uuid=cls.program_uuid,
            curriculum_uuid=cls.curriculum_uuid,
            user=cls.student_many_enrollments,
        )
        for _ in range(40):
            CourseEnrollmentFactory(user=cls.student_many_enrollments)

    def get_url(self, program_uuid=None, username=None, page_size_string=None):
        """
        Returns the primary URL requested by the test case.

        May be overriden by subclasses of this test case.
        """
        # pylint: disable=arguments-differ
        url = reverse(
            'programs_api:v1:user_program_course_enrollments',
            kwargs={
                'username': username or self.username,
                'program_uuid': program_uuid or self.program_uuid,
            },
        )
        return (
            url + '?page_size=' + page_size_string
            if page_size_string
            else url
        )

    def get_status_and_course_runs(self):
        """
        GETs the endpoint at `self.get_url`.

        Unlike the superclass's implementation of this,
        this method takes into account the endpoint being paginated.
        returning the concatentsaed results of repeated calls to the `next` URL.

        If any GET call returns non-200, immediately return that HTTP status code
        along with the results collected so far.

        Returns: (status, course_runs)
            * status (int): HTTP status code.
            * course_runs (list[dict]|None): List of dicts if 200 OK; else, None.
        """
        results = []
        next_url = self.get_url(self.program_uuid)
        while next_url:
            response = self.client.get(next_url)
            if response.status_code != 200:
                break
            results += response.data['results']
            next_url = response.data.get('next')
        return response.status_code, results

    def test_requester_must_match_username(self):
        """
        Test that the username in the URL must match the username of the requester.

        (The plan is that we will eventually allow masquerading, which will change
        require changing this test to be more permissive).
        """
        self.log_in()
        url = self.get_url(username='other_student')
        response = self.client.get(url)
        assert response.status_code == 403

    def test_no_enrollments(self):
        """
        Test that a user with no enrollments will get a 200 from this endpoint
        with an empty list of results.
        """
        self.log_in()
        no_enrollments = CourseEnrollment.objects.none()
        with mock.patch.object(
                views,
                'get_enrollments_for_courses_in_program',
                lambda _user, _program: no_enrollments,
        ):
            response_status, response_course_runs = self.get_status_and_course_runs()
        assert response_status == 200
        assert response_course_runs == []

    @ddt.data(
        # If not provided, the page size is defaults to 10.
        (None, [10, 10, 10, 10]),
        # We can set the page size below the default.
        ('5', [5, 5, 5, 5, 5, 5, 5, 5]),
        # We can set the page size above the default.
        ('19', [19, 19, 2]),
        # Invalid parameter values fall back to page size of 10.
        ('covid-19', [10, 10, 10, 10]),
        # The max page size is 25. Numbers above this will be interpreted as 25.
        ('30', [25, 15]),
    )
    @ddt.unpack
    def test_pagination(self, page_size_string, expected_page_sizes):
        """
        Test the interactions between the `page_size` parameter
        and the sizes of the each request.
        """

        def mock_get_enrollment_overviews(user, program, enrollments, request):  # lint-amnesty, pylint: disable=unused-argument
            """
            Mock implementation of `utils.get_enrollments_overviews`
            that returns a dict with the correct `course_run_id`
            but fake values for all the rest.

            This function should never get an enrollment queryset greater than the
            max page size.
            """
            assert len(enrollments) <= 25
            return [
                {
                    'course_run_id': enrollment.course.id,
                    'display_name': 'Fake Display Name for {enrollment.course.id}'.format(
                        enrollment=enrollment,
                    ),
                    'course_run_url': 'http://fake.url.example.com/course-run',
                    'start_date': '2112-02-20',
                    'end_date': '2112-12-21',
                    'course_run_status': '',
                    'due_dates': [],
                }
                for enrollment in enrollments
            ]

        self.log_in(user=self.student_many_enrollments)
        many_enrollments = CourseEnrollment.objects.filter(user=self.student_many_enrollments)

        with mock.patch.object(
                views,
                'get_enrollments_for_courses_in_program',
                lambda _user, _program: many_enrollments,
        ):
            with mock.patch.object(
                    views,
                    'get_enrollment_overviews',
                    mock_get_enrollment_overviews,
            ):
                actual_page_sizes = []
                all_results = []
                next_url = self.get_url(
                    program_uuid=self.program_uuid,
                    username=self.student_many_enrollments.username,
                    page_size_string=page_size_string,
                )
                while next_url:
                    response = self.client.get(next_url)
                    assert response.status_code == 200
                    actual_page_sizes.append(len(response.data['results']))
                    all_results += response.data['results']
                    next_url = response.data.get('next')

        assert actual_page_sizes == expected_page_sizes
        all_course_run_ids = {result['course_run_id'] for result in all_results}
        assert len(all_course_run_ids) == 40, (
            "Expected 40 unique course run IDs to be processed "
            "across all pages."
        )


class EnrollmentDataResetViewTests(ProgramCacheMixin, APITestCase):
    """ Tests endpoint for resetting enrollments in integration environments """

    FEATURES_WITH_ENABLED = settings.FEATURES.copy()
    FEATURES_WITH_ENABLED[ENABLE_ENROLLMENT_RESET_FLAG] = True

    reset_enrollments_cmd = 'reset_enrollment_data'
    reset_users_cmd = 'remove_social_auth_users'

    patch_call_command = mock.patch(
        _VIEW_PATCH_FORMAT.format('call_command'), autospec=True
    )

    def setUp(self):
        super().setUp()
        self.start_cache_isolation()

        self.organization = LMSOrganizationFactory(short_name='uox')
        self.provider = SAMLProviderConfigFactory(organization=self.organization)

        self.global_staff = GlobalStaffFactory(username='global-staff', password='password')
        self.client.login(username=self.global_staff.username, password='password')

    def request(self, organization):
        return self.client.post(
            reverse('programs_api:v1:reset_enrollment_data'),
            {'organization': organization},
            format='json',
        )

    def tearDown(self):
        self.end_cache_isolation()
        super().tearDown()

    @patch_call_command
    def test_feature_disabled_by_default(self, mock_call_command):
        response = self.request(self.organization.short_name)
        assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
        mock_call_command.assert_has_calls([])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_403_for_non_staff(self, mock_call_command):
        student = UserFactory.create(username='student', password='password')
        self.client.login(username=student.username, password='password')
        response = self.request(self.organization.short_name)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        mock_call_command.assert_has_calls([])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_reset(self, mock_call_command):
        programs = [str(uuid4()), str(uuid4())]
        self.set_org_in_catalog_cache(self.organization, programs)

        response = self.request(self.organization.short_name)
        assert response.status_code == status.HTTP_200_OK
        mock_call_command.assert_has_calls([
            mock.call(self.reset_users_cmd, self.provider.slug, force=True),
            mock.call(self.reset_enrollments_cmd, ','.join(programs), force=True),
        ])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_reset_with_multiple_idp(self, mock_call_command):
        programs = [str(uuid4()), str(uuid4())]
        self.set_org_in_catalog_cache(self.organization, programs)
        provider_2 = SAMLProviderConfigFactory(
            organization=self.organization,
            slug='test-shib-2',
            enabled=True,
        )

        response = self.request(self.organization.short_name)
        assert response.status_code == status.HTTP_200_OK
        mock_call_command.assert_has_calls([
            mock.call(self.reset_users_cmd, self.provider.slug, force=True),
            mock.call(self.reset_users_cmd, provider_2.slug, force=True),
            mock.call(self.reset_enrollments_cmd, ','.join(programs), force=True),
        ])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_reset_without_idp(self, mock_call_command):
        organization = LMSOrganizationFactory()
        programs = [str(uuid4()), str(uuid4())]
        self.set_org_in_catalog_cache(organization, programs)

        response = self.request(organization.short_name)
        assert response.status_code == status.HTTP_200_OK
        mock_call_command.assert_has_calls([
            mock.call(self.reset_enrollments_cmd, ','.join(programs), force=True),
        ])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_organization_not_found(self, mock_call_command):
        response = self.request('yyz')
        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_call_command.assert_has_calls([])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_no_programs_doesnt_break(self, mock_call_command):
        programs = []
        self.set_org_in_catalog_cache(self.organization, programs)

        response = self.request(self.organization.short_name)
        assert response.status_code == status.HTTP_200_OK
        mock_call_command.assert_has_calls([
            mock.call(self.reset_users_cmd, self.provider.slug, force=True),
        ])

    @override_settings(FEATURES=FEATURES_WITH_ENABLED)
    @patch_call_command
    def test_missing_body_content(self, mock_call_command):
        response = self.client.post(
            reverse('programs_api:v1:reset_enrollment_data'),
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_call_command.assert_has_calls([])
