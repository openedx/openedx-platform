"""
Admin site configuration for third party authentication
"""
import csv

from config_models.admin import KeyedConfigurationModelAdmin
from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from .models import (
    _PSA_OAUTH2_BACKENDS,
    _PSA_SAML_BACKENDS,
    AppleMigrationUserIdInfo,
    LTIProviderConfig,
    OAuth2ProviderConfig,
    SAMLConfiguration,
    SAMLProviderConfig,
    SAMLProviderData,
)
from .tasks import fetch_saml_metadata, update_saml_users_social_auth_uid


class OAuth2ProviderConfigForm(forms.ModelForm):
    """ Django Admin form class for OAuth2ProviderConfig """
    backend_name = forms.ChoiceField(choices=((name, name) for name in _PSA_OAUTH2_BACKENDS))


class OAuth2ProviderConfigAdmin(KeyedConfigurationModelAdmin):
    """ Django Admin class for OAuth2ProviderConfig """
    form = OAuth2ProviderConfigForm

    def get_list_display(self, request):
        """ Don't show every single field in the admin change list """
        return (
            'name', 'enabled', 'slug', 'site', 'backend_name', 'secondary', 'skip_registration_form',
            'skip_email_verification', 'change_date', 'changed_by', 'edit_link',
        )

admin.site.register(OAuth2ProviderConfig, OAuth2ProviderConfigAdmin)


class SAMLProviderConfigForm(forms.ModelForm):
    """ Django Admin form class for SAMLProviderConfig """
    backend_name = forms.ChoiceField(choices=((name, name) for name in _PSA_SAML_BACKENDS))


class SAMLProviderConfigAdmin(KeyedConfigurationModelAdmin):
    """ Django Admin class for SAMLProviderConfig """
    form = SAMLProviderConfigForm
    search_fields = ['display_name']

    def get_queryset(self, request):
        """
        Filter the queryset to exclude the archived records unless it's the /change/ view.
        """
        if request.path.endswith('/change/'):
            return self.model.objects.all()
        return super().get_queryset(request).exclude(archived=True)

    def archive_provider_configuration(self, request, queryset):
        """
        Archived the selected provider configurations.
        """
        with transaction.atomic():
            for obj in queryset:
                self.model.objects.filter(pk=obj.pk).update(archived=True, enabled=False)
        self.message_user(request, _("Deleted the selected configuration(s)."))

    def get_list_display(self, request):
        """ Don't show every single field in the admin change list """
        return (
            'name_with_update_link', 'enabled', 'site', 'entity_id', 'metadata_source',
            'has_data', 'mode', 'saml_configuration', 'change_date', 'changed_by', 'csv_uuid_update_button',
        )

    list_display_links = None

    def get_actions(self, request):
        """
        Get the actions.
        """
        actions = super().get_actions(request)
        action_delete = {
            'archive_provider_configuration': (
                SAMLProviderConfigAdmin.archive_provider_configuration,
                'archive_provider_configuration',
                _('Delete the selected configuration')
            )
        }
        actions.update(action_delete)
        return actions

    def name_with_update_link(self, instance):
        """
        Record name with link for the change view.
        """
        if not instance.is_active:
            update_url = reverse(
                f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change',
                args=[instance.pk]
            )
            return format_html(
                '<a href="{}" style="color: #999999;">{}</a>',
                update_url,
                f'{instance.name}'
            )

        update_url = reverse(f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_add')
        update_url += f'?source={instance.pk}'
        return format_html('<a href="{}">{}</a>', update_url, instance.name)

    name_with_update_link.short_description = 'Name'

    def has_data(self, inst):
        """ Do we have cached metadata for this SAML provider? """
        if not inst.is_active:
            return None  # N/A
        records = SAMLProviderData.objects.filter(entity_id=inst.entity_id)
        for record in records:
            if record.is_valid():
                return True
        return False

    has_data.short_description = 'Metadata Ready'
    has_data.boolean = True

    def mode(self, inst):
        """ Indicate if debug_mode is enabled or not"""
        if inst.debug_mode:
            return format_html('<span style="color: red;">Debug</span>')
        return "Normal"

    def save_model(self, request, obj, form, change):
        """
        Post save: Queue an asynchronous metadata fetch to update SAMLProviderData.
        We only want to do this for manual edits done using the admin interface.

        Note: This only works if the celery worker and the app worker are using the
        same 'configuration' cache.
        """
        super().save_model(request, obj, form, change)
        fetch_saml_metadata.apply_async((), countdown=2)

    def get_urls(self):
        """ Extend the admin URLs to include the custom CSV upload URL. """
        urls = super().get_urls()
        custom_urls = [
            path('<slug:slug>/upload-csv/', self.admin_site.admin_view(self.upload_csv), name='upload_csv'),

        ]
        return custom_urls + urls

    @csrf_exempt
    def upload_csv(self, request, slug):
        """ Handle CSV upload and update UserSocialAuth model. """
        if not request.user.is_staff:
            raise Http404
        if request.method == 'POST':
            csv_file = request.FILES.get('csv_file')
            if not csv_file or not csv_file.name.endswith('.csv'):
                self.message_user(request, "Please upload a valid CSV file.", level=messages.ERROR)
            else:
                try:
                    decoded_file = csv_file.read().decode('utf-8').splitlines()
                    reader = csv.DictReader(decoded_file)
                    update_saml_users_social_auth_uid(reader, slug)
                    self.message_user(request, "CSV file has been processed successfully.")
                except Exception as e:  # pylint: disable=broad-except
                    self.message_user(request, f"Failed to process CSV file: {e}", level=messages.ERROR)

        # Always redirect back to the SAMLProviderConfig listing page
        return HttpResponseRedirect(reverse('admin:third_party_auth_samlproviderconfig_changelist'))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """ Extend the change view to include CSV upload. """
        extra_context = extra_context or {}
        extra_context['show_csv_upload'] = True
        return super().change_view(request, object_id, form_url, extra_context)

    def csv_uuid_update_button(self, obj):
        """ Add CSV upload button to the form. """
        if obj:
            form_url = reverse('admin:upload_csv', args=[obj.slug])
            return format_html(
                '<form method="post" enctype="multipart/form-data" action="{}">'
                '<input type="file" name="csv_file" accept=".csv" style="margin-bottom: 10px;">'
                '<button type="submit" class="button">Upload CSV</button>'
                '</form>',
                form_url
            )
        return ""

    csv_uuid_update_button.short_description = 'UUID UPDATE CSV'
    csv_uuid_update_button.allow_tags = True

    def get_readonly_fields(self, request, obj=None):
        """ Conditionally add csv_uuid_update_button to readonly fields. """
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj:
            readonly_fields.append('csv_uuid_update_button')
        return readonly_fields

admin.site.register(SAMLProviderConfig, SAMLProviderConfigAdmin)


class SAMLConfigurationAdmin(KeyedConfigurationModelAdmin):
    """ Django Admin class for SAMLConfiguration """
    def get_list_display(self, request):
        """ Shorten the public/private keys in the change view """
        return (
            'site', 'slug', 'change_date', 'changed_by', 'enabled', 'entity_id',
            'org_info_str', 'key_summary', 'edit_link',
        )

    def key_summary(self, inst):
        """ Short summary of the key pairs configured """
        public_key = inst.get_setting('SP_PUBLIC_CERT')
        private_key = inst.get_setting('SP_PRIVATE_KEY')
        if not public_key or not private_key:
            return format_html('<em>Key pair incomplete/missing</em>')
        pub1, pub2 = public_key[0:10], public_key[-10:]
        priv1, priv2 = private_key[0:10], private_key[-10:]
        return format_html('Public: {}…{}<br>Private: {}…{}', pub1, pub2, priv1, priv2)

admin.site.register(SAMLConfiguration, SAMLConfigurationAdmin)


class SAMLProviderDataAdmin(admin.ModelAdmin):
    """ Django Admin class for SAMLProviderData (Read Only) """
    list_display = ('entity_id', 'is_valid', 'fetched_at', 'expires_at', 'sso_url')
    readonly_fields = ('is_valid', )

    def get_readonly_fields(self, request, obj=None):
        if obj:  # editing an existing object
            return [field.name for field in self.model._meta.get_fields()]
        return self.readonly_fields


admin.site.register(SAMLProviderData, SAMLProviderDataAdmin)


class LTIProviderConfigAdmin(KeyedConfigurationModelAdmin):
    """ Django Admin class for LTIProviderConfig """

    exclude = (
        'icon_class',
        'icon_image',
        'secondary',
    )

    def get_list_display(self, request):
        """ Don't show every single field in the admin change list """
        return (
            'name',
            'enabled',
            'site',
            'lti_consumer_key',
            'lti_max_timestamp_age',
            'change_date',
            'changed_by',
            'edit_link',
        )

admin.site.register(LTIProviderConfig, LTIProviderConfigAdmin)


class AppleMigrationUserIdInfoAdmin(admin.ModelAdmin):
    """ Django Admin class for AppleMigrationUserIdInfo """


admin.site.register(AppleMigrationUserIdInfo, AppleMigrationUserIdInfoAdmin)
