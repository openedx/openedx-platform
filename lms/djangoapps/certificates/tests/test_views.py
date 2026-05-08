"""Tests for certificates views. """


import datetime
from uuid import uuid4

from django.conf import settings
from django.test.client import Client
from django.test.utils import override_settings

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.models import CertificateHtmlViewConfiguration, CertificateStatuses
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.certificates.utils import get_certificate_url
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration
from xmodule.data import CertificatesDisplayBehaviors  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

FEATURES_WITH_CERTS_ENABLED = settings.FEATURES.copy()
FEATURES_WITH_CERTS_ENABLED['CERTIFICATES_HTML_VIEW'] = True

FEATURES_WITH_CERTS_DISABLED = settings.FEATURES.copy()
FEATURES_WITH_CERTS_DISABLED['CERTIFICATES_HTML_VIEW'] = False

FEATURES_WITH_CUSTOM_CERTS_ENABLED = {
    "CUSTOM_CERTIFICATE_TEMPLATES_ENABLED": True
}
FEATURES_WITH_CUSTOM_CERTS_ENABLED.update(FEATURES_WITH_CERTS_ENABLED)


class CertificatesViewsSiteTests(ModuleStoreTestCase):
    """
    Tests for the certificates web/html views
    """
    test_configuration_string = """{
        "default": {
            "accomplishment_class_append": "accomplishment-certificate",
            "platform_name": "edX",
            "company_about_url": "http://www.edx.org/about-us",
            "company_privacy_url": "http://www.edx.org/edx-privacy-policy",
            "company_tos_url": "http://www.edx.org/edx-terms-service",
            "company_verified_certificate_url": "http://www.edx.org/verified-certificate",
            "document_stylesheet_url_application": "/static/certificates/sass/main-ltr.css",
            "logo_src": "/static/certificates/images/logo-edx.svg",
            "logo_url": "http://www.edx.org",
            "company_about_description": "This should not survive being overwritten by static content"
        },
        "honor": {
            "certificate_type": "Honor Code"
        }
    }"""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.course = CourseFactory.create(
            org='testorg',
            number='run1',
            display_name='refundable course',
            certificate_available_date=datetime.datetime.today() - datetime.timedelta(days=1),
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE
        )
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.store.update_item(self.course, self.user.id)
        self.course_id = self.course.location.course_key
        self.user = UserFactory.create(
            email='joe_user@edx.org',
            username='joeuser',
            password='foo'
        )
        self.user.profile.name = "Joe User"
        self.user.profile.save()
        self.client.login(username=self.user.username, password='foo')
        self.cert = GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course_id,
            download_uuid=uuid4().hex,
            grade="0.95",
            key='the_key',
            distinction=True,
            status=CertificateStatuses.downloadable,
            mode='honor',
            name=self.user.profile.name,
            verify_uuid=uuid4().hex
        )
        self._setup_configuration()

    def _setup_configuration(self, enabled=True):
        """
        This will create a certificate html configuration
        """
        config = CertificateHtmlViewConfiguration(enabled=enabled, configuration=self.test_configuration_string)
        config.save()
        return config

    def _add_course_certificates(self, count=1, signatory_count=0, is_active=True):
        """
        Create certificate for the course.
        """
        signatories = [
            {
                'name': 'Signatory_Name ' + str(i),
                'title': 'Signatory_Title ' + str(i),
                'organization': 'Signatory_Organization ' + str(i),
                'signature_image_path': f'/static/certificates/images/demo-sig{i}.png',
                'id': i,
            } for i in range(signatory_count)

        ]

        certificates = [
            {
                'id': i,
                'name': 'Name ' + str(i),
                'description': 'Description ' + str(i),
                'course_title': 'course_title_' + str(i),
                'signatories': signatories,
                'version': 1,
                'is_active': is_active
            } for i in range(count)
        ]

        self.course.certificates = {'certificates': certificates}
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.store.update_item(self.course, self.user.id)

    @override_settings(FEATURES=FEATURES_WITH_CERTS_ENABLED)
    @with_site_configuration(configuration={'platform_name': 'My Platform Site'})
    def test_html_view_for_site(self):
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=2)
        response = self.client.get(test_url)
        self.assertContains(
            response,
            'awarded this My Platform Site Honor Code Certificate of Completion',
        )
        self.assertContains(
            response,
            'My Platform Site offers interactive online classes and MOOCs.'
        )
        self.assertContains(response, 'About My Platform Site')

    @override_settings(FEATURES=FEATURES_WITH_CERTS_ENABLED)
    def test_html_view_site_configuration_missing(self):
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=2)
        response = self.client.get(test_url)
        self.assertContains(response, 'edX')
        self.assertNotContains(response, 'My Platform Site')
        self.assertNotContains(
            response,
            'This should not survive being overwritten by static content',
        )

    @override_settings(FEATURES=FEATURES_WITH_CERTS_ENABLED, GOOGLE_ANALYTICS_4_ID='GA-abc')
    @with_site_configuration(configuration={'platform_name': 'My Platform Site'})
    def test_html_view_with_g4(self):
        test_url = get_certificate_url(
            user_id=self.user.id,
            course_id=str(self.course.id),
            uuid=self.cert.verify_uuid
        )
        self._add_course_certificates(count=1, signatory_count=2)
        response = self.client.get(test_url)
        self.assertContains(
            response,
            'awarded this My Platform Site Honor Code Certificate of Completion',
        )
        self.assertContains(response, 'googletagmanager')
