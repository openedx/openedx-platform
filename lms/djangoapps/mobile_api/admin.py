"""
Django admin dashboard configuration for LMS XBlock infrastructure.
"""


from config_models.admin import ConfigurationModelAdmin  # noqa: I001
from django.contrib import admin

from .models import AppVersionConfig, IgnoreMobileAvailableFlagConfig, MobileApiConfig, MobileConfig

admin.site.register(MobileApiConfig, ConfigurationModelAdmin)
admin.site.register(IgnoreMobileAvailableFlagConfig, ConfigurationModelAdmin)


class AppVersionConfigAdmin(admin.ModelAdmin):
    """ Admin class for AppVersionConfig model """
    fields = ('platform', 'version', 'expire_at', 'enabled')
    list_filter = ['platform']

    class Meta:
        ordering = ['-major_version', '-minor_version', '-patch_version']

    def get_list_display(self, __):
        """ defines fields to display in list view """
        return ['platform', 'version', 'expire_at', 'enabled', 'created_at', 'updated_at']


class MobileConfigAdmin(admin.ModelAdmin):
    """ Admin class for MobileConfig model """
    fields = ('name', 'value')
    readonly_fields = ['created', 'modified']

    class Meta:
        ordering = ['name']


admin.site.register(AppVersionConfig, AppVersionConfigAdmin)
admin.site.register(MobileConfig, MobileConfigAdmin)
