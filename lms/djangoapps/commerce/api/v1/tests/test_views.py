""" Commerce API v1 view tests. """


import itertools
import json
from datetime import datetime, timedelta

import ddt
import pytz
from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse, reverse_lazy
from rest_framework.utils.encoders import JSONEncoder

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import VerificationDeadline
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ....tests.mocks import mock_order_endpoint
from ....tests.test_views import UserMixin

PASSWORD = 'Password1234'
JSON_CONTENT_TYPE = 'application/json'


class CourseApiViewTestMixin:
    """ Mixin for CourseApi views.

    Automatically creates a course and CourseMode.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.course_mode = CourseMode.objects.create(
            course_id=self.course.id,
            mode_slug='verified',
            min_price=100,
            currency='USD',
            sku='ABC123',
            bulk_sku='BULK-ABC123'
        )

    @classmethod
    def _serialize_datetime(cls, dt):  # pylint: disable=invalid-name
        """ Serializes datetime values using Django REST Framework's encoder.

        Use this to simplify equality assertions.
        """
        if dt:
            return JSONEncoder().default(dt)
        return None

    @classmethod
    def _serialize_course_mode(cls, course_mode):
        """ Serialize a CourseMode to a dict. """
        return {
            'name': course_mode.mode_slug,
            'currency': course_mode.currency.lower(),
            'price': course_mode.min_price,
            'sku': course_mode.sku,
            'bulk_sku': course_mode.bulk_sku,
            'expires': cls._serialize_datetime(course_mode.expiration_datetime),
            'android_sku': course_mode.android_sku,
            'ios_sku': course_mode.ios_sku
        }

    @classmethod
    def _serialize_course(cls, course, modes=None, verification_deadline=None):
        """ Serializes a course to a Python dict. """
        modes = modes or []
        verification_deadline = verification_deadline or VerificationDeadline.deadline_for_course(course.id)

        return {
            'id': str(course.id),
            'name': str(course.display_name),
            'verification_deadline': cls._serialize_datetime(verification_deadline),
            'modes': [cls._serialize_course_mode(mode) for mode in modes]
        }


class CourseListViewTests(CourseApiViewTestMixin, ModuleStoreTestCase):
    """ Tests for CourseListView. """
    path = reverse_lazy('commerce_api:v1:courses:list')

    def test_authentication_required(self):
        """ Verify only authenticated users can access the view. """
        self.client.logout()
        response = self.client.get(self.path, content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 401

    def test_list(self):
        """ Verify the view lists the available courses and modes. """
        user = UserFactory.create()
        self.client.login(username=user.username, password=PASSWORD)
        response = self.client.get(self.path, content_type=JSON_CONTENT_TYPE)

        assert response.status_code == 200
        actual = json.loads(response.content.decode('utf-8'))
        expected = [self._serialize_course(self.course, [self.course_mode])]
        self.assertListEqual(actual, expected)


@ddt.ddt
class CourseRetrieveUpdateViewTests(CourseApiViewTestMixin, ModuleStoreTestCase):
    """ Tests for CourseRetrieveUpdateView. """
    NOW = 'now'
    DATES = {
        NOW: datetime.now(),
        None: None,
    }

    def setUp(self):
        super().setUp()
        self.path = reverse('commerce_api:v1:courses:retrieve_update', args=[str(self.course.id)])
        self.user = UserFactory.create()
        self.client.login(username=self.user.username, password=PASSWORD)

        permission = Permission.objects.get(name='Can change course mode')
        self.user.user_permissions.add(permission)

    @ddt.data('get', 'post', 'put')
    def test_authentication_required(self, method):
        """ Verify only authenticated users can access the view. """
        self.client.logout()
        response = getattr(self.client, method)(self.path, content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 401

    @ddt.data('post', 'put')
    def test_authorization_required(self, method):
        """ Verify create/edit operations require appropriate permissions. """
        self.user.user_permissions.clear()

        response = getattr(self.client, method)(self.path, content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 403

    def test_retrieve(self):
        """ Verify the view displays info for a given course. """
        response = self.client.get(self.path, content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 200

        actual = json.loads(response.content.decode('utf-8'))
        expected = self._serialize_course(self.course, [self.course_mode])
        assert actual == expected

    def test_retrieve_invalid_course(self):
        """ The view should return HTTP 404 when retrieving data for a course that does not exist. """
        path = reverse('commerce_api:v1:courses:retrieve_update', args=['a/b/c'])
        response = self.client.get(path, content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 404

    def _get_update_response_and_expected_data(self, mode_expiration, verification_deadline):
        """ Returns expected data and response for course update. """
        expected_course_mode = CourseMode(
            mode_slug='verified',
            min_price=200,
            currency='USD',
            sku='ABC123',
            bulk_sku='BULK-ABC123',
            expiration_datetime=mode_expiration
        )
        expected = self._serialize_course(self.course, [expected_course_mode], verification_deadline)

        # Sanity check: The API should return HTTP status 200 for updates
        response = self.client.put(self.path, json.dumps(expected), content_type=JSON_CONTENT_TYPE)

        return response, expected

    def test_update(self):
        """ Verify the view supports updating a course. """
        # Sanity check: Ensure no verification deadline is set
        assert VerificationDeadline.deadline_for_course(self.course.id) is None

        # Generate the expected data
        now = datetime.now(pytz.utc)
        verification_deadline = now + timedelta(days=1)
        expiration_datetime = now
        response, expected = self._get_update_response_and_expected_data(expiration_datetime, verification_deadline)

        # Sanity check: The API should return HTTP status 200 for updates
        assert response.status_code == 200

        # Verify the course and modes are returned as JSON
        actual = json.loads(response.content.decode('utf-8'))
        assert actual == expected

        # Verify the verification deadline is updated
        assert VerificationDeadline.deadline_for_course(self.course.id) == verification_deadline

    def test_update_invalid_dates(self):
        """
        Verify the API does not allow the verification deadline to be set before the course mode upgrade deadlines.
        """
        expiration_datetime = datetime.now(pytz.utc)
        verification_deadline = datetime(year=1915, month=5, day=7, tzinfo=pytz.utc)
        response, __ = self._get_update_response_and_expected_data(expiration_datetime, verification_deadline)
        assert response.status_code == 400

        # Verify the error message is correct
        actual = json.loads(response.content.decode('utf-8'))
        expected = {
            'non_field_errors': ['Verification deadline must be after the course mode upgrade deadlines.']
        }
        assert actual == expected

    def test_update_verification_deadline_without_expiring_modes(self):
        """ Verify verification deadline can be set if no course modes expire.

         This accounts for the verified professional mode, which requires verification but should never expire.
        """
        verification_deadline = datetime(year=1915, month=5, day=7, tzinfo=pytz.utc)
        response, __ = self._get_update_response_and_expected_data(None, verification_deadline)

        assert response.status_code == 200
        assert VerificationDeadline.deadline_for_course(self.course.id) == verification_deadline

    def test_update_remove_verification_deadline(self):
        """
        Verify that verification deadlines can be removed through the API.
        """
        verification_deadline = datetime(year=1915, month=5, day=7, tzinfo=pytz.utc)
        response, __ = self._get_update_response_and_expected_data(None, verification_deadline)
        assert VerificationDeadline.deadline_for_course(self.course.id) == verification_deadline

        verified_mode = CourseMode(
            mode_slug='verified',
            min_price=200,
            currency='USD',
            sku='ABC123',
            bulk_sku='BULK-ABC123',
            expiration_datetime=None
        )
        updated_data = self._serialize_course(self.course, [verified_mode], None)
        updated_data['verification_deadline'] = None

        response = self.client.put(self.path, json.dumps(updated_data), content_type=JSON_CONTENT_TYPE)

        assert response.status_code == 200
        assert VerificationDeadline.deadline_for_course(self.course.id) is None

    def test_update_verification_deadline_left_alone(self):
        """
        When the course's verification deadline is set and an update request doesn't
        include it, we should take no action on it.
        """
        verification_deadline = datetime(year=1915, month=5, day=7, tzinfo=pytz.utc)
        response, __ = self._get_update_response_and_expected_data(None, verification_deadline)
        assert VerificationDeadline.deadline_for_course(self.course.id) == verification_deadline

        verified_mode = CourseMode(
            mode_slug='verified',
            min_price=200,
            currency='USD',
            sku='ABC123',
            bulk_sku='BULK-ABC123',
            expiration_datetime=None
        )
        updated_data = self._serialize_course(self.course, [verified_mode], None)
        # don't include the verification_deadline key in the PUT request
        updated_data.pop('verification_deadline', None)

        response = self.client.put(self.path, json.dumps(updated_data), content_type=JSON_CONTENT_TYPE)

        assert response.status_code == 200
        assert VerificationDeadline.deadline_for_course(self.course.id) == verification_deadline

    def test_remove_upgrade_deadline(self):
        """
        Verify that course mode upgrade deadlines can be removed through the API.
        """
        # First create a deadline
        upgrade_deadline = datetime.now(pytz.utc) + timedelta(days=1)
        response, __ = self._get_update_response_and_expected_data(upgrade_deadline, None)
        assert response.status_code == 200
        verified_mode = CourseMode.verified_mode_for_course(self.course.id)
        assert verified_mode is not None
        assert verified_mode.expiration_datetime.date() == upgrade_deadline.date()

        # Now set the deadline to None
        response, __ = self._get_update_response_and_expected_data(None, None)
        assert response.status_code == 200

        updated_verified_mode = CourseMode.verified_mode_for_course(self.course.id)
        assert updated_verified_mode is not None
        assert updated_verified_mode.expiration_datetime is None

    def test_update_overwrite(self):
        """
        Verify that data submitted via PUT overwrites/deletes modes that are
        not included in the body of the request, EXCEPT the Masters mode,
        which it leaves alone.
        """
        existing_mode = self.course_mode
        existing_masters_mode = CourseMode.objects.create(
            course_id=self.course.id,
            mode_slug='masters',
            min_price=10000,
            currency='USD',
            sku='DEF456',
            bulk_sku='BULK-DEF456'
        )
        new_mode = CourseMode(
            course_id=self.course.id,
            mode_slug='credit',
            min_price=500,
            currency='USD',
            sku='ABC123',
            bulk_sku='BULK-ABC123'
        )

        path = reverse('commerce_api:v1:courses:retrieve_update', args=[str(self.course.id)])
        data = json.dumps(self._serialize_course(self.course, [new_mode]))
        response = self.client.put(path, data, content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 200

        # Check modes list in response, disregarding its order.
        expected_dict = self._serialize_course(self.course, [new_mode])
        expected_items = expected_dict['modes']
        actual_items = json.loads(response.content.decode('utf-8'))['modes']
        self.assertCountEqual(actual_items, expected_items)

        # The existing non-Masters CourseMode should have been removed.
        assert not CourseMode.objects.filter(id=existing_mode.id).exists()

        # The existing Masters course mode should remain.
        assert CourseMode.objects.filter(id=existing_masters_mode.id).exists()

    @ddt.data(*itertools.product(
        ('honor', 'audit', 'verified', 'professional', 'no-id-professional'),
        (NOW, None),
    ))
    @ddt.unpack
    def test_update_professional_expiration(self, mode_slug, expiration_datetime_name):
        """ Verify that pushing a mode with a professional certificate and an expiration datetime
        will be rejected (this is not allowed). """
        expiration_datetime = self.DATES[expiration_datetime_name]
        mode = self._serialize_course_mode(
            CourseMode(
                mode_slug=mode_slug,
                min_price=500,
                currency='USD',
                sku='ABC123',
                bulk_sku='BULK-ABC123',
                expiration_datetime=expiration_datetime
            )
        )
        course_id = str(self.course.id)
        payload = {'id': course_id, 'modes': [mode]}
        path = reverse('commerce_api:v1:courses:retrieve_update', args=[course_id])

        expected_status = 400 if CourseMode.is_professional_slug(mode_slug) and expiration_datetime is not None else 200
        response = self.client.put(path, json.dumps(payload), content_type=JSON_CONTENT_TYPE)
        assert response.status_code == expected_status

    def assert_can_create_course(self, **request_kwargs):
        """ Verify a course can be created by the view. """
        course = CourseFactory.create()
        expected_modes = [
            CourseMode(
                mode_slug='verified',
                min_price=150,
                currency='USD',
                sku='ABC123',
                bulk_sku='BULK-ABC123'
            ),
            CourseMode(
                mode_slug='honor',
                min_price=0,
                currency='USD',
                sku='DEADBEEF',
                bulk_sku='BULK-DEADBEEF'
            )
        ]
        expected = self._serialize_course(course, expected_modes)
        path = reverse('commerce_api:v1:courses:retrieve_update', args=[str(course.id)])

        response = self.client.put(path, json.dumps(expected), content_type=JSON_CONTENT_TYPE, **request_kwargs)
        assert response.status_code == 201

        actual = json.loads(response.content.decode('utf-8'))
        assert actual == expected

        # Verify the display names are correct
        course_modes = CourseMode.objects.filter(course_id=course.id)
        actual = [course_mode.mode_display_name for course_mode in course_modes]
        self.assertListEqual(actual, ['Verified Certificate', 'Honor Certificate'])

    def test_create_with_permissions(self):
        """ Verify the view supports creating a course as a user with the appropriate permissions. """
        permissions = Permission.objects.filter(name__in=('Can add course mode', 'Can change course mode'))
        for permission in permissions:
            self.user.user_permissions.add(permission)

        self.assert_can_create_course()

    @override_settings(EDX_API_KEY='edx')
    def test_create_with_api_key(self):
        """ Verify the view supports creating a course when authenticated with the API header key. """
        self.client.logout()
        self.assert_can_create_course(HTTP_X_EDX_API_KEY=settings.EDX_API_KEY)

    def test_create_with_non_existent_course(self):
        """ Verify the API does not allow data to be created for courses that do not exist. """

        permissions = Permission.objects.filter(name__in=('Can add course mode', 'Can change course mode'))
        for permission in permissions:
            self.user.user_permissions.add(permission)

        expected_modes = [
            CourseMode(
                mode_slug=CourseMode.DEFAULT_MODE_SLUG,
                min_price=150, currency='USD',
                sku='ABC123',
                bulk_sku='BULK-ABC123'
            )
        ]

        course_key = 'non/existing/key'

        course_dict = {
            'id': str(course_key),
            'name': 'Non Existing Course',
            'verification_deadline': None,
            'modes': [self._serialize_course_mode(mode) for mode in expected_modes]
        }

        path = reverse('commerce_api:v1:courses:retrieve_update', args=[str(course_key)])

        response = self.client.put(path, json.dumps(course_dict), content_type=JSON_CONTENT_TYPE)
        assert response.status_code == 400

        expected_dict = {
            'id': [
                'Course {} does not exist.'.format(
                    course_key
                )
            ]
        }
        self.assertDictEqual(expected_dict, json.loads(response.content.decode('utf-8')))


class OrderViewTests(UserMixin, TestCase):
    """ Tests for the basket order view. """
    view_name = 'commerce_api:v1:orders:detail'
    ORDER_NUMBER = 'EDX-100001'
    MOCK_ORDER = {'number': ORDER_NUMBER}
    path = reverse_lazy(view_name, kwargs={'number': ORDER_NUMBER})

    def setUp(self):
        super().setUp()
        self._login()

    def test_order_found(self):
        """ If the order is located, the view should pass the data from the API. """
        with mock_order_endpoint(order_number=self.ORDER_NUMBER, response=self.MOCK_ORDER):
            response = self.client.get(self.path)

        assert response.status_code == 200
        actual = json.loads(response.content.decode('utf-8'))
        assert actual == self.MOCK_ORDER

    def test_order_not_found(self):
        """ If the order is not found, the view should return a 404. """
        with mock_order_endpoint(order_number=self.ORDER_NUMBER, status=404):
            response = self.client.get(self.path)
        assert response.status_code == 404

    def test_login_required(self):
        """ The view should return 401 if the user is not logged in. """
        self.client.logout()
        response = self.client.get(self.path)
        assert response.status_code == 401
