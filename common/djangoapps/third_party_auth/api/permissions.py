"""
Third party auth API related permissions
"""

import logging

from edx_rest_framework_extensions.auth.jwt.decoder import decode_jwt_filters
from edx_rest_framework_extensions.permissions import (
    IsStaff,
    IsSuperuser,
    JwtHasScope,
    JwtRestrictedApplication,
    NotJwtRestrictedApplication,
)
from rest_framework.permissions import BasePermission

from openedx.core.lib.api.permissions import ApiKeyHeaderPermission

log = logging.getLogger(__name__)


class JwtHasTpaProviderFilterForRequestedProvider(BasePermission):
    """
    Ensures the JWT used to authenticate contains the appropriate tpa_provider
    filter for the provider_id requested in the view.
    """
    message = 'JWT missing required tpa_provider filter.'

    def has_permission(self, request, view):
        """
        Ensure that the provider_id kwarg provided to the view exists exists
        in the tpa_provider filters in the JWT used to authenticate.
        """
        provider_id = view.kwargs.get('provider_id')
        if not provider_id:
            log.warning("Permission JwtHasTpaProviderFilterForRequestedProvider requires a view with provider_id.")
            return False

        jwt_filters = decode_jwt_filters(request.auth)
        for filter_type, filter_value in jwt_filters:
            if filter_type == 'tpa_provider' and filter_value == provider_id:
                return True

        log.warning(
            "Permission JwtHasTpaProviderFilterForRequestedProvider: required filter tpa_provider:%s was not found.",
            provider_id,
        )
        return False


# TODO: Remove ApiKeyHeaderPermission. Check deprecated_api_key_header custom attribute for active usage.
_NOT_JWT_RESTRICTED_TPA_PERMISSIONS = (
    NotJwtRestrictedApplication &
    (IsSuperuser | ApiKeyHeaderPermission | IsStaff)  # pylint: disable=unsupported-binary-operation
)
_JWT_RESTRICTED_TPA_PERMISSIONS = (
    JwtRestrictedApplication &
    JwtHasScope &
    JwtHasTpaProviderFilterForRequestedProvider
)
TPA_PERMISSIONS = (
    _NOT_JWT_RESTRICTED_TPA_PERMISSIONS | _JWT_RESTRICTED_TPA_PERMISSIONS
)
