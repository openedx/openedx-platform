""" Django admin pages for student app """


from functools import wraps

from config_models.admin import ConfigurationModelAdmin
from dal_select2.views import Select2ListView
from dal_select2.widgets import ListSelect2
from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.admin.utils import unquote
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.db import models, router, transaction
from django.http import HttpResponseRedirect
from django.http.request import QueryDict
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_countries import countries
from edx_toggles.toggles import WaffleSwitch
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.constants import LANGUAGE_CHOICES
from common.djangoapps.student.models import (
    AccountRecovery,
    AccountRecoveryConfiguration,
    AllowedAuthUser,
    BulkChangeEnrollmentConfiguration,
    BulkUnenrollConfiguration,
    CourseAccessRole,
    CourseAccessRoleHistory,
    CourseEnrollment,
    CourseEnrollmentAllowed,
    CourseEnrollmentCelebration,
    DashboardConfiguration,
    LinkedInAddToProfileConfiguration,
    LoginFailures,
    PendingNameChange,
    Registration,
    RegistrationCookieConfiguration,
    UserAttribute,
    UserCelebration,
    UserProfile,
    UserTestGroup,
)
from common.djangoapps.student.roles import REGISTERED_ACCESS_ROLES
from openedx.core.lib.courses import clean_course_id
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

User = get_user_model()  # pylint:disable=invalid-name

# .. toggle_name: student.courseenrollment_admin
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: This toggle will enable the rendering of the admin view of the CourseEnrollment model.
# .. toggle_warning: Enabling this toggle may cause performance problems. The CourseEnrollment admin view
#     makes DB queries that could cause site outages for a large enough Open edX installation.
# .. toggle_use_cases: opt_in, open_edx
# .. toggle_creation_date: 2018-08-01
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/18638
COURSE_ENROLLMENT_ADMIN_SWITCH = WaffleSwitch('student.courseenrollment_admin', __name__)


class _Check:
    """
    A method decorator that pre-emptively returns false if a feature is disabled.
    Otherwise, it returns the return value of the decorated method.

    To use, add this decorator above a method and pass in a function that returns
    a boolean indicating whether the feature is enabled.

    Example:
    @_Check.is_enabled(FEATURE_TOGGLE.is_enabled)
    """
    @classmethod
    def is_enabled(cls, is_enabled_func):
        """
        See above docstring.
        """
        def inner(func):
            @wraps(func)
            def decorator(*args, **kwargs):
                if not is_enabled_func():
                    return False
                return func(*args, **kwargs)
            return decorator
        return inner


class DisableEnrollmentAdminMixin:
    """ Disables admin access to an admin page that scales with enrollments, as performance is poor at that size. """
    @_Check.is_enabled(COURSE_ENROLLMENT_ADMIN_SWITCH.is_enabled)
    def has_view_permission(self, request, obj=None):
        """
        Returns True if CourseEnrollment objects can be viewed via the admin view.
        """
        return super().has_view_permission(request, obj)

    @_Check.is_enabled(COURSE_ENROLLMENT_ADMIN_SWITCH.is_enabled)
    def has_add_permission(self, request):
        """
        Returns True if CourseEnrollment objects can be added via the admin view.
        """
        return super().has_add_permission(request)

    @_Check.is_enabled(COURSE_ENROLLMENT_ADMIN_SWITCH.is_enabled)
    def has_change_permission(self, request, obj=None):
        """
        Returns True if CourseEnrollment objects can be modified via the admin view.
        """
        return super().has_change_permission(request, obj)

    @_Check.is_enabled(COURSE_ENROLLMENT_ADMIN_SWITCH.is_enabled)
    def has_delete_permission(self, request, obj=None):
        """
        Returns True if CourseEnrollment objects can be deleted via the admin view.
        """
        return super().has_delete_permission(request, obj)

    @_Check.is_enabled(COURSE_ENROLLMENT_ADMIN_SWITCH.is_enabled)
    def has_module_permission(self, request):
        """
        Returns True if links to the CourseEnrollment admin view can be displayed.
        """
        return super().has_module_permission(request)


class CourseAccessRoleForm(forms.ModelForm):
    """Form for adding new Course Access Roles view the Django Admin Panel."""

    class Meta:
        model = CourseAccessRole
        fields = '__all__'  # noqa: DJ007

    email = forms.EmailField(required=True)
    COURSE_ACCESS_ROLES = [(role_name, role_name) for role_name in REGISTERED_ACCESS_ROLES.keys()]  # pylint: disable=consider-iterating-dictionary
    role = forms.ChoiceField(choices=COURSE_ACCESS_ROLES)

    def clean_course_id(self):
        """
        Validate the course id
        """
        if self.cleaned_data['course_id']:
            return clean_course_id(self)

    def clean_org(self):
        """If org and course-id exists then Check organization name
        against the given course.
        """
        if self.cleaned_data.get('course_id') and self.cleaned_data['org']:
            org = self.cleaned_data['org']
            org_name = self.cleaned_data.get('course_id').org
            if org.lower() != org_name.lower():
                raise forms.ValidationError(
                    "Org name {} is not valid. Valid name is {}.".format(  # noqa: UP032
                        org, org_name
                    )
                )

        return self.cleaned_data['org']

    def clean_email(self):
        """
        Checking user object against given email id.
        """
        email = self.cleaned_data['email']
        try:
            user = User.objects.get(email=email)
        except Exception:
            raise forms.ValidationError(  # pylint: disable=raise-missing-from  # noqa: B904
                "Email does not exist. Could not find {email}. Please re-enter email address".format(  # noqa: UP032
                    email=email
                )
            )

        return user

    def clean(self):
        """
        Checking the course already exists in db.
        """
        cleaned_data = super().clean()
        if not self.errors:
            if CourseAccessRole.objects.filter(
                    user=cleaned_data.get("email"),
                    org=cleaned_data.get("org"),
                    course_id=cleaned_data.get("course_id"),
                    role=cleaned_data.get("role")
            ).exists():
                raise forms.ValidationError("Duplicate Record.")

        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.user_id:
            self.fields['email'].initial = self.instance.user.email


@admin.register(CourseAccessRole)
class CourseAccessRoleAdmin(admin.ModelAdmin):
    """Admin panel for the Course Access Role. """
    form = CourseAccessRoleForm
    raw_id_fields = ("user",)
    exclude = ("user",)

    fieldsets = (
        (None, {
            'fields': ('email', 'course_id', 'org', 'role',)
        }),
    )

    list_display = (
        'id', 'user', 'org', 'course_id', 'role',
    )
    search_fields = (
        'id', 'user__username', 'user__email', 'org', 'course_id', 'role',
    )

    def save_model(self, request, obj, form, change):
        obj.user = form.cleaned_data['email']
        super().save_model(request, obj, form, change)


@admin.register(CourseAccessRoleHistory)
class CourseAccessRoleHistoryAdmin(admin.ModelAdmin):
    """Admin panel for the Course Access Role History."""
    list_display = (
        'id', 'user', 'org', 'course_id', 'role', 'action_type', 'changed_by', 'created'
    )
    list_filter = (
        'action_type', 'org', 'role'
    )
    search_fields = (
        'user__username', 'org', 'course_id', 'role', 'action_type', 'changed_by__username'
    )
    readonly_fields = (
        'user', 'org', 'course_id', 'role', 'action_type', 'changed_by', 'created', 'modified'
    )
    actions = ['revert_selected_history', 'delete_selected_history_entries']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def revert_selected_history(self, request, queryset):
        """
        Admin action to revert selected CourseAccessRoleHistory entries.
        """
        if not request.user.has_perm('student.can_revert_course_access_role'):
            self.message_user(request, "You do not have permission to revert course access roles.", level='ERROR')
            return

        reverted_count = 0
        for history_record in queryset:
            try:
                with transaction.atomic():
                    if history_record.action_type == 'created':
                        CourseAccessRole.objects.filter(
                            user=history_record.user,
                            org=history_record.org,
                            course_id=history_record.course_id,
                            role=history_record.role
                        ).delete()
                        self.message_user(
                            request, f"Successfully reverted creation of role for "
                            f"{history_record.user.username} in {history_record.course_id}"
                        )
                    elif history_record.action_type == 'updated':
                        if history_record.old_values:
                            CourseAccessRole.objects.update_or_create(
                                user_id=history_record.old_values['user_id'],
                                org=history_record.old_values['org'],
                                course_id=history_record.old_values['course_id'],
                                defaults={'role': history_record.old_values['role']}
                            )
                            self.message_user(
                                request, f"Successfully reverted update of role for "
                                f"{history_record.user.username} to {history_record.old_values['role']} "
                                f"in {history_record.course_id}"
                            )
                        else:
                            self.message_user(
                                request, f"Cannot revert update for record {history_record.id}: "
                                f"old_values not found.", level='WARNING'
                            )
                    elif history_record.action_type == 'deleted':
                        CourseAccessRole.objects.update_or_create(
                            user=history_record.user,
                            org=history_record.org,
                            course_id=history_record.course_id,
                            role=history_record.role
                        )
                        self.message_user(
                            request, f"Successfully reverted deletion of role for "
                            f"{history_record.user.username} in {history_record.course_id}"
                        )
                    reverted_count += 1
            except Exception as e:  # pylint: disable=broad-except
                self.message_user(request, f"Error reverting record {history_record.id}: {e}", level='ERROR')

        if reverted_count > 0:
            self.message_user(
                request,
                ngettext(
                    "Successfully reverted %(count)d selected history entry.",
                    "Successfully reverted %(count)d selected history entries.",
                    reverted_count
                ) % {'count': reverted_count},
            )
    revert_selected_history.short_description = "Revert selected history entries"

    def delete_selected_history_entries(self, request, queryset):
        """
        Admin action to delete selected CourseAccessRoleHistory entries.
        """
        if not request.user.has_perm('student.can_delete_course_access_role_history'):
            self.message_user(
                request, "You do not have permission to delete course access role history entries.",
                level='ERROR'
            )
            return

        deleted_count = 0
        for history_record in queryset:
            try:
                history_record.delete()
                deleted_count += 1
            except Exception as e:  # pylint: disable=broad-except
                self.message_user(request, f"Error deleting record {history_record.id}: {e}", level='ERROR')

        if deleted_count > 0:
            self.message_user(
                request,
                ngettext(
                    "Successfully deleted %(count)d selected history entry.",
                    "Successfully deleted %(count)d selected history entries.",
                    deleted_count
                ) % {'count': deleted_count},
                level='SUCCESS',
            )
    delete_selected_history_entries.short_description = "Delete selected history entries"


@admin.register(LinkedInAddToProfileConfiguration)
class LinkedInAddToProfileConfigurationAdmin(admin.ModelAdmin):
    """Admin interface for the LinkedIn Add to Profile configuration. """

    class Meta:
        model = LinkedInAddToProfileConfiguration


class CourseEnrollmentForm(forms.ModelForm):
    """ Form for Course Enrollments in the Django Admin Panel. """
    def __init__(self, *args, **kwargs):
        # If args is a QueryDict, then the ModelForm addition request came in as a POST with a course ID string.
        # Change the course ID string to a CourseLocator object by copying the QueryDict to make it mutable.
        if args and 'course' in args[0] and isinstance(args[0], QueryDict):
            args_copy = args[0].copy()
            try:
                args_copy['course'] = CourseKey.from_string(args_copy['course'])
            except InvalidKeyError:
                raise forms.ValidationError("Cannot make a valid CourseKey from id {}!".format(args_copy['course']))  # pylint: disable=raise-missing-from,line-too-long  # noqa: B904
            args = [args_copy]

        super().__init__(*args, **kwargs)

        if self.data.get('course'):
            try:
                self.data['course'] = CourseKey.from_string(self.data['course'])
            except AttributeError:
                # Change the course ID string to a CourseLocator.
                # On a POST request, self.data is a QueryDict and is immutable - so this code will fail.
                # However, the args copy above before the super() call handles this case.
                pass

    def clean_course_id(self):  # pylint: disable=missing-function-docstring
        course_id = self.cleaned_data['course']
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            raise forms.ValidationError(f"Cannot make a valid CourseKey from id {course_id}!")  # pylint: disable=raise-missing-from  # noqa: B904

        if not modulestore().has_course(course_key):
            raise forms.ValidationError(f"Cannot find course with id {course_id} in the modulestore")

        return course_key

    def save(self, *args, **kwargs):  # pylint: disable=signature-differs, unused-argument
        course_enrollment = super().save(commit=False)
        user = self.cleaned_data['user']
        course_overview = self.cleaned_data['course']
        enrollment = CourseEnrollment.get_or_create_enrollment(user, course_overview.id)
        course_enrollment.id = enrollment.id
        course_enrollment.created = enrollment.created
        return course_enrollment

    class Meta:
        model = CourseEnrollment
        fields = '__all__'  # noqa: DJ007


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(DisableEnrollmentAdminMixin, admin.ModelAdmin):
    """ Admin interface for the CourseEnrollment model. """
    list_display = ('id', 'course_id', 'mode', 'user', 'is_active',)
    list_filter = ('mode', 'is_active',)
    raw_id_fields = ('user', 'course')
    search_fields = ('course__id', 'mode', 'user__username',)
    form = CourseEnrollmentForm

    def get_search_results(self, request, queryset, search_term):
        qs, use_distinct = super().get_search_results(request, queryset, search_term)

        # annotate each enrollment with whether the username was an
        # exact match for the search term
        qs = qs.annotate(exact_username_match=models.Case(
            models.When(user__username=search_term, then=models.Value(True)),
            default=models.Value(False),
            output_field=models.BooleanField()))

        # present exact matches first
        qs = qs.order_by('-exact_username_match', 'user__username', 'course_id')

        return qs, use_distinct

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')  # pylint: disable=no-member, super-with-arguments


@method_decorator(login_required, name='dispatch')
class LanguageAutocomplete(Select2ListView):
    def get_list(self):
        if not self.request.user.is_staff:
            return []
        return [lang for lang in LANGUAGE_CHOICES if self.q.lower() in lang.lower()]


@method_decorator(login_required, name='dispatch')
class CountryAutocomplete(Select2ListView):
    """
    Autocomplete view for selecting countries using Select2.

    Only accessible to authenticated staff users. Filters the list of countries
    based on the user input (query string) and returns matching results.
    """

    def get_list(self):
        """
        Returns a filtered list of country tuples (code, name) based on the query.
        """
        if not self.request.user.is_staff:
            return []
        results = []
        for code, name in countries:
            if self.q.lower() in name.lower():
                results.append((code, name))
        return results

    def get_result_label(self, item):
        """ What the user sees in the dropdown """
        return dict(countries).get(item, item)

    def get_result_value(self, item):
        """ What gets sent back on selection (the code) """
        return item


class UserProfileInlineForm(forms.ModelForm):
    """
    A custom form for editing the UserProfile model within the admin inline.
    """
    language = forms.CharField(
        required=False,
        widget=ListSelect2(url='admin:language-autocomplete')  # pylint: disable=no-member
    )
    country = forms.CharField(
        required=False,
        widget=ListSelect2(url='admin:country-autocomplete')   # pylint: disable=no-member
    )

    class Meta:
        model = UserProfile
        fields = '__all__'  # noqa: DJ007

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            if self.instance.country:
                code = self.instance.country
                name = countries.name(code) if code in countries else code
                self.fields['country'].widget.choices = [(code, name)]
                self.initial['country'] = code

            if self.instance.language:
                language = self.instance.language
                self.fields['language'].initial = language
                self.fields['language'].widget.choices = [(language, language)]


class UserProfileInline(admin.StackedInline):
    """ Inline admin interface for UserProfile model. """
    model = UserProfile
    form = UserProfileInlineForm
    can_delete = False
    verbose_name_plural = _('User profile')


class AccountRecoveryInline(admin.StackedInline):
    """ Inline admin interface for AccountRecovery model. """
    model = AccountRecovery
    can_delete = False
    verbose_name = _('Account recovery')
    verbose_name_plural = _('Account recovery')


class UserChangeForm(BaseUserChangeForm):
    """
    Override the default UserChangeForm such that the password field
    does not contain a link to a 'change password' form.
    """
    last_name = forms.CharField(max_length=30, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not settings.FEATURES.get('ENABLE_CHANGE_USER_PASSWORD_ADMIN'):
            self.fields["password"] = ReadOnlyPasswordHashField(
                label=_("Password"),
                help_text=_(
                    "Raw passwords are not stored, so there is no way to see this "
                    "user's password."
                ),
            )


class UserAdmin(BaseUserAdmin):
    """ Admin interface for the User model. """
    inlines = (UserProfileInline, AccountRecoveryInline)
    form = UserChangeForm

    def get_readonly_fields(self, request, obj=None):
        """
        Allows editing the users while skipping the username check, so we can have Unicode username with no problems.
        The username is marked read-only when editing existing users regardless of `ENABLE_UNICODE_USERNAME`, to simplify the bokchoy tests.  # pylint: disable=line-too-long
        """
        django_readonly = super().get_readonly_fields(request, obj)
        if obj:
            return django_readonly + ('username',)
        return django_readonly

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'language-autocomplete/',
                LanguageAutocomplete.as_view(),
                name='language-autocomplete'
            ),
            path('country-autocomplete/', CountryAutocomplete.as_view(), name='country-autocomplete'),
        ]
        return custom_urls + urls


@admin.register(UserAttribute)
class UserAttributeAdmin(admin.ModelAdmin):
    """ Admin interface for the UserAttribute model. """
    list_display = ('user', 'name', 'value',)
    list_filter = ('name',)
    raw_id_fields = ('user',)
    search_fields = ('name', 'value', 'user__username',)

    class Meta:
        model = UserAttribute


@admin.register(CourseEnrollmentAllowed)
class CourseEnrollmentAllowedAdmin(admin.ModelAdmin):
    """ Admin interface for the CourseEnrollmentAllowed model. """
    list_display = ('email', 'course_id', 'auto_enroll',)
    search_fields = ('email', 'course_id',)

    class Meta:
        model = CourseEnrollmentAllowed


@admin.register(LoginFailures)
class LoginFailuresAdmin(admin.ModelAdmin):
    """Admin interface for the LoginFailures model. """
    list_display = ('user', 'failure_count', 'lockout_until')
    raw_id_fields = ('user',)
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    actions = ['unlock_student_accounts']
    change_form_template = 'admin/student/loginfailures/change_form_template.html'

    @_Check.is_enabled(LoginFailures.is_feature_enabled)
    def has_module_permission(self, request):
        """
        Only enabled if feature is enabled.
        """
        return super().has_module_permission(request)

    @_Check.is_enabled(LoginFailures.is_feature_enabled)
    def has_view_permission(self, request, obj=None):
        """
        Only enabled if feature is enabled.
        """
        return super().has_view_permission(request, obj)

    @_Check.is_enabled(LoginFailures.is_feature_enabled)
    def has_delete_permission(self, request, obj=None):
        """
        Only enabled if feature is enabled.
        """
        return super().has_delete_permission(request, obj)

    @_Check.is_enabled(LoginFailures.is_feature_enabled)
    def has_change_permission(self, request, obj=None):
        """
        Only enabled if feature is enabled.
        """
        return super().has_change_permission(request, obj)

    @_Check.is_enabled(LoginFailures.is_feature_enabled)
    def has_add_permission(self, request):
        """
        Only enabled if feature is enabled.
        """
        return super().has_add_permission(request)

    def unlock_student_accounts(self, request, queryset):
        """
        Unlock student accounts with login failures.
        """
        count = 0
        with transaction.atomic(using=router.db_for_write(self.model)):
            for obj in queryset:
                self.unlock_student(request, obj=obj)
                count += 1
        self.message_user(
            request,
            ngettext(
                '%(count)d student account was unlocked.',
                '%(count)d student accounts were unlocked.',
                count
            ) % {
                'count': count
            }
        )

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """
        Change View.

        This is overridden so we can add a custom button to unlock an account in the record's details.
        """
        if '_unlock' in request.POST:
            with transaction.atomic(using=router.db_for_write(self.model)):
                self.unlock_student(request, object_id=object_id)
                url = reverse('admin:student_loginfailures_changelist', current_app=self.admin_site.name)
                return HttpResponseRedirect(url)
        return super().change_view(request, object_id, form_url, extra_context)

    def get_actions(self, request):
        """
        Get actions for model admin and remove delete action.
        """
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def unlock_student(self, request, object_id=None, obj=None):
        """
        Unlock student account.
        """
        if object_id:
            obj = self.get_object(request, unquote(object_id))

        self.model.clear_lockout_counter(obj.user)


class AllowedAuthUserForm(forms.ModelForm):
    """Model Form for AllowedAuthUser model's admin interface."""

    class Meta:
        model = AllowedAuthUser
        fields = ('site', 'email', )

    def clean_email(self):
        """
        Validate the email field.
        """
        email = self.cleaned_data['email']
        email_domain = email.split('@')[-1]
        allowed_site_email_domain = self.cleaned_data['site'].configuration.get_value('THIRD_PARTY_AUTH_ONLY_DOMAIN')

        if not allowed_site_email_domain:  # pylint: disable=no-else-raise
            raise forms.ValidationError(
                _("Please add a key/value 'THIRD_PARTY_AUTH_ONLY_DOMAIN/{site_email_domain}' in SiteConfiguration "
                  "model's site_values field.")
            )
        elif email_domain != allowed_site_email_domain:
            raise forms.ValidationError(
                _(f"Email doesn't have {allowed_site_email_domain} domain name.")  # pylint: disable=translation-of-non-string
            )
        elif not User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("User with this email doesn't exist in system."))
        else:
            return email


@admin.register(AllowedAuthUser)
class AllowedAuthUserAdmin(admin.ModelAdmin):
    """ Admin interface for the AllowedAuthUser model. """
    form = AllowedAuthUserForm
    list_display = ('email', 'site',)
    search_fields = ('email',)
    ordering = ('-created',)

    class Meta:
        model = AllowedAuthUser


@admin.register(CourseEnrollmentCelebration)
class CourseEnrollmentCelebrationAdmin(DisableEnrollmentAdminMixin, admin.ModelAdmin):
    """Admin interface for the CourseEnrollmentCelebration model. """
    raw_id_fields = ('enrollment',)
    list_display = ('id', 'course', 'user', 'celebrate_first_section', 'celebrate_weekly_goal',)
    search_fields = ('enrollment__course__id', 'enrollment__user__username')

    class Meta:
        model = CourseEnrollmentCelebration

    def course(self, obj):
        return obj.enrollment.course.id
    course.short_description = 'Course'

    def user(self, obj):
        return obj.enrollment.user.username
    user.short_description = 'User'


@admin.register(UserCelebration)
class UserCelebrationAdmin(admin.ModelAdmin):
    """Admin interface for the UserCelebration model."""
    readonly_fields = ('user', )

    class Meta:
        model = UserCelebration

    # Disables the index view to avoid possible performance issues when
    # a large number of user celebrations are present.
    def has_module_permission(self, request):
        return False


@admin.register(PendingNameChange)
class PendingNameChangeAdmin(admin.ModelAdmin):
    """Admin interface for the Pending Name Change model"""
    readonly_fields = ('user', )
    list_display = ('user', 'new_name', 'rationale')
    search_fields = ('user', 'new_name')

    class Meta:
        model = PendingNameChange


admin.site.register(UserTestGroup)
admin.site.register(Registration)
admin.site.register(AccountRecoveryConfiguration, ConfigurationModelAdmin)
admin.site.register(DashboardConfiguration, ConfigurationModelAdmin)
admin.site.register(RegistrationCookieConfiguration, ConfigurationModelAdmin)
admin.site.register(BulkUnenrollConfiguration, ConfigurationModelAdmin)
admin.site.register(BulkChangeEnrollmentConfiguration, ConfigurationModelAdmin)


# We must first un-register the User model since it may also be registered by the auth app.
try:
    admin.site.unregister(User)
except NotRegistered:
    pass

admin.site.register(User, UserAdmin)
