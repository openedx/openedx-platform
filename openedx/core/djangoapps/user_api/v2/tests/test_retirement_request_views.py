"""Tests for the request an account's owner makes to have their own account closed."""

import json
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core import mail
from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.errors import INTERNAL_ERROR_SLUG, INTERNAL_ERROR_TITLE, error_type_uri
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.test import APIClient, APITestCase
from rest_framework.views import APIView
from social_django.models import UserSocialAuth

from common.djangoapps.student.models import (
    LoginFailures,
    Registration,
    get_retired_email_by_email,
)
from common.djangoapps.student.tests.factories import AdminFactory, SuperuserFactory, UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserRetirementRequest,
    UserRetirementStatus,
)
from openedx.core.djangoapps.user_api.v2 import view_services
from openedx.core.djangoapps.user_api.v2.errors import PasswordNotConfirmed
from openedx.core.djangoapps.user_api.v2.permissions import ACCOUNT_DELETION_IS_NOT_OFFERED
from openedx.core.djangoapps.user_api.v2.views import (
    RetirementCleanupViewSet,
    RetirementViewSet,
)
from openedx.core.djangolib.default_auth_classes import (
    DefaultJwtAuthentication,
    DefaultSessionAuthentication,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

# The class reading an access token this platform's authorization endpoint
# handed out, named here so that the operation taking it is held to taking it.
from openedx.core.lib.api.authentication import BearerAuthentication

from .test_views import UNSUPPORTED_MEDIA_TYPE_SLUG

PASSWORD = "Password1234"

#: The caller the pipeline runs as, and the caller that replaces usernames.
WORKER_USERNAME = "request-retirement-worker"
REPLACEMENT_WORKER_USERNAME = "request-replacement-worker"

#: The type each refusal of this operation carries, which is how a caller
#: tells one from another: the address answers the same status for all three.
DELETION_UNAVAILABLE_SLUG = "user/account-deletion-unavailable"
PASSWORD_NOT_CONFIRMED_SLUG = "user/password-not-confirmed"
TEMPORARILY_LOCKED_SLUG = "user/account-temporarily-locked"
STATE_CONFLICT_SLUG = "user/retirement-state-conflict"

FORM = "application/x-www-form-urlencoded"

#: The step every retirement is queued at, and the order it carries there.
FIRST_STEP = "PENDING"
FIRST_STEP_ORDER = 1

#: The address the operation this one replaces is served at.
EARLIER_ADDRESS = "/api/user/v1/accounts/deactivate_logout/"

#: How many refused password attempts this deployment allows before closing an
#: account to further ones, read from the deployment rather than named here.
MAX_ATTEMPTS = settings.MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED

#: The authenticators every operation that declares none of its own resolves.
#: They are named here rather than read from the platform: the operations of
#: this collection that move, erase and delete records are held to taking
#: these two and nothing else, and reading the list from the platform would
#: hold them to whatever it grows instead.
PLATFORM_AUTHENTICATION = [DefaultJwtAuthentication, DefaultSessionAuthentication]

#: What the one operation of this collection that declares its own resolves.
OWN_REQUEST_AUTHENTICATION = [JwtAuthentication, SessionAuthentication, BearerAuthentication]
OWN_REQUEST_PARSERS = [JSONParser, FormParser, MultiPartParser]


@override_settings(
    RETIREMENT_SERVICE_WORKER_USERNAME=WORKER_USERNAME,
    USERNAME_REPLACEMENT_WORKER=REPLACEMENT_WORKER_USERNAME,
)
@pytest.mark.usefixtures("setup_retirement_states")
class RetirementRequestTestCase(APITestCase):
    """The callers and the fixtures the tests of this operation share."""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_list")
        self.owner = UserFactory.create(
            username="account-owner", email="owner@example.com", password=PASSWORD,
        )
        self.bystander = UserFactory.create(
            username="bystander", email="bystander@example.com", password=PASSWORD,
        )

    def login(self, user, password=PASSWORD):
        """Sign this caller in through the session the platform default accepts."""
        self.client.logout()
        assert self.client.login(username=user.username, password=password)

    def ask(self, password=PASSWORD, **kwargs):
        """Ask for the retirement of the account the signed-in caller holds."""
        return self.client.post(self.url, {"password": password}, format="json", **kwargs)

    def queued(self, user):
        """Return the retirement records held for this account."""
        return UserRetirementStatus.objects.filter(user=user)

    def assert_nothing_was_recorded(self, user):
        """Assert this account is exactly as it was before the request."""
        assert not self.queued(user).exists()
        assert not UserRetirementRequest.objects.filter(user=user).exists()
        stored = User.objects.get(id=user.id)
        assert stored.email == user.email
        assert stored.check_password(PASSWORD) is True
        assert [message for message in mail.outbox if user.email in message.to] == []


@skip_unless_lms
class RetirementRequestAccessTests(RetirementRequestTestCase):
    """Who may ask for a retirement, and whose account each caller's request reaches."""

    def test_an_anonymous_caller_is_unauthenticated(self):
        self.client.logout()
        assert_error_envelope(
            self.ask(), expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )
        assert UserRetirementStatus.objects.count() == 0

    def test_every_caller_closes_the_account_they_hold_whatever_capability_it_carries(self):
        """The capability the pipeline asks for is neither needed here nor enough on its own."""
        callers = {
            "learner": UserFactory.create(username="plain-learner", password=PASSWORD),
            "pipeline": UserFactory.create(username=WORKER_USERNAME, password=PASSWORD),
            "replacer": UserFactory.create(username=REPLACEMENT_WORKER_USERNAME, password=PASSWORD),
            "staff": AdminFactory.create(username="plain-staff", password=PASSWORD),
            "superuser": SuperuserFactory.create(username="super-caller", password=PASSWORD),
        }
        for role, caller in callers.items():
            with self.subTest(caller=role):
                self.login(caller)
                assert self.ask().status_code == status.HTTP_204_NO_CONTENT
                assert self.queued(caller).count() == 1
        assert set(
            UserRetirementStatus.objects.values_list("original_username", flat=True),
        ) == {caller.username for caller in callers.values()}

    def test_a_request_naming_another_account_closes_the_one_it_was_made_from(self):
        """Nothing in the body says which account this is about, and a name sent in one is ignored."""
        self.login(self.owner)
        answered = self.client.post(
            self.url,
            {"password": PASSWORD, "username": self.bystander.username},
            format="json",
        )
        assert answered.status_code == status.HTTP_204_NO_CONTENT
        assert self.queued(self.owner).count() == 1
        self.assert_nothing_was_recorded(self.bystander)

    def test_the_password_of_another_account_does_not_close_this_one(self):
        other = UserFactory.create(username="other-owner", password="OtherPassword4321")
        self.login(self.owner)
        assert_error_envelope(
            self.ask(password="OtherPassword4321"),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=PASSWORD_NOT_CONFIRMED_SLUG,
        )
        self.assert_nothing_was_recorded(self.owner)
        assert not self.queued(other).exists()

    def test_asking_the_collection_what_it_serves_is_pipeline_work(self):
        """Its description is the queue's, and reading the queue is what that capability is for."""
        worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
        self.login(worker)
        assert self.client.options(self.url).status_code == status.HTTP_200_OK
        self.login(self.owner)
        assert_error_envelope(
            self.client.options(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )


@skip_unless_lms
class RetirementRequestTests(RetirementRequestTestCase):
    """What a request that is carried out leaves behind."""

    def setUp(self):
        super().setUp()
        UserSocialAuth.objects.create(user=self.owner, provider="a_provider", uid="owner@example.com")
        Registration().register(self.owner)
        self.login(self.owner)

    def test_the_account_is_queued_at_the_first_configured_step(self):
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        record = self.queued(self.owner).get()
        assert record.current_state.state_name == FIRST_STEP
        assert record.current_state.state_execution_order == FIRST_STEP_ORDER
        assert record.original_username == self.owner.username
        assert record.original_email == "owner@example.com"

    def test_the_request_itself_is_recorded_as_well_as_where_it_stands(self):
        """The record of the request outlives the record of its progress."""
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert UserRetirementRequest.objects.filter(user=self.owner).count() == 1

    def test_the_password_no_longer_signs_anybody_in(self):
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert User.objects.get(id=self.owner.id).has_usable_password() is False
        assert self.client.login(username=self.owner.username, password=PASSWORD) is False

    def test_the_address_is_replaced_by_one_that_reaches_nobody(self):
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert User.objects.get(id=self.owner.id).email == get_retired_email_by_email("owner@example.com")

    def test_the_keys_sent_to_confirm_the_address_are_gone_and_the_social_logins_are_not(self):
        """A request that is called off has to leave its owner a way back in."""
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert not Registration.objects.filter(user=self.owner).exists()
        assert [auth.provider for auth in UserSocialAuth.objects.filter(user=self.owner)] == [
            "a_provider",
        ]

    def test_one_message_is_sent_to_the_address_the_account_held(self):
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["owner@example.com"]

    def test_the_session_the_request_was_made_from_no_longer_authenticates(self):
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert_error_envelope(
            self.client.post(self.url, {"password": PASSWORD}, format="json"),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )

    def test_the_answer_carries_no_body_at_all(self):
        answered = self.ask()
        assert answered.status_code == status.HTTP_204_NO_CONTENT
        assert answered.content == b""

    def test_the_password_sent_is_written_nowhere(self):
        """It is read and compared, and no answer and no log line carries it."""
        with self.assertLogs("openedx.core.djangoapps.user_api.v2.view_services", level="DEBUG") as logged:
            assert self.ask(password="WrongPassword9999").status_code == status.HTTP_403_FORBIDDEN
            refused = self.client.post(self.url, {}, format="json")
        assert PASSWORD not in refused.content.decode()
        assert "WrongPassword9999" not in refused.content.decode()
        assert "WrongPassword9999" not in "\n".join(logged.output)


@skip_unless_lms
class RetirementRequestBodyTests(RetirementRequestTestCase):
    """The forms the request may be sent in, and what it is refused for."""

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_a_body_sent_as_a_web_form_is_read(self):
        """The applications shipped to learners send it that way."""
        answered = self.client.post(self.url, f"password={PASSWORD}", content_type=FORM)
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1

    def test_a_body_sent_as_a_file_upload_is_read(self):
        answered = self.client.post(self.url, {"password": PASSWORD}, format="multipart")
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1

    def test_a_body_sent_as_json_is_read(self):
        answered = self.client.post(self.url, json.dumps({"password": PASSWORD}), content_type="application/json")
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1

    def test_a_body_in_a_form_this_operation_cannot_read_is_refused(self):
        assert_error_envelope(
            self.client.post(self.url, PASSWORD, content_type="text/plain"),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug=UNSUPPORTED_MEDIA_TYPE_SLUG,
        )
        self.assert_nothing_was_recorded(self.owner)

    def test_a_request_carrying_no_password_names_what_it_needs(self):
        envelope = assert_error_envelope(
            self.client.post(self.url, {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert list(envelope["errors"]) == ["password"]
        self.assert_nothing_was_recorded(self.owner)

    def test_a_password_sent_as_nothing_at_all_is_refused_as_not_the_accounts(self):
        """The earlier address reads an empty password as one the account does not hold, and so does this one."""
        sent = {
            "form": self.client.post(self.url, "password=", content_type=FORM),
            "json": self.ask(password=""),
        }
        for body, answered in sent.items():
            with self.subTest(body=body):
                envelope = assert_error_envelope(
                    answered,
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug=PASSWORD_NOT_CONFIRMED_SLUG,
                )
                assert envelope["detail"] == view_services.PASSWORD_NOT_CONFIRMED
        self.assert_nothing_was_recorded(self.owner)

    def test_a_password_is_read_exactly_as_it_was_sent(self):
        """A password stored with a space at either end is confirmed by sending it."""
        spaced = UserFactory.create(username="spaced-owner", password="  keeps the spaces  ")
        self.login(spaced, password="  keeps the spaces  ")
        answered = self.ask(password="  keeps the spaces  ")
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(spaced).count() == 1

    def test_a_query_parameter_this_operation_does_not_read_is_refused(self):
        envelope = assert_error_envelope(
            self.client.post(f"{self.url}?view=minimal", {"password": PASSWORD}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert list(envelope["errors"]) == ["view"]
        self.assert_nothing_was_recorded(self.owner)

    def test_the_representation_selector_is_read(self):
        answered = self.client.post(f"{self.url}?format=json", {"password": PASSWORD}, format="json")
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content


@skip_unless_lms
class RetirementRequestRefusalTests(RetirementRequestTestCase):
    """Each refusal that leaves the account as it was carries a type of its own."""

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    @override_settings(ENABLE_ACCOUNT_DELETION=False)
    def test_a_deployment_that_does_not_offer_this_refuses_it_in_those_terms(self):
        envelope = assert_error_envelope(
            self.ask(),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=DELETION_UNAVAILABLE_SLUG,
        )
        assert envelope["detail"] == ACCOUNT_DELETION_IS_NOT_OFFERED
        self.assert_nothing_was_recorded(self.owner)

    def test_a_site_that_does_not_offer_this_refuses_it_where_the_deployment_does(self):
        """A site served by this deployment carries a value of its own, and it is read first."""
        with with_site_configuration_context(configuration={"ENABLE_ACCOUNT_DELETION": False}):
            assert_error_envelope(
                self.ask(),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug=DELETION_UNAVAILABLE_SLUG,
            )
        self.assert_nothing_was_recorded(self.owner)

    def test_a_password_the_account_does_not_hold_refuses_it_in_those_terms(self):
        envelope = assert_error_envelope(
            self.ask(password="WrongPassword9999"),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=PASSWORD_NOT_CONFIRMED_SLUG,
        )
        assert envelope["detail"] == view_services.PASSWORD_NOT_CONFIRMED
        self.assert_nothing_was_recorded(self.owner)

    def test_the_three_refusals_are_told_apart_by_their_type_at_one_status(self):
        """A caller branching on the status alone cannot tell them apart, which is why each has a type."""
        answered = {}
        with override_settings(ENABLE_ACCOUNT_DELETION=False):
            answered["deletion is not offered"] = self.ask().json()
        answered["the password was not the account's"] = self.ask(password="WrongPassword9999").json()
        with override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True):
            for _ in range(MAX_ATTEMPTS):
                self.ask(password="WrongPassword9999")
            answered["the account is closed to attempts"] = self.ask().json()
        assert {reason: body["status"] for reason, body in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        assert {reason: body["type"] for reason, body in answered.items()} == {
            "deletion is not offered": error_type_uri(DELETION_UNAVAILABLE_SLUG),
            "the password was not the account's": error_type_uri(PASSWORD_NOT_CONFIRMED_SLUG),
            "the account is closed to attempts": error_type_uri(TEMPORARILY_LOCKED_SLUG),
        }
        assert len({body["detail"] for body in answered.values()}) == 3
        self.assert_nothing_was_recorded(self.owner)


@skip_unless_lms
@override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
class AttemptsCountedTests(RetirementRequestTestCase):
    """Where the operator counts refused password attempts, these are counted with them."""

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def attempts(self):
        """Return how many refused attempts are recorded against this account."""
        return [record.failure_count for record in LoginFailures.objects.filter(user=self.owner)]

    def test_an_attempt_with_a_password_the_account_does_not_hold_is_counted(self):
        """
        The count outlives the answer that refuses the attempt.

        A request is carried out as one transaction and a refusal rolls it
        back, so the attempt would otherwise be forgotten as it was refused
        and no number of attempts would ever close an account.
        """
        assert self.ask(password="WrongPassword9999").status_code == status.HTTP_403_FORBIDDEN
        assert self.attempts() == [1]

    def test_an_attempt_with_an_empty_password_is_counted(self):
        """The earlier address counts it too, so blank attempts close an account as any others do."""
        assert self.ask(password="").status_code == status.HTTP_403_FORBIDDEN
        assert self.attempts() == [1]

    def test_every_attempt_adds_to_the_count_until_the_account_is_closed_to_them(self):
        for expected in range(1, MAX_ATTEMPTS + 1):
            assert self.ask(password="WrongPassword9999").status_code == status.HTTP_403_FORBIDDEN
            assert self.attempts() == [expected]
        assert LoginFailures.objects.get(user=self.owner).lockout_until is not None

    def test_a_refusal_that_keeps_the_count_keeps_nothing_else(self):
        """Only the count survives it: the account is as it was and no retirement was recorded."""
        assert self.ask(password="WrongPassword9999").status_code == status.HTTP_403_FORBIDDEN
        assert self.attempts() == [1]
        self.assert_nothing_was_recorded(self.owner)

    def test_the_count_is_cleared_by_a_password_the_account_does_hold(self):
        assert self.ask(password="WrongPassword9999").status_code == status.HTTP_403_FORBIDDEN
        assert self.attempts() == [1]
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert self.attempts() == []

    def test_an_account_closed_to_attempts_is_refused_before_the_password_is_read(self):
        for _ in range(MAX_ATTEMPTS):
            self.ask(password="WrongPassword9999")
        envelope = assert_error_envelope(
            self.ask(),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=TEMPORARILY_LOCKED_SLUG,
        )
        assert envelope["detail"] == view_services.ACCOUNT_TEMPORARILY_LOCKED
        self.assert_nothing_was_recorded(self.owner)


@skip_unless_lms
@override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=False)
class AttemptsNotCountedTests(RetirementRequestTestCase):
    """Where the operator counts nothing, nothing is counted and nothing is ever locked."""

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_no_attempt_is_recorded_and_no_account_is_ever_closed_to_them(self):
        for _ in range(MAX_ATTEMPTS + 2):
            assert_error_envelope(
                self.ask(password="WrongPassword9999"),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug=PASSWORD_NOT_CONFIRMED_SLUG,
            )
        assert [record.failure_count for record in LoginFailures.objects.filter(user=self.owner)] == []
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT

    def test_an_empty_password_is_refused_and_not_counted(self):
        assert_error_envelope(
            self.ask(password=""),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=PASSWORD_NOT_CONFIRMED_SLUG,
        )
        assert [record.failure_count for record in LoginFailures.objects.filter(user=self.owner)] == []


@skip_unless_lms
class RetirementAlreadyRequestedTests(RetirementRequestTestCase):
    """A second request for an account that already has a record changes nothing."""

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_a_second_request_is_refused_because_the_password_no_longer_signs_anybody_in(self):
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        self.client.logout()
        assert self.client.login(username=self.owner.username, password=PASSWORD) is False
        assert_error_envelope(
            self.client.post(self.url, {"password": PASSWORD}, format="json"),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert self.queued(self.owner).count() == 1
        assert len(mail.outbox) == 1

    def test_a_record_made_some_other_way_refuses_the_request_as_a_conflict(self):
        """A record can be made while the password still signs its owner in, and this is that case."""
        UserRetirementStatus.create_retirement(self.owner)
        requests_before = UserRetirementRequest.objects.filter(user=self.owner).count()
        envelope = assert_error_envelope(
            self.ask(),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["detail"] == view_services.RETIREMENT_ALREADY_REQUESTED
        assert self.queued(self.owner).count() == 1
        assert UserRetirementRequest.objects.filter(user=self.owner).count() == requests_before
        assert User.objects.get(id=self.owner.id).check_password(PASSWORD) is True
        assert [message.to for message in mail.outbox] == []


@skip_unless_lms
class RetirementRequestFailureTests(RetirementRequestTestCase):
    """A request that cannot be recorded in full records nothing and says nothing about why."""

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def assert_this_servers_failure(self, response, detail):
        """Assert the answer is the envelope this version answers its own failures with."""
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, response.content
        assert response.json() == {
            "type": error_type_uri(INTERNAL_ERROR_SLUG),
            "title": INTERNAL_ERROR_TITLE,
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "detail": detail,
            "instance": self.url,
        }

    def test_a_deployment_with_no_configured_steps_records_nothing(self):
        """Such a deployment cannot queue anything, and the caller cannot act on that."""
        RetirementState.objects.all().delete()
        self.assert_this_servers_failure(
            self.ask(), view_services.RETIREMENT_COULD_NOT_BE_REQUESTED,
        )
        self.assert_nothing_was_recorded(self.owner)

    def test_a_message_that_cannot_be_sent_leaves_the_account_as_it_was(self):
        """The owner of an account being closed is told that it is, or it is not closed."""
        with mock.patch.object(
            view_services.ace, "send", side_effect=ValueError("the mail server is on fire"),
        ):
            answered = self.ask()
        self.assert_this_servers_failure(answered, view_services.RETIREMENT_WAS_NOT_RECORDED)
        assert "fire" not in answered.content.decode()
        self.assert_nothing_was_recorded(self.owner)


    def test_a_failure_after_the_message_leaves_it_sent_and_the_account_as_it_was(self):
        """
        The owner is told before the request is kept, as they are at the earlier address.

        Nothing of the request is kept, so it can be made again, and the
        message about the first one has already gone to the address the
        account held.
        """
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.logout",
            side_effect=ValueError("the session store is on fire"),
        ):
            answered = self.ask()
        assert answered.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, answered.content
        assert len([message for message in mail.outbox if self.owner.email in message.to]) == 1
        assert not self.queued(self.owner).exists()
        assert not UserRetirementRequest.objects.filter(user=self.owner).exists()
        stored = User.objects.get(id=self.owner.id)
        assert stored.email == self.owner.email
        assert stored.check_password(PASSWORD) is True


@skip_unless_lms
class RefusedRequestLeavesTheRecordsAloneTests(RetirementRequestTestCase):
    """
    What a refused or failed request does to the records that were already there.

    A refusal keeps the count of the attempt it refused, which it does by
    letting the request it was answering be kept rather than rolled back, so
    what else such an answer keeps -- and what a failure part way through
    still undoes -- is measured here against the stored rows rather than
    against the status alone.
    """

    def setUp(self):
        super().setUp()
        self.held_for_the_bystander = UserRetirementStatus.create_retirement(self.bystander)
        self.login(self.owner)

    def bystander_record(self):
        """Return everything about the record held for the account that asked for nothing."""
        record = UserRetirementStatus.objects.get(id=self.held_for_the_bystander.id)
        return {
            "id": record.id,
            "step": record.current_state.state_name,
            "name": record.original_username,
            "address": record.original_email,
            "requests recorded": UserRetirementRequest.objects.filter(user=self.bystander).count(),
        }

    def assert_the_bystander_is_untouched(self):
        """Assert the record another request left is exactly as that request left it."""
        assert self.bystander_record() == {
            "id": self.held_for_the_bystander.id,
            "step": FIRST_STEP,
            "name": "bystander",
            "address": "bystander@example.com",
            "requests recorded": 1,
        }
        assert User.objects.get(id=self.bystander.id).email == "bystander@example.com"

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_a_refusal_that_keeps_the_count_adds_no_record_and_touches_no_other(self):
        assert self.ask(password="WrongPassword9999").status_code == status.HTTP_403_FORBIDDEN
        assert [record.failure_count for record in LoginFailures.objects.filter(user=self.owner)] == [1]
        assert not self.queued(self.owner).exists()
        assert UserRetirementStatus.objects.count() == 1
        self.assert_the_bystander_is_untouched()

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_a_refusal_at_an_account_closed_to_attempts_does_the_same(self):
        for _ in range(MAX_ATTEMPTS):
            self.ask(password="WrongPassword9999")
        assert_error_envelope(
            self.ask(),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=TEMPORARILY_LOCKED_SLUG,
        )
        assert not self.queued(self.owner).exists()
        assert UserRetirementStatus.objects.count() == 1
        self.assert_the_bystander_is_untouched()

    def test_a_request_that_cannot_be_recorded_in_full_keeps_none_of_its_own_and_none_of_theirs(self):
        """The whole of a failed request is undone, and nothing of another request is."""
        with mock.patch.object(
            view_services.ace, "send", side_effect=ValueError("the mail server is on fire"),
        ):
            answered = self.ask()
        assert answered.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR, answered.content
        assert not self.queued(self.owner).exists()
        assert not UserRetirementRequest.objects.filter(user=self.owner).exists()
        assert User.objects.get(id=self.owner.id).check_password(PASSWORD) is True
        assert UserRetirementStatus.objects.count() == 1
        self.assert_the_bystander_is_untouched()

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_what_a_request_refused_one_of_those_two_ways_wrote_is_all_of_it(self):
        """
        Credentials this service resolved an account row through are kept as well.

        A token this platform signed names an account by the name it holds,
        and resolving one for a name this service holds no account under
        writes the account row it then answers the request from. That row is
        part of what the request wrote, so a refusal that keeps what the
        request wrote keeps it -- as the address this one replaces does, which
        answers the same refusal without raising.
        """
        signed_for = UserFactory.create(username="named-by-a-token", password=PASSWORD)
        token = create_jwt_for_user(signed_for)
        User.objects.filter(id=signed_for.id).update(username="renamed-after-signing")
        self.client.logout()
        assert_error_envelope(
            self.client.post(
                self.url, {"password": "WrongPassword9999"}, format="json",
                HTTP_AUTHORIZATION=f"JWT {token}",
            ),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug=PASSWORD_NOT_CONFIRMED_SLUG,
        )
        resolved = User.objects.get(username="named-by-a-token")
        assert [record.failure_count for record in LoginFailures.objects.filter(user=resolved)] == [1]
        assert not self.queued(resolved).exists()

    def test_a_refusal_this_collection_answers_any_other_way_keeps_nothing(self):
        """
        A refusal the request is not carried out past is rolled back whole.

        The same token resolves the same account row, and a deployment that
        does not offer this refuses the request before the password is read:
        what the request wrote is undone, the row included.
        """
        signed_for = UserFactory.create(username="named-by-another-token", password=PASSWORD)
        token = create_jwt_for_user(signed_for)
        User.objects.filter(id=signed_for.id).update(username="renamed-after-signing-too")
        self.client.logout()
        with override_settings(ENABLE_ACCOUNT_DELETION=False):
            assert_error_envelope(
                self.client.post(
                    self.url, {"password": PASSWORD}, format="json",
                    HTTP_AUTHORIZATION=f"JWT {token}",
                ),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug=DELETION_UNAVAILABLE_SLUG,
            )
        assert not User.objects.filter(username="named-by-another-token").exists()

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_an_operation_that_erases_data_keeps_nothing_when_refused_the_same_way(self):
        """
        Those two refusals are the shared catalog's, so another operation can raise one.

        What is kept is declared for the request a caller makes for their own
        retirement, which counts the attempt, and not for the type of the
        refusal: an operation that erases personal data and is refused with
        the same type has what it wrote undone.
        """
        worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
        self.held_for_the_bystander.current_state = RetirementState.objects.get(
            state_name="RETIRING_LMS",
        )
        self.held_for_the_bystander.save()
        self.login(worker)

        def refuse_after_writing(*args, **kwargs):
            """Write something of the request, then raise a refusal of the first kind."""
            LoginFailures.increment_lockout_counter(self.owner)
            raise PasswordNotConfirmed("the account's password was not confirmed")

        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.erase_account_data",
            side_effect=refuse_after_writing,
        ):
            answered = self.client.post(
                reverse("user_v2:retirement_erasure", kwargs={"username": "bystander"}),
                {"scope": "lms_core"},
                format="json",
            )
        assert answered.status_code == status.HTTP_403_FORBIDDEN, answered.content
        assert not LoginFailures.objects.filter(user=self.owner).exists()

    def test_a_request_that_is_carried_out_keeps_its_own_and_touches_no_other(self):
        """The measurement the three above are read against: the same fixtures, carried out."""
        assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert self.queued(self.owner).count() == 1
        assert UserRetirementStatus.objects.count() == 2
        self.assert_the_bystander_is_untouched()


@skip_unless_lms
class RetirementRequestCredentialTests(RetirementRequestTestCase):
    """The credentials this one operation of the collection takes."""

    def access_token_for(self, user):
        """Return an access token this platform's authorization endpoint would hand out."""
        return AccessTokenFactory(
            user=user, application=ApplicationFactory(name=f"client-of-{user.username}", user=user),
        ).token

    def test_an_access_token_is_taken_under_every_spelling_of_its_scheme(self):
        """Which spelling an application sends is configured per deployment, not chosen by the app."""
        for index, scheme in enumerate(("Bearer", "BEARER", "bearer", "bEaReR")):
            with self.subTest(scheme=scheme):
                owner = UserFactory.create(username=f"token-owner-{index}", password=PASSWORD)
                token = self.access_token_for(owner)
                self.client.logout()
                answered = self.client.post(
                    self.url, {"password": PASSWORD}, format="json",
                    HTTP_AUTHORIZATION=f"{scheme} {token}",
                )
                assert answered.status_code == status.HTTP_204_NO_CONTENT, (scheme, answered.content)
                assert self.queued(owner).count() == 1

    def test_an_access_token_nobody_handed_out_authenticates_nobody(self):
        self.client.logout()
        assert_error_envelope(
            self.client.post(
                self.url, {"password": PASSWORD}, format="json",
                HTTP_AUTHORIZATION="Bearer not-a-token-anybody-holds",
            ),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert UserRetirementStatus.objects.count() == 0

    def test_a_token_this_platform_signed_is_taken(self):
        self.client.logout()
        answered = self.client.post(
            self.url, {"password": PASSWORD}, format="json",
            HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.owner)}",
        )
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1

    def test_a_session_is_held_to_the_token_the_site_hands_out_with_it(self):
        held_to_its_token = APIClient(enforce_csrf_checks=True)
        assert held_to_its_token.login(username=self.owner.username, password=PASSWORD)
        assert_error_envelope(
            held_to_its_token.post(self.url, {"password": PASSWORD}, format="json"),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        self.assert_nothing_was_recorded(self.owner)

    def test_a_session_carrying_that_token_is_taken(self):
        """
        The token a session is held to comes from signing in, not from this address.

        A page reaches this address with a session it got by signing in, so
        the sign-in is what hands out the token the session is held to, and
        the whole way round is driven here.
        """
        held_to_its_token = APIClient(enforce_csrf_checks=True)
        sign_in_address = reverse("user_api_login_session", kwargs={"api_version": "v2"})
        form = held_to_its_token.get(sign_in_address)
        assert form.status_code == status.HTTP_200_OK, form.content
        assert settings.CSRF_COOKIE_NAME in form.cookies
        signed_in = held_to_its_token.post(
            sign_in_address,
            {"email_or_username": self.owner.email, "password": PASSWORD},
            HTTP_X_CSRFTOKEN=held_to_its_token.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.content
        answered = held_to_its_token.post(
            self.url, {"password": PASSWORD}, format="json",
            HTTP_X_CSRFTOKEN=held_to_its_token.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1


@skip_unless_lms
class PipelineCredentialTests(RetirementRequestTestCase):
    """
    What the operations carrying the work out answer a token this address takes.

    The one operation an account's owner makes for themselves takes an access
    token the authorization endpoint handed out, and that it is the only one
    is why it is declared for that operation rather than for the collection.
    Each of the others is driven here with a real token held by the caller
    whose capability it asks for, because what a view declares says nothing
    about what a request carrying that token is answered.
    """

    def setUp(self):
        super().setUp()
        self.worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
        self.held = UserRetirementStatus.create_retirement(self.bystander)
        self.member = reverse(
            "user_v2:retirement_detail", kwargs={"username": self.bystander.username},
        )

    def access_token_of_the_pipeline(self):
        """Return an access token the authorization endpoint handed the pipeline's caller."""
        return AccessTokenFactory(
            user=self.worker,
            application=ApplicationFactory(name="pipeline-client", user=self.worker),
        ).token

    def test_an_access_token_reaches_no_operation_that_carries_the_work_out(self):
        carried = {"HTTP_AUTHORIZATION": f"Bearer {self.access_token_of_the_pipeline()}"}
        self.client.logout()
        answered = {
            "the queue": self.client.get(self.url, **carried),
            "what the collection serves": self.client.options(self.url, **carried),
            "one retirement": self.client.get(self.member, **carried),
            "moving one on": self.client.patch(self.member, {}, format="json", **carried),
            "calling one off": self.client.delete(self.member, **carried),
            "erasing an account's data": self.client.post(
                f"{self.member}erasures/", {}, format="json", **carried,
            ),
            "disposing of finished records": self.client.post(
                reverse("user_v2:retirement_cleanup_list"), {}, format="json", **carried,
            ),
            "the queue of accounts to report": self.client.post(
                reverse("user_v2:retirement_partner_report_list"), {}, format="json", **carried,
            ),
            "disposing of reported records": self.client.post(
                reverse("user_v2:retirement_partner_report_cleanup_list"), {}, format="json", **carried,
            ),
        }
        assert {
            operation: sent.status_code for operation, sent in answered.items()
        } == dict.fromkeys(answered, status.HTTP_401_UNAUTHORIZED), {
            operation: sent.content for operation, sent in answered.items()
        }
        held = UserRetirementStatus.objects.get(id=self.held.id)
        assert (held.original_username, held.current_state.state_name) == (
            self.bystander.username, FIRST_STEP,
        )
        assert UserRetirementStatus.objects.count() == 1

    def test_the_same_token_is_taken_at_the_one_operation_that_declares_it(self):
        """The measurement the refusals above are read against: the token itself is a good one."""
        token = AccessTokenFactory(
            user=self.owner,
            application=ApplicationFactory(name="learner-client", user=self.owner),
        ).token
        self.client.logout()
        answered = self.client.post(
            self.url, {"password": PASSWORD}, format="json", HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1


@skip_unless_lms
class RequestResolutionTests(RetirementRequestTestCase):
    """
    Which credentials and which forms of body each operation of this collection resolves.

    These are resolved while the request is still being built, from the map
    the route was mounted with, so each is read here per address and per
    method rather than from one request's outcome: were it to fall back to
    what the view declares for every operation, the applications that hold an
    access token and send a web form would be refused at this address and
    nothing else would change.
    """

    def resolved(self, address, method):
        """Return the authenticator and parser classes a request to this address resolves."""
        match = resolve(address)
        view = match.func.cls(**match.func.initkwargs)
        view.action_map = match.func.actions
        view.request = getattr(RequestFactory(), method.lower())(address)
        return (
            [type(authenticator) for authenticator in view.get_authenticators()],
            [type(parser) for parser in view.get_parsers()],
        )

    def test_each_address_and_method_resolves_the_classes_declared_for_it(self):
        assert {
            (address, method): self.resolved(address, method)
            for address, method in (
                ("/api/user/v2/retirements/", "POST"),
                ("/api/user/v2/retirements/", "GET"),
                ("/api/user/v2/retirements/", "OPTIONS"),
                ("/api/user/v2/retirements/account-owner/", "PATCH"),
                ("/api/user/v2/retirements/account-owner/", "DELETE"),
                ("/api/user/v2/retirements/account-owner/erasures/", "POST"),
                ("/api/user/v2/retirement_cleanups/", "POST"),
            )
        } == {
            ("/api/user/v2/retirements/", "POST"): (
                OWN_REQUEST_AUTHENTICATION, OWN_REQUEST_PARSERS,
            ),
            ("/api/user/v2/retirements/", "GET"): (PLATFORM_AUTHENTICATION, [JSONParser]),
            ("/api/user/v2/retirements/", "OPTIONS"): (PLATFORM_AUTHENTICATION, [JSONParser]),
            ("/api/user/v2/retirements/account-owner/", "PATCH"): (PLATFORM_AUTHENTICATION, [JSONParser]),
            ("/api/user/v2/retirements/account-owner/", "DELETE"): (PLATFORM_AUTHENTICATION, [JSONParser]),
            ("/api/user/v2/retirements/account-owner/erasures/", "POST"): (
                PLATFORM_AUTHENTICATION, [JSONParser],
            ),
            ("/api/user/v2/retirement_cleanups/", "POST"): (PLATFORM_AUTHENTICATION, [JSONParser]),
        }

    def test_the_operations_that_declare_none_of_their_own_take_two_named_credentials(self):
        """
        Those two are named here, so a third configured for everything else is a failure.

        A class that takes an access token the authorization endpoint handed
        out, added to what a deployment configures for every operation alike,
        would otherwise reach the operations that move, erase and delete
        records while every expectation above went on holding.
        """
        assert list(APIView.authentication_classes) == PLATFORM_AUTHENTICATION

    def test_the_disposal_of_finished_records_is_reached_with_the_platform_credentials_alone(self):
        """The operation it serves is named ``create`` too, and it deletes records for the pipeline."""
        assert "action_authentication" not in vars(RetirementCleanupViewSet)
        assert "action_parsers" not in vars(RetirementCleanupViewSet)
        assert self.resolved("/api/user/v2/retirement_cleanups/", "POST") == (
            PLATFORM_AUTHENTICATION, [JSONParser],
        )

    def test_the_collection_declares_no_credentials_for_all_of_its_operations_at_once(self):
        """Declaring them for the whole class is what would widen the operations that erase data."""
        assert "authentication_classes" not in vars(RetirementViewSet)
        assert set(RetirementViewSet.action_authentication) == {"create"}
        assert set(RetirementViewSet.action_parsers) == {"create"}

    def test_the_request_resolves_no_record_of_the_collection(self):
        """It writes a record for the caller and reads none, so nothing narrows a selection here."""
        self.login(self.owner)
        with mock.patch.object(
            RetirementViewSet, "get_queryset", side_effect=AssertionError("no selection is read"),
        ):
            assert self.ask().status_code == status.HTTP_204_NO_CONTENT
        assert self.queued(self.owner).count() == 1


@skip_unless_lms
class CollectionSelfDescriptionTests(RetirementRequestTestCase):
    """
    What a caller asking an address of this collection what it serves is told.

    A caller negotiating from the description sends what it was told to send,
    so what the description says about the forms a body may be sent in and
    about the body itself is held to what each address really reads. The
    request asking for the description is an operation of the collection in
    its own right, and what it reads is not what it is being asked about.
    """

    def setUp(self):
        super().setUp()
        self.worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
        self.member = reverse("user_v2:retirement_detail", kwargs={"username": "bystander"})
        self.login(self.worker)

    def description_of(self, address):
        """Return what this address says it serves."""
        answered = self.client.options(address)
        assert answered.status_code == status.HTTP_200_OK, answered.content
        return answered.json()

    def test_the_collection_reads_a_body_in_the_forms_its_own_methods_read_it_in(self):
        """One of its methods reads three forms and the rest read one, so it names three."""
        assert self.description_of(self.url)["parses"] == [
            "application/json", FORM, "multipart/form-data",
        ]

    def test_the_collection_describes_the_body_its_write_carries(self):
        """The record the collection answers with is not what a caller sends it."""
        described = self.description_of(self.url)["actions"]["POST"]
        assert list(described) == ["password"]
        assert described["password"]["required"] is True

    def test_one_record_reads_a_body_in_the_one_form_its_own_methods_read(self):
        assert self.description_of(self.member)["parses"] == ["application/json"]

    def test_the_address_that_erases_data_describes_the_body_it_reads(self):
        """Each operation of the collection names the body it reads, not the first of them."""
        described = self.description_of(f"{self.member}erasures/")["actions"]["POST"]
        assert list(described) == ["scope"]
        assert described["scope"]["required"] is True


@skip_unless_lms
class EarlierAddressTests(RetirementRequestTestCase):
    """The address this operation replaces is left exactly as it was."""

    def test_the_earlier_address_still_answers_its_own_callers(self):
        self.login(self.owner)
        answered = self.client.post(EARLIER_ADDRESS, {"password": PASSWORD})
        assert answered.status_code == status.HTTP_204_NO_CONTENT, answered.content
        assert self.queued(self.owner).count() == 1

    def test_the_name_the_earlier_address_is_reversed_by_still_reverses(self):
        assert reverse("deactivate_logout") == EARLIER_ADDRESS
