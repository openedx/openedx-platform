"""
Defines the URL routes for this app.

NOTE: These views are deprecated.  These routes are superseded by
``/api/user/v1/accounts/{username}/image``, found in
``openedx.core.djangoapps.user_api.urls``.
"""
# pylint: enable=unicode-format-string  # pylint: disable=bad-option-value


from django.conf import settings
from django.urls import re_path

from .views import ProfileImageRemoveView, ProfileImageUploadView

urlpatterns = [
    re_path(
        r'^v1/' + settings.USERNAME_PATTERN + '/upload$',
        ProfileImageUploadView.as_view(),
        name="profile_image_upload"
    ),
    re_path(
        r'^v1/' + settings.USERNAME_PATTERN + '/remove$',
        ProfileImageRemoveView.as_view(),
        name="profile_image_remove"
    ),
]
