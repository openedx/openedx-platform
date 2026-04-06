"""
Tests for the models that configures HLS Playback feature.
"""

import itertools  # lint-amnesty, pylint: disable=wrong-import-order
from contextlib import contextmanager  # lint-amnesty, pylint: disable=wrong-import-order

import ddt
from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from openedx.core.djangoapps.video_config.models import (
    CourseHLSPlaybackEnabledFlag,
    CourseVideoTranscriptEnabledFlag,
    HLSPlaybackEnabledFlag,
    VideoTranscriptEnabledFlag,
)


@contextmanager
def video_feature_flags(
        all_courses_model_class, course_specific_model_class,
        global_flag, enabled_for_all_courses=False,
        course_id=None, enabled_for_course=False
):
    """
    Yields video feature configuration records for unit tests
    Arguments:
        all_courses_model_class: Model class to enable feature for all courses
        course_specific_model_class: Model class to nable feature for course specific
        global_flag (bool): Specifies whether feature is enabled globally
        enabled_for_all_courses (bool): Specifies whether feature is enabled for all courses
        course_id (CourseLocator): Course locator for course specific configurations
        enabled_for_course (bool): Specifies whether feature should be available for a course
    """
    all_courses_model_class.objects.create(enabled=global_flag, enabled_for_all_courses=enabled_for_all_courses)
    if course_id:
        course_specific_model_class.objects.create(course_id=course_id, enabled=enabled_for_course)
    yield


class FeatureFlagTestMixin:
    """
    Adds util methods to test the behavior of the flags for video feature.
    """
    course_id_1 = CourseLocator(org="edx", course="course", run="run")
    course_id_2 = CourseLocator(org="edx", course="course2", run="run")

    def verify_feature_flags(self, all_courses_model_class, course_specific_model_class,
                             global_flag, enabled_for_all_courses, enabled_for_course_1):
        """
        Verifies that the feature flags works correctly on tweaking global flags in combination
        with course-specific flags.
        """
        with video_feature_flags(
            all_courses_model_class=all_courses_model_class,
            course_specific_model_class=course_specific_model_class,
            global_flag=global_flag,
            enabled_for_all_courses=enabled_for_all_courses,
            course_id=self.course_id_1,
            enabled_for_course=enabled_for_course_1
        ):
            assert all_courses_model_class.feature_enabled(self.course_id_1) == (
                global_flag and (enabled_for_all_courses or enabled_for_course_1)
            )
            assert all_courses_model_class.feature_enabled(self.course_id_2) == (
                global_flag and enabled_for_all_courses
            )

    def verify_enable_disable_course_flag(self, all_courses_model_class, course_specific_model_class):
        """
        Verifies that the course specific flag, once enabled for a course, can also be disabled.
        """
        with video_feature_flags(
            all_courses_model_class=all_courses_model_class,
            course_specific_model_class=course_specific_model_class,
            global_flag=True,
            enabled_for_all_courses=False,
            course_id=self.course_id_1,
            enabled_for_course=True
        ):
            assert all_courses_model_class.feature_enabled(self.course_id_1)
            with video_feature_flags(
                all_courses_model_class=all_courses_model_class,
                course_specific_model_class=course_specific_model_class,
                global_flag=True,
                enabled_for_all_courses=False,
                course_id=self.course_id_1,
                enabled_for_course=False
            ):
                assert not all_courses_model_class.feature_enabled(self.course_id_1)

    def verify_enable_disable_globally(self, all_courses_model_class, course_specific_model_class):
        """
        Verifies that global flag, once enabled globally, can also be disabled.
        """
        with video_feature_flags(
            all_courses_model_class=all_courses_model_class,
            course_specific_model_class=course_specific_model_class,
            global_flag=True,
            enabled_for_all_courses=True,
        ):
            assert all_courses_model_class.feature_enabled(self.course_id_1)
            assert all_courses_model_class.feature_enabled(self.course_id_2)
            with video_feature_flags(
                all_courses_model_class=all_courses_model_class,
                course_specific_model_class=course_specific_model_class,
                global_flag=True,
                enabled_for_all_courses=False,
            ):
                assert not all_courses_model_class.feature_enabled(self.course_id_1)
                assert not all_courses_model_class.feature_enabled(self.course_id_2)
                with video_feature_flags(
                    all_courses_model_class=all_courses_model_class,
                    course_specific_model_class=course_specific_model_class,
                    global_flag=False,
                ):
                    assert not all_courses_model_class.feature_enabled(self.course_id_1)
                    assert not all_courses_model_class.feature_enabled(self.course_id_2)


@ddt.ddt
class TestHLSPlaybackFlag(TestCase, FeatureFlagTestMixin):
    """
    Tests the behavior of the flags for HLS Playback feature.
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
    def test_hls_playback_feature_flags(self, global_flag, enabled_for_all_courses, enabled_for_course_1):
        """
        Tests that the HLS Playback feature flags works correctly on tweaking global flags in combination
        with course-specific flags.
        """
        self.verify_feature_flags(
            all_courses_model_class=HLSPlaybackEnabledFlag,
            course_specific_model_class=CourseHLSPlaybackEnabledFlag,
            global_flag=global_flag,
            enabled_for_all_courses=enabled_for_all_courses,
            enabled_for_course_1=enabled_for_course_1
        )

    def test_enable_disable_course_flag(self):
        """
        Ensures that the flag, once enabled for a course, can also be disabled.
        """
        self.verify_enable_disable_course_flag(
            all_courses_model_class=HLSPlaybackEnabledFlag,
            course_specific_model_class=CourseHLSPlaybackEnabledFlag
        )

    def test_enable_disable_globally(self):
        """
        Ensures that the flag, once enabled globally, can also be disabled.
        """
        self.verify_enable_disable_globally(
            all_courses_model_class=HLSPlaybackEnabledFlag,
            course_specific_model_class=CourseHLSPlaybackEnabledFlag
        )


@ddt.ddt
class TestVideoTranscriptFlag(TestCase, FeatureFlagTestMixin):
    """
    Tests the behavior of the flags for Video Transcript feature.
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
    def test_video_transcript_feature_flags(self, global_flag, enabled_for_all_courses, enabled_for_course_1):
        """
        Tests that Video Transcript feature flags works correctly on tweaking global flags in combination
        with course-specific flags.
        """
        self.verify_feature_flags(
            all_courses_model_class=VideoTranscriptEnabledFlag,
            course_specific_model_class=CourseVideoTranscriptEnabledFlag,
            global_flag=global_flag,
            enabled_for_all_courses=enabled_for_all_courses,
            enabled_for_course_1=enabled_for_course_1
        )

    def test_enable_disable_course_flag(self):
        """
        Ensures that the Video Transcript course specific flag, once enabled for a course, can also be disabled.
        """
        self.verify_enable_disable_course_flag(
            all_courses_model_class=VideoTranscriptEnabledFlag,
            course_specific_model_class=CourseVideoTranscriptEnabledFlag
        )

    def test_enable_disable_globally(self):
        """
        Ensures that the Video Transcript flag, once enabled globally, can also be disabled.
        """
        self.verify_enable_disable_globally(
            all_courses_model_class=VideoTranscriptEnabledFlag,
            course_specific_model_class=CourseVideoTranscriptEnabledFlag
        )
