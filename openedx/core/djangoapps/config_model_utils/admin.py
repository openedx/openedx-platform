"""
Convenience classes for defining StackedConfigModel Admin pages.
"""


from config_models.admin import ConfigurationModelAdmin
from django import forms
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangolib.markup import HTML, Text


class CourseOverviewField(forms.ModelChoiceField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        return super().to_python(CourseKey.from_string(value))


class StackedConfigModelAdminForm(forms.ModelForm):
    class Meta:
        field_classes = {
            'course': CourseOverviewField
        }


class StackedConfigModelAdmin(ConfigurationModelAdmin):
    """
    A specialized ConfigurationModel ModelAdmin for StackedConfigModels.
    """
    form = StackedConfigModelAdminForm

    raw_id_fields = ('course',)
    search_fields = ('site__domain', 'org', 'org_course', 'course__id')

    def get_fieldsets(self, request, obj=None):
        return (
            ('Context', {
                'fields': self.key_fields,
                'description': Text(_(
                    'These define the context to enable this configuration on. '
                    'If no values are set, then the configuration applies globally. '
                    'If a single value is set, then the configuration applies to all courses '
                    'within that context. At most one value can be set at a time.{br}'
                    'If multiple contexts apply to a course (for example, if configuration '
                    'is specified for the course specifically, and for the org that the course '
                    'is in, then the more specific context overrides the more general context.'
                )).format(br=HTML('<br>')),
            }),
            ('Configuration', {
                'fields': self.stackable_fields,
                'description': _(
                    'If any of these values are left empty or "Unknown", then their value '
                    'at runtime will be retrieved from the next most specific context that applies. '
                    'For example, if "Enabled" is left as "Unknown" in the course context, then that '
                    'course will be Enabled only if the org that it is in is Enabled.'
                ),
            })
        )

    @property
    def key_fields(self):
        return list(self.model.KEY_FIELDS)

    @property
    def stackable_fields(self):
        return list(self.model.STACKABLE_FIELDS)

    def get_config_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        return [field for field in fields if field not in self.key_fields]

    def get_fields(self, request, obj=None):
        return self.key_fields + self.get_config_fields(request, obj)

    def get_displayable_field_names(self):
        """
        Return all field names, excluding reverse foreign key relationships.
        """
        names = super().get_displayable_field_names()
        fixed_names = ['id', 'change_date', 'changed_by'] + list(self.model.KEY_FIELDS)
        return fixed_names + [name for name in names if name not in fixed_names]
