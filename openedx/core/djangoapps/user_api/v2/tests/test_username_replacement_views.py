"""Tests for the replacement of the names a set of accounts are stored under."""

import json
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.errors import INTERNAL_ERROR_SLUG, INTERNAL_ERROR_TITLE, error_type_uri
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.test import APITestCase
from rest_framework.views import APIView

from common.djangoapps.student.tests.factories import AdminFactory, SuperuserFactory, UserFactory
from openedx.core.djangoapps.user_api.accounts.permissions import CanReplaceUsername
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus,
)
from openedx.core.djangoapps.user_api.v2 import view_services
from openedx.core.djangoapps.user_api.v2.serializers import (
    REPLACEMENT_MAX_MAPPINGS,
    REPLACEMENT_NO_SUCH_ACCOUNT,
    REPLACEMENT_REPLACED,
)
from openedx.core.djangoapps.user_api.v2.views import UsernameReplacementViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_views import MALFORMED_BODY_SLUG, REFUSED_METHOD_SLUG, UNSUPPORTED_MEDIA_TYPE_SLUG

PASSWORD = "Password1234"

#: The caller a deployment names for replacing the names accounts are stored
#: under, which is the only caller this operation serves.
REPLACEMENT_WORKER_USERNAME = "replacement-worker"

#: The address the operation this one replaces is served at.
EARLIER_ADDRESS = "/api/user/v1/accounts/replace_usernames/"

FORM = "application/x-www-form-urlencoded"


@override_settings(USERNAME_REPLACEMENT_WORKER=REPLACEMENT_WORKER_USERNAME)
class UsernameReplacementTestCase(APITestCase):
    """The callers and the fixtures the tests of this operation share."""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:username_replacement_list")
        self.replacer = UserFactory.create(username=REPLACEMENT_WORKER_USERNAME, password=PASSWORD)
        self.learner = UserFactory.create(username="plain-learner", password=PASSWORD)

    def login(self, user):
        """Sign this caller in through the session the platform default accepts."""
        self.client.logout()
        assert self.client.login(username=user.username, password=PASSWORD)

    def ask(self, mappings, client=None):
        """Ask for these names to be replaced."""
        return (client or self.client).post(
            self.url, {"username_mappings": mappings}, format="json",
        )

    def results_of(self, response):
        """Return the results the answer carries."""
        return response.json()["results"]

    def names_stored(self):
        """Return the names every account is stored under."""
        return set(User.objects.values_list("username", flat=True))


@skip_unless_lms
class UsernameReplacementAccessTests(UsernameReplacementTestCase):
    """Only the caller a deployment names for this work reaches it."""

    def test_an_anonymous_caller_is_unauthenticated(self):
        self.client.logout()
        assert_error_envelope(
            self.ask([{"plain-learner": "renamed"}]),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert "plain-learner" in self.names_stored()

    def test_every_other_caller_is_forbidden(self):
        callers = {
            "learner": self.learner,
            "staff": AdminFactory.create(username="plain-staff", password=PASSWORD),
            "superuser": SuperuserFactory.create(username="super-caller", password=PASSWORD),
        }
        for role, caller in callers.items():
            with self.subTest(caller=role):
                self.login(caller)
                assert_error_envelope(
                    self.ask([{"plain-learner": "renamed"}]),
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug="authz",
                )
        assert "plain-learner" in self.names_stored()

    def test_the_caller_the_deployment_names_is_served(self):
        self.login(self.replacer)
        answered = self.ask([{"plain-learner": "renamed"}])
        assert answered.status_code == status.HTTP_200_OK, answered.content
        assert "renamed" in self.names_stored()

    def test_the_caller_the_deployment_names_is_the_name_it_configured(self):
        """A caller holding that name at another deployment reaches nothing here."""
        with override_settings(USERNAME_REPLACEMENT_WORKER="somebody-else"):
            self.login(self.replacer)
            assert_error_envelope(
                self.ask([{"plain-learner": "renamed"}]),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )
        assert "plain-learner" in self.names_stored()

    def test_asking_what_the_address_serves_asks_for_the_same_capability(self):
        self.login(self.replacer)
        described = self.client.options(self.url)
        assert described.status_code == status.HTTP_200_OK
        assert described.data["renders"] == ["application/json"]
        self.login(self.learner)
        assert_error_envelope(
            self.client.options(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_an_operation_added_later_reaches_no_capability(self):
        viewset = UsernameReplacementViewSet()
        viewset.action = "an_action_added_later"
        assert [
            permission.has_permission(mock.Mock(user=self.replacer), viewset)
            for permission in viewset.get_permissions()
        ][-1] is False

    def test_the_capability_asked_for_is_the_one_the_earlier_address_asks_for(self):
        """The class is the same one, read from where the earlier address reads it."""
        assert UsernameReplacementViewSet.action_permissions == {
            "create": (CanReplaceUsername,),
            "metadata": (CanReplaceUsername,),
        }

    def test_the_view_declares_no_credentials_of_its_own(self):
        assert "authentication_classes" not in vars(UsernameReplacementViewSet)
        assert UsernameReplacementViewSet.action_authentication == {}
        assert [
            type(authenticator) for authenticator in UsernameReplacementViewSet().get_authenticators()
        ] == list(APIView.authentication_classes)

    def test_the_view_reads_a_body_as_json_alone(self):
        assert UsernameReplacementViewSet.parser_classes == (JSONParser,)
        assert UsernameReplacementViewSet.action_parsers == {}

    def test_a_method_the_address_does_not_serve_is_answered_as_not_served(self):
        self.login(self.replacer)
        refused = self.client.get(self.url)
        assert_error_envelope(
            refused,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug=REFUSED_METHOD_SLUG,
        )
        assert set(refused["Allow"].replace(" ", "").split(",")) == {"POST", "OPTIONS"}


@skip_unless_lms
class UsernameReplacementTests(UsernameReplacementTestCase):
    """What a replacement does, and what it reports about each mapping it was given."""

    def setUp(self):
        super().setUp()
        self.login(self.replacer)

    def test_a_name_an_account_is_stored_under_is_replaced_and_reported(self):
        answered = self.ask([{"plain-learner": "asked-for"}])
        assert answered.status_code == status.HTTP_200_OK, answered.content
        assert self.results_of(answered) == [{
            "current_username": "plain-learner",
            "new_username": "asked-for",
            "outcome": REPLACEMENT_REPLACED,
        }]
        assert User.objects.get(id=self.learner.id).username == "asked-for"

    def test_a_name_no_account_is_stored_under_is_reported_as_such(self):
        """The earlier address reports this as a replacement that was carried out."""
        answered = self.ask([{"nobody-holds-this": "asked-for"}])
        assert self.results_of(answered) == [{
            "current_username": "nobody-holds-this",
            "new_username": None,
            "outcome": REPLACEMENT_NO_SUCH_ACCOUNT,
        }]
        assert "asked-for" not in self.names_stored()

    def test_a_name_another_account_holds_is_handed_out_with_a_suffix(self):
        UserFactory.create(username="already-held")
        answered = self.ask([{"plain-learner": "already-held"}])
        result = self.results_of(answered)[0]
        assert result["outcome"] == REPLACEMENT_REPLACED
        assert result["new_username"].startswith("already-held-")
        assert result["new_username"] != "already-held"
        assert User.objects.get(id=self.learner.id).username == result["new_username"]
        assert User.objects.filter(username="already-held").count() == 1

    def test_the_suffix_is_added_again_where_the_name_it_made_is_held_too(self):
        """A generated name can be taken as well, so the result is tried until nobody holds it."""
        UserFactory.create(username="already-held")
        taken = ["already-held-aaa111", "already-held-bbb222"]
        for name in taken:
            UserFactory.create(username=name)
        with mock.patch.object(
            view_services, "username_suffix_generator", side_effect=["aaa111", "bbb222", "ccc333"],
        ):
            answered = self.ask([{"plain-learner": "already-held"}])
        assert self.results_of(answered)[0]["new_username"] == "already-held-ccc333"

    def test_every_mapping_is_reported_in_the_order_it_was_given(self):
        others = [UserFactory.create(username=f"learner-{index}") for index in range(3)]
        mappings = [{"nobody-holds-this": "first"}]
        mappings += [{other.username: f"renamed-{index}"} for index, other in enumerate(others)]
        answered = self.ask(mappings)
        assert [result["current_username"] for result in self.results_of(answered)] == [
            "nobody-holds-this", "learner-0", "learner-1", "learner-2",
        ]
        assert [result["outcome"] for result in self.results_of(answered)] == [
            REPLACEMENT_NO_SUCH_ACCOUNT, REPLACEMENT_REPLACED, REPLACEMENT_REPLACED, REPLACEMENT_REPLACED,
        ]

    def test_a_name_given_twice_is_acted_on_once_and_reported_twice(self):
        """The first replaces it, which leaves the second nothing stored under that name."""
        answered = self.ask([{"plain-learner": "first-name"}, {"plain-learner": "second-name"}])
        assert [result["outcome"] for result in self.results_of(answered)] == [
            REPLACEMENT_REPLACED, REPLACEMENT_NO_SUCH_ACCOUNT,
        ]
        assert User.objects.get(id=self.learner.id).username == "first-name"
        assert "second-name" not in self.names_stored()

    def test_the_answer_carries_nothing_but_the_results(self):
        answered = self.ask([{"plain-learner": "asked-for"}])
        assert set(answered.json()) == {"results"}


@skip_unless_lms
class EveryPlaceTheNameIsKeptTests(UsernameReplacementTestCase):
    """The name is replaced in every place this service keeps it."""

    def setUp(self):
        super().setUp()
        self.login(self.replacer)

    def test_every_place_a_name_is_kept_resolves(self):
        """A renamed column or an application a deployment does not install is caught here."""
        resolved = []
        for model, column in view_services.NAMES_ARE_ALSO_KEPT_IN:
            with self.subTest(model=model, column=column):
                held = apps.get_model(model)
                assert held._meta.get_field(column) is not None  # pylint: disable=protected-access
                resolved.append((model, column))
        assert tuple(resolved) == (
            ("auth.user", "username"),
            ("consent.DataSharingConsent", "username"),
            ("consent.HistoricalDataSharingConsent", "username"),
            ("credit.CreditEligibility", "username"),
            ("credit.CreditRequest", "username"),
            ("credit.CreditRequirementStatus", "username"),
            ("user_api.UserRetirementPartnerReportingStatus", "original_username"),
            ("user_api.UserRetirementStatus", "original_username"),
        )

    def test_the_name_kept_on_a_retirement_record_is_replaced_too(self):
        """A record of this area keeps the name as well, and the two have to agree."""
        RetirementState.objects.create(
            state_name="PENDING", state_execution_order=1, is_dead_end_state=False, required=True,
        )
        record = create_retirement_status(self.learner)
        answered = self.ask([{"plain-learner": "asked-for"}])
        assert self.results_of(answered)[0]["outcome"] == REPLACEMENT_REPLACED
        record.refresh_from_db()
        assert record.original_username == "asked-for"
        assert User.objects.get(id=self.learner.id).username == "asked-for"

    def test_the_name_kept_on_a_partner_report_entry_is_replaced_too(self):
        entry = UserRetirementPartnerReportingStatus.objects.create(
            user=self.learner,
            original_username=self.learner.username,
            original_email=self.learner.email,
            original_name=self.learner.profile.name,
        )
        answered = self.ask([{"plain-learner": "asked-for"}])
        assert self.results_of(answered)[0]["outcome"] == REPLACEMENT_REPLACED
        entry.refresh_from_db()
        assert entry.original_username == "asked-for"

    def test_an_application_this_deployment_does_not_install_is_this_servers_failure(self):
        """Two of the applications holding a name are features a deployment may leave out."""
        with mock.patch.object(
            view_services, "NAMES_ARE_ALSO_KEPT_IN", (("a_feature_left_out.Record", "username"),),
        ):
            answered = self.ask([{"plain-learner": "asked-for"}])
        assert answered.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert answered.json() == {
            "type": error_type_uri(INTERNAL_ERROR_SLUG),
            "title": INTERNAL_ERROR_TITLE,
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "detail": view_services.NAMES_WERE_NOT_REPLACED,
            "instance": self.url,
        }
        assert "no such application" not in answered.content.decode()
        assert "plain-learner" in self.names_stored()

    def test_a_write_that_fails_part_way_through_replaces_nothing(self):
        """The earlier address reports that mapping as failed and keeps the rest."""
        other = UserFactory.create(username="other-learner")
        with mock.patch.object(
            UserRetirementStatus.objects, "filter", side_effect=ValueError("the rows would not take it"),
        ):
            answered = self.ask([{"plain-learner": "first"}, {"other-learner": "second"}])
        assert answered.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert answered.json()["detail"] == "An unexpected error occurred. Please try again later."
        assert "would not take it" not in answered.content.decode()
        assert User.objects.get(id=self.learner.id).username == "plain-learner"
        assert User.objects.get(id=other.id).username == "other-learner"


@skip_unless_lms
class UsernameReplacementBodyTests(UsernameReplacementTestCase):
    """What the request is refused for, and what it is read from."""

    def setUp(self):
        super().setUp()
        self.login(self.replacer)

    def assert_refused_for(self, response, member):
        """Assert the answer names this member of the body as the one at fault."""
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert list(envelope["errors"]) == [member]
        return envelope

    def test_a_request_carrying_no_mappings_at_all_names_what_it_needs(self):
        self.assert_refused_for(self.client.post(self.url, {}, format="json"), "username_mappings")

    def test_a_request_carrying_an_empty_list_is_refused(self):
        """The earlier address answers two empty arrays, which says nothing was asked for."""
        self.assert_refused_for(self.ask([]), "username_mappings")

    def test_a_mapping_that_is_not_an_object_is_refused(self):
        self.assert_refused_for(self.ask(["plain-learner"]), "username_mappings[0]")

    def test_a_mapping_naming_no_account_is_refused(self):
        self.assert_refused_for(self.ask([{}]), "username_mappings[0]")

    def test_a_mapping_naming_several_accounts_is_refused(self):
        self.assert_refused_for(
            self.ask([{"plain-learner": "first", "other-learner": "second"}]), "username_mappings[0]",
        )
        assert "plain-learner" in self.names_stored()

    def test_mappings_sent_as_an_object_rather_than_a_list_are_refused(self):
        self.assert_refused_for(self.ask({"plain-learner": "asked-for"}), "username_mappings")

    def test_more_mappings_than_one_request_may_carry_are_refused(self):
        held = [UserFactory.create(username=f"held-{index}") for index in range(2)]
        mappings = [{f"held-{index}": f"renamed-{index}"} for index in range(2)]
        mappings += [{f"nobody-{index}": f"asked-{index}"} for index in range(REPLACEMENT_MAX_MAPPINGS)]
        assert len(mappings) == REPLACEMENT_MAX_MAPPINGS + 2
        self.assert_refused_for(self.ask(mappings), "username_mappings")
        assert {account.username for account in held} <= self.names_stored()

    def test_as_many_mappings_as_one_request_may_carry_are_answered(self):
        mappings = [{f"nobody-{index}": f"asked-{index}"} for index in range(REPLACEMENT_MAX_MAPPINGS)]
        answered = self.ask(mappings)
        assert answered.status_code == status.HTTP_200_OK, answered.content
        assert len(self.results_of(answered)) == REPLACEMENT_MAX_MAPPINGS

    def test_a_body_sent_as_a_web_form_is_refused(self):
        assert_error_envelope(
            self.client.post(self.url, "username_mappings=x", content_type=FORM),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug=UNSUPPORTED_MEDIA_TYPE_SLUG,
        )

    def test_a_body_that_is_not_well_formed_json_is_refused(self):
        assert_error_envelope(
            self.client.post(self.url, "{not json", content_type="application/json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug=MALFORMED_BODY_SLUG,
        )

    def test_a_query_parameter_this_operation_does_not_read_is_refused(self):
        envelope = assert_error_envelope(
            self.client.post(
                f"{self.url}?view=minimal",
                json.dumps({"username_mappings": [{"plain-learner": "asked-for"}]}),
                content_type="application/json",
            ),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert list(envelope["errors"]) == ["view"]
        assert "plain-learner" in self.names_stored()

    def test_the_representation_selector_is_read(self):
        answered = self.client.post(
            f"{self.url}?format=json",
            json.dumps({"username_mappings": [{"plain-learner": "asked-for"}]}),
            content_type="application/json",
        )
        assert answered.status_code == status.HTTP_200_OK, answered.content

    def test_the_length_of_the_generated_suffix_is_the_one_the_deployment_configures(self):
        UserFactory.create(username="already-held")
        with override_settings(SOCIAL_AUTH_UUID_LENGTH=4):
            answered = self.ask([{"plain-learner": "already-held"}])
        handed_out = self.results_of(answered)[0]["new_username"]
        assert handed_out.startswith("already-held-")
        assert len(handed_out) == len("already-held-") + 4


@skip_unless_lms
class EarlierAddressTests(UsernameReplacementTestCase):
    """The address this operation replaces is left exactly as it was."""

    def test_the_earlier_address_still_answers_its_own_caller(self):
        self.login(self.replacer)
        answered = self.client.post(
            EARLIER_ADDRESS,
            json.dumps({"username_mappings": [{"plain-learner": "asked-for"}]}),
            content_type="application/json",
        )
        assert answered.status_code == status.HTTP_200_OK, answered.content
        assert answered.json() == {
            "successful_replacements": [{"plain-learner": "asked-for"}],
            "failed_replacements": [],
        }

    def test_the_name_the_earlier_address_is_reversed_by_still_reverses(self):
        assert reverse("username_replacement") == EARLIER_ADDRESS
