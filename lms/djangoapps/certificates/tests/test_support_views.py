"""
Tests for certificate app views used by the support team.
"""


import json
from unittest import mock
from uuid import uuid4

import ddt
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import GlobalStaff, SupportStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.models import CertificateInvalidation, CertificateStatuses, GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import CertificateInvalidationFactory, GeneratedCertificateFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

CAN_GENERATE_METHOD = 'lms.djangoapps.certificates.generation_handler._can_generate_regular_certificate'
FEATURES_WITH_CERTS_ENABLED = settings.FEATURES.copy()
FEATURES_WITH_CERTS_ENABLED['CERTIFICATES_HTML_VIEW'] = True


class CertificateSupportTestCase(ModuleStoreTestCase):
    """
    Base class for tests of the certificate support views.
    """

    SUPPORT_USERNAME = "support"
    SUPPORT_EMAIL = "support@example.com"
    SUPPORT_PASSWORD = "support"

    STUDENT_USERNAME = "student"
    STUDENT_EMAIL = "student@example.com"
    STUDENT_PASSWORD = "student"

    CERT_COURSE_KEY = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
    COURSE_NOT_EXIST_KEY = CourseKey.from_string("course-v1:test+TestX+Test_Course_Not_Exist")
    EXISTED_COURSE_KEY_1 = CourseKey.from_string("course-v1:test1+Test1X+Test_Course_Exist_1")
    EXISTED_COURSE_KEY_2 = CourseKey.from_string("course-v1:test2+Test2X+Test_Course_Exist_2")
    CERT_GRADE = 0.89
    CERT_STATUS = CertificateStatuses.downloadable
    CERT_MODE = "verified"
    CERT_DOWNLOAD_URL = "https://www.example.com/cert.pdf"

    def setUp(self):
        """
        Create a support team member and a student with a certificate.
        Log in as the support team member.
        """
        super().setUp()
        CourseFactory(
            org=CertificateSupportTestCase.EXISTED_COURSE_KEY_1.org,
            course=CertificateSupportTestCase.EXISTED_COURSE_KEY_1.course,
            run=CertificateSupportTestCase.EXISTED_COURSE_KEY_1.run,
        )

        # Create the support staff user
        self.support = UserFactory(
            username=self.SUPPORT_USERNAME,
            email=self.SUPPORT_EMAIL,
            password=self.SUPPORT_PASSWORD,
        )
        SupportStaffRole().add_users(self.support)

        # Create a student
        self.student = UserFactory(
            username=self.STUDENT_USERNAME,
            email=self.STUDENT_EMAIL,
            password=self.STUDENT_PASSWORD,
        )

        # Create certificates for the student
        self.cert = GeneratedCertificateFactory(
            user=self.student,
            course_id=self.CERT_COURSE_KEY,
            grade=self.CERT_GRADE,
            status=self.CERT_STATUS,
            mode=self.CERT_MODE,
            download_url=self.CERT_DOWNLOAD_URL,
            verify_uuid=uuid4().hex
        )

        # Login as support staff
        success = self.client.login(username=self.SUPPORT_USERNAME, password=self.SUPPORT_PASSWORD)
        assert success, "Couldn't log in as support staff"


@ddt.ddt
class CertificateSearchTests(CertificateSupportTestCase):
    """
    Tests for the certificate search end-point used by the support team.
    """

    def setUp(self):
        """
        Create a course
        """
        super().setUp()
        self.course = CourseFactory(
            org=self.CERT_COURSE_KEY.org,
            course=self.CERT_COURSE_KEY.course,
            run=self.CERT_COURSE_KEY.run,
        )
        self.course.cert_html_view_enabled = True
        self.course_key = self.course.id  # pylint: disable=no-member

        # Course certificate configurations
        certificates = [
            {
                'id': 1,
                'name': 'Name 1',
                'description': 'Description 1',
                'course_title': 'course_title_1',
                'signatories': [],
                'version': 1,
                'is_active': True
            }
        ]

        self.course.certificates = {'certificates': certificates}
        self.course.save()  # pylint: disable=no-member
        self.store.update_item(self.course, self.user.id)
        self.course_overview = CourseOverviewFactory(
            id=self.course_key,
            cert_html_view_enabled=True,
        )

    @ddt.data(
        (GlobalStaff, True),
        (SupportStaffRole, True),
        (None, False),
    )
    @ddt.unpack
    def test_access_control(self, role, has_access):
        # Create a user and log in
        user = UserFactory(username="foo", password="foo")
        success = self.client.login(username="foo", password="foo")
        assert success, 'Could not log in'

        # Assign the user to the role
        if role is not None:
            role().add_users(user)

        # Retrieve the page
        response = self._search("foo")

        if has_access:
            self.assertContains(response, json.dumps([]))
        else:
            assert response.status_code == 403

    @ddt.data(
        (CertificateSupportTestCase.STUDENT_USERNAME, True),
        (CertificateSupportTestCase.STUDENT_EMAIL, True),
        ("bar", False),
        ("bar@example.com", False),
        ("", False),
        (CertificateSupportTestCase.STUDENT_USERNAME, False, 'invalid_key'),
        (CertificateSupportTestCase.STUDENT_USERNAME, False,
            str(CertificateSupportTestCase.COURSE_NOT_EXIST_KEY)),
        (CertificateSupportTestCase.STUDENT_USERNAME, True,
            str(CertificateSupportTestCase.EXISTED_COURSE_KEY_1)),
    )
    @ddt.unpack
    def test_search(self, user_filter, expect_result, course_filter=None):
        response = self._search(user_filter, course_filter)
        if expect_result:
            assert response.status_code == 200
            results = json.loads(response.content.decode('utf-8'))
            assert len(results) == 1
        else:
            assert response.status_code == 400

    def test_search_with_plus_sign(self):
        """
        Test that email address that contains '+' accepted by student support
        """
        self.student.email = "student+student@example.com"
        self.student.save()

        response = self._search(self.student.email)
        assert response.status_code == 200
        results = json.loads(response.content.decode('utf-8'))

        assert len(results) == 1
        retrieved_data = results[0]
        assert retrieved_data['username'] == self.STUDENT_USERNAME

    def test_results(self):
        response = self._search(self.STUDENT_USERNAME)
        assert response.status_code == 200
        results = json.loads(response.content.decode('utf-8'))

        assert len(results) == 1
        retrieved_cert = results[0]

        assert retrieved_cert['username'] == self.STUDENT_USERNAME
        assert retrieved_cert['course_key'] == str(self.CERT_COURSE_KEY)
        assert retrieved_cert['created'] == self.cert.created_date.isoformat()
        assert retrieved_cert['modified'] == self.cert.modified_date.isoformat()
        assert retrieved_cert['grade'] == str(self.CERT_GRADE)
        assert retrieved_cert['status'] == self.CERT_STATUS
        assert retrieved_cert['type'] == self.CERT_MODE
        assert retrieved_cert['download_url'] == self.CERT_DOWNLOAD_URL
        assert not retrieved_cert['regenerate']

    @override_settings(FEATURES=FEATURES_WITH_CERTS_ENABLED)
    def test_download_link(self):
        self.cert.course_id = self.course_key
        self.cert.download_url = ''
        self.cert.save()

        response = self._search(self.STUDENT_USERNAME)
        assert response.status_code == 200
        results = json.loads(response.content.decode('utf-8'))

        assert len(results) == 1
        retrieved_cert = results[0]

        assert retrieved_cert['download_url'] ==\
               reverse('certificates:render_cert_by_uuid',
                       kwargs={'certificate_uuid': self.cert.verify_uuid})
        assert retrieved_cert['regenerate']

    def _search(self, user_filter, course_filter=None):
        """Execute a search and return the response. """
        url = reverse("certificates:search") + "?user=" + user_filter
        if course_filter:
            url += '&course_id=' + course_filter
        return self.client.get(url)


@ddt.ddt
class CertificateRegenerateTests(CertificateSupportTestCase):
    """
    Tests for the certificate regeneration end-point used by the support team.
    """

    def setUp(self):
        """
        Create a course and enroll the student in the course.
        """
        super().setUp()
        self.course = CourseFactory(
            org=self.CERT_COURSE_KEY.org,
            course=self.CERT_COURSE_KEY.course,
            run=self.CERT_COURSE_KEY.run,
        )
        self.course_key = self.course.id  # pylint: disable=no-member
        CourseEnrollment.enroll(self.student, self.CERT_COURSE_KEY, self.CERT_MODE)

    @ddt.data(
        (GlobalStaff, True),
        (SupportStaffRole, True),
        (None, False),
    )
    @ddt.unpack
    def test_access_control(self, role, has_access):
        # Create a user and log in
        user = UserFactory(username="foo", password="foo")
        success = self.client.login(username="foo", password="foo")
        assert success, 'Could not log in'

        # Assign the user to the role
        if role is not None:
            role().add_users(user)

        # Make a POST request
        # Since we're not passing valid parameters, we'll get an error response
        # but at least we'll know we have access
        response = self._regenerate()

        if has_access:
            assert response.status_code == 400
        else:
            assert response.status_code == 403

    def test_regenerate_certificate_missing_params(self):
        # Missing username
        response = self._regenerate(course_key=self.CERT_COURSE_KEY)
        assert response.status_code == 400

        # Missing course key
        response = self._regenerate(username=self.STUDENT_USERNAME)
        assert response.status_code == 400

    def test_regenerate_no_such_user(self):
        response = self._regenerate(
            course_key=str(self.CERT_COURSE_KEY),
            username="invalid_username",
        )
        assert response.status_code == 400

    def test_regenerate_no_such_course(self):
        response = self._regenerate(
            course_key=CourseKey.from_string("edx/invalid/course"),
            username=self.STUDENT_USERNAME
        )
        assert response.status_code == 400

    def test_regenerate_user_is_not_enrolled(self):
        # Unenroll the user
        CourseEnrollment.unenroll(self.student, self.CERT_COURSE_KEY)

        # Can no longer regenerate certificates for the user
        response = self._regenerate(
            course_key=self.CERT_COURSE_KEY,
            username=self.STUDENT_USERNAME
        )
        assert response.status_code == 400

    @mock.patch(CAN_GENERATE_METHOD, mock.Mock(return_value=True))
    def test_regenerate_user_has_no_certificate(self):
        # Delete the user's certificate
        GeneratedCertificate.eligible_certificates.all().delete()

        # Should be able to regenerate
        response = self._regenerate(
            course_key=self.CERT_COURSE_KEY,
            username=self.STUDENT_USERNAME
        )
        assert response.status_code == 200

        # A new certificate is created
        num_certs = GeneratedCertificate.eligible_certificates.filter(user=self.student).count()
        assert num_certs == 1

    def _regenerate(self, course_key=None, username=None):
        """Call the regeneration end-point and return the response. """
        url = reverse("certificates:regenerate_certificate_for_user")
        params = {}

        if course_key is not None:
            params["course_key"] = course_key

        if username is not None:
            params["username"] = username

        return self.client.post(url, params)

    def _invalidate_certificate(self, certificate):
        """ Dry method to mark certificate as invalid. """
        CertificateInvalidationFactory.create(
            generated_certificate=certificate,
            invalidated_by=self.support,
            active=True
        )
        # Invalidate user certificate
        certificate.invalidate()
        assert not certificate.is_valid()

    def assertCertInvalidationExists(self):
        """ Dry method to check certificate invalidated entry exists. """
        assert CertificateInvalidation.objects.filter(generated_certificate__user=self.student, active=True).exists()

    def assertInvalidatedCertDoesNotExist(self):
        """ Dry method to check certificate invalidated entry does not exists. """
        assert not CertificateInvalidation.objects\
            .filter(generated_certificate__user=self.student, active=True).exists()

    def assertGeneratedCertExists(self, user, status):
        """ Dry method to check if certificate exists. """
        assert GeneratedCertificate.objects.filter(user=user, status=status).exists()


@ddt.ddt
class CertificateGenerateTests(CertificateSupportTestCase):
    """
    Tests for the certificate generation end-point used by the support team.
    """

    def setUp(self):
        """
        Create a course and enroll the student in the course.
        """
        super().setUp()
        self.course = CourseFactory(
            org=self.EXISTED_COURSE_KEY_2.org,
            course=self.EXISTED_COURSE_KEY_2.course,
            run=self.EXISTED_COURSE_KEY_2.run
        )
        self.course_key = self.course.id  # pylint: disable=no-member
        CourseEnrollment.enroll(self.student, self.EXISTED_COURSE_KEY_2, self.CERT_MODE)

    @ddt.data(
        (GlobalStaff, True),
        (SupportStaffRole, True),
        (None, False),
    )
    @ddt.unpack
    def test_access_control(self, role, has_access):
        # Create a user and log in
        user = UserFactory(username="foo", password="foo")
        success = self.client.login(username="foo", password="foo")
        assert success, 'Could not log in'

        # Assign the user to the role
        if role is not None:
            role().add_users(user)

        # Make a POST request
        # Since we're not passing valid parameters, we'll get an error response
        # but at least we'll know we have access
        response = self._generate()

        if has_access:
            assert response.status_code == 400
        else:
            assert response.status_code == 403

    def test_generate_certificate(self):
        response = self._generate(
            course_key=self.course_key,
            username=self.STUDENT_USERNAME,
        )
        assert response.status_code == 200

    def test_generate_certificate_missing_params(self):
        # Missing username
        response = self._generate(course_key=self.EXISTED_COURSE_KEY_2)
        assert response.status_code == 400

        # Missing course key
        response = self._generate(username=self.STUDENT_USERNAME)
        assert response.status_code == 400

    def test_generate_no_such_user(self):
        response = self._generate(
            course_key=str(self.EXISTED_COURSE_KEY_2),
            username="invalid_username",
        )
        assert response.status_code == 400

    def test_generate_no_such_course(self):
        response = self._generate(
            course_key=CourseKey.from_string("edx/invalid/course"),
            username=self.STUDENT_USERNAME
        )
        assert response.status_code == 400

    def test_generate_user_is_not_enrolled(self):
        # Unenroll the user
        CourseEnrollment.unenroll(self.student, self.EXISTED_COURSE_KEY_2)

        # Can no longer regenerate certificates for the user
        response = self._generate(
            course_key=self.EXISTED_COURSE_KEY_2,
            username=self.STUDENT_USERNAME
        )
        assert response.status_code == 400

    @mock.patch(CAN_GENERATE_METHOD, mock.Mock(return_value=True))
    def test_generate_user_has_no_certificate(self):
        # Delete the user's certificate
        GeneratedCertificate.eligible_certificates.all().delete()

        # Should be able to generate
        response = self._generate(
            course_key=self.EXISTED_COURSE_KEY_2,
            username=self.STUDENT_USERNAME
        )
        assert response.status_code == 200

        # A new certificate is created
        num_certs = GeneratedCertificate.eligible_certificates.filter(user=self.student).count()
        assert num_certs == 1

    def _generate(self, course_key=None, username=None):
        """Call the generation end-point and return the response. """
        url = reverse("certificates:generate_certificate_for_user")
        params = {}

        if course_key is not None:
            params["course_key"] = course_key

        if username is not None:
            params["username"] = username

        return self.client.post(url, params)
