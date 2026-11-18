"""Behaviour parity between the two addresses an account's owner closes it at."""

import json

import pytest
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.errors import error_type_uri
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from social_django.models import UserSocialAuth

from common.djangoapps.student.models import LoginFailures, Registration, get_retired_email_by_email
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangoapps.user_api.accounts import views as legacy_views
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserRetirementRequest,
    UserRetirementStatus,
)
from openedx.core.djangoapps.user_api.v2 import view_services
from openedx.core.djangoapps.user_api.v2.views import RetirementViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
WRONG_PASSWORD = "NotThePassword9999"
FORM = "application/x-www-form-urlencoded"

#: The two addresses, by the version serving them.
EARLIER = "/api/user/v1/accounts/deactivate_logout/"

#: The step a request is queued at, and the order that step carries.
FIRST_STEP = "PENDING"
FIRST_STEP_ORDER = 1

#: The type each refusal of the conforming address carries. The earlier
#: address answers the same status for all of them, with no body at all for
#: two and an empty string for the third, which is the difference below.
PASSWORD_NOT_CONFIRMED_SLUG = "user/password-not-confirmed"
TEMPORARILY_LOCKED_SLUG = "user/account-temporarily-locked"
DELETION_UNAVAILABLE_SLUG = "user/account-deletion-unavailable"
STATE_CONFLICT_SLUG = "user/retirement-state-conflict"

#: The differences a caller moving between the two addresses would notice.
#: Each is measured on both addresses by the tests below, so a difference that
#: is not listed here fails rather than passing unnoticed.
DECLARED_DIFFERENCES = (
    (
        "a body sent as JSON",
        "the earlier address parses it and then reads the form it was not sent as, so it refuses "
        "every JSON request; the conforming one reads it",
    ),
    (
        "a body in a form neither address reads",
        "the earlier address answers that it could not read the password, the conforming one that "
        "it cannot read the form",
    ),
    (
        "a refusal of the request itself",
        "the earlier address answers one status with no body for three different reasons; the "
        "conforming one names each of them",
    ),
    (
        "an account that already has a record while its password still signs its owner in",
        "the earlier address answers this server's failure carrying the sentence the pipeline "
        "raised; the conforming one answers that the request conflicts with what is held",
    ),
    (
        "asking what the address serves",
        "the earlier address answers its own description to any caller holding an account; the "
        "conforming one answers the description of the queue, and asks for the capability the "
        "queue is read with",
    ),
)


@pytest.mark.usefixtures("setup_retirement_states")
class RequestParityTestCase(APITestCase):
    """One account per address, so that neither test acts on the other's record."""

    def setUp(self):
        super().setUp()
        self.conforming_url = reverse("user_v2:retirement_list")
        self.earlier_url = reverse("deactivate_logout")
        self.at_the_earlier_address = UserFactory.create(
            username="earlier-owner", email="earlier@example.com", password=PASSWORD,
        )
        self.at_the_conforming_address = UserFactory.create(
            username="conforming-owner", email="conforming@example.com", password=PASSWORD,
        )

    def owner_of(self, address):
        """Return the account whose owner calls this address."""
        return {
            EARLIER: self.at_the_earlier_address,
            "conforming": self.at_the_conforming_address,
        }[address]

    def addresses(self):
        """Return each address with the account whose owner calls it."""
        return ((EARLIER, self.at_the_earlier_address), ("conforming", self.at_the_conforming_address))

    def url_of(self, address):
        """Return the address to send to."""
        return self.earlier_url if address == EARLIER else self.conforming_url

    def ask(self, address, password=PASSWORD, body="form", client=None, **kwargs):
        """Ask for the retirement of the account whose owner calls this address."""
        client = client or self.client
        url = self.url_of(address)
        if body == "form":
            return client.post(url, f"password={password}", content_type=FORM, **kwargs)
        if body == "multipart":
            return client.post(url, {"password": password}, format="multipart", **kwargs)
        if body == "json":
            return client.post(
                url, json.dumps({"password": password}), content_type="application/json", **kwargs
            )
        return client.post(url, password, content_type="text/plain", **kwargs)

    def signed_in(self, user):
        """Return a client holding a session for this account."""
        client = APIClient()
        assert client.login(username=user.username, password=PASSWORD)
        return client

    def holding_a_token(self, user):
        """Return the header a caller holding a token this platform signed sends."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def holding_an_access_token(self, user, scheme="Bearer"):
        """Return the header a caller holding an access token sends."""
        token = AccessTokenFactory(
            user=user, application=ApplicationFactory(name=f"client-{user.username}", user=user),
        ).token
        return {"HTTP_AUTHORIZATION": f"{scheme} {token}"}

    def records_of(self, user):
        """Return the retirement records held for this account."""
        return UserRetirementStatus.objects.filter(user=user)

    def messages_to(self, address):
        """Return the messages sent to this address."""
        return [message for message in mail.outbox if address in message.to]


@skip_unless_lms
class RequestOutcomeParityTests(RequestParityTestCase):
    """What a request that is carried out leaves behind, measured at both addresses."""

    def carry_out(self, address, body="form"):
        """Make the request both addresses serve, as a caller holding a session."""
        owner = self.owner_of(address)
        UserSocialAuth.objects.create(user=owner, provider="a_provider", uid=owner.email)
        Registration().register(owner)
        client = self.signed_in(owner)
        answered = self.ask(address, body=body, client=client)
        return answered, client

    def state_left_behind(self, owner):
        """Return everything about the account and its record that either address decides."""
        record = self.records_of(owner).get()
        stored = User.objects.get(id=owner.id)
        return {
            "step": record.current_state.state_name,
            "order": record.current_state.state_execution_order,
            "name on the record": record.original_username,
            "address on the record": record.original_email,
            "requests recorded": UserRetirementRequest.objects.filter(user=owner).count(),
            "address on the account": stored.email,
            "the password still signs its owner in": stored.has_usable_password(),
            "keys sent to confirm the address": Registration.objects.filter(user=owner).count(),
            "social logins": [auth.provider for auth in UserSocialAuth.objects.filter(user=owner)],
            "messages to the address it held": len(self.messages_to(owner.email)),
        }

    def test_both_addresses_leave_the_same_thing_behind(self):
        left = {}
        for address, owner in self.addresses():
            answered, _client = self.carry_out(address)
            assert answered.status_code == status.HTTP_204_NO_CONTENT, (address, answered.content)
            left[address] = self.state_left_behind(owner)
        for address, owner in self.addresses():
            assert left[address] == {
                "step": FIRST_STEP,
                "order": FIRST_STEP_ORDER,
                "name on the record": owner.username,
                "address on the record": owner.email,
                "requests recorded": 1,
                "address on the account": get_retired_email_by_email(owner.email),
                "the password still signs its owner in": False,
                "keys sent to confirm the address": 0,
                "social logins": ["a_provider"],
                "messages to the address it held": 1,
            }, address

    def test_neither_address_answers_a_body(self):
        for address, _owner in self.addresses():
            answered, _client = self.carry_out(address)
            assert (answered.status_code, answered.content) == (status.HTTP_204_NO_CONTENT, b""), address

    def test_the_session_the_request_was_made_from_authenticates_at_neither_afterwards(self):
        for address, _owner in self.addresses():
            answered, client = self.carry_out(address)
            assert answered.status_code == status.HTTP_204_NO_CONTENT
            again = self.ask(address, client=client)
            assert again.status_code == status.HTTP_401_UNAUTHORIZED, (address, again.content)

    def test_a_body_sent_as_a_file_upload_is_read_at_both(self):
        for address, _owner in self.addresses():
            answered, _client = self.carry_out(address, body="multipart")
            assert answered.status_code == status.HTTP_204_NO_CONTENT, (address, answered.content)

    def test_a_body_sent_as_json_is_read_only_at_the_conforming_address(self):
        """The earlier address parses JSON and then reads the form it was not sent as."""
        answered = {}
        for address, _owner in self.addresses():
            sent, _client = self.carry_out(address, body="json")
            answered[address] = sent.status_code
        assert answered == {
            EARLIER: status.HTTP_400_BAD_REQUEST,
            "conforming": status.HTTP_204_NO_CONTENT,
        }
        assert not self.records_of(self.at_the_earlier_address).exists()
        assert self.records_of(self.at_the_conforming_address).count() == 1

    def test_a_body_in_a_form_neither_address_reads_is_refused_by_both(self):
        answered = {}
        for address, _owner in self.addresses():
            sent, _client = self.carry_out(address, body="text")
            answered[address] = sent.status_code
        assert answered == {
            EARLIER: status.HTTP_400_BAD_REQUEST,
            "conforming": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        }
        assert UserRetirementStatus.objects.count() == 0


@skip_unless_lms
class RefusalParityTests(RequestParityTestCase):
    """Both addresses refuse the same requests, and the conforming one says which refusal it is."""

    def test_a_password_the_account_does_not_hold_is_refused_by_both(self):
        answered = {}
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            answered[address] = self.ask(address, password=WRONG_PASSWORD, client=client)
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        # The earlier address answers the status with an empty string, which is
        # what two web clients read as "the password was wrong" however the
        # request was refused; the conforming one names the refusal.
        assert answered[EARLIER].json() == ""
        assert answered["conforming"].json()["type"] == error_type_uri(PASSWORD_NOT_CONFIRMED_SLUG)
        assert answered["conforming"].json()["detail"] == view_services.PASSWORD_NOT_CONFIRMED
        assert UserRetirementStatus.objects.count() == 0

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_every_refused_attempt_is_counted_by_both(self):
        """The count is what closes an account to further attempts, so both have to keep it."""
        counted = {}
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            assert self.ask(
                address, password=WRONG_PASSWORD, client=client,
            ).status_code == status.HTTP_403_FORBIDDEN
            counted[address] = [
                record.failure_count for record in LoginFailures.objects.filter(user=owner)
            ]
        assert counted == {EARLIER: [1], "conforming": [1]}

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_an_empty_password_is_refused_and_counted_by_both(self):
        """Neither refuses it as a malformed value, so blank attempts count towards closing the account."""
        answered, counted = {}, {}
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            answered[address] = self.ask(address, password="", client=client)
            counted[address] = [record.failure_count for record in LoginFailures.objects.filter(user=owner)]
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        assert answered[EARLIER].json() == ""
        assert answered["conforming"].json()["type"] == error_type_uri(PASSWORD_NOT_CONFIRMED_SLUG)
        assert answered["conforming"].json()["detail"] == view_services.PASSWORD_NOT_CONFIRMED
        assert counted == {EARLIER: [1], "conforming": [1]}
        assert UserRetirementStatus.objects.count() == 0

    @override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True)
    def test_an_account_closed_to_attempts_is_refused_by_both(self):
        answered = {}
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            for _ in range(settings.MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED):
                self.ask(address, password=WRONG_PASSWORD, client=client)
            assert LoginFailures.objects.get(user=owner).lockout_until is not None
            answered[address] = self.ask(address, client=client)
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        assert answered[EARLIER].json() == ""
        assert answered["conforming"].json()["type"] == error_type_uri(TEMPORARILY_LOCKED_SLUG)
        assert UserRetirementStatus.objects.count() == 0

    @override_settings(ENABLE_ACCOUNT_DELETION=False)
    def test_a_deployment_that_does_not_offer_this_refuses_both(self):
        answered = {}
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            answered[address] = self.ask(address, client=client)
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        # The earlier address answers no body at all here, which is the third
        # reason a caller cannot tell from the other two.
        assert answered[EARLIER].content == b""
        assert answered["conforming"].json()["type"] == error_type_uri(DELETION_UNAVAILABLE_SLUG)
        assert UserRetirementStatus.objects.count() == 0

    def test_a_site_that_does_not_offer_this_refuses_both(self):
        answered = {}
        with with_site_configuration_context(configuration={"ENABLE_ACCOUNT_DELETION": False}):
            for address, owner in self.addresses():
                client = self.signed_in(owner)
                answered[address] = self.ask(address, client=client)
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        assert UserRetirementStatus.objects.count() == 0

    def test_a_second_request_is_refused_by_both_for_the_same_reason(self):
        """The first made the password unusable, so the second cannot be confirmed with it."""
        answered = {}
        for address, owner in self.addresses():
            held = self.holding_a_token(owner)
            first = self.ask(address, **held)
            assert first.status_code == status.HTTP_204_NO_CONTENT, (address, first.content)
            answered[address] = self.ask(address, **self.holding_a_token(owner))
            assert self.records_of(owner).count() == 1
            assert len(self.messages_to(owner.email)) == 1
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        assert answered["conforming"].json()["type"] == error_type_uri(PASSWORD_NOT_CONFIRMED_SLUG)

    def test_a_record_made_while_the_password_still_works_is_answered_differently(self):
        """The earlier address answers this server's failure, carrying the sentence it raised."""
        answered = {}
        for address, owner in self.addresses():
            UserRetirementStatus.create_retirement(owner)
            client = self.signed_in(owner)
            answered[address] = self.ask(address, client=client)
            assert self.records_of(owner).count() == 1
            assert User.objects.get(id=owner.id).check_password(PASSWORD) is True
            assert self.messages_to(owner.email) == []
        assert answered[EARLIER].status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "already has a retirement status row" in answered[EARLIER].json()
        assert answered["conforming"].status_code == status.HTTP_409_CONFLICT
        assert answered["conforming"].json()["type"] == error_type_uri(STATE_CONFLICT_SLUG)
        assert answered["conforming"].json()["detail"] == view_services.RETIREMENT_ALREADY_REQUESTED

    def test_neither_address_carries_the_password_back(self):
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            refused = self.ask(address, password=WRONG_PASSWORD, client=client)
            assert WRONG_PASSWORD not in refused.content.decode(), address
            assert PASSWORD not in refused.content.decode(), address


@skip_unless_lms
class CredentialParityTests(RequestParityTestCase):
    """Both addresses take the credentials the shipped applications hold."""

    def test_an_access_token_is_taken_at_both_under_every_spelling_of_its_scheme(self):
        """Which spelling is sent is configured per deployment, so every one of them is driven."""
        for index, scheme in enumerate(("Bearer", "BEARER", "bearer", "bEaReR")):
            for address in (EARLIER, "conforming"):
                with self.subTest(scheme=scheme, address=address):
                    owner = UserFactory.create(
                        username=f"token-{index}-{address == EARLIER}", password=PASSWORD,
                    )
                    answered = self.ask(
                        address, client=APIClient(), **self.holding_an_access_token(owner, scheme),
                    )
                    assert answered.status_code == status.HTTP_204_NO_CONTENT, (
                        scheme, address, answered.content,
                    )
                    assert self.records_of(owner).count() == 1

    def test_an_access_token_nobody_handed_out_authenticates_nobody_at_either(self):
        answered = {}
        for address, _owner in self.addresses():
            answered[address] = self.ask(
                address,
                client=APIClient(),
                HTTP_AUTHORIZATION="Bearer not-a-token-anybody-holds",
            )
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_401_UNAUTHORIZED,
        )
        # The earlier address answers the library's own members at the top of
        # the body; the conforming one answers the body every refusal of this
        # version carries.
        assert set(answered[EARLIER].json()) == {"error_code", "developer_message"}
        assert set(answered["conforming"].json()) == {"type", "title", "status", "detail", "instance"}

    def test_a_session_is_held_to_the_token_the_site_hands_out_with_it_at_both(self):
        answered = {}
        for address, owner in self.addresses():
            held_to_its_token = APIClient(enforce_csrf_checks=True)
            assert held_to_its_token.login(username=owner.username, password=PASSWORD)
            answered[address] = self.ask(address, client=held_to_its_token)
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_403_FORBIDDEN,
        )
        assert UserRetirementStatus.objects.count() == 0

    def test_a_session_carrying_that_token_is_taken_at_both(self):
        answered = {}
        for address, owner in self.addresses():
            held_to_its_token = APIClient(enforce_csrf_checks=True)
            sign_in = reverse("user_api_login_session", kwargs={"api_version": "v2"})
            assert held_to_its_token.get(sign_in).status_code == status.HTTP_200_OK
            token = held_to_its_token.cookies[settings.CSRF_COOKIE_NAME].value
            assert held_to_its_token.post(
                sign_in,
                {"email_or_username": owner.email, "password": PASSWORD},
                HTTP_X_CSRFTOKEN=token,
            ).status_code == status.HTTP_200_OK
            answered[address] = self.ask(
                address, client=held_to_its_token,
                HTTP_X_CSRFTOKEN=held_to_its_token.cookies[settings.CSRF_COOKIE_NAME].value,
            )
        assert {address: sent.status_code for address, sent in answered.items()} == dict.fromkeys(
            answered, status.HTTP_204_NO_CONTENT,
        ), {address: sent.content for address, sent in answered.items()}


#: Everything a caller can notice about either address, as a measurement made
#: at both of them, named by the method that makes it. What that method
#: answers is compared across the two addresses, so a measurement that comes
#: back different while no difference is declared for it fails, and a declared
#: difference both addresses answer the same way fails too. Each measurement
#: acts on an account of its own, because a request that is carried out leaves
#: the account it was made from closed to the next one.
MEASURED_AT_BOTH = (
    ("a body sent as a web form", "measure_a_body_sent_as_a_web_form"),
    ("a body sent as JSON", "measure_a_body_sent_as_json"),
    ("a body in a form neither address reads", "measure_a_body_in_a_form_neither_address_reads"),
    ("a refusal of the request itself", "measure_a_refusal_of_the_request_itself"),
    ("an empty password", "measure_an_empty_password"),
    (
        "an account that already has a record while its password still signs its owner in",
        "measure_an_account_that_already_has_a_record",
    ),
    ("the step a request is queued at", "measure_the_step_a_request_is_queued_at"),
    ("what the request leaves behind", "measure_what_the_request_leaves_behind"),
    ("the session the request was made from", "measure_the_session_the_request_was_made_from"),
    ("an access token the endpoint handed out", "measure_an_access_token_the_endpoint_handed_out"),
    ("asking what the address serves", "measure_asking_what_the_address_serves"),
)


@skip_unless_lms
class DeclaredDifferenceTests(RequestParityTestCase):
    """
    The differences between the two addresses are the ones written down.

    Each measurement is made at both addresses and the two answers are held
    against each other, so the list of differences is read from what the two
    addresses do rather than restated beside itself.
    """

    def a_caller_of_their_own(self, address, named):
        """Return an account this measurement alone acts on, with a session held for it."""
        owner = UserFactory.create(
            username=f"{named}-{address == EARLIER}",
            email=f"{named}-{address == EARLIER}@example.com",
            password=PASSWORD,
        )
        return owner, self.signed_in(owner)

    def measure_a_body_sent_as_a_web_form(self, address):
        """Return what each address answers the body both of them read."""
        owner, client = self.a_caller_of_their_own(address, "form-body")
        answered = self.ask(address, client=client)
        return (answered.status_code, self.records_of(owner).count())

    def measure_a_body_sent_as_json(self, address):
        """Return what each address answers a body sent as JSON."""
        _owner, client = self.a_caller_of_their_own(address, "json-body")
        return self.ask(address, body="json", client=client).status_code

    def measure_a_body_in_a_form_neither_address_reads(self, address):
        """Return what each address answers a body sent as plain text."""
        _owner, client = self.a_caller_of_their_own(address, "plain-body")
        return self.ask(address, body="text", client=client).status_code

    def measure_a_refusal_of_the_request_itself(self, address):
        """
        Return how far each address tells a caller what it refused the request for.

        What is measured is the shape of the answer rather than its words: one
        address answers a sentence, which says the same thing however the
        request was refused, and the other answers an object whose members
        name which refusal it was.
        """
        _owner, client = self.a_caller_of_their_own(address, "wrong-password")
        answered = self.ask(address, password=WRONG_PASSWORD, client=client)
        refusal = answered.json()
        return (
            answered.status_code,
            sorted(refusal) if isinstance(refusal, dict) else "not an object at all",
        )

    def measure_an_empty_password(self, address):
        """Return what each address answers an empty password, what it counts, and what it records."""
        owner, client = self.a_caller_of_their_own(address, "empty-password")
        with override_settings(ENABLE_MAX_FAILED_LOGIN_ATTEMPTS=True):
            answered = self.ask(address, password="", client=client)
        return (
            answered.status_code,
            [record.failure_count for record in LoginFailures.objects.filter(user=owner)],
            self.records_of(owner).count(),
        )

    def measure_an_account_that_already_has_a_record(self, address):
        """Return what each address answers where a record is held and the password still works."""
        owner, client = self.a_caller_of_their_own(address, "already-recorded")
        UserRetirementStatus.create_retirement(owner)
        return self.ask(address, client=client).status_code

    def measure_the_step_a_request_is_queued_at(self, address):
        """Return the step a request is queued at, and the order that step carries."""
        owner, client = self.a_caller_of_their_own(address, "first-step")
        self.ask(address, client=client)
        queued = self.records_of(owner).get().current_state
        return (queued.state_name, queued.state_execution_order)

    def measure_what_the_request_leaves_behind(self, address):
        """Return what is stored about the account once the request has been carried out."""
        owner, client = self.a_caller_of_their_own(address, "left-behind")
        self.ask(address, client=client)
        stored = User.objects.get(id=owner.id)
        return (
            self.records_of(owner).count(),
            UserRetirementRequest.objects.filter(user=owner).count(),
            stored.email == get_retired_email_by_email(owner.email),
            stored.check_password(PASSWORD),
            len(self.messages_to(owner.email)),
        )

    def measure_the_session_the_request_was_made_from(self, address):
        """Return what the same session is answered once the account is queued."""
        _owner, client = self.a_caller_of_their_own(address, "session-after")
        self.ask(address, client=client)
        return self.ask(address, client=client).status_code

    def measure_an_access_token_the_endpoint_handed_out(self, address):
        """Return what each address answers a request carrying an access token."""
        owner = UserFactory.create(
            username=f"token-holder-{address == EARLIER}", password=PASSWORD,
        )
        answered = self.ask(address, **self.holding_an_access_token(owner))
        return (answered.status_code, self.records_of(owner).count())

    def measure_asking_what_the_address_serves(self, address):
        """
        Return what each address answers a caller asking for its own description.

        The description is each address's own text and differs by design, so
        what is measured is whether the caller is given one at all.
        """
        _owner, client = self.a_caller_of_their_own(address, "describing")
        return client.options(self.url_of(address)).status_code

    def test_the_differences_a_caller_would_notice_are_the_ones_declared(self):
        """A measured difference that is not declared, or declared and not measured, fails."""
        measured = {}
        for difference, measurement in MEASURED_AT_BOTH:
            measured[difference] = {
                address: getattr(self, measurement)(address) for address, _owner in self.addresses()
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

    def test_both_views_read_the_body_in_the_forms_their_callers_send_it_in(self):
        """The view each address is served by resolves these, which is what the cases above drive."""
        earlier = legacy_views.DeactivateLogoutView()
        assert [type(parser).__name__ for parser in earlier.get_parsers()] == [
            "JSONParser", "FormParser", "MultiPartParser",
        ]
        conforming = RetirementViewSet()
        conforming.action = "create"
        assert [type(parser).__name__ for parser in conforming.get_parsers()] == [
            "JSONParser", "FormParser", "MultiPartParser",
        ]

    def test_both_views_take_the_same_credentials_for_this_operation(self):
        earlier = legacy_views.DeactivateLogoutView()
        conforming = RetirementViewSet()
        conforming.action = "create"
        assert [type(authenticator) for authenticator in conforming.get_authenticators()] == [
            type(authenticator) for authenticator in earlier.get_authenticators()
        ]

    def test_the_configured_steps_decide_where_a_request_is_queued_at_both(self):
        """Neither address names a step, so a deployment that renames the first one is served."""
        RetirementState.objects.filter(state_name=FIRST_STEP).update(state_name="WAITING")
        for address, owner in self.addresses():
            client = self.signed_in(owner)
            assert self.ask(address, client=client).status_code == status.HTTP_204_NO_CONTENT
            assert self.records_of(owner).get().current_state.state_name == "WAITING"
