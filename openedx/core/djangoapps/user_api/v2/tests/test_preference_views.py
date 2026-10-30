"""Tests for the version 2 User API account preference addresses."""

import contextlib
import json
from unittest import mock
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util import model_utils
from common.djangoapps.util.model_utils import USER_FIELDS_CHANGED, USER_SETTINGS_CHANGED_EVENT_NAME
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.errors import (
    PreferenceUpdateError,
    PreferenceValidationError,
    UserAPIInternalError,
)
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2.serializers import (
    PREFERENCE_NAME_MAX_LENGTH,
    PREFERENCE_NAME_TOO_LONG,
    PREFERENCE_NAME_UNUSABLE,
)
from openedx.core.djangoapps.user_api.v2.views import DESCRIBE_ADDRESS_ACTION, AccountPreferenceViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_views import write_queries

PASSWORD = "Password1234"
MERGE_PATCH = "application/merge-patch+json"

#: The query parameters these addresses read, in the order their refusal lists them.
PREFERENCE_QUERY_PARAMS = "format"

#: The table the preferences of an account are stored in.
PREFERENCE_TABLE = "user_api_userpreference"

#: Where the classes the platform configures record that they read a request.
DEFAULT_AUTH_RECORDER = "openedx.core.djangolib.default_auth_classes.set_custom_attribute"

#: What the platform's own record of a request carries for a read of one of
#: these addresses: the query string it was made with and the form body it
#: carried, both empty.
REQUEST_RECORD_DATA = '{"GET": {}, "POST": {}}'

#: Where the language a user picked is carried between requests, and the name
#: the platform stores it under once it is carried.
LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME
LANGUAGE_PREFERENCE = "pref-lang"

#: Names a preference may be stored under that the version 1 member address
#: cannot carry: the language the platform itself stores, the names the profile
#: pages store a sharing choice under, and a name ending in a separator.
UNREACHABLE_KEYS = (LANGUAGE_PREFERENCE, "visibility.bio", "visibility.")

#: Names a preference may be stored under that no member address can carry,
#: because the name is read from one segment and a segment ends at a slash.
SLASHED_NAMES = ("a/b", "x/", "/x", "a//b")

#: Names that address another collection at version 1 and are ordinary
#: preference names here.
COLLECTION_NAMES = ("email_opt_in", "time_zones")

#: The longest name a preference can be stored under, and one character more.
LONGEST_NAME = "k" * PREFERENCE_NAME_MAX_LENGTH
TOO_LONG_NAME = "k" * (PREFERENCE_NAME_MAX_LENGTH + 1)

#: Names that differ in more than their letters, stored in an order none of the
#: orders they could come back in would put them in.
UNSORTED_NAMES = ("Zeta", "alpha", "Beta", "_under", "9nine")

#: Those names in the order they are answered in: by the code of each
#: character, which puts digits, capitals and the underscore ahead of lower
#: case. A sort that folded case would answer them in another order.
SORTED_NAMES = ["9nine", "Beta", "Zeta", "_under", "alpha"]

#: Text carrying a character that stands for half of a pair, which has no
#: encoding of its own: it can be neither stored nor carried back in an answer.
UNENCODABLE_TEXT = "x\ud800y"

#: How such a character is reported, by the code point that was sent.
UNENCODABLE_REFUSAL = "Surrogate characters are not allowed: U+D800."


def earlier_document_address(username):
    """Return the version 1 address of every preference of one account."""
    return f"/api/user/v1/preferences/{username}"


def earlier_member_address(username, key):
    """Return the version 1 address of one preference of one account."""
    return f"/api/user/v1/preferences/{username}/{key}"


@contextlib.contextmanager
def requests_served_outside_a_transaction():
    """Serve requests the way a database that commits each statement on its own would."""
    connected_as = connection.settings_dict
    each_request_in_a_transaction = connected_as["ATOMIC_REQUESTS"]
    connected_as["ATOMIC_REQUESTS"] = False
    try:
        yield
    finally:
        connected_as["ATOMIC_REQUESTS"] = each_request_in_a_transaction


class PreferenceApiTestCase(APITestCase):
    """Fixtures shared by the version 2 account preference tests."""

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(
            username="pref-owner", email="pref-owner@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="pref-other", email="pref-other@example.com", password=PASSWORD,
        )
        self.staff = UserFactory.create(
            username="pref-staff", email="pref-staff@example.com", password=PASSWORD, is_staff=True,
        )
        self.prefer("time_zone", "Asia/Tokyo")
        self.document_url = self.document_address(self.owner.username)
        self.member_url = self.member_address(self.owner.username, "time_zone")

    def document_address(self, username):
        """Return the address of every preference of one account."""
        return reverse("user_v2:account_preference_list", kwargs={"username": username})

    def member_address(self, username, key):
        """Return the address of one preference of one account."""
        return reverse(
            "user_v2:account_preference_detail",
            kwargs={"username": username, "key": key},
        )

    def login(self, user):
        """Sign the given user in through the session these views accept."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def token_headers(self, user):
        """Return the request headers that carry a token identifying the given user."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def prefer(self, key, value, user=None):
        """Store one preference for the given account, the owner's by default."""
        return UserPreferenceFactory.create(user=user or self.owner, key=key, value=value)

    def stored(self, user=None):
        """Return the preferences an account holds, as the names and values stored."""
        return {
            preference.key: preference.value
            for preference in UserPreference.objects.filter(user=user or self.owner)
        }

    def sign_in_as_a_case_variant_of(self, user):
        """
        Sign in a caller whose own name is the given user's, in upper case.

        The variant is set in memory only, since a site that stores usernames
        case-insensitively has no room for both names at once.
        """
        caller = UserFactory.create(
            username="pref-variant", email="pref-variant@example.com", password=PASSWORD,
        )
        caller.username = user.username.upper()
        self.client.force_authenticate(user=caller)
        return caller

    def patch_document(self, address, body, content_type=MERGE_PATCH, client=None, **kwargs):
        """Send a merge patch of the preference document."""
        return (client or self.client).patch(
            address, json.dumps(body), content_type=content_type, **kwargs,
        )

    def put_value(self, address, body, client=None, **kwargs):
        """Send the value one preference is to hold."""
        return (client or self.client).put(
            address, json.dumps(body), content_type="application/json", **kwargs,
        )

    def read_requests(self, username, client=None, **headers):
        """Return the requests that read the preferences of one account, by name."""
        sender = client or self.client
        return {
            "document": lambda: sender.get(self.document_address(username), **headers),
            "member": lambda: sender.get(
                self.member_address(username, "time_zone"), **headers,
            ),
        }

    def write_requests(self, username, client=None, **headers):
        """Return the requests that change the preferences of one account, by name."""
        sender = client or self.client
        return {
            "document": lambda: self.patch_document(
                self.document_address(username),
                {"time_zone": "Europe/Paris"},
                client=sender,
                **headers,
            ),
            "member": lambda: self.put_value(
                self.member_address(username, "time_zone"),
                {"value": "Europe/Paris"},
                client=sender,
                **headers,
            ),
            "member removal": lambda: sender.delete(
                self.member_address(username, "time_zone"), **headers,
            ),
        }

    def assert_all_refused(self, requests, expected_status, expected_type_slug):
        """
        Assert every request named is refused the same way.

        Each request is named in the failure as well as in the subtest it is
        run under, because a subtest name reaches the report only where
        something reads it, and the name of the one refusal that stopped
        happening is the first thing a reader of the failure needs.
        """
        for case, send in dict(requests).items():
            with self.subTest(case=case):
                refused = send()
                assert refused.status_code == expected_status, (case, refused.content)
                assert_error_envelope(
                    refused,
                    expected_status=expected_status,
                    expected_type_slug=expected_type_slug,
                )


@skip_unless_lms
class PreferenceDocumentTests(PreferenceApiTestCase):
    """GET and PATCH /api/user/v2/accounts/{username}/preferences/"""

    def test_an_owner_reads_every_preference_the_account_stores(self):
        self.prefer("account_privacy", "private")
        self.login(self.owner)
        response = self.client.get(self.document_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"account_privacy": "private", "time_zone": "Asia/Tokyo"}

    def test_an_account_that_stores_nothing_is_answered_with_an_empty_document(self):
        self.login(self.other)
        response = self.client.get(self.document_address(self.other.username))
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {}

    def test_the_names_come_back_in_one_order_whatever_order_they_were_stored_in(self):
        for key in ("zoneinfo", "account_privacy", LANGUAGE_PREFERENCE, "visibility.bio"):
            self.prefer(key, "stored")
        self.login(self.owner)
        names = list(self.client.get(self.document_url).json())
        assert names == ["account_privacy", LANGUAGE_PREFERENCE, "time_zone", "visibility.bio", "zoneinfo"]

    def test_the_order_the_names_come_back_in_is_the_code_of_each_character(self):
        """Which is what the document says, and is not the order a reader of English expects."""
        for key in UNSORTED_NAMES:
            self.prefer(key, "stored")
        self.login(self.owner)
        assert list(self.client.get(self.document_url).json()) == [*SORTED_NAMES, "time_zone"]

    def test_a_staff_caller_reads_another_accounts_preferences(self):
        self.login(self.staff)
        response = self.client.get(self.document_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"time_zone": "Asia/Tokyo"}

    def test_an_owner_changes_the_names_the_body_carries_and_leaves_the_rest(self):
        self.prefer("account_privacy", "private")
        self.login(self.owner)
        response = self.patch_document(self.document_url, {"time_zone": "Europe/Paris"})
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.content == b""
        assert self.stored() == {"account_privacy": "private", "time_zone": "Europe/Paris"}

    def test_a_name_the_account_does_not_store_is_added(self):
        self.login(self.owner)
        assert self.patch_document(
            self.document_url, {"account_privacy": "private"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"account_privacy": "private", "time_zone": "Asia/Tokyo"}

    def test_a_null_removes_the_name_from_the_account_that_was_addressed(self):
        self.prefer("time_zone", "Europe/Paris", user=self.other)
        self.login(self.owner)
        assert self.patch_document(
            self.document_url, {"time_zone": None},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {}
        assert self.stored(self.other) == {"time_zone": "Europe/Paris"}

    def test_the_document_read_back_and_sent_again_changes_nothing(self):
        self.prefer("account_privacy", "private")
        self.login(self.owner)
        document = self.client.get(self.document_url).json()
        assert self.patch_document(
            self.document_url, document,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.client.get(self.document_url).json() == document

    def test_the_names_the_earlier_member_address_cannot_carry_are_changed_here(self):
        self.login(self.owner)
        changes = {key: "stored" for key in UNREACHABLE_KEYS}
        assert self.patch_document(
            self.document_url, changes,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.client.get(self.document_url).json() == {
            "time_zone": "Asia/Tokyo", **changes,
        }

    def test_a_value_that_is_not_text_is_refused_under_the_name_that_carried_it(self):
        """The earlier address spelled a number out and stored it."""
        self.login(self.owner)
        response = self.patch_document(self.document_url, {"time_zone": 5, "account_privacy": "private"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "time_zone": ["Send a value as text, or null to remove the preference."],
        }
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_what_a_value_of_spaces_alone_means_is_settled_where_it_is_stored(self):
        """A value arrives as text; trimming it and refusing an empty one are the service's."""
        self.login(self.owner)
        assert self.patch_document(
            self.document_url, {"notes": "  draft  "},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"notes": "draft", "time_zone": "Asia/Tokyo"}
        envelope = assert_error_envelope(
            self.patch_document(self.document_url, {"other": "   "}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "other": ["Preference 'other' cannot be set to an empty value."],
        }
        assert self.stored() == {"notes": "draft", "time_zone": "Asia/Tokyo"}

    def test_an_empty_value_is_refused_under_the_name_that_carried_it(self):
        self.login(self.owner)
        response = self.patch_document(self.document_url, {"account_privacy": ""})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "account_privacy": ["Preference 'account_privacy' cannot be set to an empty value."],
        }
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_nothing_is_stored_unless_every_name_in_the_body_can_be(self):
        self.login(self.owner)
        response = self.patch_document(
            self.document_url, {"account_privacy": "private", "time_zone": "Asia/Africa"},
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "time_zone": ["Value 'Asia/Africa' is not a valid time zone selection."],
        }
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_name_as_long_as_the_column_holds_is_stored(self):
        self.login(self.owner)
        assert self.patch_document(
            self.document_url, {LONGEST_NAME: "stored"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"time_zone": "Asia/Tokyo", LONGEST_NAME: "stored"}

    def test_a_name_longer_than_the_column_holds_is_refused_under_itself(self):
        self.login(self.owner)
        response = self.patch_document(self.document_url, {TOO_LONG_NAME: "stored"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {TOO_LONG_NAME: [PREFERENCE_NAME_TOO_LONG]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_value_that_cannot_be_encoded_is_refused_under_the_name_that_carried_it(self):
        self.login(self.owner)
        response = self.patch_document(self.document_url, {"account_privacy": UNENCODABLE_TEXT})
        assert response["Content-Type"] == "application/json"
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"account_privacy": [UNENCODABLE_REFUSAL]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_name_that_cannot_be_encoded_is_refused_against_the_body(self):
        """The answer could not carry the name back, so it names the body instead of the member."""
        self.login(self.owner)
        for body in (
            {"account_privacy\ud800": "private"},
            {"\ud800": "private"},
            {"account_privacy\ud800": 5},
            {"account_privacy\ud800": None},
        ):
            response = self.patch_document(self.document_url, body)
            assert response["Content-Type"] == "application/json"
            envelope = assert_error_envelope(
                response,
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {"non_field_errors": [UNENCODABLE_REFUSAL]}, body
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_body_that_is_not_an_object_is_refused(self):
        self.login(self.owner)
        for body in ([], "time_zone", 5):
            envelope = assert_error_envelope(
                self.patch_document(self.document_url, body),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {
                "non_field_errors": ["Send the preferences to change as an object."],
            }, body

    def test_a_body_that_names_no_preference_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.patch_document(self.document_url, {}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "non_field_errors": ["Name at least one preference to change."],
        }

    def test_a_body_that_is_not_well_formed_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.patch(self.document_url, "{not json", content_type=MERGE_PATCH),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="malformed-body",
        )

    def test_a_change_sent_in_another_media_type_is_refused(self):
        """The document is changed with a merge patch, in which a null removes a name."""
        self.login(self.owner)
        for content_type in ("application/json", "application/x-www-form-urlencoded"):
            assert_error_envelope(
                self.patch_document(
                    self.document_url, {"time_zone": "Europe/Paris"}, content_type=content_type,
                ),
                expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                expected_type_slug="unsupported-media-type",
            )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_the_document_is_not_written_whole_or_added_to(self):
        self.login(self.owner)
        for method in ("put", "post", "delete"):
            response = getattr(self.client, method)(
                self.document_url, json.dumps({}), content_type="application/json",
            )
            assert_error_envelope(
                response,
                expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                expected_type_slug="method-not-allowed",
            )


@skip_unless_lms
class PreferenceMemberTests(PreferenceApiTestCase):
    """GET, PUT and DELETE /api/user/v2/accounts/{username}/preferences/{key}/"""

    def test_an_owner_reads_one_preference_as_its_name_and_its_value(self):
        self.login(self.owner)
        response = self.client.get(self.member_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"key": "time_zone", "value": "Asia/Tokyo"}

    def test_a_staff_caller_reads_one_preference_of_another_account(self):
        self.login(self.staff)
        assert self.client.get(self.member_url).json() == {
            "key": "time_zone", "value": "Asia/Tokyo",
        }

    def test_a_name_the_account_stores_no_value_under_is_absent(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.get(self.member_address(self.owner.username, "account_privacy")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_every_name_one_segment_can_carry_is_readable_and_writable(self):
        self.login(self.owner)
        for key in UNREACHABLE_KEYS + COLLECTION_NAMES:
            address = self.member_address(self.owner.username, key)
            assert self.put_value(address, {"value": "stored"}).status_code == (
                status.HTTP_204_NO_CONTENT
            ), key
            assert self.client.get(address).json() == {"key": key, "value": "stored"}, key
            assert self.client.delete(address).status_code == status.HTTP_204_NO_CONTENT, key
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_name_as_long_as_the_column_holds_is_given_a_value(self):
        self.login(self.owner)
        assert self.put_value(
            self.member_address(self.owner.username, LONGEST_NAME), {"value": "stored"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"time_zone": "Asia/Tokyo", LONGEST_NAME: "stored"}

    def test_a_longer_name_is_refused_under_the_part_of_the_address_that_carried_it(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.put_value(
                self.member_address(self.owner.username, TOO_LONG_NAME), {"value": "stored"},
            ),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"key": [PREFERENCE_NAME_TOO_LONG]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_name_nothing_can_be_stored_under_is_refused_under_that_part_too(self):
        """The fault is in the address, so changing the value sent would not mend it."""
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.put_value(
                self.member_address(self.owner.username, "!!!"), {"value": "stored"},
            ),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"key": [PREFERENCE_NAME_UNUSABLE]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_the_names_this_address_refuses_are_the_ones_the_store_refuses(self):
        """
        Which names are held is the store's to decide, so each is offered to it as well.

        A name is checked before the value is read, against what the store
        holds rather than against a list kept here, so a change to what it
        holds shows up as this address keeping a name the store would refuse or
        refusing one it would keep.
        """
        self.login(self.owner)
        for name in ("a!!!", "!!!", "...", "9", "visibility.bio", "pref-lang"):
            UserPreference.objects.filter(user=self.owner, key=name).delete()
            try:
                set_user_preference(self.owner, name, "stored")
            except PreferenceValidationError:
                the_store_refuses_it = True
            else:
                the_store_refuses_it = False
            UserPreference.objects.filter(user=self.owner, key=name).delete()
            answered = self.put_value(
                self.member_address(self.owner.username, name), {"value": "stored"},
            )
            assert (answered.status_code == status.HTTP_400_BAD_REQUEST) == the_store_refuses_it, (
                f"{name}: answered {answered.status_code}"
            )
            assert (name in self.stored()) == (not the_store_refuses_it), name

    def test_a_name_no_value_can_be_stored_under_is_read_and_removed_as_absent(self):
        """Nothing is stored under a name that long, so both report it missing."""
        self.login(self.owner)
        address = self.member_address(self.owner.username, TOO_LONG_NAME)
        for response in (self.client.get(address), self.client.delete(address)):
            assert_error_envelope(
                response,
                expected_status=status.HTTP_404_NOT_FOUND,
                expected_type_slug="not-found",
            )

    def test_a_value_that_cannot_be_encoded_is_refused_under_the_property_that_carried_it(self):
        self.login(self.owner)
        response = self.put_value(self.member_url, {"value": UNENCODABLE_TEXT})
        assert response["Content-Type"] == "application/json"
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"value": [UNENCODABLE_REFUSAL]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_name_carrying_a_slash_is_stored_read_and_removed_through_the_document(self):
        """
        A name is read from one segment, which ends at the slash, so no member address serves it.

        Encoding the slash does not reach one either: an address is read in its
        decoded form, which puts the slash back. The name the document stores
        is therefore left as it was by every method the member address serves,
        and the document is the only place it is read and removed.
        """
        self.login(self.owner)
        for name in SLASHED_NAMES:
            with self.subTest(name=name):
                held = {name: "stored", "time_zone": "Asia/Tokyo"}
                assert self.patch_document(
                    self.document_url, {name: "stored"},
                ).status_code == status.HTTP_204_NO_CONTENT
                assert self.stored() == held
                assert self.client.get(self.document_url).json() == held
                for spelling in (name, quote(name, safe="")):
                    address = f"{self.document_url}{spelling}/"
                    attempts = (
                        ("get", self.client.get(address)),
                        ("put", self.put_value(address, {"value": "other"})),
                        ("delete", self.client.delete(address)),
                    )
                    for method, response in attempts:
                        assert response["Content-Type"] == "application/json", (spelling, method)
                        assert_error_envelope(
                            response,
                            expected_status=status.HTTP_404_NOT_FOUND,
                            expected_type_slug="not-found",
                        )
                assert self.stored() == held
                assert self.patch_document(
                    self.document_url, {name: None},
                ).status_code == status.HTTP_204_NO_CONTENT
                assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_value_is_stored_whether_or_not_the_name_already_held_one(self):
        self.login(self.owner)
        new = self.member_address(self.owner.username, "account_privacy")
        assert self.put_value(new, {"value": "private"}).status_code == status.HTTP_204_NO_CONTENT
        assert self.put_value(
            self.member_url, {"value": "Europe/Paris"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"account_privacy": "private", "time_zone": "Europe/Paris"}

    def test_a_value_that_is_not_text_is_refused_under_the_property_that_carried_it(self):
        """The earlier address spelled a number out and stored it."""
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.put_value(self.member_url, {"value": 5}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"value": ["Not a valid string."]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_an_empty_value_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.put_value(self.member_url, {"value": ""}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"value": ["This field may not be blank."]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_body_carrying_no_value_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.put_value(self.member_url, {}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"value": ["This field is required."]}

    def test_a_value_the_preference_cannot_hold_is_refused_with_the_reason(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.put_value(self.member_url, {"value": "Asia/Africa"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "value": ["Value 'Asia/Africa' is not a valid time zone selection."],
        }
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_time_zone_the_zone_database_carries_is_stored(self):
        """Which zones there are is decided by the database of them, not by a list kept here."""
        self.login(self.owner)
        assert self.put_value(
            self.member_url, {"value": "America/Argentina/Buenos_Aires"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"time_zone": "America/Argentina/Buenos_Aires"}

    def test_a_preference_is_removed(self):
        self.login(self.owner)
        assert self.client.delete(self.member_url).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {}

    def test_removing_a_name_the_account_stores_no_value_under_reports_it_absent(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.delete(self.member_address(self.owner.username, "account_privacy")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_one_preference_is_not_merge_patched(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.patch(
                self.member_url, json.dumps({"value": "Europe/Paris"}), content_type=MERGE_PATCH,
            ),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_value_sent_in_a_media_type_this_address_does_not_read_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.put(
                self.member_url, json.dumps({"value": "Europe/Paris"}), content_type=MERGE_PATCH,
            ),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )

    def test_a_value_submitted_as_a_form_is_read(self):
        """These addresses read the media types the platform reads everywhere."""
        self.login(self.owner)
        assert self.client.put(
            self.member_url, "value=Europe/Paris",
            content_type="application/x-www-form-urlencoded",
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"time_zone": "Europe/Paris"}


@skip_unless_lms
class PreferenceAccessTests(PreferenceApiTestCase):
    """Who may read and who may change the preferences of the account an address names."""

    def test_an_anonymous_caller_is_refused_with_the_standard_body(self):
        self.assert_all_refused(
            {**self.read_requests(self.owner.username), **self.write_requests(self.owner.username)},
            status.HTTP_401_UNAUTHORIZED,
            "authn",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_learner_may_not_read_another_accounts_preferences(self):
        self.login(self.other)
        self.assert_all_refused(
            self.read_requests(self.owner.username), status.HTTP_403_FORBIDDEN, "authz",
        )

    def test_a_learner_may_not_change_another_accounts_preferences(self):
        self.login(self.other)
        self.assert_all_refused(
            self.write_requests(self.owner.username), status.HTTP_403_FORBIDDEN, "authz",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_staff_caller_reads_but_may_not_change_another_accounts_preferences(self):
        self.login(self.staff)
        for case, send in self.read_requests(self.owner.username).items():
            assert send().status_code == status.HTTP_200_OK, case
        self.assert_all_refused(
            self.write_requests(self.owner.username), status.HTTP_403_FORBIDDEN, "authz",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_staff_caller_reads_and_changes_their_own_preferences(self):
        self.prefer("time_zone", "Europe/Paris", user=self.staff)
        self.login(self.staff)
        own = self.staff.username
        for case, send in self.read_requests(own).items():
            assert send().status_code == status.HTTP_200_OK, case
        assert self.write_requests(own)["document"]().status_code == status.HTTP_204_NO_CONTENT
        assert self.write_requests(own)["member"]().status_code == status.HTTP_204_NO_CONTENT
        assert self.write_requests(own)["member removal"]().status_code == (
            status.HTTP_204_NO_CONTENT
        )

    def test_a_token_held_by_a_disabled_staff_account_may_not_read_another_accounts_preferences(self):
        """A privilege does not survive the deactivation of the account that held it."""
        headers = self.token_headers(self.staff)
        assert self.client.get(self.document_url, **headers).status_code == status.HTTP_200_OK
        assert self.client.get(self.member_url, **headers).status_code == status.HTTP_200_OK
        self.staff.is_active = False
        self.staff.save()
        for address in (self.document_url, self.member_url):
            assert_error_envelope(
                self.client.get(address, **headers),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_a_disabled_account_still_reads_and_changes_its_own_preferences(self):
        """The staff half of the check is the one a disabled account loses."""
        self.owner.is_active = False
        self.owner.save()
        headers = self.token_headers(self.owner)
        assert self.client.get(self.document_url, **headers).json() == {"time_zone": "Asia/Tokyo"}
        assert self.patch_document(
            self.document_url, {"time_zone": "Europe/Paris"}, **headers,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {"time_zone": "Europe/Paris"}

    def test_a_caller_named_for_the_address_in_case_alone_is_refused(self):
        """Two names differing in case alone are two names, and only one of them is addressed."""
        self.sign_in_as_a_case_variant_of(self.owner)
        self.assert_all_refused(
            {
                **self.read_requests(self.owner.username),
                **self.write_requests(self.owner.username),
            },
            status.HTTP_403_FORBIDDEN,
            "authz",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_learner_naming_an_account_that_does_not_exist_is_refused_without_learning_that(self):
        self.login(self.other)
        self.assert_all_refused(
            {**self.read_requests("nobody"), **self.write_requests("nobody")},
            status.HTTP_403_FORBIDDEN,
            "authz",
        )

    def test_an_administrator_naming_an_account_that_does_not_exist_is_told_so(self):
        self.login(self.staff)
        self.assert_all_refused(
            self.read_requests("nobody"), status.HTTP_404_NOT_FOUND, "not-found",
        )

    def test_an_administrator_may_not_change_the_preferences_of_a_name_nobody_holds(self):
        """The refusal is the one every change to another account gets, missing name or not."""
        self.login(self.staff)
        self.assert_all_refused(
            self.write_requests("nobody"), status.HTTP_403_FORBIDDEN, "authz",
        )

    def test_an_account_with_an_unconfirmed_address_reads_and_changes_its_own_preferences(self):
        """The site sends such a user to the pages that read and write these preferences."""
        self.owner.is_active = False
        self.owner.save()
        self.login(self.owner)
        assert self.client.get(self.document_url).json() == {"time_zone": "Asia/Tokyo"}
        assert self.client.get(self.member_url).status_code == status.HTTP_200_OK
        assert self.patch_document(
            self.document_url, {"account_privacy": "private"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.put_value(
            self.member_url, {"value": "Europe/Paris"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.client.delete(self.member_url).status_code == status.HTTP_204_NO_CONTENT

    def test_a_session_without_the_token_that_goes_with_it_is_refused_every_change(self):
        """
        The session class still holds a change to the token the site hands out with it.

        A read is not held to it, because only a change can be provoked from
        another site. The pages that keep a user's settings reach these
        addresses with the session they signed in with, so the refusal a
        browser meets here is the one pinned.
        """
        held_to_its_token = APIClient(enforce_csrf_checks=True)
        assert held_to_its_token.login(username=self.owner.username, password=PASSWORD)
        self.assert_all_refused(
            self.write_requests(self.owner.username, client=held_to_its_token),
            status.HTTP_403_FORBIDDEN,
            "authz",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}
        for case, send in self.read_requests(
            self.owner.username, client=held_to_its_token,
        ).items():
            assert send().status_code == status.HTTP_200_OK, case

    def test_an_administrator_asking_what_another_accounts_addresses_answer_is_told(self):
        """
        What an address does with an account is answered to whoever may read it.

        The description of a change the answer carries is built behind a check
        of its own, which is made with the method of that change, so a caller
        who may not make it is not told what it would carry.
        """
        self.login(self.staff)
        for address in (self.document_url, self.member_url):
            answer = self.client.options(address)
            assert answer.status_code == status.HTTP_200_OK, address
            assert answer.json()["name"] == "Account Preference", address
            assert "actions" not in answer.json(), address

    def test_the_addressed_account_is_told_what_a_change_at_their_own_address_carries(self):
        """
        The answer describes the body this address serves, which carries the name as well.

        What a change carries is named in the published document, which gives
        the value alone; the description assembled here is built from the body
        the address answers with, so it names both halves of a stored
        preference.
        """
        self.login(self.owner)
        answer = self.client.options(self.member_url)
        assert answer.status_code == status.HTTP_200_OK
        assert set(answer.json()["actions"]["PUT"]) == {"key", "value"}

    def test_a_learner_asking_what_another_accounts_addresses_answer_is_refused(self):
        self.login(self.other)
        for address in (self.document_url, self.member_url):
            assert_error_envelope(
                self.client.options(address),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_an_anonymous_caller_asking_what_these_addresses_answer_is_refused(self):
        for address in (self.document_url, self.member_url):
            assert_error_envelope(
                self.client.options(address),
                expected_status=status.HTTP_401_UNAUTHORIZED,
                expected_type_slug="authn",
            )

    def test_a_token_is_read_here_without_recording_the_address_as_one_on_the_defaults(self):
        """
        These addresses name their own classes, so they are not the ones the default pair serves.

        The pair the platform configures records every request it reads a
        token for, so that an operator can list the addresses it serves. These
        replace the session half of it and are therefore not among them, and
        nothing here is recorded as if they were. The address read afterwards
        names no classes of its own and is recorded, which is what shows the
        record is reachable from a request made this way at all.
        """
        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            answer = self.client.get(self.document_url, **self.token_headers(self.owner))
        assert answer.status_code == status.HTTP_200_OK
        assert answer.json() == {"time_zone": "Asia/Tokyo"}
        assert recorded.call_args_list == []

        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            on_the_default_pair = self.client.get(
                reverse("user_v2:user_preference_list"), **self.token_headers(self.staff),
            )
        assert on_the_default_pair.status_code == status.HTTP_200_OK
        assert [call.args for call in recorded.call_args_list] == [
            ("using_default_auth_classes", True),
        ]


@skip_unless_lms
class PreferenceRefusedWithNothingBehindThemTests(PreferenceApiTestCase):
    """
    Who these addresses refuse when nothing behind them refuses anybody.

    The preference service applies the same owner-or-administrator rule a
    second time, one frame below the handler, so an answer produced with it in
    place does not say which of the two refused a caller. Every request here is
    made with the service calls replaced by stand-ins that serve whoever asks,
    which leaves the classes guarding the actions as the only thing that can
    refuse: each refusal below is one of theirs, and a caller they started
    admitting would be answered here rather than refused.
    """

    #: What every preference the stand-ins serve is stored under, and its value.
    SERVED = {"time_zone": "Asia/Tokyo"}

    def setUp(self):
        super().setUp()
        self.disabled_staff = UserFactory.create(
            username="pref-disabled-staff",
            email="pref-disabled-staff@example.com",
            password=PASSWORD,
            is_staff=True,
            is_active=False,
        )

    @contextlib.contextmanager
    def nothing_refusing_behind_the_handler(self):
        """
        Serve every preference call, so only the classes guarding an action can refuse.

        The stand-ins are handed to the caller of this so a refusal can be
        held to having happened in front of them: a request they were never
        asked to serve was refused before anything behind the handler saw it.
        """
        stand_ins = {
            "get_user_preferences": mock.Mock(return_value=dict(self.SERVED)),
            "get_user_preference": mock.Mock(return_value=self.SERVED["time_zone"]),
            "update_user_preferences": mock.Mock(return_value=None),
            "set_user_preference": mock.Mock(return_value=None),
            "delete_user_preference": mock.Mock(return_value=True),
        }
        with mock.patch.multiple("openedx.core.djangoapps.user_api.v2.views", **stand_ins):
            yield stand_ins

    def assert_nothing_behind_the_handler_was_asked(self, stand_ins):
        """Assert no preference call was reached, so the refusal was the classes' own."""
        assert [name for name, stand_in in stand_ins.items() if stand_in.called] == []

    def test_the_addressed_account_is_served_at_every_address(self):
        """The stand-ins refuse nobody, which is what makes each refusal below the classes' own."""
        self.login(self.owner)
        with self.nothing_refusing_behind_the_handler() as stand_ins:
            reads = self.read_requests(self.owner.username)
            assert reads["document"]().json() == self.SERVED
            assert reads["member"]().json() == {"key": "time_zone", "value": "Asia/Tokyo"}
            for case, send in self.write_requests(self.owner.username).items():
                assert send().status_code == status.HTTP_204_NO_CONTENT, case
            assert sorted(name for name, stand_in in stand_ins.items() if stand_in.called) == [
                "delete_user_preference",
                "get_user_preference",
                "get_user_preferences",
                "set_user_preference",
                "update_user_preferences",
            ]

    def test_a_learner_reaching_another_account_is_refused_at_every_address(self):
        self.login(self.other)
        with self.nothing_refusing_behind_the_handler() as stand_ins:
            self.assert_all_refused(
                {
                    **self.read_requests(self.owner.username),
                    **self.write_requests(self.owner.username),
                },
                status.HTTP_403_FORBIDDEN,
                "authz",
            )
            self.assert_nothing_behind_the_handler_was_asked(stand_ins)
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_an_administrator_reads_another_account_and_is_refused_every_change_to_it(self):
        self.login(self.staff)
        with self.nothing_refusing_behind_the_handler() as stand_ins:
            for case, send in self.read_requests(self.owner.username).items():
                assert send().status_code == status.HTTP_200_OK, case
            self.assert_all_refused(
                self.write_requests(self.owner.username), status.HTTP_403_FORBIDDEN, "authz",
            )
            assert sorted(name for name, stand_in in stand_ins.items() if stand_in.called) == [
                "get_user_preference",
                "get_user_preferences",
            ]
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_caller_named_for_the_address_in_case_alone_is_refused_at_every_address(self):
        self.sign_in_as_a_case_variant_of(self.owner)
        with self.nothing_refusing_behind_the_handler() as stand_ins:
            self.assert_all_refused(
                {
                    **self.read_requests(self.owner.username),
                    **self.write_requests(self.owner.username),
                },
                status.HTTP_403_FORBIDDEN,
                "authz",
            )
            self.assert_nothing_behind_the_handler_was_asked(stand_ins)
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_an_administrator_whose_account_is_no_longer_active_is_refused_every_address(self):
        headers = self.token_headers(self.disabled_staff)
        with self.nothing_refusing_behind_the_handler() as stand_ins:
            self.assert_all_refused(
                {
                    **self.read_requests(self.owner.username, **headers),
                    **self.write_requests(self.owner.username, **headers),
                },
                status.HTTP_403_FORBIDDEN,
                "authz",
            )
            self.assert_nothing_behind_the_handler_was_asked(stand_ins)
        assert self.stored() == {"time_zone": "Asia/Tokyo"}


@skip_unless_lms
class PreferenceGuardTests(PreferenceApiTestCase):
    """
    Which callers the classes guarding each action admit, read from the view itself.

    The preference service refuses the same callers a second time, from behind
    these classes, so a request refused at either point is refused the same
    way. What each action is declared to admit is therefore asserted here, on
    the declarations themselves, as well as through the answers these
    addresses give when nothing behind them refuses anybody.
    """

    def setUp(self):
        super().setUp()
        self.disabled_staff = UserFactory.create(
            username="pref-disabled-staff",
            email="pref-disabled-staff@example.com",
            password=PASSWORD,
            is_staff=True,
            is_active=False,
        )

    def routed_actions(self):
        """Return every action the two preference addresses route a method to."""
        routed = set()
        for address in (self.document_url, self.member_url):
            routed.update(
                action
                for method, action in resolve(address).func.actions.items()
                if method != "head"
            )
        return routed

    def admits(self, action, caller, username, method="get"):
        """Return whether every class guarding an action admits one caller at one address."""
        request = getattr(APIRequestFactory(), method)("/")
        request.user = caller
        view = AccountPreferenceViewSet()
        view.action = action
        view.kwargs = {"username": username}
        view.request = request
        return all(
            permission.has_permission(request, view) for permission in view.get_permissions()
        )

    def test_the_reads_admit_the_addressed_account_and_an_active_administrator(self):
        for action in ("retrieve_all", "retrieve"):
            assert self.admits(action, self.owner, self.owner.username), action
            assert self.admits(action, self.staff, self.owner.username), action
            assert not self.admits(action, self.other, self.owner.username), action
            assert not self.admits(action, self.disabled_staff, self.owner.username), action
            assert not self.admits(action, AnonymousUser(), self.owner.username), action

    def test_a_change_admits_the_addressed_account_and_nobody_else(self):
        for action in ("partial_update_all", "update", "destroy"):
            assert self.admits(action, self.owner, self.owner.username), action
            assert not self.admits(action, self.staff, self.owner.username), action
            assert not self.admits(action, self.other, self.owner.username), action
            assert not self.admits(action, self.disabled_staff, self.owner.username), action
            assert not self.admits(action, AnonymousUser(), self.owner.username), action

    def test_the_address_is_the_only_place_a_name_is_read_from(self):
        """A name differing in case alone is a different name, whatever the action."""
        for action in self.routed_actions():
            assert not self.admits(action, self.owner, self.owner.username.upper()), action
            assert not self.admits(action, self.owner, self.other.username), action
            assert not self.admits(action, self.owner, "nobody"), action

    def test_the_actions_an_administrator_may_call_against_another_account_are_the_two_reads(self):
        """
        Read from the actions the addresses route, so a new one is not admitted unnoticed.

        The actions come from the mounts rather than from a list of their own,
        which is what makes this an answer about who is admitted rather than a
        restatement of the names the view holds them under.
        """
        admitted = {
            action
            for action in self.routed_actions()
            if self.admits(action, self.staff, self.owner.username)
        }
        assert admitted == {"retrieve_all", "retrieve"}
        assert self.routed_actions() - admitted == {"partial_update_all", "update", "destroy"}

    def test_asking_what_an_address_answers_is_guarded_as_a_read_of_it(self):
        """
        The action recorded for that question is named by no mount, so it is asserted apart.

        The same action is recorded again while the description of a change is
        built, with the method of that change rather than the one the caller
        sent, and is guarded as the change itself.
        """
        for caller, admitted in (
            (self.owner, True),
            (self.staff, True),
            (self.other, False),
            (self.disabled_staff, False),
            (AnonymousUser(), False),
        ):
            assert self.admits(
                DESCRIBE_ADDRESS_ACTION, caller, self.owner.username, method="options",
            ) is admitted, caller
        assert self.admits(
            DESCRIBE_ADDRESS_ACTION, self.owner, self.owner.username, method="put",
        )
        assert not self.admits(
            DESCRIBE_ADDRESS_ACTION, self.staff, self.owner.username, method="put",
        )


@skip_unless_lms
class PreferenceAddressedByMeTests(PreferenceApiTestCase):
    """``me`` beneath an account is read as a stored name on these addresses."""

    def setUp(self):
        super().setUp()
        self.me_document = self.document_address("me")
        self.me_member = self.member_address("me", "time_zone")

    def changes(self):
        """Return each change these addresses serve, as what it changes and how it is sent."""
        return (
            ("the whole document", lambda: self.patch_document(
                self.me_document, {"time_zone": "Europe/Paris"},
            )),
            ("one preference", lambda: self.put_value(self.me_member, {"value": "Europe/Paris"})),
            ("the removal of one preference", lambda: self.client.delete(self.me_member)),
        )

    def reads(self):
        """Return each read these addresses serve, as what it reads and how it is sent."""
        return (
            ("the whole document", lambda: self.client.get(self.me_document)),
            ("one preference", lambda: self.client.get(self.me_member)),
        )

    def test_a_learner_addressing_me_is_refused(self):
        self.login(self.owner)
        self.assert_all_refused(self.reads(), status.HTTP_403_FORBIDDEN, "authz")

    def test_an_administrator_addressing_me_is_told_no_account_holds_that_name(self):
        self.login(self.staff)
        self.assert_all_refused(self.reads(), status.HTTP_404_NOT_FOUND, "not-found")

    def test_a_learner_changing_the_preferences_at_me_is_refused(self):
        self.login(self.owner)
        self.assert_all_refused(self.changes(), status.HTTP_403_FORBIDDEN, "authz")
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_an_administrator_changing_them_is_refused_rather_than_told_the_name_is_free(self):
        self.login(self.staff)
        self.assert_all_refused(self.changes(), status.HTTP_403_FORBIDDEN, "authz")
        assert self.stored() == {"time_zone": "Asia/Tokyo"}
        assert self.stored(self.staff) == {}

    def test_the_account_holding_that_name_is_served(self):
        held = UserFactory.create(username="me", email="me@example.com", password=PASSWORD)
        self.prefer("time_zone", "Europe/Paris", user=held)
        self.login(held)
        assert self.client.get(self.me_document).json() == {"time_zone": "Europe/Paris"}
        assert self.client.get(self.me_member).json() == {
            "key": "time_zone", "value": "Europe/Paris",
        }
        assert self.put_value(
            self.me_member, {"value": "Asia/Tokyo"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored(held) == {"time_zone": "Asia/Tokyo"}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}
        assert self.patch_document(
            self.me_document, {"time_zone": "America/Lima"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored(held) == {"time_zone": "America/Lima"}
        assert self.client.delete(self.me_member).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored(held) == {}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}


@skip_unless_lms
class PreferenceQueryParameterTests(PreferenceApiTestCase):
    """These addresses read no query parameter, and refuse the ones they are sent."""

    def test_a_parameter_none_of_these_addresses_reads_is_refused(self):
        self.login(self.owner)
        for address in (self.document_url, self.member_url):
            envelope = assert_error_envelope(
                self.client.get(address, {"foo": "1"}),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {
                "foo": [f"No such query parameter. Send any of: {PREFERENCE_QUERY_PARAMS}."],
            }, address

    def test_the_document_carries_no_pages(self):
        """It is one account's preferences, read and changed whole."""
        self.login(self.owner)
        for parameter in ("page", "page_size"):
            envelope = assert_error_envelope(
                self.client.get(self.document_url, {parameter: "1"}),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert list(envelope["errors"]) == [parameter], parameter

    def test_no_preference_address_shapes_what_it_answers_with(self):
        """A caller wanting one name has the address of it."""
        self.login(self.owner)
        for address in (self.document_url, self.member_url):
            for parameter in ("view", "fields"):
                assert_error_envelope(
                    self.client.get(address, {parameter: "minimal"}),
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )

    def test_a_change_carrying_a_parameter_it_does_not_read_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.patch_document(
                f"{self.document_url}?foo=1", {"time_zone": "Europe/Paris"},
            ),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert self.stored() == {"time_zone": "Asia/Tokyo"}


@skip_unless_lms
class PreferenceServiceFailureTests(PreferenceApiTestCase):
    """Work that was asked for correctly and failed anyway is not the caller's fault."""

    def test_a_change_that_could_not_be_carried_out_is_a_server_fault(self):
        """The earlier address answered a failed save as a bad request."""
        self.login(self.owner)
        failure = PreferenceUpdateError(
            developer_message="Save failed for user preference 'time_zone': disk full",
            user_message="Save failed.",
        )
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.update_user_preferences", side_effect=failure,
        ):
            response = self.patch_document(self.document_url, {"time_zone": "Europe/Paris"})
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert envelope["detail"] == "The request could not be carried out."
        assert "disk full" not in json.dumps(envelope)

    def test_a_change_the_database_refused_is_a_server_fault(self):
        """
        A failure the caller cannot correct stays a server fault, whatever the body carried.

        The failure here is one the service did not make anything of, which is
        answered by the standard handling rather than by what this endpoint
        makes of the failures the service reports.
        """
        self.login(self.owner)
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.update_user_preferences",
            side_effect=DatabaseError("the table is gone"),
        ):
            response = self.patch_document(self.document_url, {"time_zone": "Europe/Paris"})
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert "the table is gone" not in json.dumps(envelope)
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def send_a_change_whose_removal_fails(self):
        """Send a merge patch whose first name is stored and whose second cannot be removed."""
        with mock.patch(
            "openedx.core.djangoapps.user_api.models.UserPreference.delete",
            side_effect=DatabaseError("the row is locked"),
        ):
            return self.patch_document(
                self.document_url,
                {"account_privacy": "all_users", "time_zone": None, "pref-lang": "eo"},
            )

    def test_a_change_that_fails_part_way_through_stores_none_of_it(self):
        """A merge patch is one change: a name that cannot be changed leaves every other alone."""
        self.login(self.owner)
        self.prefer("account_privacy", "private")
        self.prefer("pref-lang", "en")
        envelope = assert_error_envelope(
            self.send_a_change_whose_removal_fails(),
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert "the row is locked" not in json.dumps(envelope)
        assert self.stored() == {
            "account_privacy": "private", "pref-lang": "en", "time_zone": "Asia/Tokyo",
        }

    def test_a_change_that_fails_stores_none_of_it_where_a_statement_stands_on_its_own(self):
        """
        The change is carried out as one piece by the endpoint itself.

        The platform serves every request inside a transaction, which would
        undo the names stored before the one that failed on its own, so the
        promise is held here to a database configured without one.
        """
        self.login(self.owner)
        self.prefer("account_privacy", "private")
        self.prefer("pref-lang", "en")
        with requests_served_outside_a_transaction():
            response = self.send_a_change_whose_removal_fails()
        assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert self.stored() == {
            "account_privacy": "private", "pref-lang": "en", "time_zone": "Asia/Tokyo",
        }

    def test_a_read_that_could_not_be_served_is_a_server_fault(self):
        self.login(self.owner)
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.get_user_preferences",
            side_effect=UserAPIInternalError("the preference table is gone"),
        ):
            response = self.client.get(self.document_url)
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert "preference table is gone" not in json.dumps(envelope)

    def test_a_removal_that_could_not_be_carried_out_is_a_server_fault(self):
        """
        The failure is provoked at the row, so it arrives as the service reports it.

        What the service makes of a failed removal is the thing this endpoint
        turns into an answer, so the failure is raised where the row is
        removed rather than handed to the handler ready-made.
        """
        self.login(self.owner)
        with mock.patch(
            "openedx.core.djangoapps.user_api.models.UserPreference.delete",
            side_effect=DatabaseError("the row is locked"),
        ):
            response = self.client.delete(self.member_url)
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert envelope["detail"] == "The request could not be carried out."
        assert "the row is locked" not in json.dumps(envelope)
        assert "Delete failed" not in json.dumps(envelope)
        assert self.stored() == {"time_zone": "Asia/Tokyo"}


class PreferenceReadWriteCaptureCase(PreferenceApiTestCase):
    """
    What a read of these addresses does, measured statement by statement.

    Three channels are captured, because a read that changes stored data need
    not do so itself: the statements it ran, the callbacks it deferred to the
    end of its transaction, and what it announced. An announcement is captured
    because the platform's own listeners act on one, and a change announced
    from a read reaches those listeners whether or not the read wrote
    anything: the field-change signal is captured wherever it is sent from,
    and the analytics channel it is sent alongside with it.
    """

    def setUp(self):
        super().setUp()
        self.deferred = []
        self.announced = []
        self.tracked = []

    def captured_writes(self, method, address, expect=status.HTTP_200_OK, **headers):
        """Return the statements a request made that would change stored data."""
        with CaptureQueriesContext(connection) as captured, \
                mock.patch.object(USER_FIELDS_CHANGED, "send") as announced, \
                mock.patch.object(model_utils.tracker, "emit") as tracked, \
                self.captureOnCommitCallbacks(execute=True) as callbacks:
            answer = getattr(self.client, method)(address, **headers)
        assert answer.status_code == expect, answer.content
        self.deferred = callbacks
        self.announced = [call.kwargs for call in announced.call_args_list]
        self.tracked = [self.emitted(call) for call in tracked.call_args_list]
        return write_queries(captured.captured_queries)

    @staticmethod
    def emitted(call):
        """Return the name and the payload of one analytics event, however it was passed."""
        passed = (*call.args, None, None)
        return call.kwargs.get("name", passed[0]), call.kwargs.get("data", passed[1])

    def targets(self, statements):
        """Return the table each statement writes to."""
        return [statement.split('"')[1] for statement in statements]

    def assert_only_the_request_itself_was_recorded(self, address):
        """Assert the one event the request emitted was the platform's record of the request."""
        assert self.tracked == [(address, REQUEST_RECORD_DATA)]

    def assert_nothing_was_changed_or_announced(self, statements, address):
        """Assert the request wrote nothing, deferred nothing and announced no change."""
        assert (statements, self.deferred, self.announced) == ([], [], [])
        self.assert_only_the_request_itself_was_recorded(address)

    def assert_the_language_change_was_announced(self, address, caller=None):
        """Assert the one change announced was the language row the middleware stored."""
        caller = caller or self.owner
        assert self.announced == [{
            "sender": None,
            "user": caller,
            "table": PREFERENCE_TABLE,
            "changed_fields": {LANGUAGE_PREFERENCE: (None, "eo")},
        }]
        assert self.tracked == [
            (address, REQUEST_RECORD_DATA),
            (
                USER_SETTINGS_CHANGED_EVENT_NAME,
                {
                    "old": None,
                    "new": "eo",
                    "truncated": [],
                    "setting": LANGUAGE_PREFERENCE,
                    "user_id": caller.id,
                    "table": PREFERENCE_TABLE,
                },
            ),
        ]


@skip_unless_lms
class PreferenceReadsWithoutTheLanguageCookieTests(PreferenceReadWriteCaptureCase):
    """A read of these addresses changes nothing of what they serve, and announces nothing."""

    def assert_the_read_changed_nothing(self, method, address, expect=status.HTTP_200_OK, **headers):
        """Assert a read of this address wrote nothing, deferred nothing and announced nothing."""
        self.assert_nothing_was_changed_or_announced(
            self.captured_writes(method, address, expect=expect, **headers), address,
        )

    def test_reading_the_document_writes_nothing(self):
        self.login(self.owner)
        self.assert_the_read_changed_nothing("get", self.document_url)

    def test_reading_one_preference_writes_nothing(self):
        self.login(self.owner)
        self.assert_the_read_changed_nothing("get", self.member_url)

    def test_a_staff_caller_reading_another_account_writes_nothing(self):
        self.login(self.staff)
        for address in (self.document_url, self.member_url):
            self.assert_the_read_changed_nothing("get", address)

    def test_a_read_presented_with_a_token_writes_nothing(self):
        for address in (self.document_url, self.member_url):
            self.assert_the_read_changed_nothing("get", address, **self.token_headers(self.owner))

    def test_heading_either_address_writes_nothing(self):
        self.login(self.owner)
        for address in (self.document_url, self.member_url):
            self.assert_the_read_changed_nothing("head", address)

    def test_the_earlier_addresses_write_nothing_either(self):
        self.login(self.owner)
        for address in (
            earlier_document_address(self.owner.username),
            earlier_member_address(self.owner.username, "time_zone"),
        ):
            self.assert_the_read_changed_nothing("get", address)

    def test_reading_a_name_the_account_stores_no_value_under_writes_nothing(self):
        """Being told nothing is stored under a name is no reason to store anything."""
        self.login(self.owner)
        self.assert_the_read_changed_nothing(
            "get",
            self.member_address(self.owner.username, "account_privacy"),
            expect=status.HTTP_404_NOT_FOUND,
        )

    def test_a_staff_caller_told_another_account_stores_no_such_name_writes_nothing(self):
        self.login(self.staff)
        self.assert_the_read_changed_nothing(
            "get",
            self.member_address(self.other.username, "account_privacy"),
            expect=status.HTTP_404_NOT_FOUND,
        )

    def test_a_read_of_an_account_nobody_holds_writes_nothing(self):
        """The other branch that answers nothing: no such account, rather than no such name."""
        self.login(self.staff)
        for address in (
            self.document_address("pref-nobody"),
            self.member_address("pref-nobody", "time_zone"),
        ):
            self.assert_the_read_changed_nothing(
                "get", address, expect=status.HTTP_404_NOT_FOUND,
            )

    def test_a_read_leaves_the_stored_preferences_as_they_were(self):
        self.login(self.owner)
        before = self.stored()
        self.client.get(self.document_url)
        self.client.get(self.member_url)
        assert self.stored() == before == {"time_zone": "Asia/Tokyo"}


@skip_unless_lms
class PreferenceReadsWithTheLanguageCookieTests(PreferenceReadWriteCaptureCase):
    """
    A request carrying the language cookie stores the language it carries.

    The platform keeps the language a user picked in a cookie and syncs it into
    that user's preferences from middleware, which runs before any view and on
    every method, so it is reached by a read of these addresses as it is by a
    read of any other. The capture below pins what it writes: the one row, by
    the table it is stored in, and nothing else. The same request to the
    address this version supersedes writes the same row, which is what shows
    the write belongs to the platform rather than to either version.
    """

    def setUp(self):
        super().setUp()
        self.client.cookies[LANGUAGE_COOKIE] = "eo"

    def language_rows(self):
        """Return the stored language of every account, by account."""
        return {
            preference.user.username: preference.value
            for preference in UserPreference.objects.filter(key=LANGUAGE_PREFERENCE)
        }

    def assert_only_the_language_was_stored(
        self, method, address, expect=status.HTTP_200_OK, **headers,
    ):
        """Assert the one write made, and the one change announced, was the caller's language."""
        statements = self.captured_writes(method, address, expect=expect, **headers)
        assert (self.targets(statements), self.deferred) == ([PREFERENCE_TABLE], []), statements
        assert self.language_rows() == {self.owner.username: "eo"}
        self.assert_the_language_change_was_announced(address)

    def test_reading_the_document_stores_the_language_the_cookie_carries(self):
        self.login(self.owner)
        assert self.language_rows() == {}
        self.assert_only_the_language_was_stored("get", self.document_url)

    def test_reading_one_preference_stores_it_too(self):
        self.login(self.owner)
        self.assert_only_the_language_was_stored("get", self.member_url)

    def test_the_address_this_version_supersedes_stores_the_same_row(self):
        self.login(self.owner)
        self.assert_only_the_language_was_stored(
            "get", earlier_document_address(self.owner.username),
        )

    def test_the_earlier_member_address_stores_the_same_row(self):
        self.login(self.owner)
        self.assert_only_the_language_was_stored(
            "get", earlier_member_address(self.owner.username, "time_zone"),
        )

    def test_heading_the_document_stores_it_as_well(self):
        """A write one frame below the handler is reached by every method, this one included."""
        self.login(self.owner)
        self.assert_only_the_language_was_stored("head", self.document_url)

    def test_a_read_presented_with_a_token_stores_it_as_well(self):
        self.assert_only_the_language_was_stored(
            "get", self.document_url, **self.token_headers(self.owner),
        )

    def test_reading_a_name_the_account_stores_no_value_under_stores_only_the_language(self):
        """The refusal writes nothing of its own: the row below it is still the only write."""
        self.login(self.owner)
        self.assert_only_the_language_was_stored(
            "get",
            self.member_address(self.owner.username, "account_privacy"),
            expect=status.HTTP_404_NOT_FOUND,
        )

    def test_the_row_is_written_once_and_not_once_a_request(self):
        """It is the language being synced, not the read recording anything."""
        self.login(self.owner)
        self.assert_only_the_language_was_stored("get", self.document_url)
        for address in (self.document_url, self.member_url):
            self.assert_nothing_was_changed_or_announced(
                self.captured_writes("get", address), address,
            )

    def test_the_language_a_staff_caller_carries_is_stored_against_their_own_account(self):
        """The row is the caller's, whichever account the address names."""
        self.login(self.staff)
        statements = self.captured_writes("get", self.document_url)
        assert self.targets(statements) == [PREFERENCE_TABLE], statements
        assert self.language_rows() == {self.staff.username: "eo"}
        self.assert_the_language_change_was_announced(self.document_url, self.staff)

    def test_removing_the_language_removes_the_row_the_middleware_had_just_stored(self):
        """
        The row below the handler is written first and removed by the handler itself.

        A client that removes the language it is carrying is told the account
        stores no value under that name, and none does: the write one frame
        below is not a second row and does not outlast the request that made
        it.
        """
        self.login(self.owner)
        statements = self.captured_writes(
            "delete",
            self.member_address(self.owner.username, LANGUAGE_PREFERENCE),
            expect=status.HTTP_204_NO_CONTENT,
        )
        assert self.targets(statements) == [PREFERENCE_TABLE, PREFERENCE_TABLE], statements
        assert self.language_rows() == {}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_the_document_removing_the_language_leaves_it_removed_as_well(self):
        self.login(self.owner)
        assert self.patch_document(
            self.document_url, {LANGUAGE_PREFERENCE: None},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.language_rows() == {}

    def test_removing_another_name_stores_the_language_the_cookie_carries(self):
        """The write one frame below is made whatever the method, a removal included."""
        self.login(self.owner)
        statements = self.captured_writes(
            "delete", self.member_url, expect=status.HTTP_204_NO_CONTENT,
        )
        assert self.targets(statements) == [PREFERENCE_TABLE, PREFERENCE_TABLE], statements
        assert self.language_rows() == {self.owner.username: "eo"}
        assert self.stored() == {LANGUAGE_PREFERENCE: "eo"}

    def test_a_name_stored_only_by_the_middleware_is_removed_rather_than_reported_absent(self):
        """
        The row is there to remove by the time the handler looks, so the removal is answered.

        Without the cookie the same request is answered with the refusal a
        name no value is stored under gets, which is what the two assertions
        below stand on either side of.
        """
        self.login(self.owner)
        address = self.member_address(self.owner.username, LANGUAGE_PREFERENCE)
        assert self.language_rows() == {}
        assert self.client.delete(address).status_code == status.HTTP_204_NO_CONTENT
        del self.client.cookies[LANGUAGE_COOKIE]
        assert self.client.delete(address).status_code == status.HTTP_404_NOT_FOUND

    def test_the_document_answered_carries_the_language_that_was_stored(self):
        self.login(self.owner)
        assert self.client.get(self.document_url).json() == {
            LANGUAGE_PREFERENCE: "eo", "time_zone": "Asia/Tokyo",
        }
