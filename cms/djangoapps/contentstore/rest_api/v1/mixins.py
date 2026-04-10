"""
Common mixins for module.
"""
import json
import logging
from unittest.mock import patch

from django.http import Http404
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework import status

log = logging.getLogger(__name__)


class PermissionAccessMixin:
    """
    Mixin for testing permission access for views.
    """

    def get_and_check_developer_response(self, response):
        """
        Make basic asserting about the presence of an error response, and return the developer response.
        """
        content = json.loads(response.content.decode("utf-8"))
        assert "developer_message" in content
        return content["developer_message"]

    def test_permissions_unauthenticated(self):
        """
        Test that an error is returned in the absence of auth credentials.
        """
        self.client.logout()
        response = self.client.get(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "Authentication credentials were not provided.")  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_ADVANCED_SETTINGS": True})
    def test_permissions_unauthorized(self):
        """
        Test that an error is returned if the user is unauthorised.
        """
        client, _ = self.create_non_staff_authed_user_client()
        response = client.get(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "You do not have permission to perform this action.")  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class ContainerHandlerMixin:
    """
    A mixin providing common functionality for container handler views.
    """

    def get_object(self, usage_key_string):
        """
        Get an object by usage-id of the block
        """
        try:
            usage_key = UsageKey.from_string(usage_key_string)
            return usage_key
        except InvalidKeyError as err:
            log.error(f"Invalid usage key: {usage_key_string}", exc_info=True)
            raise Http404(f"Object not found for usage key: {usage_key_string}") from err
