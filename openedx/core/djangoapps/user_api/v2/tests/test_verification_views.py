"""Tests for the version 2 User API identity verification views."""

import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import (
    ManualVerification,
    SoftwareSecurePhotoVerification,
    VerificationAttempt,
)
from lms.djangoapps.verify_student.tests.factories import SSOVerificationFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_views import PAGINATION_KEYS, write_queries

PASSWORD = "Password1234"

#: Identity checks are good for a year on this site, and a user is asked to go
#: through one again in the three weeks before an accepted proof runs out.
VERIFY_STUDENT = {"DAYS_GOOD_FOR": 365, "EXPIRING_SOON_WINDOW": 20}


def earlier_status_address(username):
    """Return the version 1 address of one user's verification status."""
    return f"/api/user/v1/accounts/{username}/verification_status/"


def earlier_list_address(username):
    """Return the version 1 address of one user's verification attempts."""
    return f"/api/user/v1/accounts/{username}/verifications/"


@override_settings(VERIFY_STUDENT=VERIFY_STUDENT)
class VerificationTestCase(APITestCase):
    """Fixtures shared by the version 2 identity verification tests."""

    def setUp(self):
        super().setUp()
        self.owner = UserFactory.create(
            username="verify-owner", email="verify-owner@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="verify-other", email="verify-other@example.com", password=PASSWORD,
        )
        self.staff = UserFactory.create(
            username="verify-staff", email="verify-staff@example.com", password=PASSWORD, is_staff=True,
        )
        self.attempt = SoftwareSecurePhotoVerification.objects.create(
            user=self.owner, status="submitted",
        )
        self.status_url = self.status_address(self.owner.username)
        self.list_url = self.list_address(self.owner.username)

    def status_address(self, username):
        """Return the address of one user's verification status."""
        return reverse("user_v2:account_verification_status", kwargs={"username": username})

    def list_address(self, username):
        """Return the address of one user's verification attempts."""
        return reverse("user_v2:account_verification_list", kwargs={"username": username})

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
            username="verify-variant", email="verify-variant@example.com", password=PASSWORD,
        )
        caller.username = user.username.upper()
        self.client.force_authenticate(user=caller)
        return caller

    def approve(self, attempt, expires_in_days=200):
        """Record an attempt as accepted, running out the given number of days from now."""
        attempt.status = "approved"
        attempt.expiration_date = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(
            days=expires_in_days,
        )
        attempt.save()
        return attempt

    def run_out(self, attempt, days_ago=1):
        """Record an attempt as accepted and no longer good, the given number of days ago."""
        return self.approve(attempt, expires_in_days=-days_ago)


@skip_unless_lms
class VerificationStatusTests(VerificationTestCase):
    """GET /api/user/v2/accounts/{username}/verification_status/"""

    def test_a_user_reads_their_own_status(self):
        self.login(self.owner)
        response = self.client.get(self.status_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "is_verified": False, "status": "pending", "expiration_datetime": "",
        }

    def test_an_accepted_proof_is_reported_as_verified(self):
        self.approve(self.attempt)
        self.login(self.owner)
        response = self.client.get(self.status_url)
        assert response.data == {
            "is_verified": True, "status": "approved", "expiration_datetime": "",
        }

    def test_an_accepted_proof_about_to_run_out_carries_the_day_it_does(self):
        """The day is published only once the user is near being asked to verify again."""
        attempt = self.approve(self.attempt, expires_in_days=10)
        self.login(self.owner)
        response = self.client.get(self.status_url)
        assert response.data["status"] == "approved"
        assert response.data["is_verified"] is True
        assert response.data["expiration_datetime"] == attempt.expiration_datetime.date().strftime(
            "%m/%d/%Y",
        )

    def test_the_summary_reports_the_day_and_the_attempt_the_moment_it_runs_out(self):
        """
        The property of this name carries a day in the summary and a moment in the attempt.

        The summary reports the day the user is told about; the attempt holding
        the proof records when it stops counting, to the second.
        """
        attempt = self.approve(self.attempt, expires_in_days=10)
        self.login(self.owner)
        summary = self.client.get(self.status_url)
        listed = self.client.get(self.list_url).data["results"][0]["expiration_datetime"]
        assert summary.data["expiration_datetime"] == attempt.expiration_datetime.date().strftime(
            "%m/%d/%Y",
        )
        assert listed.startswith(attempt.expiration_datetime.strftime("%Y-%m-%dT%H:%M:%S"))

    def test_a_turned_down_proof_asks_the_user_to_try_again(self):
        self.attempt.status = "denied"
        self.attempt.error_msg = '[{"userPhotoReasons": ["Face out of view"]}]'
        self.attempt.save()
        self.login(self.owner)
        response = self.client.get(self.status_url)
        assert response.data["status"] == "must_reverify"
        assert response.data["is_verified"] is False

    def test_a_proof_that_is_no_longer_good_is_reported_as_run_out(self):
        self.run_out(self.attempt)
        self.login(self.owner)
        response = self.client.get(self.status_url)
        assert response.data["status"] == "expired"
        assert response.data["is_verified"] is False

    def test_a_proof_the_user_was_never_shown_stands_at_none_once_it_runs_out(self):
        """A check whose outcome is never shown to its subject is as if it had not been made."""
        self.attempt.delete()
        self.run_out(ManualVerification.objects.create(
            user=self.owner, status="approved", reason="Checked in person",
        ))
        self.login(self.owner)
        response = self.client.get(self.status_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "none"
        assert response.data["is_verified"] is False

    def test_a_staff_caller_reads_another_users_status(self):
        self.login(self.staff)
        response = self.client.get(self.status_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "pending"

    def test_a_learner_may_not_read_another_users_status(self):
        self.login(self.other)
        assert_error_envelope(
            self.client.get(self.status_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_a_refused_read_stays_refused_whatever_the_query_string_carries(self):
        """Who may read this status is settled from the address, before the query string is read."""
        self.login(self.other)
        for query in (
            f"username={self.other.username}",
            f"user={self.other.username}",
            "format=json",
        ):
            assert_error_envelope(
                self.client.get(f"{self.status_url}?{query}"),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_a_caller_named_for_the_address_in_case_alone_may_not_read_the_status(self):
        self.sign_in_as_a_case_variant_of(self.owner)
        assert_error_envelope(
            self.client.get(self.status_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_an_anonymous_caller_is_unauthenticated(self):
        assert_error_envelope(
            self.client.get(self.status_url),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )

    def test_a_user_who_has_never_been_checked_stands_at_none(self):
        """Nothing on record is something to report: the summary says so rather than going missing."""
        self.login(self.other)
        response = self.client.get(self.status_address(self.other.username))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "is_verified": False, "status": "none", "expiration_datetime": "",
        }

    def test_a_user_who_has_never_been_checked_is_told_apart_from_one_who_does_not_exist(self):
        self.login(self.staff)
        assert self.client.get(self.status_address(self.other.username)).status_code == status.HTTP_200_OK
        assert_error_envelope(
            self.client.get(self.status_address("nobody-at-all")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_an_unknown_username_is_not_found(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.get(self.status_address("nobody-at-all")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_an_unknown_query_parameter_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.get(f"{self.status_url}?expand=attempts"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "expand": ["No such query parameter. Send any of: format."],
        }

    def test_the_name_in_the_address_is_refused_in_the_query_string_as_well(self):
        """The address carries the username, so a parameter repeating it is one this view never reads."""
        for caller in (self.owner, self.staff):
            self.login(caller)
            envelope = assert_error_envelope(
                self.client.get(f"{self.status_url}?username={self.owner.username}"),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {
                "username": ["No such query parameter. Send any of: format."],
            }
            self.client.logout()

    def test_the_status_is_not_changed_through_this_address(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.post(self.status_url, {}, format="json"),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )


@skip_unless_lms
class VerificationListTests(VerificationTestCase):
    """GET /api/user/v2/accounts/{username}/verifications/"""

    def setUp(self):
        super().setUp()
        self.manual = ManualVerification.objects.create(
            user=self.owner, status="approved", reason="Checked in person",
        )

    def test_a_user_reads_their_own_attempts(self):
        self.login(self.owner)
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        assert {row["type"] for row in response.data["results"]} == {"Software Secure", "Manual"}

    def test_each_attempt_carries_what_was_recorded_about_it(self):
        self.login(self.owner)
        rows = {row["type"]: row for row in self.client.get(self.list_url).data["results"]}
        assert rows["Manual"]["status"] == "approved"
        assert rows["Manual"]["message"] == "Checked in person"
        assert rows["Manual"]["receipt_id"] is None
        assert rows["Software Secure"]["status"] == "submitted"
        assert rows["Software Secure"]["receipt_id"] == str(self.attempt.receipt_id)

    def test_each_kind_of_check_is_named_with_what_was_said_about_it(self):
        """The kind of check, the remark on it and the receipt are read from the record holding the attempt."""
        self.attempt.status = "denied"
        self.attempt.error_msg = '[{"userPhotoReasons": ["Face out of view"]}]'
        self.attempt.save()
        SSOVerificationFactory(user=self.owner, status="approved", identity_provider_slug="idp")
        self.login(self.owner)
        rows = {row["type"]: row for row in self.client.get(self.list_url).data["results"]}
        assert set(rows) == {"Software Secure", "Manual", "SSO"}
        assert rows["Software Secure"]["message"] == '[{"userPhotoReasons": ["Face out of view"]}]'
        assert rows["Software Secure"]["receipt_id"] == str(self.attempt.receipt_id)
        assert rows["Manual"]["message"] == "Checked in person"
        assert rows["Manual"]["receipt_id"] is None
        assert rows["SSO"]["message"] == ""
        assert rows["SSO"]["receipt_id"] is None

    def test_an_attempt_carries_the_status_the_table_holding_it_records(self):
        """
        The tables behind these attempts do not hold the same statuses, and each is carried as it stands.

        A photo check that failed to go through is held as ``must_retry``, and a
        check carried out elsewhere is held as ``pending`` while it is looked at.
        """
        self.attempt.status = "must_retry"
        self.attempt.save()
        VerificationAttempt.objects.create(user=self.owner, name="Verify Owner", status="pending")
        self.login(self.owner)
        rows = {row["type"]: row for row in self.client.get(self.list_url).data["results"]}
        assert set(rows) == {"Software Secure", "Manual", "SSO"}
        assert rows["Software Secure"]["status"] == "must_retry"
        assert rows["SSO"]["status"] == "pending"
        assert rows["SSO"]["message"] == ""
        assert rows["SSO"]["receipt_id"] is None
        assert rows["SSO"]["expiration_datetime"] is None

    def test_the_page_carries_the_whole_envelope(self):
        self.login(self.owner)
        response = self.client.get(self.list_url)
        assert set(response.data) == PAGINATION_KEYS
        assert response.data["count"] == 2
        assert response.data["num_pages"] == 1
        assert response.data["current_page"] == 1
        assert response.data["start"] == 0
        assert response.data["next"] is None
        assert response.data["previous"] is None

    def test_the_page_is_bounded(self):
        for index in range(5):
            SSOVerificationFactory(user=self.owner, status="approved", identity_provider_slug=f"idp{index}")
        self.login(self.owner)
        response = self.client.get(self.list_url, {"page_size": 3})
        assert response.data["count"] == 7
        assert len(response.data["results"]) == 3
        assert response.data["num_pages"] == 3
        assert response.data["next"].endswith("page=2&page_size=3")

    def test_the_attempts_come_back_newest_first_and_the_order_can_be_reversed(self):
        self.login(self.owner)
        descending = self.client.get(self.list_url).data["results"]
        assert [row["updated_at"] for row in descending] == sorted(
            [row["updated_at"] for row in descending], reverse=True,
        )
        ascending = self.client.get(self.list_url, {"ordering": "updated_at"}).data["results"]
        assert [row["type"] for row in ascending] == [row["type"] for row in reversed(descending)]

    def test_attempts_changed_at_the_same_moment_are_ordered_by_their_receipt(self):
        """
        Two attempts sharing a timestamp are told apart by their receipt identifier.

        The service hands back one table after another, so the order the rows
        arrive in is not an order the caller asked for: a page that falls back
        to it reads the same both ways round.
        """
        moment = datetime.datetime(2024, 5, 1, 12, 0, tzinfo=ZoneInfo("UTC"))
        for attempt in (self.attempt, self.manual):
            type(attempt).objects.filter(pk=attempt.pk).update(updated_at=moment)
        self.login(self.owner)

        descending = self.client.get(self.list_url).data["results"]
        assert [row["receipt_id"] for row in descending] == [str(self.attempt.receipt_id), None]
        ascending = self.client.get(self.list_url, {"ordering": "updated_at"}).data["results"]
        assert [row["receipt_id"] for row in ascending] == [None, str(self.attempt.receipt_id)]

    def test_a_sort_the_list_does_not_offer_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.get(self.list_url, {"ordering": "status"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "ordering": ["Sort by any of: updated_at, each optionally prefixed with '-'."],
        }

    def test_an_unknown_query_parameter_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.get(self.list_url, {"status": "approved"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "status": ["No such query parameter. Send any of: format, ordering, page, page_size."],
        }

    def test_the_name_in_the_address_is_refused_in_the_query_string_as_well(self):
        """The address carries the username, so a parameter repeating it is one this view never reads."""
        for caller in (self.owner, self.staff):
            self.login(caller)
            envelope = assert_error_envelope(
                self.client.get(f"{self.list_url}?username={self.owner.username}"),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {
                "username": ["No such query parameter. Send any of: format, ordering, page, page_size."],
            }
            self.client.logout()

    def test_a_staff_caller_reads_another_users_attempts(self):
        self.login(self.staff)
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2

    def test_a_learner_may_not_read_another_users_attempts(self):
        self.login(self.other)
        assert_error_envelope(
            self.client.get(self.list_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_a_refused_read_stays_refused_whatever_the_query_string_carries(self):
        """Who may read these attempts is settled from the address, before the query string is read."""
        self.login(self.other)
        for query in (
            f"username={self.other.username}",
            f"user={self.other.username}",
            "ordering=-updated_at",
        ):
            assert_error_envelope(
                self.client.get(f"{self.list_url}?{query}"),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_a_caller_named_for_the_address_in_case_alone_may_not_read_the_attempts(self):
        self.sign_in_as_a_case_variant_of(self.owner)
        assert_error_envelope(
            self.client.get(self.list_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_an_anonymous_caller_is_unauthenticated(self):
        assert_error_envelope(
            self.client.get(self.list_url),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )

    def test_a_user_who_has_been_through_no_checks_has_an_empty_page(self):
        """An existing user with nothing to list is an empty collection, not an absent one."""
        self.login(self.other)
        response = self.client.get(self.list_address(self.other.username))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert response.data["count"] == 0
        assert response.data["results"] == []
        assert response.data["num_pages"] == 1
        assert response.data["next"] is None

    def test_a_user_who_has_been_through_no_checks_is_told_apart_from_one_who_does_not_exist(self):
        self.login(self.staff)
        empty = self.client.get(self.list_address(self.other.username))
        unknown = self.client.get(self.list_address("nobody-at-all"))
        assert empty.status_code == status.HTTP_200_OK
        assert unknown.status_code == status.HTTP_404_NOT_FOUND
        assert empty.data["count"] == 0

    def test_an_unknown_username_is_not_found(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.get(self.list_address("nobody-at-all")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_page_that_does_not_exist_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.get(self.list_url, {"page": 9}),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_the_attempts_are_not_changed_through_this_address(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.post(self.list_url, {}, format="json"),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )


@skip_unless_lms
class VerificationPageSizeTests(VerificationTestCase):
    """
    How many attempts a page carries when the caller does not say, and the most it will carry.

    The list is asked for more attempts than it will give, against attempts
    that really exist, so what is pinned is the page the endpoint answers
    with rather than the numbers a paginator class holds.
    """

    DEFAULT_PAGE_SIZE = 10
    MAX_PAGE_SIZE = 100

    def extra_attempts(self, count):
        """Record the given number of further checks for the owner."""
        for index in range(count):
            SSOVerificationFactory(
                user=self.owner, status="approved", identity_provider_slug=f"idp{index:03d}",
            )

    def test_a_page_of_attempts_carries_ten_of_them_when_the_caller_asks_for_no_size(self):
        self.extra_attempts(self.DEFAULT_PAGE_SIZE)
        self.login(self.owner)
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.DEFAULT_PAGE_SIZE + 1
        assert len(response.data["results"]) == self.DEFAULT_PAGE_SIZE
        assert response.data["num_pages"] == 2
        assert "page=2" in response.data["next"]

    def test_a_page_of_attempts_carries_a_hundred_of_them_however_many_are_asked_for(self):
        self.extra_attempts(self.MAX_PAGE_SIZE)
        self.login(self.owner)
        response = self.client.get(self.list_url, {"page_size": 1000})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.MAX_PAGE_SIZE + 1
        assert len(response.data["results"]) == self.MAX_PAGE_SIZE
        assert response.data["num_pages"] == 2
        assert response.data["next"] is not None


@skip_unless_lms
class VerificationReadsDoNotWriteTests(VerificationTestCase):
    """Reading identity verification changes nothing a later read would see."""

    def setUp(self):
        super().setUp()
        ManualVerification.objects.create(user=self.owner, status="approved", reason="Checked in person")

    def assert_no_writes(self, address, params=None, expect=status.HTTP_200_OK, **headers):
        """
        Read the address and assert the request wrote nothing, whatever it answered.

        Request headers given here reach the client, which is how a read
        presented with a token is captured alongside one made from a session.
        Work handed to the end of the transaction is run inside the capture,
        so a write put off until the request is over is seen here too.
        """
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.get(address, params or {}, **headers)
        assert response.status_code == expect
        assert write_queries(captured) == []
        return response

    def other_status_address(self):
        """Return the status address of the account the branch fixtures below belong to."""
        return self.status_address(self.other.username)

    def test_reading_a_status_writes_nothing(self):
        self.login(self.owner)
        response = self.assert_no_writes(self.status_url)
        assert response.data["status"] == "approved"

    def test_reading_an_accepted_status_writes_nothing(self):
        self.approve(self.attempt)
        self.login(self.owner)
        response = self.assert_no_writes(self.status_url)
        assert response.data["status"] == "approved"

    def test_reading_an_accepted_status_about_to_run_out_writes_nothing(self):
        attempt = self.approve(
            SoftwareSecurePhotoVerification.objects.create(user=self.other, status="submitted"),
            expires_in_days=10,
        )
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "approved"
        assert response.data["expiration_datetime"] == attempt.expiration_datetime.date().strftime(
            "%m/%d/%Y",
        )

    def test_reading_a_pending_status_writes_nothing(self):
        SoftwareSecurePhotoVerification.objects.create(user=self.other, status="submitted")
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "pending"

    def test_reading_a_turned_down_status_writes_nothing(self):
        SoftwareSecurePhotoVerification.objects.create(
            user=self.other, status="denied", error_msg='[{"userPhotoReasons": ["Face out of view"]}]',
        )
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "must_reverify"

    def test_reading_a_status_whose_proof_has_run_out_writes_nothing(self):
        self.run_out(SoftwareSecurePhotoVerification.objects.create(user=self.other, status="submitted"))
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "expired"

    def test_reading_the_status_of_a_proof_that_ran_out_unseen_writes_nothing(self):
        self.run_out(ManualVerification.objects.create(
            user=self.other, status="approved", reason="Checked in person",
        ))
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "none"
        assert response.data["is_verified"] is False

    def test_reading_a_status_proved_by_signing_in_elsewhere_writes_nothing(self):
        SSOVerificationFactory(user=self.other, status="approved", identity_provider_slug="idp")
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "approved"
        assert response.data["is_verified"] is True

    def test_reading_a_status_proved_in_person_writes_nothing(self):
        ManualVerification.objects.create(user=self.other, status="approved", reason="Checked in person")
        self.login(self.other)
        response = self.assert_no_writes(self.other_status_address())
        assert response.data["status"] == "approved"
        assert response.data["is_verified"] is True

    def test_reading_a_status_as_staff_writes_nothing(self):
        self.login(self.staff)
        response = self.assert_no_writes(self.status_url)
        assert response.data["status"] == "approved"

    def test_asking_for_a_status_with_a_head_request_writes_nothing(self):
        self.login(self.owner)
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.head(self.status_url)
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured) == []

    def test_reading_the_attempts_writes_nothing(self):
        self.login(self.owner)
        response = self.assert_no_writes(self.list_url)
        assert response.data["count"] == 2

    def test_reading_the_attempts_in_the_other_order_writes_nothing(self):
        self.login(self.owner)
        response = self.assert_no_writes(self.list_url, {"ordering": "updated_at"})
        assert response.data["count"] == 2

    def test_reading_a_page_further_in_writes_nothing(self):
        for index in range(5):
            SSOVerificationFactory(user=self.owner, status="approved", identity_provider_slug=f"idp{index}")
        self.login(self.owner)
        response = self.assert_no_writes(self.list_url, {"page": 2, "page_size": 3})
        assert response.data["current_page"] == 2
        assert len(response.data["results"]) == 3

    def test_reading_an_empty_page_of_attempts_writes_nothing(self):
        self.login(self.other)
        response = self.assert_no_writes(self.list_address(self.other.username))
        assert response.data["count"] == 0

    def test_reading_the_attempts_as_staff_writes_nothing(self):
        self.login(self.staff)
        response = self.assert_no_writes(self.list_url)
        assert response.data["count"] == 2

    def test_asking_for_the_attempts_with_a_head_request_writes_nothing(self):
        self.login(self.owner)
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.head(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured) == []

    def test_reading_a_status_a_token_identifies_its_caller_by_writes_nothing(self):
        """
        A caller resolved from a token reads as a signed-in one does.

        A token is how a service or a micro-frontend reaches these addresses,
        and the layer that resolves one runs before the handler, so the reads
        are captured under both ways of identifying the caller.
        """
        response = self.assert_no_writes(self.status_url, **self.token_headers(self.owner))
        assert response.data["status"] == "approved"

    def test_reading_the_attempts_a_token_identifies_its_caller_by_writes_nothing(self):
        response = self.assert_no_writes(self.list_url, **self.token_headers(self.owner))
        assert response.data["count"] == 2

    def test_a_staff_token_reading_another_users_status_and_attempts_writes_nothing(self):
        headers = self.token_headers(self.staff)
        status_answer = self.assert_no_writes(self.status_url, **headers)
        assert status_answer.data["status"] == "approved"
        list_answer = self.assert_no_writes(self.list_url, **headers)
        assert list_answer.data["count"] == 2

    def test_refusing_an_anonymous_caller_writes_nothing(self):
        for address in (self.status_url, self.list_url):
            response = self.assert_no_writes(address, expect=status.HTTP_401_UNAUTHORIZED)
            assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_refusing_another_learner_writes_nothing(self):
        self.login(self.other)
        for address in (self.status_url, self.list_url):
            response = self.assert_no_writes(address, expect=status.HTTP_403_FORBIDDEN)
            assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_reporting_a_name_no_account_holds_writes_nothing(self):
        self.login(self.staff)
        for address in (self.status_address("nobody-at-all"), self.list_address("nobody-at-all")):
            response = self.assert_no_writes(address, expect=status.HTTP_404_NOT_FOUND)
            assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_refusing_a_query_parameter_these_addresses_do_not_read_writes_nothing(self):
        self.login(self.owner)
        for address in (self.status_url, self.list_url):
            response = self.assert_no_writes(
                address, {"username": self.owner.username}, expect=status.HTTP_400_BAD_REQUEST,
            )
            assert_error_envelope(response, expected_status=400, expected_type_slug="validation")


@skip_unless_lms
class NothingOnRecordTests(VerificationTestCase):
    """What the addresses under one account answer for a user who has done nothing yet."""

    def setUp(self):
        super().setUp()
        self.newcomer = UserFactory.create(
            username="verify-newcomer", email="verify-newcomer@example.com", password=PASSWORD,
        )
        UserProfile.objects.filter(user=self.newcomer).delete()
        assert not UserProfile.objects.filter(user=self.newcomer).exists()

    def test_an_account_with_nothing_on_record_is_answered_at_each_of_its_addresses(self):
        """
        A user who has been through no check and holds no profile is answered at all three addresses.

        Nothing on record is something to report, so the summary, the page of
        attempts and the account itself all answer, and they answer the same
        way as each other.
        """
        self.login(self.newcomer)
        standing = self.client.get(self.status_address(self.newcomer.username))
        assert standing.status_code == status.HTTP_200_OK
        assert standing.data == {
            "is_verified": False, "status": "none", "expiration_datetime": "",
        }

        attempts = self.client.get(self.list_address(self.newcomer.username))
        assert attempts.status_code == status.HTTP_200_OK
        assert attempts.data["count"] == 0
        assert attempts.data["results"] == []

        account = self.client.get(reverse("user_v2:account_detail", kwargs={"username": "me"}))
        assert account.status_code == status.HTTP_200_OK
        assert account.data["username"] == self.newcomer.username

    def test_the_addresses_of_a_name_no_account_holds_are_all_absent(self):
        """A missing user is what makes one of these addresses absent, and the only thing that does."""
        self.login(self.staff)
        for address in (
            self.status_address("nobody-at-all"),
            self.list_address("nobody-at-all"),
            reverse("user_v2:account_detail", kwargs={"username": "nobody-at-all"}),
        ):
            assert_error_envelope(
                self.client.get(address),
                expected_status=status.HTTP_404_NOT_FOUND,
                expected_type_slug="not-found",
            )


@skip_unless_lms
class VerificationDisabledCallerTests(VerificationTestCase):
    """What these addresses answer a caller whose own account has been disabled."""

    def test_a_token_held_by_a_disabled_staff_account_may_not_read_another_users_records(self):
        """A privilege does not survive the deactivation of the account that held it."""
        headers = self.token_headers(self.staff)
        assert self.client.get(self.status_url, **headers).status_code == status.HTTP_200_OK
        assert self.client.get(self.list_url, **headers).status_code == status.HTTP_200_OK
        self.staff.is_active = False
        self.staff.save()
        for address in (self.status_url, self.list_url):
            assert_error_envelope(
                self.client.get(address, **headers),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_a_disabled_account_still_reads_its_own_records(self):
        """What a user proved about themselves stays readable to them while the account is disabled."""
        headers = self.token_headers(self.owner)
        self.owner.is_active = False
        self.owner.save()
        summary = self.client.get(self.status_url, **headers)
        assert summary.status_code == status.HTTP_200_OK
        assert summary.data == {"is_verified": False, "status": "pending", "expiration_datetime": ""}
        page = self.client.get(self.list_url, **headers)
        assert page.status_code == status.HTTP_200_OK
        assert page.data["count"] == 1

    def test_a_disabled_staff_account_still_reads_its_own_records(self):
        """The staff half of the check is the one the disabled account loses."""
        headers = self.token_headers(self.staff)
        self.staff.is_active = False
        self.staff.save()
        summary = self.client.get(self.status_address(self.staff.username), **headers)
        assert summary.status_code == status.HTTP_200_OK
        assert summary.data == {"is_verified": False, "status": "none", "expiration_datetime": ""}
        page = self.client.get(self.list_address(self.staff.username), **headers)
        assert page.status_code == status.HTTP_200_OK
        assert page.data["count"] == 0


@skip_unless_lms
class VerificationAddressedNameTests(VerificationTestCase):
    """
    These addresses carry the name an account is stored under.

    The account collection reads ``me`` as the account of the caller; the
    addresses beneath an account read the name they carry as it stands, so a
    caller that sends ``me`` to one of them is answered as a caller naming any
    other account is.
    """

    def test_a_learner_sending_the_collections_own_name_is_refused(self):
        self.login(self.owner)
        for address in (self.status_address("me"), self.list_address("me")):
            assert_error_envelope(
                self.client.get(address),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_a_staff_caller_sending_it_is_told_no_account_holds_that_name(self):
        self.login(self.staff)
        for address in (self.status_address("me"), self.list_address("me")):
            assert_error_envelope(
                self.client.get(address),
                expected_status=status.HTTP_404_NOT_FOUND,
                expected_type_slug="not-found",
            )

    def test_the_account_holding_that_name_is_served_at_it(self):
        """A user may register the name, and reads their own records where it is addressed."""
        holder = UserFactory.create(username="me", email="me@example.com", password=PASSWORD)
        SoftwareSecurePhotoVerification.objects.create(user=holder, status="approved")
        self.login(holder)
        summary = self.client.get(self.status_address("me"))
        assert summary.status_code == status.HTTP_200_OK
        assert summary.data["status"] == "approved"
        page = self.client.get(self.list_address("me"))
        assert page.status_code == status.HTTP_200_OK
        assert page.data["count"] == 1
        assert page.data["results"][0]["status"] == "approved"


@skip_unless_lms
class VerificationParityTests(VerificationTestCase):
    """The same fixtures through the version 1 and the version 2 addresses."""

    def setUp(self):
        super().setUp()
        ManualVerification.objects.create(user=self.owner, status="approved", reason="Checked in person")

    def test_both_versions_report_the_same_status(self):
        self.login(self.owner)
        earlier = self.client.get(earlier_status_address(self.owner.username))
        later = self.client.get(self.status_url)
        assert earlier.status_code == later.status_code == status.HTTP_200_OK
        assert later.data == earlier.data

    def test_both_versions_report_the_same_accepted_status(self):
        self.approve(self.attempt, expires_in_days=10)
        self.login(self.owner)
        earlier = self.client.get(earlier_status_address(self.owner.username))
        later = self.client.get(self.status_url)
        assert later.data == earlier.data
        assert later.data["is_verified"] is True

    def test_both_versions_carry_the_same_attempts(self):
        self.login(self.owner)
        earlier = self.client.get(earlier_list_address(self.owner.username))
        later = self.client.get(self.list_url)
        assert earlier.status_code == later.status_code == status.HTTP_200_OK
        # The one declared difference: the attempts come back in a page rather
        # than as a bare list, and the rows themselves are unchanged.
        assert isinstance(earlier.data, list)
        assert later.data["results"] == earlier.data
        assert later.data["count"] == len(earlier.data)

    def test_both_versions_refuse_a_learner_reading_another_users_attempts(self):
        self.login(self.other)
        earlier = self.client.get(earlier_list_address(self.owner.username))
        later = self.client.get(self.list_url)
        assert earlier.status_code == later.status_code == status.HTTP_403_FORBIDDEN
        assert earlier.data == {"detail": "You do not have permission to perform this action."}
        assert_error_envelope(later, expected_status=403, expected_type_slug="authz")

    def test_the_status_of_a_user_who_has_been_through_no_checks_is_reported_rather_than_refused(self):
        """
        Declared difference: a user with nothing on record has a status here.

        The earlier address answers the same way for a user who has been
        through no check and for a name no account holds, so a client cannot
        tell a typo from a user who has not started. Here the summary says
        `none` and the refusal is kept for the name no account holds, which
        the second half asserts.
        """
        self.login(self.other)
        earlier = self.client.get(earlier_status_address(self.other.username))
        later = self.client.get(self.status_address(self.other.username))
        assert earlier.status_code == status.HTTP_404_NOT_FOUND
        assert later.status_code == status.HTTP_200_OK
        assert later.data == {"is_verified": False, "status": "none", "expiration_datetime": ""}

        self.client.logout()
        self.login(self.staff)
        assert self.client.get(self.status_address("nobody-at-all")).status_code == status.HTTP_404_NOT_FOUND

    def test_the_attempts_of_a_user_who_has_been_through_no_checks_are_an_empty_page(self):
        """
        Declared difference: an existing user with no attempts has an empty page here.

        The earlier address answers the same way for a user who has made no
        attempt and for a name no account holds, so a client cannot tell a typo
        from an empty history. Here the page is empty and the refusal is kept
        for the name no account holds, which the second half asserts.
        """
        self.login(self.other)
        earlier = self.client.get(earlier_list_address(self.other.username))
        later = self.client.get(self.list_address(self.other.username))
        assert earlier.status_code == status.HTTP_404_NOT_FOUND
        assert later.status_code == status.HTTP_200_OK
        assert later.data["count"] == 0
        assert later.data["results"] == []

        self.client.logout()
        self.login(self.staff)
        assert self.client.get(self.list_address("nobody-at-all")).status_code == status.HTTP_404_NOT_FOUND

    def test_both_versions_refuse_an_address_that_is_not_exactly_the_callers_name(self):
        self.login(self.owner)
        shouted = self.owner.username.upper()
        for earlier_address, later_address in (
            (earlier_status_address(shouted), self.status_address(shouted)),
            (earlier_list_address(shouted), self.list_address(shouted)),
        ):
            earlier = self.client.get(earlier_address)
            later = self.client.get(later_address)
            assert earlier.status_code == status.HTTP_403_FORBIDDEN
            assert earlier.data == {"detail": "You do not have permission to perform this action."}
            assert_error_envelope(later, expected_status=403, expected_type_slug="authz")

    def test_an_anonymous_caller_is_refused_by_both_and_told_so_by_this_one(self):
        earlier = self.client.get(earlier_list_address(self.owner.username))
        later = self.client.get(self.list_url)
        assert earlier.status_code == later.status_code == status.HTTP_401_UNAUTHORIZED
        assert_error_envelope(later, expected_status=401, expected_type_slug="authn")


#: What a page of attempts costs, and what a status costs, measured on the
#: version 1 addresses first. The attempts come from four tables, one per kind
#: of check, and the page is cut from what they return, so a page costs the
#: same whatever it carries. A status reads those four tables and then works
#: out when an accepted proof runs out, which costs more. Both versions read
#: the same rows over the same fixtures, so each number is the one number
#: asserted on both addresses: a change on either side is a change in what the
#: answer costs.
LIST_QUERIES = EARLIER_LIST_QUERIES = 18
STATUS_QUERIES = EARLIER_STATUS_QUERIES = 22


@pytest.fixture(name="verified_user")
@skip_unless_lms
def verified_user_fixture(client, django_user_model):  # pylint: disable=unused-argument
    """Sign a learner in who has been through several identity checks."""
    owner = UserFactory.create(username="count-verify-owner", password=PASSWORD)
    SoftwareSecurePhotoVerification.objects.create(user=owner, status="submitted")
    ManualVerification.objects.create(user=owner, status="approved", reason="Checked in person")
    for index in range(4):
        SSOVerificationFactory(user=owner, status="approved", identity_provider_slug=f"idp{index}")
    assert client.login(username=owner.username, password=PASSWORD)
    # The first authenticated request of a session populates caches the rest
    # of the session reuses; the counts below are the steady-state ones.
    client.get(reverse("user_v2:account_verification_list", kwargs={"username": owner.username}))
    return owner


@pytest.mark.django_db
@pytest.mark.parametrize("page_size", [1, 3, 6])
@skip_unless_lms
def test_a_page_of_attempts_costs_what_the_earlier_address_costs(
    client, verified_user, page_size, django_assert_num_queries,
):
    with django_assert_num_queries(EARLIER_LIST_QUERIES):
        earlier = client.get(f"/api/user/v1/accounts/{verified_user.username}/verifications/")
    assert len(earlier.json()) == 6

    address = reverse("user_v2:account_verification_list", kwargs={"username": verified_user.username})
    with django_assert_num_queries(LIST_QUERIES):
        later = client.get(address, {"page_size": page_size})
    assert later.json()["count"] == 6
    assert len(later.json()["results"]) == page_size


@pytest.mark.django_db
@skip_unless_lms
def test_a_status_costs_what_the_earlier_address_costs(client, verified_user, django_assert_num_queries):
    with django_assert_num_queries(EARLIER_STATUS_QUERIES):
        earlier = client.get(f"/api/user/v1/accounts/{verified_user.username}/verification_status/")
    assert earlier.json()["status"] == "approved"

    address = reverse("user_v2:account_verification_status", kwargs={"username": verified_user.username})
    with django_assert_num_queries(STATUS_QUERIES):
        later = client.get(address)
    assert later.json() == earlier.json()
