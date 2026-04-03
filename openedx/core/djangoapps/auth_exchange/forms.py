"""
Forms to support third-party to first-party OAuth 2.0 access token exchange
"""

from django import forms
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.forms import CharField
from django.utils.encoding import smart_str
from django.utils.translation import gettext as _
from oauth2_provider.models import Application
from requests import HTTPError
from social_core.backends import oauth as social_oauth
from social_core.exceptions import AuthException

from common.djangoapps.third_party_auth import pipeline


class OAuthValidationError(Exception):
    """
    Exception to throw inside :class:`AccessTokenExchangeForm` if any OAuth2 related errors
    are encountered such as invalid grant type, invalid client, etc.
    :attr:`OAuthValidationError` expects a dictionary outlining the OAuth error
    as its first argument when instantiating.
    :example:
    ::
        class GrantValidationForm(AccessTokenExchangeForm):
            grant_type = forms.CharField()
            def clean_grant(self):
                if not self.cleaned_data.get('grant_type') == 'code':
                    raise OAuthValidationError({
                        'error': 'invalid_grant',
                        'error_description': "%s is not a valid grant type" % (
                            self.cleaned_data.get('grant_type'))
                    })
    """


class ScopeChoiceField(forms.ChoiceField):
    """
    Custom form field that seperates values on space
    """
    widget = forms.SelectMultiple

    def to_python(self, value):
        if not value:
            return []

        # value may come in as a string.
        # try to parse and
        # ultimately return an empty list if nothing remains -- this will
        # eventually raise an `OAuthValidationError` in `validate` where
        # it should be anyways.
        if not isinstance(value, (list, tuple)):
            value = value.split(' ')

        # Split values into list
        return ' '.join([smart_str(val) for val in value]).split(' ')

    def validate(self, value):
        """
        Validates that the input is a list or tuple.
        """
        if self.required and not value:
            raise OAuthValidationError({'error': 'invalid_request'})

        # Validate that each value in the value list is in self.choices.
        for val in value:
            if not self.valid_value(val):
                raise OAuthValidationError({
                    'error': 'invalid_request',
                    'error_description': _("'%s' is not a valid scope.") %
                    val})


class AccessTokenExchangeForm(forms.Form):
    """Form for access token exchange endpoint"""

    access_token = CharField(required=False)
    OAUTH2_PROVIDER = getattr(settings, "OAUTH2_PROVIDER", {})
    scope_choices = ()
    if 'SCOPES' in OAUTH2_PROVIDER:
        scope_choices = OAUTH2_PROVIDER['SCOPES'].items()
    scope = ScopeChoiceField(choices=scope_choices, required=False)

    client_id = CharField(required=False)

    def __init__(self, request, oauth2_adapter, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.oauth2_adapter = oauth2_adapter

    def _clean_fields(self):
        """
        Overriding the default cleaning behaviour to exit early on errors
        instead of validating each field.
        """
        try:
            super()._clean_fields()
        except OAuthValidationError as e:
            self._errors.update(e.args[0])

    def _clean_form(self):
        """
        Overriding the default cleaning behaviour for a shallow error dict.
        """
        try:
            super()._clean_form()
        except OAuthValidationError as e:
            self._errors.update(e.args[0])

    def _require_oauth_field(self, field_name):
        """
        Raise an appropriate OAuthValidationError error if the field is missing
        """
        field_val = self.cleaned_data.get(field_name)
        if not field_val:
            raise OAuthValidationError(
                {
                    "error": "invalid_request",
                    "error_description": f"{field_name} is required",
                }
            )
        return field_val

    def clean_access_token(self):
        """
        Validates and returns the "access_token" field.
        """
        return self._require_oauth_field("access_token")

    def clean_client_id(self):
        """
        Validates and returns the "client_id" field.
        """
        return self._require_oauth_field("client_id")

    def clean_scope(self):
        """
        The scope is assembled by combining all the set flags into a single
        integer value which we can later check again for set bits.
        If *no* scope is set, we return the default scope which is the first
        defined scope in :attr:`provider.constants.SCOPES`.
        """
        flags = self.cleaned_data.get('scope', [])
        return flags

    def clean(self):
        if self._errors:
            return {}

        backend = self.request.backend
        if not isinstance(backend, social_oauth.BaseOAuth2):
            raise OAuthValidationError(
                {
                    "error": "invalid_request",
                    "error_description": f"{backend.name} is not a supported provider",
                }
            )

        self.request.session[pipeline.AUTH_ENTRY_KEY] = pipeline.AUTH_ENTRY_LOGIN_API

        client_id = self.cleaned_data["client_id"]
        try:
            client = self.oauth2_adapter.get_client(client_id=client_id)
        except Application.DoesNotExist:
            raise OAuthValidationError(  # lint-amnesty, pylint: disable=raise-missing-from
                {
                    "error": "invalid_client",
                    "error_description": f"{client_id} is not a valid client_id",
                }
            )
        if client.client_type != Application.CLIENT_PUBLIC:
            raise OAuthValidationError(
                {
                    # invalid_client isn't really the right code, but this mirrors
                    # https://github.com/openedx/django-oauth2-provider/blob/edx/provider/oauth2/forms.py#L331
                    "error": "invalid_client",
                    "error_description": f"{client_id} is not a public client",
                }
            )
        self.cleaned_data["client"] = client

        user = None
        access_token = self.cleaned_data.get("access_token")
        try:
            user = backend.do_auth(access_token, allow_inactive_user=True)
        except (HTTPError, AuthException):
            pass
        # check if user is disabled
        if isinstance(user, User) and not user.has_usable_password():
            self.request.social_strategy.clean_partial_pipeline(access_token)
            raise OAuthValidationError(
                {
                    "error": "account_disabled",
                    "error_description": 'user account is disabled',
                    "error_code": 403
                }
            )

        if user and isinstance(user, User):
            self.cleaned_data["user"] = user
        else:
            # Ensure user does not re-enter the pipeline
            self.request.social_strategy.clean_partial_pipeline(access_token)
            raise OAuthValidationError(
                {
                    "error": "invalid_grant",
                    "error_description": "access_token is not valid",
                }
            )

        return self.cleaned_data
