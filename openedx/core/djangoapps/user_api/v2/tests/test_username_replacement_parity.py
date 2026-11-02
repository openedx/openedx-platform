"""Behaviour parity between the two addresses the names of a set of accounts are replaced at."""

import json
import re
from unittest import mock

import pytest
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import DatabaseError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import get_retired_username_by_username
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.credit.models import CreditEligibility
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.v2 import view_services
from openedx.core.djangoapps.user_api.v2.serializers import (
    REPLACEMENT_MAX_MAPPINGS,
    REPLACEMENT_NO_SUCH_ACCOUNT,
    REPLACEMENT_REPLACED,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
REPLACEMENT_WORKER_USERNAME = "parity-replacement-worker"

EARLIER = "/api/user/v1/accounts/replace_usernames/"
FORM = "application/x-www-form-urlencoded"

#: What the generated suffix is while these tests run, so that a name handed
#: out by either address can be named rather than matched.
FIXED_SUFFIX = "a1b2c3"

#: The differences a caller moving between the two addresses would notice.
#: Each is measured on both addresses below, so a difference that is not
#: listed here fails rather than passing unnoticed.
DECLARED_DIFFERENCES = (
    (
        "the answer",
        "the earlier address answers two arrays of mappings, the conforming one a result per "
        "mapping with what was done about it",
    ),
    (
        "a name no account is stored under",
        "the earlier address reports it among the replacements it carried out, the conforming one "
        "as a name that reached no account",
    ),
    (
        "a request carrying no mappings at all",
        "the earlier address answers two empty arrays, the conforming one refuses a request that "
        "asks for nothing",
    ),
    (
        "a body sent as a web form",
        "the earlier address answers that the body does not match what it reads, the conforming "
        "one that it cannot read the form",
    ),
    (
        "the most mappings one request may carry",
        "the earlier address carries any number of them, the conforming one refuses a request "
        "carrying more than one page of them",
    ),
    (
        "space around the name asked for",
        "the earlier address stores the name with the space it was given with, the conforming one "
        "stores what is left once the space on either end is taken off",
    ),
    (
        "a write that fails part way through",
        "the earlier address reports that one mapping as failed and keeps the rest, the conforming "
        "one answers this server's failure and keeps none of them",
    ),
)


@override_settings(USERNAME_REPLACEMENT_WORKER=REPLACEMENT_WORKER_USERNAME)
class ReplacementParityTestCase(APITestCase):
    """The caller both addresses admit."""

    def setUp(self):
        super().setUp()
        self.conforming_url = reverse("user_v2:username_replacement_list")
        self.replacer = UserFactory.create(username=REPLACEMENT_WORKER_USERNAME, password=PASSWORD)
        assert self.client.login(username=REPLACEMENT_WORKER_USERNAME, password=PASSWORD)

    def url_of(self, address):
        """Return the address to send to."""
        return EARLIER if address == EARLIER else self.conforming_url

    def addresses(self):
        """Return each address, by the name used for it here."""
        return (EARLIER, "conforming")

    def ask(self, address, mappings, as_form=False):
        """Ask this address to replace these names."""
        if as_form:
            return self.client.post(self.url_of(address), "username_mappings=x", content_type=FORM)
        return self.client.post(
            self.url_of(address),
            json.dumps({"username_mappings": mappings}),
            content_type="application/json",
        )

    def fixed_suffix(self):
        """Hold the generated suffix at one value at both addresses."""
        return mock.patch.multiple(
            "openedx.core.djangoapps.user_api.accounts.views",
            username_suffix_generator=mock.Mock(return_value=FIXED_SUFFIX),
        ), mock.patch.object(
            view_services, "username_suffix_generator", return_value=FIXED_SUFFIX,
        )

    def with_a_fixed_suffix(self, call):
        """Run this call with the generated suffix held at one value."""
        legacy_patch, conforming_patch = self.fixed_suffix()
        with legacy_patch, conforming_patch:
            return call()


@skip_unless_lms
class ReplacementOutcomeParityTests(ReplacementParityTestCase):
    """Both addresses replace the same names; what each reports about them differs."""

    def test_a_name_an_account_is_stored_under_is_replaced_by_both(self):
        answered = {}
        for address in self.addresses():
            held = UserFactory.create(username=f"held-at-{address == EARLIER}")
            answered[address] = (
                self.ask(address, [{held.username: f"asked-{address == EARLIER}"}]),
                held,
            )
            assert User.objects.get(id=held.id).username == f"asked-{address == EARLIER}"
        assert answered[EARLIER][0].json() == {
            "successful_replacements": [{"held-at-True": "asked-True"}],
            "failed_replacements": [],
        }
        assert answered["conforming"][0].json() == {
            "results": [{
                "current_username": "held-at-False",
                "new_username": "asked-False",
                "outcome": REPLACEMENT_REPLACED,
            }],
        }

    def test_a_name_another_account_holds_is_handed_out_with_a_suffix_by_both(self):
        handed_out = {}
        for address in self.addresses():
            held = UserFactory.create(username=f"holder-{address == EARLIER}")
            UserFactory.create(username=f"taken-{address == EARLIER}")
            answered = self.with_a_fixed_suffix(
                lambda a=address, h=held: self.ask(
                    a, [{h.username: f"taken-{a == EARLIER}"}],
                ),
            )
            handed_out[address] = (answered, User.objects.get(id=held.id).username)
        assert handed_out[EARLIER][1] == f"taken-True-{FIXED_SUFFIX}"
        assert handed_out["conforming"][1] == f"taken-False-{FIXED_SUFFIX}"
        assert handed_out[EARLIER][0].json() == {
            "successful_replacements": [{"holder-True": f"taken-True-{FIXED_SUFFIX}"}],
            "failed_replacements": [],
        }
        assert handed_out["conforming"][0].json()["results"] == [{
            "current_username": "holder-False",
            "new_username": f"taken-False-{FIXED_SUFFIX}",
            "outcome": REPLACEMENT_REPLACED,
        }]

    def test_a_name_no_account_is_stored_under_is_reported_differently(self):
        """The earlier address reports it among the replacements it carried out."""
        answered = {}
        for address in self.addresses():
            answered[address] = self.ask(address, [{"nobody-holds-this": "asked-for"}])
        assert answered[EARLIER].json() == {
            "successful_replacements": [{"nobody-holds-this": "asked-for"}],
            "failed_replacements": [],
        }
        assert answered["conforming"].json() == {
            "results": [{
                "current_username": "nobody-holds-this",
                "new_username": None,
                "outcome": REPLACEMENT_NO_SUCH_ACCOUNT,
            }],
        }
        assert not User.objects.filter(username="asked-for").exists()

    def test_a_request_carrying_no_mappings_at_all_is_answered_differently(self):
        answered = {address: self.ask(address, []) for address in self.addresses()}
        assert answered[EARLIER].status_code == status.HTTP_200_OK
        assert answered[EARLIER].json() == {"successful_replacements": [], "failed_replacements": []}
        assert answered["conforming"].status_code == status.HTTP_400_BAD_REQUEST

    def test_a_mapping_naming_several_accounts_is_refused_by_both(self):
        answered = {
            address: self.ask(address, [{"one": "first", "two": "second"}])
            for address in self.addresses()
        }
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_400_BAD_REQUEST,
        )

    def test_mappings_sent_as_something_other_than_a_list_are_refused_by_both(self):
        answered = {
            address: self.ask(address, {"one": "first"}) for address in self.addresses()
        }
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_400_BAD_REQUEST,
        )

    def test_a_body_sent_as_a_web_form_is_refused_by_both(self):
        answered = {address: self.ask(address, [], as_form=True) for address in self.addresses()}
        assert answered[EARLIER].status_code == status.HTTP_400_BAD_REQUEST
        assert answered["conforming"].status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


@skip_unless_lms
@pytest.mark.usefixtures("setup_retirement_states")
class ReplacementOfARetiringAccountParityTests(ReplacementParityTestCase):
    """What either address does to an account the pipeline already holds a record for."""

    def state_of(self, record, user):
        """Return the names the account and its record are stored under."""
        record.refresh_from_db()
        return {
            "name on the account": User.objects.get(id=user.id).username,
            "name on the record": record.original_username,
            "hashed name on the record": record.retired_username,
        }

    def test_both_addresses_rewrite_the_record_and_leave_the_hashed_name_as_it_was(self):
        """
        The hashed name is left as the hash of the name the account was registered under.

        Working it out again would change what the pipeline hashes and could
        reach a name a retired account already holds, so neither address does
        it; the record stays addressable under the name it now carries.
        """
        measured = {}
        for address in self.addresses():
            held = UserFactory.create(username=f"retiring-{address == EARLIER}")
            record = create_retirement_status(held)
            hashed_before = record.retired_username
            self.ask(address, [{held.username: f"renamed-{address == EARLIER}"}])
            measured[address] = (self.state_of(record, held), hashed_before)
        for address in self.addresses():
            state, hashed_before = measured[address]
            assert state == {
                "name on the account": f"renamed-{address == EARLIER}",
                "name on the record": f"renamed-{address == EARLIER}",
                "hashed name on the record": hashed_before,
            }, address
            assert hashed_before == get_retired_username_by_username(
                f"retiring-{address == EARLIER}",
            )


#: The statements of one request, as a shape with the values taken out of it.
_VALUE = re.compile(r"\d+|'[^']*'")

#: How many statements a request carrying this many mappings makes, which is
#: what the address this one replaces makes for the same request: one look for
#: a name nobody holds and one write per place a name is kept, for every
#: mapping, on both. The numbers are measured rather than worked out, because
#: the statements that open and release the part of a request one mapping can
#: have undone on its own are not issued for every one of them.
MAPPING_COSTS = {1: 24, 2: 36, 10: 125}


@skip_unless_lms
class ReplacementCostParityTests(ReplacementParityTestCase):
    """Both addresses cost the same, and both cost one mapping's worth per mapping."""

    def statements(self, address, mappings):
        """Return what each statement of this request does, without the values."""
        with CaptureQueriesContext(connection) as captured:
            answered = self.ask(address, mappings)
        assert answered.status_code == status.HTTP_200_OK, (address, answered.content)
        return [_VALUE.sub("?", query["sql"]) for query in captured.captured_queries]

    def mappings(self, count, address):
        """Return this many mappings, each naming an account nobody else holds."""
        return [
            {f"cost-{address == EARLIER}-{index}": f"renamed-{address == EARLIER}-{index}"}
            for index in range(count)
        ]

    def measured(self, count):
        """Return the statements each address makes for a request carrying this many mappings."""
        measured = {}
        for address in self.addresses():
            for mapping in self.mappings(count, address):
                UserFactory.create(username=next(iter(mapping)))
            # The first request of a process fills caches every later one
            # reads instead of the database, so each address is driven once
            # before it is measured.
            self.ask(address, [{"warming-up": "warmed-up"}])
            measured[address] = self.statements(address, self.mappings(count, address))
        return measured

    def test_both_addresses_make_the_same_statements_for_one_mapping(self):
        measured = self.measured(1)
        assert measured[EARLIER] == measured["conforming"]

    def test_both_addresses_cost_the_same_for_every_mapping_added(self):
        """A read per mapping that only one of them makes would show up here as a longer list."""
        counts = {}
        for count in (1, 2, 10):
            measured = self.measured(count)
            assert measured[EARLIER] == measured["conforming"], count
            counts[count] = len(measured[EARLIER])
        assert counts == MAPPING_COSTS


#: Everything a caller can notice about either address, as a measurement made
#: at both of them, named by the method that makes it. What that method
#: answers is compared across the two addresses, so a measurement that comes
#: back different while no difference is declared for it fails, and a declared
#: difference both addresses answer the same way fails too.
MEASURED_AT_BOTH = (
    ("the answer", "measure_the_shape_of_the_answer"),
    ("a name an account is stored under", "measure_a_name_an_account_is_stored_under"),
    ("a name another account holds", "measure_a_name_another_account_holds"),
    ("a name no account is stored under", "measure_a_name_no_account_is_stored_under"),
    ("a request carrying no mappings at all", "measure_a_request_carrying_no_mappings_at_all"),
    ("a mapping naming several accounts", "measure_a_mapping_naming_several_accounts"),
    ("mappings sent as something other than a list", "measure_mappings_sent_as_something_else"),
    ("a body sent as a web form", "measure_a_body_sent_as_a_web_form"),
    ("the most mappings one request may carry", "measure_the_most_mappings_one_request_may_carry"),
    ("space around the name asked for", "measure_space_around_the_name_asked_for"),
    ("asking what the address serves", "measure_asking_what_the_address_serves"),
    ("a write that fails part way through", "measure_a_write_that_fails_part_way_through"),
)


@skip_unless_lms
class DeclaredDifferenceTests(ReplacementParityTestCase):
    """
    The differences between the two addresses are the ones written down.

    Each measurement is made at both addresses and the two answers are held
    against each other, so the list of differences is read from what the
    service does rather than restated beside itself. Where the accounts a
    measurement acts on have to differ between the addresses, what it answers
    is a statement about them rather than their names.
    """

    def measure_the_shape_of_the_answer(self, address):
        """Return what the answer is made of for a mapping naming an account."""
        held = UserFactory.create(username=f"shape-{address == EARLIER}")
        answered = self.ask(address, [{held.username: f"shaped-{address == EARLIER}"}])
        return (answered.status_code, sorted(answered.json()))

    def measure_a_name_an_account_is_stored_under(self, address):
        """Return whether the account is stored under the name asked for it afterwards."""
        held = UserFactory.create(username=f"stored-{address == EARLIER}")
        asked_for = f"renamed-{address == EARLIER}"
        answered = self.ask(address, [{held.username: asked_for}])
        return (answered.status_code, User.objects.get(id=held.id).username == asked_for)

    def measure_a_name_another_account_holds(self, address):
        """Return whether the name handed out is the one asked for with a suffix on it."""
        held = UserFactory.create(username=f"asking-{address == EARLIER}")
        taken = UserFactory.create(username=f"held-already-{address == EARLIER}")
        answered = self.with_a_fixed_suffix(
            lambda: self.ask(address, [{held.username: taken.username}]),
        )
        return (
            answered.status_code,
            User.objects.get(id=held.id).username == f"{taken.username}-{FIXED_SUFFIX}",
        )

    def measure_a_name_no_account_is_stored_under(self, address):
        """Return what the answer says about a name nothing is stored under."""
        answered = self.ask(address, [{"nobody-is-stored-under-this": "asked-for-nobody"}])
        return (answered.status_code, answered.json())

    def measure_a_request_carrying_no_mappings_at_all(self, address):
        """Return what each address answers a request that asks for nothing."""
        return self.ask(address, []).status_code

    def measure_a_mapping_naming_several_accounts(self, address):
        """Return what each address answers a mapping that names more than one account."""
        return self.ask(address, [{"one": "first", "two": "second"}]).status_code

    def measure_mappings_sent_as_something_else(self, address):
        """Return what each address answers mappings that are not a list of them."""
        return self.ask(address, {"one": "first"}).status_code

    def measure_a_body_sent_as_a_web_form(self, address):
        """Return what each address answers a body sent as a web form."""
        return self.ask(address, [], as_form=True).status_code

    def measure_the_most_mappings_one_request_may_carry(self, address):
        """Return what each address answers one mapping more than a page holds."""
        mappings = [
            {f"over-{address == EARLIER}-{index}": f"renamed-over-{address == EARLIER}-{index}"}
            for index in range(REPLACEMENT_MAX_MAPPINGS + 1)
        ]
        return self.ask(address, mappings).status_code

    def measure_space_around_the_name_asked_for(self, address):
        """Return the name stored for an account whose new name carried space around it."""
        held = UserFactory.create(username=f"padded-{address == EARLIER}")
        self.ask(address, [{held.username: "  padded name  "}])
        return User.objects.get(id=held.id).username

    def measure_asking_what_the_address_serves(self, address):
        """
        Return what each address answers a request for its own description.

        The description is each address's own text and differs by design, so
        what is measured is whether a caller holding the capability is given
        one at all.
        """
        return self.client.options(self.url_of(address)).status_code

    def measure_a_write_that_fails_part_way_through(self, address):
        """Return what each address answers when one place a name is kept cannot be written."""
        held = UserFactory.create(username=f"failing-{address == EARLIER}")
        broken = mock.Mock()
        broken.filter.return_value.update.side_effect = DatabaseError(
            "the rows of that table would not take it",
        )
        with mock.patch.object(CreditEligibility, "objects", broken):
            answered = self.ask(address, [{held.username: f"failed-{address == EARLIER}"}])
        return (answered.status_code, User.objects.get(id=held.id).username == held.username)

    def test_the_differences_a_caller_would_notice_are_the_ones_declared(self):
        """A measured difference that is not declared, or declared and not measured, fails."""
        measured = {}
        for difference, measurement in MEASURED_AT_BOTH:
            measured[difference] = {
                address: getattr(self, measurement)(address) for address in self.addresses()
            }
        assert [
            difference for difference, answers in measured.items()
            if answers[EARLIER] != answers["conforming"]
        ] == [difference for difference, _reason in DECLARED_DIFFERENCES]

    def test_every_declared_difference_says_what_each_address_does(self):
        """The reason is read by whoever moves a caller from one address to the other."""
        for difference, reason in DECLARED_DIFFERENCES:
            with self.subTest(difference=difference):
                assert "the earlier address" in reason
                assert "the conforming one" in reason
