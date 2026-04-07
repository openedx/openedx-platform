"""
Defines a form for providing validation of HLS Playback course-specific configuration.
"""

import logging

from django import forms

from openedx.core.djangoapps.video_config.models import (
    CourseHLSPlaybackEnabledFlag,
    CourseVideoTranscriptEnabledFlag,
    CourseYoutubeBlockedFlag,
)
from openedx.core.lib.courses import clean_course_id

log = logging.getLogger(__name__)


class CourseSpecificFlagAdminBaseForm(forms.ModelForm):
    """
    Form for course-specific feature configuration.
    """

    # Make abstract base class
    class Meta:
        abstract = True

    def clean_course_id(self):
        """
        Validate the course id
        """
        return clean_course_id(self)


class CourseHLSPlaybackFlagAdminForm(CourseSpecificFlagAdminBaseForm):
    """
    Form for course-specific HLS Playback configuration.
    """

    class Meta:
        model = CourseHLSPlaybackEnabledFlag
        fields = '__all__'


class CourseYoutubeBlockedFlagAdminForm(CourseSpecificFlagAdminBaseForm):
    """
    Form for course-specific youtube blocking configuration.
    """

    class Meta:
        model = CourseYoutubeBlockedFlag
        fields = '__all__'


class CourseVideoTranscriptFlagAdminForm(CourseSpecificFlagAdminBaseForm):
    """
    Form for course-specific Video Transcript configuration.
    """

    class Meta:
        model = CourseVideoTranscriptEnabledFlag
        fields = '__all__'
