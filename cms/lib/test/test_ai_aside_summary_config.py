"""
Tests for AiAsideSummaryConfig class.
"""


import sys
from unittest import TestCase
from unittest.mock import Mock

from opaque_keys.edx.keys import CourseKey, UsageKey

from cms.lib.ai_aside_summary_config import AiAsideSummaryConfig

ai_aside = Mock()
sys.modules['ai_aside.config_api.api'] = ai_aside


class AiAsideSummaryConfigTestCase(TestCase):
    """ Tests for AiAsideSummaryConfig. """
    COURSE_KEY = CourseKey.from_string("course-v1:test+Test+AiAsideSummaryConfigTestCase")
    UNIT_KEY = UsageKey.from_string("block-v1:test+Test+AiAsideSummaryConfigTestCase+type@vertical+block@vertical_test")

    def test_is_enabled(self):
        """
        Check if summary configuration is enabled using the ai_aside lib.
        """
        ai_aside_summary_config = AiAsideSummaryConfig(self.COURSE_KEY)
        ai_aside.is_summary_config_enabled.return_value = True
        self.assertTrue(ai_aside_summary_config.is_enabled())  # noqa: PT009

        ai_aside.is_summary_config_enabled.return_value = False
        self.assertFalse(ai_aside_summary_config.is_enabled())  # noqa: PT009

    def test_is_summary_enabled(self):
        """
        Check the summary configuration value for a particular course and an optional unit using the ai_aside lib.
        """
        ai_aside_summary_config = AiAsideSummaryConfig(self.COURSE_KEY)
        ai_aside.is_course_settings_present.return_value = True
        ai_aside.is_summary_enabled.return_value = True
        self.assertTrue(ai_aside_summary_config.is_summary_enabled())  # noqa: PT009

        ai_aside.is_course_settings_present.return_value = True
        ai_aside.is_summary_enabled.return_value = False
        self.assertFalse(ai_aside_summary_config.is_summary_enabled(self.UNIT_KEY))  # noqa: PT009

        ai_aside.is_course_settings_present.return_value = False
        ai_aside.is_summary_enabled.return_value = True
        self.assertIsNone(ai_aside_summary_config.is_summary_enabled())  # noqa: PT009

        ai_aside.is_course_settings_present.return_value = False
        ai_aside.is_summary_enabled.return_value = False
        self.assertIsNone(ai_aside_summary_config.is_summary_enabled(self.UNIT_KEY))  # noqa: PT009

    def test_set_summary_settings(self):
        """
        Set the summary configuration settings for a particular unit using the ai_aside lib.
        """
        ai_aside_summary_config = AiAsideSummaryConfig(self.COURSE_KEY)
        ai_aside.set_unit_settings.return_value = True
        self.assertTrue(ai_aside_summary_config.set_summary_settings(self.UNIT_KEY, {}))  # noqa: PT009
        self.assertIsNone(ai_aside_summary_config.set_summary_settings(self.UNIT_KEY))  # noqa: PT009
