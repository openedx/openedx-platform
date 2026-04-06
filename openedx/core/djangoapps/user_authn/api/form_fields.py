"""
Field Descriptions
"""
import logging

from django import forms
from django.conf import settings
from django.utils.translation import gettext as _

from common.djangoapps.student.models import UserProfile
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api import accounts
from openedx.core.djangoapps.user_authn.api.constants import SUPPORTED_FIELDS_TYPES

log = logging.getLogger(__name__)

FIELD_TYPE_MAP = {
    forms.CharField: "text",
    forms.PasswordInput: "password",
    forms.ChoiceField: "select",
    forms.TypedChoiceField: "select",
    forms.Textarea: "textarea",
    forms.BooleanField: "checkbox",
    forms.EmailField: "email",
}


def add_extension_form_field(field_name, custom_form, field_description, field_type):
    """
    Returns Extension form field values
    """
    restrictions = {}
    if field_type == 'required':
        if getattr(field_description, 'max_length', None):
            restrictions['max_length'] = field_description.max_length
        if getattr(field_description, 'min_length', None):
            restrictions['min_length'] = field_description.min_length

    field_options = getattr(
        getattr(custom_form, 'Meta', None), 'serialization_options', {}
    ).get(field_name, {})
    custom_field_type = field_options.get('field_type', FIELD_TYPE_MAP.get(field_description.__class__))

    if not custom_field_type:
        log.info(
            f'Field type {custom_field_type} not recognized for registration extension field {field_name}.'
        )

    return {
        'name': field_name,
        'label': field_description.label,
        'default': field_options.get('default'),
        'placeholder': field_description.initial,
        'instructions': field_description.help_text,
        'options': getattr(field_description, 'choices', None),
        'error_message': field_description.error_messages if field_type == 'required' else '',
        'restrictions': restrictions,
        'type': custom_field_type
    }


def _add_field_with_configurable_select_options(field_name, field_label, is_field_required=False, error_message=''):
    """
    Returns a field description
        If select options are given for this field in EXTRA_FIELD_OPTIONS, it
        will be a select type otherwise it will be a text type.
    """
    field_attributes = {
        'name': field_name,
        'label': field_label,
        'error_message': error_message if is_field_required else '',
    }
    extra_field_options = configuration_helpers.get_value('EXTRA_FIELD_OPTIONS')
    if extra_field_options is None or extra_field_options.get(field_name) is None:
        field_attributes.update({
            'type': SUPPORTED_FIELDS_TYPES['TEXT'],
        })
    else:
        field_options = extra_field_options.get(field_name)
        options = [(str(option.lower()), option) for option in field_options]
        field_attributes.update({
            'type': SUPPORTED_FIELDS_TYPES['SELECT'],
            'options': options
        })

    return field_attributes


def add_level_of_education_field(is_field_required=False):
    """
    Returns the level of education field description
    """
    # Translators: This label appears above a dropdown menu used to select
    # the user's highest completed level of education.
    education_level_label = _("Highest level of education completed")

    # pylint: disable=translation-of-non-string
    options = [(name, _(label)) for name, label in UserProfile.LEVEL_OF_EDUCATION_CHOICES]

    if settings.ENABLE_COPPA_COMPLIANCE:
        options = list(filter(lambda op: op[0] != 'el', options))

    return {
        'name': 'level_of_education',
        'type': SUPPORTED_FIELDS_TYPES['SELECT'],
        'label': education_level_label,
        'error_message': accounts.REQUIRED_FIELD_LEVEL_OF_EDUCATION_MSG if is_field_required else '',
        'options': options,
    }


def add_gender_field(is_field_required=False):
    """
    Returns the gender field description
    """
    # Translators: This label appears above a dropdown menu used to select
    # the user's gender.
    gender_label = _("Gender")

    # pylint: disable=translation-of-non-string
    options = [(name, _(label)) for name, label in UserProfile.GENDER_CHOICES]
    return {
        'name': 'gender',
        'type': SUPPORTED_FIELDS_TYPES['SELECT'],
        'label': gender_label,
        'error_message': accounts.REQUIRED_FIELD_GENDER_MSG if is_field_required else '',
        'options': options,
    }


def add_year_of_birth_field(is_field_required=False):
    """
    Returns the year of birth field description
    """
    # Translators: This label appears above a dropdown menu on the form
    # used to select the user's year of birth.
    year_of_birth_label = _("Year of birth")

    options = [(str(year), str(year)) for year in UserProfile.VALID_YEARS]
    return {
        'name': 'year_of_birth',
        'type': SUPPORTED_FIELDS_TYPES['SELECT'],
        'label': year_of_birth_label,
        'error_message': accounts.REQUIRED_FIELD_YEAR_OF_BIRTH_MSG if is_field_required else '',
        'options': options,
    }


def add_goals_field(is_field_required=False):
    """
    Returns the goals field description
    """
    # Translators: This phrase appears above a field meant to hold
    # the user's reasons for registering with edX.
    goals_label = _("Tell us why you're interested in {platform_name}").format(
        platform_name=configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME)
    )

    return {
        'name': 'goals',
        'type': SUPPORTED_FIELDS_TYPES['TEXTAREA'],
        'label': goals_label,
        'error_message': accounts.REQUIRED_FIELD_GOALS_MSG if is_field_required else '',
    }


def add_profession_field(is_field_required=False):
    """
    Returns the profession field description
    """
    # Translators: This label appears above a dropdown menu to select
    # the user's profession
    profession_label = _("Profession")
    return _add_field_with_configurable_select_options(
        'profession', profession_label, is_field_required, accounts.REQUIRED_FIELD_PROFESSION_TEXT_MSG,
    )


def add_specialty_field(is_field_required=False):
    """
    Returns the user specialty field description
    """
    # Translators: This label appears above a dropdown menu to select
    # the user's specialty
    specialty_label = _("Specialty")
    return _add_field_with_configurable_select_options(
        'specialty', specialty_label, is_field_required, accounts.REQUIRED_FIELD_SPECIALTY_SELECT_MSG,
    )


def add_company_field(is_field_required=False):
    """
    Returns the company field descriptions
    """
    # Translators: This label appears above a field which allows the
    # user to input the Company
    company_label = _("Company")
    return _add_field_with_configurable_select_options('company', company_label, is_field_required)


def add_title_field(is_field_required=False):
    """
    Returns the title field description
    """
    # Translators: This label appears above a field which allows the
    # user to input the Title
    title_label = _("Title")
    return _add_field_with_configurable_select_options('title', title_label, is_field_required)


def add_job_title_field(is_field_required=False):
    """
    Returns the title field description
    """
    # Translators: This label appears above a field which allows the
    # user to input the Job Title
    job_title_label = _("Job Title")
    return _add_field_with_configurable_select_options('job_title', job_title_label, is_field_required)


def add_first_name_field(is_field_required=False):
    """
    Returns the first name field description
    """
    # Translators: This label appears above a field which allows the
    # user to input the First Name
    first_name_label = _("First Name")

    return {
        'name': 'first_name',
        'type': SUPPORTED_FIELDS_TYPES['TEXT'],
        'label': first_name_label,
        'error_message': accounts.REQUIRED_FIELD_FIRST_NAME_MSG if is_field_required else '',
    }


def add_last_name_field(is_field_required=False):
    """
    Returns the last name field description
    """
    # Translators: This label appears above a field which allows the
    # user to input the Last Name
    last_name_label = _("Last Name")

    return {
        'name': 'last_name',
        'type': SUPPORTED_FIELDS_TYPES['TEXT'],
        'label': last_name_label,
        'error_message': accounts.REQUIRED_FIELD_LAST_NAME_MSG if is_field_required else '',
    }


def add_mailing_address_field(is_field_required=False):
    """
    Returns the mailing address field description
    """
    # Translators: This label appears above a field
    # meant to hold the user's mailing address.
    mailing_address_label = _("Mailing address")

    return {
        'name': 'mailing_address',
        'type': SUPPORTED_FIELDS_TYPES['TEXTAREA'],
        'label': mailing_address_label,
        'error_message': accounts.REQUIRED_FIELD_MAILING_ADDRESS_MSG if is_field_required else '',
    }


def add_state_field(is_field_required=False):
    """
    Returns a State/Province/Region field description
    """
    # Translators: This label appears above a field
    # which allows the user to input the State/Province/Region in which they live.
    state_label = _("State/Province/Region")

    return {
        'name': 'state',
        'type': SUPPORTED_FIELDS_TYPES['TEXT'],
        'label': state_label,
        'error_message': accounts.REQUIRED_FIELD_STATE_MSG if is_field_required else '',
    }


def add_city_field(is_field_required=False):
    """
    Returns a city field description
    """
    # Translators: This label appears above a field
    # which allows the user to input the city in which they live.
    city_label = _("City")

    return {
        'name': 'city',
        'type': SUPPORTED_FIELDS_TYPES['TEXT'],
        'label': city_label,
        'error_message': accounts.REQUIRED_FIELD_CITY_MSG if is_field_required else '',
    }


def add_honor_code_field(is_field_required=False):
    """
    Returns a honor code field description and this field will be displayed
    directly on AuthnMFE
    """
    fields_setting = configuration_helpers.get_value('REGISTRATION_EXTRA_FIELDS')
    if not fields_setting:
        fields_setting = settings.REGISTRATION_EXTRA_FIELDS
    separate_honor_and_tos = False
    terms_of_service = fields_setting.get('terms_of_service')
    if terms_of_service in ['required', 'optional', 'optional-exposed']:
        separate_honor_and_tos = True

    terms_type = "honor_code" if separate_honor_and_tos else "tos_and_honor_code"
    terms_label = "Honor Code" if separate_honor_and_tos else "Terms of Service and Honor Code"
    platform_name = configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME)

    # Translators: "Terms of Service" is a legal document users must agree to
    # in order to register a new account.
    error_msg = f'You must agree to the {platform_name} {terms_label}' if separate_honor_and_tos else ''
    return {
        'name': 'honor_code',
        'type': terms_type,
        'error_message': error_msg if is_field_required else '',
    }


def add_terms_of_service_field(is_field_required=False):
    """
    Returns terms of condition field description
    """
    terms_label = _("Terms of Service")
    platform_name = configuration_helpers.get_value("PLATFORM_NAME", settings.PLATFORM_NAME)

    # Translators: "Terms of service" is a legal document users must agree to
    # in order to register a new account.
    error_msg = f'You must agree to the {platform_name} {terms_label}'
    return {
        'name': 'terms_of_service',
        'error_message': error_msg if is_field_required else '',
    }


def add_country_field(is_field_required=False):
    """
    Returns country field description. This field is configurable on frontend, we just need
    to send the field name and whether or not we want to show error message if this field is
    empty
    """
    error_msg = accounts.REQUIRED_FIELD_COUNTRY_MSG
    return {'name': 'country', 'error_message': error_msg if is_field_required else ''}


def add_confirm_email_field(is_field_required=False):
    """
    Returns a email confirmation field description
    """
    # Translators: This label appears above a field on the registration form
    # meant to confirm the user's email address.

    email_label = _("Confirm Email")

    return {
        'name': 'confirm_email',
        'type': SUPPORTED_FIELDS_TYPES['TEXT'],
        'label': email_label,
        'error_message': accounts.REQUIRED_FIELD_CONFIRM_EMAIL_TEXT_MSG if is_field_required else '',
    }


def add_work_experience_field(is_field_required=False):
    """
    Returns the user work experience field description
    """
    # Translators: This label appears above a dropdown menu to select
    # the user's work experience
    work_experience_label = _("Work experience")
    return _add_field_with_configurable_select_options(
        'work_experience', work_experience_label, is_field_required,
    )
