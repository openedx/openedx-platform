"""
This module contains the view for registering a device for push notifications.
"""
from django.conf import settings
from edx_ace.push_notifications.views import GCMDeviceViewSet as GCMDeviceViewSetBase
from rest_framework import status
from rest_framework.response import Response

from ..decorators import mobile_view


@mobile_view()
class GCMDeviceViewSet(GCMDeviceViewSetBase):
    """
    **Use Case**
        This endpoint allows clients to register a device for push notifications.

        If the device is already registered, the existing registration will be updated.
        If setting PUSH_NOTIFICATIONS_SETTINGS is not configured, the endpoint will return a 501 error.

    **Example Request**
        POST /api/mobile/{version}/notifications/create-token/
        **POST Parameters**
          The body of the POST request can include the following parameters.
          * name (optional) - A name of the device.
          * registration_id (required) - The device token of the device.
          * device_id (optional) - ANDROID_ID / TelephonyManager.getDeviceId() (always as hex)
          * active (optional) - Whether the device is active, default is True.
            If False, the device will not receive notifications.
          * cloud_message_type (required) - You should choose FCM or GCM. Currently, only FCM is supported.
          * application_id (optional) - Opaque application identity, should be filled in for multiple
            key/certificate access. Should be equal settings.FCM_APP_NAME.
    **Example Response**
        ```json
        {
            "id": 1,
            "name": "My Device",
            "registration_id": "fj3j4",
            "device_id": 1234,
            "active": true,
            "date_created": "2024-04-18T07:39:37.132787Z",
            "cloud_message_type": "FCM",
            "application_id": "my_app_id"
        }
        ```
    """

    def create(self, request, *args, **kwargs):
        if not getattr(settings, 'PUSH_NOTIFICATIONS_SETTINGS', None):
            return Response('Push notifications are not configured.', status.HTTP_501_NOT_IMPLEMENTED)

        return super().create(request, *args, **kwargs)
