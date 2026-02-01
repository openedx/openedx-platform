"""
Django forms for accounts
"""

import logging
from typing import Optional, Tuple

from django import forms
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils.translation import gettext as _

from common.djangoapps.student.models import User
from openedx.core.djangoapps.user_api.accounts.utils import handle_retirement_cancellation
from openedx.core.djangoapps.user_authn.views.registration_form import (
    get_extended_profile_model,
    get_registration_extension_form,
)

logger = logging.getLogger(__name__)


class RetirementQueueDeletionForm(forms.Form):
    """
    Admin form to facilitate learner retirement cancellation
    """
    cancel_retirement = forms.BooleanField(required=True)

    def save(self, retirement):
        """
        When the form is POSTed we double-check the retirment status
        and perform the necessary steps to cancel the retirement
        request.
        """
        if retirement.current_state.state_name != 'PENDING':
            self.add_error(
                None,
                # Translators: 'current_state' is a string from an enumerated list indicating the learner's retirement
                # state. Example: FORUMS_COMPLETE
                "Retirement requests can only be cancelled for users in the PENDING state."
                " Current request state for '{original_username}': {current_state}".format(
                    original_username=retirement.original_username,
                    current_state=retirement.current_state.state_name
                )
            )
            raise ValidationError('Retirement is in the wrong state!')

        handle_retirement_cancellation(retirement)


def extract_extended_profile_fields_data(extended_profile: Optional[list]) -> Tuple[dict, dict]:
    """
    Extract extended profile fields data from extended_profile structure.

    Args:
        extended_profile (Optional[list]): List of field data dictionaries with keys
            'field_name' and 'field_value'

    Returns:
        tuple: A tuple containing (extended_profile_fields_data, field_errors)
            - extended_profile_fields_data (dict): Extracted custom fields data
            - field_errors (dict): Dictionary of validation errors, if any
    """
    field_errors = {}

    if not isinstance(extended_profile, list):
        field_errors["extended_profile"] = {
            "developer_message": "extended_profile must be a list",
            "user_message": _("Invalid extended profile format"),
        }
        return {}, field_errors

    extended_profile_fields_data = {}

    for field_data in extended_profile:
        if not isinstance(field_data, dict):
            logger.warning("Invalid field_data structure in extended_profile: %s", field_data)
            continue

        field_name = field_data.get("field_name")
        field_value = field_data.get("field_value")

        if not field_name:
            logger.warning("Missing field_name in extended_profile field_data: %s", field_data)
            continue

        if field_value is not None:
            extended_profile_fields_data[field_name] = field_value

    return extended_profile_fields_data, field_errors


def get_extended_profile_form(extended_profile_fields_data: dict, user: User) -> Tuple[Optional[forms.Form], dict]:
    """
    Get and validate an extended profile form instance.

    Args:
        extended_profile_fields_data (dict): Extended profile field data to populate the form
        user (User): User instance to associate with the extended profile

    Returns:
        tuple: A tuple containing (extended_profile_form, field_errors)
            - extended_profile_form (Optional[forms.Form]): The validated form instance, or None if
              no extended profile form is configured or creation fails
            - field_errors (dict): Dictionary of validation errors, if any
    """
    field_errors = {}

    try:
        extended_profile_model = get_extended_profile_model()
    except ImportError as e:
        logger.warning("Extended profile model not available: %s", str(e))
        return None, field_errors

    kwargs = {}

    try:
        kwargs["instance"] = extended_profile_model.objects.get(user=user)
    except AttributeError:
        logger.info("No extended profile model configured")
    except ObjectDoesNotExist:
        logger.info("No existing extended profile found for user %s, creating new instance", user.username)

    try:
        extended_profile_form = get_registration_extension_form(data=extended_profile_fields_data, **kwargs)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Unexpected error creating custom form for user %s: %s", user.username, str(e))
        field_errors["extended_profile"] = {
            "developer_message": f"Error creating custom form: {str(e)}",
            "user_message": _("There was an error processing the extended profile information"),
        }
        return None, field_errors

    if extended_profile_form is None:
        return None, field_errors

    if not extended_profile_form.is_valid():
        logger.info("Extended profile form validation failed with errors: %s", extended_profile_form.errors)

        for field_name, field_errors_list in extended_profile_form.errors.items():
            first_error = field_errors_list[0] if field_errors_list else "Unknown error"
            field_errors[field_name] = {
                "developer_message": f"Error in extended profile field {field_name}: {first_error}",
                "user_message": str(first_error),
            }

    return extended_profile_form, field_errors


def validate_and_get_extended_profile_form(
    extended_profile_data: list, user: User
) -> Tuple[Optional[forms.Form], dict]:
    """
    Validate and return an extended profile form instance.

    This function orchestrates the extraction and validation of extended profile data.

    Args:
        extended_profile_data (list): The raw extended_profile data from the API request
        user (User): The user instance for whom the extended profile is being validated

    Returns:
        tuple: A tuple containing (validated_form, field_errors)
            - validated_form (Optional[forms.Form]): The validated form instance, or None if
              validation fails or no extended profile is configured
            - field_errors (dict): Dictionary of validation errors, if any
    """
    extended_profile_fields_data, field_errors = extract_extended_profile_fields_data(extended_profile_data)

    if field_errors:
        return None, field_errors

    if not extended_profile_fields_data:
        return None, {}

    extended_profile_form, form_errors = get_extended_profile_form(extended_profile_fields_data, user)

    if form_errors:
        field_errors.update(form_errors)

    return extended_profile_form, field_errors
