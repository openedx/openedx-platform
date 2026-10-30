"""Tests for the v1 course custom pages and tabs API."""

import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import ddt
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_authz.constants.permissions import COURSES_MANAGE_PAGES_AND_RESOURCES, COURSES_VIEW_PAGES_AND_RESOURCES
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
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from cms.djangoapps.contentstore.rest_api.pagination import ENVELOPE_KEYS, DocumentedDefaultPagination
from cms.djangoapps.contentstore.rest_api.v1.custom_pages_service import (
    ORDER_NOT_SAVED_MESSAGE,
    REPEATED_CUSTOM_PAGE_MESSAGE,
    TAB_NOT_FOUND_MESSAGE,
    TAB_NOT_HIDEABLE_MESSAGE,
    UNKNOWN_CUSTOM_PAGE_MESSAGE,
)
from cms.djangoapps.contentstore.rest_api.v1.serializers.custom_pages import (
    NOT_A_COURSE_KEY_MESSAGE,
    NOT_A_USAGE_KEY_MESSAGE,
)
from cms.djangoapps.contentstore.rest_api.v1.views.custom_pages import (
    CourseCustomPageOrderViewSet,
    CourseCustomPagesViewSet,
    CourseTabsViewSet,
    _CourseKeyFromQuery,
)
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
from cms.djangoapps.contentstore.utils import get_pages_and_resources_url
from cms.djangoapps.contentstore.views.permissions import (
    CanManagePagesAndResources,
    CanViewPagesAndResources,
    IsExistingCourse,
)
from cms.lib.spectacular import LEGACY_MIGRATED_PATH_PREFIXES, SUPERSEDED_PATHS
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory
from xmodule.tabs import CourseTabList, InvalidTabsException, StaticTab

LIST_URL_NAME = "authoring_v1:custom_page_list"
ORDER_URL_NAME = "authoring_v1:course_custom_page_order"
TAB_URL_NAME = "authoring_v1:course_tab_detail"
LEGACY_LIST_URL_NAME = "cms.djangoapps.contentstore:v0:course_tab_list"
LEGACY_ORDER_URL_NAME = "cms.djangoapps.contentstore:v0:course_tab_reorder"
LEGACY_SETTINGS_URL_NAME = "cms.djangoapps.contentstore:v0:course_tab_settings"
LEGACY_MODULE = "cms.djangoapps.contentstore.rest_api.v0.views.tabs"
SERVICE_LOGGER = "cms.djangoapps.contentstore.rest_api.v1.custom_pages_service"
LEGACY_TABS_MODULE = "cms.djangoapps.contentstore.views.tabs"
COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"
# A built-in tab course authors may hide, and one they may not.
HIDEABLE_TAB = "progress"
UNHIDEABLE_TAB = "courseware"
TAB_MEMBERS = {
    "type", "title", "is_hideable", "is_hidden", "is_movable", "course_staff_only", "name", "tab_id", "settings",
}
PAGE_MEMBERS = TAB_MEMBERS | {"id"}
ACTIONS = ("list", "reorder", "hide")
# The status each action answers with on success, on v1 and on v0.
SUCCESS = {"list": 200, "reorder": 204, "hide": 200}
LEGACY_SUCCESS = {"list": 200, "reorder": 204, "hide": 204}
OK = "ok"


def list_url(course_key=None):
    """Return the custom pages address, naming ``course_key`` in its query when given."""
    address = reverse(LIST_URL_NAME)
    if course_key is None:
        return address
    return f"{address}?{urlencode({'course_key': str(course_key)})}"


def list_query(course_key, **params):
    """
    The query of a listing of the custom pages of ``course_key``. A request
    given query data ignores a query already in its address, so listings with
    other parameters pass the course this way.
    """
    return {"course_key": str(course_key), **params}


def order_url(course_key):
    """Return the address of the order of the custom pages of ``course_key``."""
    return reverse(ORDER_URL_NAME, kwargs={"course_key": str(course_key)})


def tab_url(course_key, tab_id=HIDEABLE_TAB):
    """Return the address of the tab ``tab_id`` of ``course_key``."""
    return reverse(TAB_URL_NAME, kwargs={"course_key": str(course_key), "tab_id": tab_id})


def legacy_list_url(course_key):
    """Return the deprecated address listing the tabs of ``course_key``."""
    return reverse(LEGACY_LIST_URL_NAME, kwargs={"course_id": str(course_key)})


def legacy_order_url(course_key):
    """Return the deprecated address reordering the tabs of ``course_key``."""
    return reverse(LEGACY_ORDER_URL_NAME, kwargs={"course_id": str(course_key)})


def legacy_settings_url(course_key, tab_id=HIDEABLE_TAB):
    """Return the deprecated address changing the tab ``tab_id`` of ``course_key``."""
    return f"{reverse(LEGACY_SETTINGS_URL_NAME, kwargs={'course_id': str(course_key)})}?{urlencode({'tab_id': tab_id})}"


def stored_course(course_key):
    """The stored course block of ``course_key`` (a key or its string form), or None when there is none."""
    return modulestore().get_course(CourseKey.from_string(str(course_key)))


def stored_tab_ids(course_key):
    """The ``tab_id`` of each stored tab of the course, in order."""
    return [tab.tab_id for tab in stored_course(course_key).tabs]


def stored_tab(course_key, tab_id):
    """The stored tab ``tab_id`` of the course."""
    return CourseTabList.get_tab_by_id(stored_course(course_key).tabs, tab_id)


def custom_page_keys(course_key):
    """The usage keys of the course's stored custom pages, in order; none for a course that does not exist."""
    course = stored_course(course_key)
    if course is None:
        return []
    return [
        str(course.id.make_usage_key("static_tab", tab.url_slug)) for tab in course.tabs if isinstance(tab, StaticTab)
    ]


def add_custom_pages(course, count):
    """Add ``count`` custom pages to ``course`` and return the usage keys of all its pages, in order."""
    for index in range(count):
        BlockFactory.create(parent_location=course.location, category="static_tab", display_name=f"Page {index}")
    return custom_page_keys(course.id)


def add_custom_page_entries(course_key, count):
    """
    Add ``count`` custom page entries to the course's tabs without creating
    their content blocks, which listing does not read.
    """
    course = stored_course(course_key)
    course.tabs.extend(StaticTab(name=f"Entry {index}", url_slug=f"entry_{index:03d}") for index in range(count))
    modulestore().update_item(course, ModuleStoreEnum.UserID.test)


# ---------------------------------------------------------------------------
# Who may call the endpoints
# ---------------------------------------------------------------------------

class _AccessMixin:
    """
    Checks a caller's status on each action against the expected one, and
    that v0 decides the same for the same caller.
    """

    def assert_list(self, user, expected, course_key=None):
        """Assert the status of a listing by ``user``, its body or envelope, and agreement with v0."""
        course_key = course_key or self.course.id
        response = api_client(user).get(list_url(), list_query(course_key, page_size=100))
        assert response.status_code == expected, response.content
        if expected == 200:
            assert [page["id"] for page in response.json()["results"]] == custom_page_keys(course_key)
        else:
            assert_refused(response, expected)
        legacy = api_client(user).get(legacy_list_url(course_key))
        assert legacy.status_code == expected, f"v0 decided {legacy.status_code} for the same caller"

    def assert_reorder(self, user, expected, course_key=None):
        """Assert the status of a reorder by ``user``, what it stored, and agreement with v0."""
        course_key = course_key or self.course.id
        before = custom_page_keys(course_key)
        wanted = before[::-1]
        response = api_client(user).put(order_url(course_key), {"custom_pages": wanted}, format="json")
        assert response.status_code == expected, response.content
        if expected == 204:
            assert response.content == b""
            assert custom_page_keys(course_key) == wanted
        else:
            assert_refused(response, expected)
            assert custom_page_keys(course_key) == before
        body = [{"tab_locator": key} for key in before]
        legacy = api_client(user).post(legacy_order_url(course_key), body, format="json")
        expected_legacy = LEGACY_SUCCESS["reorder"] if expected == 204 else expected
        assert legacy.status_code == expected_legacy, f"v0 decided {legacy.status_code} for the same caller"

    def assert_hide(self, user, expected, course_key=None):
        """Assert the status of a change of a tab's visibility by ``user``, what it stored, and agreement with v0."""
        course_key = course_key or self.course.id
        course = stored_course(course_key)
        before = stored_tab(course_key, HIDEABLE_TAB).is_hidden if course is not None else False
        response = api_client(user).patch(tab_url(course_key), {"is_hidden": not before}, format="json")
        assert response.status_code == expected, response.content
        if expected == 200:
            assert (response.json()["tab_id"], response.json()["is_hidden"]) == (HIDEABLE_TAB, not before)
            assert stored_tab(course_key, HIDEABLE_TAB).is_hidden is (not before)
        else:
            assert_refused(response, expected)
            if course is not None:
                assert stored_tab(course_key, HIDEABLE_TAB).is_hidden is before
        legacy = api_client(user).post(legacy_settings_url(course_key), {"is_hidden": before}, format="json")
        expected_legacy = LEGACY_SUCCESS["hide"] if expected == 200 else expected
        assert legacy.status_code == expected_legacy, f"v0 decided {legacy.status_code} for the same caller"

    def assert_access(self, user, action, expected, course_key=None):
        """Assert ``expected`` (a status, or ``OK`` for the action's success) for ``action`` by ``user``."""
        status = SUCCESS[action] if expected == OK else expected
        getattr(self, f"assert_{action}")(user, status, course_key)


@ddt.ddt
class CustomPagesAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may list, reorder and change tabs when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        add_custom_pages(cls.course, 3)
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
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), action, OK)

    @ddt.data(*ACTIONS)
    def test_course_instructor_is_allowed(self, action):
        self.assert_access(self._user_with(CourseInstructorRole(self.course.id)), action, OK)

    @ddt.data(*ACTIONS)
    def test_org_staff_and_org_instructor_are_allowed(self, action):
        self.assert_access(self._user_with(OrgStaffRole(self.course.id.org)), action, OK)
        self.assert_access(self._user_with(OrgInstructorRole(self.course.id.org)), action, OK)

    @ddt.data(*ACTIONS)
    def test_global_staff_is_allowed(self, action):
        self.assert_access(UserFactory(is_staff=True), action, OK)

    @ddt.data(*ACTIONS)
    def test_a_superuser_who_is_not_global_staff_is_allowed(self, action):
        """The role checks consult the policy engine even here, and it admits superusers, as on v0."""
        self.assert_access(UserFactory(is_superuser=True, is_staff=False), action, OK)

    def test_a_version_only_course_key_is_404(self):
        """The key parses but names no course run; v0 has no address that takes it."""
        user = UserFactory(is_staff=True)
        assert_refused(api_client(user).get(list_url(VERSION_ONLY_KEY)), 404)
        assert_refused(api_client(user).put(order_url(VERSION_ONLY_KEY), {"custom_pages": []}, format="json"), 404)
        assert_refused(api_client(user).patch(tab_url(VERSION_ONLY_KEY), {"is_hidden": True}, format="json"), 404)

    def test_a_library_is_not_a_course(self):
        library_key = LibraryFactory.create().location.library_key
        user = UserFactory(is_staff=True)
        assert_refused(api_client(user).get(list_url(library_key)), 404)
        assert_refused(api_client(user).put(order_url(library_key), {"custom_pages": []}, format="json"), 404)
        assert_refused(api_client(user).patch(tab_url(library_key), {"is_hidden": True}, format="json"), 404)


@ddt.ddt
class CustomPagesAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    Who may list, reorder and change tabs when policy-based authoring
    permissions are on: listing needs the permission to view pages and
    resources, reordering and changing a tab the permission to manage them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        add_custom_pages(cls.course, 3)
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

    def test_a_role_that_may_only_view_may_list_but_not_reorder_or_change_tabs(self):
        user = self._user_with(COURSE_AUDITOR.external_key)
        self.assert_list(user, 200)
        self.assert_reorder(user, 403)
        self.assert_hide(user, 403)

    @ddt.data(*[(action, role) for action in ACTIONS for role in (
        COURSE_EDITOR.external_key, COURSE_STAFF.external_key, COURSE_ADMIN.external_key,
    )])
    @ddt.unpack
    def test_roles_that_may_manage_may_do_everything(self, action, role):
        self.assert_access(self._user_with(role), action, OK)

    @ddt.data(*[(action, role) for action in ACTIONS for role in (
        COURSE_LIMITED_STAFF.external_key, COURSE_DATA_RESEARCHER.external_key, COURSE_BETA_TESTER.external_key,
    )])
    @ddt.unpack
    def test_other_roles_are_refused(self, action, role):
        self.assert_access(self._user_with(role), action, 403)

    @ddt.data(*ACTIONS)
    def test_a_role_on_another_course_is_refused(self, action):
        user = self._user_with(COURSE_ADMIN.external_key, self.other_course.id)
        self.assert_access(user, action, 403)

    @ddt.data(*ACTIONS)
    def test_legacy_course_staff_without_a_policy_role_is_refused(self, action):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_access(user, action, 403)

    @ddt.data(*ACTIONS)
    def test_staff_and_superuser_are_allowed_without_a_role(self, action):
        self.assert_access(self.staff_user, action, OK)
        self.assert_access(self.super_user, action, OK)

    def test_an_inactive_session_with_a_role_is_accepted(self):
        """The policy does not look at whether the account is activated, on v1 as on v0."""
        user = self._user_with(COURSE_AUDITOR.external_key)
        client = session_client(user)
        user.is_active = False
        user.save()
        assert client.get(list_url(self.course.id)).status_code == 200
        assert client.get(legacy_list_url(self.course.id)).status_code == 200


class CustomPagesPermissionWiringTest(SimpleTestCase):
    """Which permissions each viewset checks, and what the page and resource permissions ask for."""

    @staticmethod
    def _declared(view):
        # The JWT middleware appends NotJwtRestrictedApplication to views that take JWTs.
        return [
            type(permission) for permission in view.get_permissions()
            if not isinstance(permission, NotJwtRestrictedApplication)
        ]

    def test_the_writes_check_the_course_then_the_permission_to_manage_for_every_action(self):
        """These viewsets need the stronger permission, so any action, routed or added later, is checked by it."""
        for viewset in (CourseCustomPageOrderViewSet, CourseTabsViewSet):
            view = viewset()
            for action in ("list", "update", "partial_update", "retrieve", "create", "destroy", None, "metadata"):
                view.action = action
                assert self._declared(view) == [IsAuthenticated, IsExistingCourse, CanManagePagesAndResources], (
                    viewset, action,
                )

    def test_the_listing_reads_the_course_from_the_query_then_checks_it_and_the_permission_to_view(self):
        view = CourseCustomPagesViewSet()
        for action in ("list", None, "metadata"):
            view.action = action
            assert self._declared(view) == [
                IsAuthenticated, _CourseKeyFromQuery, IsExistingCourse, CanViewPagesAndResources,
            ], action

    def test_the_listing_refuses_any_other_action(self):
        view = CourseCustomPagesViewSet()
        for action in ("retrieve", "create", "update", "partial_update", "destroy"):
            view.action = action
            with pytest.raises(ImproperlyConfigured):
                view.get_permissions()

    def test_a_routed_action_the_listing_does_not_check_is_never_served(self):
        """A request reaching such an action is answered with a server error, not the handler's body."""

        class Unchecked(CourseCustomPagesViewSet):
            permission_classes = CourseCustomPagesViewSet.permission_classes

            def destroy(self, request, **kwargs):  # pylint: disable=unused-argument
                raise AssertionError("the handler ran without a permission check")

        request = APIRequestFactory().delete(list_url(COURSE_KEY))
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_active=True))
        response = Unchecked.as_view({"delete": "destroy"})(request)
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")

    def test_the_page_and_resource_permissions_ask_for_their_policy_and_studio_access(self):
        course_key = CourseKey.from_string("course-v1:edX+Wiring+Run")
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={"course_key": course_key})
        target = "cms.djangoapps.contentstore.views.permissions.user_has_course_permission"
        for permission, identifier, legacy in (
            (CanViewPagesAndResources, COURSES_VIEW_PAGES_AND_RESOURCES.identifier, LegacyAuthoringPermission.READ),
            (
                CanManagePagesAndResources,
                COURSES_MANAGE_PAGES_AND_RESOURCES.identifier,
                LegacyAuthoringPermission.WRITE,
            ),
        ):
            for decision in (True, False):
                with patch(target, return_value=decision) as check:
                    assert permission().has_permission(request, view) is decision
                check.assert_called_once_with(request.user, identifier, course_key, legacy)

    def test_the_manage_permission_refuses_a_view_without_a_course(self):
        request = SimpleNamespace(user=object())
        target = "cms.djangoapps.contentstore.views.permissions.user_has_course_permission"
        with patch(target, return_value=True) as check:
            assert CanManagePagesAndResources().has_permission(request, SimpleNamespace(kwargs={})) is False
        check.assert_not_called()


class CustomPagesAuthenticationTest(SharedModuleStoreTestCase):
    """How callers authenticate, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        add_custom_pages(cls.course, 2)

    def _course_staff(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def _assert_every_action_succeeds(self, client, **headers):
        assert client.get(list_url(self.course.id), **headers).status_code == 200
        wanted = custom_page_keys(self.course.id)[::-1]
        response = client.put(order_url(self.course.id), {"custom_pages": wanted}, format="json", **headers)
        assert response.status_code == 204, response.content
        assert custom_page_keys(self.course.id) == wanted
        hidden = not stored_tab(self.course.id, HIDEABLE_TAB).is_hidden
        response = client.patch(tab_url(self.course.id), {"is_hidden": hidden}, format="json", **headers)
        assert response.status_code == 200, response.content
        assert stored_tab(self.course.id, HIDEABLE_TAB).is_hidden is hidden

    def test_a_jwt_caller_may_list_reorder_and_change_tabs(self):
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self._course_staff())}"}
        self._assert_every_action_succeeds(APIClient(), **header)

    def test_a_session_caller_may_list_reorder_and_change_tabs(self):
        self._assert_every_action_succeeds(session_client(self._course_staff()))

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
            response = client.get(list_url(self.course.id))
            assert response.status_code == expected, response.content
            if expected == 403:
                assert_refused(response, 403)
            assert client.get(legacy_list_url(self.course.id)).status_code == expected

    def test_a_bearer_token_is_refused(self):
        """Bearer tokens are not accepted here, though v0 takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert_refused(APIClient().get(list_url(self.course.id), **header), 401)
        assert_refused(APIClient().put(order_url(self.course.id), {"custom_pages": []}, format="json", **header), 401)
        assert_refused(APIClient().patch(tab_url(self.course.id), {"is_hidden": True}, format="json", **header), 401)
        assert APIClient().get(legacy_list_url(self.course.id), **header).status_code == 200


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@ddt.ddt
class CustomPagesListTest(SharedModuleStoreTestCase):
    """What a listing returns and how it pages."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        add_custom_pages(cls.course, 3)
        cls.twelve_pages = CourseFactory.create()
        add_custom_page_entries(cls.twelve_pages.id, 12)
        cls.many_pages = CourseFactory.create()
        add_custom_page_entries(cls.many_pages.id, 105)
        cls.no_pages = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = api_client(UserFactory(is_staff=True))

    def get(self, course_key=None, **params):
        response = self.client.get(list_url(), list_query(course_key or self.course.id, **params))
        assert response.status_code == 200, response.content
        return response.json()

    @ddt.data(True, False)
    def test_only_the_custom_pages_are_listed_in_course_order(self, pages_and_resources_page):
        """Whether Studio's pages and resources page is on changes which tabs Studio walks, not what is listed."""
        with override_waffle_flag(ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND, active=pages_and_resources_page):
            assert bool(get_pages_and_resources_url(self.course.id)) is pages_and_resources_page
            body = self.get(page_size=100)
        assert [page["id"] for page in body["results"]] == custom_page_keys(self.course.id)
        assert len(body["results"]) == 3
        listed = {page["tab_id"] for page in body["results"]}
        assert {HIDEABLE_TAB, UNHIDEABLE_TAB} <= set(stored_tab_ids(self.course.id)) - listed

    def test_each_custom_page_carries_its_members(self):
        course = stored_course(self.course.id)
        slug = next(tab.url_slug for tab in course.tabs if isinstance(tab, StaticTab))
        first = self.get()["results"][0]
        assert first == {
            "type": "static_tab",
            "title": None,
            "is_hideable": False,
            "is_hidden": False,
            "is_movable": True,
            "course_staff_only": False,
            "name": "Static Tab",
            "tab_id": f"static_tab_{slug}",
            "settings": {"url_slug": slug},
            "id": str(course.id.make_usage_key("static_tab", slug)),
        }
        assert set(first) == PAGE_MEMBERS

    def test_the_envelope_has_the_seven_keys(self):
        body = self.get()
        assert set(body) == set(ENVELOPE_KEYS)
        assert (body["count"], body["num_pages"], body["current_page"], body["start"]) == (3, 1, 1, 0)
        assert (body["next"], body["previous"]) == (None, None)

    def test_page_size_splits_the_pages(self):
        keys = custom_page_keys(self.course.id)
        first = self.get(page_size=2)
        assert [page["id"] for page in first["results"]] == keys[:2]
        assert (first["count"], first["num_pages"], first["current_page"], first["start"]) == (3, 2, 1, 0)
        assert "page=2" in first["next"]
        assert first["previous"] is None
        second = self.get(page_size=2, page=2)
        assert [page["id"] for page in second["results"]] == keys[2:]
        assert (second["current_page"], second["start"], second["next"]) == (2, 2, None)
        assert second["previous"] is not None

    def test_the_default_page_size_is_ten(self):
        body = self.get(self.twelve_pages.id)
        assert (len(body["results"]), body["count"], body["num_pages"]) == (10, 12, 2)

    def test_the_page_size_is_at_most_one_hundred(self):
        body = self.get(self.many_pages.id, page_size=1000)
        assert (len(body["results"]), body["count"], body["num_pages"]) == (100, 105, 2)
        assert [page["id"] for page in body["results"]] == custom_page_keys(self.many_pages.id)[:100]
        last = self.get(self.many_pages.id, page_size=100, page=2)
        assert [page["id"] for page in last["results"]] == custom_page_keys(self.many_pages.id)[100:]

    @ddt.data("99", "0", "abc")
    def test_a_page_out_of_range_is_404(self, page):
        response = self.client.get(list_url(), list_query(self.course.id, page=page))
        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == "Invalid page."

    def test_a_course_without_custom_pages_lists_none(self):
        body = self.get(self.no_pages.id)
        assert (body["results"], body["count"], body["num_pages"]) == ([], 0, 1)

    def test_the_body_matches_the_published_schema(self):
        assert_matches_published_schema(self.get(page_size=2), "PaginatedCustomPage.v1List")

    def test_other_query_parameters_are_ignored(self):
        assert self.get(view="full", ordering="-name", fields="id") == self.get()

    def test_head_answers_without_a_body(self):
        response = self.client.head(list_url(self.course.id))
        assert response.status_code == 200
        assert response.content == b""


@ddt.ddt
class CustomPagesCourseKeyQueryTest(SharedModuleStoreTestCase):
    """How a listing reads the course from its query, and in which order it refuses a request."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.keys = add_custom_pages(cls.course, 2)
        cls.other_course = CourseFactory.create()
        add_custom_page_entries(cls.other_course.id, 1)
        cls.other_keys = custom_page_keys(cls.other_course.id)

    def assert_invalid_course_key(self, response, message):
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"course_key": [message]}
        return envelope

    def test_each_course_lists_its_own_pages(self):
        client = api_client(UserFactory(is_staff=True))
        for course, keys in ((self.course, self.keys), (self.other_course, self.other_keys)):
            body = client.get(list_url(course.id)).json()
            assert [page["id"] for page in body["results"]] == keys

    def test_a_missing_or_blank_course_key_is_refused(self):
        client = api_client(UserFactory(is_staff=True))
        self.assert_invalid_course_key(client.get(list_url()), "This field is required.")
        self.assert_invalid_course_key(client.get(list_url(), {"course_key": ""}), "This field may not be blank.")

    @ddt.data("not-a-course-key", "edX+DemoX+Demo_Course", "edX/DemoX/Demo_Course", "lib:edX:library")
    def test_a_value_that_is_not_a_course_key_is_refused_without_echoing_it(self, value):
        response = api_client(UserFactory(is_staff=True)).get(list_url(value))
        envelope = self.assert_invalid_course_key(response, NOT_A_COURSE_KEY_MESSAGE)
        assert value not in envelope["detail"]
        assert value not in json.dumps(envelope["errors"])

    def test_a_version_only_or_library_key_is_an_unknown_course(self):
        client = api_client(UserFactory(is_staff=True))
        assert_refused(client.get(list_url(VERSION_ONLY_KEY)), 404)
        assert_refused(client.get(list_url(LibraryFactory.create().location.library_key)), 404)

    def test_the_caller_is_authenticated_before_the_course_key_is_read(self):
        for address in (list_url(), list_url("not-a-course-key"), list_url(UNKNOWN_COURSE_KEY)):
            assert_refused(api_client().get(address), 401)

    def test_the_course_key_is_read_before_the_course_is_looked_up_or_the_caller_checked(self):
        outsider = api_client(UserFactory())
        self.assert_invalid_course_key(outsider.get(list_url("not-a-course-key")), NOT_A_COURSE_KEY_MESSAGE)
        assert_refused(outsider.get(list_url(UNKNOWN_COURSE_KEY)), 404)
        assert_refused(outsider.get(list_url(self.course.id)), 403)

    def test_paging_parameters_combine_with_the_course_key(self):
        client = api_client(UserFactory(is_staff=True))
        body = client.get(list_url(), list_query(self.course.id, page_size=1)).json()
        assert ([page["id"] for page in body["results"]], body["count"]) == (self.keys[:1], 2)


class SharedPageEnvelopeTest(SimpleTestCase):
    """The shared paginator pages as the library's does and publishes all seven keys."""

    def _page(self, items, **params):
        paginator = DocumentedDefaultPagination()
        request = Request(APIRequestFactory().get("/items/", params))
        page = paginator.paginate_queryset(items, request)
        return paginator.get_paginated_response(page).data

    def test_the_page_sizes_are_the_librarys(self):
        paginator = DocumentedDefaultPagination()
        assert (paginator.page_size, paginator.max_page_size, paginator.page_size_query_param) == (10, 100, "page_size")
        assert len(self._page(list(range(150)))["results"]) == 10
        assert len(self._page(list(range(150)), page_size=1000)["results"]) == 100

    def test_the_schema_describes_exactly_the_keys_of_a_page(self):
        schema = DocumentedDefaultPagination().get_paginated_response_schema({"type": "array"})
        assert list(schema["properties"]) == list(ENVELOPE_KEYS)
        assert schema["required"] == list(ENVELOPE_KEYS)
        assert set(self._page(list(range(30)), page=2)) == set(schema["properties"])
        assert schema["properties"]["results"] == {"type": "array"}
        assert {name for name, spec in schema["properties"].items() if spec.get("nullable")} == {"next", "previous"}

    def test_every_key_but_the_results_is_described_with_an_example(self):
        properties = DocumentedDefaultPagination().get_paginated_response_schema({})["properties"]
        described = {name for name, spec in properties.items() if spec.get("description") and "example" in spec}
        assert described == set(ENVELOPE_KEYS) - {"results"}


# ---------------------------------------------------------------------------
# Reordering
# ---------------------------------------------------------------------------

class _RefusingTabList(CourseTabList):
    """A tab list whose validation refuses every list, as for a course whose tabs became invalid."""

    @classmethod
    def validate_tabs(cls, tabs):
        raise InvalidTabsException("tab internals")


@ddt.ddt
class CustomPageOrderTest(ModuleStoreTestCase):
    """What a reorder stores and refuses."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.keys = add_custom_pages(self.course, 3)
        writer = UserFactory()
        CourseStaffRole(self.course.id).add_users(writer)
        self.client = api_client(writer)

    def put(self, body):
        return self.client.put(order_url(self.course.id), body, format="json")

    def assert_order_refused(self, body, errors):
        """Assert ``body`` is refused with ``errors`` and nothing is stored."""
        before = stored_tab_ids(self.course.id)
        envelope = assert_error_envelope(self.put(body), expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == errors
        assert stored_tab_ids(self.course.id) == before
        return envelope

    def test_the_pages_are_stored_in_the_new_order(self):
        wanted = [self.keys[1], self.keys[2], self.keys[0]]
        response = self.put({"custom_pages": wanted})
        assert response.status_code == 204, response.content
        assert response.content == b""
        assert custom_page_keys(self.course.id) == wanted
        listed = self.client.get(list_url(self.course.id)).json()["results"]
        assert [page["id"] for page in listed] == wanted

    def test_the_pages_stay_together_and_the_course_still_opens_on_its_courseware(self):
        self.put({"custom_pages": self.keys[::-1]})
        tab_ids = stored_tab_ids(self.course.id)
        page_ids = [f"static_tab_{UsageKey.from_string(key).block_id}" for key in self.keys[::-1]]
        start = tab_ids.index(page_ids[0])
        assert tab_ids[start:start + len(page_ids)] == page_ids
        assert tab_ids[0] == UNHIDEABLE_TAB
        assert {HIDEABLE_TAB, "dates", "wiki"} <= set(tab_ids)

    def test_pages_left_out_follow_in_their_current_order(self):
        response = self.put({"custom_pages": [self.keys[2]]})
        assert response.status_code == 204, response.content
        assert custom_page_keys(self.course.id) == [self.keys[2], self.keys[0], self.keys[1]]

    def test_an_empty_list_keeps_the_order(self):
        assert self.put({"custom_pages": []}).status_code == 204
        assert custom_page_keys(self.course.id) == self.keys

    def test_a_key_naming_a_branch_of_the_course_names_the_same_page(self):
        branched = str(UsageKey.from_string(self.keys[1]).for_branch("published-branch"))
        assert branched != self.keys[1]
        assert self.put({"custom_pages": [branched]}).status_code == 204
        assert custom_page_keys(self.course.id) == [self.keys[1], self.keys[0], self.keys[2]]

    def test_a_page_of_another_course_is_refused_even_with_the_same_url_slug(self):
        other = CourseFactory.create()
        (other_key,) = add_custom_pages(other, 1)
        assert UsageKey.from_string(other_key).block_id == UsageKey.from_string(self.keys[0]).block_id
        envelope = self.assert_order_refused({"custom_pages": [self.keys[0], other_key]},
                                             {"custom_pages": [UNKNOWN_CUSTOM_PAGE_MESSAGE]})
        assert str(other.id) not in json.dumps(envelope)

    def test_a_block_that_is_not_a_custom_page_is_refused(self):
        chapter = BlockFactory.create(parent_location=self.course.location, category="chapter")
        self.assert_order_refused(
            {"custom_pages": [str(chapter.location)]}, {"custom_pages": [UNKNOWN_CUSTOM_PAGE_MESSAGE]},
        )
        missing = str(self.course.id.make_usage_key("static_tab", "no_such_page"))
        self.assert_order_refused({"custom_pages": [missing]}, {"custom_pages": [UNKNOWN_CUSTOM_PAGE_MESSAGE]})

    def test_a_page_listed_twice_is_refused(self):
        self.assert_order_refused({"custom_pages": [self.keys[0], self.keys[0]]},
                                  {"custom_pages": [REPEATED_CUSTOM_PAGE_MESSAGE]})
        branched = str(UsageKey.from_string(self.keys[0]).for_branch("published-branch"))
        self.assert_order_refused(
            {"custom_pages": [self.keys[0], branched]}, {"custom_pages": [REPEATED_CUSTOM_PAGE_MESSAGE]},
        )

    def test_an_item_that_is_not_a_usage_key_is_refused(self):
        envelope = self.assert_order_refused({"custom_pages": ["not a usage key"]},
                                             {"custom_pages": [NOT_A_USAGE_KEY_MESSAGE]})
        assert "not a usage key" not in json.dumps(envelope)

    @ddt.data("", "   ", None, 5, True, ["x"], {"id": "x"})
    def test_an_item_that_is_not_a_string_holding_a_usage_key_is_refused_with_the_same_message(self, item):
        """Every unusable item gets the one fixed message under errors, never a per-item error or its rendering."""
        response = self.put({"custom_pages": [self.keys[0], item]})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"custom_pages": [NOT_A_USAGE_KEY_MESSAGE]}
        assert custom_page_keys(self.course.id) == self.keys

    def test_a_null_list_is_refused(self):
        self.assert_order_refused({"custom_pages": None}, {"custom_pages": ["This field may not be null."]})

    def test_the_body_must_be_an_object_naming_the_pages(self):
        self.assert_order_refused({}, {"custom_pages": ["This field is required."]})
        self.assert_order_refused(
            {"custom_pages": "abc"}, {"custom_pages": ['Expected a list of items but got type "str".']},
        )
        self.assert_order_refused(
            [self.keys[0]], {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]},
        )

    def test_a_failure_saving_the_order_is_reported_without_its_text(self):
        with (
            patch(f"{LEGACY_TABS_MODULE}.CourseTabList", _RefusingTabList),
            self.assertLogs(SERVICE_LOGGER, "WARNING") as logs,
        ):
            envelope = self.assert_order_refused({"custom_pages": self.keys[::-1]},
                                                 {"non_field_errors": [ORDER_NOT_SAVED_MESSAGE]})
        assert "tab internals" not in json.dumps(envelope)
        (record,) = logs.output
        assert "tab internals" in record
        assert str(self.course.id) in record
        with patch(f"{LEGACY_TABS_MODULE}.CourseTabList", _RefusingTabList):
            legacy = self.client.post(legacy_order_url(self.course.id), [], format="json")
        assert legacy.status_code == 400
        assert b"tab internals" in legacy.content


# ---------------------------------------------------------------------------
# Changing a tab
# ---------------------------------------------------------------------------

@ddt.ddt
class CourseTabUpdateTest(ModuleStoreTestCase):
    """What a change to a tab stores, returns and refuses."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        (self.page_key,) = add_custom_pages(self.course, 1)
        writer = UserFactory()
        CourseStaffRole(self.course.id).add_users(writer)
        self.client = api_client(writer)

    def patch(self, body, tab_id=HIDEABLE_TAB):
        return self.client.patch(tab_url(self.course.id, tab_id), body, format="json")

    @ddt.data(HIDEABLE_TAB, "dates", "wiki")
    def test_hiding_and_showing_a_tab_is_stored_and_the_tab_returned(self, tab_id):
        for hidden in (True, False):
            response = self.patch({"is_hidden": hidden}, tab_id)
            assert response.status_code == 200, response.content
            body = response.json()
            assert set(body) == TAB_MEMBERS
            assert (body["tab_id"], body["is_hideable"], body["is_hidden"]) == (tab_id, True, hidden)
            assert stored_tab(self.course.id, tab_id).is_hidden is hidden
            assert_matches_published_schema(body, "CourseTab.v1")

    def test_the_returned_tab_is_the_stored_tab(self):
        body = self.patch({"is_hidden": True}).json()
        tab = stored_tab(self.course.id, HIDEABLE_TAB)
        assert (body["type"], body["name"], body["is_movable"], body["course_staff_only"]) == (
            tab.type, str(tab.name), tab.is_movable, tab.course_staff_only,
        )

    def test_the_same_value_again_is_accepted(self):
        assert self.patch({"is_hidden": True}).status_code == 200
        response = self.patch({"is_hidden": True})
        assert (response.status_code, response.json()["is_hidden"]) == (200, True)
        assert stored_tab(self.course.id, HIDEABLE_TAB).is_hidden is True

    @ddt.data(True, False)
    def test_a_tab_that_cannot_be_hidden_is_refused(self, hidden):
        custom_page_tab = f"static_tab_{UsageKey.from_string(self.page_key).block_id}"
        for tab_id in (UNHIDEABLE_TAB, custom_page_tab):
            before = stored_tab(self.course.id, tab_id).is_hidden
            response = self.patch({"is_hidden": hidden}, tab_id)
            envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
            assert envelope["errors"] == {"is_hidden": [TAB_NOT_HIDEABLE_MESSAGE]}
            assert b"can not be hidden" not in response.content
            assert stored_tab(self.course.id, tab_id).is_hidden is before

    @ddt.data({"is_hidden": True}, {})
    def test_a_tab_the_course_does_not_have_is_404(self, body):
        response = self.patch(body, "no_such_tab")
        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == TAB_NOT_FOUND_MESSAGE
        assert "no_such_tab" not in envelope["detail"]

    def test_a_body_without_is_hidden_changes_nothing(self):
        with patch.object(MixedModuleStore, "update_item", wraps=modulestore().update_item) as update_item:
            response = self.patch({})
            renamed = self.patch({"name": "Renamed", "is_hideable": False})
        assert update_item.call_count == 0
        for answer in (response, renamed):
            assert answer.status_code == 200, answer.content
            assert (answer.json()["tab_id"], answer.json()["is_hideable"]) == (HIDEABLE_TAB, True)
        assert renamed.json()["name"] == str(stored_tab(self.course.id, HIDEABLE_TAB).name)

    @ddt.data(
        ({"is_hidden": None}, {"is_hidden": ["This field may not be null."]}),
        ({"is_hidden": "abc"}, {"is_hidden": ["Must be a valid boolean."]}),
        ([True], {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]}),
    )
    @ddt.unpack
    def test_an_invalid_body_is_refused(self, body, errors):
        before = stored_tab(self.course.id, HIDEABLE_TAB).is_hidden
        envelope = assert_error_envelope(self.patch(body), expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == errors
        assert stored_tab(self.course.id, HIDEABLE_TAB).is_hidden is before


# ---------------------------------------------------------------------------
# Requests v0 and v1 answer differently
# ---------------------------------------------------------------------------

# Requests v0 and v1 answer differently beyond the error body, as
# (request, v0 answer, v1 answer, test in CustomPagesResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("listing", "a bare array of every custom page", "a page of them in the standard envelope",
     "test_the_list_is_paginated"),
    ("change of a tab's visibility", "204 without a body", "200 with the tab",
     "test_a_visibility_change_returns_the_tab"),
    ("change of a tab the course does not have", "400", "404", "test_an_unknown_tab"),
    ("change without is_hidden", "400", "200, nothing changed", "test_a_change_without_is_hidden"),
    ("reorder naming a custom page that does not exist", "500", "400", "test_a_reorder_naming_no_page"),
    ("reorder naming a page twice", "204, the page stored twice", "400", "test_a_reorder_naming_a_page_twice"),
    ("any request for a course the modulestore returns nothing for", "500", "404",
     "test_a_course_the_modulestore_returns_nothing_for"),
    ("reorder leaving out two or more pages", "the pages left out in no particular order",
     "the pages left out in their current order", "test_pages_left_out_of_a_reorder"),
    ("reorder naming a page of another course with the same url_slug", "204, this course's page moved", "400",
     "test_a_page_of_another_course_with_the_same_url_slug"),
    ("OPTIONS by a signed-in caller without access, or for an unknown course", "200", "403 or 404",
     "test_options"),
    ("any request with a Bearer token", "2xx", "401", "test_a_bearer_token"),
    ("listing for a slash-separated course key", "404 JSON", "400", "test_a_slash_separated_course_key"),
]


class CustomPagesResponseDifferencesTest(ModuleStoreTestCase):
    """Each request v0 and v1 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.keys = add_custom_pages(self.course, 3)
        writer = UserFactory()
        CourseStaffRole(self.course.id).add_users(writer)
        self.client = api_client(writer)

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_the_list_is_paginated(self):
        legacy = self.client.get(legacy_list_url(self.course.id)).json()
        new = self.client.get(list_url(self.course.id)).json()
        assert isinstance(legacy, list)
        assert set(new) == set(ENVELOPE_KEYS)
        assert [page["id"] for page in legacy] == [page["id"] for page in new["results"]] == self.keys

    def test_a_visibility_change_returns_the_tab(self):
        legacy = self.client.post(legacy_settings_url(self.course.id), {"is_hidden": True}, format="json")
        assert (legacy.status_code, legacy.content) == (204, b"")
        new = self.client.patch(tab_url(self.course.id), {"is_hidden": False}, format="json")
        assert (new.status_code, new.json()["tab_id"], new.json()["is_hidden"]) == (200, HIDEABLE_TAB, False)

    def test_an_unknown_tab(self):
        address = legacy_settings_url(self.course.id, "no_such_tab")
        legacy = self.client.post(address, {"is_hidden": True}, format="json")
        assert legacy.status_code == 400
        assert b"no_such_tab" in legacy.content
        new = self.client.patch(tab_url(self.course.id, "no_such_tab"), {"is_hidden": True}, format="json")
        assert_error_envelope(new, expected_status=404, expected_type_slug="not-found")

    def test_a_change_without_is_hidden(self):
        legacy = self.client.post(legacy_settings_url(self.course.id), {}, format="json")
        assert legacy.status_code == 400
        new = self.client.patch(tab_url(self.course.id), {}, format="json")
        assert new.status_code == 200, new.content

    def test_a_reorder_naming_no_page(self):
        missing = str(self.course.id.make_usage_key("static_tab", "no_such_page"))
        legacy = self.client.post(legacy_order_url(self.course.id), [{"tab_locator": missing}], format="json")
        assert legacy.status_code == 500
        new = self.client.put(order_url(self.course.id), {"custom_pages": [missing]}, format="json")
        assert_error_envelope(new, expected_status=400, expected_type_slug="validation")

    def test_a_reorder_naming_a_page_twice(self):
        new = self.client.put(order_url(self.course.id), {"custom_pages": [self.keys[0]] * 2}, format="json")
        assert_error_envelope(new, expected_status=400, expected_type_slug="validation")
        assert custom_page_keys(self.course.id) == self.keys
        body = [{"tab_locator": self.keys[0]}] * 2
        legacy = self.client.post(legacy_order_url(self.course.id), body, format="json")
        assert legacy.status_code == 204
        assert custom_page_keys(self.course.id).count(self.keys[0]) == 2

    def test_a_course_the_modulestore_returns_nothing_for(self):
        with patch.object(MixedModuleStore, "get_course", return_value=None):
            for legacy, new in (
                (self.client.get(legacy_list_url(self.course.id)), self.client.get(list_url(self.course.id))),
                (
                    self.client.post(legacy_order_url(self.course.id), [], format="json"),
                    self.client.put(order_url(self.course.id), {"custom_pages": []}, format="json"),
                ),
                (
                    self.client.post(legacy_settings_url(self.course.id), {"is_hidden": True}, format="json"),
                    self.client.patch(tab_url(self.course.id), {"is_hidden": True}, format="json"),
                ),
            ):
                assert legacy.status_code == 500, legacy.content
                assert_refused(new, 404)

    def test_a_bearer_token(self):
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert APIClient().get(legacy_list_url(self.course.id), **header).status_code == 200
        assert_refused(APIClient().get(list_url(self.course.id), **header), 401)
        legacy = APIClient().post(legacy_order_url(self.course.id), [], format="json", **header)
        assert legacy.status_code == 204
        assert_refused(APIClient().put(order_url(self.course.id), {"custom_pages": []}, format="json", **header), 401)

    def test_a_slash_separated_course_key(self):
        """v0 routes the slash form and reports the course unknown in JSON; v1 refuses the key itself."""
        legacy = self.client.get("/api/contentstore/v0/tabs/edX/NoSuch/Course")
        assert legacy.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        new = self.client.get(list_url("edX/NoSuch/Course"))
        envelope = assert_error_envelope(new, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"course_key": [NOT_A_COURSE_KEY_MESSAGE]}

    def test_pages_left_out_of_a_reorder(self):
        """Naming only the last page: v1 keeps the other two in their order; v0 puts them in no set order."""
        last, first_two = self.keys[2], self.keys[:2]
        new = self.client.put(order_url(self.course.id), {"custom_pages": [last]}, format="json")
        assert new.status_code == 204, new.content
        assert custom_page_keys(self.course.id) == [last, *first_two]
        self.client.put(order_url(self.course.id), {"custom_pages": self.keys}, format="json")
        legacy = self.client.post(legacy_order_url(self.course.id), [{"tab_locator": last}], format="json")
        assert legacy.status_code == 204
        stored = custom_page_keys(self.course.id)
        assert stored[0] == last
        assert sorted(stored[1:]) == sorted(first_two)

    def test_a_page_of_another_course_with_the_same_url_slug(self):
        """v0 finds a page by its url_slug alone, so another course's page moves this course's page."""
        other = CourseFactory.create()
        (other_key,) = add_custom_pages(other, 1)
        assert UsageKey.from_string(other_key).block_id == UsageKey.from_string(self.keys[0]).block_id
        reordered = [self.keys[1], self.keys[2], other_key]
        new = self.client.put(order_url(self.course.id), {"custom_pages": reordered}, format="json")
        assert_error_envelope(new, expected_status=400, expected_type_slug="validation")
        assert custom_page_keys(self.course.id) == self.keys
        legacy_body = [{"tab_locator": key} for key in reordered]
        legacy = self.client.post(legacy_order_url(self.course.id), legacy_body, format="json")
        assert legacy.status_code == 204
        assert custom_page_keys(self.course.id) == [self.keys[1], self.keys[2], self.keys[0]]

    def test_options(self):
        """v0 answers OPTIONS to any signed-in caller; v1 checks the course and the caller first."""
        outsider = api_client(UserFactory())
        for legacy_address, new_address in (
            (legacy_list_url(self.course.id), list_url(self.course.id)),
            (legacy_order_url(self.course.id), order_url(self.course.id)),
            (legacy_settings_url(self.course.id), tab_url(self.course.id)),
        ):
            assert outsider.options(legacy_address).status_code == 200
            assert_refused(outsider.options(new_address), 403)
            assert self.client.options(new_address).status_code == 200
        assert outsider.options(legacy_list_url(UNKNOWN_COURSE_KEY)).status_code == 200
        assert_refused(outsider.options(list_url(UNKNOWN_COURSE_KEY)), 404)
        assert_refused(outsider.options(order_url(UNKNOWN_COURSE_KEY)), 404)


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
    ("errors", "a refused change names the refused member with a fixed message, where v0 returns the "
               "helper's own text under developer_message"),
    ("developer_message", "the v0 error body carries developer_message; the envelope does not"),
    ("error_code", "the v0 not-found body carries error_code; the envelope does not"),
]


def _undeclared(changed):
    """The changed paths no declared intentional difference of this module covers."""
    return undeclared_paths(changed, INTENTIONAL_DIFFERENCES)


def test_only_declared_differences_are_tolerated():
    assert _undeclared({"type", "errors.is_hidden[0]", "developer_message.error"}) == set()
    assert _undeclared({"[0].name", "results", "typed"}) == {"[0].name", "results", "typed"}


class _ParityMixin:
    """
    v1 against v0 for the same course and caller: listings return the same
    pages, reorders and visibility changes store the same tabs, and errors
    differ only by the declared differences.
    """

    def manager_for(self, course):
        """Return a user who may view and change the course's pages and resources."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user with no access to the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.keys = add_custom_pages(self.course, 3)
        self.manager = self.manager_for(self.course)
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

    def assert_same_pages(self):
        client = api_client(self.manager)
        legacy = client.get(legacy_list_url(self.course.id))
        new = client.get(list_url(), list_query(self.course.id, page_size=100))
        assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
        legacy_pages, envelope = legacy.json(), new.json()
        assert changed_paths(legacy_pages, envelope["results"]) == set(), (legacy_pages, envelope["results"])
        assert [page["id"] for page in envelope["results"]] == self.keys
        assert envelope["count"] == len(legacy_pages) == 3

    def test_the_list_returns_the_same_custom_pages_on_both_sides_of_the_pages_and_resources_flag(self):
        for pages_and_resources_page in (True, False):
            with override_waffle_flag(ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND, active=pages_and_resources_page):
                assert bool(get_pages_and_resources_url(self.course.id)) is pages_and_resources_page
                self.assert_same_pages()

    def test_reorders_store_the_same_tabs(self):
        """The full order, and an order naming all pages but one, which v0 also places deterministically."""
        client = api_client(self.manager)
        for wanted in ([self.keys[2], self.keys[0], self.keys[1]], [self.keys[1], self.keys[2]]):
            new = client.put(order_url(self.course.id), {"custom_pages": wanted}, format="json")
            stored_by_v1 = stored_tab_ids(self.course.id)
            original = [{"tab_locator": key} for key in self.keys]
            restore = client.post(legacy_order_url(self.course.id), original, format="json")
            assert restore.status_code == 204
            assert custom_page_keys(self.course.id) == self.keys
            legacy = client.post(legacy_order_url(self.course.id), [{"tab_locator": k} for k in wanted], format="json")
            self.compare(legacy, new, 204)
            assert stored_tab_ids(self.course.id) == stored_by_v1
            assert custom_page_keys(self.course.id)[:len(wanted)] == wanted
            client.put(order_url(self.course.id), {"custom_pages": self.keys}, format="json")

    def test_visibility_changes_store_the_same_tab(self):
        client = api_client(self.manager)
        assert client.patch(tab_url(self.course.id), {"is_hidden": True}, format="json").status_code == 200
        hidden_by_v1 = stored_tab(self.course.id, HIDEABLE_TAB).to_json()
        assert client.patch(tab_url(self.course.id), {"is_hidden": False}, format="json").status_code == 200
        assert stored_tab(self.course.id, HIDEABLE_TAB).is_hidden is False
        assert client.post(legacy_settings_url(self.course.id), {"is_hidden": True}, format="json").status_code == 204
        assert stored_tab(self.course.id, HIDEABLE_TAB).to_json() == hidden_by_v1
        assert hidden_by_v1["is_hidden"] is True

    def _error_pairs(self):
        """``(legacy, new, status)`` for each error both versions produce."""
        missing_page = str(self.course.id.make_usage_key("static_tab", "no_such_page"))
        cases = [
            (api_client(), "get", (legacy_list_url(self.course.id), None), (list_url(self.course.id), None), 401),
            (api_client(self.outsider()), "get", (legacy_list_url(self.course.id), None),
             (list_url(self.course.id), None), 403),
            (api_client(self.outsider()), "get", (legacy_list_url(UNKNOWN_COURSE_KEY), None),
             (list_url(UNKNOWN_COURSE_KEY), None), 404),
            (api_client(self.outsider()), ("post", "put"), (legacy_order_url(self.course.id), []),
             (order_url(self.course.id), {"custom_pages": []}), 403),
            (api_client(self.outsider()), ("post", "patch"), (legacy_settings_url(self.course.id), {"is_hidden": True}),
             (tab_url(self.course.id), {"is_hidden": True}), 403),
            (api_client(self.manager), ("post", "put"), (legacy_order_url(self.course.id), [{"tab_id": "no_such_tab"}]),
             (order_url(self.course.id), {"custom_pages": [missing_page]}), 400),
            (api_client(self.manager), ("post", "patch"),
             (legacy_settings_url(self.course.id, UNHIDEABLE_TAB), {"is_hidden": True}),
             (tab_url(self.course.id, UNHIDEABLE_TAB), {"is_hidden": True}), 400),
        ]
        for client, methods, (legacy_address, legacy_body), (new_address, new_body), status in cases:
            legacy_method, new_method = (methods, methods) if isinstance(methods, str) else methods
            legacy = getattr(client, legacy_method)(legacy_address, legacy_body, **self._json(legacy_body))
            new = getattr(client, new_method)(new_address, new_body, **self._json(new_body))
            yield legacy, new, status

    @staticmethod
    def _json(body):
        """Send ``body`` as JSON when there is one."""
        return {"format": "json"} if body is not None else {}

    def test_errors_differ_only_by_the_envelope(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)

    def test_a_refused_visibility_change_names_is_hidden_with_a_fixed_message(self):
        *_, (legacy, new, status) = self._error_pairs()
        legacy_body, new_body = self.compare(legacy, new, status)
        assert new_body["errors"] == {"is_hidden": [TAB_NOT_HIDEABLE_MESSAGE]}
        assert legacy_body["developer_message"] == {"error": f"Tab of type {UNHIDEABLE_TAB} can not be hidden"}
        assert legacy_body["developer_message"]["error"] not in new.content.decode()

    def test_every_declared_difference_occurs(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in self.observed)
        ]
        assert missing == []


class CustomPagesParityLegacyPermissionsTest(_ParityMixin, ModuleStoreTestCase):
    """Parity for course staff, with policy-based authoring permissions off."""

    def manager_for(self, course):
        manager = UserFactory()
        CourseStaffRole(course.id).add_users(manager)
        return manager

    def outsider(self):
        return UserFactory()


class CustomPagesParityAuthzTest(_ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Parity for a course staff role, with policy-based authoring permissions on."""

    def manager_for(self, course):
        manager = UserFactory()
        self.add_user_to_role_in_course(manager, COURSE_STAFF.external_key, course.id)
        return manager

    def outsider(self):
        return self.unauthorized_user


# ---------------------------------------------------------------------------
# Side effects and cost of listing
# ---------------------------------------------------------------------------

READS = [("get", {}), ("get", {"page_size": 2}), ("get", {"page_size": 2, "page": 2}), ("head", {})]


@ddt.ddt
class _ListingIsSideEffectFreeMixin:
    """
    Every listing (default, paged, and HEAD), with Studio's pages and resources
    page on and off, writes no row and no Mongo document, stores nothing
    through the modulestore and sends no signal or event. The listings start
    on a database that has never served a role or permission lookup, so the
    first may insert the permission library's one policy version row and
    nothing else; v0 inserts the same row.
    """

    def reader_for(self, course):
        """Return a user who may view ``course``, leaving the database as no lookup had touched it."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        add_custom_pages(self.course, 3)

    @ddt.data(True, False)
    def test_listings_have_no_side_effects(self, pages_and_resources_page):
        with override_waffle_flag(ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND, active=pages_and_resources_page):
            assert bool(get_pages_and_resources_url(self.course.id)) is pages_and_resources_page
            client = api_client(self.reader_for(self.course))
            assert not PolicyCacheControl.objects.exists()
            for index, (method, params) in enumerate(READS):
                with record_effects(self) as effects:
                    response = getattr(client, method)(list_url(), list_query(self.course.id, **params))
                read = (index, method, params)
                first = index == 0
                assert response.status_code == 200, (read, response.content)
                assert effects["sql_writes"] == (FIRST_LOOKUP_WRITES if first else []), read
                assert effects["signals"] == (FIRST_LOOKUP_SIGNALS if first else []), read
                assert effects["events"] == [], read
                assert effects["mongo_writes"] == 0, read
                assert effects["update_item"] == [], read

    def test_the_v0_listing_makes_the_same_first_lookup_write(self):
        client = api_client(self.reader_for(self.course))
        with record_effects(self) as effects:
            response = client.get(legacy_list_url(self.course.id))
        assert response.status_code == 200, response.content
        assert effects["sql_writes"] == FIRST_LOOKUP_WRITES
        assert effects["signals"] == FIRST_LOOKUP_SIGNALS
        assert effects["mongo_writes"] == 0


class CustomPagesListingSideEffectsLegacyPermissionsTest(_ListingIsSideEffectFreeMixin, ModuleStoreTestCase):
    """Listings by course staff with policy-based authoring permissions off."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        PolicyCacheControl.objects.all().delete()
        return reader


class CustomPagesListingSideEffectsAuthzTest(
    _ListingIsSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """
    Listings by a course auditor with policy-based authoring permissions on.
    The role is assigned through the permission library, which creates the
    policy version row; it is deleted before the listings.
    """

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        PolicyCacheControl.objects.all().delete()
        return reader


class _ListingCostMixin:
    """A listing costs no more SQL queries or Mongo reads than the v0 listing of the same pages."""

    def reader_for(self, course):
        raise NotImplementedError

    def test_a_listing_costs_no_more_than_the_v0_listing(self):
        course = CourseFactory.create()
        add_custom_pages(course, 3)
        client = api_client(self.reader_for(course))
        for pages_and_resources_page in (True, False):
            with override_waffle_flag(ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND, active=pages_and_resources_page):
                legacy = measure(lambda: client.get(legacy_list_url(course.id)))
                new = measure(lambda: client.get(list_url(), list_query(course.id, page_size=100)))
            assert new[0] <= legacy[0], (pages_and_resources_page, new, legacy)
            assert new[1] <= legacy[1], (pages_and_resources_page, new, legacy)
            assert new[2] == legacy[2] == 0, (pages_and_resources_page, new, legacy)

    def test_the_sql_cost_does_not_grow_with_the_number_of_pages(self):
        course = CourseFactory.create()
        add_custom_page_entries(course.id, 3)
        client = api_client(self.reader_for(course))

        def queries():
            client.get(list_url(), list_query(course.id, page_size=100))
            with CaptureQueriesContext(connection) as captured:
                assert client.get(list_url(), list_query(course.id, page_size=100)).status_code == 200
            return len(captured.captured_queries)

        few = queries()
        add_custom_page_entries(course.id, 60)
        assert queries() == few


class CustomPagesListingCostLegacyPermissionsTest(_ListingCostMixin, ModuleStoreTestCase):
    """Costs for course staff, with policy-based authoring permissions off."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class CustomPagesListingCostAuthzTest(CourseAuthoringAuthzTestMixin, _ListingCostMixin, ModuleStoreTestCase):
    """Costs for a course auditor, with policy-based authoring permissions on."""

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        return reader


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

ADDRESSES = {"list": list_url, "order": order_url, "tab": tab_url}


@ddt.ddt
class CustomPagesRoutingTest(SharedModuleStoreTestCase):
    """The addresses, the v0 addresses, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        add_custom_pages(cls.course, 1)

    def setUp(self):
        super().setUp()
        self.client = api_client(UserFactory(is_staff=True))

    def test_the_addresses(self):
        assert list_url() == "/api/authoring/v1/custom_pages/"
        assert list_url(COURSE_KEY) == (
            "/api/authoring/v1/custom_pages/?course_key=course-v1%3AedX%2BDemoX%2BDemo_Course"
        )
        assert order_url(COURSE_KEY) == f"/api/authoring/v1/courses/{COURSE_KEY}/custom_page_order/"
        assert tab_url(COURSE_KEY, "wiki") == f"/api/authoring/v1/courses/{COURSE_KEY}/tabs/wiki/"
        for address, viewset, actions in (
            (list_url(), CourseCustomPagesViewSet, {"get": "list"}),
            (order_url(COURSE_KEY), CourseCustomPageOrderViewSet, {"put": "update"}),
            (tab_url(COURSE_KEY, "wiki"), CourseTabsViewSet, {"patch": "partial_update"}),
        ):
            match = resolve(address)
            assert match.func.cls is viewset
            assert routed_actions(match.func) == actions
        assert resolve(tab_url(COURSE_KEY, "static_tab_abc")).kwargs["tab_id"] == "static_tab_abc"

    def test_the_v0_addresses_are_unchanged(self):
        assert legacy_list_url(COURSE_KEY) == f"/api/contentstore/v0/tabs/{COURSE_KEY}"
        assert legacy_order_url(COURSE_KEY) == f"/api/contentstore/v0/tabs/{COURSE_KEY}/reorder"
        settings_address = reverse(LEGACY_SETTINGS_URL_NAME, kwargs={"course_id": COURSE_KEY})
        assert settings_address == f"/api/contentstore/v0/tabs/{COURSE_KEY}/settings"
        for address, view_name in (
            (legacy_list_url(COURSE_KEY), "CourseTabListView"),
            (legacy_order_url(COURSE_KEY), "CourseTabReorderView"),
            (settings_address, "CourseTabSettingsView"),
        ):
            view = resolve(address).func.cls
            assert (view.__module__, view.__name__) == (LEGACY_MODULE, view_name)

    def test_v0_still_takes_a_slash_separated_key_that_v1_does_not(self):
        view = resolve("/api/contentstore/v0/tabs/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == (LEGACY_MODULE, "CourseTabListView")
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v1/courses/edX/DemoX/Demo_Course/custom_pages/")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v1 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert {"custom_page_list", "course_custom_page_order", "course_tab_detail"} <= set(names)

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data(
        *[("list", method) for method in ("post", "put", "patch", "delete")],
        *[("order", method) for method in ("get", "post", "patch", "delete")],
        *[("tab", method) for method in ("get", "put", "post", "delete")],
    )
    @ddt.unpack
    def test_other_methods_are_not_allowed(self, address, method):
        response = getattr(self.client, method)(ADDRESSES[address](self.course.id), {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_unsupported_accept_is_refused_with_406(self):
        response = self.client.get(list_url(self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # Nor ParseError or UnsupportedMediaType: a malformed or unsupported body is
    # answered with its own status but typed internal. Change the expected type
    # here once the library catalogues them.

    @ddt.data(("order", "put"), ("tab", "patch"))
    @ddt.unpack
    def test_a_malformed_json_body_is_a_400_typed_internal(self, address, method):
        before = stored_tab_ids(self.course.id)
        response = getattr(self.client, method)(
            ADDRESSES[address](self.course.id), data="{", content_type="application/json",
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="internal")
        assert envelope["title"] == "Internal Server Error"
        assert stored_tab_ids(self.course.id) == before

    @ddt.data(("order", "put"), ("tab", "patch"))
    @ddt.unpack
    def test_an_unsupported_media_type_is_a_415_typed_internal(self, address, method):
        before = stored_tab(self.course.id, HIDEABLE_TAB).is_hidden
        response = getattr(self.client, method)(
            ADDRESSES[address](self.course.id), data="is_hidden=true", content_type="text/plain",
        )
        envelope = assert_error_envelope(response, expected_status=415, expected_type_slug="internal")
        assert envelope["title"] == "Internal Server Error"
        assert stored_tab(self.course.id, HIDEABLE_TAB).is_hidden is before

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data(*[
        (key, address)
        for key in ("not-a-course-key", "edX/DemoX/Demo_Course", "edX+DemoX+Demo_Course")
        for address in ("custom_page_order/", "tabs/progress/")
    ])
    @ddt.unpack
    def test_an_unparsable_or_slash_form_key_is_an_html_404(self, key, address):
        response = self.client.get(f"/api/authoring/v1/courses/{key}/{address}")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_list_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read, query kept."""
        address = list_url(self.course.id).replace("custom_pages/?", "custom_pages?")
        response = self.client.get(address)
        assert response.status_code == 301
        assert response["Location"] == list_url(self.course.id)
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v1/custom_pages")

    def test_the_course_is_no_longer_part_of_the_list_address(self):
        with pytest.raises(Resolver404):
            resolve(f"/api/authoring/v1/courses/{COURSE_KEY}/custom_pages/")


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

class CustomPagesSchemaTest(TestCase):
    """The listing, the reorder and the tab change as published in the CMS authoring schema."""

    LIST_PATH = "/api/authoring/v1/custom_pages/"
    ORDER_PATH = "/api/authoring/v1/courses/{course_key}/custom_page_order/"
    TAB_PATH = "/api/authoring/v1/courses/{course_key}/tabs/{tab_id}/"
    LEGACY = {
        "/api/contentstore/v0/tabs/{course_id}": ("get", "v0_tabs_retrieve"),
        "/api/contentstore/v0/tabs/{course_id}/reorder": ("post", "v0_tabs_reorder_create"),
        "/api/contentstore/v0/tabs/{course_id}/settings": ("post", "v0_tabs_settings_create"),
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = published_schema()
        cls.components = cls.schema["components"]["schemas"]

    @staticmethod
    def _ref(node):
        return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def _operation(self, path, method):
        operations = self.schema["paths"][path]
        assert sorted(operations) == [method]
        return operations[method]

    def test_the_three_operations_are_published_for_the_sdk(self):
        for path, method, operation_id, statuses in (
            (self.LIST_PATH, "get", "v1_custom_pages_list", ["200", "400", "401", "403", "404"]),
            (self.ORDER_PATH, "put", "v1_courses_custom_page_order_update", ["204", "400", "401", "403", "404"]),
            (self.TAB_PATH, "patch", "v1_courses_tabs_partial_update", ["200", "400", "401", "403", "404"]),
        ):
            operation = self._operation(path, method)
            assert operation["operationId"] == operation_id
            assert operation["tags"] == ["openedx-platform-sdk"]
            assert "deprecated" not in operation
            assert sorted(operation["responses"]) == statuses
            for status in statuses:
                if status.startswith("4"):
                    assert self._ref(operation["responses"][status]) == "ErrorResponse", (path, status)

    def test_the_list_publishes_the_seven_envelope_keys(self):
        operation = self._operation(self.LIST_PATH, "get")
        name = self._ref(operation["responses"]["200"])
        assert name == "PaginatedCustomPage.v1List"
        envelope = self.components[name]
        assert list(envelope["properties"]) == list(ENVELOPE_KEYS)
        assert envelope["required"] == list(ENVELOPE_KEYS)
        assert envelope["properties"]["results"] == {
            "type": "array", "items": {"$ref": "#/components/schemas/CustomPage.v1"},
        }

    def test_the_list_parameters(self):
        parameters = {p["name"]: p for p in self._operation(self.LIST_PATH, "get")["parameters"]}
        assert set(parameters) == {"course_key", "page", "page_size"}
        assert parameters["course_key"]["in"] == "query"
        assert parameters["course_key"]["required"] is True
        assert parameters["course_key"]["schema"] == {"type": "string"}
        assert parameters["course_key"]["description"]
        for name in ("page", "page_size"):
            assert (parameters[name]["in"], parameters[name]["schema"]) == ("query", {"type": "integer"})
            assert not parameters[name].get("required")

    def test_the_reorder_documents_each_refusal_message(self):
        description = self._operation(self.ORDER_PATH, "put")["description"]
        for message in (
            NOT_A_USAGE_KEY_MESSAGE, UNKNOWN_CUSTOM_PAGE_MESSAGE, REPEATED_CUSTOM_PAGE_MESSAGE, ORDER_NOT_SAVED_MESSAGE,
        ):
            assert message in description, message

    def test_the_tab_change_says_custom_pages_cannot_be_hidden_there(self):
        description = self._operation(self.TAB_PATH, "patch")["description"]
        assert "Custom pages are among the course's tabs" in description
        assert "is_hidden is refused for them" in description
        assert TAB_NOT_HIDEABLE_MESSAGE in description

    def test_the_reorder_takes_json_naming_the_pages_and_answers_without_a_body(self):
        operation = self._operation(self.ORDER_PATH, "put")
        assert "content" not in operation["responses"]["204"]
        body = operation["requestBody"]
        assert list(body["content"]) == ["application/json"]
        assert body["required"] is True
        assert self._ref(body) == "CustomPageOrder.v1"
        order = self.components["CustomPageOrder.v1"]
        assert order["required"] == ["custom_pages"]
        assert order["properties"]["custom_pages"]["type"] == "array"
        assert order["properties"]["custom_pages"]["items"]["type"] == "string"

    def test_the_tab_change_takes_an_optional_is_hidden_and_returns_the_tab(self):
        operation = self._operation(self.TAB_PATH, "patch")
        parameters = {p["name"]: p for p in operation["parameters"]}
        assert set(parameters) == {"course_key", "tab_id"}
        assert parameters["tab_id"]["schema"] == {"type": "string"}
        body = operation["requestBody"]
        assert list(body["content"]) == ["application/json"]
        assert self._ref(body) == "PatchedCourseTabUpdate.v1"
        update = self.components["PatchedCourseTabUpdate.v1"]
        assert list(update["properties"]) == ["is_hidden"]
        assert update["properties"]["is_hidden"]["type"] == "boolean"
        assert "required" not in update
        assert self._ref(operation["responses"]["200"]) == "CourseTab.v1"

    def test_the_tab_and_page_components(self):
        tab, page = self.components["CourseTab.v1"], self.components["CustomPage.v1"]
        assert set(tab["properties"]) == set(tab["required"]) == TAB_MEMBERS
        assert set(page["properties"]) == set(page["required"]) == PAGE_MEMBERS
        for component in (tab, page):
            assert {name for name, field in component["properties"].items() if field.get("nullable")} == {"title"}
            assert component["properties"]["settings"]["type"] == "object"

    def test_every_member_is_described(self):
        for name in ("CourseTab.v1", "CustomPage.v1", "CustomPageOrder.v1", "PatchedCourseTabUpdate.v1"):
            component = self.components[name]
            assert component.get("description"), name
            assert [field for field, spec in component["properties"].items() if not spec.get("description")] == [], name

    def test_the_examples_are_valid_bodies(self):
        listing = self._operation(self.LIST_PATH, "get")["responses"]["200"]["content"]["application/json"]
        page = listing["examples"]["CustomPage"]["value"]
        assert set(page) == set(ENVELOPE_KEYS)
        assert len(page["results"]) == 1
        assert_matches_published_schema(page, "PaginatedCustomPage.v1List")
        order = self._operation(self.ORDER_PATH, "put")["requestBody"]["content"]["application/json"]
        assert_matches_published_schema(order["examples"]["NewOrder"]["value"], "CustomPageOrder.v1")
        change = self._operation(self.TAB_PATH, "patch")
        hide = change["requestBody"]["content"]["application/json"]["examples"]["Hide"]["value"]
        assert_matches_published_schema(hide, "PatchedCourseTabUpdate.v1")
        tab = change["responses"]["200"]["content"]["application/json"]["examples"]["ProgressTab"]["value"]
        assert_matches_published_schema(tab, "CourseTab.v1")

    def test_the_v0_operations_are_deprecated_and_left_out_of_the_sdk(self):
        for path, (method, operation_id) in self.LEGACY.items():
            operation = self._operation(path, method)
            assert operation["operationId"] == operation_id
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
        assert {(path, method) for path, (method, _) in self.LEGACY.items()} <= deprecated
        new = {(self.LIST_PATH, "get"), (self.ORDER_PATH, "put"), (self.TAB_PATH, "patch")}
        assert new & deprecated == set()
        stray = sorted(
            (path, method) for path, method in deprecated
            if path not in SUPERSEDED_PATHS and not path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        )
        assert stray == []
