"""Tests for Course run views"""


import datetime
from unittest.mock import patch  # pylint: disable=unused-import  # noqa: F401

import ddt
import pytz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey
from organizations.api import add_organization, get_course_organizations
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.tests.factories import TEST_PASSWORD, AdminFactory, UserFactory
from openedx.core.lib.courses import course_image_url
from xmodule.contentstore.content import StaticContent  # pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.exceptions import NotFoundError  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    CourseFactory,
    ToyCourseFactory,
)

from ...serializers.course_runs import CourseRunSerializer
from ..utils import serialize_datetime


@ddt.ddt
class CourseRunViewSetTests(ModuleStoreTestCase):
    """
    Tests for creating course runs
    """
    list_url = reverse('api:v1:course_run-list')

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        user = AdminFactory()
        self.client.login(username=user.username, password=TEST_PASSWORD)

    def get_course_run_data(self, user, start, end, pacing_type, role='instructor'):
        return {
            'title': 'Testing 101',
            'org': 'TestingX',
            'number': 'Testing101x',
            'run': '3T2017',
            'schedule': {
                'start': serialize_datetime(start),
                'end': serialize_datetime(end),
            },
            'team': [
                {
                    'user': user.username,
                    'role': role,
                }
            ],
            'pacing_type': pacing_type,
        }

    def assert_course_run_schedule(self, course_run, start, end):
        assert course_run.start == start
        assert course_run.end == end

    def assert_access_role(self, course_run, user, role):
        # An error will be raised if the endpoint did not create the role
        assert CourseAccessRole.objects.filter(
            course_id=course_run.id, org=course_run.id.org, user=user, role=role).count() == 1

    def assert_course_access_role_count(self, course_run, expected):
        assert CourseAccessRole.objects.filter(course_id=course_run.id).count() == expected

    def get_serializer_context(self):
        return {'request': RequestFactory().get('')}

    def test_without_authentication(self):
        self.client.logout()
        response = self.client.get(self.list_url)
        assert response.status_code == 401

    def test_without_authorization(self):
        user = UserFactory(is_staff=False)
        self.client.login(username=user.username, password=TEST_PASSWORD)
        response = self.client.get(self.list_url)
        assert response.status_code == 403

    def test_list(self):
        course_runs = CourseFactory.create_batch(3)
        response = self.client.get(self.list_url)
        assert response.status_code == 200

        # Order matters for the assertion
        course_runs = sorted(course_runs, key=lambda course_run: str(course_run.id))
        actual = sorted(response.data['results'], key=lambda course_run: course_run['id'])
        assert actual == CourseRunSerializer(course_runs, many=True, context=self.get_serializer_context()).data

    def test_retrieve(self):
        course_run = CourseFactory()
        url = reverse('api:v1:course_run-detail', kwargs={'pk': str(course_run.id)})
        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data == CourseRunSerializer(course_run, context=self.get_serializer_context()).data

    def test_retrieve_not_found(self):
        url = reverse('api:v1:course_run-detail', kwargs={'pk': 'course-v1:TestX+Test101x+1T2017'})
        response = self.client.get(url)
        assert response.status_code == 404

    def test_update_not_found(self):
        url = reverse('api:v1:course_run-detail', kwargs={'pk': 'course-v1:TestX+Test101x+1T2017'})
        response = self.client.put(url, {})
        assert response.status_code == 404

    def test_update(self):
        course_run = CourseFactory(start=None, end=None)
        assert CourseAccessRole.objects.filter(course_id=course_run.id).count() == 0

        url = reverse('api:v1:course_run-detail', kwargs={'pk': str(course_run.id)})
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        end = start + datetime.timedelta(days=30)
        title = 'A New Testing Strategy'
        user = UserFactory()
        role = 'staff'
        data = {
            'title': title,
            'schedule': {
                'start': serialize_datetime(start),
                'end': serialize_datetime(end),
            },
            'team': [
                {
                    'user': user.username,
                    'role': role,
                }
            ],
        }
        response = self.client.put(url, data, format='json')
        assert response.status_code == 200
        self.assert_access_role(course_run, user, role)
        self.assert_course_access_role_count(course_run, 1)

        course_run = modulestore().get_course(course_run.id)
        assert response.data == CourseRunSerializer(course_run, context=self.get_serializer_context()).data
        assert course_run.display_name == title
        self.assert_course_run_schedule(course_run, start, end)

    def test_update_with_invalid_user(self):
        course_run = CourseFactory()
        url = reverse('api:v1:course_run-detail', kwargs={'pk': str(course_run.id)})
        data = {
            'title': course_run.display_name,
            'team': [
                {
                    'user': 'test-user',
                    'role': 'staff',
                }
            ]
        }
        response = self.client.put(url, data, format='json')
        assert response.status_code == 400
        assert response.data == {'team': ['Course team user does not exist']}

    def test_update_with_pacing_type(self):
        """
        Test that update run updates the pacing type
        """
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        course_run = CourseFactory(start=start, end=None, self_paced=False)
        data = {
            'pacing_type': 'self_paced',
        }
        course_run_detail_url = reverse('api:v1:course_run-detail', kwargs={'pk': str(course_run.id)})
        response = self.client.patch(course_run_detail_url, data, format='json')
        assert response.status_code == 200

        course_run = modulestore().get_course(course_run.id)
        assert course_run.self_paced is True
        self.assert_course_run_schedule(course_run, start, None)

    def test_update_with_instructor_role(self):
        """
        Test that update creates a new instructor role only if it does not exist
        """
        instructor_role = 'instructor'
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        new_user = UserFactory()
        course_run = CourseFactory(start=start, end=None, self_paced=False)
        assert CourseAccessRole.objects.filter(course_id=course_run.id).count() == 0
        data = {
            'team': [
                {
                    'user': new_user.username,
                    'role': instructor_role,
                },
            ],
            'pacing_type': 'self_paced',
        }
        course_run_detail_url = reverse('api:v1:course_run-detail', kwargs={'pk': str(course_run.id)})
        response = self.client.patch(course_run_detail_url, data, format='json')
        assert response.status_code == 200
        self.assert_access_role(course_run, new_user, instructor_role)
        self.assert_course_access_role_count(course_run, 1)

        # Requesting again with the same data should not create new instructor role
        response = self.client.patch(course_run_detail_url, data, format='json')
        assert response.status_code == 200
        self.assert_access_role(course_run, new_user, instructor_role)
        self.assert_course_access_role_count(course_run, 1)

    def test_update_with_multiple_roles(self):
        """
        Test that update creates an instructor role for a user in addition to any other role/roles he already has
        """
        staff_role = 'staff'
        instructor_role = 'instructor'
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        course_run = CourseFactory(start=start, end=None, self_paced=False)

        existing_user = UserFactory()
        CourseAccessRole.objects.create(
            course_id=course_run.id, org=course_run.id.org, role=staff_role, user=existing_user
        )
        # existing_user already has a staff role in the course
        # The request should create an additional instructor role for existing_user

        new_user = UserFactory()
        assert CourseAccessRole.objects.filter(course_id=course_run.id).count() == 1

        data = {
            'team': [
                {
                    'user': existing_user.username,
                    'role': instructor_role,
                },
                {
                    'user': new_user.username,
                    'role': instructor_role,
                },
            ],
        }

        course_run_detail_url = reverse('api:v1:course_run-detail', kwargs={'pk': str(course_run.id)})
        response = self.client.patch(course_run_detail_url, data, format='json')
        assert response.status_code == 200
        self.assert_access_role(course_run, existing_user, instructor_role)
        self.assert_access_role(course_run, new_user, instructor_role)
        self.assert_course_access_role_count(course_run, 3)

    @ddt.data(
        ('instructor_paced', False),
        ('self_paced', True),
    )
    @ddt.unpack
    def test_create(self, pacing_type, expected_self_paced_value):
        """Tests successful course run creation"""
        user = UserFactory()
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        end = start + datetime.timedelta(days=30)
        role = 'staff'
        data = self.get_course_run_data(user, start, end, pacing_type, role)

        response = self.client.post(self.list_url, data, format='json')
        self.assertEqual(response.status_code, 201)  # noqa: PT009

        course_run_key = CourseKey.from_string(response.data['id'])
        course_run = modulestore().get_course(course_run_key)
        self.assertEqual(course_run.display_name, data['title'])  # noqa: PT009
        self.assertEqual(course_run.id.org, data['org'])  # noqa: PT009
        self.assertEqual(course_run.id.course, data['number'])  # noqa: PT009
        self.assertEqual(course_run.id.run, data['run'])  # noqa: PT009
        self.assertEqual(course_run.self_paced, expected_self_paced_value)  # noqa: PT009
        self.assert_course_run_schedule(course_run, start, end)
        self.assert_access_role(course_run, user, role)
        self.assert_course_access_role_count(course_run, 1)

    def test_create_with_invalid_course_team(self):
        """
        Tests that if the course team user is invalid, it returns bad request status
        with expected validation message
        """
        user = UserFactory()
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        end = start + datetime.timedelta(days=30)
        data = self.get_course_run_data(user, start, end, 'self-paced')
        data['team'] = [{'user': 'invalid-username'}]
        response = self.client.post(self.list_url, data, format='json')
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        self.assertEqual(response.data.get('team'), ['Course team user does not exist'])  # noqa: PT009

    def test_images_upload(self):
        # http://www.django-rest-framework.org/api-guide/parsers/#fileuploadparser
        course_run = CourseFactory()
        expected_filename = 'course_image.png'
        content_key = StaticContent.compute_location(course_run.id, expected_filename)

        assert course_run.course_image != expected_filename

        try:
            contentstore().find(content_key)
            self.fail('No image should be associated with a new course run.')
        except NotFoundError:
            pass

        url = reverse('api:v1:course_run-images', kwargs={'pk': str(course_run.id)})
        # PNG. Single black pixel
        content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS' \
                  b'\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82'

        # We are intentionally passing the incorrect JPEG extension here
        upload = SimpleUploadedFile('card_image.jpg', content, content_type='image/png')
        response = self.client.post(url, {'card_image': upload}, format='multipart')
        assert response.status_code == 200

        course_run = modulestore().get_course(course_run.id)
        assert course_run.course_image == expected_filename

        expected = {'card_image': RequestFactory().get('').build_absolute_uri(course_image_url(course_run))}
        assert response.data == expected

        # There should now be an image stored
        contentstore().find(content_key)

    @override_settings(ORGANIZATIONS_AUTOCREATE=False)
    @ddt.data(
        ('instructor_paced', False, 'NotOriginalNumber1x'),
        ('self_paced', True, None),
    )
    @ddt.unpack
    def test_rerun(self, pacing_type, expected_self_paced_value, number):
        original_course_run = ToyCourseFactory()
        add_organization({
            'name': 'Test Organization',
            'short_name': original_course_run.id.org,  # pylint: disable=no-member
            'description': 'Testing Organization Description',
        })
        start = datetime.datetime.now(pytz.UTC).replace(microsecond=0)
        end = start + datetime.timedelta(days=30)
        user = UserFactory()
        role = 'instructor'
        run = '3T2017'
        url = reverse('api:v1:course_run-rerun', kwargs={'pk': str(original_course_run.id)})  # pylint: disable=no-member
        data = {
            'run': run,
            'schedule': {
                'start': serialize_datetime(start),
                'end': serialize_datetime(end),
            },
            'team': [
                {
                    'user': user.username,
                    'role': role,
                }
            ],
            'pacing_type': pacing_type,
        }
        # If number is supplied, this should become the course number used in the course run key
        # If not, it should default to the original course run number that the rerun is based on.
        if number:
            data.update({'number': number})
        response = self.client.post(url, data, format='json')
        assert response.status_code == 201

        course_run_key = CourseKey.from_string(response.data['id'])
        course_run = modulestore().get_course(course_run_key)

        assert course_run.id.run == run
        assert course_run.self_paced is expected_self_paced_value

        if number:
            assert course_run.id.course == number
            assert course_run.id.course != original_course_run.id.course  # pylint: disable=no-member
        else:
            assert course_run.id.course == original_course_run.id.course  # pylint: disable=no-member

        self.assert_course_run_schedule(course_run, start, end)
        self.assert_access_role(course_run, user, role)
        self.assert_course_access_role_count(course_run, 1)
        course_orgs = get_course_organizations(course_run_key)
        self.assertEqual(len(course_orgs), 1)  # noqa: PT009
        self.assertEqual(course_orgs[0]['short_name'], original_course_run.id.org)  # pylint: disable=no-member  # noqa: PT009

    def test_rerun_duplicate_run(self):
        course_run = ToyCourseFactory()
        url = reverse('api:v1:course_run-rerun', kwargs={'pk': str(course_run.id)})
        data = {
            'run': course_run.id.run,
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 400
        assert response.data == {'run': [f'Course run {course_run.id} already exists']}

    def test_rerun_invalid_number(self):
        course_run = ToyCourseFactory()
        url = reverse('api:v1:course_run-rerun', kwargs={'pk': str(course_run.id)})
        data = {
            'run': '2T2019',
            'number': '!@#$%^&*()',
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 400
        assert response.data == {'non_field_errors': [
            'Invalid key supplied. Ensure there are no special characters in the Course Number.'
        ]}

    def test_clone_course(self):
        course = CourseFactory()
        url = reverse('api:v1:course_run-clone')
        data = {
            'source_course_id': str(course.id),
            'destination_course_id': 'course-v1:destination+course+id',
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 201
        self.assertEqual(response.data, {"message": "Course cloned successfully."})  # noqa: PT009

    def test_clone_course_with_missing_source_id(self):
        url = reverse('api:v1:course_run-clone')
        data = {
            'destination_course_id': 'course-v1:destination+course+id',
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 400
        self.assertEqual(response.data, {'source_course_id': ['This field is required.']})  # noqa: PT009

    def test_clone_course_with_missing_dest_id(self):
        url = reverse('api:v1:course_run-clone')
        data = {
            'source_course_id': 'course-v1:source+course+id',
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 400
        self.assertEqual(response.data, {'destination_course_id': ['This field is required.']})  # noqa: PT009

    def test_clone_course_with_nonexistent_source_course(self):
        url = reverse('api:v1:course_run-clone')
        data = {
            'source_course_id': 'course-v1:nonexistent+source+course_id',
            'destination_course_id': 'course-v1:destination+course+id',
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 400
        assert str(response.data.get('non_field_errors')[0]) == 'Source course does not exist.'

    def test_clone_course_with_existing_dest_course(self):
        url = reverse('api:v1:course_run-clone')
        course = CourseFactory()
        existing_dest_course = CourseFactory()
        data = {
            'source_course_id': str(course.id),
            'destination_course_id': str(existing_dest_course.id),
        }
        response = self.client.post(url, data, format='json')
        assert response.status_code == 400
        assert str(response.data.get('non_field_errors')[0]) == 'Destination course already exists.'
