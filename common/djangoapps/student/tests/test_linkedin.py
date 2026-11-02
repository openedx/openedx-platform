"""Tests for LinkedIn Add to Profile configuration. """

from types import SimpleNamespace
from urllib.parse import quote
import ddt
from django.conf import settings
from django.test import TestCase

from lms.djangoapps.certificates.tests.factories import LinkedInAddToProfileConfigurationFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context


@ddt.ddt
class LinkedInAddToProfileUrlTests(TestCase):
    """Tests for URL generation of LinkedInAddToProfileConfig. """

    COURSE_NAME = 'Test Course ☃'
    CERT_URL = 'http://s3.edx/cert'
    COURSE_ORGANIZATION = 'TEST+ORGANIZATION'
    SITE_CONFIGURATION = {
        'SOCIAL_SHARING_SETTINGS': {
            'CERTIFICATE_LINKEDIN_MODE_TO_CERT_NAME': {
                'honor': '{platform_name} Honor Code Credential for {course_name}',
                'verified': '{platform_name} Verified Credential for {course_name}',
                'professional': '{platform_name} Professional Credential for {course_name}',
                'no-id-professional': '{platform_name} Professional Credential for {course_name}',
            }
        }
    }
    SITE_CONFIGURATION_COURSE_LEVEL_ORG = {
        'SOCIAL_SHARING_SETTINGS': {
            'CERTIFICATE_LINKEDIN_DEFAULTS_TO_COURSE_ORGANIZATION_NAME': True,
            'CERTIFICATE_LINKEDIN_MODE_TO_CERT_NAME': {
                'honor': '{platform_name} Honor Code Credential for {course_name}',
                'verified': '{platform_name} Verified Credential for {course_name}',
                'professional': '{platform_name} Professional Credential for {course_name}',
                'no-id-professional': '{platform_name} Professional Credential for {course_name}',
            }
        }
    }

    @ddt.data(
        ('honor', 'Honor+Code+Certificate+for+Test+Course+%E2%98%83'),
        ('verified', 'Verified+Certificate+for+Test+Course+%E2%98%83'),
        ('professional', 'Professional+Certificate+for+Test+Course+%E2%98%83'),
        ('default_mode', 'Certificate+for+Test+Course+%E2%98%83')
    )
    @ddt.unpack
    def test_linked_in_url(self, cert_mode, expected_cert_name):
        config = LinkedInAddToProfileConfigurationFactory()

        expected_url = (
            'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&'
            'name={platform}+{cert_name}&certUrl={cert_url}&'
            'organizationId={company_identifier}'
        ).format(
            platform=quote(settings.PLATFORM_NAME.encode('utf-8')),
            cert_name=expected_cert_name,
            cert_url=quote(self.CERT_URL, safe=''),
            company_identifier=config.company_identifier,
        )

        course_mock_object = SimpleNamespace(
            display_name=self.COURSE_NAME, display_organization=self.COURSE_ORGANIZATION
        )

        actual_url = config.add_to_profile_url(
            course_mock_object, cert_mode, self.CERT_URL
        )

        self.assertEqual(actual_url, expected_url)

    @ddt.data(
        ('honor', 'Honor+Code+Credential+for+Test+Course+%E2%98%83'),
        ('verified', 'Verified+Credential+for+Test+Course+%E2%98%83'),
        ('professional', 'Professional+Credential+for+Test+Course+%E2%98%83'),
        ('no-id-professional', 'Professional+Credential+for+Test+Course+%E2%98%83'),
        ('default_mode', 'Certificate+for+Test+Course+%E2%98%83')
    )
    @ddt.unpack
    def test_linked_in_url_with_cert_name_override(self, cert_mode, expected_cert_name):
        config = LinkedInAddToProfileConfigurationFactory()

        expected_url = (
            'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&'
            'name={platform}+{cert_name}&certUrl={cert_url}&'
            'organizationId={company_identifier}'
        ).format(
            platform=quote(settings.PLATFORM_NAME.encode('utf-8')),
            cert_name=expected_cert_name,
            cert_url=quote(self.CERT_URL, safe=''),
            company_identifier=config.company_identifier,
        )
        with with_site_configuration_context(configuration=self.SITE_CONFIGURATION):
            course_mock_object = SimpleNamespace(
                display_name=self.COURSE_NAME,
                display_organization=self.COURSE_ORGANIZATION,
            )
            actual_url = config.add_to_profile_url(
                course_mock_object, cert_mode, self.CERT_URL
            )
        self.assertEqual(actual_url, expected_url)

    @ddt.data(
        ('honor', 'Honor+Code+Credential+for+Test+Course+%E2%98%83'),
        ('verified', 'Verified+Credential+for+Test+Course+%E2%98%83'),
        ('professional', 'Professional+Credential+for+Test+Course+%E2%98%83'),
        ('no-id-professional', 'Professional+Credential+for+Test+Course+%E2%98%83'),
        ('default_mode', 'Certificate+for+Test+Course+%E2%98%83')
    )
    @ddt.unpack
    def test_linked_in_url_with_course_org_name_override(
        self, cert_mode, expected_cert_name
    ):
        config = LinkedInAddToProfileConfigurationFactory()

        expected_url = (
            'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&'
            'name={platform}+{cert_name}&certUrl={cert_url}&'
            'organizationName={course_organization_name}'
        ).format(
            platform=quote(settings.PLATFORM_NAME.encode('utf-8')),
            cert_name=expected_cert_name,
            cert_url=quote(self.CERT_URL, safe=''),
            course_organization_name=quote(self.COURSE_ORGANIZATION.encode('utf-8')),
        )

        with with_site_configuration_context(
            configuration=self.SITE_CONFIGURATION_COURSE_LEVEL_ORG
        ):
            course_mock_object = SimpleNamespace(
                display_name=self.COURSE_NAME,
                display_organization=self.COURSE_ORGANIZATION,
            )
            actual_url = config.add_to_profile_url(
                course_mock_object, cert_mode, self.CERT_URL
            )

        self.assertEqual(actual_url, expected_url)
