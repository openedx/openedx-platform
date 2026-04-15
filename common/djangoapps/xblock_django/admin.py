"""
Django admin dashboard configuration.
"""


from config_models.admin import ConfigurationModelAdmin, KeyedConfigurationModelAdmin
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from common.djangoapps.xblock_django.models import (  # lint-amnesty, pylint: disable=line-too-long
    XBlockConfiguration,
    XBlockStudioConfiguration,
    XBlockStudioConfigurationFlag,
)


class XBlockConfigurationAdmin(KeyedConfigurationModelAdmin):
    """
    Admin for XBlockConfiguration.
    """
    fieldsets = (
        ('XBlock Name', {
            'fields': ('name',)
        }),
        ('Enable/Disable XBlock', {
            'description': _('To disable the XBlock and prevent rendering in the LMS, leave "Enabled" deselected; '
                             'for clarity, update XBlockStudioConfiguration support state accordingly.'),
            'fields': ('enabled',)
        }),
        ('Deprecate XBlock', {
            'description': _("Only XBlocks listed in a course's Advanced Module List can be flagged as deprecated. "
                             "Remember to update XBlockStudioConfiguration support state accordingly, as deprecated "
                             "does not impact whether or not new XBlock instances can be created in Studio."),
            'fields': ('deprecated',)
        }),
    )


class XBlockStudioConfigurationAdmin(KeyedConfigurationModelAdmin):
    """
    Admin for XBlockStudioConfiguration.
    """
    fieldsets = (
        ('', {
            'fields': ('name', 'template')
        }),
        ('Enable Studio Authoring', {
            'description': _(
                'XBlock/template combinations that are disabled cannot be edited in Studio, regardless of support '
                'level. Remember to also check if all instances of the XBlock are disabled in XBlockConfiguration.'
            ),
            'fields': ('enabled',)
        }),
        ('Support Level', {
            'description': _(
                "Enabled XBlock/template combinations with full or provisional support can always be created "
                "in Studio. Unsupported XBlock/template combinations require course author opt-in."
            ),
            'fields': ('support_level',)
        }),
    )


admin.site.register(XBlockConfiguration, XBlockConfigurationAdmin)
admin.site.register(XBlockStudioConfiguration, XBlockStudioConfigurationAdmin)
admin.site.register(XBlockStudioConfigurationFlag, ConfigurationModelAdmin)
