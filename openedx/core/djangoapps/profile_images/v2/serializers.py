"""Serializers for version 2 of the profile image endpoints."""

import itertools

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..images import IMAGE_TYPES


# A file field is published as the address a stored file is served from,
# because that is what a response carries; this body carries the file itself.
@extend_schema_field(OpenApiTypes.BINARY)
class UploadedImageField(serializers.FileField):
    """A file sent as part of the request rather than named by its address."""


class ProfileImageUploadSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The image a user sends to be shown beside their name."""

    file = UploadedImageField(
        help_text=(
            "Image to show beside the user's name. It is stored in each size the site renders, "
            "cropped to a square, and must be a JPEG, PNG or GIF within the size limits this "
            "site configures."
        ),
    )


#: The media types an image sent as the whole request body may be sent in,
#: which are the ones the site stores a picture from.
UPLOAD_MEDIA_TYPES = tuple(sorted(set(
    itertools.chain(*(image_type.mimetypes for image_type in IMAGE_TYPES.values()))
)))

#: What an upload carries, by media type: the image as a named part of a form,
#: or the image alone as the whole body sent in the media type of the image.
#: Every address that takes an image reads both forms and publishes this.
IMAGE_UPLOAD_REQUEST = {
    "multipart/form-data": ProfileImageUploadSerializer,
    **{media_type: OpenApiTypes.BINARY for media_type in UPLOAD_MEDIA_TYPES},
}
