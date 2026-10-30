"""Behaviour parity between the version 1 and version 2 account preference addresses."""

import json

from rest_framework import status

from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_email_opt_in_views import COURSE_KEY, EARLIER_ADDRESS, UNVERSIONED_EARLIER_ADDRESS
from .test_parity import diff, unexpected
from .test_preference_views import (
    MERGE_PATCH,
    SORTED_NAMES,
    UNREACHABLE_KEYS,
    UNSORTED_NAMES,
    PreferenceApiTestCase,
    earlier_document_address,
    earlier_member_address,
)

#: Preference names a filled-in account stores, in the numbers the document is
#: compared at: none, one, and enough that a body would be paged if it were a
#: list of them.
STORED_COUNTS = (0, 1, 25)

#: The members every refusal of this version carries, which the earlier
#: addresses answer with bodies of their own instead.
ENVELOPE_MEMBERS = {"type", "title", "status", "detail", "instance"}

#: Differences each case is expected to show, as (json path, reason) pairs: the
#: body compared must carry all of them and nothing else. The empty path is the
#: whole body, which is a value rather than an object at one of the addresses
#: compared. A difference in the order of the names a body carries is not a
#: path and is pinned by the test named for it instead.
INTENTIONAL_DIFFERENCES = {
    "document": [],
    "staff_document": [],
    "sorted_document": [],
    "member": [
        ("", "the earlier address answers with the stored value and nothing else"),
        ("key", "the body names the preference the value belongs to"),
        ("value", "the value is carried under a property of an object"),
    ],
    "anonymous_refusal": [
        ("type", "a refusal names the kind of fault it reports"),
        ("title", "a refusal carries a summary of the kind of fault it reports"),
        ("status", "a refusal carries the status it was answered with"),
    ],
}


def assert_parity(earlier, current, case):
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


@skip_unless_lms
class PreferenceDocumentParityTests(PreferenceApiTestCase):
    """The document each version answers with for the same account."""

    def documents(self, user, username=None):
        """Return the document each version answers, read in one session."""
        username = username or user.username
        self.login(user)
        earlier = self.client.get(earlier_document_address(username))
        current = self.client.get(self.document_address(username))
        assert earlier.status_code == status.HTTP_200_OK, earlier.content
        assert current.status_code == status.HTTP_200_OK, current.content
        return json.loads(earlier.content), json.loads(current.content)

    def test_the_document_carries_the_same_names_and_values_however_many_are_stored(self):
        for count in STORED_COUNTS:
            UserPreference.objects.filter(user=self.owner).delete()
            for index in range(count):
                self.prefer(f"counted_{index:03d}", f"value {index}")
            earlier, current = self.documents(self.owner)
            assert_parity(earlier, current, "document")
            assert current == {
                f"counted_{index:03d}": f"value {index}" for index in range(count)
            }, count

    def test_a_staff_caller_reads_the_same_document_through_either_address(self):
        earlier, current = self.documents(self.staff, username=self.owner.username)
        assert_parity(earlier, current, "staff_document")
        assert current == {"time_zone": "Asia/Tokyo"}

    def test_the_document_this_version_answers_is_sorted_and_carries_the_same_names(self):
        """
        Declared difference: the names come back in one order rather than the stored order.

        The names stored include ones that sort differently by the code of
        each character than they would with the case folded, so the order
        pinned here is the one order the document promises rather than any
        order that happens to agree with it.
        """
        for key in UNSORTED_NAMES:
            self.prefer(key, "stored")
        earlier, current = self.documents(self.owner)
        assert_parity(earlier, current, "sorted_document")
        assert list(current) == sorted(earlier) == [*SORTED_NAMES, "time_zone"]
        assert current == {
            **{name: "stored" for name in UNSORTED_NAMES}, "time_zone": "Asia/Tokyo",
        }

    def test_the_same_merge_patch_leaves_both_versions_storing_the_same_rows(self):
        self.login(self.owner)
        changes = json.dumps({"account_privacy": "private", "time_zone": None})
        assert self.client.patch(
            earlier_document_address(self.owner.username), changes, content_type=MERGE_PATCH,
        ).status_code == status.HTTP_204_NO_CONTENT
        after_earlier = self.stored()

        UserPreference.objects.filter(user=self.owner).delete()
        self.prefer("time_zone", "Asia/Tokyo")
        assert self.client.patch(
            self.document_url, changes, content_type=MERGE_PATCH,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == after_earlier == {"account_privacy": "private"}

    def test_both_versions_refuse_a_change_sent_in_another_media_type(self):
        self.login(self.owner)
        body = json.dumps({"time_zone": "Europe/Paris"})
        for address in (earlier_document_address(self.owner.username), self.document_url):
            assert self.client.patch(
                address, body, content_type="application/json",
            ).status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, address

    def test_both_versions_refuse_a_value_the_preference_cannot_hold(self):
        """The message a user is shown comes from the same place in both."""
        self.login(self.owner)
        body = json.dumps({"time_zone": "Asia/Africa"})
        earlier = self.client.patch(
            earlier_document_address(self.owner.username), body, content_type=MERGE_PATCH,
        )
        current = self.client.patch(self.document_url, body, content_type=MERGE_PATCH)
        assert earlier.status_code == current.status_code == status.HTTP_400_BAD_REQUEST
        message = "Value 'Asia/Africa' is not a valid time zone selection."
        assert json.loads(earlier.content)["field_errors"]["time_zone"]["user_message"] == message
        assert json.loads(current.content)["errors"] == {"time_zone": [message]}
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_a_value_that_is_not_text_was_spelled_out_and_stored_and_is_now_refused(self):
        """Declared difference: a value of another type is the caller's mistake, not a string."""
        self.login(self.owner)
        body = json.dumps({"account_privacy": 5})
        assert self.client.patch(
            earlier_document_address(self.owner.username), body, content_type=MERGE_PATCH,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored()["account_privacy"] == "5"

        UserPreference.objects.filter(user=self.owner, key="account_privacy").delete()
        assert self.client.patch(
            self.document_url, body, content_type=MERGE_PATCH,
        ).status_code == status.HTTP_400_BAD_REQUEST
        assert "account_privacy" not in self.stored()

    def test_a_body_naming_nothing_is_refused_by_both_versions(self):
        self.login(self.owner)
        for address in (earlier_document_address(self.owner.username), self.document_url):
            assert self.client.patch(
                address, json.dumps({}), content_type=MERGE_PATCH,
            ).status_code == status.HTTP_400_BAD_REQUEST, address


@skip_unless_lms
class PreferenceMemberParityTests(PreferenceApiTestCase):
    """One preference through either version's member address."""

    def test_the_value_is_the_same_and_this_version_names_it(self):
        """Declared difference: the body is the name and the value rather than the value alone."""
        self.login(self.owner)
        earlier = self.client.get(earlier_member_address(self.owner.username, "time_zone"))
        current = self.client.get(self.member_url)
        assert earlier.status_code == current.status_code == status.HTTP_200_OK
        assert_parity(json.loads(earlier.content), json.loads(current.content), "member")
        assert json.loads(earlier.content) == "Asia/Tokyo"
        assert json.loads(current.content) == {"key": "time_zone", "value": "Asia/Tokyo"}

    def test_both_versions_report_a_name_the_account_stores_no_value_under_as_absent(self):
        self.login(self.owner)
        for address in (
            earlier_member_address(self.owner.username, "account_privacy"),
            self.member_address(self.owner.username, "account_privacy"),
        ):
            assert self.client.get(address).status_code == status.HTTP_404_NOT_FOUND, address
            assert self.client.delete(address).status_code == status.HTTP_404_NOT_FOUND, address

    def test_the_names_the_earlier_member_route_cannot_carry_are_addressed_here(self):
        """Declared difference: the key half of the address takes the whole segment."""
        self.login(self.owner)
        for key in UNREACHABLE_KEYS:
            self.prefer(key, "stored")
            earlier = self.client.get(earlier_member_address(self.owner.username, key))
            assert earlier.status_code == status.HTTP_404_NOT_FOUND, key
            assert earlier["Content-Type"].startswith("text/html"), key
            current = self.client.get(self.member_address(self.owner.username, key))
            assert current.status_code == status.HTTP_200_OK, key
            assert json.loads(current.content) == {"key": key, "value": "stored"}, key

    def test_the_value_sent_is_stored_by_both_versions(self):
        self.login(self.owner)
        assert self.client.put(
            earlier_member_address(self.owner.username, "time_zone"),
            json.dumps("Europe/Paris"),
            content_type="application/json",
        ).status_code == status.HTTP_204_NO_CONTENT
        after_earlier = self.stored()

        assert self.put_value(
            self.member_url, {"value": "Europe/Paris"},
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == after_earlier == {"time_zone": "Europe/Paris"}

    def test_a_value_sent_as_a_bare_string_is_no_longer_read(self):
        """Declared difference: the value travels as a property of an object."""
        self.login(self.owner)
        refused = self.client.put(
            self.member_url, json.dumps("Europe/Paris"), content_type="application/json",
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_both_versions_refuse_an_empty_value_with_the_same_message(self):
        self.login(self.owner)
        earlier = self.client.put(
            earlier_member_address(self.owner.username, "account_privacy"),
            json.dumps(""),
            content_type="application/json",
        )
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert json.loads(earlier.content)["user_message"] == (
            "Preference 'account_privacy' cannot be set to an empty value."
        )
        current = self.put_value(
            self.member_address(self.owner.username, "account_privacy"), {"value": ""},
        )
        assert current.status_code == status.HTTP_400_BAD_REQUEST
        assert json.loads(current.content)["errors"] == {"value": ["This field may not be blank."]}

    def test_a_preference_is_removed_by_both_versions(self):
        self.login(self.owner)
        assert self.client.delete(
            earlier_member_address(self.owner.username, "time_zone"),
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {}
        self.prefer("time_zone", "Asia/Tokyo")
        assert self.client.delete(self.member_url).status_code == status.HTTP_204_NO_CONTENT
        assert self.stored() == {}


@skip_unless_lms
class PreferenceRefusalParityTests(PreferenceApiTestCase):
    """How each version refuses the same caller."""

    def addresses(self, username):
        """Return the document and member address of one account in each version."""
        return (
            (earlier_document_address(username), self.document_address(username)),
            (
                earlier_member_address(username, "time_zone"),
                self.member_address(username, "time_zone"),
            ),
        )

    def test_both_versions_refuse_a_learner_reading_another_account(self):
        self.login(self.other)
        for earlier_address, address in self.addresses(self.owner.username):
            earlier = self.client.get(earlier_address)
            current = self.client.get(address)
            assert earlier.status_code == current.status_code == status.HTTP_403_FORBIDDEN
            assert earlier.content == b""
            assert set(json.loads(current.content)) >= ENVELOPE_MEMBERS

    def test_both_versions_refuse_a_learner_changing_another_account(self):
        self.login(self.other)
        body = json.dumps({"time_zone": "Europe/Paris"})
        earlier_document, document = self.addresses(self.owner.username)[0]
        for address in (earlier_document, document):
            assert self.client.patch(
                address, body, content_type=MERGE_PATCH,
            ).status_code == status.HTTP_403_FORBIDDEN, address
        earlier_member, member = self.addresses(self.owner.username)[1]
        for address in (earlier_member, member):
            assert self.client.delete(address).status_code == status.HTTP_403_FORBIDDEN, address
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_both_versions_refuse_a_staff_caller_changing_another_account(self):
        self.login(self.staff)
        earlier = self.client.patch(
            earlier_document_address(self.owner.username),
            json.dumps({"time_zone": "Europe/Paris"}),
            content_type=MERGE_PATCH,
        )
        current = self.client.patch(
            self.document_url, json.dumps({"time_zone": "Europe/Paris"}), content_type=MERGE_PATCH,
        )
        assert earlier.status_code == current.status_code == status.HTTP_403_FORBIDDEN
        assert self.stored() == {"time_zone": "Asia/Tokyo"}

    def test_both_versions_refuse_an_address_that_is_not_exactly_the_callers_name(self):
        caller = self.sign_in_as_a_case_variant_of(self.owner)
        assert caller.username == self.owner.username.upper()
        for earlier_address, address in self.addresses(self.owner.username):
            assert self.client.get(earlier_address).status_code == status.HTTP_403_FORBIDDEN
            assert self.client.get(address).status_code == status.HTTP_403_FORBIDDEN

    def test_a_staff_caller_is_told_about_a_name_nobody_holds_by_both_versions(self):
        self.login(self.staff)
        for earlier_address, address in self.addresses("nobody"):
            assert self.client.get(earlier_address).status_code == status.HTTP_404_NOT_FOUND
            assert self.client.get(address).status_code == status.HTTP_404_NOT_FOUND

    def test_a_learner_is_refused_a_name_nobody_holds_by_both_versions(self):
        """Neither version lets a caller learn whether a name is held from these addresses."""
        self.login(self.other)
        for earlier_address, address in self.addresses("nobody"):
            assert self.client.get(earlier_address).status_code == status.HTTP_403_FORBIDDEN
            assert self.client.get(address).status_code == status.HTTP_403_FORBIDDEN

    def test_an_anonymous_caller_is_refused_by_both_versions(self):
        """Declared difference: the refusal carries the standard body."""
        for earlier_address, address in self.addresses(self.owner.username):
            earlier = self.client.get(earlier_address)
            current = self.client.get(address)
            assert earlier.status_code == current.status_code == status.HTTP_401_UNAUTHORIZED
            assert_parity(
                json.loads(earlier.content), json.loads(current.content), "anonymous_refusal",
            )
            assert set(json.loads(earlier.content)) == {"detail"}
            assert set(json.loads(current.content)) >= ENVELOPE_MEMBERS

    def test_a_query_parameter_neither_version_reads_is_ignored_by_one_and_refused_by_the_other(self):
        """Declared difference: a parameter this version does not read is refused."""
        self.login(self.owner)
        for earlier_address, address in self.addresses(self.owner.username):
            assert self.client.get(earlier_address, {"page": "2"}).status_code == (
                status.HTTP_200_OK
            )
            assert self.client.get(address, {"page": "2"}).status_code == (
                status.HTTP_400_BAD_REQUEST
            )


@skip_unless_lms
class SupersededAddressAnnouncementTests(PreferenceApiTestCase):
    """
    What the superseded addresses say about being superseded: nothing, in any header.

    Which addresses are superseded is published in the document and carried by
    the removal ticket. The addresses themselves answer exactly as they always
    have, because a response header would be a change to a version already in
    use. That is pinned here so the declaration and the running code cannot
    drift apart.
    """

    #: Headers an answer would carry an announcement of its own removal in.
    ANNOUNCING_HEADERS = ("Deprecation", "Sunset", "Warning", "Link")

    def assert_nothing_was_announced(self, response, expected_status, address):
        """Assert the answer is the one that address gives, and announces nothing."""
        assert response.status_code == expected_status, (address, response.content)
        announced = [header for header in self.ANNOUNCING_HEADERS if header in response]
        assert announced == [], address

    def test_no_superseded_preference_address_announces_its_own_removal(self):
        self.login(self.owner)
        document = earlier_document_address(self.owner.username)
        member = earlier_member_address(self.owner.username, "time_zone")
        exchanges = (
            (f"GET {document}", self.client.get(document), status.HTTP_200_OK),
            (
                f"PATCH {document}",
                self.client.patch(
                    document, json.dumps({"zoneinfo": "Europe/Paris"}), content_type=MERGE_PATCH,
                ),
                status.HTTP_204_NO_CONTENT,
            ),
            (f"GET {member}", self.client.get(member), status.HTTP_200_OK),
            (
                f"PUT {member}",
                self.client.put(member, json.dumps("Asia/Tokyo"), content_type="application/json"),
                status.HTTP_204_NO_CONTENT,
            ),
            (f"DELETE {member}", self.client.delete(member), status.HTTP_204_NO_CONTENT),
        )
        for address, response, expected_status in exchanges:
            with self.subTest(address=address):
                self.assert_nothing_was_announced(response, expected_status, address)

    def test_neither_address_of_the_superseded_email_choice_announces_one(self):
        self.login(self.owner)
        for address in (EARLIER_ADDRESS, UNVERSIONED_EARLIER_ADDRESS):
            with self.subTest(address=address):
                self.assert_nothing_was_announced(
                    self.client.post(address, {"course_id": COURSE_KEY, "email_opt_in": "True"}),
                    status.HTTP_200_OK,
                    address,
                )
