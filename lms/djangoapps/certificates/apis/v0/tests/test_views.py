"""
Tests for the Certificate REST APIs.
"""


from unittest.mock import patch

import ddt
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.apis.v0.views import CertificatesDetailView, CertificatesListView
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_authn.tests.utils import JWT_AUTH_TYPES, AuthAndScopesTestMixin, AuthType
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


@ddt.ddt
class CertificatesDetailRestApiTest(AuthAndScopesTestMixin, SharedModuleStoreTestCase, APITestCase):
    """
    Test for the Certificates REST APIs
    """
    now = timezone.now()
    default_scopes = CertificatesDetailView.required_scopes

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edx',
            number='verified',
            display_name='Verified Course'
        )
        CourseOverviewFactory.create(
            id=cls.course.id,
            display_org_with_default='edx',
            display_name='Verified Course',
            cert_html_view_enabled=True,
            self_paced=True,
        )

    def setUp(self):
        freezer = freeze_time(self.now)
        freezer.start()
        self.addCleanup(freezer.stop)

        super().setUp()

        self.cert = GeneratedCertificateFactory.create(
            user=self.student,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
            download_url='www.google.com',
            grade="0.88"
        )

        self.namespaced_url = 'certificates_api:v0:certificates:detail'

    def get_url(self, username):
        """ This method is required by AuthAndScopesTestMixin. """
        return reverse(
            self.namespaced_url,
            kwargs={
                'course_id': self.course.id,
                'username': username
            }
        )

    def assert_success_response_for_student(self, response):
        """ This method is required by AuthAndScopesTestMixin. """
        assert response.data ==\
               {'username': self.student.username,
                'status': CertificateStatuses.downloadable,
                'is_passing': True,
                'grade': '0.88',
                'download_url': 'www.google.com',
                'certificate_type': CourseMode.VERIFIED,
                'course_id': str(self.course.id),
                'created_date': self.now}

    def test_no_certificate(self):
        student_no_cert = UserFactory.create(password=self.user_password)
        resp = self.get_response(
            AuthType.session,
            requesting_user=student_no_cert,
            requested_user=student_no_cert,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert 'error_code' in resp.data
        assert resp.data['error_code'] == 'no_certificate_for_user'

    def test_no_certificate_configuration(self):
        """
        Verify that certificate is not returned if there is no active
        certificate configuration.
        """
        self.cert.download_url = ''
        self.cert.save()
        resp = self.get_response(
            AuthType.session,
            requesting_user=self.student,
            requested_user=self.student,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert 'error_code' in resp.data
        assert resp.data['error_code'] == 'no_certificate_configuration_for_course'


@ddt.ddt
class CertificatesListRestApiTest(AuthAndScopesTestMixin, SharedModuleStoreTestCase, APITestCase):
    """
    Test for the Certificates REST APIs
    """
    now = timezone.now()
    default_scopes = CertificatesListView.required_scopes

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            org='edx',
            number='verified',
            display_name='Verified Course',
            self_paced=True,
        )
        cls.course_overview = CourseOverviewFactory.create(
            id=cls.course.id,
            display_org_with_default='edx',
            display_name='Verified Course',
            cert_html_view_enabled=True,
            self_paced=True,
        )

    def setUp(self):
        freezer = freeze_time(self.now)
        freezer.start()
        self.addCleanup(freezer.stop)

        super().setUp()

        self.cert = GeneratedCertificateFactory.create(
            user=self.student,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
            download_url='www.google.com',
            grade="0.88",
        )
        self.student.is_staff = True
        self.student.save()
        self.namespaced_url = 'certificates_api:v0:certificates:list'

    def get_url(self, username):
        """ This method is required by AuthAndScopesTestMixin. """
        return reverse(
            self.namespaced_url,
            kwargs={
                'username': username
            }
        )

    def assert_success_response_for_student(self, response, download_url='www.google.com'):
        """ This method is required by AuthAndScopesTestMixin. """
        assert response.data == [
            {
                'username': self.student.username,
                'course_id': str(self.course.id),
                'course_display_name': self.course.display_name,
                'course_organization': self.course.org,
                'certificate_type': CourseMode.VERIFIED,
                'created_date': self.now,
                'modified_date': self.now,
                'status': CertificateStatuses.downloadable,
                'is_passing': True,
                'download_url': download_url,
                'grade': '0.88',
                'uuid': str(self.cert.verify_uuid)
            }
        ]

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*list(AuthType))
    def test_another_user(self, auth_type, mock_log):
        """
        Returns 403 response for non-staff user on all auth types.
        """
        resp = self.get_response(auth_type, requesting_user=self.other_student)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(*list(AuthType))
    def test_another_user_with_certs_shared_public(self, auth_type):
        """
        Returns 200 with cert list for OAuth, Session, and JWT auth.
        Returns 200 for jwt_restricted and user:me filter unset.
        """
        self.student.profile.year_of_birth = 1977
        self.student.profile.save()
        UserPreferenceFactory.build(
            user=self.student,
            key='account_privacy',
            value='all_users',
        ).save()

        resp = self.get_response(auth_type, requesting_user=self.global_staff)

        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert 'uuid' in resp.data[0]

    def test_owner_can_access_its_certs(self):
        """
        Tests the owner of the certs can access the certificate list api
        """
        self.student.profile.year_of_birth = 1977
        self.student.profile.save()
        UserPreferenceFactory.build(
            user=self.student,
            key='visibility.course_certificates',
            value='private',
        ).save()

        resp = self.get_response(AuthType.session, requesting_user=self.student)
        assert resp.status_code == status.HTTP_200_OK
        assert 'uuid' in resp.data[0]

        # verifies that other than owner cert list api is not accessible
        resp = self.get_response(AuthType.session, requesting_user=self.other_student)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_public_profile_certs_is_accessible(self):
        """
        Tests the public profile certs can be accessed by all users
        """
        self.student.profile.year_of_birth = 1977
        self.student.profile.save()
        UserPreferenceFactory.build(
            user=self.student,
            key='visibility.course_certificates',
            value='all_users',
        ).save()

        resp = self.get_response(AuthType.session, requesting_user=self.student)
        assert resp.status_code == status.HTTP_200_OK
        assert 'uuid' in resp.data[0]

        resp = self.get_response(AuthType.session, requesting_user=self.other_student)
        assert resp.status_code == status.HTTP_200_OK
        assert 'uuid' in resp.data[0]

        resp = self.get_response(AuthType.session, requesting_user=self.global_staff)
        assert resp.status_code == status.HTTP_200_OK
        assert 'uuid' in resp.data[0]

    @ddt.data(*list(AuthType))
    def test_another_user_with_certs_shared_custom(self, auth_type):
        """
        Returns 200 with cert list for OAuth, Session, and JWT auth.
        Returns 200 for jwt_restricted and user:me filter unset.
        """
        self.student.profile.year_of_birth = 1977
        self.student.profile.save()
        UserPreferenceFactory.build(
            user=self.student,
            key='account_privacy',
            value='custom',
        ).save()
        UserPreferenceFactory.build(
            user=self.student,
            key='visibility.course_certificates',
            value='all_users',
        ).save()

        resp = self.get_response(auth_type, requesting_user=self.global_staff)

        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert 'uuid' in resp.data[0]

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_on_behalf_of_other_user(self, auth_type, mock_log):
        """ Returns 403 when scopes are enforced with JwtHasUserFilterForRequestedUser. """
        jwt_token = self._create_jwt_token(self.global_staff, auth_type, include_me_filter=True)
        resp = self.get_response(AuthType.jwt, token=jwt_token)

        if auth_type == AuthType.jwt_restricted:
            assert resp.status_code == status.HTTP_403_FORBIDDEN
            self._assert_in_log("JwtHasUserFilterForRequestedUser", mock_log.warning)
        else:
            assert resp.status_code == status.HTTP_200_OK
            assert len(resp.data) == 1
            assert 'uuid' in resp.data[0]

    @patch('edx_rest_framework_extensions.permissions.log')
    @ddt.data(*JWT_AUTH_TYPES)
    def test_jwt_no_filter(self, auth_type, mock_log):
        assert True  # pylint: disable=redundant-unittest-assert

    def test_no_certificate(self):
        student_no_cert = UserFactory.create(password=self.user_password)
        resp = self.get_response(
            AuthType.session,
            requesting_user=self.global_staff,
            requested_user=student_no_cert,
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == []

    def test_query_counts(self):
        # Test student with no certificates
        student_no_cert = UserFactory.create(password=self.user_password)
        with self.assertNumQueries(20, table_ignorelist=WAFFLE_TABLES):
            resp = self.get_response(
                AuthType.jwt,
                requesting_user=self.global_staff,
                requested_user=student_no_cert,
            )
            assert resp.status_code == status.HTTP_200_OK
            assert len(resp.data) == 0

        # Test student with 1 certificate
        with self.assertNumQueries(13, table_ignorelist=WAFFLE_TABLES):
            resp = self.get_response(
                AuthType.jwt,
                requesting_user=self.global_staff,
                requested_user=self.student,
            )
            assert resp.status_code == status.HTTP_200_OK
            assert len(resp.data) == 1

        # Test student with 2 certificates
        student_2_certs = UserFactory.create(password=self.user_password)
        course = CourseFactory.create(
            org='edx',
            number='test',
            display_name='Test Course',
            self_paced=True,
        )
        CourseOverviewFactory.create(
            id=course.id,
            display_org_with_default='edx',
            display_name='Test Course',
            cert_html_view_enabled=True,
            self_paced=True,
        )
        GeneratedCertificateFactory.create(
            user=student_2_certs,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
            download_url='www.google.com',
            grade="0.88",
        )
        GeneratedCertificateFactory.create(
            user=student_2_certs,
            course_id=course.id,
            status=CertificateStatuses.downloadable,
            mode='verified',
            download_url='www.google.com',
            grade="0.88",
        )
        with self.assertNumQueries(13, table_ignorelist=WAFFLE_TABLES):
            resp = self.get_response(
                AuthType.jwt,
                requesting_user=self.global_staff,
                requested_user=student_2_certs,
            )
            assert resp.status_code == status.HTTP_200_OK
            assert len(resp.data) == 2

    @patch.dict(settings.FEATURES, {'CERTIFICATES_HTML_VIEW': True})
    def test_with_no_certificate_configuration(self):
        """
        Verify that certificates are not returned until there is an active
        certificate configuration.
        """
        self.cert.download_url = ''
        self.cert.save()

        response = self.get_response(
            AuthType.jwt,
            requesting_user=self.global_staff,
            requested_user=self.student,
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

        self.course_overview.has_any_active_web_certificate = True
        self.course_overview.save()

        response = self.get_response(
            AuthType.jwt,
            requesting_user=self.global_staff,
            requested_user=self.student,
        )
        kwargs = {"certificate_uuid": self.cert.verify_uuid}
        expected_download_url = reverse('certificates:render_cert_by_uuid', kwargs=kwargs)
        self.assert_success_response_for_student(response, download_url=expected_download_url)

    @patch('openedx.core.djangoapps.content.course_overviews.api.get_course_run_details')
    def test_certificate_without_course(self, mock_get_course_run_details):
        """
        Verify that certificates are returned for deleted XML courses.
        """
        expected_course_name = 'Test Course Title'
        mock_get_course_run_details.return_value = {'title': expected_course_name}
        xml_course_key = self.store.make_course_key('edX', 'testDeletedCourse', '2020')
        cert_for_deleted_course = GeneratedCertificateFactory.create(
            user=self.student,
            course_id=xml_course_key,
            status=CertificateStatuses.downloadable,
            mode='honor',
            download_url='www.edx.org/honor-cert-for-deleted-course.pdf',
            grade="0.88"
        )

        response = self.get_response(
            AuthType.jwt,
            requesting_user=self.global_staff,
            requested_user=self.student,
        )
        assert response.status_code == status.HTTP_200_OK
        self.assertContains(response, cert_for_deleted_course.download_url)
        self.assertContains(response, expected_course_name)
        assert 'uuid' in response.data[0]
