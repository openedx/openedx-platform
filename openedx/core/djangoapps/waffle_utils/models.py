"""
Models for configuring waffle utils.
"""

from config_models.models import ConfigurationModel
from django.db.models import CharField, Index, TextField
from django.utils.translation import gettext_lazy as _
from model_utils import Choices
from opaque_keys.edx.django.models import CourseKeyField

from openedx.core.lib.cache_utils import request_cached


class WaffleFlagCourseOverrideModel(ConfigurationModel):
    """
    Used to force a waffle flag on or off for a course.

    Prioritization:
    A course-level waffle flag overrides a relevant org-level waffle flag.
    An org-level waffle flag overrides any defaults.

    So: Course level overrides (THIS MODEL) (highest priority) ->
        Org level overrides ->
        Defaults (lowest priority)

    .. no_pii:
    """
    OVERRIDE_CHOICES = Choices(('on', _('Force On')), ('off', _('Force Off')))
    ALL_CHOICES = OVERRIDE_CHOICES + Choices('unset')

    KEY_FIELDS = ('waffle_flag', 'course_id')

    # The course that these features are attached to.
    waffle_flag = CharField(max_length=255, db_index=True)
    course_id = CourseKeyField(max_length=255, db_index=True)
    override_choice = CharField(choices=OVERRIDE_CHOICES, default=OVERRIDE_CHOICES.on, max_length=3)
    note = TextField(blank=True, help_text='e.g. why this exists and when/if it can be dropped')

    @classmethod
    @request_cached()
    def override_value(cls, waffle_flag, course_id):
        """
        Returns whether the waffle flag was overridden (on or off) for the
        course, or is unset.

        Arguments:
            waffle_flag (String): The name of the flag.
            course_id (CourseKey): The course id for which the flag may have
                been overridden.

        If the current config is not set or disabled for this waffle flag and
            course id, returns ALL_CHOICES.unset.
        Otherwise, returns ALL_CHOICES.on or ALL_CHOICES.off as configured for
            the override_choice.

        """
        if not course_id or not waffle_flag:
            return cls.ALL_CHOICES.unset

        effective = cls.objects.filter(waffle_flag=waffle_flag, course_id=course_id).order_by('-change_date').first()
        if effective and effective.enabled:
            return effective.override_choice
        return cls.ALL_CHOICES.unset

    class Meta:
        app_label = 'waffle_utils'
        verbose_name = 'Waffle flag course override'
        verbose_name_plural = 'Waffle flag course overrides'

    def __str__(self):
        enabled_label = 'Enabled' if self.enabled else 'Not Enabled'
        return f'Course {str(self.course_id)}: Waffle Override {enabled_label}'


class WaffleFlagOrgOverrideModel(ConfigurationModel):
    """
    Used to force a waffle flag on or off for an organization.

    This class mostly mirrors WaffleFlagCourseOverrideModel.

    Prioritization:
    A course-level waffle flag overrides a relevant org-level waffle flag.
    An org-level waffle flag overrides any defaults.

    So: Course level overrides (highest priority) ->
        Org level overrides (THIS MODEL) ->
        Defaults (lowest priority)

    .. no_pii:
    """
    OVERRIDE_CHOICES = Choices(('on', _('Force On')), ('off', _('Force Off')))
    ALL_CHOICES = OVERRIDE_CHOICES + Choices('unset')

    KEY_FIELDS = ('waffle_flag', 'org')

    # The course that these features are attached to.
    waffle_flag = CharField(max_length=255, db_index=True)
    org = CharField(max_length=255, db_index=True)
    override_choice = CharField(choices=OVERRIDE_CHOICES, default=OVERRIDE_CHOICES.on, max_length=3)
    note = TextField(blank=True, help_text='e.g. why this exists and when/if it can be dropped')

    @classmethod
    @request_cached()
    def override_value(cls, waffle_flag, org):
        """
        Returns whether the waffle flag was overridden (on or off) for the
        org, or is unset.

        Arguments:
            waffle_flag (String): The name of the flag.
            org (String): The org for which the flag may have been overridden.

        If the current config is not set or disabled for this waffle flag and
            org, returns ALL_CHOICES.unset.
        Otherwise, returns ALL_CHOICES.on or ALL_CHOICES.off as configured for
            the override_choice.
        """
        if not org or not waffle_flag:
            return cls.ALL_CHOICES.unset

        effective = cls.objects.filter(waffle_flag=waffle_flag, org=org).order_by('-change_date').first()
        if effective and effective.enabled:
            return effective.override_choice
        return cls.ALL_CHOICES.unset

    class Meta:
        app_label = 'waffle_utils'
        verbose_name = 'Waffle flag org override'
        verbose_name_plural = 'Waffle flag org overrides'
        indexes = [
            Index(
                name="waffle_org_and_waffle_flag",
                fields=["org", "waffle_flag"],
            )
        ]

    def __str__(self):
        enabled_label = 'Enabled' if self.enabled else 'Not Enabled'
        return f'Org {str(self.org)}: Waffle Override {enabled_label}'
