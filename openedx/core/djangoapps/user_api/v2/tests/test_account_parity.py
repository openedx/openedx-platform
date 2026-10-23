"""Behaviour parity between the version 1 and version 2 account addresses."""

import json

from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import get_retired_email_by_email
from common.djangoapps.student.tests.factories import (
    ContentTypeFactory,
    PermissionFactory,
    UserFactory,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_parity import diff, unexpected

PASSWORD = "Password1234"
MERGE_PATCH = "application/merge-patch+json"

EARLIER_ME = "/api/user/v1/me"
EARLIER_LIST = "/api/user/v1/accounts"
EARLIER_SEARCH = "/api/user/v1/accounts/search_emails"


def earlier_detail(username):
    """Return the version 1 member address of one account."""
    return f"/api/user/v1/accounts/{username}"


def by_username(rows):
    """
    Return the given rows in username order.

    The earlier addresses answer in whatever order the database returns the
    accounts, so rows are brought into one order before their properties are
    compared with this version's. The order a page comes back in is a
    difference of its own and is pinned by the tests named for it.
    """
    return sorted(rows, key=lambda row: row["username"])


#: Differences each case is expected to show, as (json path, reason) pairs: the
#: diff must carry all of them and nothing else. A difference in the shape of a
#: body, or in the order of the rows it carries, is not a path and is pinned by
#: the test named for it instead.
INTENTIONAL_DIFFERENCES = {
    "account_list": [],
    "account_detail": [],
    "shared_account": [],
    "another_account": [],
    "changed_account": [],
    "email_search": [],
}

#: Names the member address of this version reads as something other than a
#: username, with what each one names instead and what that address answers for
#: an account holding it. The earlier member address answers every one of them
#: with that account; here each is read through the collection, with
#: ``username=`` naming it.
RESERVED_MEMBER_IDENTIFIERS = (
    ("me", "the account of the caller", status.HTTP_200_OK),
)


@skip_unless_lms
class AccountParityTests(APITestCase):
    """The same fixtures through the version 1 and the version 2 account addresses."""

    def setUp(self):
        super().setUp()
        self.staff = UserFactory.create(
            username="parity-staff", email="parity-staff@example.com", password=PASSWORD, is_staff=True,
        )
        self.owner = UserFactory.create(
            username="parity-owner", email="parity-owner@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="parity-other", email="parity-other@example.com", password=PASSWORD,
        )

    def login(self, user):
        """Sign the given user in, which both versions authenticate the same way."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def bodies(self, user, earlier_address, address, params=None):
        """
        Return the decoded body each version answers the same request with.

        Both are read inside one session, because signing in again would stamp
        a new last-login time on the account and so on the second body.
        """
        self.login(user)
        earlier = self.client.get(earlier_address, params or {})
        current = self.client.get(address, params or {})
        self.client.logout()
        assert earlier.status_code == status.HTTP_200_OK, earlier.content
        assert current.status_code == status.HTTP_200_OK, current.content
        return json.loads(earlier.content), json.loads(current.content)

    def assert_parity(self, earlier, current, case):
        """Assert the two bodies differ only where the case declares they do."""
        changed = diff(earlier, current)
        intentional = INTENTIONAL_DIFFERENCES[case]
        assert not unexpected(changed, intentional), (
            f"undeclared differences: {sorted(unexpected(changed, intentional))}\n"
            f"earlier={earlier}\ncurrent={current}"
        )
        for path, reason in intentional:
            assert any(
                key == path or key.startswith(path + ".") or key.startswith(path + "[")
                for key in changed
            ), f"declared difference did not occur: {path} ({reason})"

    def test_the_callers_own_address_answers_with_the_whole_account(self):
        """Declared difference: the earlier address answers with the username alone."""
        self.login(self.owner)
        earlier = json.loads(self.client.get(EARLIER_ME).content)
        current = json.loads(
            self.client.get(reverse("user_v2:account_detail", kwargs={"username": "me"})).content,
        )
        named = json.loads(
            self.client.get(
                reverse("user_v2:account_detail", kwargs={"username": "parity-owner"}),
            ).content,
        )
        self.client.logout()

        assert earlier == {"username": "parity-owner"}
        assert current["username"] == earlier["username"]
        assert current["email"] == "parity-owner@example.com"
        assert current == named
        assert set(current) > set(earlier)

    def test_the_earlier_own_address_body_is_the_selection_of_one_property(self):
        """The property the earlier address answered with is one selection of this one."""
        earlier, current = self.bodies(
            self.owner,
            EARLIER_ME,
            reverse("user_v2:account_detail", kwargs={"username": "me"}),
            {"fields": "username"},
        )
        assert earlier == current == {"username": "parity-owner"}

    def test_account_list_parity(self):
        earlier, current = self.bodies(
            self.staff, EARLIER_LIST, reverse("user_v2:account_list"),
            {"username": "parity-other,parity-owner"},
        )
        assert isinstance(earlier, list)
        assert len(earlier) == 2
        self.assert_parity(earlier, current["results"], "account_list")

    def test_the_page_carries_the_rows_the_earlier_list_returned_unbounded(self):
        """
        Differences this version declares: the rows come in a page, and in username order.

        The earlier address answered with a bare array of every account named,
        in the order the database returned them; here they are the page of the
        envelope each list of this version answers with, ordered by username,
        so the same selection asked for twice is cut into the same pages.
        """
        earlier, current = self.bodies(
            self.staff, EARLIER_LIST, reverse("user_v2:account_list"),
            {"username": "parity-other,parity-owner"},
        )
        assert isinstance(earlier, list)
        assert {account["username"] for account in earlier} == {"parity-other", "parity-owner"}
        assert set(current) == {
            "count", "num_pages", "current_page", "start", "next", "previous", "results",
        }
        assert current["count"] == len(earlier)
        assert [account["username"] for account in current["results"]] == [
            "parity-other", "parity-owner",
        ]

    def test_an_account_whose_name_this_version_reserves_is_read_through_the_collection(self):
        """Declared difference: a reserved name addresses something else here."""
        self.login(self.staff)
        for name, _names_instead, member_status in RESERVED_MEMBER_IDENTIFIERS:
            held = UserFactory.create(
                username=name, email=f"{name}@example.com", password=PASSWORD,
            )
            earlier = self.client.get(earlier_detail(name))
            assert earlier.status_code == status.HTTP_200_OK, earlier.content
            assert json.loads(earlier.content)["username"] == name

            member = self.client.get(reverse("user_v2:account_detail", kwargs={"username": name}))
            assert member.status_code == member_status
            assert json.loads(member.content).get("username") != name

            page = self.client.get(reverse("user_v2:account_list"), {"username": name})
            assert page.status_code == status.HTTP_200_OK
            assert [row["username"] for row in json.loads(page.content)["results"]] == [name]
            assert json.loads(page.content)["results"][0]["email"] == held.email
        self.client.logout()

    def test_a_selection_that_matches_no_account_is_an_empty_page(self):
        """Declared difference: a collection that matched nothing is empty rather than absent."""
        self.login(self.staff)
        earlier = self.client.get(EARLIER_LIST, {"username": "nobody"})
        current = self.client.get(reverse("user_v2:account_list"), {"username": "nobody"})
        self.client.logout()

        assert earlier.status_code == status.HTTP_404_NOT_FOUND
        assert earlier.content == b""
        assert current.status_code == status.HTTP_200_OK
        assert json.loads(current.content)["count"] == 0
        assert json.loads(current.content)["results"] == []

    def test_account_detail_parity_for_the_owner(self):
        earlier, current = self.bodies(
            self.owner,
            earlier_detail("parity-owner"),
            reverse("user_v2:account_detail", kwargs={"username": "parity-owner"}),
        )
        self.assert_parity(earlier, current, "account_detail")
        assert current["email"] == "parity-owner@example.com"

    def test_account_detail_parity_for_a_staff_caller(self):
        earlier, current = self.bodies(
            self.staff,
            earlier_detail("parity-owner"),
            reverse("user_v2:account_detail", kwargs={"username": "parity-owner"}),
        )
        self.assert_parity(earlier, current, "account_detail")

    def test_the_properties_another_learner_may_see_are_unchanged(self):
        """The field-level visibility is the whole of the record protection on this resource."""
        earlier, current = self.bodies(
            self.owner,
            earlier_detail("parity-other"),
            reverse("user_v2:account_detail", kwargs={"username": "parity-other"}),
        )
        assert set(earlier) == {"account_privacy", "profile_image", "username"}
        self.assert_parity(earlier, current, "another_account")

    def test_the_shared_preset_is_unchanged(self):
        earlier, current = self.bodies(
            self.owner,
            earlier_detail("parity-owner"),
            reverse("user_v2:account_detail", kwargs={"username": "parity-owner"}),
            {"view": "shared"},
        )
        assert set(earlier) == {"account_privacy", "profile_image", "username"}
        self.assert_parity(earlier, current, "shared_account")

    def test_the_account_a_change_answers_with_is_unchanged(self):
        self.login(self.owner)
        earlier = self.client.patch(
            earlier_detail("parity-owner"), json.dumps({"bio": "parity bio"}), content_type=MERGE_PATCH,
        )
        current = self.client.patch(
            reverse("user_v2:account_detail", kwargs={"username": "parity-owner"}),
            json.dumps({"bio": "parity bio"}),
            content_type=MERGE_PATCH,
        )
        assert earlier.status_code == status.HTTP_200_OK, earlier.content
        assert current.status_code == status.HTTP_200_OK, current.content
        self.assert_parity(json.loads(earlier.content), json.loads(current.content), "changed_account")
        assert json.loads(current.content)["bio"] == "parity bio"

    def test_email_search_parity(self):
        emails = {"emails": ["parity-other@example.com", "parity-owner@example.com"]}
        self.login(self.staff)
        earlier = self.client.post(EARLIER_SEARCH, emails, format="json")
        current = self.client.post(reverse("user_v2:email_search_list"), emails, format="json")
        assert earlier.status_code == status.HTTP_200_OK, earlier.content
        assert current.status_code == status.HTTP_200_OK, current.content
        earlier_rows = by_username(json.loads(earlier.content))
        self.assert_parity(earlier_rows, json.loads(current.content)["results"], "email_search")
        assert earlier_rows[0]["email"] == "parity-other@example.com"

    def test_the_search_page_carries_the_rows_the_earlier_search_returned_unbounded(self):
        """
        Differences this version declares: the rows come in a page, and in username order.

        The earlier address answered with a bare array of every account it
        found, in the order the database returned them; here they are the page
        of the envelope each list of this version answers with, ordered by
        username, so the same search asked for twice is cut into the same
        pages.
        """
        emails = {"emails": [
            "parity-other@example.com", "parity-owner@example.com", "parity-staff@example.com",
        ]}
        self.login(self.staff)
        earlier = json.loads(self.client.post(EARLIER_SEARCH, emails, format="json").content)
        current = json.loads(
            self.client.post(reverse("user_v2:email_search_list"), emails, format="json").content,
        )
        self.client.logout()

        assert isinstance(earlier, list)
        assert {row["username"] for row in earlier} == {
            "parity-other", "parity-owner", "parity-staff",
        }
        assert set(current) == {
            "count", "num_pages", "current_page", "start", "next", "previous", "results",
        }
        assert current["count"] == len(earlier)
        assert [row["username"] for row in current["results"]] == [
            "parity-other", "parity-owner", "parity-staff",
        ]
        assert current["next"] is None


@skip_unless_lms
class AccountRefusalParityTests(APITestCase):
    """The refusals each version answers with, recorded beside each other."""

    def setUp(self):
        super().setUp()
        self.staff = UserFactory.create(
            username="refusal-staff", email="refusal-staff@example.com", password=PASSWORD, is_staff=True,
        )
        self.owner = UserFactory.create(
            username="refusal-owner", email="refusal-owner@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="refusal-other", email="refusal-other@example.com", password=PASSWORD,
        )
        self.member = reverse("user_v2:account_detail", kwargs={"username": "refusal-owner"})

    def login(self, user):
        """Sign the given user in."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def test_an_unauthenticated_refusal_keeps_its_status_and_changes_its_body(self):
        earlier = self.client.get(EARLIER_LIST)
        assert earlier.status_code == status.HTTP_401_UNAUTHORIZED
        assert json.loads(earlier.content) == {
            "detail": "Authentication credentials were not provided.",
        }

        current = self.client.get(reverse("user_v2:account_list"))
        envelope = assert_error_envelope(
            current, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )
        assert envelope["title"] == "Authentication Required"
        assert envelope["instance"] == "/api/user/v2/accounts/"

    def test_a_selection_no_account_matches_is_a_page_here_and_was_a_refusal_before(self):
        """
        Selecting by an address nobody holds narrows the page to nothing instead of refusing.

        The earlier address answered one collection two ways: a name it found
        nothing behind left the page empty, while an address it found nothing
        behind was refused. Here every selector narrows the same way, and the
        refusal is kept for an address an account held before it was retired.
        """
        self.login(self.staff)
        selection = {"email": "nobody@example.com"}
        earlier = self.client.get(EARLIER_LIST, selection)
        assert earlier.status_code == status.HTTP_404_NOT_FOUND

        current = self.client.get(reverse("user_v2:account_list"), selection)
        assert current.status_code == status.HTTP_200_OK
        body = json.loads(current.content)
        assert body["count"] == 0
        assert body["results"] == []

    def test_a_name_no_account_holds_keeps_its_status_and_gains_a_body(self):
        self.login(self.staff)
        earlier = self.client.get(earlier_detail("nobody"))
        assert earlier.status_code == status.HTTP_404_NOT_FOUND
        assert earlier.content == b""

        current = self.client.get(reverse("user_v2:account_detail", kwargs={"username": "nobody"}))
        envelope = assert_error_envelope(
            current, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )
        assert envelope["detail"] == "Not found."

    def test_changing_another_account_keeps_its_status_and_gains_a_body(self):
        self.login(self.owner)
        earlier = self.client.patch(
            earlier_detail("refusal-other"), json.dumps({"bio": "x"}), content_type=MERGE_PATCH,
        )
        assert earlier.status_code == status.HTTP_403_FORBIDDEN
        assert earlier.content == b""

        current = self.client.patch(
            reverse("user_v2:account_detail", kwargs={"username": "refusal-other"}),
            json.dumps({"bio": "x"}),
            content_type=MERGE_PATCH,
        )
        assert_error_envelope(
            current, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_refused_change_names_the_same_property_in_a_body_of_another_shape(self):
        self.login(self.owner)
        body = json.dumps({"year_of_birth": "recently"})
        earlier = self.client.patch(earlier_detail("refusal-owner"), body, content_type=MERGE_PATCH)
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert json.loads(earlier.content) == {
            "field_errors": {
                "year_of_birth": {
                    "developer_message": "Value 'recently' is not valid for field 'year_of_birth': "
                                         "['A valid integer is required.']",
                    "user_message": "This value is invalid.",
                },
            },
        }

        current = self.client.patch(self.member, body, content_type=MERGE_PATCH)
        envelope = assert_error_envelope(
            current, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        # Both answers name the property and say the value is not a year. The
        # earlier one says why inside its developer message and keeps a fixed
        # sentence for the reader; this one says it once, where a reader looks.
        assert envelope["errors"] == {"year_of_birth": ["A valid integer is required."]}

    def test_a_body_in_another_media_type_keeps_its_status_and_gains_a_body(self):
        self.login(self.owner)
        earlier = self.client.patch(
            earlier_detail("refusal-owner"), json.dumps({"bio": "x"}), content_type="application/json",
        )
        assert earlier.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

        current = self.client.patch(self.member, json.dumps({"bio": "x"}), content_type="application/json")
        assert_error_envelope(
            current,
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )

    def test_the_search_hides_itself_from_a_learner_in_both_versions(self):
        self.login(self.owner)
        emails = {"emails": ["refusal-owner@example.com"]}
        earlier = self.client.post(EARLIER_SEARCH, emails, format="json")
        assert earlier.status_code == status.HTTP_404_NOT_FOUND
        assert json.loads(earlier.content) == {
            "developer_message": "not_found", "user_message": "Not Found",
        }

        current = self.client.post(reverse("user_v2:email_search_list"), emails, format="json")
        assert_error_envelope(
            current, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_a_search_without_addresses_keeps_its_status_and_names_the_property(self):
        self.login(self.staff)
        earlier = self.client.post(EARLIER_SEARCH, {}, format="json")
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert json.loads(earlier.content) == {
            "developer_message": "'emails' field is required",
            "user_message": "'emails' field is required",
        }

        current = self.client.post(reverse("user_v2:email_search_list"), {}, format="json")
        envelope = assert_error_envelope(
            current, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"emails": ["This field is required."]}

    def test_a_search_longer_than_a_page_is_refused_here_and_was_answered_before(self):
        """
        A difference this version declares: a search names at most a hundred addresses.

        The earlier address took as many addresses as a body could carry and
        read them in one query; here a search is held to what the largest page
        a caller may ask for can answer with.
        """
        self.login(self.staff)
        emails = {"emails": [f"crowd-{index:03d}@example.com" for index in range(101)]}

        earlier = self.client.post(EARLIER_SEARCH, emails, format="json")
        assert earlier.status_code == status.HTTP_200_OK, earlier.content
        assert json.loads(earlier.content) == []

        current = self.client.post(reverse("user_v2:email_search_list"), emails, format="json")
        envelope = assert_error_envelope(
            current, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "emails": ["Ensure this field has no more than 100 elements."],
        }

    def test_the_address_of_a_retired_account_keeps_what_the_earlier_body_carried(self):
        self.login(self.staff)
        retired_email = self.other.email
        self.other.email = get_retired_email_by_email(retired_email)
        self.other.save()

        earlier = self.client.get(EARLIER_LIST, {"email": retired_email})
        assert earlier.status_code == status.HTTP_404_NOT_FOUND
        earlier_body = json.loads(earlier.content)
        assert set(earlier_body) == {"error_msg", "can_cancel_retirement", "retirement_id"}

        current = self.client.get(reverse("user_v2:account_list"), {"email": retired_email})
        envelope = assert_error_envelope(
            current,
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="user/retired-account",
        )
        assert envelope["detail"] == earlier_body["error_msg"]
        assert envelope["can_cancel_retirement"] == earlier_body["can_cancel_retirement"]
        assert envelope["retirement_id"] == earlier_body["retirement_id"]


@skip_unless_lms
class DeactivationParityTests(APITestCase):
    """The deactivation of an account, through each version."""

    def setUp(self):
        super().setUp()
        self.deactivator = UserFactory.create(username="parity-deactivator", password=PASSWORD)
        self.deactivator.user_permissions.add(
            PermissionFactory(
                codename="can_deactivate_users",
                content_type=ContentTypeFactory(app_label="student"),
            ),
        )
        self.earlier_target = UserFactory.create(username="parity-target-one", password=PASSWORD)
        self.target = UserFactory.create(username="parity-target-two", password=PASSWORD)
        assert self.client.login(username=self.deactivator.username, password=PASSWORD)

    def test_both_versions_make_the_password_unusable(self):
        earlier = self.client.post(f"/api/user/v1/accounts/{self.earlier_target.username}/deactivate/")
        assert earlier.status_code == status.HTTP_200_OK
        assert json.loads(earlier.content)["username"] == "parity-target-one"
        self.earlier_target.refresh_from_db()
        assert not self.earlier_target.has_usable_password()

        current = self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": self.target.username}),
        )
        assert current.status_code == status.HTTP_204_NO_CONTENT
        assert current.content == b""
        self.target.refresh_from_db()
        assert not self.target.has_usable_password()

    def test_the_reserved_identifier_names_the_same_account_on_both_addresses(self):
        """``me`` is a stored username on this action in both versions, not a name for the caller."""
        held = UserFactory.create(username="me", password=PASSWORD)
        earlier = self.client.post("/api/user/v1/accounts/me/deactivate/")
        assert earlier.status_code == status.HTTP_200_OK
        assert json.loads(earlier.content)["username"] == "me"
        held.refresh_from_db()
        assert not held.has_usable_password()

        held.set_password(PASSWORD)
        held.save()
        current = self.client.post(reverse("user_v2:account_deactivation", kwargs={"username": "me"}))
        assert current.status_code == status.HTTP_204_NO_CONTENT
        held.refresh_from_db()
        assert not held.has_usable_password()
        self.deactivator.refresh_from_db()
        assert self.deactivator.has_usable_password()

    def test_the_account_is_readable_after_it_is_deactivated(self):
        """The account the earlier version answered with is one read away."""
        self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": self.target.username}),
        )
        account = self.client.get(
            reverse("user_v2:account_detail", kwargs={"username": self.target.username}),
        )
        assert account.status_code == status.HTTP_200_OK
        assert json.loads(account.content)["username"] == "parity-target-two"

    def test_a_name_no_account_holds_is_not_found(self):
        """An address naming no account is refused with the envelope every refusal carries."""
        current = self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": "nobody"}),
        )
        assert_error_envelope(
            current, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )
