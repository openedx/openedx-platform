"""
Utility functions for validating forms
"""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.http import int_to_base36
from edx_ace import ace
from edx_ace.recipient import Recipient

from common.djangoapps.student.message_types import AccountRecovery as AccountRecoveryMessage
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_current_site
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.djangoapps.user_authn.toggles import should_redirect_to_authn_microfrontend


def send_account_recovery_email_for_user(user, request, email=None):
    """
    Send out a account recovery email for the given user.

    Arguments:
        user (User): Django User object
        request (HttpRequest): Django request object
        email (str): Send email to this address.
    """
    site = get_current_site()
    message_context = get_base_template_context(site)
    site_name = settings.AUTHN_MICROFRONTEND_DOMAIN if should_redirect_to_authn_microfrontend() \
        else configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME)
    message_context.update({
        'request': request,  # Used by google_analytics_tracking_pixel
        'email': email,
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'reset_link': '{protocol}://{site}{link}?is_account_recovery=true'.format(
            protocol='https' if request.is_secure() else 'http',
            site=site_name,
            link=reverse('password_reset_confirm', kwargs={
                'uidb36': int_to_base36(user.id),
                'token': default_token_generator.make_token(user),
            }),
        )
    })

    msg = AccountRecoveryMessage().personalize(
        recipient=Recipient(user.id, email),
        language=get_user_preference(user, LANGUAGE_KEY),
        user_context=message_context,
    )
    ace.send(msg)
