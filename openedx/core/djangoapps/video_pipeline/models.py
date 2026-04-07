"""
Model to hold edx-video-pipeline configurations.
"""

from config_models.models import ConfigurationModel
from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.django.models import CourseKeyField


class VEMPipelineIntegration(ConfigurationModel):
    """
    Manages configuration for connecting to the video encode manager service and using its API.

    .. no_pii:

    .. toggle_name: VEMPipelineIntegration.enabled
    .. toggle_implementation: ConfigurationModel
    .. toggle_default: False
    .. toggle_description: Send videos to the Video Encode Manager (VEM) as part of the
      video pipeline.
    .. toggle_use_cases:  open_edx
    .. toggle_creation_date: 2020-06-04
    .. toggle_tickets: https://github.com/openedx/edx-platform/pull/24093
    """
    client_name = models.CharField(
        max_length=100,
        default='VEM-Prod',
        null=False,
        blank=False,
        help_text=_('Oauth client name of VEM service.')
    )

    api_url = models.URLField(
        verbose_name=_('Internal API URL'),
        help_text=_('video encode manager API URL.')
    )

    service_username = models.CharField(
        max_length=100,
        default='vem_service_user',
        null=False,
        blank=False,
        help_text=_('Username created for VEM Integration, e.g. vem_service_user.')
    )

    def get_service_user(self):
        User = get_user_model()  # pylint: disable=invalid-name
        return User.objects.get(username=self.service_username)


class VideoUploadsEnabledByDefault(ConfigurationModel):
    """
    Enables video uploads across the platform.

    .. no_pii:

    .. toggle_name: VideoUploadsEnabledByDefault.enabled_for_all_courses
    .. toggle_implementation: ConfigurationModel
    .. toggle_default: False
    .. toggle_description: Allow video uploads for all courses of the platform. This
      enables the "Video Uploads" menu in the CMS.
    .. toggle_use_cases:  open_edx
    .. toggle_creation_date: 2017-11-10
    .. toggle_tickets: https://github.com/openedx/edx-platform/pull/16536
    """
    # this field overrides course-specific settings
    enabled_for_all_courses = models.BooleanField(default=False)

    @classmethod
    def feature_enabled(cls, course_id):
        """
        Looks at the currently active configuration model to determine whether
        the VideoUploadsEnabledByDefault feature is available.

        If the feature flag is not enabled, the feature is not available.
        If the flag is enabled for all the courses, feature is available.
        If the flag is enabled and the provided course_id is for a course
            with CourseVideoUploadsEnabledByDefault enabled, then the
            feature is available.

        Arguments:
            course_id (CourseKey): course id for whom feature will be checked.
        """
        if not cls.is_enabled():
            return False
        elif not cls.current().enabled_for_all_courses:
            feature = (CourseVideoUploadsEnabledByDefault.objects
                       .filter(course_id=course_id)
                       .order_by('-change_date')
                       .first())
            return feature.enabled if feature else False
        return True

    def __str__(self):
        current_model = VideoUploadsEnabledByDefault.current()
        return "VideoUploadsEnabledByDefault: enabled {is_enabled}".format(
            is_enabled=current_model.is_enabled()
        )


class CourseVideoUploadsEnabledByDefault(ConfigurationModel):
    """
    Enables video uploads for a specific course.

    .. no_pii:

    .. toggle_name: CourseVideoUploadsEnabledByDefault.course_id
    .. toggle_implementation: ConfigurationModel
    .. toggle_default: False
    .. toggle_description: Allow video uploads for a specific course. This enables the
      "Video Uploads" menu in the CMS.
    .. toggle_use_cases:  open_edx
    .. toggle_creation_date: 2017-11-10
    .. toggle_tickets: https://github.com/openedx/edx-platform/pull/16536
    """
    KEY_FIELDS = ('course_id',)

    course_id = CourseKeyField(max_length=255, db_index=True)

    def __str__(self):
        not_en = "Not "
        if self.enabled:
            not_en = ""

        return "Course '{course_key}': Video Uploads {not_enabled}Enabled by default.".format(
            course_key=str(self.course_id),
            not_enabled=not_en
        )
