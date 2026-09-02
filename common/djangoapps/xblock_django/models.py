"""
Models.
"""


from config_models.models import ConfigurationModel
from django.db import models
from django.utils.translation import gettext_lazy as _


class XBlockConfiguration(ConfigurationModel):
    """
    XBlock configuration used by both LMS and Studio, and not specific to a particular template.

    .. no_pii:
    """

    KEY_FIELDS = ('name',)  # xblock name is unique

    class Meta(ConfigurationModel.Meta):
        app_label = 'xblock_django'

    # boolean field 'enabled' inherited from parent ConfigurationModel
    name = models.CharField(max_length=255, null=False, db_index=True)
    deprecated = models.BooleanField(
        default=False,
        verbose_name=_('show deprecation messaging in Studio')
    )

    def __str__(self):
        return (  # noqa: UP032
            "XBlockConfiguration(name={}, enabled={}, deprecated={})"
        ).format(self.name, self.enabled, self.deprecated)


class XBlockStudioConfigurationFlag(ConfigurationModel):
    """
    Enables site-wide Studio configuration for XBlocks.

    .. no_pii:
    """

    class Meta:
        app_label = "xblock_django"

    # boolean field 'enabled' inherited from parent ConfigurationModel

    def __str__(self):
        return f"XBlockStudioConfigurationFlag(enabled={self.enabled})"


class XBlockStudioConfiguration(ConfigurationModel):
    """
    Studio editing configuration for a specific XBlock/template combination.

    .. no_pii:
    """
    KEY_FIELDS = ('name', 'template')  # xblock name/template combination is unique

    FULL_SUPPORT = 'fs'
    PROVISIONAL_SUPPORT = 'ps'
    UNSUPPORTED = 'us'

    SUPPORT_CHOICES = (
        (FULL_SUPPORT, _('Fully Supported')),
        (PROVISIONAL_SUPPORT, _('Provisionally Supported')),
        (UNSUPPORTED, _('Unsupported'))
    )

    # boolean field 'enabled' inherited from parent ConfigurationModel
    name = models.CharField(max_length=255, null=False, db_index=True)
    template = models.CharField(max_length=255, blank=True, default='')
    support_level = models.CharField(max_length=2, choices=SUPPORT_CHOICES, default=UNSUPPORTED)
    advanced_by_default = models.BooleanField(
        default=False,
        help_text=_(
            "Offer this XBlock in the Advanced component list of every course, without course teams having to "
            "add it to the course's Advanced Module List. Has no effect on XBlocks that are already offered as "
            "basic components."
        )
    )

    class Meta:
        app_label = "xblock_django"

    def __str__(self):
        return (  # noqa: UP032
            "XBlockStudioConfiguration(name={}, template={}, enabled={}, support_level={}, advanced_by_default={})"
        ).format(self.name, self.template, self.enabled, self.support_level, self.advanced_by_default)
