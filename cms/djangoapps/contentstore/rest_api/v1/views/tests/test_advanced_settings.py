"""Tests for the v1 course advanced settings API."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import ddt
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.dispatch import Signal
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.roles import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_LIMITED_STAFF,
    COURSE_STAFF,
)
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.utils.encoders import JSONEncoder

from cms.djangoapps.contentstore.rest_api.v1.advanced_settings_service import (
    INVALID_SETTING_MESSAGE,
    SETTINGS_NOT_SAVED_MESSAGE,
    is_rule_message,
)
from cms.djangoapps.contentstore.rest_api.v1.views.advanced_settings import CourseAdvancedSettingsViewSet
from cms.djangoapps.contentstore.rest_api.v1.views.tests.helpers import (
    FIRST_LOOKUP_SIGNALS,
    FIRST_LOOKUP_WRITES,
    UNKNOWN_COURSE_KEY,
    VERSION_ONLY_KEY,
    api_client,
    assert_matches_published_schema,
    assert_refused,
    changed_paths,
    measure,
    published_schema,
    record_effects,
    session_client,
    undeclared_paths,
)
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.views.permissions import (
    CanManageAdvancedSettings,
    CanViewAdvancedSettings,
    IsExistingCourse,
)
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from cms.lib.spectacular import LEGACY_MIGRATED_PATH_PREFIXES, SUPERSEDED_PATHS
from common.djangoapps.student.auth import check_course_advanced_settings_access, has_studio_advanced_settings_access
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import InvalidProctoringProvider
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, LibraryFactory
from xmodule.tabs import InvalidTabsException

URL_NAME = "authoring_v1:course_advanced_settings"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v0:course_advanced_settings"
LEGACY_VIEW = ("cms.djangoapps.contentstore.rest_api.v0.views.advanced_settings", "AdvancedCourseSettingsView")
SERVICE_LOGGER = "cms.djangoapps.contentstore.rest_api.v1.advanced_settings_service"
FULL = {"view": "full"}
LEGACY_DEFAULT = {"fetch_all": "0"}
# An integer setting authors edit, so a non-numeric value is refused by its field.
INTEGER_SETTING = "max_student_enrollments_allowed"
SETTING_MEMBERS = {"value", "display_name", "help", "deprecated", "hide_on_enabled_publisher"}
ACTIONS = ("read", "update")


def url(course_key):
    """Return the address of the advanced settings of ``course_key``."""
    return reverse(URL_NAME, kwargs={"course_key": str(course_key)})


def legacy_url(course_key):
    """Return the deprecated advanced settings address of ``course_key``."""
    return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key)})


def stored_course(course_key):
    """The stored course block of ``course_key`` (a key or its string form), or None when there is none."""
    return modulestore().get_course(CourseKey.from_string(str(course_key)))


def stored_display_name(course_key):
    """The display name stored for the course."""
    return stored_course(course_key).display_name


def editable_settings(course_key):
    """The settings authors edit, as the settings model returns them for the stored course."""
    return CourseMetadata.fetch(stored_course(course_key))


def every_setting(course_key):
    """Every setting of the stored course, as the settings model returns them."""
    return CourseMetadata.fetch_all(stored_course(course_key))


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

class _AccessMixin:
    """
    Checks a caller's status on a read or an update against the expected one,
    and that v0 decides the same for the same caller.
    """

    def assert_read(self, user, expected, course_key=None):
        """Assert the status of a read by ``user``, its body or envelope, and agreement with v0."""
        course_key = course_key or self.course.id
        response = api_client(user).get(url(course_key))
        legacy = api_client(user).get(legacy_url(course_key), LEGACY_DEFAULT)
        assert response.status_code == expected, response.content
        if expected == 200:
            assert list(response.json()) == list(editable_settings(course_key))
        else:
            assert_refused(response, expected)
        assert legacy.status_code == expected, f"v0 decided {legacy.status_code} for the same caller"

    def assert_update(self, user, expected, course_key=None):
        """Assert the status of an update by ``user``, what it stored, and agreement with v0."""
        course_key = course_key or self.course.id
        course = stored_course(course_key)
        before = course.display_name if course is not None else None
        new_name = f"Renamed {uuid.uuid4().hex[:8]}"
        response = api_client(user).patch(url(course_key), {"display_name": {"value": new_name}}, format="json")
        assert response.status_code == expected, response.content
        if expected == 200:
            assert response.json()["display_name"]["value"] == new_name
            assert stored_display_name(course_key) == new_name
        else:
            assert_refused(response, expected)
            if course is not None:
                assert stored_display_name(course_key) == before
        legacy_name = f"Legacy {uuid.uuid4().hex[:8]}"
        legacy = api_client(user).patch(legacy_url(course_key), {"display_name": {"value": legacy_name}}, format="json")
        assert legacy.status_code == expected, f"v0 decided {legacy.status_code} for the same caller"

    def assert_access(self, user, action, expected, course_key=None):
        """Assert ``expected`` for ``action`` (``read`` or ``update``) by ``user``."""
        getattr(self, f"assert_{action}")(user, expected, course_key)


@ddt.ddt
class AdvancedSettingsAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may read and change the settings when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    @ddt.data(*ACTIONS)
    def test_anonymous_caller_is_refused_with_401(self, action):
        self.assert_access(None, action, 401)

    @ddt.data(*ACTIONS)
    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self, action):
        self.assert_access(None, action, 401, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*ACTIONS)
    def test_user_without_a_role_is_refused_with_403(self, action):
        self.assert_access(UserFactory(), action, 403)

    @ddt.data(*ACTIONS)
    def test_unknown_course_is_404_before_permission_is_checked(self, action):
        self.assert_access(UserFactory(), action, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*ACTIONS)
    def test_limited_staff_is_refused(self, action):
        self.assert_access(self._user_with(CourseLimitedStaffRole(self.course.id)), action, 403)

    @ddt.data(*ACTIONS)
    def test_staff_and_instructors_of_another_course_are_refused(self, action):
        self.assert_access(self._user_with(CourseStaffRole(self.other_course.id)), action, 403)
        self.assert_access(self._user_with(CourseInstructorRole(self.other_course.id)), action, 403)

    @ddt.data(*ACTIONS)
    def test_course_staff_is_allowed(self, action):
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), action, 200)

    @ddt.data(*ACTIONS)
    def test_course_instructor_is_allowed(self, action):
        self.assert_access(self._user_with(CourseInstructorRole(self.course.id)), action, 200)

    @ddt.data(*ACTIONS)
    def test_org_staff_and_org_instructor_are_allowed(self, action):
        self.assert_access(self._user_with(OrgStaffRole(self.course.id.org)), action, 200)
        self.assert_access(self._user_with(OrgInstructorRole(self.course.id.org)), action, 200)

    @ddt.data(*ACTIONS)
    def test_global_staff_is_allowed(self, action):
        self.assert_access(UserFactory(is_staff=True), action, 200)

    @ddt.data(*ACTIONS)
    def test_a_superuser_is_allowed(self, action):
        """Saving a superuser also makes it global staff, so it is allowed, as on v0."""
        self.assert_access(UserFactory(is_superuser=True, is_staff=False), action, 200)

    def test_a_version_only_course_key_is_404(self):
        """The key parses but names no course run; v0 has no address that takes it."""
        user = UserFactory(is_staff=True)
        assert_refused(api_client(user).get(url(VERSION_ONLY_KEY)), 404)
        assert_refused(
            api_client(user).patch(url(VERSION_ONLY_KEY), {"display_name": {"value": "x"}}, format="json"), 404,
        )

    def test_a_library_is_not_a_course(self):
        library_key = LibraryFactory.create().location.library_key
        user = UserFactory(is_staff=True)
        assert_refused(api_client(user).get(url(library_key)), 404)
        assert_refused(api_client(user).patch(url(library_key), {}, format="json"), 404)


@ddt.ddt
class AdvancedSettingsAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    Who may read and change the settings when policy-based authoring
    permissions are on: viewing needs the permission to view advanced
    settings, changing them the permission to manage them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def _user_with(self, role, course_key=None):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, course_key or self.course.id)
        return user

    @ddt.data(*ACTIONS)
    def test_anonymous_caller_is_refused_with_401(self, action):
        self.assert_access(None, action, 401)

    @ddt.data(*ACTIONS)
    def test_user_without_a_role_is_refused_with_403(self, action):
        self.assert_access(self.unauthorized_user, action, 403)

    @ddt.data(*ACTIONS)
    def test_unknown_course_is_404_before_permission_is_checked(self, action):
        self.assert_access(self.unauthorized_user, action, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(COURSE_AUDITOR.external_key, COURSE_EDITOR.external_key)
    def test_roles_that_may_only_view_may_read_but_not_update(self, role):
        user = self._user_with(role)
        self.assert_read(user, 200)
        self.assert_update(user, 403)

    @ddt.data(*[(action, role) for action in ACTIONS for role in (
        COURSE_STAFF.external_key, COURSE_ADMIN.external_key,
    )])
    @ddt.unpack
    def test_roles_that_may_manage_may_read_and_update(self, action, role):
        self.assert_access(self._user_with(role), action, 200)

    @ddt.data(*[(action, role) for action in ACTIONS for role in (
        COURSE_LIMITED_STAFF.external_key, COURSE_DATA_RESEARCHER.external_key, COURSE_BETA_TESTER.external_key,
    )])
    @ddt.unpack
    def test_other_roles_are_refused(self, action, role):
        self.assert_access(self._user_with(role), action, 403)

    @ddt.data(*ACTIONS)
    def test_a_role_on_another_course_is_refused(self, action):
        user = self._user_with(COURSE_ADMIN.external_key, self.other_course.id)
        self.assert_access(user, action, 200, course_key=self.other_course.id)
        self.assert_access(user, action, 403)

    @ddt.data(*ACTIONS)
    def test_legacy_course_staff_without_a_policy_role_is_refused(self, action):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_access(user, action, 403)

    @ddt.data(*ACTIONS)
    def test_staff_and_superuser_are_allowed_without_a_role(self, action):
        self.assert_access(self.staff_user, action, 200)
        self.assert_access(self.super_user, action, 200)

    def test_an_inactive_session_with_a_role_is_accepted(self):
        """The policy does not look at whether the account is activated, on v1 as on v0."""
        user = self._user_with(COURSE_AUDITOR.external_key)
        client = session_client(user)
        user.is_active = False
        user.save()
        assert client.get(url(self.course.id)).status_code == 200
        assert client.get(legacy_url(self.course.id)).status_code == 200


@override_settings(DISABLE_ADVANCED_SETTINGS=True)
class AdvancedSettingsSiteRestrictionLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """
    With advanced settings restricted to staff site-wide and policy-based
    permissions off, course staff still read and change them here, as on v0.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.course_staff = UserFactory()
        CourseStaffRole(self.course.id).add_users(self.course_staff)

    def test_the_site_restriction_is_on(self):
        """The Studio advanced settings page would refuse this caller."""
        assert has_studio_advanced_settings_access(self.course_staff) is False
        assert check_course_advanced_settings_access(self.course_staff, self.course.id, "feature_restricted") is False

    def test_course_staff_may_still_read_and_update(self):
        self.assert_read(self.course_staff, 200)
        self.assert_update(self.course_staff, 200)

    def test_a_caller_without_a_role_is_still_refused(self):
        self.assert_read(UserFactory(), 403)
        self.assert_update(UserFactory(), 403)


@override_settings(DISABLE_ADVANCED_SETTINGS=True)
class AdvancedSettingsSiteRestrictionAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    With advanced settings restricted to staff site-wide and policy-based
    permissions on, the policy alone still decides here, as on v0.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, self.course.id)
        return user

    def test_the_site_restriction_is_on(self):
        """The restricted check that Studio pages use would refuse a course staff role."""
        user = self._user_with(COURSE_STAFF.external_key)
        assert check_course_advanced_settings_access(user, self.course.id, "feature_restricted") is False

    def test_a_role_that_may_manage_may_still_read_and_update(self):
        user = self._user_with(COURSE_STAFF.external_key)
        self.assert_read(user, 200)
        self.assert_update(user, 200)

    def test_a_role_that_may_only_view_still_may_not_update(self):
        user = self._user_with(COURSE_EDITOR.external_key)
        self.assert_read(user, 200)
        self.assert_update(user, 403)


class AdvancedSettingsPermissionWiringTest(SimpleTestCase):
    """Which permission each action checks, and which access each settings permission asks for."""

    def test_each_action_checks_the_course_then_its_own_permission(self):
        view = CourseAdvancedSettingsViewSet()
        for action, expected in (
            ("retrieve", [IsAuthenticated, IsExistingCourse, CanViewAdvancedSettings]),
            ("partial_update", [IsAuthenticated, IsExistingCourse, CanManageAdvancedSettings]),
            (None, [IsAuthenticated, IsExistingCourse]),
            ("metadata", [IsAuthenticated, IsExistingCourse]),
        ):
            view.action = action
            # The JWT middleware appends NotJwtRestrictedApplication to views that take JWTs.
            declared = [
                type(permission) for permission in view.get_permissions()
                if not isinstance(permission, NotJwtRestrictedApplication)
            ]
            assert declared == expected, action

    def test_an_action_without_a_declared_permission_is_refused(self):
        view = CourseAdvancedSettingsViewSet()
        for action in ("destroy", "update", "list", "create"):
            view.action = action
            with pytest.raises(ImproperlyConfigured):
                view.get_permissions()

    def test_a_routed_action_without_a_declared_permission_is_never_served(self):
        """A request reaching such an action is answered with a server error, not the handler's body."""

        class Unchecked(CourseAdvancedSettingsViewSet):
            permission_classes = CourseAdvancedSettingsViewSet.permission_classes

            def destroy(self, request, course_key):  # pylint: disable=unused-argument
                raise AssertionError("the handler ran without a permission check")

        view = Unchecked.as_view({"delete": "destroy"})
        request = APIRequestFactory().delete(url(COURSE_KEY))
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_active=True))
        response = view(request, course_key=CourseKey.from_string(COURSE_KEY))
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")

    def test_the_settings_permissions_ask_for_read_and_write_access(self):
        course_key = "course-v1:edX+Wiring+Run"
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={"course_key": course_key})
        target = "cms.djangoapps.contentstore.views.permissions.check_course_advanced_settings_access"
        for permission, access_type in ((CanViewAdvancedSettings, "read"), (CanManageAdvancedSettings, "write")):
            for decision in (True, False):
                with patch(target, return_value=decision) as check:
                    assert permission().has_permission(request, view) is decision
                check.assert_called_once_with(request.user, course_key, access_type=access_type)

    def test_the_settings_permissions_refuse_a_view_without_a_course(self):
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={})
        for permission in (CanViewAdvancedSettings, CanManageAdvancedSettings, IsExistingCourse):
            assert permission().has_permission(request, view) is False


class AdvancedSettingsAuthenticationTest(SharedModuleStoreTestCase):
    """How callers authenticate, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _course_staff(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def test_a_jwt_caller_may_read_and_update(self):
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self._course_staff())}"}
        assert APIClient().get(url(self.course.id), **header).status_code == 200
        response = APIClient().patch(
            url(self.course.id), {"display_name": {"value": "By JWT"}}, format="json", **header,
        )
        assert response.status_code == 200
        assert stored_display_name(self.course.id) == "By JWT"

    def test_a_session_caller_may_read_and_update(self):
        client = session_client(self._course_staff())
        assert client.get(url(self.course.id)).status_code == 200
        response = client.patch(url(self.course.id), {"display_name": {"value": "By session"}}, format="json")
        assert response.status_code == 200
        assert stored_display_name(self.course.id) == "By session"

    def test_an_inactive_session_is_authenticated(self):
        """
        A session whose account is not activated is accepted, as on v0. Global
        staff then pass; course staff are refused (403, not 401) because a
        course role does not count while the account is inactive.
        """
        for user, expected in ((UserFactory(is_staff=True), 200), (self._course_staff(), 403)):
            client = session_client(user)
            user.is_active = False
            user.save()
            response = client.get(url(self.course.id))
            assert response.status_code == expected, response.content
            if expected == 403:
                assert_refused(response, 403)
            assert client.get(legacy_url(self.course.id)).status_code == expected

    def test_a_bearer_token_is_refused(self):
        """Bearer tokens are not accepted here, though v0 takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert_refused(APIClient().get(url(self.course.id), **header), 401)
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@ddt.ddt
class AdvancedSettingsReadTest(SharedModuleStoreTestCase):
    """What a read returns, by view and by named settings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        reader = UserFactory()
        CourseStaffRole(self.course.id).add_users(reader)
        self.client = api_client(reader)

    def get(self, **params):
        response = self.client.get(url(self.course.id), params)
        assert response.status_code == 200, response.content
        return response.json()

    def test_the_default_view_is_the_settings_authors_edit_in_course_order(self):
        body = self.get()
        assert list(body) == list(editable_settings(self.course.id))
        assert "display_name" in body
        assert "tabs" not in body

    def test_the_full_view_is_every_setting_in_course_order(self):
        body = self.get(**FULL)
        assert list(body) == list(every_setting(self.course.id))
        assert "tabs" in body

    def test_the_default_view_leaves_out_exactly_the_excluded_settings(self):
        default, full = self.get(), self.get(**FULL)
        excluded = set(CourseMetadata.get_exclude_list_of_fields(self.course.id))
        assert set(full) - set(default) == set(full) & excluded
        assert set(full) - set(default) != set()

    @ddt.data({}, FULL)
    def test_every_setting_carries_the_five_members(self, params):
        body = self.get(**params)
        assert {name: set(setting) for name, setting in body.items()} == {name: SETTING_MEMBERS for name in body}

    @ddt.data({}, FULL)
    def test_the_body_matches_the_published_schema(self, params):
        assert_matches_published_schema(self.get(**params), "CourseAdvancedSettings.v1")

    def test_the_values_are_those_the_settings_model_reads(self):
        body = self.get(**FULL)
        assert {name: setting["value"] for name, setting in body.items()} == {
            name: json.loads(json.dumps(setting["value"], cls=JSONEncoder))
            for name, setting in every_setting(self.course.id).items()
        }

    def test_fields_narrows_to_the_named_settings_in_course_order(self):
        named = {"days_early_for_beta", "display_name"}
        body = self.get(fields="days_early_for_beta,display_name")
        assert list(body) == [name for name in editable_settings(self.course.id) if name in named]

    def test_fields_ignores_names_outside_the_view(self):
        assert self.get(fields="tabs,no_such_setting") == {}
        assert list(self.get(fields="tabs,no_such_setting", **FULL)) == ["tabs"]

    def test_fields_ignores_spaces_around_names(self):
        assert set(self.get(fields=" display_name , days_early_for_beta ")) == {"display_name", "days_early_for_beta"}

    def test_empty_fields_returns_the_whole_view(self):
        assert list(self.get(fields="")) == list(editable_settings(self.course.id))

    def test_the_minimal_view_is_the_default(self):
        assert self.get(view="minimal") == self.get()
        assert list(self.get(view="minimal")) == list(editable_settings(self.course.id))

    def test_an_unknown_view_is_refused(self):
        response = self.client.get(url(self.course.id), {"view": "default"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"view": ['"default" is not a valid choice.']}

    def test_the_v0_query_parameters_are_ignored(self):
        """fetch_all and filter_fields have no meaning here; view and fields replace them."""
        for params in ({}, FULL):
            expected = self.get(**params)
            assert self.get(**params, fetch_all="0", filter_fields="display_name") == expected
            assert self.get(**params, fetch_all="1") == expected

    def test_head_answers_without_a_body(self):
        for params in ({}, FULL):
            response = self.client.head(url(self.course.id), params)
            assert response.status_code == 200
            assert response.content == b""


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------

class AdvancedSettingsUpdateTest(ModuleStoreTestCase):
    """What an update stores, returns and refuses."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name="Original")
        writer = UserFactory()
        CourseStaffRole(self.course.id).add_users(writer)
        self.writer = writer
        self.client = api_client(writer)

    def patch(self, body):
        return self.client.patch(url(self.course.id), body, format="json")

    def test_named_settings_are_stored_and_the_editable_settings_returned(self):
        response = self.patch({"display_name": {"value": "Renamed"}, "days_early_for_beta": {"value": 3.0}})
        assert response.status_code == 200, response.content
        course = modulestore().get_course(self.course.id)
        assert (course.display_name, course.days_early_for_beta) == ("Renamed", 3.0)
        body = response.json()
        assert list(body) == list(editable_settings(self.course.id))
        assert body["display_name"]["value"] == "Renamed"
        assert body["days_early_for_beta"]["value"] == 3.0
        assert_matches_published_schema(body, "CourseAdvancedSettings.v1")

    def test_members_other_than_value_are_ignored(self):
        response = self.patch({"display_name": {"value": "Renamed", "display_name": "Not stored", "deprecated": True}})
        assert response.status_code == 200, response.content
        setting = response.json()["display_name"]
        assert (setting["value"], setting["deprecated"]) == ("Renamed", False)
        assert setting["display_name"] == str(editable_settings(self.course.id)["display_name"]["display_name"])

    def test_unknown_settings_and_settings_authors_do_not_edit_are_ignored(self):
        start = modulestore().get_course(self.course.id).start
        response = self.patch({"no_such_setting": {"value": 1}, "start": {"value": "2030-01-01T00:00:00Z"}})
        assert response.status_code == 200, response.content
        assert "no_such_setting" not in response.json()
        assert "start" not in response.json()
        assert modulestore().get_course(self.course.id).start == start

    def test_a_null_value_is_stored(self):
        self.patch({"days_early_for_beta": {"value": 3.0}})
        response = self.patch({"days_early_for_beta": {"value": None}})
        assert response.status_code == 200, response.content
        assert modulestore().get_course(self.course.id).days_early_for_beta is None

    def test_a_refused_value_is_reported_with_a_fixed_message_and_nothing_is_stored(self):
        body = {INTEGER_SETTING: {"value": "lots"}, "display_name": {"value": "Not stored"}}
        with self.assertLogs(SERVICE_LOGGER, "WARNING") as logs:
            response = self.patch(body)
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {INTEGER_SETTING: [INVALID_SETTING_MESSAGE]}
        assert stored_display_name(self.course.id) == "Original"
        legacy = self.client.patch(legacy_url(self.course.id), body, format="json")
        assert legacy.status_code == 400
        (validator_text,) = [
            entry["message"] for entry in legacy.json()["developer_message"] if entry["key"] == INTEGER_SETTING
        ]
        assert validator_text != INVALID_SETTING_MESSAGE
        assert validator_text not in response.content.decode()
        (record,) = logs.output
        assert validator_text in record
        assert INTEGER_SETTING in record
        assert str(self.course.id) in record

    def test_several_refused_settings_are_each_named(self):
        body = {INTEGER_SETTING: {"value": "lots"}, "days_early_for_beta": {"value": "soon"}}
        with self.assertLogs(SERVICE_LOGGER, "WARNING") as logs:
            response = self.patch(body)
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {
            INTEGER_SETTING: [INVALID_SETTING_MESSAGE], "days_early_for_beta": [INVALID_SETTING_MESSAGE],
        }
        assert len(logs.output) == 2

    def test_a_body_that_is_not_an_object_is_refused(self):
        envelope = assert_error_envelope(self.patch([1, 2]), expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]}

    def test_each_entry_must_be_an_object_with_a_value(self):
        response = self.patch({"display_name": "Renamed", "days_early_for_beta": {}})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {
            "display_name": ["Invalid data. Expected a dictionary, but got str."],
            "days_early_for_beta": ["This field is required."],
        }
        assert stored_display_name(self.course.id) == "Original"

    def test_an_entry_that_is_not_an_object_is_refused_by_v0_too(self):
        body = {"display_name": "Renamed"}
        assert self.patch(body).status_code == 400
        assert self.client.patch(legacy_url(self.course.id), body, format="json").status_code == 400
        assert stored_display_name(self.course.id) == "Original"

    def test_a_malformed_entry_of_a_setting_authors_do_not_edit_is_ignored(self):
        start = stored_course(self.course.id).start
        response = self.patch({"tabs": "not tabs", "start": {}, "display_name": {"value": "Renamed"}})
        assert response.status_code == 200, response.content
        assert stored_display_name(self.course.id) == "Renamed"
        assert stored_course(self.course.id).start == start

    def test_a_failure_saving_the_tabs_is_reported_without_its_text(self):
        target = "cms.djangoapps.contentstore.views.course._refresh_course_tabs"
        with (
            patch(target, side_effect=InvalidTabsException("tab internals")),
            self.assertLogs(SERVICE_LOGGER, "WARNING"),
        ):
            response = self.patch({"display_name": {"value": "Not stored"}})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"non_field_errors": [SETTINGS_NOT_SAVED_MESSAGE]}
        assert b"tab internals" not in response.content
        assert b"Tabs Exception" not in response.content
        assert stored_display_name(self.course.id) == "Original"
        with patch(target, side_effect=InvalidTabsException("tab internals")):
            legacy = self.client.patch(legacy_url(self.course.id), {"display_name": {"value": "x"}}, format="json")
        assert legacy.status_code == 400

    def test_an_unexpected_validation_failure_is_reported_without_its_text(self):
        target = "cms.djangoapps.models.settings.course_metadata.CourseMetadata.validate_and_update_from_json"
        with patch(target, side_effect=TypeError("validator internals")), self.assertLogs(SERVICE_LOGGER, "WARNING"):
            response = self.patch({"display_name": {"value": "Not stored"}})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"non_field_errors": [SETTINGS_NOT_SAVED_MESSAGE]}
        assert b"validator internals" not in response.content
        assert stored_display_name(self.course.id) == "Original"
        with patch(target, side_effect=TypeError("validator internals")):
            legacy = self.client.patch(legacy_url(self.course.id), {"display_name": {"value": "x"}}, format="json")
        assert legacy.status_code == 400
        assert b"validator internals" in legacy.content

    def test_each_message_is_listed_once_per_setting(self):
        """Several unreadable values of one setting give one fixed message; a repeated rule message is listed once."""
        target = "cms.djangoapps.models.settings.course_metadata.CourseMetadata.validate_and_update_from_json"
        refusals = [
            {"key": INTEGER_SETTING, "message": "first internals"},
            {"key": INTEGER_SETTING, "message": "second internals"},
            {"key": "teams_configuration", "message": "duplicate ids: topic"},
            {"key": "teams_configuration", "message": "duplicate ids: topic"},
        ]
        with (
            patch(target, return_value=(False, refusals, None)),
            self.assertLogs(SERVICE_LOGGER, "WARNING") as logs,
        ):
            response = self.patch({"display_name": {"value": "Not stored"}})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {
            INTEGER_SETTING: [INVALID_SETTING_MESSAGE], "teams_configuration": ["duplicate ids: topic"],
        }
        assert b"internals" not in response.content
        assert len(logs.output) == 2


TEAM_SET = {"id": "topic", "name": "Topic", "description": "A topic"}
RETIRED_PROVIDER = "retired_provider"
ESCALATING_PROVIDER = "escalating_provider"


def team_settings(**value):
    """A body changing the teams configuration to ``value``."""
    return {"teams_configuration": {"value": value}}


@ddt.ddt
@override_settings(
    ENABLE_PROCTORED_EXAMS=True,
    PROCTORING_BACKENDS={
        "DEFAULT": "valid_provider",
        "valid_provider": {},
        ESCALATING_PROVIDER: {"requires_escalation_email": True},
    },
)
class AdvancedSettingsRuleMessagesTest(ModuleStoreTestCase):
    """
    A value that breaks one of the course's rules is reported with the rule's
    own message, as v0 reports it. The text of an exception raised while
    converting a value is not.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name="Original", start=datetime.now(UTC) + timedelta(days=30))
        writer = UserFactory()
        CourseStaffRole(self.course.id).add_users(writer)
        self.client = api_client(writer)

    def store_directly(self, values):
        """Store ``values`` on the course directly, without the rules an update checks."""
        CourseMetadata.update_from_dict(values, stored_course(self.course.id), self.user)

    def refusals(self, body):
        """Return the messages v1 and v0 report for each refused setting of ``body``, by setting name."""
        new = self.client.patch(url(self.course.id), body, format="json")
        legacy = self.client.patch(legacy_url(self.course.id), body, format="json")
        envelope = assert_error_envelope(new, expected_status=400, expected_type_slug="validation")
        assert legacy.status_code == 400, legacy.content
        legacy_messages = {}
        for entry in legacy.json()["developer_message"]:
            legacy_messages.setdefault(entry["key"], []).append(entry["message"])
        assert stored_display_name(self.course.id) == "Original"
        return envelope["errors"], legacy_messages

    @ddt.data(
        ({}, team_settings(max_team_size=0, topics=[]), "teams_configuration",
         "max_team_size must be greater than zero"),
        ({}, team_settings(max_team_size=501, topics=[]), "teams_configuration",
         "max_team_size cannot be greater than 500"),
        ({}, team_settings(topics=[TEAM_SET, TEAM_SET]), "teams_configuration", "duplicate ids: topic"),
        (
            {},
            team_settings(topics=[{**TEAM_SET, "name": "", "type": "secret", "max_team_size": 501, "colour": "red"}]),
            "teams_configuration",
            "type secret is invalid,max_team_size cannot be greater than 500,name attribute must not be empty,"
            "extra keys: colour",
        ),
        (
            {"enable_proctored_exams": True, "proctoring_provider": RETIRED_PROVIDER},
            {},
            "proctoring_provider",
            f"The proctoring provider configured for this course, '{RETIRED_PROVIDER}', is not valid.",
        ),
        (
            {"enable_proctored_exams": True},
            {"proctoring_provider": {"value": ESCALATING_PROVIDER}},
            "proctoring_provider",
            f"Provider '{ESCALATING_PROVIDER}' requires an exam escalation contact.",
        ),
        (
            {"enable_proctored_exams": True, "proctoring_provider": ESCALATING_PROVIDER},
            {"proctoring_escalation_email": {"value": ""}},
            "proctoring_escalation_email",
            f"Provider '{ESCALATING_PROVIDER}' requires an exam escalation contact.",
        ),
    )
    @ddt.unpack
    def test_a_rule_message_is_reported_as_v0_reports_it(self, stored, body, setting, message):
        """The rule's message reaches v1 unchanged, under the setting v0 names."""
        self.store_directly(stored)
        new, legacy = self.refusals({**body, "display_name": {"value": "Not stored"}})
        assert new == {setting: [message]}
        assert legacy == {setting: [message]}

    def test_an_unknown_provider_is_reported_as_v0_reports_it(self):
        """The sentence naming the providers the course may use reaches v1 unchanged."""
        body = {"proctoring_provider": {"value": RETIRED_PROVIDER}, "display_name": {"value": "Not stored"}}
        new, legacy = self.refusals(body)
        message = (
            f"The selected proctoring provider, {RETIRED_PROVIDER}, is not a valid provider. "
            f"Please select from one of {['escalating_provider', 'valid_provider']}."
        )
        assert new == {"proctoring_provider": [message]}
        assert legacy == {"proctoring_provider": [message]}

    def test_rule_messages_and_exception_text_of_one_setting_are_told_apart(self):
        """Each of a setting's messages is judged on its own, in the order v0 lists them."""
        self.store_directly({"enable_proctored_exams": True})
        body = {"proctoring_provider": {"value": 5}, "display_name": {"value": "Not stored"}}
        with self.assertLogs(SERVICE_LOGGER, "WARNING") as logs:
            new, legacy = self.refusals(body)
        rule_message = "The proctoring provider configured for this course, '5', is not valid."
        assert new == {"proctoring_provider": [INVALID_SETTING_MESSAGE, rule_message]}
        exception_text, *rest = legacy["proctoring_provider"]
        assert rest == [rule_message]
        assert exception_text.startswith("Value stored in a String must be None or a string")
        assert exception_text not in json.dumps(new)
        (record,) = logs.output
        assert exception_text in record


@ddt.ddt
class AdvancedSettingsRuleMessageCheckTest(SimpleTestCase):
    """Which explanations of a refused setting are the course rules' own messages."""

    @ddt.data(
        {"max_team_size": 0, "topics": [TEAM_SET, TEAM_SET]},
        {"max_team_size": 501, "topics": []},
        {"topics": [{**TEAM_SET, "id": "a,b"}, {**TEAM_SET, "id": "a,b"}]},
        {"topics": [{**TEAM_SET, "type": "a, b is invalid"}]},
        {"topics": [{"id": "", "max_team_size": -1, "x,y": 1}]},
        {"team_sets": [{**TEAM_SET, "description": "", "type": "two\nlines"}]},
    )
    def test_every_message_of_the_teams_rules_is_a_rule_message(self, value):
        """Each message the teams rules write for these configurations is recognised."""
        errors = CourseMetadata.validate_team_settings({"teams_configuration": {"value": value}})
        assert errors != []
        assert [error["message"] for error in errors if not is_rule_message(error["key"], error["message"])] == []

    @ddt.data([], ["lti_external"], ["escalating_provider", "valid_provider"])
    def test_the_unknown_provider_sentence_is_a_rule_message(self, available_providers):
        """The sentence an unknown provider raises, naming the providers the course may use, is recognised."""
        sentence = str(InvalidProctoringProvider("retired, or renamed", available_providers))
        assert is_rule_message("proctoring_provider", sentence)
        assert not is_rule_message("proctoring_escalation_email", sentence)
        assert not is_rule_message("proctoring_provider", f"{sentence} Traceback (most recent call last)")

    @ddt.data(
        (INTEGER_SETTING, "invalid literal for int() with base 10: 'lots'"),
        ("proctoring_provider", "Value stored in a String must be None or a string, found <class 'int'>"),
        ("teams_configuration", "Team-set 'x' does not exist."),
        ("teams_configuration", ""),
        ("teams_configuration", "max_team_size must be greater than zero, Traceback (most recent call last)"),
        ("teams_configuration", "type foo is invalid,id attribute must not be empty,Traceback"),
        ("display_name", "max_team_size must be greater than zero"),
        ("proctoring_escalation_email", "The proctoring provider configured for this course, 'x', is not valid."),
    )
    @ddt.unpack
    def test_other_text_is_not_a_rule_message(self, setting, text):
        """Exception text, a rule message with more after it, and a message under another setting are refused."""
        assert not is_rule_message(setting, text)

    def test_a_long_explanation_is_judged_quickly(self):
        """An explanation that nearly matches does not make the check slow."""
        text = "type " + "a is invalid," * 3000 + "extra keys: " + "x," * 3000 + "!\x00"
        started = perf_counter()
        assert not is_rule_message("teams_configuration", text)
        assert perf_counter() - started < 1


# Requests v0 and v1 answer differently beyond the error body, as
# (request, v0 answer, v1 answer, test in AdvancedSettingsResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("read or update of a course the modulestore returns nothing for", "500", "404",
     "test_a_course_the_modulestore_returns_nothing_for"),
    ("update whose body is not an object", "500", "400", "test_an_update_whose_body_is_not_an_object"),
    ("update entry without a value", "500", "400", "test_an_update_entry_without_a_value"),
    ("read with view=default", "200", "400", "test_an_unknown_view"),
    ("read or update with a Bearer token", "200", "401", "test_a_bearer_token"),
    ("slash-separated course key", "404 JSON", "404 HTML", "test_a_slash_separated_course_key"),
    ("OPTIONS on an unknown course", "200", "404", "test_options_on_an_unknown_course"),
    ("PUT on an unknown course", "405", "404", "test_put_on_an_unknown_course"),
]


class AdvancedSettingsResponseDifferencesTest(ModuleStoreTestCase):
    """Each request v0 and v1 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name="Original")
        writer = UserFactory()
        CourseStaffRole(self.course.id).add_users(writer)
        self.client = api_client(writer)

    def both(self, method, body=None, **params):
        """The v0 and v1 responses to the same request."""
        call = getattr(self.client, method)
        kwargs = {"format": "json"} if body is not None else {}
        data = body if body is not None else params
        return call(legacy_url(self.course.id), data, **kwargs), call(url(self.course.id), data, **kwargs)

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_a_course_the_modulestore_returns_nothing_for(self):
        with patch.object(MixedModuleStore, "get_course", return_value=None):
            for method, body in (("get", None), ("patch", {"display_name": {"value": "x"}})):
                legacy, new = self.both(method, body)
                assert legacy.status_code == 500, method
                assert_refused(new, 404)

    def test_an_update_whose_body_is_not_an_object(self):
        legacy, new = self.both("patch", [1, 2])
        assert legacy.status_code == 500
        envelope = assert_error_envelope(new, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]}

    def test_an_update_entry_without_a_value(self):
        legacy, new = self.both("patch", {"display_name": {}})
        assert legacy.status_code == 500
        envelope = assert_error_envelope(new, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"display_name": ["This field is required."]}
        assert stored_display_name(self.course.id) == "Original"

    def test_an_unknown_view(self):
        legacy, new = self.both("get", view="default")
        assert legacy.status_code == 200
        assert_error_envelope(new, expected_status=400, expected_type_slug="validation")

    def test_a_bearer_token(self):
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200
        assert_refused(APIClient().get(url(self.course.id), **header), 401)
        body = {"display_name": {"value": "By Bearer"}}
        assert APIClient().patch(legacy_url(self.course.id), body, format="json", **header).status_code == 200
        assert_refused(APIClient().patch(url(self.course.id), body, format="json", **header), 401)

    def test_a_slash_separated_course_key(self):
        """v0 routes the slash form and reports the course unknown in JSON; v1 has no route for it."""
        legacy = self.client.get("/api/contentstore/v0/advanced_settings/edX/NoSuch/Course")
        new = self.client.get("/api/authoring/v1/courses/edX/NoSuch/Course/advanced_settings/")
        assert legacy.status_code == new.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        assert new["Content-Type"].startswith("text/html")

    def test_options_on_an_unknown_course(self):
        """v0 checks the course only when reading or updating; v1 checks it for every method."""
        assert self.client.options(legacy_url(UNKNOWN_COURSE_KEY)).status_code == 200
        assert_refused(self.client.options(url(UNKNOWN_COURSE_KEY)), 404)

    def test_put_on_an_unknown_course(self):
        """v0 refuses the method; v1 reports the missing course first."""
        body = {"display_name": {"value": "x"}}
        assert self.client.put(legacy_url(UNKNOWN_COURSE_KEY), body, format="json").status_code == 405
        assert_refused(self.client.put(url(UNKNOWN_COURSE_KEY), body, format="json"), 404)


@ddt.ddt
@override_settings(
    ENABLE_PROCTORED_EXAMS=True,
    PROCTORING_BACKENDS={"DEFAULT": "test_proctoring_provider", "valid_provider": {}},
    PARTNER_SUPPORT_EMAIL="support@example.com",
)
class AdvancedSettingsProctoringProviderTest(ModuleStoreTestCase):
    """
    Once a course has started, only global staff may change its proctoring
    provider, on v1 as on v0. Before it starts, course staff may.
    """

    CHANGE = {"proctoring_provider": {"value": "valid_provider"}}
    AFTER_START = (
        "The proctoring provider cannot be modified after a course has started. "
        "Contact support@example.com for assistance"
    )

    def _course(self, started):
        offset = timedelta(days=-1 if started else 30)
        return CourseFactory.create(start=datetime.now(UTC) + offset)

    def _course_staff(self, course):
        user = UserFactory()
        CourseStaffRole(course.id).add_users(user)
        return user

    def _patch(self, version, user, course):
        address = url(course.id) if version == "v1" else legacy_url(course.id)
        return api_client(user).patch(address, self.CHANGE, format="json")

    @ddt.data("v1", "v0")
    def test_course_staff_may_not_change_it_after_the_course_starts(self, version):
        course = self._course(started=True)
        before = stored_course(course.id).proctoring_provider
        assert before != "valid_provider"
        response = self._patch(version, self._course_staff(course), course)
        assert response.status_code == 400, response.content
        if version == "v1":
            envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
            assert envelope["errors"] == {"proctoring_provider": [self.AFTER_START]}
        else:
            refusals = [(entry["key"], entry["message"]) for entry in response.json()["developer_message"]]
            assert refusals == [("proctoring_provider", self.AFTER_START)]
        assert stored_course(course.id).proctoring_provider == before

    @ddt.data("v1", "v0")
    def test_global_staff_may_change_it_after_the_course_starts(self, version):
        course = self._course(started=True)
        response = self._patch(version, UserFactory(is_staff=True), course)
        assert response.status_code == 200, response.content
        assert response.json()["proctoring_provider"]["value"] == "valid_provider"
        assert stored_course(course.id).proctoring_provider == "valid_provider"

    @ddt.data("v1", "v0")
    def test_course_staff_may_change_it_before_the_course_starts(self, version):
        course = self._course(started=False)
        response = self._patch(version, self._course_staff(course), course)
        assert response.status_code == 200, response.content
        assert stored_course(course.id).proctoring_provider == "valid_provider"


# ---------------------------------------------------------------------------
# Parity with v0
# ---------------------------------------------------------------------------

# Members of a response body that differ between v0 and v1 for the same
# request. The comparison asserts nothing else differs, and that each of these
# occurs in the error cases below.
INTENTIONAL_DIFFERENCES = [
    ("type", "errors use the standardized envelope"),
    ("title", "errors use the standardized envelope"),
    ("status", "errors use the standardized envelope"),
    ("detail", "errors use the standardized envelope"),
    ("errors", "a refused update maps the name of each refused setting to its messages, where v0 lists them "
               "under developer_message; a message that is not one of the course rules' own, such as exception "
               "text, is INVALID_SETTING_MESSAGE on v1"),
    ("developer_message", "the v0 error body carries developer_message; the envelope does not"),
    ("error_code", "the v0 not-found body carries error_code; the envelope does not"),
]

# Reads of the two addresses that return the same settings. Their parameters
# differ by design: the default view is v0's fetch_all=0 response, view=full
# is v0's default, and fields replaces filter_fields.
EQUIVALENT_READS = [
    ({}, LEGACY_DEFAULT),
    (FULL, {}),
    (
        {"fields": "display_name,days_early_for_beta"},
        {**LEGACY_DEFAULT, "filter_fields": "display_name,days_early_for_beta"},
    ),
    ({**FULL, "fields": "tabs,display_name"}, {"filter_fields": "tabs,display_name"}),
]


def _undeclared(changed):
    """The changed paths no declared intentional difference covers."""
    return undeclared_paths(changed, INTENTIONAL_DIFFERENCES)


def test_the_comparison_sees_types_order_free_members_and_empty_containers():
    assert changed_paths({"a": {"b": []}}, {"a": {}}) == {"a", "a.b"}
    assert changed_paths({"x": True}, {"x": 1}) == {"x"}
    assert changed_paths({"x": 1.0}, {"x": 1}) == {"x"}
    assert changed_paths({"x": {}}, {"x": []}) == {"x"}
    assert changed_paths({"x": None, "y": 1}, {"y": 1}) == {"x"}
    assert changed_paths({"instance": "/a"}, {"instance": "/b"}) == set()
    assert changed_paths({"x": [1, {"y": False}]}, {"x": [1, {"y": False}]}) == set()


def test_only_declared_differences_are_tolerated():
    assert _undeclared({"type", "errors.display_name[0]", "developer_message[0].key"}) == set()
    assert _undeclared({"display_name.value", "typed"}) == {"display_name.value", "typed"}


class _ParityMixin:
    """
    v1 against v0 for the same course and caller: reads and updates return
    identical bodies, and errors differ only by the declared differences.
    """

    def reader_for(self, course):
        """Return a user who may read and change the course's settings."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user with no access to the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name="Parity")
        self.reader = self.reader_for(self.course)
        self.observed = set()

    def compare(self, legacy, new, expected_status):
        """Assert both statuses and that the bodies differ only as declared; return both bodies."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_body, new_body = json.loads(legacy.content or b"null"), json.loads(new.content or b"null")
        changed = changed_paths(legacy_body, new_body)
        assert _undeclared(changed) == set(), f"undeclared differences: legacy={legacy_body} new={new_body}"
        self.observed |= changed
        return legacy_body, new_body

    def test_equivalent_reads_return_identical_bodies(self):
        client = api_client(self.reader)
        for new_params, legacy_params in EQUIVALENT_READS:
            legacy_body, new_body = self.compare(
                client.get(legacy_url(self.course.id), legacy_params),
                client.get(url(self.course.id), new_params),
                200,
            )
            assert changed_paths(legacy_body, new_body) == set(), (new_params, legacy_params)
            assert list(new_body) == list(legacy_body)
            assert new_body != {}

    def test_the_same_parameters_return_different_settings(self):
        """The default view is not v0's default: the difference is exactly the excluded settings."""
        client = api_client(self.reader)
        legacy_body = client.get(legacy_url(self.course.id)).json()
        new_body = client.get(url(self.course.id)).json()
        assert set(legacy_body) - set(new_body) == set(every_setting(self.course.id)) - set(
            editable_settings(self.course.id)
        )
        assert set(new_body) < set(legacy_body)
        assert {name: legacy_body[name] for name in new_body} == new_body

    def test_an_update_returns_an_identical_body(self):
        body = {"display_name": {"value": "Changed by both"}, "days_early_for_beta": {"value": 2.0}}
        client = api_client(self.reader)
        new = client.patch(url(self.course.id), body, format="json")
        legacy = client.patch(legacy_url(self.course.id), body, format="json")
        legacy_body, new_body = self.compare(legacy, new, 200)
        assert changed_paths(legacy_body, new_body) == set()
        assert new_body["display_name"]["value"] == "Changed by both"

    def _error_pairs(self):
        """``(legacy, new, status)`` for each error both addresses produce."""
        refused = {INTEGER_SETTING: {"value": "lots"}}
        cases = [
            (api_client(), self.course.id, "get", None, 401),
            (api_client(self.outsider()), self.course.id, "get", None, 403),
            (api_client(self.outsider()), UNKNOWN_COURSE_KEY, "get", None, 404),
            (api_client(self.reader), self.course.id, "patch", refused, 400),
        ]
        for client, course_key, method, body, status in cases:
            call = getattr(client, method)
            kwargs = {"format": "json"} if body is not None else {}
            legacy = call(legacy_url(course_key), body, **kwargs)
            new = call(url(course_key), body, **kwargs)
            yield legacy, new, status

    def test_errors_differ_only_by_the_envelope(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)

    def test_a_refused_update_names_the_settings_v0_names_with_a_fixed_message(self):
        *_, (legacy, new, status) = self._error_pairs()
        legacy_body, new_body = self.compare(legacy, new, status)
        validator_texts = {entry["key"]: entry["message"] for entry in legacy_body["developer_message"]}
        assert new_body["errors"] == {name: [INVALID_SETTING_MESSAGE] for name in validator_texts}
        assert INVALID_SETTING_MESSAGE not in validator_texts.values()
        assert [text for text in validator_texts.values() if text in new.content.decode()] == []

    def test_a_malformed_entry_of_a_setting_authors_do_not_edit_is_ignored_by_both(self):
        client = api_client(self.reader)
        for body in ({"tabs": "not tabs"}, {"start": {}}, {"tabs": "not tabs", "display_name": {"value": "Both"}}):
            new = client.patch(url(self.course.id), body, format="json")
            legacy = client.patch(legacy_url(self.course.id), body, format="json")
            legacy_body, new_body = self.compare(legacy, new, 200)
            assert changed_paths(legacy_body, new_body) == set(), body

    def test_every_declared_difference_occurs(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in self.observed)
        ]
        assert missing == []


class AdvancedSettingsParityLegacyPermissionsTest(_ParityMixin, ModuleStoreTestCase):
    """Parity read and written by course staff, with policy-based authoring permissions off."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader

    def outsider(self):
        return UserFactory()


class AdvancedSettingsParityAuthzTest(_ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Parity read and written by a course staff role, with policy-based authoring permissions on."""

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_STAFF.external_key, course.id)
        return reader

    def outsider(self):
        return self.unauthorized_user


# ---------------------------------------------------------------------------
# Side effects of reads
# ---------------------------------------------------------------------------


READS = [("get", {}), ("get", FULL), ("get", {"fields": "display_name"}), ("head", {}), ("head", FULL)]


class _ReadsAreSideEffectFreeMixin:
    """
    Every read (default, full, named settings, and HEAD on both views) writes
    no row and no Mongo document, stores nothing through the modulestore and
    sends no signal or event. The reads start on a database that has never
    served a role or permission lookup, so the first read may insert the
    permission library's one policy version row and nothing else; v0 inserts
    the same row.
    """

    def reader_for(self, course):
        """Return a user who may read ``course``, leaving the database as no lookup had touched it."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def test_reads_have_no_side_effects(self):
        client = api_client(self.reader_for(self.course))
        assert not PolicyCacheControl.objects.exists()
        for index, (method, params) in enumerate(READS):
            with record_effects(self) as effects:
                response = getattr(client, method)(url(self.course.id), params)
            read = (index, method, params)
            first = index == 0
            assert response.status_code == 200, (read, response.content)
            assert effects["sql_writes"] == (FIRST_LOOKUP_WRITES if first else []), read
            assert effects["signals"] == (FIRST_LOOKUP_SIGNALS if first else []), read
            assert effects["events"] == [], read
            assert effects["mongo_writes"] == 0, read
            assert effects["update_item"] == [], read

    def test_the_v0_read_makes_the_same_first_lookup_write(self):
        client = api_client(self.reader_for(self.course))
        with record_effects(self) as effects:
            response = client.get(legacy_url(self.course.id), LEGACY_DEFAULT)
        assert response.status_code == 200, response.content
        assert effects["sql_writes"] == FIRST_LOOKUP_WRITES
        assert effects["signals"] == FIRST_LOOKUP_SIGNALS
        assert effects["mongo_writes"] == 0

    def test_the_recorder_sees_writes_signals_and_modulestore_updates(self):
        """The checks above would notice a write: one made inside the recorder is seen."""
        with record_effects(self) as effects:
            UserFactory()
            course = modulestore().get_course(self.course.id)
            modulestore().update_item(course, self.user.id)
        assert ("INSERT", "auth_user") in effects["sql_writes"]
        assert ("post_save", "User") in effects["signals"]
        assert effects["mongo_writes"] > 0
        assert effects["update_item"] == [str(course.location)]

    def test_the_recorder_sees_what_commit_callbacks_do(self):
        """Work deferred to the end of the transaction is recorded too."""
        probe = Signal()

        class Sender:
            pass

        with record_effects(self) as effects:
            transaction.on_commit(lambda: probe.send(sender=Sender))
            transaction.on_commit(lambda: UserFactory(username="made_on_commit"))
        assert (repr(probe), "Sender") in effects["signals"]
        assert ("INSERT", "auth_user") in effects["sql_writes"]
        assert ("post_save", "User") in effects["signals"]


class AdvancedSettingsReadSideEffectsLegacyPermissionsTest(_ReadsAreSideEffectFreeMixin, ModuleStoreTestCase):
    """Reads by course staff with policy-based authoring permissions off."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        PolicyCacheControl.objects.all().delete()
        return reader


class AdvancedSettingsReadSideEffectsAuthzTest(
    _ReadsAreSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """
    Reads by a course auditor with policy-based authoring permissions on. The
    role is assigned through the permission library, which creates the policy
    version row; it is deleted before the reads.
    """

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        PolicyCacheControl.objects.all().delete()
        return reader


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------


class _QueryCountMixin:
    """Each read costs no more SQL queries or Mongo reads than the v0 read returning the same settings."""

    def reader_for(self, course):
        raise NotImplementedError

    def test_reads_cost_no_more_than_the_equivalent_v0_reads(self):
        course = CourseFactory.create()
        client = api_client(self.reader_for(course))
        for new_params, legacy_params in EQUIVALENT_READS[:2]:
            legacy = measure(lambda params=legacy_params: client.get(legacy_url(course.id), params))
            new = measure(lambda params=new_params: client.get(url(course.id), params))
            assert new[0] <= legacy[0], (new_params, new, legacy)
            assert new[1] <= legacy[1], (new_params, new, legacy)
            assert new[2] == legacy[2] == 0, (new_params, new, legacy)


class AdvancedSettingsQueryCountLegacyPermissionsTest(_QueryCountMixin, ModuleStoreTestCase):
    """Costs read by course staff, with policy-based authoring permissions off."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class AdvancedSettingsQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
    """Costs read by a course auditor, with policy-based authoring permissions on."""

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        return reader


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"


@ddt.ddt
class AdvancedSettingsRoutingTest(SharedModuleStoreTestCase):
    """The address, the v0 address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_the_address(self):
        assert url(COURSE_KEY) == f"/api/authoring/v1/courses/{COURSE_KEY}/advanced_settings/"
        match = resolve(url(COURSE_KEY))
        assert match.func.cls is CourseAdvancedSettingsViewSet
        assert routed_actions(match.func) == {"get": "retrieve", "patch": "partial_update"}

    def test_the_v0_address_is_unchanged(self):
        assert legacy_url(COURSE_KEY) == f"/api/contentstore/v0/advanced_settings/{COURSE_KEY}"
        view = resolve(legacy_url(COURSE_KEY)).func.cls
        assert (view.__module__, view.__name__) == LEGACY_VIEW

    def test_v0_still_takes_a_slash_separated_key_that_v1_does_not(self):
        view = resolve("/api/contentstore/v0/advanced_settings/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == LEGACY_VIEW
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v1/courses/edX/DemoX/Demo_Course/advanced_settings/")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v1 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert "course_advanced_settings" in names

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data("post", "put", "delete")
    def test_other_methods_are_not_allowed(self, method):
        response = getattr(api_client(UserFactory(is_staff=True)), method)(url(self.course.id), {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_unsupported_accept_is_refused_with_406(self):
        response = api_client(UserFactory(is_staff=True)).get(url(self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # Nor ParseError or UnsupportedMediaType: a malformed or unsupported body is
    # answered with its own status but typed internal. Change the expected type
    # here once the library catalogues them.

    def test_a_malformed_json_body_is_a_400_typed_internal(self):
        response = api_client(UserFactory(is_staff=True)).patch(
            url(self.course.id), data="{", content_type="application/json",
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="internal")
        assert envelope["title"] == "Internal Server Error"

    def test_an_unsupported_media_type_is_a_415_typed_internal(self):
        response = api_client(UserFactory(is_staff=True)).patch(
            url(self.course.id), data="display_name=Renamed", content_type="text/plain",
        )
        envelope = assert_error_envelope(response, expected_status=415, expected_type_slug="internal")
        assert envelope["title"] == "Internal Server Error"
        assert stored_display_name(self.course.id) != "Renamed"

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data("not-a-course-key", "edX/DemoX/Demo_Course", "edX+DemoX+Demo_Course")
    def test_an_unparsable_or_slash_form_key_is_an_html_404(self, key):
        response = api_client(UserFactory(is_staff=True)).get(f"/api/authoring/v1/courses/{key}/advanced_settings/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        address = f"/api/authoring/v1/courses/{self.course.id}/advanced_settings"
        response = api_client(UserFactory(is_staff=True)).get(address)
        assert response.status_code == 301
        assert response["Location"] == url(self.course.id)
        with pytest.raises(Resolver404):
            resolve(address)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

class AdvancedSettingsSchemaTest(TestCase):
    """The read and the update as published in the CMS authoring schema."""

    PATH = "/api/authoring/v1/courses/{course_key}/advanced_settings/"
    LEGACY = "/api/contentstore/v0/advanced_settings/{course_id}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = published_schema()
        cls.components = cls.schema["components"]["schemas"]

    @staticmethod
    def _ref(node):
        return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def test_the_read_and_the_update_are_published_for_the_sdk(self):
        operations = self.schema["paths"][self.PATH]
        assert sorted(operations) == ["get", "patch"]
        assert operations["get"]["operationId"] == "v1_courses_advanced_settings_retrieve"
        assert operations["patch"]["operationId"] == "v1_courses_advanced_settings_partial_update"
        for operation in operations.values():
            assert operation["tags"] == ["openedx-platform-sdk"]
            assert "deprecated" not in operation
            assert self._ref(operation["responses"]["200"]) == "CourseAdvancedSettings.v1"
            for status in ("400", "401", "403", "404"):
                assert self._ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_read_parameters(self):
        parameters = {p["name"]: p for p in self.schema["paths"][self.PATH]["get"]["parameters"]}
        assert set(parameters) == {"course_key", "view", "fields"}
        assert parameters["course_key"]["in"] == "path"
        assert parameters["course_key"]["schema"] == {"type": "string", "pattern": "^[^/]+$"}
        assert parameters["view"]["in"] == parameters["fields"]["in"] == "query"
        assert not parameters["view"].get("required")
        assert not parameters["fields"].get("required")
        assert parameters["view"]["schema"] == {"type": "string", "enum": ["full", "minimal"], "default": "minimal"}
        assert parameters["fields"]["schema"] == {"type": "string"}

    def test_the_settings_are_an_object_keyed_by_setting_name(self):
        settings = self.components["CourseAdvancedSettings.v1"]
        assert settings["type"] == "object"
        assert settings["additionalProperties"] == {"$ref": "#/components/schemas/AdvancedSetting.v1"}
        assert "properties" not in settings
        setting = self.components["AdvancedSetting.v1"]
        assert set(setting["properties"]) == SETTING_MEMBERS
        assert set(setting["required"]) == SETTING_MEMBERS
        nullable = {name for name, field in setting["properties"].items() if field.get("nullable")}
        assert nullable == {"value", "help"}

    def test_the_update_body_is_json_settings_each_with_a_value(self):
        body = self.schema["paths"][self.PATH]["patch"]["requestBody"]
        assert list(body["content"]) == ["application/json"]
        assert self._ref(body) == "PatchedCourseAdvancedSettingsUpdate.v1"
        update = self.components[self._ref(body)]
        assert update["type"] == "object"
        assert update["additionalProperties"] == {"$ref": "#/components/schemas/AdvancedSettingUpdate.v1"}
        entry = self.components["AdvancedSettingUpdate.v1"]
        assert set(entry["properties"]) == {"value"}
        assert entry["required"] == ["value"]
        assert entry["properties"]["value"]["nullable"] is True

    def test_every_member_is_described(self):
        for name in ("CourseAdvancedSettings.v1", "AdvancedSetting.v1", "AdvancedSettingUpdate.v1"):
            component = self.components[name]
            assert component.get("description"), name
            properties = component.get("properties", {})
            assert [field for field, spec in properties.items() if not spec.get("description")] == [], name

    def test_the_examples_are_valid_bodies(self):
        get = self.schema["paths"][self.PATH]["get"]
        patch_ = self.schema["paths"][self.PATH]["patch"]
        response_example = get["responses"]["200"]["content"]["application/json"]["examples"]["AdvancedSettings"]
        assert_matches_published_schema(response_example["value"], "CourseAdvancedSettings.v1")
        request_example = patch_["requestBody"]["content"]["application/json"]["examples"]["NewValues"]
        assert_matches_published_schema(request_example["value"], self._ref(patch_["requestBody"]))

    def test_the_v0_operations_are_deprecated_and_left_out_of_the_sdk(self):
        operations = self.schema["paths"][self.LEGACY]
        assert sorted(operations) == ["get", "patch"]
        assert operations["get"]["operationId"] == "v0_advanced_settings_retrieve"
        assert operations["patch"]["operationId"] == "v0_advanced_settings_partial_update"
        for operation in operations.values():
            assert operation["deprecated"] is True
            assert "openedx-platform-sdk" not in operation.get("tags", [])

    def test_only_superseded_or_migrated_addresses_are_deprecated(self):
        """No operation is marked deprecated except by the legacy-address lists, so a stray mark is caught."""
        deprecated = {
            (path, method)
            for path, operations in self.schema["paths"].items()
            for method, operation in operations.items()
            if isinstance(operation, dict) and operation.get("deprecated")
        }
        assert {(self.LEGACY, "get"), (self.LEGACY, "patch")} <= deprecated
        assert {(self.PATH, "get"), (self.PATH, "patch")} & deprecated == set()
        stray = sorted(
            (path, method) for path, method in deprecated
            if path not in SUPERSEDED_PATHS and not path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        )
        assert stray == []
