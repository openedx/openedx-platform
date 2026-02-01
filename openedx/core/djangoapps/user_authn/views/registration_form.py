"""
Objects and utilities used to construct registration forms.
"""

import copy
import re
from importlib import import_module
import logging

from django import forms
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ImproperlyConfigured
from django.core.validators import RegexValidator, ValidationError, slug_re
from django.forms import widgets
from django.urls import reverse
from django.utils.translation import gettext as _
from django_countries import countries
from django.db.models import Model
from typing import Optional, Type
from eventtracking import tracker

from common.djangoapps import third_party_auth
from common.djangoapps.edxmako.shortcuts import marketing_link
from common.djangoapps.student.models import CourseEnrollmentAllowed, UserProfile, email_exists_or_retired
from common.djangoapps.util.password_policy_validators import (
    password_validators_instruction_texts,
    password_validators_restrictions,
    validate_password
)
from openedx.core.djangoapps.embargo.models import GlobalRestrictedCountry
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_api.helpers import FormDescription
from openedx.core.djangoapps.user_authn.utils import check_pwned_password
from openedx.core.djangoapps.user_authn.utils import is_registration_api_v1 as is_api_v1
from openedx.core.djangoapps.user_authn.views.utils import remove_disabled_country_from_list
from openedx.core.djangolib.markup import HTML, Text
from openedx.features.enterprise_support.api import enterprise_customer_for_request

logger = logging.getLogger(__name__)


class TrueCheckbox(widgets.CheckboxInput):
    """
    A checkbox widget that only accepts "true" (case-insensitive) as true.
    """

    def value_from_datadict(self, data, files, name):
        value = data.get(name, '')
        return value.lower() == 'true'


class TrueField(forms.BooleanField):
    """
    A boolean field that only accepts "true" (case-insensitive) as true
    """
    widget = TrueCheckbox


def validate_username(username):
    """
    Verifies a username is valid, raises a ValidationError otherwise.
    Args:
        username (unicode): The username to validate.

    This function is configurable with `ENABLE_UNICODE_USERNAME` feature.
    """

    username_re = slug_re
    flags = None
    message = accounts.USERNAME_INVALID_CHARS_ASCII

    if settings.FEATURES.get("ENABLE_UNICODE_USERNAME"):
        username_re = fr"^{settings.USERNAME_REGEX_PARTIAL}$"
        flags = re.UNICODE
        message = accounts.USERNAME_INVALID_CHARS_UNICODE

    validator = RegexValidator(
        regex=username_re,
        flags=flags,
        message=message,
        code='invalid',
    )

    validator(username)


def contains_html(value):
    """
    Validator method to check whether name contains html tags
    """
    regex = re.compile('(<|>)', re.UNICODE)
    return bool(regex.search(value))


def contains_url(value):
    """
    Validator method to check whether full name contains url
    """
    regex = re.findall(r'://', value)
    return bool(regex)


def validate_name(name):
    """
    Verifies a Full_Name is valid, raises a ValidationError otherwise.
    Args:
        name (unicode): The name to validate.
    """
    if contains_html(name):
        raise forms.ValidationError(_('Full Name cannot contain the following characters: < >'))
    if contains_url(name):
        raise forms.ValidationError(_('Enter a valid name'))


class UsernameField(forms.CharField):
    """
    A CharField that validates usernames based on the `ENABLE_UNICODE_USERNAME` feature.
    """

    default_validators = [validate_username]

    def __init__(self, *args, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        super().__init__(
            min_length=accounts.USERNAME_MIN_LENGTH,
            max_length=accounts.USERNAME_MAX_LENGTH,
            error_messages={
                "required": accounts.USERNAME_BAD_LENGTH_MSG,
                "min_length": accounts.USERNAME_BAD_LENGTH_MSG,
                "max_length": accounts.USERNAME_BAD_LENGTH_MSG,
            }
        )

    def clean(self, value):
        """
        Strips the spaces from the username.

        Similar to what `django.forms.SlugField` does.
        """

        value = self.to_python(value).strip()
        return super().clean(value)


class AccountCreationForm(forms.Form):
    """
    A form to for account creation data. It is currently only used for
    validation, not rendering.
    """

    _EMAIL_INVALID_MSG = _("A properly formatted e-mail is required")
    _NAME_TOO_SHORT_MSG = _("Your legal name must be a minimum of one character long")
    _NAME_TOO_LONG_MSG = _("Your legal name is too long. It must not exceed %(max_length)s characters")

    # TODO: Resolve repetition

    username = UsernameField()

    email = forms.EmailField(
        max_length=accounts.EMAIL_MAX_LENGTH,
        min_length=accounts.EMAIL_MIN_LENGTH,
        error_messages={
            "required": _EMAIL_INVALID_MSG,
            "invalid": _EMAIL_INVALID_MSG,
            "max_length": _("Email cannot be more than %(limit_value)s characters long"),
        }
    )

    password = forms.CharField()

    name = forms.CharField(
        min_length=accounts.NAME_MIN_LENGTH,
        max_length=accounts.NAME_MAX_LENGTH,
        error_messages={
            "required": _NAME_TOO_SHORT_MSG,
            "min_length": _NAME_TOO_SHORT_MSG,
            "max_length": _NAME_TOO_LONG_MSG % {"max_length": accounts.NAME_MAX_LENGTH},
        },
        validators=[validate_name]
    )

    def __init__(
        self,
        data=None,
        extra_fields=None,
        extended_profile_fields=None,
        do_third_party_auth=True,
        tos_required=True
    ):
        super().__init__(data)

        extra_fields = extra_fields or {}
        self.extended_profile_fields = extended_profile_fields or {}
        self.do_third_party_auth = do_third_party_auth
        if tos_required:
            self.fields["terms_of_service"] = TrueField(
                error_messages={"required": _("You must accept the terms of service.")}
            )

        error_message_dict = {
            "level_of_education": _("A level of education is required"),
            "gender": _("Your gender is required"),
            "year_of_birth": _("Your year of birth is required"),
            "mailing_address": _("Your mailing address is required"),
            "goals": _("A description of your goals is required"),
            "city": _("A city is required"),
            "country": _("A country is required")
        }
        for field_name, field_value in extra_fields.items():
            if field_name not in self.fields:
                if field_name == "honor_code":
                    if field_value == "required":
                        self.fields[field_name] = TrueField(
                            error_messages={
                                "required": _("To enroll, you must follow the honor code.")
                            }
                        )
                else:
                    required = field_value == "required"
                    min_length = 1
                    error_message = error_message_dict.get(
                        field_name,
                        _("You are missing one or more required fields")
                    )
                    self.fields[field_name] = forms.CharField(
                        required=required,
                        min_length=min_length,
                        error_messages={
                            "required": error_message,
                            "min_length": error_message,
                        }
                    )

        for field in self.extended_profile_fields:
            if field not in self.fields:
                self.fields[field] = forms.CharField(required=False)

    def clean_password(self):
        """Enforce password policies (if applicable)"""
        password = self.cleaned_data["password"]
        if not self.do_third_party_auth:
            # Creating a temporary user object to test password against username
            # This user should NOT be saved
            username = self.cleaned_data.get('username')
            email = self.cleaned_data.get('email')
            temp_user = User(username=username, email=email) if username else None
            validate_password(password, temp_user)

            if settings.ENABLE_AUTHN_REGISTER_HIBP_POLICY:
                # Checks the Pwned Databases for password vulnerability.
                pwned_properties = check_pwned_password(password)

                if (
                    pwned_properties.get('vulnerability', 'no') == 'yes' and
                    pwned_properties.get('frequency', 0) >= settings.HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD
                ):
                    pwned_properties['user_request_page'] = 'registration'
                    tracker.emit('edx.bi.user.pwned.password.status', pwned_properties)
                    raise ValidationError(accounts.AUTHN_PASSWORD_COMPROMISED_MSG)
        return password

    def clean_email(self):
        """ Enforce email restrictions (if applicable) """
        email = self.cleaned_data["email"]
        if settings.REGISTRATION_EMAIL_PATTERNS_ALLOWED is not None:
            # This Open edX instance has restrictions on what email addresses are allowed.
            allowed_patterns = settings.REGISTRATION_EMAIL_PATTERNS_ALLOWED
            # We append a '$' to the regexs to prevent the common mistake of using a
            # pattern like '.*@edx\\.org' which would match 'bob@edx.org.badguy.com'
            if not any(re.match(pattern + "$", email) for pattern in allowed_patterns):
                # This email is not on the whitelist of allowed emails. Check if
                # they may have been manually invited by an instructor and if not,
                # reject the registration.
                if not CourseEnrollmentAllowed.objects.filter(email=email).exists():
                    raise ValidationError(_("Unauthorized email address."))
        if email_exists_or_retired(email):
            raise ValidationError(
                _(
                    "It looks like {email} belongs to an existing account. Try again with a different email address."
                ).format(email=email)
            )
        return email

    def clean_year_of_birth(self):
        """
        Parse year_of_birth to an integer, but just use None instead of raising
        an error if it is malformed
        """
        try:
            year_str = self.cleaned_data["year_of_birth"]
            return int(year_str) if year_str is not None else None
        except ValueError:
            return None

    @property
    def cleaned_extended_profile(self):
        """
        Return a dictionary containing the extended_profile_fields and values
        """
        return {
            key: value
            for key, value in self.cleaned_data.items()
            if key in self.extended_profile_fields and value is not None
        }

    def clean_country(self):
        """
        Check if the user's country is in the embargoed countries list.
        """
        country = self.cleaned_data.get("country")
        if (
            settings.FEATURES.get('EMBARGO', False) and
            country in GlobalRestrictedCountry.get_countries()
        ):
            raise ValidationError(_("Registration from this country is not allowed due to restrictions."))
        return self.cleaned_data.get("country")


def get_registration_extension_form(*args, **kwargs):
    """
    Convenience function for getting the custom form set in settings.REGISTRATION_EXTENSION_FORM.

    An example form app for this can be found at http://github.com/open-craft/custom-form-app
    """
    if not getattr(settings, 'REGISTRATION_EXTENSION_FORM', None):
        return None
    module, klass = settings.REGISTRATION_EXTENSION_FORM.rsplit('.', 1)
    module = import_module(module)
    return getattr(module, klass)(*args, **kwargs)


def get_extended_profile_model() -> Optional[Type[Model]]:
    """
    Get the model class for the extended profile form.

    Returns the Django model class associated with the form specified in
    the `REGISTRATION_EXTENSION_FORM` setting.

    Returns:
        Optional[Type[Model]]: The model class if found and valid, None otherwise.

    Example:
        # In settings.py: REGISTRATION_EXTENSION_FORM = 'myapp.forms.ExtendedForm'
        model_class = get_extended_profile_model()
    """
    setting_value = getattr(settings, "REGISTRATION_EXTENSION_FORM", None)
    if not setting_value:
        return None

    try:
        module_path, klass_name = setting_value.rsplit(".", 1)
        module = import_module(module_path)
        form_class = getattr(module, klass_name)
        return getattr(form_class.Meta, "model", None)
    except (ValueError, ImportError, ModuleNotFoundError, AttributeError) as e:
        logger.warning("Could not load extended profile model from '%s': %s", setting_value, e)
        return None


class RegistrationFormFactory:
    """
    Construct Registration forms and associated fields.
    """

    DEFAULT_FIELDS = ["email", "name", "username", "password"]

    def _is_field_visible(self, field_name):
        """Check whether a field is visible based on Django settings. """
        return self._extra_fields_setting.get(field_name) in ["required", "optional", "optional-exposed"]

    def _is_field_required(self, field_name):
        """Check whether a field is required based on Django settings. """
        return self._extra_fields_setting.get(field_name) == "required"

    def _is_field_exposed(self, field_name):
        """Check whether a field is optional and should be toggled. """
        return self._extra_fields_setting.get(field_name) in ["required", "optional-exposed"]

    def __init__(self):

        self.EXTRA_FIELDS = [
            "confirm_email",
            "first_name",
            "last_name",
            "city",
            "state",
            "country",
            "gender",
            "year_of_birth",
            "level_of_education",
            "company",
            "job_title",
            "title",
            "mailing_address",
            "goals",
            "honor_code",
            "terms_of_service",
            "profession",
            "specialty",
            "marketing_emails_opt_in",
        ]

        if settings.ENABLE_COPPA_COMPLIANCE and 'year_of_birth' in self.EXTRA_FIELDS:
            self.EXTRA_FIELDS.remove('year_of_birth')

        # Backwards compatibility: Honor code is required by default, unless
        # explicitly set to "optional" in Django settings.
        self._extra_fields_setting = copy.deepcopy(configuration_helpers.get_value('REGISTRATION_EXTRA_FIELDS'))
        if not self._extra_fields_setting:
            self._extra_fields_setting = copy.deepcopy(settings.REGISTRATION_EXTRA_FIELDS)
        self._extra_fields_setting["honor_code"] = self._extra_fields_setting.get("honor_code", "required")

        if settings.MARKETING_EMAILS_OPT_IN:
            self._extra_fields_setting['marketing_emails_opt_in'] = 'optional'

        # Check that the setting is configured correctly
        for field_name in self.EXTRA_FIELDS:
            if self._extra_fields_setting.get(field_name, "hidden") not in ["required", "optional", "hidden"]:
                msg = "Setting REGISTRATION_EXTRA_FIELDS values must be either required, optional, or hidden."
                raise ImproperlyConfigured(msg)

        # Map field names to the instance method used to add the field to the form
        self.field_handlers = {}
        valid_fields = self.DEFAULT_FIELDS + self.EXTRA_FIELDS
        for field_name in valid_fields:
            handler = getattr(self, f"_add_{field_name}_field")
            self.field_handlers[field_name] = handler

        custom_form = get_registration_extension_form()
        if custom_form:
            custom_form_field_names = [field_name for field_name, field in custom_form.fields.items()]
            valid_fields.extend(custom_form_field_names)

        field_order = configuration_helpers.get_value('REGISTRATION_FIELD_ORDER')
        if not field_order:
            field_order = settings.REGISTRATION_FIELD_ORDER or valid_fields
        # Check that all of the valid_fields are in the field order and vice versa,
        # if not append missing fields at end of field order
        if set(valid_fields) != set(field_order):
            difference = set(valid_fields).difference(set(field_order))
            # sort the additional fields so we have could have a deterministic result when presenting them
            field_order.extend(sorted(difference))

        self.field_order = field_order

    def get_registration_form(self, request):
        """Return a description of the registration form.
        This decouples clients from the API definition:
        if the API decides to modify the form, clients won't need
        to be updated.
        This is especially important for the registration form,
        since different edx-platform installations might
        collect different demographic information.
        See `user_api.helpers.FormDescription` for examples
        of the JSON-encoded form description.
        Arguments:
            request (HttpRequest)
        Returns:
            HttpResponse
        """
        form_desc = FormDescription("post", self._get_registration_submit_url(request))
        self._apply_third_party_auth_overrides(request, form_desc)

        # Custom form fields can be added via the form set in settings.REGISTRATION_EXTENSION_FORM
        custom_form = get_registration_extension_form()
        if custom_form:
            custom_form_field_names = [field_name for field_name, field in custom_form.fields.items()]
        else:
            custom_form_field_names = []

        # Go through the fields in the fields order and add them if they are required or visible
        for field_name in self.field_order:
            if field_name in self.DEFAULT_FIELDS:
                self.field_handlers[field_name](form_desc, required=True)
            elif self._is_field_visible(field_name) and self.field_handlers.get(field_name):
                self.field_handlers[field_name](
                    form_desc,
                    required=self._is_field_required(field_name)
                )
            elif field_name in custom_form_field_names:
                for custom_field_name, field in custom_form.fields.items():
                    if field_name == custom_field_name:
                        restrictions = {}
                        if getattr(field, 'max_length', None):
                            restrictions['max_length'] = field.max_length
                        if getattr(field, 'min_length', None):
                            restrictions['min_length'] = field.min_length
                        field_options = getattr(
                            getattr(custom_form, 'Meta', None), 'serialization_options', {}
                        ).get(field_name, {})
                        field_type = field_options.get(
                            'field_type',
                            FormDescription.FIELD_TYPE_MAP.get(field.__class__))
                        if not field_type:
                            raise ImproperlyConfigured(
                                "Field type '{}' not recognized for registration extension field '{}'.".format(
                                    field_type,
                                    field_name
                                )
                            )
                        if self._is_field_visible(field_name) or field.required:
                            form_desc.add_field(
                                field_name,
                                label=field.label,
                                default=field_options.get('default'),
                                field_type=field_options.get(
                                    'field_type',
                                    FormDescription.FIELD_TYPE_MAP.get(field.__class__)),
                                placeholder=field.initial,
                                instructions=field.help_text,
                                exposed=self._is_field_exposed(field_name),
                                required=(self._is_field_required(field_name) or field.required),
                                restrictions=restrictions,
                                options=getattr(field, 'choices', None), error_messages=field.error_messages,
                                include_default_option=field_options.get('include_default_option'),
                            )

        # remove confirm_email form v1 registration form
        if is_api_v1(request):
            for index, field in enumerate(form_desc.fields):
                if field['name'] == 'confirm_email':
                    del form_desc.fields[index]
                    break
        return form_desc

    def _get_registration_submit_url(self, request):
        return reverse("user_api_registration") if is_api_v1(request) else reverse("user_api_registration_v2")

    def _add_email_field(self, form_desc, required=True):
        """Add an email field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # meant to hold the user's email address.
        email_label = _("Email")

        # Translators: These instructions appear on the registration form, immediately
        # below a field meant to hold the user's email address.
        email_instructions = _("This is what you will use to login.")

        form_desc.add_field(
            "email",
            field_type="email",
            label=email_label,
            instructions=email_instructions,
            restrictions={
                "min_length": accounts.EMAIL_MIN_LENGTH,
                "max_length": accounts.EMAIL_MAX_LENGTH,
            },
            required=required
        )

    def _add_confirm_email_field(self, form_desc, required=True):
        """Add an email confirmation field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # meant to confirm the user's email address.
        email_label = _("Confirm Email")

        error_msg = accounts.REQUIRED_FIELD_CONFIRM_EMAIL_MSG

        form_desc.add_field(
            "confirm_email",
            field_type="email",
            label=email_label,
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_name_field(self, form_desc, required=True):
        """Add a name field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # meant to hold the user's full name.
        name_label = _("Full Name")

        # Translators: These instructions appear on the registration form, immediately
        # below a field meant to hold the user's full name.
        name_instructions = _("This name will be used on any certificates that you earn.")

        form_desc.add_field(
            "name",
            label=name_label,
            instructions=name_instructions,
            restrictions={
                "max_length": accounts.NAME_MAX_LENGTH,
            },
            required=required
        )

    def _add_username_field(self, form_desc, required=True):
        """Add a username field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # meant to hold the user's public username.
        username_label = _("Public Username")

        username_instructions = _(
            # Translators: These instructions appear on the registration form, immediately
            # below a field meant to hold the user's public username.
            "The name that will identify you in your courses. "
            "It cannot be changed later."
        )
        form_desc.add_field(
            "username",
            label=username_label,
            instructions=username_instructions,
            restrictions={
                "min_length": accounts.USERNAME_MIN_LENGTH,
                "max_length": accounts.USERNAME_MAX_LENGTH,
            },
            required=required
        )

    def _add_password_field(self, form_desc, required=True):
        """Add a password field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # meant to hold the user's password.
        password_label = _("Password")

        form_desc.add_field(
            "password",
            label=password_label,
            field_type="password",
            instructions=password_validators_instruction_texts(),
            restrictions=password_validators_restrictions(),
            required=required
        )

    def _add_level_of_education_field(self, form_desc, required=True):
        """Add a level of education field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a dropdown menu on the registration
        # form used to select the user's highest completed level of education.
        education_level_label = _("Highest level of education completed")
        error_msg = accounts.REQUIRED_FIELD_LEVEL_OF_EDUCATION_MSG

        # The labels are marked for translation in UserProfile model definition.
        # pylint: disable=translation-of-non-string

        options = [(name, _(label)) for name, label in UserProfile.LEVEL_OF_EDUCATION_CHOICES]
        if settings.ENABLE_COPPA_COMPLIANCE:
            options = filter(lambda op: op[0] != 'el', options)
        form_desc.add_field(
            "level_of_education",
            label=education_level_label,
            field_type="select",
            options=options,
            include_default_option=True,
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_gender_field(self, form_desc, required=True):
        """Add a gender field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a dropdown menu on the registration
        # form used to select the user's gender.
        gender_label = _("Gender")

        # The labels are marked for translation in UserProfile model definition.
        # pylint: disable=translation-of-non-string
        options = [(name, _(label)) for name, label in UserProfile.GENDER_CHOICES]
        form_desc.add_field(
            "gender",
            label=gender_label,
            field_type="select",
            options=options,
            include_default_option=True,
            required=required
        )

    def _add_year_of_birth_field(self, form_desc, required=True):
        """Add a year of birth field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a dropdown menu on the registration
        # form used to select the user's year of birth.
        yob_label = _("Year of birth")

        options = [(str(year), str(year)) for year in UserProfile.VALID_YEARS]
        form_desc.add_field(
            "year_of_birth",
            label=yob_label,
            field_type="select",
            options=options,
            include_default_option=True,
            required=required
        )

    def _add_marketing_emails_opt_in_field(self, form_desc, required=False):
        """Add a marketing email checkbox to form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        opt_in_label = _(
            'I agree that {platform_name} may send me marketing messages.').format(
            platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        )

        form_desc.add_field(
            'marketing_emails_opt_in',
            label=opt_in_label,
            field_type="checkbox",
            exposed=True,
            default=True,  # the checkbox will automatically be checked; meaning user has opted in
            required=required,
        )

    def _add_field_with_configurable_select_options(self, field_name, field_label, form_desc, required=False):
        """Add a field to a form description.
            If select options are given for this field, it will be a select type
            otherwise it will be a text type.

        Arguments:
            field_name: name of field
            field_label: label for the field
            form_desc: A form description

        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False

        """

        extra_field_options = configuration_helpers.get_value('EXTRA_FIELD_OPTIONS')
        if extra_field_options is None or extra_field_options.get(field_name) is None:
            field_type = "text"
            include_default_option = False
            options = None
            error_msg = ''
            error_msg = getattr(accounts, f'REQUIRED_FIELD_{field_name.upper()}_TEXT_MSG')
        else:
            field_type = "select"
            include_default_option = True
            field_options = extra_field_options.get(field_name)
            options = [(str(option.lower()), option) for option in field_options]
            error_msg = ''
            error_msg = getattr(accounts, f'REQUIRED_FIELD_{field_name.upper()}_SELECT_MSG')

        form_desc.add_field(
            field_name,
            label=field_label,
            field_type=field_type,
            options=options,
            include_default_option=include_default_option,
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_profession_field(self, form_desc, required=False):
        """Add a profession field to a form description.

        Arguments:
            form_desc: A form description

        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False

        """
        # Translators: This label appears above a dropdown menu on the registration
        # form used to select the user's profession
        profession_label = _("Profession")

        self._add_field_with_configurable_select_options('profession', profession_label, form_desc, required=required)

    def _add_specialty_field(self, form_desc, required=False):
        """Add a specialty field to a form description.

        Arguments:
            form_desc: A form description

        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False

        """
        # Translators: This label appears above a dropdown menu on the registration
        # form used to select the user's specialty
        specialty_label = _("Specialty")

        self._add_field_with_configurable_select_options('specialty', specialty_label, form_desc, required=required)

    def _add_mailing_address_field(self, form_desc, required=True):
        """Add a mailing address field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # meant to hold the user's mailing address.
        mailing_address_label = _("Mailing address")
        error_msg = accounts.REQUIRED_FIELD_MAILING_ADDRESS_MSG

        form_desc.add_field(
            "mailing_address",
            label=mailing_address_label,
            field_type="textarea",
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_goals_field(self, form_desc, required=True):
        """Add a goals field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This phrase appears above a field on the registration form
        # meant to hold the user's reasons for registering with edX.
        goals_label = _("Tell us why you're interested in {platform_name}").format(
            platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME)
        )
        error_msg = accounts.REQUIRED_FIELD_GOALS_MSG

        form_desc.add_field(
            "goals",
            label=goals_label,
            field_type="textarea",
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_city_field(self, form_desc, required=True):
        """Add a city field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the city in which they live.
        city_label = _("City")
        error_msg = accounts.REQUIRED_FIELD_CITY_MSG

        form_desc.add_field(
            "city",
            label=city_label,
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_state_field(self, form_desc, required=False):
        """Add a State/Province/Region field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the State/Province/Region in which they live.
        state_label = _("State/Province/Region")

        form_desc.add_field(
            "state",
            label=state_label,
            required=required
        )

    def _add_company_field(self, form_desc, required=False):
        """Add a Company field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the Company
        company_label = _("Company")

        form_desc.add_field(
            "company",
            label=company_label,
            required=required
        )

    def _add_title_field(self, form_desc, required=False):
        """Add a Title field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the Title
        title_label = _("Title")

        form_desc.add_field(
            "title",
            label=title_label,
            required=required
        )

    def _add_job_title_field(self, form_desc, required=False):
        """Add a Job Title field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the Job Title
        job_title_label = _("Job Title")

        form_desc.add_field(
            "job_title",
            label=job_title_label,
            required=required
        )

    def _add_first_name_field(self, form_desc, required=False):
        """Add a First Name field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the First Name
        first_name_label = _("First Name")

        form_desc.add_field(
            "first_name",
            label=first_name_label,
            required=required
        )

    def _add_last_name_field(self, form_desc, required=False):
        """Add a Last Name field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to False
        """
        # Translators: This label appears above a field on the registration form
        # which allows the user to input the First Name
        last_name_label = _("Last Name")

        form_desc.add_field(
            "last_name",
            label=last_name_label,
            required=required
        )

    def _add_country_field(self, form_desc, required=True):
        """Add a country field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This label appears above a dropdown menu on the registration
        # form used to select the country in which the user lives.
        country_label = _("Country or Region of Residence")

        error_msg = accounts.REQUIRED_FIELD_COUNTRY_MSG

        # If we set a country code, make sure it's uppercase for the sake of the form.
        # pylint: disable=protected-access
        default_country = form_desc._field_overrides.get('country', {}).get('defaultValue')

        country_instructions = _(
            # Translators: These instructions appear on the registration form, immediately
            # below a field meant to hold the user's country.
            "The country or region where you live."
        )
        if default_country:
            form_desc.override_field_properties(
                'country',
                default=default_country.upper()
            )
        form_desc.add_field(
            "country",
            label=country_label,
            instructions=country_instructions,
            field_type="select",
            options=list(remove_disabled_country_from_list(dict(countries)).items()),
            include_default_option=True,
            required=required,
            error_messages={
                "required": error_msg
            }
        )

    def _add_honor_code_field(self, form_desc, required=True):
        """Add an honor code field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """

        separate_honor_and_tos = self._is_field_visible("terms_of_service")
        # Separate terms of service and honor code checkboxes
        if separate_honor_and_tos:
            terms_label = _("Honor Code")
            terms_link = marketing_link("HONOR")

        # Combine terms of service and honor code checkboxes
        else:
            # Translators: This is a legal document users must agree to
            # in order to register a new account.
            terms_label = _("Terms of Service and Honor Code")
            terms_link = marketing_link("HONOR")

        # Translators: "Terms of Service" is a legal document users must agree to
        # in order to register a new account.
        label = Text(_(
            "I agree to the {platform_name} {terms_of_service_link_start}{terms_of_service}{terms_of_service_link_end}"
        )).format(
            platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME),
            terms_of_service=terms_label,
            terms_of_service_link_start=HTML("<a href='{terms_link}' rel='noopener' target='_blank'>").format(
                terms_link=terms_link
            ),
            terms_of_service_link_end=HTML("</a>"),
        )

        # Translators: "Terms of Service" is a legal document users must agree to
        # in order to register a new account.
        error_msg = _("You must agree to the {platform_name} {terms_of_service}").format(
            platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME),
            terms_of_service=terms_label
        )
        field_type = 'checkbox'

        if not separate_honor_and_tos:
            field_type = 'plaintext'

            pp_link = marketing_link("PRIVACY")
            label = Text(_(
                "By creating an account, you agree to the \
                  {terms_of_service_link_start}{terms_of_service}{terms_of_service_link_end} \
                  and you acknowledge that {platform_name} and each Member process your personal data in accordance \
                  with the {privacy_policy_link_start}Privacy Policy{privacy_policy_link_end}."
            )).format(
                platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME),
                terms_of_service=terms_label,
                terms_of_service_link_start=HTML("<a href='{terms_url}' rel='noopener' target='_blank'>").format(
                    terms_url=terms_link
                ),
                terms_of_service_link_end=HTML("</a>"),
                privacy_policy_link_start=HTML("<a href='{pp_url}' rel='noopener' target='_blank'>").format(
                    pp_url=pp_link
                ),
                privacy_policy_link_end=HTML("</a>"),
            )

        form_desc.add_field(
            "honor_code",
            label=label,
            field_type=field_type,
            default=False,
            required=required,
            error_messages={
                "required": error_msg
            },
        )

    def _add_terms_of_service_field(self, form_desc, required=True):
        """Add a terms of service field to a form description.
        Arguments:
            form_desc: A form description
        Keyword Arguments:
            required (bool): Whether this field is required; defaults to True
        """
        # Translators: This is a legal document users must agree to
        # in order to register a new account.
        terms_label = _("Terms of Service")
        terms_link = marketing_link("TOS")

        # Translators: "Terms of service" is a legal document users must agree to
        # in order to register a new account.
        label = Text(_("I agree to the {platform_name} {tos_link_start}{terms_of_service}{tos_link_end}")).format(
            platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME),
            terms_of_service=terms_label,
            tos_link_start=HTML("<a href='{terms_link}' rel='noopener' target='_blank'>").format(
                terms_link=terms_link
            ),
            tos_link_end=HTML("</a>"),
        )

        # Translators: "Terms of service" is a legal document users must agree to
        # in order to register a new account.
        error_msg = _("You must agree to the {platform_name} {terms_of_service}").format(
            platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME),
            terms_of_service=terms_label
        )

        form_desc.add_field(
            "terms_of_service",
            label=label,
            field_type="checkbox",
            default=False,
            required=required,
            error_messages={
                "required": error_msg
            },
        )

    def _apply_third_party_auth_overrides(self, request, form_desc):
        """Modify the registration form if the user has authenticated with a third-party provider.
        If a user has successfully authenticated with a third-party provider,
        but does not yet have an account with EdX, we want to fill in
        the registration form with any info that we get from the
        provider.
        This will also hide the password field, since we assign users a default
        (random) password on the assumption that they will be using
        third-party auth to log in.
        Arguments:
            request (HttpRequest): The request for the registration form, used
                to determine if the user has successfully authenticated
                with a third-party provider.
            form_desc (FormDescription): The registration form description
        """
        # pylint: disable=too-many-nested-blocks
        if third_party_auth.is_enabled():
            running_pipeline = third_party_auth.pipeline.get(request)
            if running_pipeline:
                current_provider = third_party_auth.provider.Registry.get_from_pipeline(running_pipeline)

                if current_provider:
                    # Override username / email / full name
                    field_overrides = current_provider.get_register_form_data(
                        running_pipeline.get('kwargs')
                    )

                    # When the TPA Provider is configured to skip the registration form and we are in an
                    # enterprise context, we need to hide all fields except for terms of service and
                    # ensure that the user explicitly checks that field.
                    # pylint: disable=consider-using-ternary
                    hide_registration_fields_except_tos = (
                        (
                            current_provider.skip_registration_form and enterprise_customer_for_request(request)
                        ) or current_provider.sync_learner_profile_data
                    )

                    for field_name in self.DEFAULT_FIELDS + self.EXTRA_FIELDS:
                        if field_name in field_overrides:
                            form_desc.override_field_properties(
                                field_name, default=field_overrides[field_name]
                            )

                            if (
                                field_name not in ['terms_of_service', 'honor_code'] and
                                field_overrides[field_name] and
                                hide_registration_fields_except_tos
                            ):
                                form_desc.override_field_properties(
                                    field_name,
                                    field_type="hidden",
                                    label="",
                                    instructions="",
                                )

                    # Hide the confirm_email field
                    form_desc.override_field_properties(
                        "confirm_email",
                        default="",
                        field_type="hidden",
                        required=False,
                        label="",
                        instructions="",
                        restrictions={}
                    )

                    # Hide the password field
                    form_desc.override_field_properties(
                        "password",
                        default="",
                        field_type="hidden",
                        required=False,
                        label="",
                        instructions="",
                        restrictions={}
                    )
                    # used to identify that request is running third party social auth
                    form_desc.add_field(
                        "social_auth_provider",
                        field_type="hidden",
                        label="",
                        default=current_provider.name if current_provider.name else "Third Party",
                        required=False,
                    )
