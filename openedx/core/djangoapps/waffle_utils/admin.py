"""
Django admin page for waffle utils models
"""

from config_models.admin import KeyedConfigurationModelAdmin
from django.contrib import admin

from .forms import WaffleFlagCourseOverrideAdminForm, WaffleFlagOrgOverrideAdminForm
from .models import WaffleFlagCourseOverrideModel, WaffleFlagOrgOverrideModel


class WaffleFlagCourseOverrideAdmin(KeyedConfigurationModelAdmin):
    """
    Admin for course override of waffle flags.

    Includes search by course_id and waffle_flag.

    """
    form = WaffleFlagCourseOverrideAdminForm
    search_fields = ['waffle_flag', 'course_id']
    fieldsets = (
        (None, {
            'fields': ('waffle_flag', 'course_id', 'note', 'override_choice', 'enabled'),
            'description':
                'Enter a valid course id and an existing waffle flag. The waffle flag name is not validated.'
        }),
    )


class WaffleFlagOrgOverrideAdmin(KeyedConfigurationModelAdmin):
    """
    Admin for org override of waffle flags.

    Includes search by org and waffle_flag.

    """
    form = WaffleFlagOrgOverrideAdminForm
    search_fields = ['waffle_flag', 'org']
    fieldsets = (
        (None, {
            'fields': ('waffle_flag', 'org', 'note', 'override_choice', 'enabled'),
            'description':
                'Enter a valid organization and an existing waffle flag. The waffle flag name is not validated.'
        }),
    )

admin.site.register(WaffleFlagCourseOverrideModel, WaffleFlagCourseOverrideAdmin)
admin.site.register(WaffleFlagOrgOverrideModel, WaffleFlagOrgOverrideAdmin)
