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
        """Call a version 2 address as the administrator it is gated by."""
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        response = self.client.get(address, params or {})
        self.client.logout()
        return response

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

    def test_the_api_key_admits_at_the_legacy_addresses_only(self):
        """The header the version 1 addresses are gated by is not a credential at version 2."""
        legacy = self.legacy("/api/user/v1/users/", {"page_size": 100})
        assert legacy.status_code == status.HTTP_200_OK, legacy.content
        assert sorted(row["username"] for row in json.loads(legacy.content)["results"]) == [
            "parity-admin", "parity-user",
        ]

        for address in (
            reverse("user_v2:user_list"),
            reverse("user_v2:user_detail", kwargs={"username": "parity-user"}),
            reverse("user_v2:user_preference_list"),
            reverse(
                "user_v2:user_preference_detail",
                kwargs={"username": "parity-user", "key": "pref-lang"},
            ),
        ):
            refused = self.client.get(address, HTTP_X_EDX_API_KEY=API_KEY)
            envelope = assert_error_envelope(
                refused, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
            )
            assert envelope["detail"] == "Authentication credentials were not provided.", address

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
