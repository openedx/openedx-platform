"""Tests for the selections version 2 of the User API answers from."""

import datetime
from unittest import mock

import pytest
from django.conf import settings
from django.test import TestCase

from common.djangoapps.student.models import get_retired_username_by_username
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus,
)
from openedx.core.djangoapps.user_api.v2.selectors import (
    accounts_holding_emails,
    cancellable_retirement_for_email,
    partner_reports_requested_as,
    retirement_of_the_account_named,
    retirements,
    retirements_requested_as,
    unclaimed_partner_reports,
    username_holding_id,
    usernames_among,
    usernames_holding_email,
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
        assert usernames_holding_email("second@example.com") == ["second-holder"]

    def test_no_account_holds_an_unregistered_address(self):
        assert usernames_holding_email("nobody@example.com") == []

    def test_every_account_holding_one_address_is_named(self):
        """Nothing keeps one address to one account, so an address can be held by several."""
        UserFactory.create(username="also-second", email="second@example.com")
        assert usernames_holding_email("second@example.com") == ["also-second", "second-holder"]

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


@skip_unless_lms
@pytest.mark.usefixtures("setup_retirement_states")
class RetirementPipelineSelectionTests(TestCase):
    """What the retirement selections answer, asked for outside a request."""

    def setUp(self):
        super().setUp()
        self.subject = UserFactory.create(username="pipeline-subject", email="subject@example.com")
        self.retirement = create_retirement_status(self.subject)

    def test_the_queue_carries_the_account_and_both_steps_without_asking_again(self):
        selected = retirements().get(id=self.retirement.id)
        with self.assertNumQueries(0):
            assert selected.user.username == "pipeline-subject"
            assert selected.user.profile.name == self.subject.profile.name
            assert selected.current_state.state_name == "PENDING"
            assert selected.last_state.state_name == "PENDING"

    def test_an_account_is_addressed_by_the_name_it_was_registered_under(self):
        assert retirement_of_the_account_named("pipeline-subject").id == self.retirement.id

    def test_an_account_is_addressed_by_the_hashed_name_it_was_moved_to(self):
        hashed = get_retired_username_by_username("pipeline-subject")
        self.subject.username = hashed
        self.subject.save()
        assert retirement_of_the_account_named(hashed).id == self.retirement.id
        assert retirement_of_the_account_named("pipeline-subject").id == self.retirement.id

    def test_a_name_no_account_holds_addresses_no_retirement(self):
        assert retirement_of_the_account_named("nobody") is None

    def test_an_account_with_no_retirement_behind_it_addresses_none(self):
        UserFactory.create(username="not-retiring")
        assert retirement_of_the_account_named("not-retiring") is None

    def test_a_retirement_is_found_by_the_name_it_was_requested_under(self):
        found = retirements_requested_as(["pipeline-subject", "nobody"])
        assert set(found) == {"pipeline-subject"}
        assert found["pipeline-subject"].id == self.retirement.id

    def test_a_name_spelled_differently_finds_no_retirement(self):
        """Stored names are compared without regard to case in some deployments, so this is exact."""
        assert retirements_requested_as(["Pipeline-Subject"]) == {}

    def test_both_retirements_of_a_pair_differing_only_in_case_are_found_separately(self):
        other = UserFactory.create(username="PIPELINE-SUBJECT", email="other@example.com")
        other_retirement = create_retirement_status(other)
        found = retirements_requested_as(["pipeline-subject", "PIPELINE-SUBJECT"])
        assert found["pipeline-subject"].id == self.retirement.id
        assert found["PIPELINE-SUBJECT"].id == other_retirement.id

    def test_the_partner_report_queue_leaves_out_what_has_been_taken(self):
        waiting = self.queued(self.subject)
        taken_user = UserFactory.create(username="already-taken")
        self.queued(taken_user, taken=True)
        assert [report.id for report in unclaimed_partner_reports()] == [waiting.id]

    def test_the_partner_report_queue_carries_the_courses_each_account_studied(self):
        self.queued(self.subject)
        CourseEnrollmentFactory.create(user=self.subject, course_id="course-v1:edX+DemoX+Demo")
        report = list(unclaimed_partner_reports())[0]
        with self.assertNumQueries(0):
            assert [
                enrolment.course_id.org for enrolment in report.user.courseenrollment_set.all()
            ] == ["edX"]

    def test_a_partner_report_is_found_by_the_name_it_was_registered_under(self):
        report = self.queued(self.subject)
        found = partner_reports_requested_as(["pipeline-subject", "nobody"])
        assert set(found) == {"pipeline-subject"}
        assert found["pipeline-subject"].id == report.id

    def test_a_partner_report_is_not_found_by_a_name_spelled_differently(self):
        self.queued(self.subject)
        assert partner_reports_requested_as(["Pipeline-Subject"]) == {}

    def queued(self, user, taken=False):
        """Return a partner report entry for this account, taken for processing or not."""
        return UserRetirementPartnerReportingStatus.objects.create(
            user=user,
            original_username=user.username,
            original_email=user.email,
            original_name=user.profile.name,
            is_being_processed=taken,
        )


class _CollationIgnoringCase:
    """
    Stands in for a database that ignores case when it compares stored names.

    The deployments this runs on do exactly that, which once let a learner
    register a retired name again by changing its capitalisation; the database
    the tests run on compares names character for character, so what the
    selections do with a second row that differs only in case is settled here
    rather than left to the database to decide.
    """

    def __init__(self, rows):
        self.rows = rows

    def filter(self, **kwargs):  # pylint: disable=unused-argument
        """Return every row, whichever names were asked for."""
        return self.rows


@skip_unless_lms
@pytest.mark.usefixtures("setup_retirement_states")
class CaseExactSelectionTests(TestCase):
    """A name is compared character for character, whatever the database would compare."""

    def setUp(self):
        super().setUp()
        self.lower = UserFactory.create(username="exact-subject", email="lower@example.com")
        self.upper = UserFactory.create(username="EXACT-SUBJECT", email="upper@example.com")

    def test_a_retirement_is_selected_by_the_spelling_asked_for(self):
        wanted = create_retirement_status(self.lower)
        create_retirement_status(self.upper)
        offered = _CollationIgnoringCase(list(UserRetirementStatus.objects.all()))
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.selectors.retirements", return_value=offered,
        ):
            found = retirements_requested_as(["exact-subject"])
        assert set(found) == {"exact-subject"}
        assert found["exact-subject"].id == wanted.id

    def test_a_partner_report_is_selected_by_the_spelling_asked_for(self):
        wanted = UserRetirementPartnerReportingStatus.objects.create(
            user=self.lower,
            original_username=self.lower.username,
            original_email=self.lower.email,
            original_name=self.lower.profile.name,
        )
        UserRetirementPartnerReportingStatus.objects.create(
            user=self.upper,
            original_username=self.upper.username,
            original_email=self.upper.email,
            original_name=self.upper.profile.name,
        )
        offered = _CollationIgnoringCase(list(UserRetirementPartnerReportingStatus.objects.all()))
        with mock.patch.object(
            UserRetirementPartnerReportingStatus, "objects", mock.Mock(filter=offered.filter),
        ):
            found = partner_reports_requested_as(["exact-subject"])
        assert set(found) == {"exact-subject"}
        assert found["exact-subject"].id == wanted.id
