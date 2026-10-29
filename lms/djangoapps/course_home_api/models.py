"""
Course home api models file
"""

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

from openedx.core.djangoapps.config_model_utils.models import StackedConfigurationModel

User = get_user_model()


class CourseNotificationPreference(models.Model):
    """
    Stores per-user notification preferences for course home updates.
    Allows learners to opt in or out of different notification channels.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='course_notification_prefs')
    full_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    notify_by_email = models.BooleanField(default=True)
    notify_by_sms = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'course_home_api'
        unique_together = ('user',)

    def __str__(self):
        return f'CourseNotificationPreference for {self.user.username}'


class DisableProgressPageStackedConfig(StackedConfigurationModel):
    """
    Stacked Config Model for disabling the frontend-app-learning progress page

    .. no_pii:
    """

    STACKABLE_FIELDS = ('disabled',)
    # Since this config disables the progress page,
    # it seemed it would be clearer to use a disabled flag instead of an enabled flag.
    # The enabled field still exists but is not used or shown in the admin.
    disabled = models.BooleanField(default=None, verbose_name=_("Disabled"), null=True)

    def __str__(self):
        return "DisableProgressPageStackedConfig(disabled={!r})".format(  # noqa: UP032
            self.disabled
        )
