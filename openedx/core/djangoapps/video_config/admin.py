"""
Django admin dashboard configuration for Video XModule.
"""


from config_models.admin import ConfigurationModelAdmin, KeyedConfigurationModelAdmin
from django.contrib import admin

from openedx.core.djangoapps.video_config.forms import (
    CourseHLSPlaybackFlagAdminForm,
    CourseVideoTranscriptFlagAdminForm,
    CourseYoutubeBlockedFlagAdminForm,
)
from openedx.core.djangoapps.video_config.models import (
    CourseHLSPlaybackEnabledFlag,
    CourseVideoTranscriptEnabledFlag,
    CourseYoutubeBlockedFlag,
    HLSPlaybackEnabledFlag,
    MigrationEnqueuedCourse,
    TranscriptMigrationSetting,
    UpdatedCourseVideos,
    VideoThumbnailSetting,
    VideoTranscriptEnabledFlag,
)


class CourseSpecificEnabledFlagBaseAdmin(KeyedConfigurationModelAdmin):
    """
    Admin of course specific feature on course-by-course basis.
    Allows searching by course id.
    """
    # Make abstract base class
    class Meta:
        abstract = True

    search_fields = ['course_id']
    fieldsets = (
        (None, {
            'fields': ('course_id', 'enabled'),
            'description': 'Enter a valid course id. If it is invalid, an error message will be displayed.'
        }),
    )


class CourseHLSPlaybackEnabledFlagAdmin(CourseSpecificEnabledFlagBaseAdmin):
    """
    Admin of HLS Playback feature on course-by-course basis.
    Allows searching by course id.
    """
    form = CourseHLSPlaybackFlagAdminForm


class CourseYoutubeBlockedFlagAdmin(CourseSpecificEnabledFlagBaseAdmin):
    """
    Admin of youtube blocking feature on course-by-course basis.
    Allows searching by course id.
    """
    form = CourseYoutubeBlockedFlagAdminForm


class CourseVideoTranscriptEnabledFlagAdmin(CourseSpecificEnabledFlagBaseAdmin):
    """
    Admin of Video Transcript feature on course-by-course basis.
    Allows searching by course id.
    """
    form = CourseVideoTranscriptFlagAdminForm


class MigrationEnqueuedCourseAdmin(admin.ModelAdmin):
    """
    Simple, read-only list/search view of the Courses whose transcripts have been migrated to S3.
    """
    list_display = [
        'course_id',
        'command_run',
    ]

    search_fields = ['course_id', 'command_run']


class UpdatedCourseVideosAdmin(admin.ModelAdmin):
    """
    Read-only list/search view of the videos whose thumbnails have been updated.
    """
    list_display = [
        'course_id',
        'edx_video_id',
        'command_run',
    ]

    search_fields = ['course_id', 'edx_video_id', 'command_run']


admin.site.register(HLSPlaybackEnabledFlag, ConfigurationModelAdmin)
admin.site.register(CourseHLSPlaybackEnabledFlag, CourseHLSPlaybackEnabledFlagAdmin)
admin.site.register(CourseYoutubeBlockedFlag, CourseYoutubeBlockedFlagAdmin)
admin.site.register(VideoTranscriptEnabledFlag, ConfigurationModelAdmin)
admin.site.register(CourseVideoTranscriptEnabledFlag, CourseVideoTranscriptEnabledFlagAdmin)
admin.site.register(TranscriptMigrationSetting, ConfigurationModelAdmin)
admin.site.register(MigrationEnqueuedCourse, MigrationEnqueuedCourseAdmin)
admin.site.register(VideoThumbnailSetting, ConfigurationModelAdmin)
admin.site.register(UpdatedCourseVideos, UpdatedCourseVideosAdmin)
