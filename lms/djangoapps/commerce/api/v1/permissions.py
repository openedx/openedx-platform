""" Custom API permissions. """


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from rest_framework.permissions import BasePermission, DjangoModelPermissions

from openedx.core.lib.api.permissions import ApiKeyHeaderPermission

from ...utils import is_account_activation_requirement_disabled


class ApiKeyOrModelPermission(BasePermission):
    """ Access granted for requests with API key in header,
    or made by user with appropriate Django model permissions. """
    def has_permission(self, request, view):
        return ApiKeyHeaderPermission().has_permission(request, view) or DjangoModelPermissions().has_permission(
            request, view)


class IsAuthenticatedOrActivationOverridden(BasePermission):
    """ Considers the account activation override switch when determining the authentication status of the user """
    def has_permission(self, request, view):
        if not request.user.is_authenticated and is_account_activation_requirement_disabled():
            try:
                request.user = User.objects.get(id=request.session._session_cache['_auth_user_id'])  # pylint: disable=protected-access
            except User.DoesNotExist:
                pass
        return request.user.is_authenticated
