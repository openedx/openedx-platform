"""Views for version 2 of the profile image endpoints."""

import datetime
import logging
import struct
from contextlib import closing
from zoneinfo import ZoneInfo

import piexif
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from PIL import Image
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_names, set_has_profile_image
from openedx.core.djangoapps.user_api.errors import UserNotFound
from openedx.core.djangoapps.user_api.v2.errors import CatalogedRequestErrorsMixin
from openedx.core.djangoapps.user_api.v2.permissions import IsAddressedUser
from openedx.core.djangoapps.user_api.v2.serializers import ErrorResponse
from openedx.core.djangoapps.user_api.v2.view_utils import (
    RESP_UNAUTHENTICATED,
    RESP_UNREADABLE_BODY,
    SDK_TAG,
    StrictQueryParametersMixin,
)
from openedx.core.djangolib.default_auth_classes import DefaultJwtAuthentication
from openedx.core.lib.api.parsers import TypedFileUploadParser

from ..exceptions import ImageValidationError
from ..images import create_profile_images, remove_profile_images, validate_uploaded_image
from .serializers import IMAGE_UPLOAD_REQUEST, UPLOAD_MEDIA_TYPES, ProfileImageUploadSerializer

log = logging.getLogger(__name__)

_USERNAME_PATH_PARAM = OpenApiParameter(
    name="username",
    description=(
        "Unique name of the user whose image is addressed. The name is read as it is stored, so "
        "`me` here names an account holding that name rather than the account of the caller "
        "making the request."
    ),
    required=True,
    location=OpenApiParameter.PATH,
    type=str,
)

_RESP_ANOTHER_USERS_IMAGE = OpenApiResponse(
    ErrorResponse, description="The image belongs to another user, which nobody else may change.",
)
_RESP_NO_PROFILE = OpenApiResponse(
    ErrorResponse,
    description=(
        "The account at this address carries no profile to record an image on. A name no account "
        "holds is refused as somebody else's image instead of being reported here."
    ),
)

_CONTENT_DISPOSITION_HEADER_PARAM = OpenApiParameter(
    name="Content-Disposition",
    description=(
        "Name the image is sent under, as `attachment; filename=\"picture.png\"`. Required of a "
        "request that sends the image as the whole body, because the extension of that name is "
        "what is held against the media type the body was sent in. A form part carries its own "
        "name and needs no header."
    ),
    required=False,
    location=OpenApiParameter.HEADER,
    type=str,
)

#: What a caller is told about a file that is shaped like an image the site
#: takes but holds nothing the site can draw.
UNREADABLE_IMAGE_MESSAGE = "The image could not be read. The file may be incomplete or corrupted."

#: What a caller is told about a file whose header declares a picture far
#: larger than any this site draws, which is never decoded.
OVERSIZED_IMAGE_MESSAGE = "The image declares more pixels than this site draws."

#: What the imaging library raises when the bytes handed to it hold no picture
#: it can draw: a file whose bytes stop early, or metadata that does not parse.
UNREADABLE_IMAGE_ERRORS = (OSError, struct.error)


class NamedImageUploadParser(TypedFileUploadParser):
    """
    Reads an image sent as the whole request body, and refuses one sent without a name.

    The name a body is sent under carries the extension that is compared with
    the media type, and it is read from the ``Content-Disposition`` header. A
    body sent without that header has no name to compare, which is a fault in
    the request, so it is refused here rather than left to fail further in.
    """

    def get_filename(self, stream, media_type, parser_context):
        """Return the name the body was sent under, refusing a body sent without one."""
        filename = super().get_filename(stream, media_type, parser_context)
        if not filename:
            raise ValidationError({"file": [self.errors["no_filename"]]})
        return filename


@extend_schema(tags=[SDK_TAG])
class ProfileImageView(
    StandardizedErrorMixin,
    CatalogedRequestErrorsMixin,
    StrictQueryParametersMixin,
    APIView,
):
    """
    The image shown beside a user's name, in each size the site renders.

    An account has one image, set and cleared whole and never listed or
    addressed by an identifier of its own, so it is served from this single
    address rather than routed as a collection. A user sets and clears the
    image on their own account, and nobody else's: staff included, since
    clearing an image is an account change rather than a moderation tool. That
    is not what the account collection admits, which is the other reason this
    is a view of its own rather than an action of that collection. The sizes
    are stored under the username the address carries. The addresses those
    stored sizes are served from belong to the account resource rather than to
    this one.
    """

    # The site's own pages let a user who has not confirmed their email
    # address set a picture, and the platform default refuses a session
    # belonging to such an account. Only that one class is replaced: the token
    # class stays the one the platform configures, which is what records a
    # request as having been authenticated the default way.
    authentication_classes = (DefaultJwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (permissions.IsAuthenticated, IsAddressedUser)
    parser_classes = (MultiPartParser, FormParser, NamedImageUploadParser)
    serializer_class = ProfileImageUploadSerializer
    upload_media_types = UPLOAD_MEDIA_TYPES

    @extend_schema(
        summary="Set the profile image",
        description=(
            "Stores the uploaded image in each size the site renders, cropped to a square, and "
            "records on the account that it has one. An image already set is replaced. The image "
            "may be sent as a form field named `file` or as the whole body, with the media type "
            "of the image."
        ),
        parameters=[_USERNAME_PATH_PARAM, _CONTENT_DISPOSITION_HEADER_PARAM],
        request=IMAGE_UPLOAD_REQUEST,
        responses={
            204: OpenApiResponse(description="The account now carries this image."),
            400: OpenApiResponse(
                ErrorResponse,
                description=(
                    "The request carries no usable image: none was sent, the body is not well "
                    "formed in the media type it was sent in, one sent as the whole body was sent "
                    "without a name or under a name whose extension names another media type, the "
                    "image is of a type or a size this site does not take, or no picture can be "
                    "drawn from the file, whose bytes may stop early, carry metadata that does "
                    "not parse, or declare more pixels than this site draws. A query parameter "
                    "this endpoint does not read is refused here too."
                ),
            ),
            401: RESP_UNAUTHENTICATED,
            403: _RESP_ANOTHER_USERS_IMAGE,
            404: _RESP_NO_PROFILE,
            415: RESP_UNREADABLE_BODY,
        },
    )
    def post(self, request, username):
        """Store the uploaded image in each size the site renders."""
        upload = ProfileImageUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        uploaded_file = upload.validated_data["file"]
        with closing(uploaded_file):
            try:
                validate_uploaded_image(uploaded_file)
            except ImageValidationError as error:
                raise self._unusable_image(username, error.user_message, error) from error
            self._read_image(username, uploaded_file)
            image_names = get_profile_image_names(username)
            create_profile_images(uploaded_file, image_names)
            self._record_image(username, True)
            log.info(
                "Generated and uploaded images %(image_names)s for user %(user_id)s",
                {"image_names": list(image_names.values()), "user_id": request.user.id},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Clear the profile image",
        description=(
            "Removes the stored sizes of the user's image and records on the account that it has "
            "none, which is what the default image is shown for. Clearing an account that has no "
            "image of its own is not an error."
        ),
        parameters=[_USERNAME_PATH_PARAM],
        request=None,
        responses={
            204: OpenApiResponse(description="The account carries no image of its own."),
            400: OpenApiResponse(
                ErrorResponse, description="The request carries a query parameter this endpoint does not read.",
            ),
            401: RESP_UNAUTHENTICATED,
            403: _RESP_ANOTHER_USERS_IMAGE,
            404: _RESP_NO_PROFILE,
        },
    )
    def delete(self, request, username):
        """Remove the stored sizes of the user's image."""
        self._record_image(username, False)
        image_names = get_profile_image_names(username)
        remove_profile_images(image_names)
        log.info(
            "Deleted images %(image_names)s for user %(user_id)s",
            {"image_names": list(image_names.values()), "user_id": request.user.id},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _read_image(self, username, uploaded_file):
        """
        Read the uploaded image once, refusing bytes no picture can be drawn from.

        Every stored size is drawn from these bytes and carries the metadata
        found in them, so a file whose bytes stop early, whose metadata does
        not parse, or whose header declares a picture far larger than any this
        site draws fails partway through that work. Reading it here tells a
        fault in the request apart from a failure to store what was drawn from
        it, which is the site's own.
        """
        try:
            image = Image.open(uploaded_file)
            image.load()
            metadata = image.info.get("exif")
            if metadata is not None:
                piexif.load(metadata)
        except Image.DecompressionBombError as error:
            raise self._unusable_image(username, OVERSIZED_IMAGE_MESSAGE, error) from error
        except UNREADABLE_IMAGE_ERRORS as error:
            raise self._unusable_image(username, UNREADABLE_IMAGE_MESSAGE, error) from error
        finally:
            uploaded_file.seek(0)

    def _record_image(self, username, is_uploaded):
        """Record on the account whether it carries an image of its own."""
        uploaded_at = datetime.datetime.now(ZoneInfo("UTC")) if is_uploaded else None
        try:
            set_has_profile_image(username, is_uploaded, uploaded_at)
        except UserNotFound as exc:
            raise NotFound from exc

    def _unusable_image(self, username, message, reason):
        """Return the refusal telling the caller what the site cannot do with this image."""
        log.info("Profile image refused for %r: %r", username, reason)
        return ValidationError({"file": [message]})
