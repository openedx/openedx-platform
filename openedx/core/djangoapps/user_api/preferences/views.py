"""
An API for retrieving user preference information.

For additional information and historical context, see:
https://openedx.atlassian.net/wiki/display/TNL/User+API
"""


from django.db import transaction
from django.utils.translation import gettext as _
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.parsers import MergePatchParser
from openedx.core.lib.api.permissions import IsUserInUrlOrStaff

from ..errors import PreferenceUpdateError, PreferenceValidationError, UserNotAuthorized, UserNotFound
from .api import (
    delete_user_preference,
    get_user_preference,
    get_user_preferences,
    set_user_preference,
    update_user_preferences,
)


class PreferencesView(APIView):
    """
        **Use Cases**

            Get or update the user's preference information. Updates are only
            supported through merge patch. Preference values of null in a
            patch request are treated as requests to remove the preference.

        **Example Requests**

            GET /api/user/v1/preferences/{username}/

            PATCH /api/user/v1/preferences/{username}/ with content_type "application/merge-patch+json"

        **Response Values for GET**

            If no user exists with the specified username, an HTTP 404 "Not
            Found" response is returned.

            If a user without "is_staff" access requests preferences for a
            different user, an HTTP 404 "Not Found" message is returned.

            If the user makes the request for her own account, or makes a
            request for another account and has "is_staff" access, an HTTP 200
            "OK" response is returned. The response contains a JSON dictionary
            with a key/value pair (of type String) for each preference.

            The list of preferences depends on your implementation. By default,
            the list includes the following preferences.

            * account_privacy: The user's setting for sharing her personal
              profile. Possible values are "all_users" or "private".
            * pref-lan: The user's preferred language, as set in account
              settings.

        **Response Values for PATCH**

            Users can only modify their own preferences. If the
            requesting user does not have the specified username and has staff
            access, the request returns an HTTP 403 "Forbidden" response. If
            the requesting user does not have staff access, the request
            returns an HTTP 404 "Not Found" response to avoid revealing the
            existence of the account.

            If no user exists with the specified username, an HTTP 404 "Not
            Found" response is returned.

            If "application/merge-patch+json" is not the specified content
            type, a 415 "Unsupported Media Type" response is returned.

            If validation errors prevent the update, this method returns a 400
            "Bad Request" response that includes a "field_errors" field that
            lists all error messages.

            If a failure at the time of the update prevents the update, a 400
            "Bad Request" error is returned. The JSON collection contains
            specific errors.

            If the update is successful, an HTTP 204 "No Content" response is
            returned with no additional content.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAuthenticated, IsUserInUrlOrStaff)
    parser_classes = (MergePatchParser,)

    def get(self, request, username):
        """
        GET /api/user/v1/preferences/{username}/
        """
        try:
            user_preferences = get_user_preferences(request.user, username=username)
        except UserNotAuthorized:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(user_preferences)

    def patch(self, request, username):
        """
        PATCH /api/user/v1/preferences/{username}/
        """
        if not request.data or not getattr(request.data, "keys", None):
            error_message = _("No data provided for user preference update")
            return Response(
                {
                    "developer_message": error_message,
                    "user_message": error_message
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            with transaction.atomic():
                update_user_preferences(request.user, request.data, user=username)
        except UserNotAuthorized:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except PreferenceValidationError as error:
            return Response(
                {"field_errors": error.preference_errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        except PreferenceUpdateError as error:
            return Response(
                {
                    "developer_message": error.developer_message,
                    "user_message": error.user_message
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PreferencesDetailView(APIView):
    """
        **Use Cases**

            Get, create, update, or delete a specific user preference.

        **Example Requests**

            GET /api/user/v1/preferences/{username}/{preference_key}

            PUT /api/user/v1/preferences/{username}/{preference_key}

            DELETE /api/user/v1/preferences/{username}/{preference_key}

        **Response Values for GET**

            If the specified username or preference does not exist, an HTTP
            404 "Not Found" response is returned.

            If a user without "is_staff" access requests preferences for a
            different user, a 404 error is returned.

            If the user makes the request for her own account, or makes a
            request for another account and has "is_staff" access, an HTTP 200
            "OK" response is returned that contains a JSON string.

        **Response Values for PUT**

            Users can only modify their own preferences. If the
            requesting user does not have the specified username and has staff
            access, the request returns an HTTP 403 "Forbidden" response. If
            the requesting user does not have staff access, the request
            returns an HTTP 404 "Not Found" response to avoid revealing the
            existence of the account.

            If the specified preference does not exist, an HTTP 404 "Not
            Found" response is returned.

            If the request is successful, a 204 "No Content" status is returned
            with no additional content.

        **Response Values for DELETE**

            Users can only delete their own preferences. If the
            requesting user does not have the specified username and has staff
            access, the request returns an HTTP 403 "Forbidden" response. If
            the requesting user does not have staff access, the request
            returns an HTTP 404 "Not Found" response to avoid revealing the
            existence of the account.

            If the specified preference does not exist, an HTTP 404 "Not
            Found" response is returned.

            If the update is successful, an HTTP 204 "No Content" response is
            returned with no additional content.
    """
    authentication_classes = (BearerAuthenticationAllowInactiveUser, SessionAuthenticationAllowInactiveUser)
    permission_classes = (permissions.IsAuthenticated, IsUserInUrlOrStaff)

    def get(self, request, username, preference_key):
        """
        GET /api/user/v1/preferences/{username}/{preference_key}
        """
        try:
            value = get_user_preference(request.user, preference_key, username=username)
            # There was no preference with that key, raise a 404.
            if value is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
        except UserNotAuthorized:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(value)

    def put(self, request, username, preference_key):
        """
        PUT /api/user/v1/preferences/{username}/{preference_key}
        """
        try:
            set_user_preference(request.user, preference_key, request.data, username=username)
        except UserNotAuthorized:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except PreferenceValidationError as error:
            return Response(
                {
                    "developer_message": error.preference_errors[preference_key]["developer_message"],
                    "user_message": error.preference_errors[preference_key]["user_message"]
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        except PreferenceUpdateError as error:
            return Response(
                {
                    "developer_message": error.developer_message,
                    "user_message": error.user_message
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request, username, preference_key):
        """
        DELETE /api/user/v1/preferences/{username}/{preference_key}
        """
        try:
            preference_existed = delete_user_preference(request.user, preference_key, username=username)
        except UserNotAuthorized:
            return Response(status=status.HTTP_403_FORBIDDEN)
        except UserNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except PreferenceUpdateError as error:
            return Response(
                {
                    "developer_message": error.developer_message,
                    "user_message": error.user_message
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        if not preference_existed:
            return Response(status=status.HTTP_404_NOT_FOUND)

        return Response(status=status.HTTP_204_NO_CONTENT)
