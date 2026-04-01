"""Tests for the certificates panel of the instructor dash. """


import contextlib
import io
import json
from unittest import mock

import ddt
import pytest
from config_models.models import cache
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import GlobalStaffFactory
from common.djangoapps.student.tests.factories import InstructorFactory
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates import api as certs_api
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import (
    CertificateAllowlistFactory,
    CertificateInvalidationFactory,
    GeneratedCertificateFactory
)
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class CertificateTaskViewTests(SharedModuleStoreTestCase):
    """Tests for the certificate panel of the instructor dash. """

    @classmethod
    def setUpClass(cls):
        """
        Set up the test class with a test course and instructor dashboard URL.
        """
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.url = reverse(
            'instructor_dashboard',
            kwargs={'course_id': str(cls.course.id)}
        )

    def setUp(self):
        """
        Set up test users and enable certificate generation configuration.
        """
        super().setUp()
        self.user = UserFactory.create()
        self.global_staff = GlobalStaffFactory()
        self.instructor = InstructorFactory(course_key=self.course.id)

        # Need to clear the cache for model-based configuration
        cache.clear()

        # Enable the certificate generation feature
        certs_api.set_certificate_generation_config(enabled=True)

    def _login_as(self, role):
        """
        Log in the test client as the specified user role.
        """
        user_map = {
            "user": self.user.username,
            "instructor": self.instructor.username,
            "global_staff": self.global_staff.username
        }
        self.client.login(username=user_map.get(role, "user"), password=self.TEST_PASSWORD)

    def _get_url(self, action):
        """
        Build the unified certificate task URL for the given action.
        """
        return reverse("certificate_task", kwargs={"course_id": self.course.id, "action": action})

    def _assert_redirects_to_instructor_dash(self, response):
        """Check that the response redirects to the certificates section. """
        expected_redirect = reverse(
            'instructor_dashboard',
            kwargs={'course_id': str(self.course.id)}
        )
        expected_redirect += '#view-certificates'
        self.assertRedirects(response, expected_redirect)

    @ddt.data(True, False)
    def test_certificate_generation_enable(self, is_enabled):
        """
        Test enabling or disabling self-generated certificates as global staff.
        """
        self._login_as("global_staff")

        params = {"certificates-enabled": "true" if is_enabled else "false"}
        response = self.client.post(
            self._get_url("toggle"),
            data=params
        )

        # Expect a redirect back to the instructor dashboard
        self._assert_redirects_to_instructor_dash(response)

        # Expect that certificate generation is now enabled for the course
        actual_enabled = certs_api.has_self_generated_certificates_enabled(str(self.course.id))
        assert is_enabled == actual_enabled

    @ddt.data("user", "instructor", "global_staff")
    def test_certificate_generation(self, role):
        """
        Test permission-based access to certificate generation by role.
        """
        self._login_as(role)
        response = self.client.post(self._get_url("generate"))
        actual_status_code = {
            "user": 403,
            "instructor": 200,
            "global_staff": 200
        }
        assert response.status_code == actual_status_code[role]

    @ddt.data(
        ("downloadable", 200, True, 'Certificate regeneration task has been started. You can view '
         'the status of the generation task in the "Pending Tasks" section.'),
        ("generating", 400, False, 'Please select certificate statuses that lie with '
         'in "certificate_statuses" entry in POST data.')
    )
    @ddt.unpack
    def test_certificate_regeneration_status_handling(self, cert_status, expected_status, success, expected_message):
        """
        Test certificate regeneration with valid and invalid certificate statuses.
        """
        # Create a certificate with the given status
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=cert_status,
            mode='honor'
        )

        self._login_as("global_staff")
        response = self.client.post(
            self._get_url("regenerate"),
            data={'certificate_statuses': [cert_status]},
        )

        assert response.status_code == expected_status
        res_json = response.json()
        assert res_json.get('success', False) is success
        assert res_json.get('message') == expected_message


@ddt.ddt
class CertificatesInstructorDashTest(SharedModuleStoreTestCase):
    """Tests for the certificate panel of the instructor dash. """

    ERROR_REASON = "An error occurred!"
    DOWNLOAD_URL = "https://www.example.com/abcd123/cert.pdf"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.url = reverse(
            'instructor_dashboard',
            kwargs={'course_id': str(cls.course.id)}
        )

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.instructor = InstructorFactory(course_key=self.course.id)

        # Need to clear the cache for model-based configuration
        cache.clear()

        # Enable the certificate generation feature
        certs_api.set_certificate_generation_config(enabled=True)

    def test_visible_only_to_global_staff(self):
        # Instructors don't see the certificates section
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self._assert_certificates_visible(False)

        # Global staff can see the certificates section
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        self._assert_certificates_visible(True)

    def test_visible_only_when_feature_flag_enabled(self):
        # Disable the feature flag
        certs_api.set_certificate_generation_config(enabled=False)
        cache.clear()

        # Now even global staff can't see the certificates section
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        self._assert_certificates_visible(False)

    @mock.patch.dict(settings.FEATURES, {'ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE': True})
    def test_visible_for_instructors_when_feature_is_enabled(self):
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self._assert_certificates_visible(True)

    @ddt.data("started", "error", "success")
    def test_show_certificate_status(self, certificate_status):
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        with self._certificate_status("honor", certificate_status):
            self._assert_certificate_status("honor", certificate_status)

    def test_show_enabled_button(self):
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)

        # Initially, no example certs are generated, so
        # the enable button should be disabled
        self._assert_enable_certs_button_is_disabled()

        with self._certificate_status("honor", "success"):
            # Certs are disabled for the course, so the enable button should be shown
            self._assert_enable_certs_button(True)

            # Enable certificates for the course
            certs_api.set_cert_generation_enabled(self.course.id, True)

            # Now the "disable" button should be shown
            self._assert_enable_certs_button(False)

    def test_can_disable_even_after_failure(self):
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)

        with self._certificate_status("honor", "error"):
            # When certs are disabled for a course, then don't allow them
            # to be enabled if certificate generation doesn't complete successfully
            certs_api.set_cert_generation_enabled(self.course.id, False)
            self._assert_enable_certs_button_is_disabled()

            # However, if certificates are already enabled, allow them
            # to be disabled even if an error has occurred
            certs_api.set_cert_generation_enabled(self.course.id, True)
            self._assert_enable_certs_button(False)

    @mock.patch.dict(settings.FEATURES, {'CERTIFICATES_HTML_VIEW': True})
    def test_show_enabled_button_for_html_certs(self):
        """
        Tests `Enable Student-Generated Certificates` button is enabled
        and `Generate Example Certificates` button is not available if
        course has Web/HTML certificates view enabled.
        """
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.store.update_item(self.course, self.global_staff.id)
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        self.assertContains(response, 'Enable Student-Generated Certificates')
        self.assertContains(response, 'enable-certificates-submit')
        self.assertNotContains(response, 'Generate Example Certificates')

    @mock.patch.dict(settings.FEATURES, {
        'CERTIFICATES_HTML_VIEW': True,
        'CERTIFICATES_INSTRUCTOR_GENERATION': False
    }
    )
    def test_buttons_for_html_certs_in_self_paced_course(self):
        """
        Tests `Enable Student-Generated Certificates` button is enabled
        and `Generate Certificates` button is not available if
        course has Web/HTML certificates view enabled on a self paced course.
        """
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.store.update_item(self.course, self.global_staff.id)
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        self.assertContains(response, 'Enable Student-Generated Certificates')
        self.assertContains(response, 'enable-certificates-submit')
        self.assertNotContains(response, 'Generate Certificates')
        self.assertNotContains(response, 'btn-start-generating-certificates')

    def _assert_certificates_visible(self, is_visible):
        """Check that the certificates section is visible on the instructor dash. """
        response = self.client.get(self.url)
        if is_visible:
            self.assertContains(response, "Student-Generated Certificates")
        else:
            self.assertNotContains(response, "Student-Generated Certificates")

    @contextlib.contextmanager
    def _certificate_status(self, description, certificate_status):
        """Configure the certificate status by mocking the certificates API. """
        patched = 'lms.djangoapps.instructor.views.instructor_dashboard.certs_api.example_certificates_status'
        with mock.patch(patched) as certs_api_status:
            cert_status = [{
                'description': description,
                'status': certificate_status
            }]

            if certificate_status == 'error':
                cert_status[0]['error_reason'] = self.ERROR_REASON
            if certificate_status == 'success':
                cert_status[0]['download_url'] = self.DOWNLOAD_URL

            certs_api_status.return_value = cert_status
            yield

    def _assert_certificate_status(self, cert_name, expected_status):
        """Check the certificate status display on the instructor dash. """
        response = self.client.get(self.url)

        if expected_status == 'started':
            expected = f'Generating example {cert_name} certificate'
            self.assertContains(response, expected)
        elif expected_status == 'error':
            expected = self.ERROR_REASON
            self.assertContains(response, expected)
        elif expected_status == 'success':
            expected = self.DOWNLOAD_URL
            self.assertContains(response, expected)
        else:
            self.fail(f"Invalid certificate status: {expected_status}")

    def _assert_enable_certs_button_is_disabled(self):
        """Check that the "enable student-generated certificates" button is disabled. """
        response = self.client.get(self.url)
        expected_html = '<button class="is-disabled" disabled>Enable Student-Generated Certificates</button>'
        self.assertContains(response, expected_html)

    def _assert_enable_certs_button(self, is_enabled):
        """Check whether the button says "enable" or "disable" cert generation. """
        response = self.client.get(self.url)
        expected_html = (
            'Enable Student-Generated Certificates' if is_enabled
            else 'Disable Student-Generated Certificates'
        )
        self.assertContains(response, expected_html)


@override_settings(CERT_QUEUE='certificates')
@ddt.ddt
class CertificatesInstructorApiTest(SharedModuleStoreTestCase):
    """Tests for the certificates end-points in the instructor dash API. """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.user = UserFactory()
        CourseEnrollment.enroll(self.user, self.course.id)

        # Enable certificate generation
        cache.clear()
        certs_api.set_certificate_generation_config(enabled=True)

    @ddt.data('enable_certificate_generation')
    def test_allow_only_global_staff(self, url_name):
        url = reverse(url_name, kwargs={'course_id': self.course.id})

        # Instructors do not have access
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        response = self.client.post(url)
        assert response.status_code == 302

        # Global staff have access
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        response = self.client.post(url)
        assert response.status_code == 302

    @ddt.data(True, False)
    def test_enable_certificate_generation(self, is_enabled):
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        url = reverse(
            'enable_certificate_generation',
            kwargs={'course_id': str(self.course.id)}
        )
        params = {'certificates-enabled': 'true' if is_enabled else 'false'}
        response = self.client.post(url, data=params)

        # Expect a redirect back to the instructor dashboard
        self._assert_redirects_to_instructor_dash(response)

        # Expect that certificate generation is now enabled for the course
        actual_enabled = certs_api.has_self_generated_certificates_enabled(self.course.id)
        assert is_enabled == actual_enabled

    def _assert_redirects_to_instructor_dash(self, response):
        """Check that the response redirects to the certificates section. """
        expected_redirect = reverse(
            'instructor_dashboard',
            kwargs={'course_id': str(self.course.id)}
        )
        expected_redirect += '#view-certificates'
        self.assertRedirects(response, expected_redirect)

    def test_certificate_generation_api_without_global_staff(self):
        """
        Test certificates generation api endpoint returns permission denied if
        user who made the request is not member of global staff.
        """
        user = UserFactory.create()
        self.client.login(username=user.username, password=self.TEST_PASSWORD)
        url = reverse(
            'start_certificate_generation',
            kwargs={'course_id': str(self.course.id)}
        )

        response = self.client.post(url)
        assert response.status_code == 403

        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        response = self.client.post(url)
        assert response.status_code == 200

    def test_certificate_generation_api_with_global_staff(self):
        """
        Test certificates generation api endpoint returns success status when called with
        valid course key
        """
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        url = reverse(
            'start_certificate_generation',
            kwargs={'course_id': str(self.course.id)}
        )

        response = self.client.post(url)
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json['message'] is not None
        assert res_json['task_id'] is not None

    def test_certificate_regeneration_success(self):
        """
        Test certificate regeneration is successful when accessed with 'certificate_statuses'
        present in GeneratedCertificate table.
        """

        # Create a generated Certificate of some user with status 'downloadable'
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor'
        )

        # Login the client and access the url with 'certificate_statuses'
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        url = reverse('start_certificate_regeneration', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, data={'certificate_statuses': [CertificateStatuses.downloadable]})

        # Assert 200 status code in response
        assert response.status_code == 200
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert request is successful
        assert res_json['success']

        # Assert success message
        assert res_json['message'] ==\
            'Certificate regeneration task has been started.' \
            ' You can view the status of the generation task in the "Pending Tasks" section.'

    def test_certificate_regeneration_error(self):
        """
        Test certificate regeneration errors out when accessed with either empty list of 'certificate_statuses' or
        the 'certificate_statuses' that are not present in GeneratedCertificate table.
        """
        # Create a dummy course and GeneratedCertificate with the same status as the one we will use to access
        # 'start_certificate_regeneration' but their error message should be displayed as GeneratedCertificate
        # belongs to a different course
        dummy_course = CourseFactory.create()
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=dummy_course.id,
            status=CertificateStatuses.generating,
            mode='honor'
        )

        # Login the client and access the url without 'certificate_statuses'
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)
        url = reverse('start_certificate_regeneration', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url)

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Error Message
        assert res_json['message'] ==\
            'Please select certificate statuses from the list only.'

        # Access the url passing 'certificate_statuses' that are not present in db
        url = reverse('start_certificate_regeneration', kwargs={'course_id': str(self.course.id)})
        response = self.client.post(url, data={'certificate_statuses': [CertificateStatuses.generating]})

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Error Message
        assert (res_json['message'] ==
                'Please select certificate statuses from the list only.')


@override_settings(CERT_QUEUE='certificates')
@ddt.ddt
class CertificateExceptionViewInstructorApiTest(SharedModuleStoreTestCase):
    """Tests for the generate certificates end-points in the instructor dash API. """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.user = UserFactory()
        self.user2 = UserFactory()
        CourseEnrollment.enroll(self.user, self.course.id)
        CourseEnrollment.enroll(self.user2, self.course.id)
        self.url = reverse('certificate_exception_view', kwargs={'course_id': str(self.course.id)})

        certificate_allowlist_item = CertificateAllowlistFactory.create(
            user=self.user2,
            course_id=self.course.id,
        )

        self.certificate_exception = dict(
            created="",
            notes="Test Notes for Test Certificate Exception",
            user_email='',
            user_id='',
            user_name=str(self.user.username)
        )

        self.certificate_exception_in_db = dict(
            id=certificate_allowlist_item.id,
            user_name=certificate_allowlist_item.user.username,
            notes=certificate_allowlist_item.notes,
            user_email=certificate_allowlist_item.user.email,
            user_id=certificate_allowlist_item.user.id,
        )

        # Enable certificate generation
        cache.clear()
        certs_api.set_certificate_generation_config(enabled=True)
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)

    def test_certificate_exception_added_successfully(self):
        """
        Test certificates exception addition api endpoint returns success status and updated certificate exception data
        when called with valid course key and certificate exception data
        """
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json'
        )
        # Assert successful request processing
        assert response.status_code == 200
        certificate_exception = json.loads(response.content.decode('utf-8'))

        # Assert Certificate Exception Updated data
        assert certificate_exception['user_email'] == self.user.email
        assert certificate_exception['user_name'] == self.user.username
        assert certificate_exception['user_id'] == self.user.id

    def test_certificate_exception_invalid_username_error(self):
        """
        Test certificates exception addition api endpoint returns failure when called with
        invalid username.
        """
        invalid_user = 'test_invalid_user_name'
        self.certificate_exception.update({'user_name': invalid_user})
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json'
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request not successful
        assert not res_json['success']

        # Assert Error Message
        assert res_json['message'] == f'{invalid_user} does not exist in the LMS. Please check your spelling and retry.'

    def test_certificate_exception_missing_username_and_email_error(self):
        """
        Test certificates exception addition api endpoint returns failure when called with
        missing username/email.
        """
        self.certificate_exception.update({'user_name': '', 'user_email': ''})
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json'
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request not successful
        assert not res_json['success']

        # Assert Error Message
        assert res_json['message'] == {'user': ['This field may not be blank.']}

    def test_certificate_exception_duplicate_user_error(self):
        """
        Ensure the certificates exception endpoint returns failure when called with
        username/email that already exists on the certificate allowlist.
        """
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception_in_db),
            content_type='application/json'
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request not successful
        assert not res_json['success']

        user = self.certificate_exception_in_db['user_name']
        # Assert Error Message
        assert res_json['message'] == f'Student (username/email={user}) already in certificate exception list.'

    def test_certificate_exception_same_user_in_two_different_courses(self):
        """
        Test certificates exception addition api endpoint in scenario when same
        student is added to two different courses.
        """
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json'
        )
        assert response.status_code == 200
        certificate_exception = json.loads(response.content.decode('utf-8'))

        # Assert Certificate Exception Updated data
        assert certificate_exception['user_email'] == self.user.email
        assert certificate_exception['user_name'] == self.user.username
        assert certificate_exception['user_id'] == self.user.id

        course2 = CourseFactory.create()
        url_course2 = reverse(
            'certificate_exception_view',
            kwargs={'course_id': str(course2.id)}
        )

        # add certificate exception for same user in a different course
        self.client.post(
            url_course2,
            data=json.dumps(self.certificate_exception),
            content_type='application/json'
        )

        assert response.status_code == 200
        certificate_exception = json.loads(response.content.decode('utf-8'))

        # Assert Certificate Exception Updated data
        assert certificate_exception['user_email'] == self.user.email
        assert certificate_exception['user_name'] == self.user.username
        assert certificate_exception['user_id'] == self.user.id

    def test_certificate_exception_user_not_enrolled_error(self):
        """
        Test certificates exception addition api endpoint returns failure when called with
        username/email that is not enrolled in the given course.
        """
        # Un-enroll student from the course
        CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json'
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request not successful
        assert not res_json['success']

        # Assert Error Message
        assert res_json['message'] == (
            f"Student {self.user.username} is not enrolled in this course. Please check your spelling and retry."
        )

    def test_certificate_exception_removed_successfully(self):
        """
        Test certificates exception removal api endpoint returns success status
        when called with valid course key and certificate exception id
        """
        GeneratedCertificateFactory.create(
            user=self.user2,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            grade='1.0'
        )
        # Verify that certificate exception exists
        assert certs_api.is_on_allowlist(self.user2, self.course.id)

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception_in_db),
            content_type='application/json',
            REQUEST_METHOD='DELETE'
        )
        # Assert successful request processing
        assert response.status_code == 204

        # Verify that certificate exception does not exist
        assert not certs_api.is_on_allowlist(self.user2, self.course.id)

    def test_certificate_exception_removed_successfully_form_url(self):
        """
        In case of deletion front-end is sending content-type x-www-form-urlencoded.
        Just to handle that some logic added in api and this test is for that part.
        Test certificates exception removal api endpoint returns success status
        when called with valid course key and certificate exception id
        """
        GeneratedCertificateFactory.create(
            user=self.user2,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            grade='1.0'
        )
        # Verify that certificate exception exists
        assert certs_api.is_on_allowlist(self.user2, self.course.id)

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception_in_db),
            content_type='application/x-www-form-urlencoded',
            REQUEST_METHOD='DELETE'
        )
        # Assert successful request processing
        assert response.status_code == 204

        # Verify that certificate exception does not exist
        assert not certs_api.is_on_allowlist(self.user2, self.course.id)

    def test_remove_certificate_exception_invalid_request_error(self):
        """
        Test certificates exception removal api endpoint returns error
        when called without certificate exception id
        """
        # Try to delete certificate exception without passing valid data
        response = self.client.post(
            self.url,
            data='Test Invalid data',
            content_type='application/json',
            REQUEST_METHOD='DELETE'
        )
        # Assert error on request
        assert response.status_code == 400

        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request not successful
        assert not res_json['success']
        # Assert Error Message
        assert res_json['message'] ==\
            'The record is not in the correct format. Please add a valid username or email address.'

    def test_remove_certificate_exception_non_existing_error(self):
        """
        Test certificates exception removal api endpoint returns error
        when called with non existing certificate exception id
        """
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json',
            REQUEST_METHOD='DELETE'
        )
        # Assert error on request
        assert response.status_code == 400

        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request not successful
        assert not res_json['success']
        # Assert Error Message
        assert res_json['message'] == (
            f"Error occurred removing the allowlist entry for student {self.user.username}. Please refresh the page "
            "and try again"
        )

    def test_certificate_invalidation_already_exists(self):
        """
        Test to confirm an error message is raised when generating a certificate exception for a learner that already
        has an active certificate invalidation.
        """
        # generate a certificate for the test learner in our course
        generated_certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor',
        )

        # create a certificate invalidation tied to the generated certificate
        CertificateInvalidationFactory.create(
            generated_certificate=generated_certificate,
            invalidated_by=self.global_staff,
        )

        # attempt to add learner to the allowlist, expect an error
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_exception),
            content_type='application/json',
            REQUEST_METHOD='POST'
        )

        res_json = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 400
        assert res_json['message'] == (
            f"Student {self.user.username} is already on the certificate invalidation list and cannot be added to "
            "the certificate exception list."
        )


@override_settings(CERT_QUEUE='certificates')
@ddt.ddt
class GenerateCertificatesInstructorApiTest(SharedModuleStoreTestCase):
    """Tests for the generate certificates end-points in the instructor dash API. """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.user = UserFactory()
        CourseEnrollment.enroll(self.user, self.course.id)
        certificate_exception = CertificateAllowlistFactory.create(
            user=self.user,
            course_id=self.course.id,
        )

        self.certificate_exception = dict(
            id=certificate_exception.id,
            user_name=certificate_exception.user.username,
            notes=certificate_exception.notes,
            user_email=certificate_exception.user.email,
            user_id=certificate_exception.user.id,
        )

        # Enable certificate generation
        cache.clear()
        certs_api.set_certificate_generation_config(enabled=True)
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)

    def test_generate_certificate_exceptions_all_students(self):
        """
        Test generate certificates exceptions api endpoint returns success
        when called with existing certificate exception
        """
        url = reverse(
            'generate_certificate_exceptions',
            kwargs={'course_id': str(self.course.id), 'generate_for': 'all'}
        )

        response = self.client.post(
            url,
            content_type='application/json'
        )
        # Assert Success
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request is successful
        assert res_json['success']
        # Assert Message
        assert res_json['message'] == 'Certificate generation started for students on the allowlist.'

    def test_generate_certificate_exceptions_allowlist_not_generated(self):
        """
        Test generate certificates exceptions api endpoint returns success
        when calling with new certificate exception.
        """
        url = reverse(
            'generate_certificate_exceptions',
            kwargs={'course_id': str(self.course.id), 'generate_for': 'new'}
        )

        response = self.client.post(
            url,
            content_type='application/json'
        )

        # Assert Success
        assert response.status_code == 200

        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request is successful
        assert res_json['success']
        # Assert Message
        assert res_json['message'] == 'Certificate generation started for students on the allowlist.'

    def test_generate_certificate_exceptions_generate_for_incorrect_value(self):
        """
        Test generate certificates exceptions api endpoint returns error
        when calling with generate_for without 'new' or 'all' value.
        """
        url = reverse(
            'generate_certificate_exceptions',
            kwargs={'course_id': str(self.course.id), 'generate_for': ''}
        )

        response = self.client.post(
            url,
            content_type='application/json'
        )

        # Assert Failure
        assert response.status_code == 400

        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Request is not successful
        assert not res_json['success']
        # Assert Message
        assert res_json['message'] == 'Invalid data, generate_for must be "new" or "all".'


@ddt.ddt
class TestCertificatesInstructorApiBulkAllowlist(SharedModuleStoreTestCase):
    """
    Test bulk certificates allowlist uploads from csv file
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.url = reverse('generate_bulk_certificate_exceptions',
                          kwargs={'course_id': cls.course.id})

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.enrolled_user_1_email = 'test_student1@example.com'
        self.enrolled_user_1 = UserFactory(
            username='TestStudent1',
            email=self.enrolled_user_1_email,
            first_name='Enrolled',
            last_name='Student'
        )
        self.enrolled_user_2_email = 'test_student2@example.com'
        self.enrolled_user_2 = UserFactory(
            username='TestStudent2',
            email=self.enrolled_user_2_email,
            first_name='Enrolled',
            last_name='Student'
        )

        self.not_enrolled_user_email = 'nonenrolled@test.com'
        self.not_enrolled_student = UserFactory(
            username='NotEnrolledStudent',
            email=self.not_enrolled_user_email,
            first_name='NotEnrolled',
            last_name='Student'
        )
        CourseEnrollment.enroll(self.enrolled_user_1, self.course.id)
        CourseEnrollment.enroll(self.enrolled_user_2, self.course.id)

        # Global staff can see the certificates section
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)

    def test_create_allowlist_exception_record(self):
        """
        Happy path test to create a single new allowlisted record
        """
        assert not certs_api.is_on_allowlist(self.enrolled_user_1, self.course.id)
        assert not certs_api.is_on_allowlist(self.enrolled_user_2, self.course.id)

        csv_content = b"test_student1@example.com,dummy_notes\n" \
                      b"test_student2@example.com,dummy_notes"
        data = self.upload_file(csv_content=csv_content)
        assert len(data['general_errors']) == 0
        assert len(data['row_errors']['data_format_error']) == 0
        assert len(data['row_errors']['user_not_exist']) == 0
        assert len(data['row_errors']['user_already_allowlisted']) == 0
        assert len(data['row_errors']['user_not_enrolled']) == 0
        assert len(data['success']) == 2

        assert certs_api.is_on_allowlist(self.enrolled_user_1, self.course.id)
        assert certs_api.is_on_allowlist(self.enrolled_user_2, self.course.id)

    def test_invalid_data_format_in_csv(self):
        """
        Try uploading a CSV file with invalid data formats and verify the errors.
        """
        csv_content = b"test_student1@example.com,test,1,USA\n" \
                      b"test_student2@example.com,test,1"

        data = self.upload_file(csv_content=csv_content)
        assert len(data['row_errors']['data_format_error']) == 2
        assert len(data['general_errors']) == 0
        assert len(data['success']) == 0

        assert not certs_api.is_on_allowlist(self.enrolled_user_1, self.course.id)
        assert not certs_api.is_on_allowlist(self.enrolled_user_2, self.course.id)

    def test_file_upload_type_not_csv(self):
        """
        Try uploading some non-CSV file e.g. .JPG file and verify that it is rejected
        """
        uploaded_file = SimpleUploadedFile("temp.jpg", io.BytesIO(b"some initial binary data: \x00\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['general_errors']) != 0
        assert data['general_errors'][0] ==\
            'Make sure that the file you upload is in CSV format with no extraneous characters or rows.'

    def test_bad_file_upload_type(self):
        """
        Try uploading CSV file with invalid binary data and verify that it is rejected
        """
        uploaded_file = SimpleUploadedFile("temp.csv", io.BytesIO(b"some initial binary data: \xC3\x01").read())
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['general_errors']) != 0
        assert data['general_errors'][0] == 'Could not read uploaded file.'

    def test_invalid_email_in_csv(self):
        """
        Test failure case of a poorly formatted email field
        """
        csv_content = b"test_student.example.com,dummy_notes"

        data = self.upload_file(csv_content=csv_content)
        assert len(data['row_errors']['user_not_exist']) == 1
        assert len(data['success']) == 0

    def test_csv_user_not_enrolled(self):
        """
        If the user is not enrolled in the course then there should be a user_not_enrolled error.
        """
        csv_content = b"nonenrolled@test.com,dummy_notes"

        data = self.upload_file(csv_content=csv_content)
        assert len(data['row_errors']['user_not_enrolled']) == 1
        assert len(data['general_errors']) == 0

        assert not certs_api.is_on_allowlist(self.not_enrolled_student, self.course.id)

    def test_certificate_exception_already_exist(self):
        """
        Test error if existing user is already in certificates exception list.
        """
        CertificateAllowlistFactory.create(user=self.enrolled_user_1, course_id=self.course.id)

        csv_content = b"test_student1@example.com,dummy_notes"
        data = self.upload_file(csv_content=csv_content)
        assert len(data['row_errors']['user_already_allowlisted']) == 1
        assert len(data['general_errors']) == 0
        assert len(data['success']) == 0

    def test_csv_file_not_attached(self):
        """
        Test when the user does not attach a file
        """
        csv_content = b"test_student1@example.com,dummy_notes\n" \
                      b"test_student2@example.com,dummy_notes"

        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)

        response = self.client.post(self.url, {'file_not_found': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data['general_errors']) == 1
        assert len(data['success']) == 0

    def test_certificate_invalidation_already_exists(self):
        """
        Test to confirm an error message is raised when generating a certificate exception for a learner appears in the
        CSV file who has an active certificate invalidation.
        """
        # generate a certificate for the test learner in our course
        generated_certificate = GeneratedCertificateFactory.create(
            user=self.enrolled_user_1,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor',
        )

        CertificateInvalidationFactory.create(
            generated_certificate=generated_certificate,
            invalidated_by=self.global_staff,
        )

        # attempt to add learner to the allowlist, expect an error
        csv_content = b"test_student1@example.com,notes"
        data = self.upload_file(csv_content=csv_content)
        assert len(data['row_errors']['user_on_certificate_invalidation_list']) == 1
        assert data['row_errors']['user_on_certificate_invalidation_list'][0] == 'user "TestStudent1" in row# 1'

    def upload_file(self, csv_content):
        """
        Upload a csv file.
        :return json data
        """
        uploaded_file = SimpleUploadedFile("temp.csv", csv_content)
        response = self.client.post(self.url, {'students_list': uploaded_file})
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        return data


@ddt.ddt
class CertificateInvalidationViewTests(SharedModuleStoreTestCase):
    """
    Test certificate invalidation view.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.url = reverse('certificate_invalidation_view',
                          kwargs={'course_id': cls.course.id})
        cls.notes = "Test notes."

    def setUp(self):
        super().setUp()
        self.global_staff = GlobalStaffFactory()
        self.enrolled_user_1 = UserFactory(
            username='TestStudent1',
            email='test_student1@example.com',
            first_name='Enrolled',
            last_name='Student',
        )
        self.enrolled_user_2 = UserFactory(
            username='TestStudent2',
            email='test_student2@example.com',
            first_name='Enrolled',
            last_name='Student',
        )

        self.not_enrolled_student = UserFactory(
            username='NotEnrolledStudent',
            email='nonenrolled@test.com',
            first_name='NotEnrolled',
            last_name='Student',
        )
        CourseEnrollment.enroll(self.enrolled_user_1, self.course.id)
        CourseEnrollment.enroll(self.enrolled_user_2, self.course.id)

        self.generated_certificate = GeneratedCertificateFactory.create(
            user=self.enrolled_user_1,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor',
        )

        self.certificate_invalidation_data = dict(
            user=self.enrolled_user_1.username,
            notes=self.notes,
        )

        # Global staff can see the certificates section
        self.client.login(username=self.global_staff.username, password=self.TEST_PASSWORD)

    def test_invalidate_certificate(self):
        """
        Test user can invalidate a generated certificate.
        """
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )
        # Assert successful request processing
        assert response.status_code == 200
        result = json.loads(response.content.decode('utf-8'))

        # Assert Certificate Exception Updated data
        assert result['user'] == self.enrolled_user_1.username
        assert result['invalidated_by'] == self.global_staff.username
        assert result['notes'] == self.notes

        # Verify that CertificateInvalidation record has been created in the database i.e. no DoesNotExist error
        try:
            cert_filter_args = {
                "generated_certificate": self.generated_certificate,
                "invalidated_by": self.global_staff,
                "notes": self.notes,
                "active": True
            }
            certs_api.get_certificate_invalidation_entry(**cert_filter_args)

        except ObjectDoesNotExist:
            self.fail("The certificate is not invalidated.")

        # Check if the generated certificate was invalidated
        generated_certificate = certs_api.get_certificate_for_user(self.enrolled_user_1, self.course.id, False)

        assert not generated_certificate.is_valid()

    def test_missing_username_and_email_error(self):
        """
        Test error message if user name or email is missing.
        """
        self.certificate_invalidation_data.update({'user': ''})
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Error Message
        assert res_json['message'] == {'user': ['This field may not be blank.']}

    def test_invalid_user_name_error(self):
        """
        Test error message if invalid user name is given.
        """
        invalid_user = "test_invalid_user_name"

        self.certificate_invalidation_data.update({"user": invalid_user})

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))
        # Assert Error Message
        assert res_json['message'] == f'{invalid_user} does not exist in the LMS. Please check your spelling and retry.'

    def test_no_generated_certificate_error(self):
        """
        Test error message if there is no generated certificate for the student.
        """
        self.certificate_invalidation_data.update({"user": self.enrolled_user_2.username})

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))
        # Assert Error Message
        assert res_json['message'] == f'The student {self.enrolled_user_2.username} does not have certificate for the course {self.course.number}. Kindly verify student username/email and the selected course are correct and try again.'  # pylint: disable=line-too-long

    def test_certificate_already_invalid_error(self):
        """
        Test error message if certificate for the student is already invalid.
        """
        # Invalidate user certificate
        self.generated_certificate.invalidate()

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Error Message
        assert res_json['message'] == f'Certificate for student {self.enrolled_user_1.username} is already invalid, kindly verify that certificate was generated for this student and then proceed.'  # pylint: disable=line-too-long

    def test_duplicate_certificate_invalidation_error(self):
        """
        Test error message if certificate invalidation for the student is already present.
        """
        CertificateInvalidationFactory.create(
            generated_certificate=self.generated_certificate,
            invalidated_by=self.global_staff,
        )
        # Invalidate user certificate
        self.generated_certificate.invalidate()

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Error Message
        assert res_json['message'] == f'Certificate of {self.enrolled_user_1.username} has already been invalidated. Please check your spelling and retry.'  # pylint: disable=line-too-long

    def test_remove_certificate_invalidation(self):
        """
        Test that user can remove certificate invalidation.
        """
        # Invalidate user certificate
        self.generated_certificate.invalidate()

        CertificateInvalidationFactory.create(
            generated_certificate=self.generated_certificate,
            invalidated_by=self.global_staff,
        )

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
            REQUEST_METHOD='DELETE'
        )

        # Assert 204 status code in response
        assert response.status_code == 204

        # Verify that certificate invalidation successfully removed from database

        with pytest.raises(ObjectDoesNotExist):
            certs_filter_args = {
                "generated_certificate": self.generated_certificate,
                "invalidated_by": self.global_staff,
                "active": True
            }

            cert_invalidation_entry = certs_api.get_certificate_invalidation_entry(**certs_filter_args)

            if cert_invalidation_entry is None:
                raise ObjectDoesNotExist

    def test_remove_certificate_invalidation_error(self):
        """
        Test error message if certificate invalidation does not exists.
        """
        # Invalidate user certificate
        self.generated_certificate.invalidate()

        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
            REQUEST_METHOD='DELETE'
        )

        # Assert 400 status code in response
        assert response.status_code == 400
        res_json = json.loads(response.content.decode('utf-8'))

        # Assert Error Message
        assert res_json['message'] == 'Certificate Invalidation does not exist, Please refresh the page and try again.'

    def test_learner_already_on_certificate_exception_list(self):
        """
        Test to make sure we don't allow a single to learner to appear on both the certificate exception and
        invalidation lists.
        """
        # add test learner to the allowlist
        CertificateAllowlistFactory.create(user=self.enrolled_user_1, course_id=self.course.id)

        # now try and add them to the invalidation list, expect an error
        response = self.client.post(
            self.url,
            data=json.dumps(self.certificate_invalidation_data),
            content_type='application/json',
        )

        res_json = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 400
        assert res_json['message'] == (
            f"The student {self.enrolled_user_1.username} appears on the Certificate Exception list in course "
            f"{self.course.id}. Please remove them from the Certificate Exception list before attempting to "
            "invalidate their certificate."
        )
