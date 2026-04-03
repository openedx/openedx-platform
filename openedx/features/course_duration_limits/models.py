"""
Course Duration Limit Configuration Models
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openedx.core.djangoapps.config_model_utils.models import StackedConfigurationModel
from openedx.features.content_type_gating.helpers import correct_modes_for_fbe, enrollment_date_for_fbe


class CourseDurationLimitConfig(StackedConfigurationModel):
    """
    Configuration to manage the Course Duration Limit facility.

    .. no_pii:

    .. toggle_name: CourseDurationLimitConfig.enabled
    .. toggle_implementation: ConfigurationModel
    .. toggle_default: False
    .. toggle_description: When enabled, users will have a limited time to complete and audit the course. The exact
       duration is given by the "weeks_to_complete" course detail. When enabled, it is necessary to also define the
       "enabled_as_of" flag: only enrollments created after this date will be affected.
    .. toggle_use_cases: opt_in
    .. toggle_creation_date: 2018-11-02
    """

    STACKABLE_FIELDS = ('enabled', 'enabled_as_of')

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

    @classmethod
    def enabled_for_enrollment(cls, user, course):
        """
        Return whether Course Duration Limits are enabled for this enrollment.

        Course Duration Limits are enabled for an enrollment if they are enabled for
        the course being enrolled in (either specifically, or via a containing context,
        such as the org, site, or globally), and if the configuration is specified to be
        ``enabled_as_of`` before the enrollment was created.

        Arguments:
            user: The user being queried.
            course_key: The CourseKey of the course being queried.
            course: The CourseOverview object being queried.
        """
        target_datetime = enrollment_date_for_fbe(user, course=course)
        if not target_datetime:
            return False
        current_config = cls.current(course_key=course.id)
        return current_config.enabled_as_of_datetime(target_datetime=target_datetime)

    @classmethod
    def enabled_for_course(cls, course_key, target_datetime=None):
        """
        Return whether Course Duration Limits are enabled for this course as of a particular date.

        Course Duration Limits are enabled for a course on a date if they are enabled either specifically,
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
        Return whether this Course Duration Limit configuration context is enabled as of a date and time.

        Arguments:
            target_datetime (:class:`datetime.datetime`): The datetime that ``enabled_as_of`` must be equal to or before
        """

        # Explicitly cast this to bool, so that when self.enabled is None the method doesn't return None
        return bool(self.enabled and self.enabled_as_of <= target_datetime)

    def __str__(self):
        return "CourseDurationLimits(enabled={!r}, enabled_as_of={!r})".format(
            self.enabled,
            self.enabled_as_of,
        )
