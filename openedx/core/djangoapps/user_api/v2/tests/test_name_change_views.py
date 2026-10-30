"""Tests for the version 2 User API profile name change views."""

import pytest
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import PendingNameChange
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"

#: The version 1 addresses these views supersede.
EARLIER_REQUEST = "/api/user/v1/accounts/name_change/"


def earlier_confirmation(username):
    """Return the version 1 address that confirms one user's requested name."""
    return f"/api/user/v1/accounts/name_change/{username}/confirm/"


class NameChangeTestCase(APITestCase):
    """Fixtures shared by the version 2 name change tests."""

    def setUp(self):
        super().setUp()
        self.staff = UserFactory.create(
            username="name-staff", email="name-staff@example.com", password=PASSWORD, is_staff=True,
        )
        self.owner = UserFactory.create(
            username="name-owner", email="name-owner@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="name-other", email="name-other@example.com", password=PASSWORD,
        )
        self.owner.profile.name = "Original Name"
        self.owner.profile.save()
        self.request_url = reverse("user_v2:name_change_list")

    def login(self, user):
        """Sign the given user in through the session these views accept."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def token_headers(self, user):
        """Return the request headers that carry a token identifying the given user."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def confirmation_url(self, username):
        """Return the address that confirms one user's requested name."""
        return reverse("user_v2:name_change_confirmation", kwargs={"username": username})

    def request_name(self, name, **kwargs):
        """Ask for a profile name as the signed-in caller."""
        return self.client.post(self.request_url, {"name": name}, format="json", **kwargs)

    def pending_name(self, user):
        """Return the name this user has asked for, or None when they have asked for none."""
        pending = PendingNameChange.objects.filter(user=user).first()
        return pending.new_name if pending else None

    def profile_name(self, user):
        """Return the name the user's profile carries right now."""
        user.profile.refresh_from_db()
        return user.profile.name


@skip_unless_lms
class NameChangeRequestTests(NameChangeTestCase):
    """POST /api/user/v2/name_changes/"""

    def test_a_caller_asks_for_a_name_on_their_own_profile(self):
        self.login(self.owner)
        response = self.request_name("Jon Doe")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data == {"name": "Jon Doe"}
        assert self.pending_name(self.owner) == "Jon Doe"
        assert self.profile_name(self.owner) == "Original Name"

    def test_the_request_records_who_asked_for_it(self):
        self.login(self.owner)
        self.request_name("Jon Doe")
        pending = PendingNameChange.objects.get(user=self.owner)
        assert pending.rationale == "Name change requested through account API by name-owner"

    def test_a_second_request_replaces_the_first(self):
        self.login(self.owner)
        self.request_name("First Try")
        response = self.request_name("Second Try")
        assert response.status_code == status.HTTP_201_CREATED
        assert PendingNameChange.objects.filter(user=self.owner).count() == 1
        assert self.pending_name(self.owner) == "Second Try"

    def test_a_caller_may_only_ask_for_a_name_on_their_own_profile(self):
        """The address carries no username, so a request is about the caller and nobody else."""
        self.login(self.other)
        self.request_name("Jon Doe")
        assert self.pending_name(self.other) == "Jon Doe"
        assert self.pending_name(self.owner) is None

    def test_the_name_the_profile_already_carries_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.request_name("Original Name"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"name": ["The profile already carries this name."]}
        assert self.pending_name(self.owner) is None

    def test_a_name_carrying_markup_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.request_name("Jon <b>Doe</b>"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "name": ["Name cannot contain the following characters: < >"],
        }
        assert self.pending_name(self.owner) is None

    def test_a_name_carrying_a_web_address_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.request_name("Jon Doe https://example.com"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"name": ["Name cannot contain a URL"]}
        assert self.pending_name(self.owner) is None

    def test_a_body_carrying_no_name_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.post(self.request_url, {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"name": ["This field is required."]}

    def test_an_empty_name_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.request_name(""),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"name": ["This field may not be blank."]}

    def test_a_name_longer_than_the_column_that_stores_it_is_refused(self):
        """The stored column holds 255 characters, and a longer one would fail at the database."""
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.request_name("N" * 256),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "name": ["Ensure this field has no more than 255 characters."],
        }
        assert self.pending_name(self.owner) is None
        assert PendingNameChange.objects.count() == 0

    def test_a_name_exactly_as_long_as_the_column_is_accepted(self):
        self.login(self.owner)
        assert self.request_name("N" * 255).status_code == status.HTTP_201_CREATED
        assert self.pending_name(self.owner) == "N" * 255

    def test_an_account_carrying_no_profile_has_no_name_to_change(self):
        self.login(self.other)
        self.other.profile.delete()
        assert_error_envelope(
            self.request_name("Jon Doe"),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert PendingNameChange.objects.count() == 0

    def test_an_anonymous_caller_is_unauthenticated(self):
        assert_error_envelope(
            self.request_name("Jon Doe"),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert PendingNameChange.objects.count() == 0

    def test_a_token_held_by_a_caller_who_has_not_confirmed_their_address_asks_for_a_name(self):
        """Asking for a name is about the caller's own profile, which a token reaches while unconfirmed."""
        self.owner.is_active = False
        self.owner.save()
        response = self.request_name("Jon Doe", **self.token_headers(self.owner))
        assert response.status_code == status.HTTP_201_CREATED
        assert self.pending_name(self.owner) == "Jon Doe"

    def test_a_session_held_by_such_a_caller_identifies_nobody_here(self):
        """A session here is read the way the platform reads one, and that is a token's work."""
        self.login(self.owner)
        self.owner.is_active = False
        self.owner.save()
        assert_error_envelope(
            self.request_name("Jon Doe"),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert PendingNameChange.objects.count() == 0

    def test_an_unknown_query_parameter_is_refused(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.post(f"{self.request_url}?username=name-other", {"name": "Jon Doe"}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "username": ["No such query parameter. Send any of: format."],
        }
        assert PendingNameChange.objects.count() == 0

    def test_a_body_that_is_not_well_formed_is_refused_as_the_callers_fault(self):
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.client.post(self.request_url, "{not json", content_type="application/json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="malformed-body",
        )
        assert envelope["title"] == "Malformed Request Body"
        assert PendingNameChange.objects.count() == 0

    def test_a_body_in_a_media_type_this_endpoint_cannot_read_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.post(self.request_url, "<name>Jon Doe</name>", content_type="application/xml"),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )
        assert PendingNameChange.objects.count() == 0

    def test_a_name_sent_as_a_web_form_is_recorded_the_way_one_sent_as_json_is(self):
        self.login(self.owner)
        encoded = self.client.post(
            self.request_url, "name=Jon+Doe", content_type="application/x-www-form-urlencoded",
        )
        assert encoded.status_code == status.HTTP_201_CREATED
        assert encoded.data == {"name": "Jon Doe"}
        assert self.pending_name(self.owner) == "Jon Doe"

        uploaded = self.client.post(self.request_url, {"name": "Jane Doe"}, format="multipart")
        assert uploaded.status_code == status.HTTP_201_CREATED
        assert uploaded.data == {"name": "Jane Doe"}
        assert self.pending_name(self.owner) == "Jane Doe"

    def test_the_collection_is_not_read_with_a_get(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.get(self.request_url),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )


@skip_unless_lms
class NameChangeConfirmationTests(NameChangeTestCase):
    """POST /api/user/v2/name_changes/{username}/confirmations/"""

    def setUp(self):
        super().setUp()
        PendingNameChange.objects.create(
            user=self.owner, new_name="Jon Doe", rationale="asked for in a test",
        )
        self.url = self.confirmation_url(self.owner.username)

    def test_a_staff_caller_puts_the_requested_name_on_the_profile(self):
        self.login(self.staff)
        response = self.client.post(self.url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.data is None
        assert self.profile_name(self.owner) == "Jon Doe"
        assert self.pending_name(self.owner) is None

    def test_the_name_the_profile_carried_is_kept_in_its_history(self):
        self.login(self.staff)
        self.client.post(self.url)
        self.owner.profile.refresh_from_db()
        recorded = self.owner.profile.get_meta()["old_names"]
        assert len(recorded) == 1
        assert recorded[0][0] == "Original Name"
        assert recorded[0][1] == "asked for in a test"

    def test_the_user_who_asked_may_not_confirm_their_own_request(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.profile_name(self.owner) == "Original Name"
        assert self.pending_name(self.owner) == "Jon Doe"

    def test_another_learner_may_not_confirm_a_request_either(self):
        self.login(self.other)
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.profile_name(self.owner) == "Original Name"

    def test_an_anonymous_caller_is_unauthenticated(self):
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert self.profile_name(self.owner) == "Original Name"

    def test_a_token_held_by_a_deactivated_staff_account_is_refused(self):
        headers = self.token_headers(self.staff)
        self.staff.is_active = False
        self.staff.save()
        assert_error_envelope(
            self.client.post(self.url, **headers),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.profile_name(self.owner) == "Original Name"
        assert self.pending_name(self.owner) == "Jon Doe"

    def test_a_token_held_by_an_active_staff_account_reaches_the_action(self):
        response = self.client.post(self.url, **self.token_headers(self.staff))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert self.profile_name(self.owner) == "Jon Doe"

    def test_a_user_who_has_asked_for_nothing_has_nothing_to_confirm(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.post(self.confirmation_url(self.other.username)),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert self.profile_name(self.other) == self.other.profile.name

    def test_the_reserved_identifier_is_read_as_a_stored_name_here(self):
        """``me`` names an account holding that name at this address, as the parameter says."""
        self.login(self.staff)
        assert_error_envelope(
            self.client.post(self.confirmation_url("me")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        named = UserFactory.create(username="me", email="me@example.com", password=PASSWORD)
        PendingNameChange.objects.create(
            user=named, new_name="Named Me", rationale="asked for in a test",
        )
        assert self.client.post(self.confirmation_url("me")).status_code == status.HTTP_204_NO_CONTENT
        assert self.profile_name(named) == "Named Me"
        assert self.pending_name(self.owner) == "Jon Doe"

    def test_an_unknown_username_is_not_found(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.post(self.confirmation_url("nobody-at-all")),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_an_account_carrying_no_profile_has_no_name_to_change(self):
        self.login(self.staff)
        self.owner.profile.delete()
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert self.pending_name(self.owner) == "Jon Doe"

    def test_the_address_is_not_read_with_a_get(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.get(self.url),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )
        assert self.profile_name(self.owner) == "Original Name"

    def test_an_unknown_query_parameter_is_refused(self):
        self.login(self.staff)
        envelope = assert_error_envelope(
            self.client.post(f"{self.url}?confirm=true"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "confirm": ["No such query parameter. Send any of: format."],
        }
        assert self.profile_name(self.owner) == "Original Name"


@skip_unless_lms
class NameChangeParityTests(NameChangeTestCase):
    """The same requests through the version 1 and the version 2 addresses."""

    def test_both_versions_record_the_same_request(self):
        self.login(self.owner)
        earlier = self.client.post(EARLIER_REQUEST, {"name": "Jon Doe"}, format="json")
        assert earlier.status_code == status.HTTP_201_CREATED
        assert earlier.data is None
        earlier_pending = PendingNameChange.objects.get(user=self.owner)
        earlier_name, earlier_rationale = earlier_pending.new_name, earlier_pending.rationale
        earlier_pending.delete()

        later = self.request_name("Jon Doe")
        later_pending = PendingNameChange.objects.get(user=self.owner)
        assert (later_pending.new_name, later_pending.rationale) == (earlier_name, earlier_rationale)
        assert later.status_code == earlier.status_code
        # The one declared difference: the created request is returned as the
        # body where version 1 answered with none.
        assert later.data == {"name": "Jon Doe"}

    def test_neither_version_records_a_request_from_an_unconfirmed_session(self):
        """Both addresses read a session the way the platform reads one, and so refuse the same ones."""
        self.login(self.owner)
        self.owner.is_active = False
        self.owner.save()
        earlier = self.client.post(EARLIER_REQUEST, {"name": "Jon Doe"}, format="json")
        later = self.request_name("Jon Doe")
        assert earlier.status_code == status.HTTP_401_UNAUTHORIZED
        assert later.status_code == earlier.status_code
        assert_error_envelope(later, expected_status=401, expected_type_slug="authn")
        assert PendingNameChange.objects.count() == 0

    def test_both_versions_read_a_name_sent_as_a_web_form(self):
        """A form posts its fields encoded rather than as JSON, and both addresses take them."""
        self.login(self.owner)
        body, encoded = "name=Jon+Doe", "application/x-www-form-urlencoded"
        earlier = self.client.post(EARLIER_REQUEST, body, content_type=encoded)
        assert earlier.status_code == status.HTTP_201_CREATED
        assert self.pending_name(self.owner) == "Jon Doe"
        PendingNameChange.objects.get(user=self.owner).delete()

        later = self.client.post(self.request_url, body, content_type=encoded)
        assert later.status_code == earlier.status_code
        assert self.pending_name(self.owner) == "Jon Doe"

    def test_both_versions_refuse_the_name_the_profile_already_carries(self):
        self.login(self.owner)
        earlier = self.client.post(EARLIER_REQUEST, {"name": "Original Name"}, format="json")
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert earlier.data == {
            "new_name": "The profile name given was identical to the current name.",
        }

        later = self.request_name("Original Name")
        assert later.status_code == earlier.status_code
        assert later.data["errors"] == {"name": ["The profile already carries this name."]}

    def test_both_versions_refuse_a_name_carrying_markup(self):
        self.login(self.owner)
        earlier = self.client.post(EARLIER_REQUEST, {"name": "Jon <b>Doe</b>"}, format="json")
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert earlier.data == {
            "new_name": ["Name cannot contain the following characters: < >"],
        }

        later = self.request_name("Jon <b>Doe</b>")
        assert later.status_code == earlier.status_code
        assert later.data["errors"] == {
            "name": ["Name cannot contain the following characters: < >"],
        }
        assert PendingNameChange.objects.count() == 0

    def test_both_versions_confirm_a_request_the_same_way(self):
        PendingNameChange.objects.create(user=self.owner, new_name="Jon Doe", rationale="a test")
        self.login(self.staff)
        earlier = self.client.post(earlier_confirmation(self.owner.username))
        assert earlier.status_code == status.HTTP_204_NO_CONTENT
        assert self.profile_name(self.owner) == "Jon Doe"
        earlier_history = self.owner.profile.get_meta()["old_names"]

        self.owner.profile.name = "Original Name"
        self.owner.profile.save()
        PendingNameChange.objects.create(user=self.owner, new_name="Jon Doe", rationale="a test")
        later = self.client.post(self.confirmation_url(self.owner.username))
        assert later.status_code == earlier.status_code
        assert self.profile_name(self.owner) == "Jon Doe"
        later_history = self.owner.profile.get_meta()["old_names"]
        assert [entry[:2] for entry in later_history] == [entry[:2] for entry in earlier_history] * 2

    def test_both_versions_refuse_a_confirmation_from_a_caller_who_is_not_staff(self):
        PendingNameChange.objects.create(user=self.owner, new_name="Jon Doe", rationale="a test")
        self.login(self.other)
        earlier = self.client.post(earlier_confirmation(self.owner.username))
        assert earlier.status_code == status.HTTP_403_FORBIDDEN
        assert earlier.data is None

        later = self.client.post(self.confirmation_url(self.owner.username))
        assert later.status_code == earlier.status_code
        # The one declared difference: version 1 answers with an empty body.
        assert_error_envelope(later, expected_status=403, expected_type_slug="authz")
        assert self.profile_name(self.owner) == "Original Name"

    def test_a_name_no_account_holds_is_not_found(self):
        """A confirmation addressed to no account is refused before anything is looked up."""
        self.login(self.staff)
        later = self.client.post(self.confirmation_url("nobody-at-all"))
        assert_error_envelope(
            later, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )


#: What each address costs, measured on the version 1 address first and
#: asserted on both. Neither version reads anything per row: the request reads
#: the caller's profile and stores the name asked for, and the confirmation
#: reads the user, their request and their profile before writing the new name
#: and deleting the request.
REQUEST_QUERIES = 20
CONFIRMATION_QUERIES = 20


@pytest.fixture(name="name_change")
@skip_unless_lms
def name_change_fixture(client, django_user_model):  # pylint: disable=unused-argument
    """Sign a staff caller in beside the learner whose profile is renamed."""
    staff = UserFactory.create(username="count-name-staff", password=PASSWORD, is_staff=True)
    owner = UserFactory.create(username="count-name-owner", password=PASSWORD)
    owner.profile.name = "Original Name"
    owner.profile.save()
    assert client.login(username=staff.username, password=PASSWORD)
    # The first authenticated request of a session populates caches the rest
    # of the session reuses; the counts below are the steady-state ones.
    client.post(reverse("user_v2:name_change_list"), {"name": "Warm Up"}, content_type="application/json")
    PendingNameChange.objects.all().delete()
    return staff, owner


@pytest.mark.django_db
@skip_unless_lms
def test_a_request_costs_what_the_earlier_address_costs(client, name_change, django_assert_num_queries):
    body = {"name": "Jon Doe"}
    with django_assert_num_queries(REQUEST_QUERIES):
        earlier = client.post(EARLIER_REQUEST, body, content_type="application/json")
    assert earlier.status_code == status.HTTP_201_CREATED
    PendingNameChange.objects.all().delete()

    with django_assert_num_queries(REQUEST_QUERIES):
        later = client.post(reverse("user_v2:name_change_list"), body, content_type="application/json")
    assert later.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
@skip_unless_lms
def test_a_confirmation_costs_what_the_earlier_address_costs(client, name_change, django_assert_num_queries):
    _staff, owner = name_change

    PendingNameChange.objects.create(user=owner, new_name="Jon Doe", rationale="a test")
    with django_assert_num_queries(CONFIRMATION_QUERIES):
        earlier = client.post(earlier_confirmation(owner.username))
    assert earlier.status_code == status.HTTP_204_NO_CONTENT

    PendingNameChange.objects.create(user=owner, new_name="Jon Doe Again", rationale="a test")
    with django_assert_num_queries(CONFIRMATION_QUERIES):
        later = client.post(
            reverse("user_v2:name_change_confirmation", kwargs={"username": owner.username}),
        )
    assert later.status_code == status.HTTP_204_NO_CONTENT
