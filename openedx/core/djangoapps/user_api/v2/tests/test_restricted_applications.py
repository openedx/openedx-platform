"""Tests of the refusal every address of version 2 of the User API gives a restricted application's token."""

import json
from datetime import UTC, datetime
from unittest import mock

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.auth.jwt.tests.utils import generate_jwt
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import PendingNameChange, UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.profile_images.images import get_profile_image_storage
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_names
from openedx.core.djangoapps.user_api.models import UserOrgTag, UserPreference
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2 import urls as v2_urls
from openedx.core.djangoapps.user_api.v2.permissions import RestrictedApplicationGuardMixin
from openedx.core.djangoapps.user_api.v2.views import AccountPreferenceViewSet, EmailOptInViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
MERGE_PATCH = "application/merge-patch+json"
API_KEY = "restricted-token-test-api-key"
COURSE_KEY = "course-v1:edX+DemoX+Demo"

#: The middleware that adds the restricted-application refusal to the classes a
#: view declares, once that view has served a request.
JWT_SETTINGS_MIDDLEWARE = "edx_rest_framework_extensions.auth.jwt.middleware.EnsureJWTAuthSettingsMiddleware"

#: The classes the preference and email choice views declare, read when this
#: module is imported, which is before any request has been served and so before
#: the middleware has added anything to them.
DECLARED_PERMISSION_CLASSES = {
    view_class: view_class.permission_classes
    for view_class in (AccountPreferenceViewSet, EmailOptInViewSet)
}


def routed_views():
    """
    Yield the view class, the route name and the operations of every route of this version.

    The operations are the handler names a route dispatches to: the actions of
    a viewset, or the methods an ``APIView`` implements.
    """
    for pattern in v2_urls.urlpatterns:
        callback = pattern.callback
        view_class = getattr(callback, "cls", None) or callback.view_class
        if getattr(callback, "actions", None):
            operations = sorted(set(callback.actions.values()))
        else:
            operations = sorted(
                method for method in view_class.http_method_names
                if method not in ("options", "head") and hasattr(view_class, method)
            )
        yield view_class, pattern.name, callback.initkwargs, operations


@skip_unless_lms
class EveryOperationCarriesTheRefusalTests(SimpleTestCase):
    """
    Each operation resolves the restricted-application refusal among its own classes.

    These read the classes an operation resolves without a request reaching
    it, so the platform middleware, which adds the refusal only once a view
    has served a request, plays no part in what they see.
    """

    def test_every_routed_view_inherits_the_guard(self):
        unguarded = sorted(
            {view_class.__name__ for view_class, _, _, _ in routed_views()
             if not issubclass(view_class, RestrictedApplicationGuardMixin)}
        )
        assert unguarded == []

    def test_every_operation_resolves_the_refusal(self):
        missing = []
        for view_class, name, initkwargs, operations in routed_views():
            for operation in operations:
                view = view_class(**initkwargs)
                view.action = operation
                view.kwargs = {}
                resolved = view.get_permissions()
                if not any(isinstance(permission, NotJwtRestrictedApplication) for permission in resolved):
                    missing.append((name, operation))
        assert not missing, missing

    def test_the_name_change_confirmation_keeps_its_own_classes_and_gains_the_refusal(self):
        pattern = next(p for p in v2_urls.urlpatterns if p.name == "name_change_confirmation")
        view = pattern.callback.cls(**pattern.callback.initkwargs)
        view.action = "confirmations"
        assert [type(permission).__name__ for permission in view.get_permissions()] == [
            "IsAuthenticated", "IsActiveUser", "IsStaff", "NotJwtRestrictedApplication",
        ]


@skip_unless_lms
@ddt.ddt
@override_settings(EDX_API_KEY=API_KEY)
class RestrictedApplicationTokenTests(APITestCase):
    """
    A token issued to a restricted application is refused at every operation that reads a token.

    Each operation is called twice by the same staff account, once with a
    token issued to an ordinary application, which the operation answers, and
    once with the same token flagged as issued to a restricted application,
    which it refuses before changing anything. The directory addresses are
    sent the site's API key as well, since that is what admits a caller there,
    so the token is the only thing that can refuse the request.
    """

    def setUp(self):
        super().setUp()
        self.staff = UserFactory.create(
            username="token-staff", email="token-staff@example.com", password=PASSWORD,
            is_staff=True, is_superuser=True,
        )
        self.learner = UserFactory.create(
            username="token-learner", email="token-learner@example.com", password=PASSWORD,
        )
        UserPreferenceFactory.create(user=self.learner, key="pref-lang", value="en")
        PendingNameChange.objects.create(
            user=self.learner, new_name="Renamed Learner", rationale="asked for in a test",
        )
        self.addCleanup(self.remove_stored_images, self.staff.username)

    def remove_stored_images(self, username):
        """Take whatever this user has stored out of the image store."""
        storage = get_profile_image_storage()
        for name in get_profile_image_names(username).values():
            storage.delete(name)

    def token_headers(self, is_restricted):
        """Return headers carrying the staff account's token, flagged restricted or not."""
        return {"HTTP_AUTHORIZATION": f"JWT {generate_jwt(self.staff, is_restricted=is_restricted)}"}

    def prepare(self, operation):
        """Put in place what ``operation`` acts on beyond the fixtures every operation shares."""
        if operation == "profile_image_clear":
            UserProfile.objects.filter(user=self.staff).update(profile_image_uploaded_at=datetime.now(UTC))

    def call(self, operation, headers):
        """Send the request that exercises ``operation``."""
        staff, learner = self.staff.username, self.learner.username
        client = self.client
        directory = {**headers, "HTTP_X_EDX_API_KEY": API_KEY}
        if operation == "user_list":
            return client.get(reverse("user_v2:user_list"), **directory)
        if operation == "user_detail":
            return client.get(reverse("user_v2:user_detail", kwargs={"username": learner}), **directory)
        if operation == "user_preference_list":
            return client.get(reverse("user_v2:user_preference_list"), **directory)
        if operation == "user_preference_detail":
            return client.get(
                reverse("user_v2:user_preference_detail", kwargs={"username": learner, "key": "pref-lang"}),
                **directory,
            )
        if operation == "time_zone_list":
            return client.get(reverse("user_v2:time_zone_list"), **headers)
        if operation == "account_list":
            return client.get(reverse("user_v2:account_list"), **headers)
        if operation == "account_detail":
            return client.get(reverse("user_v2:account_detail", kwargs={"username": staff}), **headers)
        if operation == "account_partial_update":
            return client.patch(
                reverse("user_v2:account_detail", kwargs={"username": staff}),
                json.dumps({"bio": "Changed by a token"}), content_type=MERGE_PATCH, **headers,
            )
        if operation == "account_deactivation":
            return client.post(reverse("user_v2:account_deactivation", kwargs={"username": learner}), **headers)
        if operation == "email_search":
            return client.post(
                reverse("user_v2:email_search_list"), {"emails": [self.learner.email]}, format="json", **headers,
            )
        if operation == "name_change_request":
            return client.post(
                reverse("user_v2:name_change_list"), {"name": "Renamed Staff"}, format="json", **headers,
            )
        if operation == "name_change_confirmation":
            return client.post(
                reverse("user_v2:name_change_confirmation", kwargs={"username": learner}), **headers,
            )
        if operation == "verification_status":
            return client.get(
                reverse("user_v2:account_verification_status", kwargs={"username": learner}), **headers,
            )
        if operation == "verification_list":
            return client.get(
                reverse("user_v2:account_verification_list", kwargs={"username": learner}), **headers,
            )
        if operation == "profile_image_set":
            with make_image_file() as image_file:
                return client.post(
                    reverse("user_v2:account_profile_image", kwargs={"username": staff}),
                    {"file": image_file}, format="multipart", **headers,
                )
        if operation == "profile_image_clear":
            return client.delete(reverse("user_v2:account_profile_image", kwargs={"username": staff}), **headers)
        raise AssertionError(f"No request is defined for {operation}")

    def state(self):
        """Return what any of these operations could change."""
        staff = UserProfile.objects.get(user=self.staff)
        learner = User.objects.get(pk=self.learner.pk)
        return {
            "staff_bio": staff.bio,
            "staff_has_profile_image": staff.has_profile_image,
            "staff_pending_name": PendingNameChange.objects.filter(user=self.staff).count(),
            "learner_name": UserProfile.objects.get(user=self.learner).name,
            "learner_password_usable": learner.has_usable_password(),
        }

    OPERATIONS = {
        "user_list": status.HTTP_200_OK,
        "user_detail": status.HTTP_200_OK,
        "user_preference_list": status.HTTP_200_OK,
        "user_preference_detail": status.HTTP_200_OK,
        "time_zone_list": status.HTTP_200_OK,
        "account_list": status.HTTP_200_OK,
        "account_detail": status.HTTP_200_OK,
        "account_partial_update": status.HTTP_200_OK,
        "account_deactivation": status.HTTP_204_NO_CONTENT,
        "email_search": status.HTTP_200_OK,
        "name_change_request": status.HTTP_201_CREATED,
        "name_change_confirmation": status.HTTP_204_NO_CONTENT,
        "verification_status": status.HTTP_200_OK,
        "verification_list": status.HTTP_200_OK,
        "profile_image_set": status.HTTP_204_NO_CONTENT,
        "profile_image_clear": status.HTTP_204_NO_CONTENT,
    }

    @ddt.data(*OPERATIONS)
    def test_a_restricted_applications_token_is_refused(self, operation):
        self.prepare(operation)
        before = self.state()
        response = self.call(operation, self.token_headers(is_restricted=True))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")
        assert self.state() == before

    @ddt.data(*OPERATIONS.items())
    @ddt.unpack
    def test_the_same_token_from_an_ordinary_application_is_answered(self, operation, expected_status):
        self.prepare(operation)
        response = self.call(operation, self.token_headers(is_restricted=False))
        assert response.status_code == expected_status, response.content

    def test_a_restricted_token_leaves_a_requested_name_unconfirmed(self):
        self.call("name_change_confirmation", self.token_headers(is_restricted=True))
        assert UserProfile.objects.get(user=self.learner).name != "Renamed Learner"
        assert PendingNameChange.objects.filter(user=self.learner, new_name="Renamed Learner").exists()

    def test_an_ordinary_token_confirms_the_requested_name(self):
        response = self.call("name_change_confirmation", self.token_headers(is_restricted=False))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert UserProfile.objects.get(user=self.learner).name == "Renamed Learner"
        assert not PendingNameChange.objects.filter(user=self.learner).exists()


@skip_unless_lms
@ddt.ddt
@override_settings(MIDDLEWARE=[entry for entry in settings.MIDDLEWARE if entry != JWT_SETTINGS_MIDDLEWARE])
class PreferenceTokenRefusalTests(APITestCase):
    """
    The preference and email choice addresses refuse a restricted application's token themselves.

    The platform middleware adds the same refusal to the classes a view
    declares once that view has served a request. It is left out of these
    requests, and whatever it added to these views earlier in the run is set
    aside, so the refusal seen here is the one each operation resolves for
    itself. Each operation is called with the owner's token, or a staff
    member's for what staff may do with another account, once as issued to an
    ordinary application, which the operation answers, and once as issued to a
    restricted one, which it refuses before reading or changing anything.
    """

    def setUp(self):
        super().setUp()
        for view_class, declared in DECLARED_PERMISSION_CLASSES.items():
            declared_only = mock.patch.object(view_class, "permission_classes", declared)
            declared_only.start()
            self.addCleanup(declared_only.stop)
        self.owner = UserFactory.create(
            username="token-owner", email="token-owner@example.com", password=PASSWORD,
        )
        self.staff = UserFactory.create(
            username="token-reader", email="token-reader@example.com", password=PASSWORD, is_staff=True,
        )
        UserPreferenceFactory.create(user=self.owner, key="time_zone", value="Asia/Tokyo")

    def call(self, operation, is_restricted):
        """Send the request that exercises ``operation`` with a token flagged restricted or not."""
        caller = self.staff if operation.startswith("staff_") else self.owner
        headers = {"HTTP_AUTHORIZATION": f"JWT {generate_jwt(caller, is_restricted=is_restricted)}"}
        owner = self.owner.username
        document = reverse("user_v2:account_preference_list", kwargs={"username": owner})
        member = reverse("user_v2:account_preference_detail", kwargs={"username": owner, "key": "time_zone"})
        client = self.client
        requests = {
            "document_read": lambda: client.get(document, **headers),
            "document_description": lambda: client.options(document, **headers),
            "document_change": lambda: client.patch(
                document, json.dumps({"time_zone": "Europe/Paris"}), content_type=MERGE_PATCH, **headers,
            ),
            "preference_read": lambda: client.get(member, **headers),
            "preference_description": lambda: client.options(member, **headers),
            "preference_store": lambda: client.put(
                member, json.dumps({"value": "Europe/Paris"}), content_type="application/json", **headers,
            ),
            "preference_removal": lambda: client.delete(member, **headers),
            "staff_document_read": lambda: client.get(document, **headers),
            "staff_document_description": lambda: client.options(document, **headers),
            "staff_preference_read": lambda: client.get(member, **headers),
            "staff_preference_description": lambda: client.options(member, **headers),
            "email_opt_in": lambda: client.post(
                reverse("user_v2:email_opt_in_list"),
                {"course_key": COURSE_KEY, "email_opt_in": True},
                format="json",
                **headers,
            ),
        }
        return requests[operation]()

    def state(self):
        """Return what any of these operations could change."""
        return {
            "preferences": sorted(UserPreference.objects.values_list("user__username", "key", "value")),
            "email_choices": sorted(UserOrgTag.objects.values_list("user__username", "org", "value")),
        }

    OPERATIONS = {
        "document_read": status.HTTP_200_OK,
        "document_description": status.HTTP_200_OK,
        "document_change": status.HTTP_204_NO_CONTENT,
        "preference_read": status.HTTP_200_OK,
        "preference_description": status.HTTP_200_OK,
        "preference_store": status.HTTP_204_NO_CONTENT,
        "preference_removal": status.HTTP_204_NO_CONTENT,
        "staff_document_read": status.HTTP_200_OK,
        "staff_document_description": status.HTTP_200_OK,
        "staff_preference_read": status.HTTP_200_OK,
        "staff_preference_description": status.HTTP_200_OK,
        "email_opt_in": status.HTTP_204_NO_CONTENT,
    }

    @ddt.data(*OPERATIONS)
    def test_a_restricted_applications_token_is_refused(self, operation):
        before = self.state()
        response = self.call(operation, is_restricted=True)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")
        assert self.state() == before

    @ddt.data(*OPERATIONS.items())
    @ddt.unpack
    def test_the_same_token_from_an_ordinary_application_is_answered(self, operation, expected_status):
        response = self.call(operation, is_restricted=False)
        assert response.status_code == expected_status, response.content

    def test_the_reads_answer_the_ordinary_token_with_what_the_account_stores(self):
        """What the restricted token is refused is the stored preference itself."""
        assert self.call("document_read", is_restricted=False).json() == {"time_zone": "Asia/Tokyo"}
        assert self.call("staff_preference_read", is_restricted=False).json() == {
            "key": "time_zone", "value": "Asia/Tokyo",
        }
