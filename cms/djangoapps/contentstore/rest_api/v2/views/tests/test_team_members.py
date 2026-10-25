"""Tests for the v2 course team members API."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import ddt
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from opaque_keys.edx.keys import CourseKey
from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_LIMITED_STAFF,
    COURSE_STAFF,
)
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from cms.djangoapps.contentstore.rest_api.pagination import ENVELOPE_KEYS, DocumentedDefaultPagination
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
from cms.djangoapps.contentstore.rest_api.v2.views.team_members import CourseTeamMembersViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import resolve_ref
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.views.permissions import CanViewCourseTeam, IsExistingCourse
from cms.lib.spectacular import LEGACY_MIGRATED_PATH_PREFIXES, SUPERSEDED_PATHS
from common.djangoapps.student.auth import (
    STUDIO_EDIT_CONTENT,
    STUDIO_EDIT_ROLES,
    STUDIO_NO_PERMISSIONS,
    STUDIO_VIEW_CONTENT,
    STUDIO_VIEW_USERS,
)
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.tests.fixtures import seed_policies
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, LibraryFactory

LIST_URL_NAME = "authoring_v2:course_team_member_list"
ME_URL_NAME = "authoring_v2:course_team_member_me"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:course_team"
LEGACY_MODULE = "cms.djangoapps.contentstore.rest_api.v1.views.course_team"
COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"
ACTIONS = ("list", "me")
# The roles in the order members are listed.
LISTED_ORDER = ("instructor", "staff")
MEMBER_MEMBERS = {"id", "username", "email", "role"}
CALLER_MEMBERS = {"username", "email", "role", "can_manage_team", "is_only_instructor"}
OK = 200


def list_url(course_key):
    """Return the address of the members of the team of ``course_key``."""
    return reverse(LIST_URL_NAME, kwargs={"course_key": str(course_key)})


def me_url(course_key):
    """Return the address of the caller's place on the team of ``course_key``."""
    return reverse(ME_URL_NAME, kwargs={"course_key": str(course_key)})


def legacy_url(course_key):
    """Return the deprecated course team address of ``course_key``."""
    return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key)})


def in_listed_order(members):
    """``members`` in the order the team is listed: instructors, then staff, each by username."""
    return sorted(members, key=lambda member: (LISTED_ORDER.index(member["role"]), member["username"]))


def caller_from_legacy(legacy_body, user):
    """The ``me`` body the legacy course team body implies for ``user``."""
    role = next((member["role"] for member in legacy_body["users"] if member["id"] == user.id), None)
    return {
        "username": user.username,
        "email": user.email,
        "role": role,
        "can_manage_team": legacy_body["allow_actions"],
        "is_only_instructor": legacy_body["show_transfer_ownership_hint"],
    }


def member(user, role):
    """The listing entry of ``user`` holding ``role``."""
    return {"id": user.id, "username": user.username, "email": user.email, "role": role}


def legacy_team(course_key, role):
    """Add a new user to the team of ``course_key`` through Studio's course roles; ``role`` is the role class."""
    user = UserFactory()
    role(course_key).add_users(user)
    return user


# ---------------------------------------------------------------------------
# Who may call the endpoints
# ---------------------------------------------------------------------------

class _AccessMixin:
    """
    Checks a caller's status on each action against the expected one, and
    that the legacy endpoint decides the same for the same caller. On success
    the listing holds what the legacy endpoint holds, in listed order, and
    ``me`` says what the legacy body says about the caller.
    """

    def assert_list(self, user, expected, course_key=None):
        """Assert the status of a listing by ``user``, its body or envelope, and agreement with the legacy endpoint."""
        course_key = course_key or self.course.id
        client = api_client(user)
        response = client.get(list_url(course_key), {"page_size": 100})
        legacy = client.get(legacy_url(course_key))
        assert response.status_code == expected, response.content
        assert legacy.status_code == expected, f"the legacy endpoint decided {legacy.status_code} for the same caller"
        if expected == OK:
            results = response.json()["results"]
            assert results == in_listed_order(legacy.json()["users"])
            assert {entry["username"] for entry in results} >= {self.instructor.username, self.staff.username}
        else:
            assert_refused(response, expected)

    def assert_me(self, user, expected, course_key=None):
        """Assert the status of ``me`` for ``user``, its body or envelope, and agreement with the legacy endpoint."""
        course_key = course_key or self.course.id
        client = api_client(user)
        response = client.get(me_url(course_key))
        legacy = client.get(legacy_url(course_key))
        assert response.status_code == expected, response.content
        assert legacy.status_code == expected, f"the legacy endpoint decided {legacy.status_code} for the same caller"
        if expected == OK:
            assert response.json() == caller_from_legacy(legacy.json(), user)
        else:
            assert_refused(response, expected)

    def assert_access(self, user, action, expected, course_key=None):
        """Assert ``expected`` for ``action`` (``list`` or ``me``) by ``user``."""
        getattr(self, f"assert_{action}")(user, expected, course_key)

    def me(self, user):
        """Return the ``me`` body for ``user`` on the course."""
        response = api_client(user).get(me_url(self.course.id))
        assert response.status_code == 200, response.content
        return response.json()


@ddt.ddt
class TeamMembersAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may see a course's team when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.instructor = legacy_team(self.course.id, CourseInstructorRole)
        self.staff = legacy_team(self.course.id, CourseStaffRole)

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    def _user_with_policy_role(self, role):
        """Return a new user holding ``role`` on the course through policy-based permissions only."""
        seed_policies()
        self.addCleanup(AuthzEnforcer.get_enforcer().clear_policy)
        user = UserFactory()
        assign_role_to_user_in_scope(user.username, role, str(self.course.id))
        AuthzEnforcer.get_enforcer().load_policy()
        assert not CourseAccessRole.objects.filter(user=user).exists()
        return user

    @staticmethod
    def _policy_permissions_off():
        return patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, "is_enabled", return_value=False)

    @ddt.data(*ACTIONS)
    def test_anonymous_caller_is_refused_with_401(self, action):
        self.assert_access(None, action, 401)

    @ddt.data(*ACTIONS)
    def test_a_policy_course_staff_role_opens_the_team_without_listing_its_holder(self, action):
        """
        A course staff role assigned through policy-based permissions counts as
        Studio's staff role even while those permissions are off for the
        course, as on the legacy endpoint. Its holder is not listed, because
        the team is then read from Studio's own role records.
        """
        user = self._user_with_policy_role(COURSE_STAFF.external_key)
        with self._policy_permissions_off():
            self.assert_access(user, action, OK)
            listed = api_client(user).get(list_url(self.course.id)).json()["results"]
            assert listed == [member(self.instructor, "instructor"), member(self.staff, "staff")]
            assert self.me(user) == {
                "username": user.username, "email": user.email, "role": None,
                "can_manage_team": False, "is_only_instructor": False,
            }

    @ddt.data(*[(action, role) for action in ACTIONS for role in (
        COURSE_EDITOR.external_key,
        COURSE_AUDITOR.external_key,
    )])
    @ddt.unpack
    def test_a_policy_role_without_a_studio_course_role_is_refused(self, action, role):
        """The policy lets these roles view the team, but they stand for no Studio course role."""
        user = self._user_with_policy_role(role)
        with self._policy_permissions_off():
            self.assert_access(user, action, 403)

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
        self.assert_access(self.staff, action, OK)

    @ddt.data(*ACTIONS)
    def test_course_instructor_is_allowed(self, action):
        self.assert_access(self.instructor, action, OK)

    @ddt.data(*ACTIONS)
    def test_org_staff_and_org_instructor_are_allowed(self, action):
        self.assert_access(self._user_with(OrgStaffRole(self.course.id.org)), action, OK)
        self.assert_access(self._user_with(OrgInstructorRole(self.course.id.org)), action, OK)

    @ddt.data(*ACTIONS)
    def test_global_staff_is_allowed(self, action):
        self.assert_access(UserFactory(is_staff=True), action, OK)

    @ddt.data(*ACTIONS)
    def test_a_superuser_is_allowed_as_global_staff(self, action):
        """Saving a superuser makes them global staff, so one created without staff status still has it."""
        user = UserFactory(is_superuser=True, is_staff=False)
        assert user.is_staff is True
        self.assert_access(user, action, OK)

    def test_course_staff_may_see_but_not_manage_the_team(self):
        assert self.me(self.staff) == {
            "username": self.staff.username,
            "email": self.staff.email,
            "role": "staff",
            "can_manage_team": False,
            "is_only_instructor": False,
        }

    def test_the_only_instructor_may_manage_the_team_and_is_told_so(self):
        assert self.me(self.instructor) == {
            "username": self.instructor.username,
            "email": self.instructor.email,
            "role": "instructor",
            "can_manage_team": True,
            "is_only_instructor": True,
        }

    def test_one_of_two_instructors_is_not_the_only_one(self):
        second = legacy_team(self.course.id, CourseInstructorRole)
        for instructor in (self.instructor, second):
            body = self.me(instructor)
            assert (body["role"], body["can_manage_team"], body["is_only_instructor"]) == ("instructor", True, False)

    def test_global_staff_without_a_role_may_manage_the_team_and_holds_no_role_on_it(self):
        user = UserFactory(is_staff=True)
        assert self.me(user) == {
            "username": user.username, "email": user.email, "role": None,
            "can_manage_team": True, "is_only_instructor": False,
        }

    def test_org_roles_are_not_team_roles(self):
        """Org staff and instructors may see the team without being listed on it; only instructors may manage it."""
        for role, can_manage in ((OrgStaffRole, False), (OrgInstructorRole, True)):
            user = self._user_with(role(self.course.id.org))
            body = self.me(user)
            assert (body["role"], body["can_manage_team"], body["is_only_instructor"]) == (None, can_manage, False)

    def test_a_version_only_course_key_is_404(self):
        """The key parses but names no course run; the legacy endpoint has no address that takes it."""
        user = UserFactory(is_staff=True)
        assert_refused(api_client(user).get(list_url(VERSION_ONLY_KEY)), 404)
        assert_refused(api_client(user).get(me_url(VERSION_ONLY_KEY)), 404)

    def test_a_library_is_not_a_course(self):
        library_key = LibraryFactory.create().location.library_key
        user = UserFactory(is_staff=True)
        assert_refused(api_client(user).get(list_url(library_key)), 404)
        assert_refused(api_client(user).get(me_url(library_key)), 404)


@ddt.ddt
class TeamMembersAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    Who may see a course's team when policy-based authoring permissions are
    on. The team is then the holders of the course admin and course staff
    policy roles, and those roles count as the instructor and staff course
    roles; no other policy role or permission opens the team.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.instructor = self._user_with(COURSE_ADMIN.external_key)
        self.staff = self._user_with(COURSE_STAFF.external_key)

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

    @ddt.data(*ACTIONS)
    def test_the_course_admin_and_course_staff_roles_are_allowed(self, action):
        self.assert_access(self.instructor, action, OK)
        self.assert_access(self.staff, action, OK)

    def test_the_course_admin_role_is_listed_as_instructor_and_may_manage_the_team(self):
        body = self.me(self.instructor)
        assert (body["role"], body["can_manage_team"], body["is_only_instructor"]) == ("instructor", True, True)
        body = self.me(self.staff)
        assert (body["role"], body["can_manage_team"], body["is_only_instructor"]) == ("staff", False, False)
        listed = api_client(self.staff).get(list_url(self.course.id)).json()["results"]
        assert listed == [member(self.instructor, "instructor"), member(self.staff, "staff")]

    @ddt.data(*[(action, role) for action in ACTIONS for role in (
        COURSE_AUDITOR.external_key,
        COURSE_EDITOR.external_key,
        COURSE_LIMITED_STAFF.external_key,
        COURSE_DATA_RESEARCHER.external_key,
        COURSE_BETA_TESTER.external_key,
    )])
    @ddt.unpack
    def test_other_roles_are_refused(self, action, role):
        self.assert_access(self._user_with(role), action, 403)

    @ddt.data(*ACTIONS)
    def test_a_role_on_another_course_is_refused(self, action):
        self.assert_access(self._user_with(COURSE_ADMIN.external_key, self.other_course.id), action, 403)

    @ddt.data(*ACTIONS)
    def test_a_studio_staff_role_still_opens_the_team_without_listing_its_holder(self, action):
        """Studio's own staff role still counts, as on the legacy endpoint, but only policy roles are listed."""
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_access(user, action, OK)
        listed = api_client(user).get(list_url(self.course.id)).json()["results"]
        assert user.username not in {entry["username"] for entry in listed}
        assert self.me(user)["role"] is None

    @ddt.data(*ACTIONS)
    def test_staff_and_superuser_are_allowed_without_a_role(self, action):
        """Global staff need no role; a superuser counts as global staff because saving one sets staff status."""
        assert self.super_user.is_staff is True
        self.assert_access(self.staff_user, action, OK)
        self.assert_access(self.super_user, action, OK)

    def test_an_inactive_session_with_a_role_is_refused(self):
        """A course role does not count while the account is inactive, on v2 as on the legacy endpoint."""
        client = session_client(self.staff)
        self.staff.is_active = False
        self.staff.save()
        assert_refused(client.get(list_url(self.course.id)), 403)
        assert_refused(client.get(me_url(self.course.id)), 403)
        assert client.get(legacy_url(self.course.id)).status_code == 403


class TeamMembersPermissionWiringTest(SimpleTestCase):
    """Which permissions the viewset checks, and what the team permission asks for."""

    @staticmethod
    def _declared(view):
        # The JWT middleware appends NotJwtRestrictedApplication to views that take JWTs.
        return [
            type(permission) for permission in view.get_permissions()
            if not isinstance(permission, NotJwtRestrictedApplication)
        ]

    def test_both_actions_check_the_course_then_the_permission_to_see_the_team(self):
        view = CourseTeamMembersViewSet()
        for action in ("list", "retrieve", None, "metadata"):
            view.action = action
            assert self._declared(view) == [IsAuthenticated, IsExistingCourse, CanViewCourseTeam], action

    def test_any_other_action_is_refused(self):
        view = CourseTeamMembersViewSet()
        for action in ("create", "update", "partial_update", "destroy", "anything_else"):
            view.action = action
            with pytest.raises(ImproperlyConfigured):
                view.get_permissions()

    def test_a_routed_action_the_viewset_does_not_check_is_never_served(self):
        """A request reaching such an action is answered with a server error, not the handler's body."""

        class Unchecked(CourseTeamMembersViewSet):
            permission_classes = CourseTeamMembersViewSet.permission_classes

            def destroy(self, request, **kwargs):  # pylint: disable=unused-argument
                raise AssertionError("the handler ran without a permission check")

        request = APIRequestFactory().delete(f"/api/authoring/v2/courses/{COURSE_KEY}/team_members/me/")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_active=True))
        response = Unchecked.as_view({"delete": "destroy"})(request, course_key=CourseKey.from_string(COURSE_KEY))
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")

    def test_the_team_permission_needs_studios_permission_to_see_users(self):
        course_key = CourseKey.from_string("course-v1:edX+Wiring+Run")
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={"course_key": course_key})
        target = "cms.djangoapps.contentstore.views.permissions.get_user_permissions"
        for bits, decision in (
            (STUDIO_EDIT_ROLES | STUDIO_VIEW_USERS | STUDIO_EDIT_CONTENT | STUDIO_VIEW_CONTENT, True),
            (STUDIO_VIEW_USERS | STUDIO_EDIT_CONTENT | STUDIO_VIEW_CONTENT, True),
            (STUDIO_VIEW_USERS | STUDIO_VIEW_CONTENT, True),
            (STUDIO_EDIT_ROLES | STUDIO_EDIT_CONTENT | STUDIO_VIEW_CONTENT, False),
            (STUDIO_EDIT_CONTENT, False),
            (STUDIO_NO_PERMISSIONS, False),
        ):
            with patch(target, return_value=bits) as check:
                assert CanViewCourseTeam().has_permission(request, view) is decision, bits
            check.assert_called_once_with(request.user, course_key)

    def test_the_team_permission_refuses_a_view_without_a_course(self):
        target = "cms.djangoapps.contentstore.views.permissions.get_user_permissions"
        request, view = SimpleNamespace(user=object()), SimpleNamespace(kwargs={})
        with patch(target, return_value=STUDIO_VIEW_USERS) as check:
            assert CanViewCourseTeam().has_permission(request, view) is False
        check.assert_not_called()


class TeamMembersAuthenticationTest(SharedModuleStoreTestCase):
    """How callers authenticate, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _course_staff(self):
        return legacy_team(self.course.id, CourseStaffRole)

    def _assert_both_actions_succeed(self, client, user, **headers):
        """Assert that ``client`` lists ``user`` as the only staff member, and that ``me`` names them as staff."""
        listed = client.get(list_url(self.course.id), **headers)
        assert listed.status_code == 200, listed.content
        assert listed.json()["results"] == [member(user, "staff")]
        me = client.get(me_url(self.course.id), **headers)
        assert me.status_code == 200, me.content
        assert (me.json()["username"], me.json()["role"]) == (user.username, "staff")

    def test_a_jwt_caller_may_list_and_see_themself(self):
        user = self._course_staff()
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}
        self._assert_both_actions_succeed(APIClient(), user, **header)

    def test_a_session_caller_may_list_and_see_themself(self):
        user = self._course_staff()
        self._assert_both_actions_succeed(session_client(user), user)

    def test_an_inactive_session_is_authenticated(self):
        """
        A session whose account is not activated is accepted, as on the legacy
        endpoint. Global staff then pass; course staff are refused (403, not
        401) because a course role does not count while the account is inactive.
        """
        for user, expected in ((UserFactory(is_staff=True), 200), (self._course_staff(), 403)):
            client = session_client(user)
            user.is_active = False
            user.save()
            for address in (list_url(self.course.id), me_url(self.course.id)):
                response = client.get(address)
                assert response.status_code == expected, response.content
                if expected == 403:
                    assert_refused(response, 403)
            assert client.get(legacy_url(self.course.id)).status_code == expected

    def test_a_bearer_token_is_refused(self):
        """Bearer tokens are not accepted here, though the legacy endpoint takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert_refused(APIClient().get(list_url(self.course.id), **header), 401)
        assert_refused(APIClient().get(me_url(self.course.id), **header), 401)
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class TeamMembersListTest(ModuleStoreTestCase):
    """What a listing returns and how it pages."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.caller = UserFactory(is_staff=True)
        self.client = api_client(self.caller)

    def _add(self, username, *roles):
        user = UserFactory(username=username)
        for role in roles:
            role(self.course.id).add_users(user)
        return user

    def get(self, course_key=None, **params):
        response = self.client.get(list_url(course_key or self.course.id), params)
        assert response.status_code == 200, response.content
        return response.json()

    def test_instructors_come_first_then_staff_each_by_username(self):
        zed = self._add("zed_instructor", CourseInstructorRole)
        amy = self._add("amy_instructor", CourseInstructorRole)
        both = self._add("carl_both", CourseStaffRole, CourseInstructorRole)
        bob = self._add("bob_staff", CourseStaffRole)
        aaron = self._add("aaron_staff", CourseStaffRole)
        assert self.get()["results"] == [
            member(amy, "instructor"),
            member(both, "instructor"),
            member(zed, "instructor"),
            member(aaron, "staff"),
            member(bob, "staff"),
        ]

    def test_each_member_carries_exactly_its_members(self):
        user = self._add("only_member", CourseStaffRole)
        (entry,) = self.get()["results"]
        assert set(entry) == MEMBER_MEMBERS
        assert entry == {"id": user.id, "username": "only_member", "email": user.email, "role": "staff"}

    def test_a_member_whose_account_is_inactive_is_still_listed(self):
        user = self._add("dormant_staff", CourseStaffRole)
        user.is_active = False
        user.save()
        assert self.get()["results"] == [member(user, "staff")]

    def test_global_staff_without_a_role_are_not_listed(self):
        staff = self._add("listed_staff", CourseStaffRole)
        assert self.caller.is_staff
        assert self.get()["results"] == [member(staff, "staff")]

    def test_members_of_another_course_are_not_listed(self):
        other = CourseFactory.create()
        outsider = UserFactory()
        CourseInstructorRole(other.id).add_users(outsider)
        staff = self._add("this_course_staff", CourseStaffRole)
        assert self.get()["results"] == [member(staff, "staff")]

    def test_the_envelope_has_the_seven_keys(self):
        self._add("enveloped", CourseStaffRole)
        body = self.get()
        assert set(body) == set(ENVELOPE_KEYS)
        assert (body["count"], body["num_pages"], body["current_page"], body["start"]) == (1, 1, 1, 0)
        assert (body["next"], body["previous"]) == (None, None)

    def test_page_size_splits_the_pages(self):
        users = [self._add(f"member_{index:02d}", CourseStaffRole) for index in range(12)]
        first, second = self.get(page_size=5), self.get(page_size=5, page=3)
        assert (first["count"], first["num_pages"], len(first["results"])) == (12, 3, 5)
        assert [entry["username"] for entry in first["results"]] == [user.username for user in users[:5]]
        assert (second["current_page"], second["start"]) == (3, 10)
        assert [entry["username"] for entry in second["results"]] == [user.username for user in users[10:]]
        assert second["next"] is None

    def test_the_default_page_size_is_ten(self):
        for index in range(12):
            self._add(f"member_{index:02d}", CourseStaffRole)
        body = self.get()
        assert (body["count"], len(body["results"]), body["num_pages"]) == (12, 10, 2)

    def test_the_page_size_is_at_most_one_hundred(self):
        users = UserFactory.create_batch(101)
        CourseStaffRole(self.course.id).add_users(*users)
        body = self.get(page_size=500)
        assert (body["count"], len(body["results"]), body["num_pages"]) == (101, 100, 2)

    def test_a_page_beyond_the_last_is_404(self):
        self._add("single", CourseStaffRole)
        response = self.client.get(list_url(self.course.id), {"page": 2})
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_a_course_without_a_team_lists_no_one(self):
        body = self.get()
        assert (body["count"], body["results"]) == (0, [])

    def test_the_body_matches_the_published_schema(self):
        self._add("schema_instructor", CourseInstructorRole)
        self._add("schema_staff", CourseStaffRole)
        assert_matches_published_schema(self.get(), "PaginatedCourseTeamMember.v2List")

    def test_head_answers_without_a_body(self):
        response = self.client.head(list_url(self.course.id))
        assert (response.status_code, response.content) == (200, b"")


class TeamMembersMeTest(ModuleStoreTestCase):
    """What ``me`` returns."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def me(self, user):
        response = api_client(user).get(me_url(self.course.id))
        assert response.status_code == 200, response.content
        return response.json()

    def test_a_user_holding_both_roles_is_an_instructor(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        CourseInstructorRole(self.course.id).add_users(user)
        body = self.me(user)
        assert set(body) == CALLER_MEMBERS
        assert (body["role"], body["can_manage_team"], body["is_only_instructor"]) == ("instructor", True, True)

    def test_global_staff_who_are_the_only_instructor_are_told_so(self):
        user = UserFactory(is_staff=True)
        CourseInstructorRole(self.course.id).add_users(user)
        body = self.me(user)
        assert (body["role"], body["can_manage_team"], body["is_only_instructor"]) == ("instructor", True, True)

    def test_the_body_matches_the_published_schema(self):
        user = legacy_team(self.course.id, CourseStaffRole)
        assert_matches_published_schema(self.me(user), "CourseTeamCaller.v2")
        assert_matches_published_schema(self.me(UserFactory(is_staff=True)), "CourseTeamCaller.v2")

    def test_head_answers_without_a_body(self):
        response = api_client(UserFactory(is_staff=True)).head(me_url(self.course.id))
        assert (response.status_code, response.content) == (200, b"")


# ---------------------------------------------------------------------------
# Requests the legacy endpoint and v2 answer differently
# ---------------------------------------------------------------------------

# Requests the legacy endpoint and v2 answer differently beyond the error body,
# as (request, legacy answer, v2 answer, test in TeamMembersResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("listing", "one object holding every member and the caller's flags", "a page of members in the standard envelope",
     "test_the_list_is_paginated"),
    ("order of members holding the same role", "no particular order", "by username",
     "test_members_holding_the_same_role_are_ordered_by_username"),
    ("whether the caller may manage the team, and is its only instructor",
     "allow_actions and show_transfer_ownership_hint on the team", "can_manage_team and is_only_instructor on me",
     "test_the_caller_flags_are_on_me"),
    ("listing with a page beyond the last", "200 with every member (no paging)", "404",
     "test_a_page_beyond_the_last"),
    ("OPTIONS by a signed-in caller without access, or for an unknown course", "200", "403 or 404",
     "test_options"),
    ("any request with a Bearer token", "200", "401", "test_a_bearer_token"),
    ("slash-separated course key", "404 JSON", "404 HTML", "test_a_slash_separated_course_key"),
]


class TeamMembersResponseDifferencesTest(ModuleStoreTestCase):
    """Each request the legacy endpoint and v2 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.instructor = legacy_team(self.course.id, CourseInstructorRole)
        self.client = api_client(self.instructor)

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_the_list_is_paginated(self):
        legacy = self.client.get(legacy_url(self.course.id)).json()
        new = self.client.get(list_url(self.course.id)).json()
        assert set(legacy) == {"show_transfer_ownership_hint", "users", "allow_actions"}
        assert set(new) == set(ENVELOPE_KEYS)
        assert legacy["users"] == new["results"] == [member(self.instructor, "instructor")]

    def test_members_holding_the_same_role_are_ordered_by_username(self):
        staff = [UserFactory(username=name) for name in ("staff_c", "staff_a", "staff_b")]
        CourseStaffRole(self.course.id).add_users(*staff)
        legacy = self.client.get(legacy_url(self.course.id)).json()["users"]
        new = self.client.get(list_url(self.course.id)).json()["results"]
        assert legacy[0] == new[0] == member(self.instructor, "instructor")
        assert sorted(entry["username"] for entry in legacy[1:]) == ["staff_a", "staff_b", "staff_c"]
        assert [entry["username"] for entry in new[1:]] == ["staff_a", "staff_b", "staff_c"]

    def test_the_caller_flags_are_on_me(self):
        legacy = self.client.get(legacy_url(self.course.id)).json()
        new = self.client.get(me_url(self.course.id)).json()
        assert (legacy["allow_actions"], legacy["show_transfer_ownership_hint"]) == (True, True)
        assert (new["can_manage_team"], new["is_only_instructor"]) == (True, True)
        listed = self.client.get(list_url(self.course.id)).json()
        assert "allow_actions" not in listed
        assert "show_transfer_ownership_hint" not in listed

    def test_a_page_beyond_the_last(self):
        legacy = self.client.get(legacy_url(self.course.id), {"page": 2})
        assert legacy.status_code == 200
        assert legacy.json()["users"] == [member(self.instructor, "instructor")]
        new = self.client.get(list_url(self.course.id), {"page": 2})
        assert_error_envelope(new, expected_status=404, expected_type_slug="not-found")

    def test_options(self):
        """The legacy endpoint answers OPTIONS to any signed-in caller; v2 checks the course and the caller first."""
        outsider = api_client(UserFactory())
        assert outsider.options(legacy_url(self.course.id)).status_code == 200
        assert outsider.options(legacy_url(UNKNOWN_COURSE_KEY)).status_code == 200
        for address in (list_url, me_url):
            assert_refused(outsider.options(address(self.course.id)), 403)
            assert_refused(outsider.options(address(UNKNOWN_COURSE_KEY)), 404)
            assert self.client.options(address(self.course.id)).status_code == 200

    def test_a_bearer_token(self):
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200
        assert_refused(APIClient().get(list_url(self.course.id), **header), 401)
        assert_refused(APIClient().get(me_url(self.course.id), **header), 401)

    def test_a_slash_separated_course_key(self):
        """The legacy endpoint routes the slash form and reports the course unknown in JSON; v2 has no route for it."""
        legacy = self.client.get("/api/contentstore/v1/course_team/edX/NoSuch/Course")
        new = self.client.get("/api/authoring/v2/courses/edX/NoSuch/Course/team_members/")
        assert legacy.status_code == new.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        assert new["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Parity with the legacy endpoint
# ---------------------------------------------------------------------------

# Members of an error body that differ between the legacy endpoint and v2 for
# the same request. The comparison asserts nothing else differs, and that each
# of these occurs in the error cases below.
INTENTIONAL_DIFFERENCES = [
    ("type", "errors use the standardized envelope"),
    ("title", "errors use the standardized envelope"),
    ("status", "errors use the standardized envelope"),
    ("detail", "errors use the standardized envelope"),
    ("developer_message", "the legacy error body carries developer_message; the envelope does not"),
    ("error_code", "the legacy not-found body carries error_code; the envelope does not"),
]


def _undeclared(changed):
    """The changed paths no declared intentional difference of this module covers."""
    return undeclared_paths(changed, INTENTIONAL_DIFFERENCES)


def test_only_declared_differences_are_tolerated():
    assert _undeclared({"type", "developer_message", "error_code"}) == set()
    assert _undeclared({"[0].role", "results", "users", "typed"}) == {"[0].role", "results", "users", "typed"}


class _ParityMixin:
    """
    v2 against the legacy endpoint for the same course and caller: the listing
    holds the legacy members, ``me`` says what the legacy body says about the
    caller, and errors differ only by the declared differences.
    """

    def add_member(self, course, role):
        """Return a new user holding ``role`` (``instructor`` or ``staff``) on ``course``."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user with no access to the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.instructors = [self.add_member(self.course, "instructor") for _ in range(2)]
        self.staff = [self.add_member(self.course, "staff") for _ in range(3)]
        self.observed = set()

    def compare(self, legacy, new, expected_status):
        """Assert both statuses and that the bodies differ only as declared."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_body, new_body = json.loads(legacy.content or b"null"), json.loads(new.content or b"null")
        changed = changed_paths(legacy_body, new_body)
        assert _undeclared(changed) == set(), f"undeclared differences: legacy={legacy_body} new={new_body}"
        self.observed |= changed

    def test_the_listing_holds_the_legacy_members_in_listed_order(self):
        for caller in (self.instructors[0], self.staff[0]):
            client = api_client(caller)
            legacy = client.get(legacy_url(self.course.id))
            new = client.get(list_url(self.course.id), {"page_size": 100})
            assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
            members, envelope = legacy.json()["users"], new.json()
            assert changed_paths(in_listed_order(members), envelope["results"]) == set()
            assert envelope["count"] == len(members) == 5
            assert [entry["role"] for entry in envelope["results"]] == ["instructor"] * 2 + ["staff"] * 3

    def test_me_says_what_the_legacy_body_says_about_the_caller(self):
        for caller in (*self.instructors, *self.staff):
            client = api_client(caller)
            legacy = client.get(legacy_url(self.course.id))
            new = client.get(me_url(self.course.id))
            assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
            assert new.json() == caller_from_legacy(legacy.json(), caller)

    def _error_pairs(self):
        """``(legacy, new, status)`` for each error both endpoints produce, for the listing and for ``me``."""
        for address in (list_url, me_url):
            for client, course_key, status in (
                (api_client(), self.course.id, 401),
                (api_client(self.outsider()), self.course.id, 403),
                (api_client(self.outsider()), UNKNOWN_COURSE_KEY, 404),
            ):
                yield client.get(legacy_url(course_key)), client.get(address(course_key)), status

    def test_errors_differ_only_by_the_envelope(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)

    def test_every_declared_difference_occurs(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in self.observed)
        ]
        assert missing == []


class TeamMembersParityLegacyPermissionsTest(_ParityMixin, ModuleStoreTestCase):
    """Parity for course instructors and staff, with policy-based authoring permissions off."""

    def add_member(self, course, role):
        return legacy_team(course.id, {"instructor": CourseInstructorRole, "staff": CourseStaffRole}[role])

    def outsider(self):
        return UserFactory()


class TeamMembersParityAuthzTest(_ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Parity for course admins and staff, with policy-based authoring permissions on."""

    def add_member(self, course, role):
        user = UserFactory()
        policy_role = {"instructor": COURSE_ADMIN, "staff": COURSE_STAFF}[role]
        self.add_user_to_role_in_course(user, policy_role.external_key, course.id)
        return user

    def outsider(self):
        return self.unauthorized_user


# ---------------------------------------------------------------------------
# Side effects and cost of reading
# ---------------------------------------------------------------------------

# Every read, by address and query, in the order the side-effects test makes them.
READS = [
    (list_url, "get", {}),
    (list_url, "get", {"page_size": 1}),
    (list_url, "get", {"page_size": 1, "page": 2}),
    (list_url, "head", {}),
    (me_url, "get", {}),
    (me_url, "head", {}),
]


class _ReadsAreSideEffectFreeMixin:
    """
    Every read (listing, paged listing, ``me``, and HEAD of both) writes no row
    and no Mongo document, stores nothing through the modulestore and sends no
    signal or event. The reads start on a database that has never served a
    role or permission lookup, so the first may insert the permission
    library's one policy version row and nothing else; the legacy endpoint
    inserts the same row.
    """

    def reader_for(self, course):
        """Return a team member of ``course``, leaving the database as no lookup had touched it."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def test_reads_have_no_side_effects(self):
        client = api_client(self.reader_for(self.course))
        assert not PolicyCacheControl.objects.exists()
        for index, (address, method, params) in enumerate(READS):
            with record_effects(self) as effects:
                response = getattr(client, method)(address(self.course.id), params)
            read = (index, address.__name__, method, params)
            first = index == 0
            assert response.status_code == 200, (read, response.content)
            assert effects["sql_writes"] == (FIRST_LOOKUP_WRITES if first else []), read
            assert effects["signals"] == (FIRST_LOOKUP_SIGNALS if first else []), read
            assert effects["events"] == [], read
            assert effects["mongo_writes"] == 0, read
            assert effects["update_item"] == [], read

    def test_the_legacy_read_makes_the_same_first_lookup_write(self):
        client = api_client(self.reader_for(self.course))
        with record_effects(self) as effects:
            response = client.get(legacy_url(self.course.id))
        assert response.status_code == 200, response.content
        assert effects["sql_writes"] == FIRST_LOOKUP_WRITES
        assert effects["signals"] == FIRST_LOOKUP_SIGNALS
        assert effects["mongo_writes"] == 0


class TeamMembersReadSideEffectsLegacyPermissionsTest(_ReadsAreSideEffectFreeMixin, ModuleStoreTestCase):
    """Reads by course staff with policy-based authoring permissions off."""

    def reader_for(self, course):
        reader = legacy_team(course.id, CourseStaffRole)
        legacy_team(course.id, CourseInstructorRole)
        PolicyCacheControl.objects.all().delete()
        return reader


class TeamMembersReadSideEffectsAuthzTest(
    _ReadsAreSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """
    Reads by a course staff role holder with policy-based authoring
    permissions on. The roles are assigned through the permission library,
    which creates the policy version row; it is deleted before the reads.
    """

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_STAFF.external_key, course.id)
        self.add_user_to_role_in_course(UserFactory(), COURSE_ADMIN.external_key, course.id)
        PolicyCacheControl.objects.all().delete()
        return reader


class _ReadCostMixin:
    """Reads cost no more SQL queries or Mongo reads than the legacy read, and do not grow with the team."""

    def add_member(self, course, role):
        """Return a new user holding ``role`` (``instructor`` or ``staff``) on ``course``."""
        raise NotImplementedError

    def test_reads_cost_no_more_than_the_legacy_read(self):
        course = CourseFactory.create()
        reader = self.add_member(course, "staff")
        self.add_member(course, "instructor")
        client = api_client(reader)
        legacy = measure(lambda: client.get(legacy_url(course.id)))
        for address in (list_url, me_url):
            new = measure(lambda address=address: client.get(address(course.id)))
            assert new[0] <= legacy[0], (address.__name__, new, legacy)
            assert new[1] <= legacy[1], (address.__name__, new, legacy)
            assert new[2] == legacy[2] == 0, (address.__name__, new, legacy)

    def test_the_sql_cost_does_not_grow_with_the_team(self):
        course = CourseFactory.create()
        client = api_client(self.add_member(course, "staff"))

        def queries(address):
            client.get(address(course.id), {"page_size": 100})
            with CaptureQueriesContext(connection) as captured:
                assert client.get(address(course.id), {"page_size": 100}).status_code == 200
            return len(captured.captured_queries)

        one = {address: queries(address) for address in (list_url, me_url)}
        for index in range(19):
            self.add_member(course, "instructor" if index % 4 == 0 else "staff")
        assert {address: queries(address) for address in (list_url, me_url)} == one
        assert client.get(list_url(course.id), {"page_size": 100}).json()["count"] == 20


class TeamMembersReadCostLegacyPermissionsTest(_ReadCostMixin, ModuleStoreTestCase):
    """Costs for course staff, with policy-based authoring permissions off."""

    def add_member(self, course, role):
        return legacy_team(course.id, {"instructor": CourseInstructorRole, "staff": CourseStaffRole}[role])


class TeamMembersReadCostAuthzTest(CourseAuthoringAuthzTestMixin, _ReadCostMixin, ModuleStoreTestCase):
    """Costs for a course staff role holder, with policy-based authoring permissions on."""

    def add_member(self, course, role):
        user = UserFactory()
        policy_role = {"instructor": COURSE_ADMIN, "staff": COURSE_STAFF}[role]
        self.add_user_to_role_in_course(user, policy_role.external_key, course.id)
        return user


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

@ddt.ddt
class TeamMembersRoutingTest(SharedModuleStoreTestCase):
    """The addresses, the legacy address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = api_client(UserFactory(is_staff=True))

    def test_the_addresses(self):
        assert list_url(COURSE_KEY) == f"/api/authoring/v2/courses/{COURSE_KEY}/team_members/"
        assert me_url(COURSE_KEY) == f"/api/authoring/v2/courses/{COURSE_KEY}/team_members/me/"
        for address, actions in ((list_url(COURSE_KEY), {"get": "list"}), (me_url(COURSE_KEY), {"get": "retrieve"})):
            match = resolve(address)
            assert match.func.cls is CourseTeamMembersViewSet
            assert routed_actions(match.func) == actions
            assert match.kwargs == {"course_key": CourseKey.from_string(COURSE_KEY)}

    def test_the_legacy_address_is_unchanged(self):
        assert legacy_url(COURSE_KEY) == f"/api/contentstore/v1/course_team/{COURSE_KEY}"
        view = resolve(legacy_url(COURSE_KEY)).func.cls
        assert (view.__module__, view.__name__) == (LEGACY_MODULE, "CourseTeamView")

    def test_the_legacy_address_still_takes_a_slash_separated_key_that_v2_does_not(self):
        view = resolve("/api/contentstore/v1/course_team/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == (LEGACY_MODULE, "CourseTeamView")
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v2/courses/edX/DemoX/Demo_Course/team_members/")

    def test_only_the_caller_is_addressable_as_a_member(self):
        with pytest.raises(Resolver404):
            resolve(f"/api/authoring/v2/courses/{COURSE_KEY}/team_members/someone/")
        response = self.client.get(f"/api/authoring/v2/courses/{self.course.id}/team_members/someone/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v2 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert {"course_team_member_list", "course_team_member_me"} <= set(names)

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data(*[(address, method) for address in ("list", "me") for method in ("post", "put", "patch", "delete")])
    @ddt.unpack
    def test_other_methods_are_not_allowed(self, address, method):
        target = {"list": list_url, "me": me_url}[address](self.course.id)
        response = getattr(self.client, method)(target, {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_unsupported_accept_is_refused_with_406(self):
        response = self.client.get(list_url(self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data(*[
        (key, suffix)
        for key in ("not-a-course-key", "edX/DemoX/Demo_Course", "edX+DemoX+Demo_Course")
        for suffix in ("team_members/", "team_members/me/")
    ])
    @ddt.unpack
    def test_an_unparsable_or_slash_form_key_is_an_html_404(self, key, suffix):
        response = self.client.get(f"/api/authoring/v2/courses/{key}/{suffix}")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    @ddt.data(list_url, me_url)
    def test_the_address_without_its_trailing_slash_redirects_to_it(self, address):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        slashless = address(self.course.id).rstrip("/")
        response = self.client.get(slashless)
        assert response.status_code == 301
        assert response["Location"] == address(self.course.id)
        with pytest.raises(Resolver404):
            resolve(slashless)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

class TeamMembersSchemaTest(TestCase):
    """The listing and ``me`` as published in the CMS authoring schema."""

    LIST_PATH = "/api/authoring/v2/courses/{course_key}/team_members/"
    ME_PATH = "/api/authoring/v2/courses/{course_key}/team_members/me/"
    LEGACY_PATH = "/api/contentstore/v1/course_team/{course_id}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = published_schema()
        cls.components = cls.schema["components"]["schemas"]

    @staticmethod
    def _ref(node):
        return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def _operation(self, path):
        operations = self.schema["paths"][path]
        assert sorted(operations) == ["get"]
        return operations["get"]

    def test_both_operations_are_published_for_the_sdk(self):
        for path, operation_id in (
            (self.LIST_PATH, "v2_courses_team_members_list"),
            (self.ME_PATH, "v2_courses_team_members_me_retrieve"),
        ):
            operation = self._operation(path)
            assert operation["operationId"] == operation_id
            assert operation["tags"] == ["openedx-platform-sdk"]
            assert "deprecated" not in operation
            assert sorted(operation["responses"]) == ["200", "401", "403", "404"]
            for status in ("401", "403", "404"):
                assert self._ref(operation["responses"][status]) == "ErrorResponse", (path, status)

    def test_the_list_publishes_the_seven_envelope_keys(self):
        name = self._ref(self._operation(self.LIST_PATH)["responses"]["200"])
        assert name == "PaginatedCourseTeamMember.v2List"
        envelope = self.components[name]
        assert list(envelope["properties"]) == list(ENVELOPE_KEYS)
        assert envelope["required"] == list(ENVELOPE_KEYS)
        assert envelope["properties"]["results"] == {
            "type": "array", "items": {"$ref": "#/components/schemas/CourseTeamMember.v2"},
        }

    def test_the_parameters(self):
        listing = {p["name"]: p for p in self._operation(self.LIST_PATH)["parameters"]}
        assert set(listing) == {"course_key", "page", "page_size"}
        for name in ("page", "page_size"):
            assert (listing[name]["in"], listing[name]["schema"]) == ("query", {"type": "integer"})
            assert not listing[name].get("required")
        me = {p["name"]: p for p in self._operation(self.ME_PATH)["parameters"]}
        assert set(me) == {"course_key"}
        for parameters in (listing, me):
            course_key = parameters["course_key"]
            assert (course_key["in"], course_key["required"], course_key["schema"]["type"]) == ("path", True, "string")
            assert course_key["schema"]["pattern"] == f"^{CourseKeyConverter.regex}$"

    def test_the_member_and_caller_components(self):
        member_component = self.components["CourseTeamMember.v2"]
        caller_component = self.components["CourseTeamCaller.v2"]
        assert self._ref(self._operation(self.ME_PATH)["responses"]["200"]) == "CourseTeamCaller.v2"
        assert set(member_component["properties"]) == set(member_component["required"]) == MEMBER_MEMBERS
        assert set(caller_component["properties"]) == set(caller_component["required"]) == CALLER_MEMBERS
        (role,) = member_component["properties"]["role"]["allOf"]
        assert resolve_ref(self.schema, role)["enum"] == list(LISTED_ORDER)
        caller_role = caller_component["properties"]["role"]
        assert caller_role["nullable"] is True
        assert [resolve_ref(self.schema, option)["enum"] for option in caller_role["oneOf"]] == [
            list(LISTED_ORDER), [None],
        ]
        assert member_component["properties"]["id"]["type"] == "integer"
        for flag in ("can_manage_team", "is_only_instructor"):
            assert caller_component["properties"][flag]["type"] == "boolean"
        nullable = {
            name for component in (member_component, caller_component)
            for name, field in component["properties"].items() if field.get("nullable")
        }
        assert nullable == {"role"}

    def test_every_member_is_described(self):
        for name in ("CourseTeamMember.v2", "CourseTeamCaller.v2"):
            component = self.components[name]
            assert component.get("description"), name
            assert [field for field, spec in component["properties"].items() if not spec.get("description")] == [], name

    def test_the_examples_are_valid_bodies(self):
        listing = self._operation(self.LIST_PATH)["responses"]["200"]["content"]["application/json"]
        page = listing["examples"]["TeamMember"]["value"]
        assert set(page) == set(ENVELOPE_KEYS)
        assert len(page["results"]) == 1
        assert_matches_published_schema(page, "PaginatedCourseTeamMember.v2List")
        me = self._operation(self.ME_PATH)["responses"]["200"]["content"]["application/json"]
        assert_matches_published_schema(me["examples"]["OnlyInstructor"]["value"], "CourseTeamCaller.v2")

    def test_the_paginator_is_the_shared_one(self):
        assert CourseTeamMembersViewSet.pagination_class is DocumentedDefaultPagination

    def test_the_legacy_operation_is_deprecated_and_left_out_of_the_sdk(self):
        operation = self._operation(self.LEGACY_PATH)
        assert operation["operationId"] == "v1_course_team_retrieve"
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
        assert (self.LEGACY_PATH, "get") in deprecated
        assert {(self.LIST_PATH, "get"), (self.ME_PATH, "get")} & deprecated == set()
        stray = sorted(
            (path, method) for path, method in deprecated
            if path not in SUPERSEDED_PATHS and not path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        )
        assert stray == []
