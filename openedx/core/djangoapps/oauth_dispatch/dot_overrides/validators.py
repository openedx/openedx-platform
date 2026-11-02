"""
Classes that override default django-oauth-toolkit behavior
"""


from datetime import datetime, timedelta

from django.contrib.auth import authenticate, get_user_model
from django.db.models.signals import pre_save
from django.dispatch import receiver
from oauth2_provider.models import AccessToken
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauth2_provider.scopes import get_scopes_backend
from zoneinfo import ZoneInfo

from ..models import RestrictedApplication
# pylint: disable=W0223


@receiver(pre_save, sender=AccessToken)
def on_access_token_presave(sender, instance, *args, **kwargs):  # pylint: disable=unused-argument
    """
    Mark AccessTokens as expired for 'restricted applications' if required.
    """
    if RestrictedApplication.should_expire_access_token(instance.application):
        instance.expires = datetime(1970, 1, 1, tzinfo=ZoneInfo("UTC"))


class EdxOAuth2Validator(OAuth2Validator):
    """
    Validator class that implements edX-specific custom behavior:

        * It allows users to log in with their email or username.
        * It does not require users to be active before logging in.
    """

    def validate_user(self, username, password, client, request, *args, **kwargs):
        """
        Authenticate users, but allow inactive users (with u.is_active == False)
        to authenticate.
        """
        user = self._authenticate(username=username, password=password)
        if user is not None:
            request.user = user
            return True
        return False

    def _authenticate(self, username, password):
        """
        Authenticate the user, allowing the user to identify themselves either
        by username or email
        """

        authenticated_user = authenticate(username=username, password=password)
        if authenticated_user is None:
            UserModel = get_user_model()  # pylint: disable=invalid-name
            try:
                email_user = UserModel.objects.get(email=username)
            except UserModel.DoesNotExist:
                authenticated_user = None
            else:
                authenticated_user = authenticate(username=email_user.username, password=password)
        return authenticated_user

    def save_bearer_token(self, token, request, *args, **kwargs):
        """
        Ensure that access tokens issued via client credentials grant are
        associated with the owner of the ``Application``.

        Also, update the `expires_in` value in the token response for
        RestrictedApplications.
        """
        grant_type = request.grant_type
        user = request.user

        if grant_type == 'client_credentials':
            # Temporarily remove the grant type to avoid triggering the super method's code that removes request.user.
            request.grant_type = None

            # Ensure the tokens get associated with the correct user since DOT does not normally
            # associate access tokens issued with the client_credentials grant to users.
            request.user = request.client.user

        super().save_bearer_token(token, request, *args, **kwargs)

        is_restricted_client = self._update_token_expiry_if_restricted_client(token, request.client)
        if not is_restricted_client:
            self._update_token_expiry_if_overridden_in_request(token, request)

        # Restore the original request attributes
        request.grant_type = grant_type
        request.user = user

    def get_default_scopes(self, client_id, request, *args, **kwargs):
        """
        Returns the default scopes.

        If the request payload does not have `scopes` attribute for a grant_type of
        client credentials, add `user_id` as a default scope if it is an allowed scope.
        """
        default_scopes = super().get_default_scopes(client_id, request, *args, **kwargs)
        if request.grant_type == 'client_credentials' and not request.scopes:
            if get_scopes_backend().has_user_id_in_application_scopes(application=request.client):
                # copy the default scopes and add user_id to it to avoid modifying the original list
                extended_default_scopes = default_scopes.copy()
                extended_default_scopes.append('user_id')
                return extended_default_scopes
        return default_scopes

    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        """
        Ensure required scopes are permitted (as specified in the settings file)
        """
        available_scopes = get_scopes_backend().get_available_scopes(application=client, request=request)
        return set(scopes).issubset(set(available_scopes))

    def _update_token_expiry_if_restricted_client(self, token, client):
        """
        Update the token's expires_in value if the given client is a
        RestrictedApplication and return whether the given client is restricted.
        """
        # Since RestrictedApplications override the DOT defined expiry such that
        # access_tokens are always expired, re-read the token from the database
        # and calculate expires_in (in seconds) from the database value. This
        # value should be a negative value, meaning that it is already expired.
        if RestrictedApplication.should_expire_access_token(client):
            access_token = AccessToken.objects.get(token=token['access_token'])
            expires_in = (access_token.expires - _get_utc_now()).total_seconds()
            assert expires_in < 0
            token['expires_in'] = expires_in
            return True

    def _update_token_expiry_if_overridden_in_request(self, token, request):
        """
        Update the token's expires_in value if the request specifies an
        expiration value and update the expires value on the stored AccessToken
        object.

        This is needed since DOT's save_bearer_token method always uses
        the dot_settings.ACCESS_TOKEN_EXPIRE_SECONDS value instead of applying
        the requesting expiration value.
        """
        expires_in = getattr(request, 'expires_in', None)
        if expires_in:
            access_token = AccessToken.objects.get(token=token['access_token'])
            access_token.expires = _get_utc_now() + timedelta(seconds=expires_in)
            access_token.save()
            token['expires_in'] = expires_in


def _get_utc_now():
    """
    Return current time in UTC.
    """
    return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
