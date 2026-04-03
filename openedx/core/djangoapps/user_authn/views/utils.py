"""
User Auth Views Utils
"""
import logging
import random
import re
import string
from datetime import datetime
from typing import Dict

from django.conf import settings
from django.contrib import messages
from django.utils.translation import gettext as _
from ipware.ip import get_client_ip
from text_unidecode import unidecode

from common.djangoapps import third_party_auth
from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.models import clean_username
from openedx.core.djangoapps.embargo.models import GlobalRestrictedCountry
from openedx.core.djangoapps.geoinfo.api import country_code_from_ip
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

log = logging.getLogger(__name__)
API_V1 = 'v1'
UUID4_REGEX = '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
ENTERPRISE_ENROLLMENT_URL_REGEX = fr'/enterprise/{UUID4_REGEX}/course/{settings.COURSE_KEY_REGEX}/enroll'


def third_party_auth_context(request, redirect_to, tpa_hint=None):
    """
    Context for third party auth providers and the currently running pipeline.

    Arguments:
        request (HttpRequest): The request, used to determine if a pipeline
            is currently running.
        redirect_to: The URL to send the user to following successful
            authentication.
        tpa_hint (string): An override flag that will return a matching provider
            as long as its configuration has been enabled

    Returns:
        dict

    """
    context = {
        "currentProvider": None,
        "platformName": configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        "providers": [],
        "secondaryProviders": [],
        "finishAuthUrl": None,
        "errorMessage": None,
        "registerFormSubmitButtonText": _("Create Account"),
        "syncLearnerProfileData": False,
        "pipeline_user_details": {},
        "skipRegistrationOptionalCheckboxes": False
    }

    if third_party_auth.is_enabled():
        for enabled in third_party_auth.provider.Registry.displayed_for_login(tpa_hint=tpa_hint):
            info = {
                "id": enabled.provider_id,
                "name": enabled.name,
                "iconClass": enabled.icon_class or None,
                "iconImage": enabled.icon_image.url if enabled.icon_image else None,
                "skipHintedLogin": enabled.skip_hinted_login_dialog,
                "skipRegistrationForm": enabled.skip_registration_form,
                "loginUrl": pipeline.get_login_url(
                    enabled.provider_id,
                    pipeline.AUTH_ENTRY_LOGIN,
                    redirect_url=redirect_to,
                ),
                "registerUrl": pipeline.get_login_url(
                    enabled.provider_id,
                    pipeline.AUTH_ENTRY_REGISTER,
                    redirect_url=redirect_to,
                ),
            }
            context["providers" if not enabled.secondary else "secondaryProviders"].append(info)

        running_pipeline = pipeline.get(request)
        if running_pipeline is not None:
            current_provider = third_party_auth.provider.Registry.get_from_pipeline(running_pipeline)
            user_details = running_pipeline['kwargs']['details']
            if user_details:
                username = running_pipeline['kwargs'].get('username') or user_details.get('username')
                if username:
                    user_details['username'] = clean_username(username)
                context['pipeline_user_details'] = user_details

            if current_provider is not None:
                context["currentProvider"] = current_provider.name
                context["finishAuthUrl"] = pipeline.get_complete_url(current_provider.backend_name)
                context["syncLearnerProfileData"] = current_provider.sync_learner_profile_data

                if current_provider.skip_registration_form:
                    # As a reliable way of "skipping" the registration form, we just submit it automatically
                    context["autoSubmitRegForm"] = True

                # Check if SAML provider wants to skip optional checkboxes
                if hasattr(current_provider, 'skip_registration_optional_checkboxes'):
                    context["skipRegistrationOptionalCheckboxes"] = (
                        current_provider.skip_registration_optional_checkboxes
                    )

        # Check for any error messages we may want to display:
        for msg in messages.get_messages(request):
            if msg.extra_tags.split()[0] == "social-auth":
                # msg may or may not be translated. Try translating [again] in case we are able to:
                context["errorMessage"] = _(str(msg))  # pylint: disable=E7610
                break

    return context


def get_mfe_context(request, redirect_to, tpa_hint=None):
    """
    Returns Authn MFE context.
    """

    ip_address = get_client_ip(request)[0]
    country_code = country_code_from_ip(ip_address)
    context = third_party_auth_context(request, redirect_to, tpa_hint)
    context.update({
        'countryCode': country_code,
    })
    return context


def _get_username_prefix(data):
    """
    Get the username prefix (name initials) based on the provided data.

    Args:
    - data (dict):  Registration payload.

    Returns:
    - str: Name initials or None.
    """
    username_regex_partial = settings.USERNAME_REGEX_PARTIAL
    valid_username_regex = r'^[A-Za-z0-9_\-]+$'
    full_name = ''
    try:
        if data.get('first_name', '').strip() and data.get('last_name', '').strip():
            full_name = f"{unidecode(data.get('first_name', ''))} {unidecode(data.get('last_name', ''))}"
        elif data.get('name', '').strip():
            full_name = unidecode(data['name'])

        if full_name.strip():
            matched_name = re.findall(username_regex_partial, full_name)
            if matched_name:
                full_name = " ".join(matched_name)
                name_initials = "".join([name_part[0] for name_part in full_name.split()[:2]])
                if re.match(valid_username_regex, name_initials):
                    return name_initials.upper() if name_initials else None

    except Exception as e:  # pylint: disable=broad-except
        logging.info(f"Error in _get_username_prefix: {e}")
        return None

    return None


def get_auto_generated_username(data):
    """
    Generate username based on learner's name initials, current date and configurable random string.

    This function creates a username in the format <name_initials>_<YYMM>_<configurable_random_string>

    The length of random string is determined by AUTO_GENERATED_USERNAME_RANDOM_STRING_LENGTH setting.

     Args:
    - data (dict):  Registration payload.

    Returns:
    - str: username.
    """
    current_year, current_month = datetime.now().strftime('%y %m').split()

    random_string = ''.join(random.choices(
        string.ascii_uppercase + string.digits,
        k=settings.AUTO_GENERATED_USERNAME_RANDOM_STRING_LENGTH))

    username_prefix = _get_username_prefix(data)
    username_suffix = f"{current_year}{current_month}_{random_string}"

    # We generate the username regardless of whether the name is empty or invalid. We do this
    # because the name validations occur later, ensuring that users cannot create an account without a valid name.
    return f"{username_prefix}_{username_suffix}" if username_prefix else username_suffix


def remove_disabled_country_from_list(countries: Dict) -> Dict:
    """
    Remove disabled countries from the list of countries.

    Args:
    - countries (dict): List of countries.

    Returns:
    - dict: Dict of countries with disabled countries removed.
    """
    if not settings.FEATURES.get("EMBARGO", False):
        return countries

    for country_code in GlobalRestrictedCountry.get_countries():
        del countries[country_code]
    return countries
