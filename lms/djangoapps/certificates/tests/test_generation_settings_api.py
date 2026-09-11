"""Tests for the public certificate-generation settings API."""

from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from lms.djangoapps.certificates import api


class CertificateGenerationSettingsApiTests(TestCase):
    """The API handles historical duplicate rows using the most recent value."""

    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator(org='FBB', course='BNJjTSOB', run='2026_C1')

    def test_create_and_get_latest_settings(self):
        api.create_course_certificate_generation_settings(self.course_key, {
            'self_generation_enabled': False,
            'language_specific_templates_enabled': False,
            'include_hours_of_effort': None,
        })
        api.create_course_certificate_generation_settings(self.course_key, {
            'self_generation_enabled': True,
            'language_specific_templates_enabled': True,
            'include_hours_of_effort': True,
        })

        result = api.get_course_certificate_generation_settings(self.course_key)

        self.assertEqual(result, {
            'self_generation_enabled': True,
            'language_specific_templates_enabled': True,
            'include_hours_of_effort': True,
        })
        self.assertEqual(api.get_self_generation_enabled_for_courses([self.course_key]), {
            str(self.course_key): True,
        })

    def test_create_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            api.create_course_certificate_generation_settings(self.course_key, {'enabled': True})
