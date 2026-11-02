"""
Tests for credit app views.
"""


import datetime
import json

import ddt
from zoneinfo import ZoneInfo
from django.conf import settings
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.credit.models import (
    CreditCourse,
    CreditProvider,
    CreditRequest,
    CreditRequirement,
    CreditRequirementStatus
)
from openedx.core.djangoapps.credit.serializers import CreditEligibilitySerializer, CreditProviderSerializer
from openedx.core.djangoapps.credit.signature import signature
from openedx.core.djangoapps.credit.tests.factories import (
    CreditCourseFactory,
    CreditEligibilityFactory,
    CreditProviderFactory,
    CreditRequestFactory
)
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import ApplicationFactory, AccessTokenFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from common.djangoapps.util.date_utils import to_timestamp

JSON = 'application/json'


class ApiTestCaseMixin:
    """ Mixin to aid with API testing. """

    def assert_error_response(self, response, msg, status_code=400):
        """ Validate the response's status and detail message. """
        assert response.status_code == status_code
        self.assertDictEqual(response.data, {'detail': msg})


class UserMixin:
    """ Test mixin that creates, and authenticates, a new user for every test. """
    password = 'password'
    list_path = None

    def setUp(self):
        super().setUp()

        # This value must be set here, as setting it outside of a method results in issues with CMS/Studio tests.
        if self.list_path:
            self.path = reverse(self.list_path)

        # Create a user and login, so that we can use session auth for the
        # tests that aren't specifically testing authentication or authorization.
        self.user = UserFactory(password=self.password, is_staff=True)
        self.client.login(username=self.user.username, password=self.password)


class AuthMixin:
    """ Test mixin with methods to test OAuth 2.0 and session authentication. """

    def test_authentication_required(self):
        """ Verify the endpoint requires authentication. """
        self.client.logout()
        response = self.client.get(self.path)
        assert response.status_code == 401

    def test_oauth(self):
        """ Verify the endpoint supports authentication via OAuth 2.0. """
        access_token = AccessTokenFactory(user=self.user, application=ApplicationFactory()).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }
        self.client.logout()
        response = self.client.get(self.path, **headers)
        assert response.status_code == 200

    def test_session_auth(self):
        """ Verify the endpoint supports authentication via session. """
        self.client.logout()
        self.client.login(username=self.user.username, password=self.password)
        response = self.client.get(self.path)
        assert response.status_code == 200

    def test_jwt_auth(self):
        """ verify the endpoints JWT authentication. """
        token = create_jwt_for_user(self.user)
        headers = {
            'HTTP_AUTHORIZATION': 'JWT ' + token
        }
        self.client.logout()
        response = self.client.get(self.path, **headers)
        assert response.status_code == 200


@ddt.ddt
class ReadOnlyMixin:
    """ Test mixin for read-only API endpoints. """

    @ddt.data('delete', 'post', 'put')
    def test_readonly(self, method):
        """ Verify the viewset does not allow CreditProvider objects to be created or modified. """
        response = getattr(self.client, method)(self.path)
        assert response.status_code == 405


@skip_unless_lms
class CreditCourseViewSetTests(AuthMixin, UserMixin, TestCase):
    """ Tests for the CreditCourse endpoints.

     GET/POST /api/v1/credit/creditcourse/
     GET/PUT  /api/v1/credit/creditcourse/:course_id/
    """
    list_path = 'credit:creditcourse-list'

    def _serialize_credit_course(self, credit_course):
        """ Serializes a CreditCourse to a Python dict. """

        return {
            'course_key': str(credit_course.course_key),
            'enabled': credit_course.enabled
        }

    def test_session_auth(self):
        """ Verify the endpoint supports session authentication, and only allows authorization for staff users. """
        user = UserFactory(password=self.password, is_staff=False)
        self.client.login(username=user.username, password=self.password)

        # Non-staff users should not have access to the API
        response = self.client.get(self.path)
        assert response.status_code == 403

        # Staff users should have access to the API
        user.is_staff = True
        user.save()
        response = self.client.get(self.path)
        assert response.status_code == 200

    def test_session_auth_post_requires_csrf_token(self):
        """ Verify non-GET requests require a CSRF token be attached to the request. """
        user = UserFactory(password=self.password, is_staff=True)
        client = Client(enforce_csrf_checks=True)
        assert client.login(username=user.username, password=self.password)

        data = {
            'course_key': 'a/b/c',
            'enabled': True
        }

        # POSTs without a CSRF token should fail.
        response = client.post(self.path, data=json.dumps(data), content_type=JSON)
        self.assertContains(response, 'CSRF', status_code=403)

        # Retrieve a CSRF token
        response = client.get('/')
        csrf_token = response.cookies[settings.CSRF_COOKIE_NAME].value
        assert len(csrf_token) > 0

        # Ensure POSTs made with the token succeed.
        response = client.post(self.path, data=json.dumps(data), content_type=JSON, HTTP_X_CSRFTOKEN=csrf_token)
        assert response.status_code == 201

    def test_oauth(self):
        """ Verify the endpoint supports OAuth, and only allows authorization for staff users. """
        client = Client()  # avoid mixing cookies
        user = UserFactory(is_staff=False)
        oauth_client = ApplicationFactory.create()
        access_token = AccessTokenFactory.create(user=user, application=oauth_client).token
        headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + access_token
        }

        # Non-staff users should not have access to the API
        response = client.get(self.path, **headers)
        assert response.status_code == 403

        # Staff users should have access to the API
        user.is_staff = True
        user.save()
        response = client.get(self.path, **headers)
        assert response.status_code == 200

    def assert_course_created(self, course_id, response):
        """ Verify an API request created a new CreditCourse object. """
        enabled = True
        data = {
            'course_key': str(course_id),
            'enabled': enabled
        }

        assert response.status_code == 201

        # Verify the API returns the serialized CreditCourse
        self.assertDictEqual(json.loads(response.content.decode('utf-8')), data)

        # Verify the CreditCourse was actually created
        course_key = CourseKey.from_string(course_id)
        assert CreditCourse.objects.filter(course_key=course_key, enabled=enabled).exists()

    def test_create(self):
        """ Verify the endpoint supports creating new CreditCourse objects. """
        course_id = 'a/b/c'
        enabled = True
        data = {
            'course_key': str(course_id),
            'enabled': enabled
        }

        response = self.client.post(self.path, data=json.dumps(data), content_type=JSON)
        self.assert_course_created(course_id, response)

    def test_put_as_create(self):
        """ Verify the update endpoint supports creating a new CreditCourse object. """
        course_id = 'd/e/f'
        enabled = True
        data = {
            'course_key': str(course_id),
            'enabled': enabled
        }

        path = reverse('credit:creditcourse-detail', args=[course_id])
        response = self.client.put(path, data=json.dumps(data), content_type=JSON)
        self.assert_course_created(course_id, response)

    def test_get(self):
        """ Verify the endpoint supports retrieving CreditCourse objects. """
        course_id = 'a/b/c'
        cc1 = CreditCourse.objects.create(course_key=CourseKey.from_string(course_id))
        path = reverse('credit:creditcourse-detail', args=[course_id])

        response = self.client.get(path)
        assert response.status_code == 200

        # Verify the API returns the serialized CreditCourse
        self.assertDictEqual(json.loads(response.content.decode('utf-8')), self._serialize_credit_course(cc1))

    def test_list(self):
        """ Verify the endpoint supports listing all CreditCourse objects. """
        cc1 = CreditCourse.objects.create(course_key=CourseKey.from_string('a/b/c'))
        cc2 = CreditCourse.objects.create(course_key=CourseKey.from_string('d/e/f'), enabled=True)
        expected = [self._serialize_credit_course(cc1), self._serialize_credit_course(cc2)]

        response = self.client.get(self.path)
        assert response.status_code == 200

        # Verify the API returns a list of serialized CreditCourse objects
        self.assertListEqual(json.loads(response.content.decode('utf-8')), expected)

    def test_update(self):
        """ Verify the endpoint supports updating a CreditCourse object. """
        course_id = 'course-v1:edX+BlendedX+1T2015'
        credit_course = CreditCourse.objects.create(course_key=CourseKey.from_string(course_id), enabled=False)
        assert not credit_course.enabled

        path = reverse('credit:creditcourse-detail', args=[course_id])
        data = {'course_key': course_id, 'enabled': True}
        response = self.client.put(path, json.dumps(data), content_type=JSON)
        assert response.status_code == 200

        # Verify the serialized CreditCourse is returned
        self.assertDictEqual(json.loads(response.content.decode('utf-8')), data)

        # Verify the data was persisted
        credit_course = CreditCourse.objects.get(course_key=credit_course.course_key)
        assert credit_course.enabled


@ddt.ddt
@skip_unless_lms
class CreditProviderViewSetTests(ApiTestCaseMixin, ReadOnlyMixin, AuthMixin, UserMixin, TestCase):
    """ Tests for CreditProviderViewSet. """
    list_path = 'credit:creditprovider-list'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bayside = CreditProviderFactory(provider_id='bayside')
        cls.hogwarts = CreditProviderFactory(provider_id='hogwarts')
        cls.starfleet = CreditProviderFactory(provider_id='starfleet')

    def test_list(self):
        """ Verify the endpoint returns a list of all CreditProvider objects. """
        response = self.client.get(self.path)
        assert response.status_code == 200

        expected = CreditProviderSerializer(CreditProvider.objects.all(), many=True).data
        assert response.data == expected

    @ddt.data(
        ('bayside',),
        ('hogwarts', 'starfleet')
    )
    def test_list_filtering(self, provider_ids):
        """ Verify the endpoint returns a list of all CreditProvider objects, filtered to contain only those objects
        associated with the given IDs. """
        url = '{}?provider_ids={}'.format(self.path, ','.join(provider_ids))
        response = self.client.get(url)
        assert response.status_code == 200

        expected = CreditProviderSerializer(CreditProvider.objects.filter(provider_id__in=provider_ids),
                                            many=True).data
        assert response.data == expected

    def test_retrieve(self):
        """ Verify the endpoint returns the details for a single CreditProvider. """
        url = reverse('credit:creditprovider-detail', kwargs={'provider_id': self.bayside.provider_id})
        response = self.client.get(url)
        assert response.status_code == 200
        assert response.data == CreditProviderSerializer(self.bayside).data


@skip_unless_lms
class CreditProviderRequestCreateViewTests(ApiTestCaseMixin, UserMixin, TestCase):
    """ Tests for CreditProviderRequestCreateView. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = CreditProviderFactory()

    def setUp(self):
        super().setUp()
        self.path = reverse('credit:create_request', kwargs={'provider_id': self.provider.provider_id})
        self.eligibility = CreditEligibilityFactory(username=self.user.username)

    def post_credit_request(self, username, course_id):
        """ Create a credit request for the given user and course. """
        data = {
            'username': username,
            'course_key': str(course_id)
        }
        return self.client.post(self.path, json.dumps(data), content_type=JSON)

    def test_post_with_provider_integration(self):
        """ Verify the endpoint can create a new credit request. """
        username = self.user.username
        course = self.eligibility.course
        course_key = course.course_key
        final_grade = 0.95

        # Enable provider integration
        self.provider.enable_integration = True
        self.provider.save()

        # Add a single credit requirement (final grade)
        requirement = CreditRequirement.objects.create(
            course=course,
            namespace='grade',
            name='grade',
        )

        # Mark the user as having satisfied the requirement and eligible for credit.
        CreditRequirementStatus.objects.create(
            username=username,
            requirement=requirement,
            status='satisfied',
            reason={'final_grade': final_grade}
        )

        secret_key = 'secret'
        # Provider keys can be stored as a string or list of strings
        secret_key_with_key_as_string = {self.provider.provider_id: secret_key}
        # The None represents a key that was not ascii encodable
        secret_key_with_key_as_list = {
            self.provider.provider_id: [secret_key, None]
        }

        for secret_key_dict in [secret_key_with_key_as_string, secret_key_with_key_as_list]:
            with override_settings(CREDIT_PROVIDER_SECRET_KEYS=secret_key_dict):
                response = self.post_credit_request(username, course_key)
            assert response.status_code == 200

            # Check that the user's request status is pending
            request = CreditRequest.objects.get(username=username, course__course_key=course_key)
            assert request.status == 'pending'

            # Check request parameters
            content = json.loads(response.content.decode('utf-8'))
            parameters = content['parameters']

            assert content['url'] == self.provider.provider_url
            assert content['method'] == 'POST'
            assert len(parameters['request_uuid']) == 32
            assert parameters['course_org'] == course_key.org
            assert parameters['course_num'] == course_key.course
            assert parameters['course_run'] == course_key.run
            assert parameters['final_grade'] == str(final_grade)
            assert parameters['user_username'] == username
            assert parameters['user_full_name'] == self.user.get_full_name()
            assert parameters['user_mailing_address'] == ''
            assert parameters['user_country'] == ''

            # The signature is going to change each test run because the request
            # is assigned a different UUID each time.
            # For this reason, we use the signature function directly
            # (the "signature" parameter will be ignored when calculating the signature).
            # Other unit tests verify that the signature function is working correctly.
            assert parameters['signature'] == signature(parameters, secret_key)

    def test_post_invalid_provider(self):
        """ Verify the endpoint returns HTTP 404 if the credit provider is not valid. """
        path = reverse('credit:create_request', kwargs={'provider_id': 'fake'})
        response = self.client.post(path, {})
        assert response.status_code == 404

    def test_post_no_username(self):
        """ Verify the endpoint returns HTTP 400 if no username is supplied. """
        response = self.post_credit_request(None, 'a/b/c')
        self.assert_error_response(response, 'A username must be specified.')

    def test_post_invalid_course_key(self):
        """ Verify the endpoint returns HTTP 400 if the course is not a valid course key. """
        course_key = 'not-a-course-id'
        response = self.post_credit_request(self.user.username, course_key)
        self.assert_error_response(response, f'[{course_key}] is not a valid course key.')

    def test_post_user_not_eligible(self):
        """ Verify the endpoint returns HTTP 400 if the user is not eligible for credit for the course. """
        credit_course = CreditCourseFactory()
        username = 'ineligible-user'
        course_key = credit_course.course_key

        response = self.post_credit_request(username, course_key)
        msg = '[{username}] is not eligible for credit for [{course_key}].'.format(username=username,
                                                                                   course_key=course_key)
        self.assert_error_response(response, msg)

    def test_post_permissions_staff(self):
        """ Verify staff users can create requests for any user. """
        admin = AdminFactory(password=self.password)
        self.client.logout()
        self.client.login(username=admin.username, password=self.password)
        response = self.post_credit_request(self.user.username, self.eligibility.course.course_key)
        assert response.status_code == 200

    def test_post_other_user(self):
        """ Verify non-staff users cannot create requests for other users. """
        user = UserFactory(password=self.password)
        self.client.logout()
        self.client.login(username=user.username, password=self.password)
        response = self.post_credit_request(self.user.username, self.eligibility.course.course_key)
        assert response.status_code == 403

    def test_post_no_provider_integration(self):
        """ Verify the endpoint returns the provider URL if provider integration is not enabled. """
        response = self.post_credit_request(self.user.username, self.eligibility.course.course_key)
        assert response.status_code == 200
        expected = {
            'url': self.provider.provider_url,
            'method': 'GET',
            'parameters': {},
        }
        assert response.data == expected

    def test_post_secret_key_not_set(self):
        """ Verify the endpoint returns HTTP 400 if we attempt to create a
        request for a provider with no secret key set. """
        # Enable provider integration
        self.provider.enable_integration = True
        self.provider.save()

        # Cannot initiate a request because we cannot sign it
        with override_settings(CREDIT_PROVIDER_SECRET_KEYS={}):
            response = self.post_credit_request(self.user.username, self.eligibility.course.course_key)
        assert response.status_code == 400

    def test_post_secret_key_not_set_key_as_list(self):
        """ Verify the endpoint returns HTTP 400 if we attempt to create a
        request for a provider with no secret key set with keys set as list. """
        # Enable provider integration
        self.provider.enable_integration = True
        self.provider.save()

        # Cannot initiate a request because we cannot sign it
        secret_key_with_key_as_list = {
            self.provider.provider_id: []
        }
        with override_settings(CREDIT_PROVIDER_SECRET_KEYS=secret_key_with_key_as_list):
            response = self.post_credit_request(self.user.username, self.eligibility.course.course_key)
        assert response.status_code == 400


@ddt.ddt
@skip_unless_lms
class CreditProviderCallbackViewTests(UserMixin, TestCase):
    """ Tests for CreditProviderCallbackView. """

    def setUp(self):
        super().setUp()

        # Authentication should NOT be required for this endpoint.
        self.client.logout()

        self.provider = CreditProviderFactory()
        self.path = reverse('credit:provider_callback', args=[self.provider.provider_id])
        self.eligibility = CreditEligibilityFactory(username=self.user.username)

    def _credit_provider_callback(self, request_uuid, status, **kwargs):
        """
        Simulate a response from the credit provider approving
        or rejecting the credit request.

        Arguments:
            request_uuid (str): The UUID of the credit request.
            status (str): The status of the credit request.

        Keyword Arguments:
            provider_id (str): Identifier for the credit provider.
            secret_key (str): Shared secret key for signing messages.
            timestamp (datetime): Timestamp of the message.
            sig (str): Digital signature to use on messages.
            keys (dict): Override for CREDIT_PROVIDER_SECRET_KEYS setting.

        """
        provider_id = kwargs.get('provider_id', self.provider.provider_id)
        secret_key = kwargs.get('secret_key', '931433d583c84ca7ba41784bad3232e6')
        timestamp = kwargs.get('timestamp', to_timestamp(datetime.datetime.now(ZoneInfo("UTC"))))
        keys = kwargs.get('keys', {self.provider.provider_id: secret_key})

        url = reverse('credit:provider_callback', args=[provider_id])

        parameters = {
            'request_uuid': request_uuid,
            'status': status,
            'timestamp': timestamp,
        }
        parameters['signature'] = kwargs.get('sig', signature(parameters, secret_key))

        with override_settings(CREDIT_PROVIDER_SECRET_KEYS=keys):
            return self.client.post(url, data=json.dumps(parameters), content_type=JSON)

    def _create_credit_request_and_get_uuid(self, username=None, course_key=None):
        """ Initiate a request for credit and return the request UUID. """
        username = username or self.user.username
        course = CreditCourse.objects.get(course_key=course_key) if course_key else self.eligibility.course
        credit_request = CreditRequestFactory(username=username, course=course, provider=self.provider)
        return credit_request.uuid

    def _assert_request_status(self, uuid, expected_status):
        """ Check the status of a credit request. """
        request = CreditRequest.objects.get(uuid=uuid)
        assert request.status == expected_status

    def test_post_invalid_provider_id(self):
        """ Verify the endpoint returns HTTP 404 if the provider does not exist. """
        provider_id = 'fakey-provider'
        assert not CreditProvider.objects.filter(provider_id=provider_id).exists()

        path = reverse('credit:provider_callback', args=[provider_id])
        response = self.client.post(path, {})
        assert response.status_code == 404

    def test_post_with_invalid_signature(self):
        """ Verify the endpoint returns HTTP 403 if a request is received with an invalid signature. """
        request_uuid = self._create_credit_request_and_get_uuid()

        # Simulate a callback from the credit provider with an invalid signature
        # Since the signature is invalid, we respond with a 403 Not Authorized.
        response = self._credit_provider_callback(request_uuid, "approved", sig="invalid")
        assert response.status_code == 403

    @ddt.data(
        -datetime.timedelta(0, 60 * 15 + 1),
        'invalid'
    )
    def test_post_with_invalid_timestamp(self, timedelta):
        """ Verify HTTP 400 is returned for requests with an invalid timestamp. """
        if timedelta == 'invalid':
            timestamp = timedelta
        else:
            timestamp = to_timestamp(datetime.datetime.now(ZoneInfo("UTC")) + timedelta)
        request_uuid = self._create_credit_request_and_get_uuid()
        response = self._credit_provider_callback(request_uuid, 'approved', timestamp=timestamp)
        assert response.status_code == 400

    def test_post_with_string_timestamp(self):
        """ Verify the endpoint supports timestamps transmitted as strings instead of integers. """
        request_uuid = self._create_credit_request_and_get_uuid()
        timestamp = str(to_timestamp(datetime.datetime.now(ZoneInfo("UTC"))))
        response = self._credit_provider_callback(request_uuid, 'approved', timestamp=timestamp)
        assert response.status_code == 200

    def test_credit_provider_callback_is_idempotent(self):
        """ Verify clients can make subsequent calls with the same status. """
        request_uuid = self._create_credit_request_and_get_uuid()

        # Initially, the status should be "pending"
        self._assert_request_status(request_uuid, "pending")

        # First call sets the status to approved
        self._credit_provider_callback(request_uuid, 'approved')
        self._assert_request_status(request_uuid, "approved")

        # Second call succeeds as well; status is still approved
        self._credit_provider_callback(request_uuid, 'approved')
        self._assert_request_status(request_uuid, "approved")

    def test_credit_provider_invalid_status(self):
        """ Verify requests with an invalid status value return HTTP 400. """
        request_uuid = self._create_credit_request_and_get_uuid()
        response = self._credit_provider_callback(request_uuid, 'invalid')
        assert response.status_code == 400

    def test_request_associated_with_another_provider(self):
        """ Verify the endpoint returns HTTP 404 if a request is received for the incorrect provider. """
        other_provider_id = 'other-provider'
        other_provider_secret_key = '1d01f067a5a54b0b8059f7095a7c636d'

        # Create an additional credit provider
        CreditProvider.objects.create(provider_id=other_provider_id, enable_integration=True)

        # Initiate a credit request with the first provider
        request_uuid = self._create_credit_request_and_get_uuid()

        # Attempt to update the request status for a different provider
        response = self._credit_provider_callback(
            request_uuid,
            'approved',
            provider_id=other_provider_id,
            secret_key=other_provider_secret_key,
            keys={other_provider_id: other_provider_secret_key}
        )

        # Response should be a 404 to avoid leaking request UUID values to other providers.
        assert response.status_code == 404

        # Request status should still be 'pending'
        self._assert_request_status(request_uuid, 'pending')

    def test_credit_provider_key_not_configured(self):
        """ Verify the endpoint returns HTTP 403 if the provider has no key configured. """
        request_uuid = self._create_credit_request_and_get_uuid()

        # Callback from the provider is not authorized, because the shared secret isn't configured.
        with override_settings(CREDIT_PROVIDER_SECRET_KEYS={}):
            response = self._credit_provider_callback(request_uuid, 'approved', keys={})
            assert response.status_code == 403


@ddt.ddt
@skip_unless_lms
class CreditEligibilityViewTests(AuthMixin, UserMixin, ReadOnlyMixin, TestCase):
    """ Tests for CreditEligibilityView. """
    view_name = 'credit:eligibility_details'

    def setUp(self):
        super().setUp()
        self.eligibility = CreditEligibilityFactory(username=self.user.username)
        self.path = self.create_url(self.eligibility)

    def create_url(self, eligibility):
        """ Returns a URL that can be used to view eligibility data. """
        return '{path}?username={username}&course_key={course_key}'.format(
            path=reverse(self.view_name),
            username=eligibility.username,
            course_key=eligibility.course.course_key
        )

    def assert_valid_get_response(self, eligibility):
        """ Ensure the endpoint returns the correct eligibility data. """
        url = self.create_url(eligibility)
        response = self.client.get(url)

        assert response.status_code == 200
        self.assertListEqual(response.data, CreditEligibilitySerializer([eligibility], many=True).data)

    def test_get(self):
        """ Verify the endpoint returns eligibility information for the give user and course. """
        self.assert_valid_get_response(self.eligibility)

    def test_get_with_missing_parameters(self):
        """ Verify the endpoint returns HTTP status 400 if either the username or course_key querystring argument
        is not provided. """
        response = self.client.get(reverse(self.view_name))
        assert response.status_code == 400
        self.assertDictEqual(response.data,
                             {'detail': 'Both the course_key and username querystring parameters must be supplied.'})

    def test_get_with_invalid_course_key(self):
        """ Verify the endpoint returns HTTP status 400 if the provided course_key is not an actual CourseKey. """
        url = f'{reverse(self.view_name)}?username=edx&course_key=a'
        response = self.client.get(url)
        assert response.status_code == 400
        self.assertDictEqual(response.data, {'detail': '[a] is not a valid course key.'})

    def test_staff_can_view_all(self):
        """ Verify that staff users can view eligibility data for all users. """
        staff = AdminFactory(password=self.password)
        self.client.logout()
        self.client.login(username=staff.username, password=self.password)
        self.assert_valid_get_response(self.eligibility)

    def test_nonstaff_can_only_view_own_data(self):
        """ Verify that non-staff users can only view their own eligibility data. """
        user = UserFactory(password=self.password)
        eligibility = CreditEligibilityFactory(username=user.username)
        url = self.create_url(eligibility)

        # Verify user can view own data
        self.client.logout()
        self.client.login(username=user.username, password=self.password)
        response = self.client.get(url)
        assert response.status_code == 200

        # User should not be able to view data for other users.
        alt_user = UserFactory(password=self.password)
        alt_eligibility = CreditEligibilityFactory(username=alt_user.username)
        url = self.create_url(alt_eligibility)
        response = self.client.get(url)
        assert response.status_code == 403
