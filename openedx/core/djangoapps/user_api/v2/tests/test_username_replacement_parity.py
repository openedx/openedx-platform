"""Behaviour parity between the two addresses the names of a set of accounts are replaced at."""

import json
import re
from unittest import mock

import pytest
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import DatabaseError, connection, connections, transaction
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
from openedx.core.djangoapps.user_api.v2.paginators import UserApiPagination
from openedx.core.djangoapps.user_api.v2.serializers import (
    REPLACEMENT_FAILED,
    REPLACEMENT_NO_SUCH_ACCOUNT,
    REPLACEMENT_REPLACED,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_username_replacement_views import LONGEST_NAME, refusing_names_longer_than_their_column

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
        "a body sent as a web form",
        "the earlier address answers that the body does not match what it reads, the conforming "
        "one that it cannot read the form",
    ),
    (
        "space around the name asked for",
        "the earlier address stores the name with the space it was given with, the conforming one "
        "stores what is left once the space on either end is taken off",
    ),
    (
        "a fault of this server part way through",
        "the earlier address reports the mapping it was carrying out as failed and keeps the rest, "
        "the conforming one answers this server's failure and keeps none of them, whether or not "
        "the deployment runs each request in a transaction",
    ),
)

#: A name no account is stored under.
NOBODY = "nobody"

#: How many mappings the replacing job sends when it was given more than a
#: page of any collection holds: it sends every one of them in one request.
MORE_THAN_A_PAGE = UserApiPagination.max_page_size + 1

#: Names of every shape the earlier address writes, each asked for an account
#: of its own, beside a null asked for a name no account is stored under. The
#: null is the one the database refuses for an account; the long name is
#: taken by the database these tests run on and refused by one holding a name
#: to its column, which is measured apart.
EVERY_SHAPE_OF_NAME = (
    {"slashed": "has/a/slash#and o'brien"},
    {"lengthy": "n" * (LONGEST_NAME + 1)},
    {"emptied": ""},
    {"nulled": None},
    {"plain": "plain-renamed"},
    {NOBODY: None},
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

    def failing_the_write_for(self, name, failure):
        """Fail the rewrite of the eligibility kept under this name, and of no other, with this."""
        rewrites = CreditEligibility.objects.filter

        def rewrite(**selected):
            if selected.get("username") == name:
                raise failure("the rows of that table would not take it")
            return rewrites(**selected)

        return mock.patch.object(CreditEligibility.objects, "filter", side_effect=rewrite)

    def fail_part_way_through(self, address, in_a_transaction, failure=DatabaseError):
        """
        Return what this address answers and keeps when the second of two mappings fails with this.

        That is the status, whether the first account is stored under the
        name asked for it, and whether the second is stored under the name it
        had. The deployment decides whether each request runs in a
        transaction, so it is measured both ways.
        """
        named = f"{address == EARLIER}-{in_a_transaction}"
        carried = UserFactory.create(username=f"carried-{named}")
        failing = UserFactory.create(username=f"failing-{named}")
        with self.failing_the_write_for(failing.username, failure), mock.patch.dict(
            connections.settings["default"], {"ATOMIC_REQUESTS": in_a_transaction},
        ):
            answered = self.ask(
                address, [{carried.username: f"renamed-{named}"}, {failing.username: f"failed-{named}"}],
            )
        return (
            answered.status_code,
            User.objects.get(id=carried.id).username == f"renamed-{named}",
            User.objects.get(id=failing.id).username == failing.username,
        )

    def carried_out_alone(self, address, mappings):
        """
        Return what this address answers these mappings over accounts of their own, then undo it.

        Every name a mapping replaces but ``NOBODY`` is given an account, the
        request is sent, and the name each account is stored under afterwards
        is read; then all of it is rolled back, so the other address is sent
        the very same mappings over the very same accounts.
        """
        with transaction.atomic():
            held = {
                current: UserFactory.create(username=current)
                for mapping in mappings for current in mapping if current != NOBODY
            }
            answered = self.ask(address, list(mappings))
            stored = {current: User.objects.get(id=user.id).username for current, user in held.items()}
            transaction.set_rollback(True)
        return answered, stored

    def failures_reported(self, address, answered):
        """Return the names this address reported as failed, in the order it reported them."""
        if address == EARLIER:
            return [current for mapping in answered.json()["failed_replacements"] for current in mapping]
        return [
            result["current_username"] for result in answered.json()["results"]
            if result["outcome"] == REPLACEMENT_FAILED
        ]


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

    def test_a_request_carrying_no_mappings_at_all_is_answered_by_both(self):
        """The replacing job sends an empty list when it was given no mapping."""
        answered = {address: self.ask(address, []) for address in self.addresses()}
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_200_OK,
        )
        assert answered[EARLIER].json() == {"successful_replacements": [], "failed_replacements": []}
        assert answered["conforming"].json() == {"results": []}

    def test_more_mappings_than_a_page_holds_are_carried_out_by_both(self):
        """
        The replacing job reads every mapping it was given and sends them in one request.

        Each address is sent the very same mappings over the very same
        accounts, and both store every account under the name asked for it.
        """
        mappings = [{f"many-{index}": f"renamed-many-{index}"} for index in range(MORE_THAN_A_PAGE)]
        asked_for = {current: new for mapping in mappings for current, new in mapping.items()}
        answered, stored = {}, {}
        for address in self.addresses():
            answered[address], stored[address] = self.carried_out_alone(address, mappings)
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_200_OK,
        )
        assert stored == dict.fromkeys(answered, asked_for)
        assert answered[EARLIER].json() == {"successful_replacements": mappings, "failed_replacements": []}
        assert answered["conforming"].json() == {
            "results": [
                {"current_username": current, "new_username": new, "outcome": REPLACEMENT_REPLACED}
                for current, new in asked_for.items()
            ],
        }

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

    def test_a_write_the_database_refuses_part_way_through_is_reported_alike_by_both(self):
        """Both report that mapping as failed and keep the rest, however requests run."""
        for in_a_transaction in (True, False):
            with self.subTest(in_a_transaction=in_a_transaction):
                for address in self.addresses():
                    assert self.fail_part_way_through(address, in_a_transaction) == (
                        status.HTTP_200_OK, True, True,
                    ), address

    def test_a_fault_part_way_through_is_answered_differently(self):
        """The earlier address keeps the rest; the conforming one keeps none, however requests run."""
        for in_a_transaction in (True, False):
            with self.subTest(in_a_transaction=in_a_transaction):
                assert self.fail_part_way_through(EARLIER, in_a_transaction, ValueError) == (
                    status.HTTP_200_OK, True, True,
                )
                assert self.fail_part_way_through("conforming", in_a_transaction, ValueError) == (
                    status.HTTP_500_INTERNAL_SERVER_ERROR, False, True,
                )

    def test_names_of_every_shape_are_carried_out_alike_by_both(self):
        """The same mappings over the same accounts: each address writes the same names and fails the same one."""
        answered, stored = {}, {}
        for address in self.addresses():
            answered[address], stored[address] = self.carried_out_alone(address, EVERY_SHAPE_OF_NAME)
        written = {
            "slashed": "has/a/slash#and o'brien",
            "lengthy": "n" * (LONGEST_NAME + 1),
            "emptied": "",
            "nulled": "nulled",
            "plain": "plain-renamed",
        }
        assert stored == {EARLIER: written, "conforming": written}
        assert {address: self.failures_reported(address, sent) for address, sent in answered.items()} == {
            EARLIER: ["nulled"], "conforming": ["nulled"],
        }
        assert answered[EARLIER].json() == {
            "successful_replacements": [
                {"slashed": written["slashed"]},
                {"lengthy": written["lengthy"]},
                {"emptied": ""},
                {"plain": "plain-renamed"},
                {NOBODY: None},
            ],
            "failed_replacements": [{"nulled": None}],
        }
        assert answered["conforming"].json() == {"results": [
            {"current_username": "slashed", "new_username": written["slashed"], "outcome": REPLACEMENT_REPLACED},
            {"current_username": "lengthy", "new_username": written["lengthy"], "outcome": REPLACEMENT_REPLACED},
            {"current_username": "emptied", "new_username": "", "outcome": REPLACEMENT_REPLACED},
            {"current_username": "nulled", "new_username": None, "outcome": REPLACEMENT_FAILED},
            {"current_username": "plain", "new_username": "plain-renamed", "outcome": REPLACEMENT_REPLACED},
            {"current_username": NOBODY, "new_username": None, "outcome": REPLACEMENT_NO_SUCH_ACCOUNT},
        ]}

    def test_a_name_longer_than_its_column_is_failed_by_both_where_the_database_refuses_it(self):
        mappings = ({"lengthy": "n" * (LONGEST_NAME + 1)}, {"plain": "plain-renamed"})
        answered, stored = {}, {}
        with refusing_names_longer_than_their_column():
            for address in self.addresses():
                answered[address], stored[address] = self.carried_out_alone(address, mappings)
        assert stored == dict.fromkeys(stored, {"lengthy": "lengthy", "plain": "plain-renamed"})
        assert {address: self.failures_reported(address, sent) for address, sent in answered.items()} == {
            EARLIER: ["lengthy"], "conforming": ["lengthy"],
        }


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

#: How many statements a request carrying this many mappings makes at the
#: address this one replaces: one look for a name nobody holds and one write per
#: place a name is kept, for every mapping. The numbers are measured rather than
#: worked out, because the statements that open and release the part of a
#: request one mapping can have undone on its own are not issued for every one
#: of them.
MAPPING_COSTS = {1: 24, 2: 36, 10: 125}

#: The look for a name nobody holds, which is the first statement of a mapping.
NAME_LOOKUP = 'SELECT ? AS "a" FROM "auth_user" WHERE "auth_user"."username" = ? LIMIT ?'

#: The statements opening and closing a transaction inside a request.
OPENS_A_TRANSACTION = 'SAVEPOINT "s?_x?"'
CLOSES_A_TRANSACTION = 'RELEASE SAVEPOINT "s?_x?"'


@skip_unless_lms
class ReplacementCostParityTests(ReplacementParityTestCase):
    """
    Both addresses cost one mapping's worth per mapping, and the same statements for it.

    The conforming address adds the two statements that open and close the
    one transaction it carries every mapping out in, whatever their number.
    """

    def without_the_transaction_around_the_mappings(self, statements):
        """Return the statements without the transaction opened before the first mapping and closed after the last."""
        opened = statements.index(NAME_LOOKUP) - 1
        closed = max(index for index, sql in enumerate(statements) if sql.startswith("UPDATE ")) + 2
        assert [statements[opened], statements[closed]] == [OPENS_A_TRANSACTION, CLOSES_A_TRANSACTION]
        return statements[:opened] + statements[opened + 1:closed] + statements[closed + 1:]

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
        assert self.without_the_transaction_around_the_mappings(measured["conforming"]) == measured[EARLIER]

    def test_both_addresses_cost_the_same_for_every_mapping_added(self):
        """A read per mapping that only one of them makes would show up here as a longer list."""
        counts = {}
        for count in (1, 2, 10):
            measured = self.measured(count)
            assert self.without_the_transaction_around_the_mappings(measured["conforming"]) == (
                measured[EARLIER]
            ), count
            counts[count] = (len(measured[EARLIER]), len(measured["conforming"]))
        assert counts == {count: (cost, cost + 2) for count, cost in MAPPING_COSTS.items()}


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
    ("more mappings than a page holds", "measure_more_mappings_than_a_page_holds"),
    ("space around the name asked for", "measure_space_around_the_name_asked_for"),
    ("names of every shape", "measure_names_of_every_shape"),
    (
        "a name longer than its column where the database refuses it",
        "measure_a_name_longer_than_its_column_where_the_database_refuses_it",
    ),
    ("asking what the address serves", "measure_asking_what_the_address_serves"),
    ("a write the database refuses part way through", "measure_a_write_the_database_refuses_part_way_through"),
    ("a fault of this server part way through", "measure_a_fault_of_this_server_part_way_through"),
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
        """Return what each address answers a request that asks for nothing, and how many mappings it reports."""
        answered = self.ask(address, [])
        return (answered.status_code, sum(len(reported) for reported in answered.json().values()))

    def measure_a_mapping_naming_several_accounts(self, address):
        """Return what each address answers a mapping that names more than one account."""
        return self.ask(address, [{"one": "first", "two": "second"}]).status_code

    def measure_mappings_sent_as_something_else(self, address):
        """Return what each address answers mappings that are not a list of them."""
        return self.ask(address, {"one": "first"}).status_code

    def measure_a_body_sent_as_a_web_form(self, address):
        """Return what each address answers a body sent as a web form."""
        return self.ask(address, [], as_form=True).status_code

    def measure_more_mappings_than_a_page_holds(self, address):
        """Return the status, the name each account is stored under and the names reported as failed."""
        answered, stored = self.carried_out_alone(
            address, [{f"over-{index}": f"renamed-over-{index}"} for index in range(MORE_THAN_A_PAGE)],
        )
        return (answered.status_code, stored, self.failures_reported(address, answered))

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

    def measure_names_of_every_shape(self, address):
        """Return the status, the name each account is stored under and the names reported as failed."""
        answered, stored = self.carried_out_alone(address, EVERY_SHAPE_OF_NAME)
        return (answered.status_code, stored, self.failures_reported(address, answered))

    def measure_a_name_longer_than_its_column_where_the_database_refuses_it(self, address):
        """Return the same, where the database holds a name to its column."""
        with refusing_names_longer_than_their_column():
            answered, stored = self.carried_out_alone(
                address, ({"lengthy": "n" * (LONGEST_NAME + 1)}, {"plain": "plain-renamed"}),
            )
        return (answered.status_code, stored, self.failures_reported(address, answered))

    def measure_a_write_the_database_refuses_part_way_through(self, address):
        """Return what each address answers and keeps, with and without a transaction per request."""
        return {
            in_a_transaction: self.fail_part_way_through(address, in_a_transaction)
            for in_a_transaction in (True, False)
        }

    def measure_a_fault_of_this_server_part_way_through(self, address):
        """Return the same where the failure is not the database refusing a write."""
        return {
            in_a_transaction: self.fail_part_way_through(address, in_a_transaction, ValueError)
            for in_a_transaction in (True, False)
        }

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
