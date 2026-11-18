"""Behaviour parity between the version 1 and version 2 User API addresses."""

import json

import pytest
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ObjectDoesNotExist
from django.test.utils import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
API_KEY = "parity-test-api-key"
COURSE_KEY = CourseKey.from_string("course-v1:edX+DemoX+Demo")

#: Fields that legitimately differ per request and are ignored in the diff.
VOLATILE_FIELDS = {"instance", "next", "previous"}

#: Differences the plan declares intentional, per case. Each entry is a
#: (json path, reason) pair and the diff must contain all of them and nothing else.
INTENTIONAL_DIFFERENCES = {
    "user_list": [
        ("results[0].url", "the member address is keyed by username instead of database id"),
        ("results[1].url", "the member address is keyed by username instead of database id"),
    ],
    "user_detail": [
        ("url", "the member address is keyed by username instead of database id"),
    ],
    "preference_list": [
        (
            "results[0].user",
            "the embedded owner is a member of every row and is null unless the full preset is asked for",
        ),
        ("results[0].username", "the owner's username is carried in place of the embedded user object"),
        ("results[0].url", "the member address is keyed by username and preference key"),
        (
            "results[1].user",
            "the embedded owner is a member of every row and is null unless the full preset is asked for",
        ),
        ("results[1].username", "the owner's username is carried in place of the embedded user object"),
        ("results[1].url", "the member address is keyed by username and preference key"),
    ],
    "preference_detail": [
        ("user", "the embedded owner is a member of every row and is null unless the full preset is asked for"),
        ("username", "the owner's username is carried in place of the embedded user object"),
        ("url", "the member address is keyed by username and preference key"),
    ],
    "preference_key_filter": [
        ("results[0].url", "the member address is keyed by username instead of database id"),
    ],
    "forum_role_filter": [
        ("results[0].url", "the member address is keyed by username instead of database id"),
    ],
}


def normalize(obj, path=""):
    """Sort keys, drop volatile fields, and collect a {json path: value} map for diffing."""
    flat = {}
    if isinstance(obj, dict):
        for key in sorted(obj):
            if key in VOLATILE_FIELDS:
                continue
            flat.update(normalize(obj[key], f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            flat.update(normalize(item, f"{path}[{index}]"))
    else:
        flat[path] = obj
    return flat


def diff(legacy, new):
    """Return (every changed path, the changed paths no declared difference covers)."""
    left, right = normalize(legacy), normalize(new)
    return {key for key in set(left) | set(right) if left.get(key, "<absent>") != right.get(key, "<absent>")}


def unexpected(changed, intentional):
    """Return the changed paths that no declared intentional difference covers."""
    declared = {path for path, _reason in intentional}
    return {
        key for key in changed
        if not any(key == path or key.startswith(path + ".") or key.startswith(path + "[") for path in declared)
    }


@skip_unless_lms
@override_settings(EDX_API_KEY=API_KEY)
class ParityTests(APITestCase):
    """The same fixtures through the legacy and the conforming address."""

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory.create(username="parity-admin", password=PASSWORD)
        self.user = UserFactory.create(
            username="parity-user", email="parity-user@example.com", password=PASSWORD,
        )
        self.preference = UserPreferenceFactory.create(user=self.user, key="pref-lang", value="en")
        role = Role.objects.create(course_id=COURSE_KEY, name="Moderator")
        role.users.add(self.user)

    def legacy(self, address, params=None):
        """Call a version 1 address with the API key it is gated by."""
        return self.client.get(address, params or {}, HTTP_X_EDX_API_KEY=API_KEY)

    def conforming(self, address, params=None):
        """Call a version 2 address with the same API key."""
        return self.client.get(address, params or {}, HTTP_X_EDX_API_KEY=API_KEY)

    def assert_parity(self, legacy, new, case, expected_status=status.HTTP_200_OK):
        """
        Assert the two bodies differ only where the case declares they do.

        Rows are compared where each version put them, so a page whose rows
        move is a difference like any other and has to be declared.
        """
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_body = json.loads(legacy.content)
        new_body = json.loads(new.content)
        changed = diff(legacy_body, new_body)
        intentional = INTENTIONAL_DIFFERENCES[case]
        assert not unexpected(changed, intentional), (
            f"undeclared differences: {sorted(unexpected(changed, intentional))}\n"
            f"legacy={legacy_body}\nnew={new_body}"
        )
        for path, reason in intentional:
            assert any(
                key == path or key.startswith(path + ".") or key.startswith(path + "[")
                for key in changed
            ), f"declared difference did not occur: {path} ({reason})"

    def test_user_list_parity(self):
        legacy = self.legacy("/api/user/v1/users/", {"page_size": 100})
        new = self.conforming(reverse("user_v2:user_list"), {"page_size": 100})
        self.assert_parity(legacy, new, "user_list")

    def test_the_conforming_page_is_ordered_by_username_whatever_the_creation_order(self):
        """Version 1 applies no ordering, so only the conforming side can be pinned to a sequence."""
        for username in ("parity-zulu", "parity-alpha"):
            UserFactory.create(username=username, email=f"{username}@example.com", password=PASSWORD)

        new = self.conforming(reverse("user_v2:user_list"), {"page_size": 100})
        usernames = [row["username"] for row in json.loads(new.content)["results"]]
        assert usernames == ["parity-admin", "parity-alpha", "parity-user", "parity-zulu"]

        legacy = self.legacy("/api/user/v1/users/", {"page_size": 100})
        legacy_usernames = [row["username"] for row in json.loads(legacy.content)["results"]]
        assert sorted(legacy_usernames) == usernames

    def directory_addresses(self):
        """Return each directory address of version 1 beside the version 2 address replacing it."""
        return {
            "user_list": ("/api/user/v1/users/", reverse("user_v2:user_list")),
            "user_detail": (
                f"/api/user/v1/users/{self.user.id}/",
                reverse("user_v2:user_detail", kwargs={"username": "parity-user"}),
            ),
            "preference_list": ("/api/user/v1/user_prefs/", reverse("user_v2:user_preference_list")),
            "preference_detail": (
                f"/api/user/v1/user_prefs/{self.preference.id}/",
                reverse(
                    "user_v2:user_preference_detail",
                    kwargs={"username": "parity-user", "key": "pref-lang"},
                ),
            ),
        }

    def test_the_api_key_alone_admits_at_both_versions(self):
        """The header the version 1 addresses are read with is what admits a caller to version 2."""
        for name, (legacy_address, address) in self.directory_addresses().items():
            with self.subTest(address=name):
                legacy = self.legacy(legacy_address)
                assert legacy.status_code == status.HTTP_200_OK, legacy.content
                new = self.conforming(address)
                assert new.status_code == status.HTTP_200_OK, new.content

    def test_a_staff_account_without_the_key_is_refused_at_both_versions(self):
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        for name, (legacy_address, address) in self.directory_addresses().items():
            with self.subTest(address=name):
                legacy = self.client.get(legacy_address)
                assert legacy.status_code == status.HTTP_403_FORBIDDEN, legacy.content
                envelope = assert_error_envelope(
                    self.client.get(address), expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )
                assert envelope["detail"] == json.loads(legacy.content)["detail"]

    def test_a_key_other_than_the_sites_is_refused_at_both_versions(self):
        for name, (legacy_address, address) in self.directory_addresses().items():
            with self.subTest(address=name):
                legacy = self.client.get(legacy_address, HTTP_X_EDX_API_KEY=f"{API_KEY}-other")
                assert legacy.status_code == status.HTTP_401_UNAUTHORIZED, legacy.content
                assert_error_envelope(
                    self.client.get(address, HTTP_X_EDX_API_KEY=f"{API_KEY}-other"),
                    expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
                )

    def test_a_name_with_spaces_before_it_selects_nothing_in_either_version(self):
        """
        Both versions match a username exactly as it was sent.

        The directory addresses of version 1 take no username, so its account
        list, the one place it selects by name, is the reading compared here.
        """
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        exact = self.client.get("/api/user/v1/accounts", {"username": "parity-user"})
        padded = self.client.get("/api/user/v1/accounts", {"username": " parity-user"})
        self.client.logout()
        assert exact.status_code == status.HTTP_200_OK
        assert [account["username"] for account in json.loads(exact.content)] == ["parity-user"]
        assert padded.status_code == status.HTTP_404_NOT_FOUND

        for name in ("user_v2:user_list", "user_v2:user_preference_list"):
            with self.subTest(address=name):
                selected = self.conforming(reverse(name), {"username": "parity-user"})
                assert [row["username"] for row in json.loads(selected.content)["results"]] == ["parity-user"]
                padded = self.conforming(reverse(name), {"username": " parity-user"})
                assert padded.status_code == status.HTTP_200_OK, padded.content
                assert json.loads(padded.content)["results"] == []

    def test_user_detail_parity(self):
        legacy = self.legacy(f"/api/user/v1/users/{self.user.id}/")
        new = self.conforming(reverse("user_v2:user_detail", kwargs={"username": "parity-user"}))
        self.assert_parity(legacy, new, "user_detail")

    def test_preference_list_parity(self):
        """More than one row, so the comparison pairs rows rather than reading a single one."""
        UserPreferenceFactory.create(user=self.user, key="pref-time", value="UTC")
        legacy = self.legacy("/api/user/v1/user_prefs/", {"page_size": 100})
        new = self.conforming(reverse("user_v2:user_preference_list"), {"page_size": 100})
        assert len(json.loads(new.content)["results"]) == 2
        self.assert_parity(legacy, new, "preference_list")

    def test_the_conforming_preference_page_is_ordered_by_owner_and_key(self):
        """Version 1 applies no ordering, so only the conforming side can be pinned to a sequence."""
        UserPreferenceFactory.create(user=self.user, key="pref-time", value="UTC")
        UserPreferenceFactory.create(user=self.admin, key="pref-zone", value="UTC")

        new = self.conforming(reverse("user_v2:user_preference_list"), {"page_size": 100})
        rows = [(row["username"], row["key"]) for row in json.loads(new.content)["results"]]
        assert rows == [
            ("parity-admin", "pref-zone"),
            ("parity-user", "pref-lang"),
            ("parity-user", "pref-time"),
        ]

        legacy = self.legacy("/api/user/v1/user_prefs/", {"page_size": 100})
        legacy_rows = [
            (row["user"]["username"], row["key"]) for row in json.loads(legacy.content)["results"]
        ]
        assert legacy_rows == [
            ("parity-user", "pref-lang"),
            ("parity-user", "pref-time"),
            ("parity-admin", "pref-zone"),
        ]
        assert sorted(legacy_rows) == rows

    def test_preference_detail_parity(self):
        legacy = self.legacy(f"/api/user/v1/user_prefs/{self.preference.id}/")
        new = self.conforming(
            reverse(
                "user_v2:user_preference_detail",
                kwargs={"username": "parity-user", "key": "pref-lang"},
            ),
        )
        self.assert_parity(legacy, new, "preference_detail")

    def test_preference_key_filter_parity(self):
        legacy = self.legacy("/api/user/v1/preferences/pref-lang/users/", {"page_size": 100})
        new = self.conforming(
            reverse("user_v2:user_list"), {"preference_key": "pref-lang", "page_size": 100},
        )
        self.assert_parity(legacy, new, "preference_key_filter")

    def test_forum_role_filter_parity(self):
        legacy = self.legacy(
            "/api/user/v1/forum_roles/Moderator/users/",
            {"course_id": str(COURSE_KEY), "page_size": 100},
        )
        new = self.conforming(
            reverse("user_v2:user_list"),
            {"forum_role": "Moderator", "course_key": str(COURSE_KEY), "page_size": 100},
        )
        self.assert_parity(legacy, new, "forum_role_filter")

    def test_full_preset_reproduces_the_legacy_embedded_user(self):
        legacy = self.legacy(f"/api/user/v1/user_prefs/{self.preference.id}/")
        new = self.conforming(
            reverse(
                "user_v2:user_preference_detail",
                kwargs={"username": "parity-user", "key": "pref-lang"},
            ),
            {"view": "full"},
        )
        legacy_user = json.loads(legacy.content)["user"]
        new_user = json.loads(new.content)["user"]
        assert set(legacy_user) == set(new_user)
        for field in ("id", "email", "name", "username", "preferences"):
            assert legacy_user[field] == new_user[field], field
        assert legacy_user["url"].endswith(f"/user_api/v1/users/{self.user.id}/")
        assert new_user["url"].endswith("/api/user/v2/users/parity-user/")

    def test_a_user_without_a_profile_is_a_row_here_and_a_failure_at_version_one(self):
        """Declared difference: the version 1 entry cannot render a user who has no profile row."""
        profileless = User.objects.create_user("parity-profileless", "pp@example.com", PASSWORD)
        with pytest.raises(ObjectDoesNotExist):
            self.legacy(f"/api/user/v1/users/{profileless.id}/")

        new = self.conforming(
            reverse("user_v2:user_detail", kwargs={"username": "parity-profileless"}),
        )
        assert new.status_code == status.HTTP_200_OK
        body = json.loads(new.content)
        assert body["name"] is None
        assert body["username"] == "parity-profileless"

    def test_time_zone_rows_are_unchanged_inside_the_new_envelope(self):
        legacy = json.loads(self.legacy("/api/user/v1/preferences/time_zones/").content)
        new = json.loads(self.conforming(reverse("user_v2:time_zone_list"), {"page_size": 100}).content)
        assert isinstance(legacy, list)
        assert new["count"] == len(legacy)
        assert new["results"] == legacy[:100]

    def test_country_filtered_time_zones_are_unchanged(self):
        legacy = json.loads(
            self.legacy("/api/user/v1/preferences/time_zones/", {"country_code": "FR"}).content,
        )
        new = json.loads(
            self.conforming(reverse("user_v2:time_zone_list"), {"country_code": "FR"}).content,
        )
        assert new["results"] == legacy

    def test_unauthenticated_bodies_differ_as_declared(self):
        legacy = self.client.get("/api/user/v1/users/")
        assert legacy.status_code == status.HTTP_401_UNAUTHORIZED
        assert json.loads(legacy.content) == {
            "detail": "Authentication credentials were not provided.",
        }

        new = self.client.get(reverse("user_v2:user_list"))
        envelope = assert_error_envelope(
            new, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )
        assert envelope["title"] == "Authentication Required"
        assert envelope["detail"] == "Authentication credentials were not provided."
        assert envelope["instance"] == "/api/user/v2/users/"

    def test_forbidden_bodies_differ_as_declared(self):
        assert self.client.login(username=self.user.username, password=PASSWORD)
        legacy = self.client.get("/api/user/v1/users/")
        assert legacy.status_code == status.HTTP_403_FORBIDDEN
        assert json.loads(legacy.content) == {
            "detail": "You do not have permission to perform this action.",
        }

        new = self.client.get(reverse("user_v2:user_list"))
        envelope = assert_error_envelope(
            new, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )
        assert envelope["title"] == "Permission Denied"
        assert envelope["detail"] == "You do not have permission to perform this action."

    def test_a_missing_course_parameter_keeps_its_status_and_changes_its_body(self):
        """The legacy address already refused this caller; only the shape of the refusal moves."""
        legacy = self.legacy("/api/user/v1/forum_roles/Moderator/users/")
        assert legacy.status_code == status.HTTP_400_BAD_REQUEST
        assert json.loads(legacy.content) == {"detail": "course_id must be specified"}

        new = self.conforming(reverse("user_v2:user_list"), {"forum_role": "Moderator"})
        envelope = assert_error_envelope(
            new, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "course_key": ["This parameter is required when forum_role is supplied."],
        }

    def test_an_unparsable_course_parameter_is_answered_rather_than_raised(self):
        """Declared difference: the version 1 address lets the key error escape the handler."""
        with pytest.raises(InvalidKeyError):
            self.legacy("/api/user/v1/forum_roles/Moderator/users/", {"course_id": "not a course"})

        new = self.conforming(
            reverse("user_v2:user_list"), {"forum_role": "Moderator", "course_key": "not a course"},
        )
        envelope = assert_error_envelope(
            new, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": ["Enter a valid course key."]}

    def test_a_database_identifier_is_refused_instead_of_narrowing_nothing(self):
        """The directory is keyed by username, and says so rather than answering an unfiltered page."""
        refused = self.conforming(reverse("user_v2:user_list"), {"id": self.user.id, "page_size": 100})
        envelope = assert_error_envelope(
            refused, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert sorted(envelope["errors"]) == ["id"]

    def test_an_identifier_held_by_a_caller_resolves_to_the_username_the_entry_is_keyed_by(self):
        """The step an integration holding identifiers takes before addressing an entry."""
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        resolved = self.client.get("/api/user/v1/accounts", {"lms_user_id": self.user.id})
        self.client.logout()
        assert resolved.status_code == status.HTTP_200_OK
        assert [account["username"] for account in json.loads(resolved.content)] == ["parity-user"]

        entry = self.conforming(reverse("user_v2:user_detail", kwargs={"username": "parity-user"}))
        assert entry.status_code == status.HTTP_200_OK
        assert json.loads(entry.content)["id"] == self.user.id


@skip_unless_lms
@override_settings(EDX_API_KEY=API_KEY)
class RefusedRequestMessageTests(APITestCase):
    """
    What a person reads when a request of either version is refused.

    The earlier addresses answer a refusal with one sentence, and the
    standardized body of this version keeps a member for that sentence beside
    the list of what each parameter was refused for. Where the refusal names
    parameters, that sentence was being built by printing the list itself, so
    the quoting, the bracketing and the class names of the data structure
    holding it reached everything that shows the sentence to a person: the
    message of an exception a generated client raises, a line in an
    integration's log, the notification a page puts on the screen.
    """

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory.create(username="refusal-admin", password=PASSWORD)

    def refused(self, address, params):
        """Call a version 2 address as an administrator carrying the site's API key."""
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        response = self.client.get(address, params, HTTP_X_EDX_API_KEY=API_KEY)
        self.client.logout()
        return assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )

    def test_the_sentence_of_a_refusal_naming_one_parameter_is_the_sentence_that_parameter_got(self):
        cases = [
            (
                reverse("user_v2:user_list"),
                {"forum_role": "Moderator"},
                {"course_key": ["This parameter is required when forum_role is supplied."]},
                "course_key: This parameter is required when forum_role is supplied.",
            ),
            (
                reverse("user_v2:user_list"),
                {"is_active": "maybe"},
                {"is_active": ["Enter true or false."]},
                "is_active: Enter true or false.",
            ),
            (
                reverse("user_v2:user_preference_list"),
                {"page_size": "0"},
                {
                    "page_size": [
                        "Enter a whole number of rows of at least 1. "
                        "A larger number than 100 returns 100 rows.",
                    ],
                },
                "page_size: Enter a whole number of rows of at least 1. "
                "A larger number than 100 returns 100 rows.",
            ),
        ]
        for address, params, expected_errors, expected_sentence in cases:
            with self.subTest(address=address, params=params):
                envelope = self.refused(address, params)
                assert envelope["errors"] == expected_errors
                assert envelope["detail"] == expected_sentence

    def test_the_sentence_of_a_refusal_naming_several_parameters_names_each_of_them(self):
        """Every parameter refused is in the sentence, in the order the list carries them."""
        envelope = self.refused(reverse("user_v2:user_list"), {"nope": "1", "alsonope": "2"})
        assert sorted(envelope["errors"]) == ["alsonope", "nope"]
        assert envelope["detail"] == " ".join(
            f"{name}: {message}"
            for name, messages in envelope["errors"].items()
            for message in messages
        )
        assert envelope["detail"].startswith("alsonope: No such query parameter.")

    def test_a_refusal_that_names_nothing_in_particular_is_spelled_on_its_own(self):
        """A body of the wrong shape is refused with no property to hang the refusal on."""
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        response = self.client.post(reverse("user_v2:email_search_list"), [], format="json")
        self.client.logout()
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "non_field_errors": ["Invalid data. Expected a dictionary, but got list."],
        }
        assert envelope["detail"] == "Invalid data. Expected a dictionary, but got list."

    def test_a_refusal_naming_no_parameter_says_what_it_has_always_said(self):
        """Only the refusals built from a list of parameters change; the rest are untouched."""
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        missing = self.client.get(
            reverse("user_v2:user_detail", kwargs={"username": "nobody"}), HTTP_X_EDX_API_KEY=API_KEY,
        )
        envelope = assert_error_envelope(
            missing, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )
        assert envelope["detail"] == "Not found."
        assert "errors" not in envelope

    def test_no_sentence_of_a_refusal_carries_the_printed_form_of_a_list_or_a_mapping(self):
        """
        One sweep over the refusals this version produces, rather than a case each.

        A sentence built by printing a data structure carries the brackets,
        the quotes and the class names that write it, none of which belong in
        something a person reads.
        """
        refusals = [
            (reverse("user_v2:user_list"), {"is_active": "maybe"}),
            (reverse("user_v2:user_list"), {"forum_role": "Moderator"}),
            (reverse("user_v2:user_list"), {"course_key": "course-v1:edX+DemoX+Demo"}),
            (reverse("user_v2:user_list"), {"ordering": "password"}),
            (reverse("user_v2:user_list"), {"nope": "1"}),
            (reverse("user_v2:user_list"), {"page_size": "0"}),
            (reverse("user_v2:user_preference_list"), {"ordering": "nope"}),
            (reverse("user_v2:user_preference_list"), {"page_size": "-1"}),
            (reverse("user_v2:time_zone_list"), {"country_code": "nowhere"}),
        ]
        for address, params in refusals:
            with self.subTest(address=address, params=params):
                envelope = self.refused(address, params)
                sentence = envelope["detail"]
                assert isinstance(sentence, str)
                assert not any(mark in sentence for mark in ("{", "}", "[", "]", "ErrorDetail(")), sentence
                assert sentence == " ".join(
                    f"{name}: {message}"
                    for name, messages in envelope["errors"].items()
                    for message in messages
                )
