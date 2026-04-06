"""
Content Type Gating Configuration Models
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openedx.core.djangoapps.config_model_utils.models import StackedConfigurationModel
from openedx.features.content_type_gating.helpers import correct_modes_for_fbe, enrollment_date_for_fbe


class ContentTypeGatingConfig(StackedConfigurationModel):
    """
    A ConfigurationModel used to manage configuration for Content Type Gating (Feature Based Enrollments).

    .. no_pii:
    """

    STACKABLE_FIELDS = ('enabled', 'enabled_as_of', 'studio_override_enabled')

    enabled_as_of = models.DateTimeField(
        default=None,
        null=True,
        verbose_name=_('Enabled As Of'),
        blank=True,
        help_text=_(
            'If the configuration is Enabled, then all enrollments '
            'created after this date and time (user local time) will be affected.'
        )
    )
    studio_override_enabled = models.BooleanField(
        default=None,
        verbose_name=_('Studio Override Enabled'),
        blank=True,
        help_text=_(
            'Allow Feature Based Enrollment visibility to be overriden '
            'on a per-component basis in Studio.'
        ),
        null=True
    )

    @classmethod
    def enabled_for_enrollment(cls, user=None, course_key=None):
        """
        Return whether Content Type Gating is enabled for this enrollment.

        Content Type Gating is enabled for an enrollment if it is enabled for
        the course being enrolled in (either specifically, or via a containing context,
        such as the org, site, or globally), and if the configuration is specified to be
        ``enabled_as_of`` before the enrollment was created.

        Arguments:
            user: The user being queried.
            course_key: The CourseKey of the course being queried.
        """
        target_datetime = enrollment_date_for_fbe(user, course_key=course_key)
        if not target_datetime:
            return False
        current_config = cls.current(course_key=course_key)
        return current_config.enabled_as_of_datetime(target_datetime=target_datetime)

    @classmethod
    def enabled_for_course(cls, course_key, target_datetime=None):
        """
        Return whether Content Type Gating is enabled for this course as of a particular date.

        Content Type Gating is enabled for a course on a date if it is enabled either specifically,
        or via a containing context, such as the org, site, or globally, and if the configuration
        is specified to be ``enabled_as_of`` before ``target_datetime``.

        Only one of enrollment and (user, course_key) may be specified at a time.

        Arguments:
            course_key: The CourseKey of the course being queried.
            target_datetime: The datetime to checked enablement as of. Defaults to the current date and time.
        """
        if not correct_modes_for_fbe(course_key):
            return False

        if target_datetime is None:
            target_datetime = timezone.now()

        current_config = cls.current(course_key=course_key)
        return current_config.enabled_as_of_datetime(target_datetime=target_datetime)

    def clean(self):
        if self.enabled and self.enabled_as_of is None:
            raise ValidationError({'enabled_as_of': _('enabled_as_of must be set when enabled is True')})

    def enabled_as_of_datetime(self, target_datetime):
        """
        Return whether this Content Type Gating configuration context is enabled as of a date and time.

        Arguments:
            target_datetime (:class:`datetime.datetime`): The datetime that ``enabled_as_of`` must be equal to or before
        """

        # Explicitly cast this to bool, so that when self.enabled is None the method doesn't return None
        return bool(self.enabled and self.enabled_as_of <= target_datetime)

    def __str__(self):
        return "ContentTypeGatingConfig(enabled={!r}, enabled_as_of={!r}, studio_override_enabled={!r})".format(
            self.enabled,
            self.enabled_as_of,
            self.studio_override_enabled,
        )
