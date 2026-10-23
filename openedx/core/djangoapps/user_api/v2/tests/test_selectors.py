"""Tests for the selections version 2 of the User API answers from."""

import datetime

from django.conf import settings
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus
from openedx.core.djangoapps.user_api.v2.selectors import (
    accounts_holding_emails,
    cancellable_retirement_for_email,
    username_holding_email,
    username_holding_id,
    usernames_among,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class AccountSelectionTests(TestCase):
    """What the account selections answer, asked for outside a request."""

    def setUp(self):
        super().setUp()
        self.first = UserFactory.create(username="first-holder", email="first@example.com")
        self.second = UserFactory.create(username="second-holder", email="second@example.com")

    def test_the_accounts_holding_a_set_of_addresses_come_back_by_name(self):
        found = accounts_holding_emails(["second@example.com", "first@example.com"])
        assert [user.username for user in found] == ["first-holder", "second-holder"]

    def test_an_address_no_account_holds_is_left_out(self):
        found = accounts_holding_emails(["first@example.com", "nobody@example.com"])
        assert [user.username for user in found] == ["first-holder"]

    def test_no_addresses_find_no_accounts(self):
        assert not list(accounts_holding_emails([]))

    def test_the_accounts_named_come_back_in_the_order_asked_for(self):
        assert list(usernames_among(["second-holder", "first-holder"], "username")) == [
            "first-holder", "second-holder",
        ]
        assert list(usernames_among(["first-holder", "second-holder"], "-username")) == [
            "second-holder", "first-holder",
        ]

    def test_a_name_no_account_holds_is_left_out_of_the_selection(self):
        assert list(usernames_among(["first-holder", "nobody-at-all"], "username")) == ["first-holder"]

    def test_no_names_select_no_accounts(self):
        assert not list(usernames_among([], "username"))

    def test_the_account_holding_one_address_is_named(self):
        assert username_holding_email("second@example.com") == "second-holder"

    def test_no_account_holds_an_unregistered_address(self):
        assert username_holding_email("nobody@example.com") is None

    def test_the_account_holding_one_identifier_is_named(self):
        assert username_holding_id(self.second.id) == "second-holder"

    def test_no_account_holds_an_unused_identifier(self):
        assert username_holding_id(self.second.id + 1000) is None


@skip_unless_lms
class RetirementSelectionTests(TestCase):
    """Which retirement of a retired address can still be cancelled."""

    def setUp(self):
        super().setUp()
        RetirementState.objects.create(state_name="PENDING", state_execution_order=1)
        self.user = UserFactory.create(username="leaving", email="leaving@example.com")

    def age(self, retirement, days):
        """Record the given retirement as having been asked for that many days ago."""
        asked = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
        UserRetirementStatus.objects.filter(pk=retirement.pk).update(created=asked)

    def test_a_retirement_inside_the_cooling_off_period_is_the_one_reported(self):
        retirement = UserRetirementStatus.create_retirement(self.user)
        found = cancellable_retirement_for_email("leaving@example.com")
        assert found is not None
        assert found.pk == retirement.pk
        assert found.original_email == "leaving@example.com"

    def test_a_retirement_older_than_the_cooling_off_period_is_not_reported(self):
        retirement = UserRetirementStatus.create_retirement(self.user)
        self.age(retirement, settings.COOL_OFF_DAYS + 1)
        assert cancellable_retirement_for_email("leaving@example.com") is None

    def test_an_address_with_no_retirement_behind_it_has_none(self):
        UserRetirementStatus.create_retirement(self.user)
        assert cancellable_retirement_for_email("staying@example.com") is None

    def test_the_most_recently_asked_for_retirement_of_an_address_is_the_one_reported(self):
        """One address can have been held and left more than once; the last one is cancellable."""
        earlier = UserRetirementStatus.create_retirement(self.user)
        self.age(earlier, 2)
        again = UserFactory.create(username="leaving-again", email="leaving-again@example.com")
        later = UserRetirementStatus.create_retirement(again)
        UserRetirementStatus.objects.filter(pk=later.pk).update(original_email="leaving@example.com")
        found = cancellable_retirement_for_email("leaving@example.com")
        assert found.pk == later.pk
        assert found.user_id == again.id
