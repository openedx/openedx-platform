"""
Tests for the models that configures 'VideoUploadsEnabledByDefault' feature.
"""

import itertools  # lint-amnesty, pylint: disable=wrong-import-order

import ddt
from django.test import TestCase

from openedx.core.djangoapps.video_config.tests.test_models import FeatureFlagTestMixin
from openedx.core.djangoapps.video_pipeline.models import (
    CourseVideoUploadsEnabledByDefault,
    VideoUploadsEnabledByDefault,
)


@ddt.ddt
class TestVideoUploadsEnabledByDefault(TestCase, FeatureFlagTestMixin):
    """
    Tests the behavior of the flags for video uploads enabled by default feature.
    These are set via Django admin settings.
    """
    @ddt.data(
        *itertools.product(
            (True, False),
            (True, False),
            (True, False),
        )
    )
    @ddt.unpack
    def test_video_upload_enabled_by_default_feature_flags(self, global_flag,
                                                           enabled_for_all_courses, enabled_for_course_1):
        """
        Tests that video uploads enabled by default feature flags works correctly on tweaking global flags
        in combination with course-specific flags.
        """
        self.verify_feature_flags(
            all_courses_model_class=VideoUploadsEnabledByDefault,
            course_specific_model_class=CourseVideoUploadsEnabledByDefault,
            global_flag=global_flag,
            enabled_for_all_courses=enabled_for_all_courses,
            enabled_for_course_1=enabled_for_course_1
        )

    def test_enable_disable_course_flag(self):
        """
        Ensures that the video uploads enabled by default course specific flag, once enabled for a course,
        can also be disabled.
        """
        self.verify_enable_disable_course_flag(
            all_courses_model_class=VideoUploadsEnabledByDefault,
            course_specific_model_class=CourseVideoUploadsEnabledByDefault
        )

    def test_enable_disable_globally(self):
        """
        Ensures that the video uploads enabled by default flag, once enabled globally, can also be disabled.
        """
        self.verify_enable_disable_globally(
            all_courses_model_class=VideoUploadsEnabledByDefault,
            course_specific_model_class=CourseVideoUploadsEnabledByDefault
        )
