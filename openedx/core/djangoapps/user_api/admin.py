"""
Django admin configuration pages for the user_api app
"""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _

from openedx.core.djangoapps.user_api.accounts.forms import RetirementQueueDeletionForm

from .models import (
    BulkUserRetirementConfig,
    RetirementState,
    UserRetirementPartnerReportingStatus,
    UserRetirementRequest,
    UserRetirementStatus,
)


@admin.register(RetirementState)
class RetirementStateAdmin(admin.ModelAdmin):
    """
    Admin interface for the RetirementState model.
    """
    list_display = ('state_name', 'state_execution_order', 'is_dead_end_state', 'required',)
    list_filter = ('is_dead_end_state', 'required',)
    search_fields = ('state_name',)

    class Meta:
        model = RetirementState


@admin.register(UserRetirementStatus)
class UserRetirementStatusAdmin(admin.ModelAdmin):
    """
    Admin interface for the UserRetirementStatus model.
    """
    list_display = ('user', 'original_username', 'current_state', 'modified', 'retirement_actions')
    list_filter = ('current_state',)
    raw_id_fields = ('user',)
    search_fields = ('original_username', 'retired_username', 'original_email', 'retired_email', 'original_name')

    def cancel_retirement(self, request, retirement_id):
        """
        Executed when the admin clicks the "Cancel" button on a UserRetirementStatus row,
        this handles the confirmation view form, top level error handling, and permissions.
        """
        if not request.user.has_perm('user_api.change_userretirementstatus'):
            return HttpResponseForbidden(_("Permission Denied"))

        retirement = self.get_object(request, retirement_id)

        redirect_url = reverse(
            'admin:user_api_userretirementstatus_changelist',
            current_app=self.admin_site.name,
        )

        if retirement is None:
            self.message_user(request, _('Retirement does not exist!'), level=messages.ERROR)
            return HttpResponseRedirect(redirect_url)

        if request.method != 'POST':
            form = RetirementQueueDeletionForm()
        else:
            form = RetirementQueueDeletionForm(request.POST)
            if form.is_valid():
                try:
                    form.save(retirement)
                    self.message_user(request, _('Success'))
                    return HttpResponseRedirect(redirect_url)
                except ValidationError:
                    # An exception in form.save will display errors on the form page
                    pass

        context = self.admin_site.each_context(request)
        context['opts'] = self.model._meta
        context['form'] = form
        context['retirement'] = retirement

        return TemplateResponse(
            request,
            'admin/user_api/accounts/cancel_retirement_action.html',
            context,
        )

    def get_urls(self):
        """
        Adds our custom URL to the admin
        """
        urls = super().get_urls()
        custom_urls = [
            path('<path:retirement_id>/cancel_retirement/', self.admin_site.admin_view(self.cancel_retirement),
                 name='cancel-retirement',
                 ),
        ]
        return custom_urls + urls

    def retirement_actions(self, obj):
        """
        Creates the HTML button in the admin for cancelling retirements,
        but only if the row is in the right state.
        """
        try:
            if obj.current_state.state_name == 'PENDING':
                return format_html(
                    '<a class="button" href="{}">{}</a>&nbsp;',
                    reverse('admin:cancel-retirement', args=[obj.pk]),
                    _('Cancel')
                )
            return format_html('')
        except RetirementState.DoesNotExist:
            # If the states don't exist, nothing to do here
            return format_html('')

    retirement_actions.short_description = _('Actions')

    def get_actions(self, request):
        """
        Removes the default bulk delete option provided by Django,
        it doesn't do what we need for this model.
        """
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def has_add_permission(self, request):
        """
        Removes the "add" button from admin
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Removes the "delete" button from admin
        """
        return False

    class Meta:
        model = UserRetirementStatus


@admin.register(UserRetirementRequest)
class UserRetirementRequestAdmin(admin.ModelAdmin):
    """
    Admin interface for the UserRetirementRequest model.
    """
    list_display = ('user', 'created')
    raw_id_fields = ('user',)

    class Meta:
        model = UserRetirementRequest


@admin.register(UserRetirementPartnerReportingStatus)
class UserRetirementPartnerReportingStatusAdmin(admin.ModelAdmin):
    """
    Admin interface for the UserRetirementPartnerReportingStatus model.
    """
    list_display = (
        'user_id',  # See user_id() below.
        'original_username',
        'is_being_processed',
        'modified',
    )
    list_filter = ('is_being_processed',)
    raw_id_fields = ('user',)
    search_fields = ('user__id', 'original_username', 'original_email', 'original_name')
    actions = [
        'reset_state_false',
        'reset_state_true',
    ]

    class Meta:
        model = UserRetirementPartnerReportingStatus

    def user_id(self, obj):
        """
        List display for the user_id field.

        This is an alternative to listing the "user" field directly, since that would print the retired (hashed)
        username which isn't super helpful.
        """
        return obj.user.id

    def reset_state_false(self, request, queryset):
        """
        Action callback for bulk resetting is_being_processed to False (0).
        """
        rows_updated = queryset.update(is_being_processed=0)
        if rows_updated == 1:
            message_bit = "one user was"
        else:
            message_bit = "%s users were" % rows_updated  # noqa: UP031
        self.message_user(request, "%s successfully reset to False." % message_bit)  # noqa: UP031

    reset_state_false.short_description = "Reset is_being_processed to False"

    def reset_state_true(self, request, queryset):
        """
        Action callback for bulk resetting is_being_processed to True (1).
        """
        rows_updated = queryset.update(is_being_processed=1)
        if rows_updated == 1:
            message_bit = "one user was"
        else:
            message_bit = "%s users were" % rows_updated  # noqa: UP031
        self.message_user(request, "%s successfully reset to True." % message_bit)  # noqa: UP031

    reset_state_true.short_description = "Reset is_being_processed to True"


@admin.register(BulkUserRetirementConfig)
class BulkUserRetirementConfigurationAdmin(admin.ModelAdmin):
    """
    Admin for BulkUserRetirementConfig model.
    """
    list_display = ('id', 'enabled', 'changed_by', 'change_date')
