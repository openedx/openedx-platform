"""
Customize the django admin experience
"""
from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.contrib.admin.utils import quote
from simple_history.admin import SimpleHistoryAdmin

from openedx.core.djangoapps.config_model_utils.admin import StackedConfigModelAdmin

from .models import DiscussionsConfiguration, ProviderFilter


class DiscussionsConfigurationAdmin(SimpleHistoryAdmin):
    """
    Customize the admin interface for the discussions configuration
    """

    search_fields = (
        'context_key',
        'enabled',
        'provider_type',
    )
    list_filter = (
        'enabled',
        'provider_type',
    )

    def change_view(self, request, object_id=None, form_url="", extra_context=None):
        return super().change_view(request, quote(object_id), form_url, extra_context)


class AllowListFilter(SimpleListFilter):
    """
    Customize the admin interface for the AllowList
    """

    title = 'Allow List'
    parameter_name = 'allow'

    def lookups(self, request, model_admin):
        queryset = model_admin.get_queryset(request)
        values = tuple(
            (
                ','.join(filters[self.parameter_name] or ['None']),
                ', '.join(filters[self.parameter_name] or ['None']),
            )
            for filters in queryset.values(self.parameter_name).distinct()
        )
        return values

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            filter_kwargs = {}
            if ',' in value:
                for v in value.split(','):
                    filter_kwargs[self.parameter_name + '__contains'] = v
                    queryset = queryset.filter(**filter_kwargs)
            else:
                if value == 'None':
                    filter_kwargs[self.parameter_name + '__exact'] = ''
                else:
                    filter_kwargs[self.parameter_name + '__contains'] = value
                queryset = queryset.filter(**filter_kwargs)
        return queryset


class DenyListFilter(AllowListFilter):
    """
    Customize the admin interface for the DenyList
    """
    title = 'Deny List'
    parameter_name = 'deny'


class ProviderFilterAdmin(StackedConfigModelAdmin):
    """
    Customize the admin interface for the ProviderFilter
    """

    search_fields = (
        'allow',
        'deny',
    )
    list_filter = (
        'enabled',
        AllowListFilter,
        DenyListFilter,
    )

admin.site.register(DiscussionsConfiguration, DiscussionsConfigurationAdmin)
admin.site.register(ProviderFilter, ProviderFilterAdmin)
