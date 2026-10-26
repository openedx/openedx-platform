"""Tests for the version 2 User API account views."""

import datetime
import json
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from edx_rest_framework_extensions.scoping import FullScopePolicy
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase
from waffle.testutils import override_switch

from common.djangoapps.student.models import PendingNameChange, UserProfile, get_retired_email_by_email
from common.djangoapps.student.tests.factories import (
    ContentTypeFactory,
    PermissionFactory,
    SuperuserFactory,
    UserFactory,
)
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangoapps.user_api.accounts.utils import ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2.permissions import ACCOUNT_CONTACT_SELECTORS
from openedx.core.djangoapps.user_api.v2.serializers import ACCOUNT_DEFAULT_SORT, AccountQuerySerializer
from openedx.core.djangoapps.user_api.v2.views import EmailSearchViewSet
from openedx.core.djangoapps.user_authn.views.password_reset import PasswordResetFormNoActive
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_schema import name_affirmation_answering
from .test_views import PAGINATION_KEYS, write_queries, write_target

PASSWORD = "Password1234"

#: The properties every caller may see, whoever they are.
PUBLIC_FIELDS = {"account_privacy", "profile_image", "username"}

#: ``ACCOUNT_VISIBILITY_CONFIGURATION`` is an LMS setting, and this package is
#: shared with the CMS. Every test below that reads these sets is
#: ``@skip_unless_lms``, but a module body runs before any skip can apply, so a
#: missing setting is tolerated here instead of breaking collection under the
#: CMS settings. The same guard is used for the same setting in
#: ``user_api/accounts/tests/test_views.py``.
_VISIBILITY = getattr(settings, "ACCOUNT_VISIBILITY_CONFIGURATION", {})

#: The properties a user sees of their own account, and a staff caller of any.
FULL_FIELDS = set(_VISIBILITY.get("admin_fields", ()))

#: The properties a learner sees of an account whose owner shares it with everybody.
SHARED_FIELDS = set(_VISIBILITY.get("bulk_shareable_fields", ()))

#: The properties an owner is able to share, and so the only ones another caller
#: may select; the refusal offers them in this order.
SHAREABLE_FIELDS = set(_VISIBILITY.get("custom_shareable_fields", ()))
SHAREABLE_FIELDS_OFFERED = ", ".join(sorted(SHAREABLE_FIELDS))

#: The identifying properties the ``minimal`` preset keeps, and the ones of those
#: another caller may select; the refusal offers them in this order.
MINIMAL_FIELDS = {"username", "email", "name", "profile_image"}
SHAREABLE_MINIMAL_FIELDS_OFFERED = ", ".join(sorted(SHAREABLE_FIELDS & MINIMAL_FIELDS))

#: The query parameters each account action reads, in the order its refusal lists them.
LIST_QUERY_PARAMS = "email, fields, format, lms_user_id, ordering, page, page_size, username, view"
MEMBER_QUERY_PARAMS = "fields, format, view"
ACTION_QUERY_PARAMS = "format, page, page_size"

#: A deactivation carries no page, so it reads only the representation parameter.
DEACTIVATION_QUERY_PARAMS = "format"

MERGE_PATCH = "application/merge-patch+json"

#: Where the classes the platform declares record that they read a request.
DEFAULT_AUTH_RECORDER = "openedx.core.djangolib.default_auth_classes.set_custom_attribute"

def counting_cache(location):
    """
    Return a cache that counts what it is given, under a name of its own.

    The cache the test settings configure discards everything, so a rate limit
    never counts past its first request and the path it guards is never
    reached. Each test names its own cache, because the counts outlive the
    test that made them.
    """
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": location,
        },
    }


class AccountApiV2TestCase(APITestCase):
    """Fixtures shared by the version 2 account tests."""

    def setUp(self):
        super().setUp()
        self.staff = UserFactory.create(
            username="staff-caller", email="staff-caller@example.com", password=PASSWORD, is_staff=True,
        )
        self.owner = UserFactory.create(
            username="owner-caller", email="owner-caller@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="other-caller", email="other-caller@example.com", password=PASSWORD,
        )
        self.list_url = reverse("user_v2:account_list")
        self.own_url = reverse("user_v2:account_detail", kwargs={"username": "me"})

    def login(self, user):
        """Sign the given user in through the session these views accept."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def detail_url(self, username):
        """Return the member address of one account."""
        return reverse("user_v2:account_detail", kwargs={"username": username})

    def token_headers(self, user):
        """Return the request headers that carry a token identifying the given user."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def usernames_in(self, response):
        """Return the usernames carried by a page of accounts."""
        return [row["username"] for row in response.data["results"]]

    def patch(self, address, body, **kwargs):
        """Send a merge patch, the only body these views read."""
        return self.client.patch(address, body, content_type=MERGE_PATCH, **kwargs)


@skip_unless_lms
class AccountListViewTests(AccountApiV2TestCase):
    """GET /api/user/v2/accounts/"""

    def test_a_caller_with_no_selector_receives_their_own_account(self):
        self.login(self.owner)
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert self.usernames_in(response) == ["owner-caller"]
        assert set(response.data["results"][0]) == FULL_FIELDS

    def test_the_page_carries_the_whole_envelope(self):
        self.login(self.staff)
        response = self.client.get(self.list_url)
        assert set(response.data) == PAGINATION_KEYS
        assert response.data["count"] == 1
        assert response.data["num_pages"] == 1
        assert response.data["current_page"] == 1
        assert response.data["start"] == 0
        assert response.data["next"] is None
        assert response.data["previous"] is None

    def test_named_accounts_come_back_in_one_page(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"username": "other-caller,owner-caller"})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["other-caller", "owner-caller"]

    def test_the_page_is_ordered_by_username_and_the_order_can_be_reversed(self):
        self.login(self.staff)
        selection = {"username": "owner-caller,other-caller,staff-caller"}
        ascending = self.client.get(self.list_url, selection)
        assert self.usernames_in(ascending) == ["other-caller", "owner-caller", "staff-caller"]

        descending = self.client.get(self.list_url, {**selection, "ordering": "-username"})
        assert self.usernames_in(descending) == ["staff-caller", "owner-caller", "other-caller"]

    def test_the_page_is_sorted_by_username_when_no_order_is_asked_for(self):
        """The order the parameter publishes as its default is the one a page comes back in."""
        self.login(self.staff)
        selection = {"username": "owner-caller,other-caller,staff-caller"}
        omitted = self.client.get(self.list_url, selection)
        named = self.client.get(self.list_url, {**selection, "ordering": ACCOUNT_DEFAULT_SORT})
        blank = self.client.get(self.list_url, {**selection, "ordering": ""})
        assert self.usernames_in(omitted) == ["other-caller", "owner-caller", "staff-caller"]
        assert self.usernames_in(named) == self.usernames_in(omitted)
        assert self.usernames_in(blank) == self.usernames_in(omitted)

    def test_a_property_the_list_does_not_sort_by_is_refused(self):
        """
        A sort the list does not offer is refused rather than quietly applied.

        Any other column of the user table would otherwise order the page, so
        a caller who may read nothing but usernames could read the order of a
        column they may not be answered with.
        """
        self.login(self.staff)
        for asked in ("bogus", "password", "date_joined", "-date_joined", "username,-username"):
            with self.subTest(ordering=asked):
                response = self.client.get(
                    self.list_url, {"username": "owner-caller,other-caller", "ordering": asked},
                )
                envelope = assert_error_envelope(
                    response,
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )
                assert envelope["errors"] == {
                    "ordering": ["Sort by any of: username, each optionally prefixed with '-'."],
                }
                assert "results" not in envelope

    def test_a_name_sent_with_spaces_around_it_names_the_same_account_throughout(self):
        """
        The name is read the same way when the answer is shaped and when the page is cut.

        What a caller may select depends on whose accounts the page carries,
        and spaces around the name they sent do not make their own account
        somebody else's for that purpose.
        """
        self.login(self.owner)
        for asked in ("owner-caller", " owner-caller", "owner-caller "):
            with self.subTest(username=asked):
                response = self.client.get(self.list_url, {"username": asked, "fields": "email"})
                assert response.status_code == status.HTTP_200_OK, response.content
                assert response.data["results"] == [{"email": "owner-caller@example.com"}]

    def test_a_name_no_account_holds_is_left_out_rather_than_refused(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"username": "owner-caller,nobody"})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["owner-caller"]

    def test_a_selector_that_names_no_account_answers_an_empty_page(self):
        """A collection that matched nothing is an empty page, not an absent collection."""
        self.login(self.staff)
        response = self.client.get(self.list_url, {"username": "nobody"})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert response.data["count"] == 0
        assert response.data["results"] == []
        assert response.data["num_pages"] == 1
        assert response.data["next"] is None
        assert response.data["previous"] is None

    def test_an_empty_page_reads_no_account(self):
        """Nothing was selected, so no account is read to fill the page."""
        self.login(self.owner)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.list_url, {"username": "nobody"})
        assert response.data["results"] == []
        assert [query for query in captured.captured_queries if "auth_userprofile" in query["sql"]] == []

    def test_a_learner_sees_only_the_shared_properties_of_another_account(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"username": "other-caller"})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["results"][0]) == PUBLIC_FIELDS
        assert response.data["results"][0]["username"] == "other-caller"

    def test_a_staff_caller_sees_every_property_of_another_account(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"username": "other-caller"})
        assert set(response.data["results"][0]) == FULL_FIELDS
        assert response.data["results"][0]["email"] == "other-caller@example.com"

    def test_a_page_mixing_the_caller_and_others_shapes_each_row_for_its_own_owner(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"username": "owner-caller,other-caller"})
        rows = {row["username"]: set(row) for row in response.data["results"]}
        assert rows == {"other-caller": PUBLIC_FIELDS, "owner-caller": FULL_FIELDS}

    def test_the_shared_preset_narrows_the_callers_own_account(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"view": "shared"})
        assert set(response.data["results"][0]) == PUBLIC_FIELDS

    def test_the_minimal_preset_keeps_the_identifying_properties(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"view": "minimal"})
        assert set(response.data["results"][0]) == {"username", "email", "name", "profile_image"}

    def test_the_minimal_preset_cannot_hand_out_a_property_the_caller_may_not_see(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"username": "other-caller", "view": "minimal"})
        assert set(response.data["results"][0]) == {"username", "profile_image"}

    def test_a_learner_selecting_an_identifying_property_they_may_not_see_is_refused(self):
        """The preset keeps the identifying properties; this caller may be answered with some of them."""
        self.login(self.owner)
        response = self.client.get(
            self.list_url, {"username": "other-caller", "view": "minimal", "fields": "email"},
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            f"No such field: email. Select any of: {SHAREABLE_MINIMAL_FIELDS_OFFERED}.",
        ]

    def test_a_caller_selects_an_identifying_property_of_their_own_account(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"view": "minimal", "fields": "email"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [{"email": "owner-caller@example.com"}]

    def test_selected_fields_narrow_each_row(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"fields": "username,email"})
        assert response.data["results"] == [
            {"username": "owner-caller", "email": "owner-caller@example.com"},
        ]

    def test_a_field_the_response_cannot_carry_is_refused(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"fields": "username,wallet"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"][0].startswith("No such field: wallet.")

    def test_a_learner_selecting_a_property_of_another_account_they_may_not_see_is_refused(self):
        """A property this caller's page cannot carry is refused, not answered as an empty object."""
        self.login(self.owner)
        response = self.client.get(self.list_url, {"username": "other-caller", "fields": "email"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            f"No such field: email. Select any of: {SHAREABLE_FIELDS_OFFERED}.",
        ]

    def test_a_learner_selecting_a_property_this_owner_does_not_share_receives_the_rest(self):
        """
        A property an owner is able to share can be selected; whether they share it is their own choice.

        This owner shares nothing beyond the properties every caller may see,
        so the row carries the rest of the selection rather than the property
        asked for.
        """
        self.login(self.owner)
        response = self.client.get(self.list_url, {"username": "other-caller", "fields": "bio,username"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [{"username": "other-caller"}]

    def test_a_staff_caller_still_selects_a_property_only_they_may_see(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"username": "other-caller", "fields": "email"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [{"email": "other-caller@example.com"}]

    def test_a_caller_still_selects_a_property_only_they_may_see_of_their_own_account(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"username": "owner-caller", "fields": "email"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [{"email": "owner-caller@example.com"}]

    def test_a_property_the_shared_preset_leaves_out_is_refused_with_the_preset_asked_for(self):
        """``shared`` answers with what the owner shares, so only those properties can be selected."""
        self.login(self.staff)
        response = self.client.get(
            self.list_url, {"username": "other-caller", "view": "shared", "fields": "email"},
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            f"No such field: email. Select any of: {SHAREABLE_FIELDS_OFFERED}.",
        ]

    def test_a_preset_the_endpoint_does_not_offer_is_refused(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"view": "everything"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": ["Ask for any of: minimal, shared, or omit this parameter for the default response."],
        }

    def test_a_query_parameter_the_endpoint_does_not_read_is_refused(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"usernames": "owner-caller"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "usernames": [f"No such query parameter. Send any of: {LIST_QUERY_PARAMS}."],
        }

    def test_a_page_that_does_not_exist_is_not_found(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"page": 7})
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_the_collection_is_split_into_pages(self):
        self.login(self.staff)
        selection = {"username": "owner-caller,other-caller,staff-caller", "page_size": 2}
        first = self.client.get(self.list_url, selection)
        assert first.data["count"] == 3
        assert first.data["num_pages"] == 2
        assert self.usernames_in(first) == ["other-caller", "owner-caller"]
        assert "page=2" in first.data["next"]

        second = self.client.get(self.list_url, {**selection, "page": 2})
        assert self.usernames_in(second) == ["staff-caller"]
        assert second.data["start"] == 2

    def test_an_anonymous_caller_is_unauthenticated(self):
        response = self.client.get(self.list_url)
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )


@skip_unless_lms
class AccountListSelectorTests(AccountApiV2TestCase):
    """The selectors of GET /api/user/v2/accounts/ that only staff may use."""

    def test_a_staff_caller_selects_an_account_by_email_address(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"email": "other-caller@example.com"})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["other-caller"]

    def test_a_staff_caller_selects_an_account_by_identifier(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"lms_user_id": self.other.id})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["other-caller"]

    def test_the_selectors_a_learner_may_not_use_are_the_ones_the_privilege_is_asked_for(self):
        """Every selector the list documents as staff-only is one the permission classes gate."""
        documented = {
            name for name, field in AccountQuerySerializer().fields.items()
            if "Only staff" in (field.help_text or "")
        }
        assert documented == {"email", "lms_user_id"}
        assert set(ACCOUNT_CONTACT_SELECTORS) == documented

    def test_a_learner_may_not_select_by_email_address(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"email": "other-caller@example.com"})
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_learner_may_not_select_by_identifier(self):
        self.login(self.owner)
        response = self.client.get(self.list_url, {"lms_user_id": self.other.id})
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_learner_may_not_select_their_own_account_by_email_address_either(self):
        """The selector is the staff-only part, whoever the account it names belongs to."""
        self.login(self.owner)
        response = self.client.get(self.list_url, {"email": "owner-caller@example.com"})
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_every_selector_that_finds_no_account_answers_the_same_empty_page(self):
        """
        A selection no account matches is an empty page, whichever selector was given.

        A caller filtering this collection reads one answer for a selection
        that matched nothing, instead of having to tell an empty page from a
        refusal by which parameter it sent.
        """
        self.login(self.staff)
        selections = (
            {"username": "nobody"},
            {"email": "nobody@example.com"},
            {"lms_user_id": 999999},
        )
        for selection in selections:
            with self.subTest(**selection):
                response = self.client.get(self.list_url, selection)
                assert response.status_code == status.HTTP_200_OK, response.content
                assert set(response.data) == PAGINATION_KEYS
                assert response.data["count"] == 0
                assert response.data["results"] == []
                assert response.data["next"] is None

    def test_an_identifier_that_is_not_a_number_is_refused(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"lms_user_id": "nope"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"lms_user_id": ["A valid integer is required."]}

    def test_an_address_that_is_not_an_email_address_is_refused(self):
        self.login(self.staff)
        response = self.client.get(self.list_url, {"email": "not-an-address"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"email": ["Enter a valid email address."]}

    def test_names_are_read_before_the_other_selectors(self):
        self.login(self.staff)
        response = self.client.get(
            self.list_url, {"username": "owner-caller", "email": "other-caller@example.com"},
        )
        assert self.usernames_in(response) == ["owner-caller"]


@skip_unless_lms
class RetiredAccountTests(AccountApiV2TestCase):
    """An email address an account held before it was retired."""

    def setUp(self):
        super().setUp()
        self.login(self.staff)
        self.retired_email = self.other.email

    def retire(self):
        """Replace the account's address with its retired form, as retirement does."""
        self.other.email = get_retired_email_by_email(self.retired_email)
        self.other.save()

    def test_the_address_of_a_retired_account_is_not_found(self):
        self.retire()
        response = self.client.get(self.list_url, {"email": self.retired_email})
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="user/retired-account",
        )
        assert envelope["title"] == "Retired Account"
        assert envelope["can_cancel_retirement"] is False
        assert envelope["retirement_id"] is None

    def test_reading_the_address_of_a_retired_account_writes_nothing(self):
        """The record the refusal names is read, and the refusal leaves it as it was."""
        RetirementState.objects.create(state_name="PENDING", state_execution_order=1)
        UserRetirementStatus.create_retirement(self.other)
        self.retire()
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.get(self.list_url, {"email": self.retired_email})
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert write_queries(captured.captured_queries) == []

    def test_a_retirement_still_inside_the_cooling_off_period_can_be_cancelled(self):
        RetirementState.objects.create(state_name="PENDING", state_execution_order=1)
        retirement = UserRetirementStatus.create_retirement(self.other)
        self.retire()
        response = self.client.get(self.list_url, {"email": self.retired_email})
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="user/retired-account",
        )
        assert envelope["can_cancel_retirement"] is True
        assert envelope["retirement_id"] == retirement.id

    def test_a_retirement_older_than_the_cooling_off_period_can_no_longer_be_cancelled(self):
        RetirementState.objects.create(state_name="PENDING", state_execution_order=1)
        retirement = UserRetirementStatus.create_retirement(self.other)
        long_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=settings.COOL_OFF_DAYS + 1,
        )
        UserRetirementStatus.objects.filter(pk=retirement.pk).update(created=long_ago)
        self.retire()
        envelope = assert_error_envelope(
            self.client.get(self.list_url, {"email": self.retired_email}),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="user/retired-account",
        )
        assert envelope["can_cancel_retirement"] is False
        assert envelope["retirement_id"] is None


@skip_unless_lms
class AccountDetailViewTests(AccountApiV2TestCase):
    """GET /api/user/v2/accounts/{username}/ and its ``me`` form."""

    def test_a_caller_reads_their_own_account_through_the_reserved_identifier(self):
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "owner-caller"
        assert set(response.data) == FULL_FIELDS

    def test_the_reserved_identifier_names_the_caller_and_not_the_account_holding_it(self):
        """``me`` addresses the caller, so an account holding that name is read through the list."""
        UserFactory.create(username="me", email="me@example.com", password=PASSWORD)
        self.login(self.staff)
        assert self.client.get(self.own_url).data["username"] == "staff-caller"

        named = self.client.get(self.list_url, {"username": "me"})
        assert named.status_code == status.HTTP_200_OK
        assert self.usernames_in(named) == ["me"]
        assert named.data["results"][0]["email"] == "me@example.com"

    def test_an_account_named_after_another_collection_is_served_at_its_own_address(self):
        """
        A username is whatever its owner registered, and the collections beside this one take none away.

        ``email_searches`` is a name a user may hold, so the member address
        carrying it belongs to that account: its owner reads and changes it
        there, a staff caller reads it there, and another learner is answered
        the properties that account shares rather than told it is absent.
        """
        held = UserFactory.create(
            username="email_searches", email="email-searches@example.com", password=PASSWORD,
        )
        address = self.detail_url("email_searches")

        self.login(held)
        own = self.client.get(address)
        assert own.status_code == status.HTTP_200_OK
        assert own.data["username"] == "email_searches"
        assert own.data["email"] == "email-searches@example.com"
        changed = self.patch(address, json.dumps({"bio": "named for a search"}))
        assert changed.status_code == status.HTTP_200_OK
        assert changed.data["bio"] == "named for a search"
        self.client.logout()

        self.login(self.staff)
        by_staff = self.client.get(address)
        assert by_staff.status_code == status.HTTP_200_OK
        assert by_staff.data["username"] == "email_searches"
        named = self.client.get(self.list_url, {"username": "email_searches"})
        assert self.usernames_in(named) == ["email_searches"]
        assert named.data["results"][0]["email"] == "email-searches@example.com"
        self.client.logout()

        self.login(self.other)
        by_a_learner = self.client.get(address)
        assert by_a_learner.status_code == status.HTTP_200_OK
        assert set(by_a_learner.data) == PUBLIC_FIELDS
        assert by_a_learner.data["username"] == "email_searches"

    def test_a_member_address_spelled_in_another_case_carries_the_account_it_names(self):
        """
        A database that holds usernames case-insensitively answers an address spelled differently.

        Whether two names differing only in case name one account is the
        database's decision, and the collation MySQL applies to these columns
        by default says they do, so an account can come back stored under a
        spelling other than the one the address carried. The test database
        compares the names exactly, so the account service is answered here as
        a database holding them case-insensitively answers it.
        """
        def stored_under_another_spelling(request, usernames=None, **kwargs):  # pylint: disable=unused-argument
            assert usernames == ["Owner-Caller"]
            return [{"username": "owner-caller", "email": "owner-caller@example.com"}]

        self.login(self.staff)
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.get_account_settings",
            stored_under_another_spelling,
        ):
            response = self.client.get(self.detail_url("Owner-Caller"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"username": "owner-caller", "email": "owner-caller@example.com"}

    def test_a_caller_reads_their_own_account_by_name(self):
        self.login(self.owner)
        response = self.client.get(self.detail_url("owner-caller"))
        assert set(response.data) == FULL_FIELDS
        assert response.data["email"] == "owner-caller@example.com"

    def test_a_learner_sees_only_the_shared_properties_of_another_account(self):
        self.login(self.owner)
        response = self.client.get(self.detail_url("other-caller"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PUBLIC_FIELDS

    def test_a_staff_caller_sees_every_property_of_another_account(self):
        self.login(self.staff)
        response = self.client.get(self.detail_url("owner-caller"))
        assert set(response.data) == FULL_FIELDS
        assert response.data["email"] == "owner-caller@example.com"

    def test_the_shared_preset_narrows_the_callers_own_account(self):
        self.login(self.owner)
        response = self.client.get(self.detail_url("owner-caller"), {"view": "shared"})
        assert set(response.data) == PUBLIC_FIELDS

    def test_the_minimal_preset_keeps_the_identifying_properties(self):
        self.login(self.owner)
        response = self.client.get(self.own_url, {"view": "minimal"})
        assert set(response.data) == {"username", "email", "name", "profile_image"}

    def test_the_empty_preset_narrows_nothing_at_any_address_that_takes_one(self):
        """
        A query string built from an empty variable carries a preset with no
        value, which is answered with everything the caller may see.
        """
        self.login(self.owner)
        assert set(self.client.get(self.own_url, {"view": ""}).data) == FULL_FIELDS
        assert set(self.client.get(self.list_url, {"view": ""}).data["results"][0]) == FULL_FIELDS
        changed = self.patch(self.own_url, json.dumps({"bio": "unnarrowed"}), QUERY_STRING="view=")
        assert changed.status_code == status.HTTP_200_OK
        assert set(changed.data) == FULL_FIELDS

    def test_a_learner_selecting_an_identifying_property_they_may_not_see_is_refused(self):
        """The preset keeps the identifying properties; this caller may be answered with some of them."""
        self.login(self.owner)
        response = self.client.get(
            self.detail_url("other-caller"), {"view": "minimal", "fields": "email"},
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            f"No such field: email. Select any of: {SHAREABLE_MINIMAL_FIELDS_OFFERED}.",
        ]

    def test_selected_fields_narrow_the_account(self):
        self.login(self.owner)
        response = self.client.get(self.own_url, {"fields": "username,email"})
        assert response.data == {"username": "owner-caller", "email": "owner-caller@example.com"}

    def test_a_learner_selecting_a_property_of_another_account_they_may_not_see_is_refused(self):
        """A property this caller's response cannot carry is refused, not answered as an empty object."""
        self.login(self.owner)
        response = self.client.get(self.detail_url("other-caller"), {"fields": "email"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            f"No such field: email. Select any of: {SHAREABLE_FIELDS_OFFERED}.",
        ]

    def test_a_learner_selecting_a_property_this_owner_does_not_share_receives_the_rest(self):
        """
        A property an owner is able to share can be selected; whether they share it is their own choice.

        This owner shares nothing beyond the properties every caller may see,
        so the account carries the rest of the selection rather than the
        property asked for.
        """
        self.login(self.owner)
        response = self.client.get(self.detail_url("other-caller"), {"fields": "bio,username"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"username": "other-caller"}

    def test_a_staff_caller_still_selects_a_property_only_they_may_see(self):
        self.login(self.staff)
        response = self.client.get(self.detail_url("other-caller"), {"fields": "email"})
        assert response.data == {"email": "other-caller@example.com"}

    def test_the_reserved_identifier_selects_the_properties_of_the_callers_own_account(self):
        self.login(self.owner)
        response = self.client.get(self.own_url, {"fields": "email"})
        assert response.data == {"email": "owner-caller@example.com"}

    def test_a_name_no_account_holds_is_not_found(self):
        self.login(self.staff)
        response = self.client.get(self.detail_url("nobody"))
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_an_anonymous_caller_is_unauthenticated(self):
        response = self.client.get(self.detail_url("owner-caller"))
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_a_selector_of_the_list_is_not_a_parameter_of_a_member(self):
        self.login(self.staff)
        response = self.client.get(self.detail_url("owner-caller"), {"ordering": "username"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "ordering": [f"No such query parameter. Send any of: {MEMBER_QUERY_PARAMS}."],
        }

    def test_a_member_carries_no_page_so_the_parameters_that_cut_one_are_refused(self):
        """
        One account is not a page of them, and a parameter that cuts a page is refused here.

        A caller that mistyped a parameter of the collection and sent it to a
        member would otherwise be answered the whole account as though the
        parameter had been read.
        """
        self.login(self.owner)
        for address in (self.own_url, self.detail_url("owner-caller")):
            envelope = assert_error_envelope(
                self.client.get(address, {"page": 9, "page_size": 3}),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {
                "page": [f"No such query parameter. Send any of: {MEMBER_QUERY_PARAMS}."],
                "page_size": [f"No such query parameter. Send any of: {MEMBER_QUERY_PARAMS}."],
            }

    def test_a_change_carries_no_page_either(self):
        """A change answers with the one account it changed, so it reads no page parameter."""
        self.login(self.owner)
        envelope = assert_error_envelope(
            self.patch(f"{self.own_url}?page=2", json.dumps({"bio": "paged"})),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "page": [f"No such query parameter. Send any of: {MEMBER_QUERY_PARAMS}."],
        }
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio is None


@skip_unless_lms
class UnconfirmedAccountTests(AccountApiV2TestCase):
    """
    A user who has not confirmed their email address still reaches their own account.

    The site sends them to account settings to change the address, so the
    session class these views accept is the one that admits them.
    """

    def setUp(self):
        super().setUp()
        self.owner.is_active = False
        self.owner.save()

    def test_an_unconfirmed_account_is_readable_by_its_owner(self):
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "owner-caller"
        assert response.data["is_active"] is False

    def test_an_unconfirmed_account_is_changeable_by_its_owner(self):
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"bio": "still unconfirmed"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["bio"] == "still unconfirmed"

    def test_a_token_held_by_a_disabled_staff_account_may_not_select_accounts_by_contact(self):
        """A privilege does not survive the deactivation of the account that held it."""
        headers = self.token_headers(self.staff)
        for query in ({"email": "other-caller@example.com"}, {"lms_user_id": self.other.id}):
            assert self.client.get(self.list_url, query, **headers).status_code == status.HTTP_200_OK
        self.staff.is_active = False
        self.staff.save()
        for query in ({"email": "other-caller@example.com"}, {"lms_user_id": self.other.id}):
            assert_error_envelope(
                self.client.get(self.list_url, query, **headers),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_a_disabled_staff_account_still_reads_the_accounts_it_names(self):
        """Naming an account is not the privilege a disabled account loses."""
        self.staff.is_active = False
        self.staff.save()
        headers = self.token_headers(self.staff)
        page = self.client.get(self.list_url, {"username": "other-caller"}, **headers)
        assert page.status_code == status.HTTP_200_OK
        assert self.usernames_in(page) == ["other-caller"]

    def test_a_token_is_read_here_without_recording_the_address_as_one_on_the_defaults(self):
        """
        This address names its own classes, so it is not one the default pair serves.

        The pair the platform configures records every request it reads a
        token for, so that an operator can list the addresses it serves. This
        one replaces the session half of it and is therefore not among them,
        and nothing here is recorded as if it were. The address read afterwards
        names no classes of its own and is recorded, which is what shows the
        record is reachable from a request made this way at all.
        """
        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            answer = self.client.get(self.detail_url("other-caller"), **self.token_headers(self.other))
        assert answer.status_code == status.HTTP_200_OK
        assert answer.json()["username"] == "other-caller"
        assert recorded.call_args_list == []

        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            on_the_default_pair = self.client.get(
                reverse("user_v2:user_preference_list"), **self.token_headers(self.staff),
            )
        assert on_the_default_pair.status_code == status.HTTP_200_OK
        assert [call.args for call in recorded.call_args_list] == [("using_default_auth_classes", True)]

    def test_an_unconfirmed_account_still_may_not_search_for_others(self):
        self.staff.is_active = False
        self.staff.save()
        response = self.client.post(
            reverse("user_v2:email_search_list"),
            {"emails": ["other-caller@example.com"]},
            format="json",
            **self.token_headers(self.staff),
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )


@skip_unless_lms
class AccountChangeTests(AccountApiV2TestCase):
    """PATCH /api/user/v2/accounts/{username}/"""

    def test_an_owner_changes_their_own_account(self):
        self.login(self.owner)
        response = self.patch(self.detail_url("owner-caller"), json.dumps({"bio": "a new bio"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["bio"] == "a new bio"
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio == "a new bio"

    def test_the_reserved_identifier_changes_the_callers_own_account(self):
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"goals": "learn everything"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["goals"] == "learn everything"

    def test_every_level_of_education_a_profile_stores_is_taken_as_it_is_sent(self):
        """The codes are not all one letter, and each is stored as the caller sent it."""
        self.login(self.owner)
        offered = [code for code, _label in UserProfile.LEVEL_OF_EDUCATION_CHOICES]
        assert offered == ["p", "m", "b", "a", "hs", "jhs", "el", "none", "other"]
        for code in offered:
            with self.subTest(level_of_education=code):
                response = self.patch(self.own_url, json.dumps({"level_of_education": code}))
                assert response.status_code == status.HTTP_200_OK, response.content
                assert response.data["level_of_education"] == code
                self.owner.profile.refresh_from_db()
                assert self.owner.profile.level_of_education == code

    def test_the_changed_account_is_shaped_like_a_read_of_it(self):
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"bio": "x"}), QUERY_STRING="view=minimal")
        assert set(response.data) == {"username", "email", "name", "profile_image"}

    def test_the_shared_preset_narrows_the_changed_account_as_it_narrows_a_read_of_it(self):
        """
        The preset asked for shapes what a change answers with, as it shapes what a read answers with.

        A caller asking for the shared form of their account after changing it
        is answered the properties their account shares with everybody, not
        the ones only they may see.
        """
        self.login(self.owner)
        response = self.patch(
            self.own_url, json.dumps({"bio": "shared bio"}), QUERY_STRING="view=shared",
        )
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PUBLIC_FIELDS
        assert response.data["username"] == "owner-caller"
        read = self.client.get(self.own_url, {"view": "shared"})
        assert set(read.data) == set(response.data)

        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio == "shared bio"
        assert self.client.get(self.own_url).data["bio"] == "shared bio"

    def test_a_property_the_shared_preset_leaves_out_cannot_be_selected_from_a_change(self):
        self.login(self.owner)
        response = self.patch(
            self.own_url, json.dumps({"bio": "x"}), QUERY_STRING="view=shared&fields=email",
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            f"No such field: email. Select any of: {SHAREABLE_FIELDS_OFFERED}.",
        ]
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio is None

    def test_a_learner_may_not_change_another_account(self):
        self.login(self.owner)
        response = self.patch(self.detail_url("other-caller"), json.dumps({"bio": "not mine"}))
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )
        self.other.profile.refresh_from_db()
        assert self.other.profile.bio is None

    def test_a_staff_caller_may_not_change_another_account_either(self):
        """Reading somebody else's account is a staff privilege; changing it is nobody's."""
        self.login(self.staff)
        response = self.patch(self.detail_url("owner-caller"), json.dumps({"bio": "not mine"}))
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_name_no_account_holds_is_refused_before_it_is_looked_up(self):
        """Only the caller's own account can be changed, so no other name is reached."""
        self.login(self.owner)
        response = self.patch(self.detail_url("nobody"), json.dumps({"bio": "x"}))
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_an_anonymous_caller_is_unauthenticated(self):
        response = self.patch(self.detail_url("owner-caller"), json.dumps({"bio": "x"}))
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_a_body_in_another_media_type_is_refused(self):
        self.login(self.owner)
        response = self.client.patch(
            self.detail_url("owner-caller"), json.dumps({"bio": "x"}), content_type="application/json",
        )
        assert_error_envelope(
            response,
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )

    def test_a_body_that_is_not_well_formed_is_refused_as_the_callers_fault(self):
        self.login(self.owner)
        response = self.client.patch(self.own_url, "{not json", content_type=MERGE_PATCH)
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="malformed-body",
        )
        assert envelope["title"] == "Malformed Request Body"

    def test_a_body_that_is_not_an_object_is_refused(self):
        self.login(self.owner)
        response = self.patch(self.detail_url("owner-caller"), json.dumps(["bio"]))
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "non_field_errors": ["Send the properties to change as an object."],
        }

    def test_a_value_the_property_cannot_take_is_refused_with_the_property_named(self):
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"year_of_birth": "recently"}))
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"year_of_birth": ["A valid integer is required."]}

    def test_a_value_of_the_right_type_the_account_will_not_hold_is_refused_by_the_account(self):
        """
        What a property may hold is still settled where the change is carried out.

        The body is measured against the published shape first, and that is
        about types and nothing else: a value of the right type that this
        property cannot hold comes back refused in the account's own words.
        """
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"country": "Neverland", "bio": "x" * 4000}))
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "country": ["This value is invalid."],
            "bio": ["The about me field must be at most 300 characters long."],
        }
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.country is None
        assert self.owner.profile.bio is None

    def test_a_property_this_version_does_not_publish_still_reaches_the_account(self):
        """
        The body is checked against the published shape and then carried out as it was sent.

        A property the published shape does not carry is left for the account
        to deal with, which either changes something by it or ignores it; what
        it must not do is disappear here, because the account is where the
        properties that may not be changed are refused.
        """
        self.login(self.owner)
        ignored = self.patch(self.own_url, json.dumps({"time_zone": "Asia/Tokyo"}))
        assert ignored.status_code == status.HTTP_200_OK

        refused = self.patch(self.own_url, json.dumps({"is_active": False}))
        envelope = assert_error_envelope(
            refused, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"is_active": ["The 'is_active' field cannot be edited."]}
        self.owner.refresh_from_db()
        assert self.owner.is_active is True

    def test_a_property_that_cannot_be_changed_is_refused_with_the_property_named(self):
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"username": "renamed"}))
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"username": ["The 'username' field cannot be edited."]}
        self.owner.refresh_from_db()
        assert self.owner.username == "owner-caller"

    def test_no_change_is_kept_when_one_property_of_the_body_is_refused(self):
        self.login(self.owner)
        response = self.patch(
            self.own_url, json.dumps({"bio": "kept?", "year_of_birth": "recently"}),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio is None

    @override_settings(EMAIL_CHANGE_RATE_LIMIT="1/d", CACHES=counting_cache("email-change-refused"))
    def test_changing_an_email_address_too_often_is_refused(self):
        self.login(self.owner)
        first = self.patch(self.own_url, json.dumps({"email": "owner-first@example.com"}))
        assert first.status_code == status.HTTP_200_OK

        second = self.patch(self.own_url, json.dumps({"email": "owner-second@example.com"}))
        envelope = assert_error_envelope(
            second, expected_status=status.HTTP_429_TOO_MANY_REQUESTS, expected_type_slug="rate-limited",
        )
        assert envelope["detail"] == "Wait before changing email again."

    @override_settings(EMAIL_CHANGE_RATE_LIMIT="1/d", CACHES=counting_cache("email-change-allowed"))
    def test_the_rate_limit_leaves_a_change_of_another_property_alone(self):
        self.login(self.owner)
        assert self.patch(self.own_url, json.dumps({"email": "owner-first@example.com"})).status_code == (
            status.HTTP_200_OK
        )
        later = self.patch(self.own_url, json.dumps({"bio": "unrelated"}))
        assert later.status_code == status.HTTP_200_OK
        assert later.data["bio"] == "unrelated"

    def test_a_property_the_body_cannot_carry_a_null_for_is_cleared_with_an_empty_text(self):
        """Neither property takes a null, so the change that empties each one is pinned here."""
        self.login(self.owner)
        assert self.patch(
            self.own_url, json.dumps({"bio": "a bio", "phone_number": "+15550000"}),
        ).status_code == status.HTTP_200_OK

        cleared = self.patch(self.own_url, json.dumps({"bio": "", "phone_number": ""}))
        assert cleared.status_code == status.HTTP_200_OK
        assert cleared.data["bio"] is None
        assert cleared.data["phone_number"] is None

    def test_an_email_change_waits_for_the_owner_to_confirm_it(self):
        self.login(self.owner)
        response = self.patch(self.own_url, json.dumps({"email": "owner-new@example.com"}))
        assert response.status_code == status.HTTP_200_OK
        self.owner.refresh_from_db()
        assert self.owner.email == "owner-caller@example.com"


#: Not text, so not a name: the refusals below quote these back.
NOT_TEXT = "Not a valid string."
#: What a property that carries a list of objects says about anything else.
NOT_A_LIST = 'Expected a list of items but got type "{}".'
#: Nothing at all, where the property carries something or is left out.
NOT_SENDABLE_AS_NULL = "This field may not be null."
#: What a property holding an email address says about anything else.
NOT_AN_ADDRESS = "Enter a valid email address."

#: A property of an account, a value of a type it is not sent as, and what the
#: caller is told. Each pair reached the account service unmeasured before the
#: body was checked against the shape this endpoint publishes, and several were
#: answered as a failure of the server.
WRONGLY_TYPED_CHANGES = (
    ("name", None, NOT_SENDABLE_AS_NULL),
    ("name", 5, NOT_TEXT),
    ("name", [], NOT_TEXT),
    ("name", {}, NOT_TEXT),
    ("name", True, NOT_TEXT),
    ("bio", None, NOT_SENDABLE_AS_NULL),
    ("bio", 5, NOT_TEXT),
    ("email", None, NOT_SENDABLE_AS_NULL),
    ("email", 5, NOT_AN_ADDRESS),
    ("email", True, NOT_AN_ADDRESS),
    ("secondary_email", 5, NOT_AN_ADDRESS),
    ("country", [], NOT_TEXT),
    ("country", {}, NOT_TEXT),
    ("phone_number", 5, NOT_TEXT),
    ("phone_number", [], NOT_TEXT),
    ("phone_number", {}, NOT_TEXT),
    ("phone_number", True, NOT_TEXT),
    ("year_of_birth", [], "A valid integer is required."),
    ("account_privacy", [], NOT_TEXT),
    ("social_links", None, NOT_SENDABLE_AS_NULL),
)


@skip_unless_lms
class AccountChangeBodyShapeTests(AccountApiV2TestCase):
    """
    A change is measured against the shape this endpoint publishes before it is carried out.

    The account service reads a body of its own shape and its helpers assume a
    property arrives as the type the document gives it, so a value of another
    type used to reach them unmeasured. Here each one is the caller's fault,
    named property by property, and the account is left as it was.
    """

    def setUp(self):
        super().setUp()
        self.owner.profile.bio = "A bio that stays."
        self.owner.profile.name = "Owner Caller"
        self.owner.profile.country = "GB"
        self.owner.profile.phone_number = "+15550000"
        self.owner.profile.save()
        self.login(self.owner)

    def stored(self):
        """Return the stored values of the properties these bodies try to change."""
        self.owner.refresh_from_db()
        self.owner.profile.refresh_from_db()
        return {
            "email": self.owner.email,
            "name": self.owner.profile.name,
            "bio": self.owner.profile.bio,
            "country": self.owner.profile.country.code,
            "phone_number": self.owner.profile.phone_number,
        }

    def test_a_property_sent_as_another_type_is_refused_with_the_property_named(self):
        """Each pair is answered 400, naming the property and what it will not take."""
        before = self.stored()
        for name, value, reason in WRONGLY_TYPED_CHANGES:
            with self.subTest(property=name, value=value):
                response = self.patch(self.own_url, json.dumps({name: value}))
                envelope = assert_error_envelope(
                    response,
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )
                assert envelope["errors"] == {name: [reason]}
        assert self.stored() == before

    def test_a_null_for_a_property_the_body_publishes_no_null_for_is_refused(self):
        """``bio`` and ``name`` carry text and no null, and each says so rather than failing."""
        for name in ("bio", "name"):
            with self.subTest(property=name):
                response = self.patch(self.own_url, json.dumps({name: None}))
                envelope = assert_error_envelope(
                    response,
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )
                assert envelope["errors"] == {name: ["This field may not be null."]}
        assert self.stored()["bio"] == "A bio that stays."
        assert self.stored()["name"] == "Owner Caller"

    def test_a_number_is_not_read_as_the_digits_that_spell_it(self):
        """A property that carries text takes text, and the account keeps the text it had."""
        response = self.patch(self.own_url, json.dumps({"bio": 5}))
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"bio": ["Not a valid string."]}
        assert self.stored()["bio"] == "A bio that stays."

    def test_a_property_that_carries_a_list_is_refused_when_it_is_not_one(self):
        """
        The refusal names the property and says a list is what belongs there.

        It is quoted rather than matched whole because the envelope carries one
        line of text per property, and a refusal that came from inside a list
        is written into that line as it stood.
        """
        for name, sent, kind in (
            ("language_proficiencies", 5, "int"),
            ("extended_profile", {}, "dict"),
            ("social_links", "https://www.facebook.com/owner", "str"),
        ):
            with self.subTest(property=name):
                response = self.patch(self.own_url, json.dumps({name: sent}))
                envelope = assert_error_envelope(
                    response,
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )
                assert list(envelope["errors"]) == [name]
                assert NOT_A_LIST.format(kind) in envelope["errors"][name][0]

    def test_an_incomplete_object_inside_the_body_is_refused(self):
        """A property that is an object of its own is sent whole, not a part at a time."""
        response = self.patch(
            self.own_url, json.dumps({"social_links": [{"platform": "facebook"}]}),
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        # The envelope carries one line per property, so a refusal from inside a
        # list arrives as the text of the refusal rather than as its own object.
        assert list(envelope["errors"]) == ["social_links"]
        assert "social_link" in envelope["errors"]["social_links"][0]
        assert "This field is required." in envelope["errors"]["social_links"][0]
        assert self.owner.profile.social_links.count() == 0

    def test_an_empty_address_still_drops_a_platform(self):
        """The body publishes an empty address as the way to drop a link, and it is taken."""
        assert self.patch(
            self.own_url,
            json.dumps({"social_links": [{"platform": "facebook", "social_link": "https://www.facebook.com/owner"}]}),
        ).status_code == status.HTTP_200_OK
        assert self.owner.profile.social_links.count() == 1

        dropped = self.patch(
            self.own_url, json.dumps({"social_links": [{"platform": "facebook", "social_link": ""}]}),
        )
        assert dropped.status_code == status.HTTP_200_OK
        assert dropped.data["social_links"] == []
        assert self.owner.profile.social_links.count() == 0

    def test_the_change_a_refused_body_asked_for_alongside_is_not_carried_out(self):
        """One unusable value refuses the whole body, as one the account rejects does."""
        self.owner.profile.goals = "Finish the course."
        self.owner.profile.save()

        response = self.patch(self.own_url, json.dumps({"goals": "learn", "name": 5}))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.goals == "Finish the course."


@skip_unless_lms
class AccountChangeFailureTests(AccountApiV2TestCase):
    """
    A change that was asked for correctly and could not be carried out is this server's fault.

    What is left once the body has been measured and the account has had its
    say is the work itself failing, and a caller cannot mend that by sending
    something else: it is reported as a failure here, not as an unusable
    request.
    """

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def test_a_change_that_cannot_be_stored_is_answered_as_this_servers_failure(self):
        with mock.patch(
            "openedx.core.djangoapps.user_api.accounts.api.AccountLegacyProfileSerializer.save",
            side_effect=DatabaseError("the rows would not take it"),
        ):
            response = self.patch(self.own_url, json.dumps({"bio": "a bio"}))
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert envelope["detail"] == "The change could not be carried out."
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio is None

    def test_a_message_that_cannot_be_sent_is_answered_as_this_servers_failure(self):
        """The address is confirmed from a message, so a change of it fails when none can be sent."""
        with mock.patch(
            "openedx.core.djangoapps.user_api.accounts.api.student_views.do_email_change_request",
            side_effect=ValueError("Unable to send email activation link. Please try again later."),
        ):
            response = self.patch(self.own_url, json.dumps({"email": "owner-new@example.com"}))
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert envelope["detail"] == "The change could not be carried out."
        self.owner.refresh_from_db()
        assert self.owner.email == "owner-caller@example.com"

    def test_what_the_failure_says_is_the_same_whatever_went_wrong(self):
        """Neither answer carries what the account service said, which names rows and addresses."""
        with mock.patch(
            "openedx.core.djangoapps.user_api.accounts.api.AccountLegacyProfileSerializer.save",
            side_effect=DatabaseError("relation user_profile does not exist"),
        ):
            response = self.patch(self.own_url, json.dumps({"bio": "a bio"}))
        assert "relation" not in json.dumps(response.data)
        assert response.data["title"] == "Internal Server Error"

    @override_settings(ALLOW_EMAIL_ADDRESS_CHANGE=False)
    def test_a_site_that_does_not_change_addresses_here_says_so_about_the_address(self):
        """
        The refusal names ``email``, so a form can show it where the caller typed.

        A site that does not take an address change through this endpoint is
        not a failure of the request and not a failure of the server; it is the
        one property this endpoint will not change, and it is reported as that.
        """
        response = self.patch(self.own_url, json.dumps({"email": "owner-new@example.com"}))
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "email": ["This site does not let an email address be changed here."],
        }
        self.owner.refresh_from_db()
        assert self.owner.email == "owner-caller@example.com"

    @override_settings(ALLOW_EMAIL_ADDRESS_CHANGE=False)
    def test_a_site_that_does_not_change_addresses_here_still_changes_the_rest(self):
        response = self.patch(self.own_url, json.dumps({"bio": "still changeable"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["bio"] == "still changeable"


@skip_unless_lms
class SelectionIsSettledBeforeTheAddressIsReadTests(AccountApiV2TestCase):
    """
    A selection this endpoint cannot answer is refused before the account is read or written.

    Which properties an answer can carry depends on the caller and on the
    address, so a selection naming a property this caller cannot be given at
    this address is refused whoever holds the name and whether or not anybody
    does. The refusal names the selection and nothing else, so it says no more
    about who holds a name than the refusal that would follow it, and the
    account the address names is neither read nor changed on the way to it.
    """

    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def assert_selection_refused(self, response, missing):
        """Assert the answer is the refusal of the selection, naming the property asked for."""
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"][0].startswith(f"No such field: {missing}.")
        return envelope

    def test_a_selection_the_answer_cannot_carry_leaves_the_account_as_it_was(self):
        response = self.patch(
            f"{self.own_url}?fields=nosuchfield", json.dumps({"bio": "a bio"}),
        )
        self.assert_selection_refused(response, "nosuchfield")
        self.owner.profile.refresh_from_db()
        assert self.owner.profile.bio is None

    def test_a_property_another_owner_keeps_is_refused_at_their_address(self):
        """``email`` is a property of an account that only its owner and staff read."""
        refused = self.patch(
            f"{self.detail_url('other-caller')}?fields=email", json.dumps({"bio": "a bio"}),
        )
        self.assert_selection_refused(refused, "email")
        self.other.profile.refresh_from_db()
        assert self.other.profile.bio is None

    def test_the_same_selection_is_refused_the_same_way_at_a_name_nobody_holds(self):
        """What comes back names the selection, so it tells nothing about who holds the name."""
        held = self.patch(
            f"{self.detail_url('other-caller')}?fields=email", json.dumps({"bio": "a bio"}),
        )
        unheld = self.patch(
            f"{self.detail_url('nobody-at-all')}?fields=email", json.dumps({"bio": "a bio"}),
        )
        assert held.status_code == unheld.status_code == status.HTTP_400_BAD_REQUEST
        assert held.data["errors"] == unheld.data["errors"]

    def test_a_request_that_names_no_selection_is_answered_by_the_address(self):
        """Without a selection there is nothing to settle, and the address answers as it does."""
        for address in (self.detail_url("other-caller"), self.detail_url("nobody-at-all")):
            response = self.patch(address, json.dumps({"bio": "a bio"}))
            assert_error_envelope(
                response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
            )


@skip_unless_lms
class OtherApiClassificationTests(AccountApiV2TestCase):
    """
    What another API calls a request failure is not changed by what this one calls it.

    The catalog of error types is shared by every API in the process, so naming
    one of the failures below for this API by registering the type it is raised
    as would rename it for all of them. The names used here belong to this
    API's own exception classes; the same two failures are read below at a
    standardized address of another API, which keeps the name the shared
    catalog gives a failure it does not know.
    """

    #: Standardized, served by another API, and reads a request body.
    OTHER_API_WRITE_URL = "/api/enrollment/v2/enrollment/"

    def setUp(self):
        super().setUp()
        self.login(self.staff)

    def assert_unnamed(self, response, expected_status):
        """Assert the failure is reported as one the shared catalog does not name."""
        envelope = assert_error_envelope(
            response, expected_status=expected_status, expected_type_slug="internal",
        )
        assert envelope["title"] == "Internal Server Error"

    def test_another_apis_malformed_body_keeps_the_name_the_shared_catalog_gives_it(self):
        response = self.client.post(
            self.OTHER_API_WRITE_URL, "{not json", content_type="application/json",
        )
        self.assert_unnamed(response, status.HTTP_400_BAD_REQUEST)

    def test_another_apis_unreadable_body_keeps_the_name_the_shared_catalog_gives_it(self):
        response = self.client.post(
            self.OTHER_API_WRITE_URL, "course_id=course-v1:edX+DemoX+Demo", content_type="text/plain",
        )
        self.assert_unnamed(response, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_the_same_two_failures_are_named_at_an_address_of_this_api(self):
        """The contrast the two above are measured against."""
        malformed = self.client.patch(self.own_url, "{not json", content_type=MERGE_PATCH)
        assert_error_envelope(
            malformed,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="malformed-body",
        )
        unreadable = self.client.patch(self.own_url, "bio=x", content_type="text/plain")
        assert_error_envelope(
            unreadable,
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )


@skip_unless_lms
class EmailSearchTests(AccountApiV2TestCase):
    """POST /api/user/v2/email_searches/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:email_search_list")

    def search(self, emails, **kwargs):
        """Ask for the accounts holding the given addresses."""
        return self.client.post(self.url, {"emails": emails}, format="json", **kwargs)

    def test_a_staff_caller_resolves_addresses_to_accounts(self):
        self.login(self.staff)
        response = self.search(["owner-caller@example.com", "other-caller@example.com"])
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [
            {"email": "other-caller@example.com", "id": self.other.id, "username": "other-caller"},
            {"email": "owner-caller@example.com", "id": self.owner.id, "username": "owner-caller"},
        ]

    def test_the_page_carries_the_whole_envelope(self):
        self.login(self.staff)
        response = self.search(["owner-caller@example.com"])
        assert set(response.data) == PAGINATION_KEYS
        assert response.data["count"] == 1

    def test_an_address_no_account_holds_is_left_out_of_the_page(self):
        self.login(self.staff)
        response = self.search(["owner-caller@example.com", "nobody@example.com"])
        assert [row["username"] for row in response.data["results"]] == ["owner-caller"]
        assert response.data["count"] == 1

    def test_the_page_carries_the_accounts_the_visibility_layer_leaves(self):
        """
        The page is answered from a selection the view's policy has narrowed.

        The policy this view declares keeps every matched account, and the
        only caller admitted may read them all, so what is held here is the
        layer rather than a narrowing: a policy that keeps a row back is one
        the page is answered under, not one the search reads past. The rows
        come from the same table the collections of this version narrow, so
        the same policy applies to the search as to them.
        """
        class _WithoutTheOtherAccount:
            """Keeps one account back, standing for a policy narrower than the declared one."""

            def scope(self, queryset, subject):  # pylint: disable=unused-argument
                """Return the accounts of this selection other than the other caller's."""
                return queryset.exclude(username="other-caller")

        self.login(self.staff)
        with mock.patch.object(EmailSearchViewSet, "scoping_policy", _WithoutTheOtherAccount()):
            response = self.search(["owner-caller@example.com", "other-caller@example.com"])
        assert response.status_code == status.HTTP_200_OK
        assert [row["username"] for row in response.data["results"]] == ["owner-caller"]
        assert response.data["count"] == 1

    def test_the_search_declares_which_of_the_accounts_it_finds_a_caller_may_see(self):
        """Every account the addresses match is answered, because that is what the policy keeps."""
        assert isinstance(EmailSearchViewSet.scoping_policy, FullScopePolicy)
        self.login(self.staff)
        response = self.search(["owner-caller@example.com", "other-caller@example.com"])
        assert [row["username"] for row in response.data["results"]] == ["other-caller", "owner-caller"]

    def test_an_empty_search_finds_nothing(self):
        self.login(self.staff)
        response = self.search([])
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []
        assert response.data["count"] == 0

    def test_a_body_without_the_addresses_is_refused(self):
        self.login(self.staff)
        response = self.client.post(self.url, {}, format="json")
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"emails": ["This field is required."]}

    def test_an_entry_that_is_not_an_address_is_refused_with_a_message(self):
        """
        A refusal names the entries at fault in a sentence a client can show.

        An entry reported under the position it holds would reach the caller
        as a mapping nested inside the member naming the list, which nothing
        can display.
        """
        self.login(self.staff)
        response = self.client.post(
            self.url, {"emails": [None, "owner-caller@example.com", {"address": "x"}]}, format="json",
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "emails": ["Every entry must be text, and the entries at these positions are not: 0, 2."],
        }

    def test_addresses_that_are_not_a_list_are_refused(self):
        self.login(self.staff)
        response = self.client.post(self.url, {"emails": "owner-caller@example.com"}, format="json")
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"emails": ['Expected a list of items but got type "str".']}

    def test_a_body_that_is_not_well_formed_is_refused_as_the_callers_fault(self):
        self.login(self.staff)
        response = self.client.post(self.url, "{not json", content_type="application/json")
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="malformed-body",
        )
        assert envelope["title"] == "Malformed Request Body"

    def test_a_learner_is_told_there_is_nothing_at_this_address(self):
        """The search is hidden rather than refused, so it does not confirm what it is for."""
        self.login(self.owner)
        response = self.search(["owner-caller@example.com"])
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_an_anonymous_caller_is_unauthenticated_rather_than_hidden_from(self):
        response = self.search(["owner-caller@example.com"])
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_a_staff_token_reaches_the_search(self):
        response = self.search(["owner-caller@example.com"], **self.token_headers(self.staff))
        assert response.status_code == status.HTTP_200_OK
        assert [row["username"] for row in response.data["results"]] == ["owner-caller"]

    def test_a_token_held_by_a_deactivated_staff_account_is_refused(self):
        headers = self.token_headers(self.staff)
        assert self.search(["owner-caller@example.com"], **headers).status_code == status.HTTP_200_OK
        self.staff.is_active = False
        self.staff.save()
        assert_error_envelope(
            self.search(["owner-caller@example.com"], **headers),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_a_session_whose_owner_has_not_confirmed_their_address_identifies_nobody_here(self):
        """
        Searching other people's accounts is not done from the pages an unconfirmed account reaches.

        The account addresses accept such a session because the site's own
        settings pages are reached with one; this address keeps the platform
        default, under which it identifies nobody, and the same session still
        reads the account it belongs to.
        """
        self.staff.is_active = False
        self.staff.save()
        self.login(self.staff)
        assert_error_envelope(
            self.search(["owner-caller@example.com"]),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        own = self.client.get(self.own_url)
        assert own.status_code == status.HTTP_200_OK
        assert own.data["username"] == "staff-caller"

    def test_the_search_is_not_read_with_a_get(self):
        self.login(self.staff)
        response = self.client.get(self.url)
        assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )

    def test_the_rest_of_a_search_is_read_by_sending_the_body_to_the_link_again(self):
        """
        A caller who asked for pages reads the rest by sending the same body to the link.

        The link is an address like any other, and this collection is only
        read by sending the addresses to it, so a caller follows the link the
        way they made the first request rather than by fetching it.
        """
        self.login(self.staff)
        addresses = [f"paged-{index:02d}@example.com" for index in range(12)]
        User.objects.bulk_create(
            User(username=f"paged-{index:02d}", email=address)
            for index, address in enumerate(addresses)
        )

        first = self.client.post(f"{self.url}?page_size=10", {"emails": addresses}, format="json")
        assert first.status_code == status.HTTP_200_OK
        assert first.data["count"] == 12
        assert first.data["num_pages"] == 2
        assert [row["username"] for row in first.data["results"]] == [
            f"paged-{index:02d}" for index in range(10)
        ]

        link = first.data["next"]
        assert urlparse(link).path == "/api/user/v2/email_searches/"
        assert parse_qs(urlparse(link).query) == {"page": ["2"], "page_size": ["10"]}
        rest = self.client.post(link, {"emails": addresses}, format="json")
        assert rest.status_code == status.HTTP_200_OK
        assert [row["username"] for row in rest.data["results"]] == ["paged-10", "paged-11"]
        assert rest.data["next"] is None
        assert urlparse(rest.data["previous"]).path == "/api/user/v2/email_searches/"
        assert parse_qs(urlparse(rest.data["previous"]).query) == {"page_size": ["10"]}

    def test_a_search_is_answered_a_page_at_a_time_when_the_caller_asks_for_no_size(self):
        """
        A search that found more accounts than a page carries answers with the first page of them.

        The answer is a list like every other list of this version: it carries
        the rows one page holds and a link to the rest, rather than every
        account found however many that is.
        """
        self.login(self.staff)
        addresses = [f"whole-{index:02d}@example.com" for index in range(12)]
        User.objects.bulk_create(
            User(username=f"whole-{index:02d}", email=address)
            for index, address in enumerate(addresses)
        )

        answer = self.search(addresses)
        assert answer.status_code == status.HTTP_200_OK
        assert answer.data["count"] == 12
        assert answer.data["num_pages"] == 2
        assert [row["username"] for row in answer.data["results"]] == [
            f"whole-{index:02d}" for index in range(10)
        ]
        assert parse_qs(urlparse(answer.data["next"]).query) == {"page": ["2"]}
        assert answer.data["previous"] is None

    def test_a_search_may_name_as_many_addresses_as_the_largest_page_carries(self):
        """A hundred addresses is the most one search takes, and it is answered."""
        self.login(self.staff)
        addresses = [f"bound-{index:03d}@example.com" for index in range(100)]
        User.objects.bulk_create(
            User(username=f"bound-{index:03d}", email=address)
            for index, address in enumerate(addresses)
        )

        answer = self.search(addresses)
        assert answer.status_code == status.HTTP_200_OK
        assert answer.data["count"] == 100
        assert answer.data["num_pages"] == 10
        assert [row["username"] for row in answer.data["results"]] == [
            f"bound-{index:03d}" for index in range(10)
        ]

    def test_a_search_naming_more_addresses_than_that_is_refused(self):
        """
        A search is held to the addresses the largest page could answer for.

        An unbounded list would be read in one database query as long as the
        body, and could ask for more accounts than any page may carry.
        """
        self.login(self.staff)
        response = self.search([f"bound-{index:03d}@example.com" for index in range(101)])
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "emails": ["Ensure this field has no more than 100 elements."],
        }

    def test_fetching_the_link_to_the_rest_of_a_search_is_not_how_it_is_read(self):
        """The link addresses this collection, which is read by sending the addresses to it."""
        self.login(self.staff)
        response = self.client.get(f"{self.url}?page=2")
        assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )

    def test_a_page_the_search_does_not_have_is_not_found(self):
        self.login(self.staff)
        response = self.client.post(
            f"{self.url}?page=7", {"emails": ["owner-caller@example.com"]}, format="json",
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_a_query_parameter_is_not_read_by_the_search(self):
        self.login(self.staff)
        response = self.client.post(
            f"{self.url}?view=minimal", {"emails": []}, format="json",
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": [f"No such query parameter. Send any of: {ACTION_QUERY_PARAMS}."],
        }


@skip_unless_lms
class AccountPageSizeTests(AccountApiV2TestCase):
    """
    How many accounts a page carries when the caller does not say, and the most it will carry.

    Both lists are asked for more rows than they will give, against rows that
    really exist, so what is pinned is the page the endpoint answers with
    rather than the numbers a paginator class holds.
    """

    DEFAULT_PAGE_SIZE = 10
    MAX_PAGE_SIZE = 100

    def extra_accounts(self, count):
        """Create the given number of accounts and return their names in page order."""
        User.objects.bulk_create(
            User(username=f"paged-{index:03d}", email=f"paged-{index:03d}@example.com")
            for index in range(count)
        )
        return [f"paged-{index:03d}" for index in range(count)]

    def test_a_page_of_accounts_carries_ten_of_them_when_the_caller_asks_for_no_size(self):
        names = self.extra_accounts(self.DEFAULT_PAGE_SIZE + 1)
        self.login(self.staff)
        response = self.client.get(self.list_url, {"username": ",".join(names)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.DEFAULT_PAGE_SIZE + 1
        assert self.usernames_in(response) == names[:self.DEFAULT_PAGE_SIZE]
        assert response.data["num_pages"] == 2
        assert "page=2" in response.data["next"]

    def test_a_page_of_accounts_carries_a_hundred_of_them_however_many_are_asked_for(self):
        names = self.extra_accounts(self.MAX_PAGE_SIZE + 1)
        self.login(self.staff)
        response = self.client.get(
            self.list_url, {"username": ",".join(names), "page_size": 1000},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.MAX_PAGE_SIZE + 1
        assert self.usernames_in(response) == names[:self.MAX_PAGE_SIZE]
        assert response.data["num_pages"] == 2
        assert response.data["next"] is not None

    def test_a_page_of_found_addresses_carries_ten_of_them_when_the_caller_asks_for_no_size(self):
        names = self.extra_accounts(self.DEFAULT_PAGE_SIZE + 1)
        self.login(self.staff)
        response = self.client.post(
            reverse("user_v2:email_search_list"),
            {"emails": [f"{name}@example.com" for name in names]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.DEFAULT_PAGE_SIZE + 1
        assert [row["username"] for row in response.data["results"]] == names[:self.DEFAULT_PAGE_SIZE]
        assert response.data["num_pages"] == 2
        assert "page=2" in response.data["next"]

    def test_the_longest_search_allowed_fits_in_the_largest_page_a_caller_may_ask_for(self):
        """
        What a search may ask about and what a page may carry are the same number.

        A caller that names the most addresses a search takes and asks for a
        page larger than the cap is answered with all of them at once, so no
        search can find more accounts than one page will hold.
        """
        names = self.extra_accounts(self.MAX_PAGE_SIZE)
        self.login(self.staff)
        response = self.client.post(
            f"{reverse('user_v2:email_search_list')}?page_size=1000",
            {"emails": [f"{name}@example.com" for name in names]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.MAX_PAGE_SIZE
        assert [row["username"] for row in response.data["results"]] == names
        assert response.data["num_pages"] == 1
        assert response.data["next"] is None


@skip_unless_lms
class AccountDeactivationTests(AccountApiV2TestCase):
    """POST /api/user/v2/accounts/{username}/deactivation/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:account_deactivation", kwargs={"username": "other-caller"})
        self.deactivator = UserFactory.create(username="deactivator", password=PASSWORD)
        self.deactivator.user_permissions.add(
            PermissionFactory(
                codename="can_deactivate_users",
                content_type=ContentTypeFactory(app_label="student"),
            ),
        )

    def test_a_caller_holding_the_privilege_deactivates_an_account(self):
        self.login(self.deactivator)
        assert self.other.has_usable_password()
        response = self.client.post(self.url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not response.content
        self.other.refresh_from_db()
        assert not self.other.has_usable_password()

    def test_asking_for_a_password_reset_does_not_bring_a_deactivated_account_back(self):
        """
        The reset flow refuses an account whose password is unusable, which is what this leaves.

        The description of this operation tells a support caller what a
        deactivation can and cannot be undone by, so the refusal it names is
        pinned here: a reset asked for on the deactivated address is refused,
        and the account still has no usable password afterwards.
        """
        self.login(self.deactivator)
        assert self.client.post(self.url).status_code == status.HTTP_204_NO_CONTENT
        form = PasswordResetFormNoActive(data={"email": self.other.email})
        assert form.is_valid() is False
        assert form.errors["email"] == [
            "The user account associated with this e-mail address cannot reset the password.",
        ]
        self.other.refresh_from_db()
        assert not self.other.has_usable_password()

    def test_a_deactivated_account_keeps_its_data(self):
        self.login(self.deactivator)
        self.client.post(self.url)
        self.other.refresh_from_db()
        assert self.other.is_active is True
        assert self.other.username == "other-caller"
        assert self.other.email == "other-caller@example.com"

    def test_a_superuser_deactivates_an_account(self):
        superuser = SuperuserFactory(username="super-caller", password=PASSWORD)
        self.login(superuser)
        assert self.client.post(self.url).status_code == status.HTTP_204_NO_CONTENT

    def test_a_staff_caller_without_the_privilege_is_refused(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        self.other.refresh_from_db()
        assert self.other.has_usable_password()

    def test_a_learner_is_refused(self):
        self.login(self.owner)
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_a_learner_may_not_deactivate_their_own_account_either(self):
        self.login(self.owner)
        response = self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": "owner-caller"}),
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )
        self.owner.refresh_from_db()
        assert self.owner.has_usable_password()

    def test_the_reserved_identifier_names_the_account_holding_it_and_not_the_caller(self):
        """This action names the account to disable, so ``me`` is the username it looks like."""
        held = UserFactory.create(username="me", password=PASSWORD)
        self.login(self.deactivator)
        response = self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": "me"}),
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        held.refresh_from_db()
        assert not held.has_usable_password()
        self.deactivator.refresh_from_db()
        assert self.deactivator.has_usable_password()

    def test_the_reserved_identifier_is_not_found_when_no_account_holds_it(self):
        """Nobody is named ``me`` here, so there is no account at that address to disable."""
        self.login(self.deactivator)
        response = self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": "me"}),
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )
        self.deactivator.refresh_from_db()
        assert self.deactivator.has_usable_password()

    def test_an_anonymous_caller_is_unauthenticated(self):
        assert_error_envelope(
            self.client.post(self.url),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )

    def test_a_query_parameter_is_not_read_by_a_deactivation(self):
        """A parameter this action reads nothing from is refused before the account is touched."""
        self.login(self.deactivator)
        response = self.client.post(f"{self.url}?view=minimal")
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": [f"No such query parameter. Send any of: {DEACTIVATION_QUERY_PARAMS}."],
        }
        self.other.refresh_from_db()
        assert self.other.has_usable_password()

    def test_a_name_no_account_holds_is_not_found(self):
        self.login(self.deactivator)
        response = self.client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": "nobody"}),
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_a_token_held_by_a_deactivated_caller_is_refused(self):
        headers = self.token_headers(self.deactivator)
        self.deactivator.is_active = False
        self.deactivator.save()
        assert_error_envelope(
            self.client.post(self.url, **headers),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        self.other.refresh_from_db()
        assert self.other.has_usable_password()

    def test_a_token_held_by_an_active_caller_reaches_the_action(self):
        response = self.client.post(self.url, **self.token_headers(self.deactivator))
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_the_account_is_not_deactivated_by_reading_it(self):
        self.login(self.deactivator)
        response = self.client.get(self.url)
        assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )
        self.other.refresh_from_db()
        assert self.other.has_usable_password()


@skip_unless_lms
class ProfilelessAccountTests(AccountApiV2TestCase):
    """An account whose user has no profile row is served rather than failing."""

    def setUp(self):
        super().setUp()
        self.profileless = UserFactory.create(username="no-profile", password=PASSWORD)
        self.profileless.profile.delete()

    def test_a_learner_reading_an_account_without_a_profile_sees_the_shared_properties(self):
        self.login(self.owner)
        response = self.client.get(self.detail_url("no-profile"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PUBLIC_FIELDS
        assert response.data["username"] == "no-profile"

    def test_a_staff_caller_reading_an_account_without_a_profile_sees_empty_properties(self):
        self.login(self.staff)
        response = self.client.get(self.detail_url("no-profile"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == FULL_FIELDS
        assert response.data["name"] is None
        assert response.data["country"] is None

    def test_the_owner_of_an_account_without_a_profile_reads_it_too(self):
        self.login(self.profileless)
        response = self.client.get(self.own_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "no-profile"


@skip_unless_lms
@override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=True)
class SecondaryEmailEnabledTests(AccountApiV2TestCase):
    """The recovery address is collected on this site."""

    def test_the_account_says_the_site_collects_a_recovery_address(self):
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.data["secondary_email_enabled"] is True
        assert response.data["secondary_email"] is None


@skip_unless_lms
@override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=False)
class SecondaryEmailDisabledTests(AccountApiV2TestCase):
    """The recovery address is not collected on this site."""

    def test_the_account_says_nothing_about_a_recovery_address(self):
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.data["secondary_email_enabled"] is None
        assert response.data["secondary_email"] is None


@skip_unless_lms
class ExtendedProfileConfiguredTests(AccountApiV2TestCase):
    """A site that collects extra profile fields."""

    def setUp(self):
        super().setUp()
        # The configuration is entered per test. The helper that applies it
        # wraps a function, so a class given to it comes back as a function and
        # the runner finds no tests inside it at all.
        self.enterContext(
            with_site_configuration_context(configuration={"extended_profile_fields": ["department"]}),
        )

    def test_the_account_carries_a_value_for_each_configured_field(self):
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.data["extended_profile"] == [{"field_name": "department", "field_value": ""}]

    def test_an_extra_field_is_changed_by_its_owner(self):
        self.login(self.owner)
        response = self.patch(
            self.own_url,
            json.dumps({"extended_profile": [{"field_name": "department", "field_value": "physics"}]}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["extended_profile"] == [
            {"field_name": "department", "field_value": "physics"},
        ]

    def test_an_extra_field_keeps_the_shape_the_site_stored_it_in(self):
        """A field a site collects as something other than text comes back as that, not as its text."""
        self.login(self.owner)
        response = self.patch(
            self.own_url,
            json.dumps({"extended_profile": [{"field_name": "department", "field_value": 41}]}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["extended_profile"] == [{"field_name": "department", "field_value": 41}]
        reread = self.client.get(self.own_url)
        assert reread.data["extended_profile"] == [{"field_name": "department", "field_value": 41}]

    def test_a_field_the_site_does_not_collect_is_left_out_of_the_account(self):
        """Only the fields the site asks for are carried, whatever else the profile holds."""
        self.owner.profile.meta = json.dumps({"department": "physics", "shoe_size": 44})
        self.owner.profile.save()
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.data["extended_profile"] == [
            {"field_name": "department", "field_value": "physics"},
        ]


@skip_unless_lms
class ExtendedProfileUnconfiguredTests(AccountApiV2TestCase):
    """A site that collects no extra profile fields."""

    def test_the_account_carries_no_extra_fields(self):
        self.login(self.owner)
        response = self.client.get(self.own_url)
        assert response.data["extended_profile"] == []


@skip_unless_lms
class AccountVisibilityTests(AccountApiV2TestCase):
    """What one learner sees of another account is what that account's owner shares."""

    def setUp(self):
        super().setUp()
        # A profile that does not say how old its owner is counts as a child's,
        # and a child shares nothing, so the account being read is an adult's
        # wherever the owner's own choice is what decides.
        self.other.profile.year_of_birth = 1985
        self.other.profile.bio = "Teaches mathematics."
        self.other.profile.save()
        self.login(self.owner)

    def prefer(self, key, value):
        """Record one of the account owner's visibility choices."""
        UserPreferenceFactory.create(user=self.other, key=key, value=value)

    def test_an_account_shared_with_everybody_carries_what_its_owner_shares(self):
        response = self.client.get(self.detail_url("other-caller"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == SHARED_FIELDS
        assert response.data["account_privacy"] == "all_users"
        assert response.data["bio"] == "Teaches mathematics."

    def test_a_page_carries_the_same_shared_properties_as_the_account_itself(self):
        response = self.client.get(self.list_url, {"username": "other-caller"})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["results"][0]) == SHARED_FIELDS
        assert response.data["results"][0]["bio"] == "Teaches mathematics."

    def test_an_account_shared_property_by_property_carries_the_ones_chosen(self):
        self.prefer("account_privacy", "custom")
        self.prefer("visibility.bio", "all_users")
        response = self.client.get(self.detail_url("other-caller"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PUBLIC_FIELDS | {"bio"}
        assert response.data["account_privacy"] == "custom"
        assert response.data["bio"] == "Teaches mathematics."

    def test_an_account_kept_private_carries_only_what_everybody_may_see(self):
        self.prefer("account_privacy", "private")
        response = self.client.get(self.detail_url("other-caller"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PUBLIC_FIELDS
        assert response.data["account_privacy"] == "private"

    def test_an_account_whose_profile_does_not_say_how_old_its_owner_is_shares_nothing(self):
        self.other.profile.year_of_birth = None
        self.other.profile.save()
        self.prefer("account_privacy", "all_users")
        response = self.client.get(self.detail_url("other-caller"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PUBLIC_FIELDS
        assert response.data["account_privacy"] == "private"

    def test_the_owner_of_a_shared_account_still_sees_every_property_of_it(self):
        self.login(self.other)
        response = self.client.get(self.own_url)
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == FULL_FIELDS

    def test_a_selected_property_an_owner_shares_with_everybody_is_answered(self):
        response = self.client.get(self.detail_url("other-caller"), {"fields": "bio"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"bio": "Teaches mathematics."}

    def test_a_selected_property_an_owner_shares_by_choice_is_answered(self):
        self.prefer("account_privacy", "custom")
        self.prefer("visibility.bio", "all_users")
        response = self.client.get(self.detail_url("other-caller"), {"fields": "bio"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"bio": "Teaches mathematics."}

    def test_a_selected_property_an_owner_chose_not_to_share_is_left_out(self):
        """
        Which properties an owner shares is theirs to decide, so a selection can come back empty.

        What can be selected is settled from the properties an owner is able
        to share, which is the same question for every account; whether this
        owner shares this one is a different question, and it is answered
        where the account is read.
        """
        self.prefer("account_privacy", "custom")
        self.prefer("visibility.country", "all_users")
        response = self.client.get(self.detail_url("other-caller"), {"fields": "bio"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {}

    def test_a_selected_property_of_an_account_kept_private_is_left_out(self):
        self.prefer("account_privacy", "private")
        response = self.client.get(self.detail_url("other-caller"), {"fields": "bio"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {}

    def test_a_property_only_a_chosen_few_share_is_left_out_of_an_account_shared_with_everybody(self):
        """Sharing with everybody shares a set the site fixes, and the full name is outside it."""
        response = self.client.get(self.detail_url("other-caller"), {"fields": "name"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {}

    def test_a_staff_caller_selecting_the_same_property_is_answered_it(self):
        """The empty answer above is the owner's choice and not a property the endpoint cannot carry."""
        self.login(self.staff)
        self.prefer("account_privacy", "private")
        response = self.client.get(self.detail_url("other-caller"), {"fields": "bio"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"bio": "Teaches mathematics."}

    def test_a_page_answers_each_row_by_the_choice_of_the_owner_it_belongs_to(self):
        """One selection over two accounts answers what each owner shares, row by row."""
        self.prefer("account_privacy", "private")
        self.owner.profile.bio = "Learning mathematics."
        self.owner.profile.save()
        response = self.client.get(
            self.list_url, {"username": "other-caller,owner-caller", "fields": "bio"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [{}, {"bio": "Learning mathematics."}]


class AccountReadsDoNotWriteMixin:
    """
    Reading an account changes nothing, on every branch a read can take.

    The capture runs the callbacks a request defers to the end of its
    transaction, because a write made from one is a write the request made.
    """

    def captured_writes(self, method, address, params=None, expect=status.HTTP_200_OK, **headers):
        """
        Return the statements a request made that would change stored data.

        The body the request answered with is left on ``self.answer``, so a
        test can show which branch of the read the capture went through
        instead of asserting that an unidentified read wrote nothing. Request
        headers given here reach the client, which is how a read presented
        with a token is captured alongside one made from a session.
        """
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                self.answer = getattr(self.client, method)(address, params or {}, **headers)
        assert self.answer.status_code == expect, self.answer.content
        return write_queries(captured.captured_queries)

    def populate(self, user):
        """Give an account a value for each kind of data a read of it branches on."""
        user.profile.year_of_birth = 1985
        user.profile.bio = "Teaches mathematics."
        user.profile.country = "US"
        user.profile.state = "MA"
        user.profile.profile_image_uploaded_at = datetime.datetime(
            2020, 1, 1, tzinfo=datetime.UTC,
        )
        user.profile.meta = json.dumps({"department": "physics"})
        user.profile.save()
        user.profile.language_proficiencies.create(code="fr")
        user.profile.social_links.create(
            platform="twitter", social_link="https://www.twitter.com/example",
        )
        return user

    def test_a_staff_caller_reading_the_collection_writes_nothing(self):
        self.login(self.staff)
        assert self.captured_writes("get", self.list_url, {"username": "owner-caller,other-caller"}) == []

    def test_a_learner_reading_the_collection_writes_nothing(self):
        self.login(self.owner)
        assert self.captured_writes("get", self.list_url) == []
        assert self.captured_writes("get", self.list_url, {"username": "other-caller"}) == []

    def test_reading_a_member_writes_nothing(self):
        self.login(self.owner)
        assert self.captured_writes("get", self.own_url) == []
        assert self.captured_writes("get", self.detail_url("other-caller")) == []

    def test_a_staff_caller_reading_a_member_writes_nothing(self):
        self.login(self.staff)
        assert self.captured_writes("get", self.detail_url("owner-caller")) == []

    def test_the_shared_preset_writes_nothing(self):
        self.login(self.owner)
        assert self.captured_writes("get", self.own_url, {"view": "shared"}) == []

    def test_heading_the_collection_and_a_member_writes_nothing(self):
        self.login(self.owner)
        assert self.captured_writes("head", self.list_url) == []
        assert self.captured_writes("head", self.own_url) == []

    def test_a_search_writes_nothing(self):
        self.login(self.staff)
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("user_v2:email_search_list"),
                    {"emails": ["owner-caller@example.com"]},
                    format="json",
                )
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []

    def test_narrowing_what_a_read_carries_writes_nothing(self):
        self.login(self.owner)
        assert self.captured_writes("get", self.list_url, {"view": "minimal"}) == []
        assert self.captured_writes("get", self.own_url, {"view": "minimal"}) == []
        assert self.captured_writes("get", self.list_url, {"fields": "username,email"}) == []
        assert self.captured_writes("get", self.own_url, {"fields": "username,email"}) == []

    def test_selecting_an_account_by_something_other_than_its_name_writes_nothing(self):
        self.login(self.staff)
        assert self.captured_writes("get", self.list_url, {"email": "owner-caller@example.com"}) == []
        assert self.captured_writes("get", self.list_url, {"lms_user_id": str(self.owner.id)}) == []

    def test_reading_an_account_with_a_name_change_outstanding_writes_nothing(self):
        PendingNameChange.objects.create(
            user=self.owner, new_name="Changed Name", rationale="Asked for in a test",
        )
        self.login(self.owner)
        assert self.captured_writes("get", self.own_url) == []

    def test_a_read_that_finds_no_account_writes_nothing(self):
        self.login(self.staff)
        assert self.captured_writes(
            "get", self.detail_url("nobody-at-all"), expect=status.HTTP_404_NOT_FOUND,
        ) == []
        assert self.captured_writes(
            "get", self.list_url, {"email": "nobody@example.com"},
        ) == []
        assert self.answer.data["results"] == []

    def test_a_read_a_token_identifies_its_caller_by_writes_nothing(self):
        """
        A caller resolved from a token reads as a signed-in one does.

        A token is how a service or a micro-frontend reaches these addresses,
        and the layer that resolves one runs before the handler, so each read
        is captured under both ways of identifying the caller.
        """
        self.populate(self.owner)
        headers = self.token_headers(self.owner)
        assert self.captured_writes("get", self.own_url, **headers) == []
        assert self.answer.data["username"] == "owner-caller"
        assert self.captured_writes("get", self.detail_url("other-caller"), **headers) == []
        assert set(self.answer.data) == PUBLIC_FIELDS
        assert self.captured_writes("get", self.list_url, **headers) == []
        assert self.usernames_in(self.answer) == ["owner-caller"]

    def test_a_read_a_staff_token_identifies_its_caller_by_writes_nothing(self):
        self.populate(self.other)
        headers = self.token_headers(self.staff)
        assert self.captured_writes(
            "get", self.list_url, {"username": "other-caller,owner-caller"}, **headers,
        ) == []
        assert self.usernames_in(self.answer) == ["other-caller", "owner-caller"]
        assert self.captured_writes("get", self.detail_url("other-caller"), **headers) == []
        assert self.answer.data["bio"] == "Teaches mathematics."

    def test_a_search_a_token_identifies_its_caller_by_writes_nothing(self):
        headers = self.token_headers(self.staff)
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("user_v2:email_search_list"),
                    {"emails": ["owner-caller@example.com"]},
                    format="json",
                    **headers,
                )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert [row["username"] for row in response.data["results"]] == ["owner-caller"]
        assert write_queries(captured.captured_queries) == []

    def test_reading_an_account_whose_name_was_checked_against_an_identity_document_writes_nothing(self):
        """The service holding a checked name is consulted by every read, and the read still writes nothing."""
        self.login(self.owner)
        with name_affirmation_answering("Checked Name"):
            assert self.captured_writes("get", self.own_url) == []
            assert self.answer.data["verified_name"] == "Checked Name"
            assert self.captured_writes("get", self.list_url) == []
            assert self.answer.data["results"][0]["verified_name"] == "Checked Name"

    def test_reading_an_account_the_name_service_holds_no_checked_name_for_writes_nothing(self):
        """The same read on an account the service has nothing for carries no name and writes nothing."""
        self.login(self.owner)
        with name_affirmation_answering(None) as service:
            service.return_value.get_verified_name.return_value = None
            assert self.captured_writes("get", self.own_url) == []
            assert self.answer.data["verified_name"] is None

    def test_reading_an_account_that_holds_every_kind_of_data_writes_nothing(self):
        """The read has a branch per kind of stored data, and none of them writes."""
        self.populate(self.owner)
        self.login(self.owner)
        assert self.captured_writes("get", self.own_url) == []
        assert self.answer.data["bio"] == "Teaches mathematics."
        assert self.answer.data["country"] == "US"
        assert self.answer.data["language_proficiencies"] == [{"code": "fr"}]
        assert self.answer.data["social_links"] == [
            {"platform": "twitter", "social_link": "https://www.twitter.com/example"},
        ]
        assert self.answer.data["profile_image"]["has_image"] is True
        assert self.captured_writes("get", self.list_url) == []
        assert self.answer.data["results"][0]["bio"] == "Teaches mathematics."

    def test_a_staff_caller_reading_an_account_that_holds_every_kind_of_data_writes_nothing(self):
        self.populate(self.other)
        self.login(self.staff)
        assert self.captured_writes("get", self.detail_url("other-caller")) == []
        assert self.answer.data["bio"] == "Teaches mathematics."
        assert self.answer.data["profile_image"]["has_image"] is True

    def test_reading_an_account_whose_site_collects_extra_profile_fields_writes_nothing(self):
        self.populate(self.owner)
        self.login(self.owner)
        with with_site_configuration_context(
            configuration={"extended_profile_fields": ["department"]},
        ):
            assert self.captured_writes("get", self.own_url) == []
        assert self.answer.data["extended_profile"] == [
            {"field_name": "department", "field_value": "physics"},
        ]

    def test_reading_an_account_that_has_no_profile_writes_nothing(self):
        profileless = UserFactory.create(username="unprofiled-caller", password=PASSWORD)
        profileless.profile.delete()
        self.login(self.staff)
        assert self.captured_writes("get", self.detail_url("unprofiled-caller")) == []
        assert self.answer.data["name"] is None
        self.login(self.owner)
        assert self.captured_writes("get", self.detail_url("unprofiled-caller")) == []
        assert set(self.answer.data) == PUBLIC_FIELDS

    def test_a_learner_reading_an_account_kept_private_writes_nothing(self):
        """The read that answers with the properties everybody may see writes nothing either."""
        self.populate(self.other)
        UserPreferenceFactory.create(user=self.other, key="account_privacy", value="private")
        self.login(self.owner)
        assert self.captured_writes("get", self.detail_url("other-caller")) == []
        assert set(self.answer.data) == PUBLIC_FIELDS
        assert self.answer.data["account_privacy"] == "private"

    def test_the_shared_preset_over_another_account_writes_nothing(self):
        self.populate(self.other)
        self.login(self.staff)
        assert self.captured_writes(
            "get", self.detail_url("other-caller"), {"view": "shared"},
        ) == []
        assert set(self.answer.data) == SHARED_FIELDS
        assert self.captured_writes(
            "get", self.list_url, {"username": "other-caller", "view": "shared"},
        ) == []
        assert set(self.answer.data["results"][0]) == SHARED_FIELDS

    def test_reading_the_collection_a_page_at_a_time_writes_nothing(self):
        self.populate(self.owner)
        self.login(self.staff)
        selection = {"username": "owner-caller,other-caller,staff-caller", "page_size": 2}
        assert self.captured_writes("get", self.list_url, selection) == []
        assert self.usernames_in(self.answer) == ["other-caller", "owner-caller"]
        assert self.captured_writes("get", self.list_url, {**selection, "page": 2}) == []
        assert self.usernames_in(self.answer) == ["staff-caller"]

    def test_asking_for_a_page_the_collection_does_not_have_writes_nothing(self):
        self.login(self.staff)
        assert self.captured_writes(
            "get", self.list_url, {"page": 7}, expect=status.HTTP_404_NOT_FOUND,
        ) == []

    def test_reading_the_collection_in_either_order_writes_nothing(self):
        self.populate(self.owner)
        self.login(self.staff)
        selection = {"username": "owner-caller,other-caller,staff-caller"}
        assert self.captured_writes(
            "get", self.list_url, {**selection, "ordering": ACCOUNT_DEFAULT_SORT},
        ) == []
        assert self.usernames_in(self.answer) == ["other-caller", "owner-caller", "staff-caller"]
        assert self.captured_writes(
            "get", self.list_url, {**selection, "ordering": f"-{ACCOUNT_DEFAULT_SORT}"},
        ) == []
        assert self.usernames_in(self.answer) == ["staff-caller", "owner-caller", "other-caller"]

    def test_a_learner_reading_an_account_shared_with_everybody_writes_nothing(self):
        """The visibility a read consults is stored as a preference, which is read and not written."""
        self.other.profile.year_of_birth = 1985
        self.other.profile.save()
        UserPreferenceFactory.create(user=self.other, key="account_privacy", value="custom")
        UserPreferenceFactory.create(user=self.other, key="visibility.bio", value="all_users")
        self.login(self.owner)
        assert self.captured_writes("get", self.detail_url("other-caller")) == []
        self.other.preferences.filter(key="account_privacy").delete()
        assert self.captured_writes("get", self.detail_url("other-caller")) == []


@skip_unless_lms
class TokenProvisionedAccountTests(AccountApiV2TestCase):
    """What a token naming an account no row holds leaves behind."""

    #: The tables written the first time a token names an account that does
    #: not exist: the account itself, and the two rows the platform gives
    #: every new account through the receivers that run when one is created.
    PROVISIONED_TABLES = ["auth_user", "notifications_notificationpreference", "user_tours_usertour"]

    #: The body of the read most recently captured.
    answer = None

    def token_for_a_name_no_account_holds(self):
        """Return request headers carrying a token for a name no row holds."""
        user = UserFactory.create(username="token-only-caller", email="token-only@example.com")
        headers = self.token_headers(user)
        User.objects.filter(pk=user.pk).delete()
        return headers

    def tables_written(self, address, headers, expect=status.HTTP_200_OK):
        """Return the tables one read changed, and leave its body on ``self.answer``."""
        with CaptureQueriesContext(connection) as captured:
            with self.captureOnCommitCallbacks(execute=True):
                self.answer = self.client.get(address, **headers)
        assert self.answer.status_code == expect, self.answer.content
        return sorted({write_target(sql) for sql in write_queries(captured.captured_queries)})

    def test_the_first_read_a_token_for_an_unknown_name_makes_creates_that_account(self):
        """
        A token carrying a name no account holds signs that name up.

        The authentication layer the whole platform shares resolves a token by
        the name in it and creates the account when it finds none; the
        receivers that run on a new account then add the rows belonging with
        it. All of that happens before any handler of this API is reached, and
        it is declared here rather than left out of the captures, because it
        is the one way a read of these addresses is followed by new rows.
        """
        headers = self.token_for_a_name_no_account_holds()
        assert self.tables_written(self.own_url, headers) == self.PROVISIONED_TABLES
        assert self.answer.data["username"] == "token-only-caller"
        assert User.objects.filter(username="token-only-caller").count() == 1

    def test_a_later_read_the_same_token_makes_writes_nothing(self):
        """Once the account exists, the reads behind that token write nothing at all."""
        headers = self.token_for_a_name_no_account_holds()
        assert self.client.get(self.own_url, **headers).status_code == status.HTTP_200_OK
        assert self.tables_written(self.own_url, headers) == []
        assert self.answer.data["username"] == "token-only-caller"
        assert self.tables_written(self.list_url, headers) == []
        assert self.usernames_in(self.answer) == ["token-only-caller"]

    def test_the_earlier_address_answers_such_a_token_the_same_way(self):
        """Both versions sign the name up, because the layer in front of both does it."""
        headers = self.token_for_a_name_no_account_holds()
        assert self.tables_written("/api/user/v1/me", headers) == self.PROVISIONED_TABLES
        assert self.answer.data["username"] == "token-only-caller"


@skip_unless_lms
@override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=False)
class AccountReadsDoNotWriteTests(AccountReadsDoNotWriteMixin, AccountApiV2TestCase):
    """Reading an account on a site that collects no recovery address writes nothing."""


@skip_unless_lms
@override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=True)
class AccountReadsWithARecoveryAddressDoNotWriteTests(AccountReadsDoNotWriteMixin, AccountApiV2TestCase):
    """The same reads on a site that collects a recovery address, which is another row to read."""
