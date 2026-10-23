"""Tests for the version 2 profile image views."""

import datetime
import os
import struct
import zlib
from contextlib import closing
from io import BytesIO
from unittest import mock
from zoneinfo import ZoneInfo

import freezegun
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    get_profile_image_names,
    get_profile_image_storage,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

from ...tests.helpers import make_image_file

PASSWORD = "Password1234"

#: Where the view records what it refused, which is where an operator reads
#: why a caller was told their image could not be used.
VIEWS_LOGGER = "openedx.core.djangoapps.profile_images.v2.views"

#: Where the classes the platform declares record that they read a request.
DEFAULT_AUTH_RECORDER = "openedx.core.djangolib.default_auth_classes.set_custom_attribute"

def earlier_address(username):
    """Return the version 1 address of one user's image, which takes both methods."""
    return f"/api/user/v1/accounts/{username}/image"


class ProfileImageTestCase(APITestCase):
    """Fixtures shared by the version 2 profile image tests."""

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(
            username="image-owner", email="image-owner@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="image-other", email="image-other@example.com", password=PASSWORD,
        )
        self.staff = UserFactory.create(
            username="image-staff", email="image-staff@example.com", password=PASSWORD, is_staff=True,
        )
        self.url = self.image_url(self.owner.username)
        self.storage = get_profile_image_storage()
        self.addCleanup(self.remove_stored_images, self.owner.username)
        self.addCleanup(self.remove_stored_images, self.other.username)

    def remove_stored_images(self, username):
        """Take whatever this user has stored out of the image store."""
        for name in get_profile_image_names(username).values():
            self.storage.delete(name)

    def image_url(self, username):
        """Return the address of one user's image."""
        return reverse("user_v2:account_profile_image", kwargs={"username": username})

    def login(self, user):
        """Sign the given user in through the session these views accept."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def token_headers(self, user):
        """Return the request headers that carry a token identifying the given user."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def sign_in_as_a_case_variant_of(self, user):
        """
        Sign in a caller whose own name is the given user's, in upper case.

        The variant is set in memory only, since a site that stores usernames
        case-insensitively has no room for both names at once.
        """
        caller = UserFactory.create(
            username="image-variant", email="image-variant@example.com", password=PASSWORD,
        )
        caller.username = user.username.upper()
        self.client.force_authenticate(user=caller)
        return caller

    def upload(self, address=None, **kwargs):
        """Send an image as a form field, the way a browser does."""
        with make_image_file(**kwargs) as image_file:
            return self.client.post(address or self.url, {"file": image_file}, format="multipart")

    def not_an_image(self):
        """Return a file the site stores nothing from, long enough to pass the size check."""
        return SimpleUploadedFile("notes.txt", b"x" * 200, content_type="text/plain")

    def unreadable_image(self):
        """
        Return a file that answers every check the site makes and still holds no image.

        The name, the media type and the leading bytes are a PNG's, so the size,
        extension, media-type and leading-byte checks all pass and the file is
        only found to be unreadable when the site tries to draw it.
        """
        return SimpleUploadedFile(
            "picture.png", bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 400, content_type="image/png",
        )

    def noisy_image(self, image_format="JPEG", dimensions=(320, 240), **options):
        """
        Return the bytes of an image of random pixels, in the given format.

        Random pixels compress to little less than their own size, so half of
        such a file is still larger than the smallest file the site takes,
        which is what makes a part of one stand in for an upload that stopped
        in transfer. An image of one colour shrinks to a few hundred bytes and
        would be refused for its size before anything tried to draw it.
        """
        image = Image.frombytes("RGB", dimensions, os.urandom(dimensions[0] * dimensions[1] * 3))
        body = BytesIO()
        image.save(body, format=image_format, **options)
        return body.getvalue()

    def cut_short(self, data):
        """Return the leading half of the given bytes, as an upload that stopped partway."""
        return data[: len(data) // 2]

    def image_with_unreadable_metadata(self):
        """Return a JPEG carrying a metadata block that is not the metadata it announces."""
        return self.noisy_image(exif=b"Exif\x00\x00" + b"\xff" * 64)

    def image_declaring_more_pixels_than_it_holds(self, dimensions=(40000, 40000)):
        """
        Return a PNG of a few hundred bytes whose header declares a vast picture.

        A PNG records its dimensions in its header and its pixels in a later
        chunk, so a file that declares far more pixels than it carries is
        refused on the declaration alone, without anything being decoded.
        """
        def chunk(kind, payload):
            return (
                struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload))
            )

        header = struct.pack(">IIBBBBB", dimensions[0], dimensions[1], 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\x0a"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
            + chunk(b"tEXt", b"padding" + b"\x00" * 150)
            + chunk(b"IEND", b"")
        )

    def stored_sizes(self, username):
        """Return the sizes this user has an image stored in."""
        return {
            size for size, name in get_profile_image_names(username).items()
            if self.storage.exists(name)
        }

    def all_sizes(self, username):
        """Return every size the site stores an image in."""
        return set(get_profile_image_names(username))

    def has_image(self, user):
        """Return whether the account records an image of its own."""
        user.profile.refresh_from_db()
        return user.profile.has_profile_image


@skip_unless_lms
class ProfileImageUploadTests(ProfileImageTestCase):
    """POST /api/user/v2/accounts/{username}/profile_image/"""

    def test_a_user_sets_the_image_on_their_own_account(self):
        self.login(self.owner)
        response = self.upload()
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.data is None
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_each_stored_size_is_a_square_of_that_size(self):
        self.login(self.owner)
        self.upload(dimensions=(320, 240))
        for size, name in get_profile_image_names(self.owner.username).items():
            with closing(Image.open(self.storage.path(name))) as stored:
                assert stored.size == (size, size), size
                assert stored.format == "JPEG", size

    def test_the_account_records_when_the_image_was_set(self):
        self.login(self.owner)
        self.upload()
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.profile_image_uploaded_at is not None

    def test_an_image_sent_as_the_whole_body_is_stored(self):
        """The mobile apps send the image bytes with the media type of the image."""
        self.login(self.owner)
        with make_image_file(extension=".jpg") as image_file:
            response = self.client.post(
                self.url,
                image_file.read(),
                content_type="image/jpeg",
                HTTP_CONTENT_DISPOSITION='attachment; filename="image.jpg"',
            )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)

    def test_a_second_image_replaces_the_first(self):
        self.login(self.owner)
        with freezegun.freeze_time("2024-01-01 10:00:00"):
            assert self.upload(dimensions=(320, 240)).status_code == status.HTTP_204_NO_CONTENT
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.profile_image_uploaded_at == datetime.datetime(
            2024, 1, 1, 10, 0, tzinfo=ZoneInfo("UTC"),
        )

        with freezegun.freeze_time("2024-01-01 11:30:00"):
            assert self.upload(dimensions=(640, 640), prefix="second").status_code == (
                status.HTTP_204_NO_CONTENT
            )
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.profile_image_uploaded_at == datetime.datetime(
            2024, 1, 1, 11, 30, tzinfo=ZoneInfo("UTC"),
        )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_a_body_carrying_no_image_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.post(self.url, {}, format="multipart"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"file": ["No file was submitted."]}
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_the_site_will_not_take_is_refused_by_what_is_wrong_with_it(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.post(self.url, {"file": self.not_an_image()}, format="multipart"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": ["The file must be one of the following types: .jpeg, .jpg, .png, .gif."],
        }
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_whose_media_type_does_not_match_its_name_is_refused(self):
        self.login(self.owner)
        with make_image_file(extension=".png") as image_file:
            mislabelled = SimpleUploadedFile(
                "picture.png", image_file.read(), content_type="image/jpeg",
            )
        envelope = assert_error_envelope(
            self.client.post(self.url, {"file": mislabelled}, format="multipart"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": [
                "The Content-Type header for this file does not match the file data. "
                "The file may be corrupted.",
            ],
        }
        assert self.stored_sizes(self.owner.username) == set()

    def test_an_image_larger_than_the_site_takes_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.upload(force_size=1024 * 1024 + 1),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"file": ["The file must be smaller than 1 MB in size."]}
        assert self.stored_sizes(self.owner.username) == set()

    def test_an_image_smaller_than_the_site_takes_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.upload(dimensions=(1, 1), extension=".png", force_size=99),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"file": ["The file must be at least 100 bytes in size."]}
        assert self.stored_sizes(self.owner.username) == set()

    def test_a_file_shaped_like_an_image_that_holds_none_is_refused(self):
        """A file whose leading bytes are a PNG's is one nothing can be drawn from."""
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.post(self.url, {"file": self.unreadable_image()}, format="multipart"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": ["The image could not be read. The file may be incomplete or corrupted."],
        }
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_whose_bytes_stop_early_is_refused(self):
        """An upload cut off partway answers every check its bytes can answer."""
        self.login(self.owner)
        for image_format, name, media_type in (
            ("JPEG", "picture.jpg", "image/jpeg"),
            ("PNG", "picture.png", "image/png"),
            ("GIF", "picture.gif", "image/gif"),
        ):
            with self.subTest(image_format=image_format):
                cut_off = SimpleUploadedFile(
                    name, self.cut_short(self.noisy_image(image_format=image_format)),
                    content_type=media_type,
                )
                envelope = assert_error_envelope(
                    self.client.post(self.url, {"file": cut_off}, format="multipart"),
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )
                assert envelope["errors"] == {
                    "file": ["The image could not be read. The file may be incomplete or corrupted."],
                }
                assert self.stored_sizes(self.owner.username) == set()
                assert self.has_image(self.owner) is False

    def test_an_image_sent_as_the_whole_body_whose_bytes_stop_early_is_refused(self):
        """A body cut off in transfer is as much the caller's fault as a form part is."""
        self.login(self.owner)
        with self.assertLogs(VIEWS_LOGGER, level="INFO") as logged:
            response = self.client.post(
                self.url,
                self.cut_short(self.noisy_image(image_format="PNG")),
                content_type="image/png",
                HTTP_CONTENT_DISPOSITION='attachment; filename="picture.png"',
            )
        assert [
            line for line in logged.output
            if self.owner.username in line and "OSError" in line
        ], logged.output
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": ["The image could not be read. The file may be incomplete or corrupted."],
        }
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_whose_metadata_cannot_be_read_is_refused(self):
        """Each stored size carries the metadata of the file it was drawn from."""
        self.login(self.owner)
        unreadable = SimpleUploadedFile(
            "picture.jpg", self.image_with_unreadable_metadata(), content_type="image/jpeg",
        )
        envelope = assert_error_envelope(
            self.client.post(self.url, {"file": unreadable}, format="multipart"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": ["The image could not be read. The file may be incomplete or corrupted."],
        }
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_declaring_more_pixels_than_the_site_draws_is_refused(self):
        """The refusal names the declaration, which is not the same fault as a file that stops early."""
        self.login(self.owner)
        declared = SimpleUploadedFile(
            "picture.png", self.image_declaring_more_pixels_than_it_holds(), content_type="image/png",
        )
        envelope = assert_error_envelope(
            self.client.post(self.url, {"file": declared}, format="multipart"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": ["The image declares more pixels than this site draws."],
        }
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_sent_as_the_whole_body_without_a_name_is_refused(self):
        """The name the body is sent under carries the extension, and nothing else does."""
        self.login(self.owner)
        with make_image_file(extension=".jpg") as image_file:
            response = self.client.post(self.url, image_file.read(), content_type="image/jpeg")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "file": [
                "Missing filename. Request should include a Content-Disposition header "
                "with a filename parameter.",
            ],
        }
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_sent_as_the_whole_body_under_a_name_of_another_type_is_refused(self):
        """The extension of the name and the media type of the body must agree."""
        self.login(self.owner)
        with make_image_file(extension=".jpg") as image_file:
            response = self.client.post(
                self.url,
                image_file.read(),
                content_type="image/jpeg",
                HTTP_CONTENT_DISPOSITION='attachment; filename="image.png"',
            )
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="malformed-body",
        )
        assert envelope["title"] == "Malformed Request Body"
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_a_form_body_that_is_not_well_formed_is_refused_as_the_callers_fault(self):
        """A multipart body sent without the boundary that separates its parts cannot be read."""
        self.login(self.owner)
        response = self.client.post(
            self.url, b"--nothing-here--", content_type="multipart/form-data",
        )
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="malformed-body",
        )
        assert envelope["title"] == "Malformed Request Body"
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_a_learner_may_not_set_another_learners_image(self):
        self.login(self.other)
        assert_error_envelope(
            self.upload(address=self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_a_caller_named_for_the_address_in_case_alone_may_not_set_the_image(self):
        """The address names one account, and a name that differs in case is another name."""
        self.sign_in_as_a_case_variant_of(self.owner)
        assert_error_envelope(
            self.upload(address=self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_a_name_no_account_holds_is_refused_as_somebody_elses_image(self):
        """
        A name nobody holds is not the caller's own, so it is refused rather than reported missing.

        The refusal a caller meets at a name nobody holds is the one they meet
        at any other account's address, which is what the published refusals
        say happens here.
        """
        self.login(self.owner)
        assert_error_envelope(
            self.upload(address=self.image_url("nobody-at-all")),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes("nobody-at-all") == set()

    def test_an_account_carrying_no_profile_is_not_found(self):
        """The account is read when the image is recorded, and the refusal says so."""
        self.login(self.owner)
        self.owner.profile.delete()
        assert_error_envelope(
            self.upload(),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_staff_caller_may_not_set_another_account_s_image_either(self):
        """Staff access is not an exception here: the image is the owner's to set."""
        self.login(self.staff)
        assert_error_envelope(
            self.upload(address=self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == set()

    def test_a_refused_upload_stays_refused_whatever_else_the_request_carries(self):
        """Who may set this image is settled from the address, before the query string or the form is read."""
        self.login(self.other)
        for address in (
            f"{self.url}?username={self.other.username}",
            f"{self.url}?user={self.other.username}",
        ):
            with self.subTest(address=address):
                assert_error_envelope(
                    self.upload(address=address),
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug="authz",
                )
        for field in ("username", "user"):
            with self.subTest(field=field):
                with make_image_file() as image_file:
                    response = self.client.post(
                        self.url, {"file": image_file, field: self.other.username}, format="multipart",
                    )
                assert_error_envelope(
                    response,
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug="authz",
                )
        assert self.stored_sizes(self.owner.username) == set()
        assert self.stored_sizes(self.other.username) == set()
        assert self.has_image(self.owner) is False
        assert self.has_image(self.other) is False

    def test_a_name_sent_with_the_image_does_not_move_where_it_is_stored(self):
        """The sizes are stored under the name the address carries, whatever the form carries."""
        self.login(self.owner)
        with make_image_file() as image_file:
            response = self.client.post(
                self.url, {"file": image_file, "username": self.other.username}, format="multipart",
            )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.stored_sizes(self.other.username) == set()
        assert self.has_image(self.owner) is True
        assert self.has_image(self.other) is False

    def test_an_anonymous_caller_is_unauthenticated(self):
        assert_error_envelope(
            self.upload(),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert self.stored_sizes(self.owner.username) == set()

    def test_a_caller_who_has_not_confirmed_their_email_address_may_still_set_an_image(self):
        self.owner.is_active = False
        self.owner.save()
        with make_image_file() as image_file:
            response = self.client.post(
                self.url, {"file": image_file}, format="multipart", **self.token_headers(self.owner),
            )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)

    def test_a_token_is_read_by_the_class_the_platform_declares(self):
        """
        Only the session class is replaced here, so a token is read by the platform's own.

        That class records every request it reads a token for, which is how an
        operator finds the addresses that authenticate the way the platform
        declares rather than by a list of their own.
        """
        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            with make_image_file() as image_file:
                response = self.client.post(
                    self.url, {"file": image_file}, format="multipart", **self.token_headers(self.owner),
                )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert [call.args for call in recorded.call_args_list] == [("using_default_auth_classes", True)]

    def test_a_body_in_a_media_type_this_endpoint_cannot_read_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.post(self.url, {"file": "not an image"}, format="json"),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )
        assert self.stored_sizes(self.owner.username) == set()

    def test_a_query_parameter_this_endpoint_does_not_read_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.upload(address=f"{self.url}?foo=1"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"foo": ["No such query parameter. Send any of: format."]}
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_the_address_is_not_read_with_a_get(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.get(self.url),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )


@skip_unless_lms
class ProfileImageRemovalTests(ProfileImageTestCase):
    """DELETE /api/user/v2/accounts/{username}/profile_image/"""

    def setUp(self):
        super().setUp()
        self.login(self.owner)
        assert self.upload().status_code == status.HTTP_204_NO_CONTENT

    def test_a_user_clears_the_image_on_their_own_account(self):
        response = self.client.delete(self.url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.data is None
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_clearing_an_account_that_carries_no_image_is_not_an_error(self):
        assert self.client.delete(self.url).status_code == status.HTTP_204_NO_CONTENT
        assert self.client.delete(self.url).status_code == status.HTTP_204_NO_CONTENT
        assert self.has_image(self.owner) is False

    def test_a_query_parameter_this_endpoint_does_not_read_is_refused(self):
        """A caller that guards a destructive request with a parameter is told it is not read."""
        envelope = assert_error_envelope(
            self.client.delete(f"{self.url}?confirm=no&foo=1"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "confirm": ["No such query parameter. Send any of: format."],
            "foo": ["No such query parameter. Send any of: format."],
        }
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_a_learner_may_not_clear_another_learners_image(self):
        self.client.logout()
        self.login(self.other)
        assert_error_envelope(
            self.client.delete(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_a_caller_named_for_the_address_in_case_alone_may_not_clear_the_image(self):
        """The address names one account, and a name that differs in case is another name."""
        self.client.logout()
        self.sign_in_as_a_case_variant_of(self.owner)
        assert_error_envelope(
            self.client.delete(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_a_staff_caller_may_not_clear_another_account_s_image_either(self):
        self.client.logout()
        self.login(self.staff)
        assert_error_envelope(
            self.client.delete(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_a_refused_removal_stays_refused_whatever_the_query_string_carries(self):
        """Who may clear this image is settled from the address, before the query string is read."""
        self.client.logout()
        self.login(self.other)
        for address in (
            f"{self.url}?username={self.other.username}",
            f"{self.url}?user={self.other.username}",
        ):
            with self.subTest(address=address):
                assert_error_envelope(
                    self.client.delete(address),
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug="authz",
                )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_an_anonymous_caller_is_unauthenticated(self):
        self.client.logout()
        assert_error_envelope(
            self.client.delete(self.url),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)

    def test_an_account_that_no_longer_exists_is_not_found(self):
        """The account is read when the image is cleared, and the refusal says so."""
        address = self.image_url(self.owner.username)
        self.owner.profile.delete()
        assert_error_envelope(
            self.client.delete(address),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_name_no_account_holds_is_refused_as_somebody_elses_image(self):
        """
        A name nobody holds is not the caller's own, so it is refused rather than reported missing.

        The refusal a caller meets at a name nobody holds is the one they meet
        at any other account's address, which is what the published refusals
        say happens here.
        """
        assert_error_envelope(
            self.client.delete(self.image_url("nobody-at-all")),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )


@skip_unless_lms
class ProfileImageAddressedNameTests(ProfileImageTestCase):
    """
    This address carries the name an account is stored under.

    The account collection reads ``me`` as the account of the caller; the
    addresses beneath an account read the name they carry as it stands, so a
    caller that sends ``me`` to this one is answered as a caller naming any
    other account is.
    """

    def setUp(self):
        super().setUp()
        self.login(self.owner)
        self.collection_name_url = self.image_url("me")

    def test_an_image_is_not_set_at_the_collections_own_name(self):
        assert_error_envelope(
            self.upload(address=self.collection_name_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_an_image_is_not_cleared_at_the_collections_own_name(self):
        assert self.upload().status_code == status.HTTP_204_NO_CONTENT
        assert_error_envelope(
            self.client.delete(self.collection_name_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.stored_sizes(self.owner.username) == self.all_sizes(self.owner.username)
        assert self.has_image(self.owner) is True

    def test_the_account_holding_that_name_is_served_at_it(self):
        """A user may register the name, and sets their own image where it is addressed."""
        holder = UserFactory.create(username="me", email="me@example.com", password=PASSWORD)
        self.addCleanup(self.remove_stored_images, holder.username)
        self.client.logout()
        self.login(holder)
        assert self.upload(address=self.collection_name_url).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored_sizes(holder.username) == self.all_sizes(holder.username)
        assert self.has_image(holder) is True


@skip_unless_lms
class ProfileImageParityTests(ProfileImageTestCase):
    """The same requests through the version 1 and the version 2 addresses."""

    def test_both_versions_store_the_same_sizes(self):
        self.login(self.owner)
        earlier = self.upload(address=earlier_address(self.owner.username))
        assert earlier.status_code == status.HTTP_204_NO_CONTENT
        earlier_sizes = self.stored_sizes(self.owner.username)
        assert self.has_image(self.owner) is True
        self.client.delete(earlier_address(self.owner.username))

        later = self.upload()
        assert later.status_code == earlier.status_code
        assert later.data == earlier.data
        assert self.stored_sizes(self.owner.username) == earlier_sizes
        assert self.has_image(self.owner) is True

    def test_both_versions_clear_the_image_the_same_way(self):
        self.login(self.owner)
        self.upload()
        earlier = self.client.delete(earlier_address(self.owner.username))
        assert earlier.status_code == status.HTTP_204_NO_CONTENT
        assert self.stored_sizes(self.owner.username) == set()

        self.upload()
        later = self.client.delete(self.url)
        assert later.status_code == earlier.status_code
        assert self.stored_sizes(self.owner.username) == set()
        assert self.has_image(self.owner) is False

    def test_both_versions_refuse_an_image_the_site_will_not_take(self):
        self.login(self.owner)
        earlier = self.client.post(
            earlier_address(self.owner.username), {"file": self.not_an_image()}, format="multipart",
        )
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert earlier.data == {
            "developer_message": "The file must be one of the following types: .jpeg, .jpg, .png, .gif.",
            "user_message": "The file must be one of the following types: .jpeg, .jpg, .png, .gif.",
        }

        later = self.client.post(self.url, {"file": self.not_an_image()}, format="multipart")
        assert later.status_code == earlier.status_code
        # The one declared difference: the refusal is the standard body, and
        # the message version 1 carried as ``user_message`` names the field.
        assert later.data["errors"] == {
            "file": ["The file must be one of the following types: .jpeg, .jpg, .png, .gif."],
        }

    def test_both_versions_refuse_a_body_carrying_no_image(self):
        self.login(self.owner)
        earlier = self.client.post(earlier_address(self.owner.username), {}, format="multipart")
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert earlier.data == {
            "developer_message": "No file provided for profile image",
            "user_message": "No file provided for profile image",
        }

        later = self.client.post(self.url, {}, format="multipart")
        assert later.status_code == earlier.status_code
        assert later.data["errors"] == {"file": ["No file was submitted."]}

    def test_both_versions_refuse_another_learners_image(self):
        self.login(self.other)
        earlier = self.upload(address=earlier_address(self.owner.username))
        assert earlier.status_code == status.HTTP_403_FORBIDDEN

        later = self.upload()
        assert later.status_code == earlier.status_code
        assert_error_envelope(later, expected_status=403, expected_type_slug="authz")
        assert self.stored_sizes(self.owner.username) == set()


@pytest.fixture(name="image_owner")
@skip_unless_lms
def image_owner_fixture(client, django_user_model):  # pylint: disable=unused-argument
    """Sign a learner in and hand back the addresses of their image in both versions."""
    owner = UserFactory.create(username="count-image-owner", password=PASSWORD)
    assert client.login(username=owner.username, password=PASSWORD)
    # The first authenticated request of a session populates caches the rest
    # of the session reuses; the counts below are the steady-state ones.
    client.delete(reverse("user_v2:account_profile_image", kwargs={"username": owner.username}))
    return owner


#: What setting and clearing an image cost, measured on the version 1 address
#: first and asserted on both. Neither version reads anything per stored size.
UPLOAD_QUERIES = 17
REMOVAL_QUERIES = 17


@pytest.mark.django_db
@skip_unless_lms
def test_setting_an_image_costs_what_the_earlier_address_costs(
    client, image_owner, django_assert_num_queries,
):
    storage = get_profile_image_storage()
    try:
        with make_image_file() as image_file, django_assert_num_queries(UPLOAD_QUERIES):
            earlier = client.post(
                f"/api/user/v1/accounts/{image_owner.username}/image", {"file": image_file},
            )
        assert earlier.status_code == status.HTTP_204_NO_CONTENT

        address = reverse("user_v2:account_profile_image", kwargs={"username": image_owner.username})
        with make_image_file() as image_file, django_assert_num_queries(UPLOAD_QUERIES):
            later = client.post(address, {"file": image_file})
        assert later.status_code == status.HTTP_204_NO_CONTENT
    finally:
        for name in get_profile_image_names(image_owner.username).values():
            storage.delete(name)


@pytest.mark.django_db
@skip_unless_lms
def test_clearing_an_image_costs_what_the_earlier_address_costs(
    client, image_owner, django_assert_num_queries,
):
    with django_assert_num_queries(REMOVAL_QUERIES):
        earlier = client.delete(f"/api/user/v1/accounts/{image_owner.username}/image")
    assert earlier.status_code == status.HTTP_204_NO_CONTENT

    address = reverse("user_v2:account_profile_image", kwargs={"username": image_owner.username})
    with django_assert_num_queries(REMOVAL_QUERIES):
        later = client.delete(address)
    assert later.status_code == status.HTTP_204_NO_CONTENT
