"""
Configuration models for Video XModule
"""


from config_models.models import ConfigurationModel
from django.db import models
from django.db.models import BooleanField, PositiveIntegerField, TextField
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField

URL_REGEX = r'^[a-zA-Z0-9\-_]*$'


class HLSPlaybackEnabledFlag(ConfigurationModel):
    """
    Enables HLS Playback across the platform.
    When this feature flag is set to true, individual courses
    must also have HLS Playback enabled for this feature to
    take effect.

    .. no_pii:

    .. toggle_name: HLSPlaybackEnabledFlag.enabled_for_all_courses
    .. toggle_implementation: ConfigurationModel
    .. toggle_default: False
    .. toggle_description: Add the "hls" profile to all displayed videos on the platform.
    .. toggle_use_cases:  open_edx
    .. toggle_creation_date: 2017-04-19
    .. toggle_tickets: https://github.com/openedx/edx-platform/pull/14924
    """
    # this field overrides course-specific settings
    enabled_for_all_courses = BooleanField(default=False)

    @classmethod
    def feature_enabled(cls, course_id):
        """
        Looks at the currently active configuration model to determine whether
        the HLS Playback feature is available.

        If the feature flag is not enabled, the feature is not available.
        If the flag is enabled for all the courses, feature is available.
        If the flag is enabled and the provided course_id is for an course
            with HLS Playback enabled, the feature is available.

        Arguments:
            course_id (CourseKey): course id for whom feature will be checked.
        """
        if not HLSPlaybackEnabledFlag.is_enabled():
            return False
        elif not HLSPlaybackEnabledFlag.current().enabled_for_all_courses:
            feature = (CourseHLSPlaybackEnabledFlag.objects
                       .filter(course_id=course_id)
                       .order_by('-change_date')
                       .first())
            return feature.enabled if feature else False
        return True

    def __str__(self):
        current_model = HLSPlaybackEnabledFlag.current()
        return "HLSPlaybackEnabledFlag: enabled {is_enabled}".format(  # noqa: UP032
            is_enabled=current_model.is_enabled()
        )


class CourseHLSPlaybackEnabledFlag(ConfigurationModel):
    """
    Enables HLS Playback for a specific course. Global feature must be
    enabled for this to take effect.

    .. no_pii:

    .. toggle_name: CourseHLSPlaybackEnabledFlag.course_id
    .. toggle_implementation: ConfigurationModel
    .. toggle_default: False
    .. toggle_description: Add the "hls" profile to all displayed videos for a single course.
    .. toggle_use_cases:  open_edx
    .. toggle_creation_date: 2017-04-19
    .. toggle_tickets: https://github.com/openedx/edx-platform/pull/14924
    """
    KEY_FIELDS = ('course_id',)

    course_id = CourseKeyField(max_length=255, db_index=True)

    def __str__(self):
        not_en = "Not "
        if self.enabled:
            not_en = ""

        return "Course '{course_key}': HLS Playback {not_enabled}Enabled".format(  # noqa: UP032
            course_key=str(self.course_id),
            not_enabled=not_en
        )


class CourseYoutubeBlockedFlag(ConfigurationModel):
    """
    Disables the playback of youtube videos for a given course.
    If the flag is present for the course, and set to "enabled",
    then youtube is disabled for that course.

    .. no_pii:
    """
    KEY_FIELDS = ('course_id',)

    course_id = CourseKeyField(max_length=255, db_index=True)

    @classmethod
    def feature_enabled(cls, course_id):
        """
        Determine if the youtube blocking feature is enabled for the specified course id.
        Argument:
         course_id (CourseKey): course id for whom feature will be checked
        """
        feature = (CourseYoutubeBlockedFlag.objects
                   .filter(course_id=course_id)
                   .order_by('-change_date')
                   .first())
        return feature.enabled if feature else False

    def __str__(self):
        not_en = "Not "
        if self.enabled:
            not_en = ""

        return "Course '{course_key}': Youtube Block {not_enabled}Enabled".format(  # noqa: UP032
            course_key=str(self.course_id),
            not_enabled=not_en
        )


class VideoTranscriptEnabledFlag(ConfigurationModel):
    """
    Enables Video Transcript across the platform.
    When this feature flag is set to true, individual courses
    must also have Video Transcript enabled for this feature to
    take effect.
    When this feature is enabled, 3rd party transcript integration functionality would be available accross all
    courses or some specific courses and S3 video transcript would be served (currently as a fallback).

    .. no_pii:
    """
    # this field overrides course-specific settings
    enabled_for_all_courses = BooleanField(default=False)

    @classmethod
    def feature_enabled(cls, course_id):
        """
        Looks at the currently active configuration model to determine whether
        the Video Transcript feature is available.

        If the feature flag is not enabled, the feature is not available.
        If the flag is enabled for all the courses, feature is available.
        If the flag is enabled and the provided course_id is for an course
            with Video Transcript enabled, the feature is available.

        Arguments:
            course_id (CourseKey): course id for whom feature will be checked.
        """
        if not VideoTranscriptEnabledFlag.is_enabled():
            return False
        elif not VideoTranscriptEnabledFlag.current().enabled_for_all_courses:
            feature = (CourseVideoTranscriptEnabledFlag.objects
                       .filter(course_id=course_id)
                       .order_by('-change_date')
                       .first())
            return feature.enabled if feature else False
        return True

    def __str__(self):
        current_model = VideoTranscriptEnabledFlag.current()
        return "VideoTranscriptEnabledFlag: enabled {is_enabled}".format(  # noqa: UP032
            is_enabled=current_model.is_enabled()
        )


class CourseVideoTranscriptEnabledFlag(ConfigurationModel):
    """
    Enables Video Transcript for a specific course. Global feature must be
    enabled for this to take effect.
    When this feature is enabled, 3rd party transcript integration functionality would be available for the
    specific course and S3 video transcript would be served (currently as a fallback).

    .. no_pii:
    """
    KEY_FIELDS = ('course_id',)

    course_id = CourseKeyField(max_length=255, db_index=True)

    def __str__(self):
        not_en = "Not "
        if self.enabled:
            not_en = ""

        return "Course '{course_key}': Video Transcript {not_enabled}Enabled".format(  # noqa: UP032
            course_key=str(self.course_id),
            not_enabled=not_en
        )


class TranscriptMigrationSetting(ConfigurationModel):
    """
    Arguments for the Transcript Migration management command

    .. no_pii:
    """
    def __str__(self):
        return (
            "[TranscriptMigrationSetting] Courses {courses} with update if already present as {force}"
            " and commit as {commit}"
        ).format(
            courses='ALL' if self.all_courses else self.course_ids,
            force=self.force_update,
            commit=self.commit
        )
    force_update = BooleanField(
        default=False,
        help_text="Flag to force migrate transcripts for the requested courses, overwrite if already present."
    )
    command_run = PositiveIntegerField(default=0)
    batch_size = PositiveIntegerField(default=0)
    commit = BooleanField(
        default=False,
        help_text="Dry-run or commit."
    )
    all_courses = BooleanField(
        default=False,
        help_text="Process all courses."
    )
    course_ids = TextField(
        blank=False,
        help_text="Whitespace-separated list of course keys for which to migrate transcripts."
    )

    def increment_run(self):
        """
        Increments the run which indicates how many time the mgmt. command has run.
        """
        self.command_run += 1
        self.save()
        return self.command_run


class MigrationEnqueuedCourse(TimeStampedModel):
    """
    Temporary model to persist the course IDs who has been enqueued for transcripts migration to S3.

    .. no_pii:
    """
    course_id = CourseKeyField(db_index=True, primary_key=True, max_length=255)
    command_run = PositiveIntegerField(default=0)

    def __str__(self):
        return 'MigrationEnqueuedCourse: ID={course_id}, Run={command_run}'.format(  # noqa: UP032
            course_id=self.course_id, command_run=self.command_run
        )


class VideoThumbnailSetting(ConfigurationModel):
    """
    Arguments for the Video Thumbnail management command

    .. no_pii:
    """
    command_run = PositiveIntegerField(default=0)
    offset = PositiveIntegerField(default=0)
    batch_size = PositiveIntegerField(default=0)
    videos_per_task = PositiveIntegerField(default=0)
    commit = BooleanField(
        default=False,
        help_text="Dry-run or commit."
    )
    all_course_videos = BooleanField(
        default=False,
        help_text="Process all videos."
    )
    course_ids = TextField(
        blank=True,
        help_text="Whitespace-separated list of course ids for which to update videos."
    )

    def increment_run(self):
        """
        Increments the run which indicates the management command run count.
        """
        self.command_run += 1
        self.save()
        return self.command_run

    def update_offset(self):
        self.offset += self.batch_size
        self.save()

    def __str__(self):
        return "[VideoThumbnailSetting] update for {courses} courses if commit as {commit}".format(
            courses='ALL' if self.all_course_videos else self.course_ids,
            commit=self.commit,
        )


class UpdatedCourseVideos(TimeStampedModel):
    """
    Temporary model to persist the course videos which have been enqueued to update video thumbnails.

    .. no_pii:
    """
    course_id = CourseKeyField(db_index=True, max_length=255)
    edx_video_id = models.CharField(max_length=100)
    command_run = PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('course_id', 'edx_video_id')

    def __str__(self):
        return 'UpdatedCourseVideos: CourseID={course_id}, VideoID={video_id}, Run={command_run}'.format(  # noqa: UP032
            course_id=self.course_id, video_id=self.edx_video_id, command_run=self.command_run
        )
