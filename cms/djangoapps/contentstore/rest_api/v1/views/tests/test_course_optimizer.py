"""Tests for the v1 course optimizer API: link checks and updates of links to a previous run."""

import json
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse
from drf_spectacular.hooks import postprocess_schema_enums
from drf_spectacular.plumbing import ComponentRegistry, list_hash
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey, UsageKey
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
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from cms.djangoapps.contentstore.rest_api.v1.course_optimizer_service import (
    LINK_CHECK_ERROR_MESSAGE,
    LINK_NOT_UPDATED_MESSAGE,
    PREVIOUS_RUN_LINK_UPDATES_DISABLED_MESSAGE,
    RUN_STATES,
)
from cms.djangoapps.contentstore.rest_api.v1.views.course_optimizer import (
    CourseLinkCheckViewSet,
    CoursePreviousRunLinkUpdateViewSet,
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
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import resolve_ref
from cms.djangoapps.contentstore.tasks import CourseLinkCheckTask, CourseLinkUpdateTask, LinkState
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.toggles import ENABLE_COURSE_OPTIMIZER_CHECK_PREV_RUN_LINKS
from cms.djangoapps.contentstore.views.permissions import CanEditCourseContent, CanStartLinkCheck, IsExistingCourse
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
from openedx.core.djangoapps.authz.constants import LEGACY_PERMISSION_HANDLER_MAP, LegacyAuthoringPermission
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory

LINK_CHECK = "authoring_v1:course_link_check"
LINK_UPDATE = "authoring_v1:course_previous_run_link_update"
LEGACY_NAMESPACE = "cms.djangoapps.contentstore:v0"
LEGACY_VIEWS = "cms.djangoapps.contentstore.rest_api.v0.views.course_optimizer"
SERVICE = "cms.djangoapps.contentstore.rest_api.v1.course_optimizer_service"
FULL = {"view": "full"}


def previous_run_links(active):
    """Turn updating links to a previous run on or off for every course, as a decorator or a context."""
    return override_waffle_flag(ENABLE_COURSE_OPTIMIZER_CHECK_PREV_RUN_LINKS, active=active)


# Each operation as (HTTP method, v1 URL name, v0 URL name, default body, v1 success status).
OPERATIONS = {
    "start_check": ("post", LINK_CHECK, "link_check", None, 202),
    "read_check": ("get", LINK_CHECK, "link_check_status", None, 200),
    "start_update": ("post", LINK_UPDATE, "rerun_link_update", {"action": "all"}, 202),
    "read_update": ("get", LINK_UPDATE, "rerun_link_update_status", None, 200),
}
STARTS = ("start_check", "start_update")
TASK_OF = {"start_check": "check_broken_links", "start_update": "update_course_rerun_links"}
# What each operation answers a caller who has no run of the course.
EMPTY_BODIES = {
    "start_check": {"status": "Pending"},
    "read_check": {"status": "Uninitiated", "created_at": None, "error": None},
    "start_update": {"status": "Pending"},
    "read_update": {"status": "Uninitiated"},
}
LEGACY_EMPTY_BODIES = {
    "start_check": {"LinkCheckStatus": "Pending"},
    "read_check": {"LinkCheckStatus": "Uninitiated"},
    "start_update": {"status": "Pending"},
    "read_update": {"status": "uninitiated", "results": []},
}
# The states a link check reports; a link update also reports Scanning and Updating.
LINK_CHECK_STATES = [
    UserTaskStatus.PENDING,
    UserTaskStatus.IN_PROGRESS,
    UserTaskStatus.SUCCEEDED,
    UserTaskStatus.FAILED,
    UserTaskStatus.CANCELED,
    UserTaskStatus.RETRYING,
]
LINK_UPDATE_STATES = [*LINK_CHECK_STATES, "Scanning", "Updating"]
# Text a failed run records, standing for an exception's message.
RECORDED_FAILURE = "{'raw_error_msg': 'connection to 10.0.0.7:5432 refused'}"
_DEFAULT = object()


def url(name, course_key):
    """Return the v1 address ``name`` for ``course_key``."""
    return reverse(name, kwargs={"course_key": str(course_key)})


def legacy_url(name, course_key):
    """Return the v0 address ``name`` for ``course_key``."""
    return reverse(f"{LEGACY_NAMESPACE}:{name}", kwargs={"course_id": str(course_key)})


def call(client, operation, course_key, legacy=False, params=None, body=_DEFAULT):
    """Perform ``operation`` on ``course_key`` at the v1 address, or the v0 one with ``legacy``."""
    method, name, legacy_name, default_body, _ = OPERATIONS[operation]
    address = legacy_url(legacy_name, course_key) if legacy else url(name, course_key)
    if method in ("get", "head"):
        return getattr(client, method)(address, params or {})
    data = default_body if body is _DEFAULT else body
    if data is None:
        return client.post(address)
    return client.post(address, data, format="json")


def assert_feature_off(response):
    """Assert ``response`` is the 404 answered where updating links to a previous run is not enabled."""
    envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
    assert envelope["detail"] == PREVIOUS_RUN_LINK_UPDATES_DISABLED_MESSAGE
    return envelope


def assert_invalid(response):
    """Assert ``response`` is the 400 validation envelope, and return its ``errors`` member."""
    return assert_error_envelope(response, expected_status=400, expected_type_slug="validation")["errors"]


def record_link_check(user, course_key, state, links=None, error=None, created=None):
    """Store a link check of ``course_key`` started by ``user``, as the task records one."""
    run = UserTaskStatus.objects.create(
        user=user,
        task_id=str(uuid.uuid4()),
        task_class="cms.djangoapps.contentstore.tasks.check_broken_links",
        name=CourseLinkCheckTask.generate_name({"course_key_string": str(course_key)}),
        total_steps=1,
        state=state,
    )
    if links is not None:
        UserTaskArtifact.objects.create(
            status=run, name="BrokenLinks", file=ContentFile(json.dumps(links).encode(), name="links.json"),
        )
    if error is not None:
        UserTaskArtifact.objects.create(status=run, name="Error", text=error)
    if created is not None:
        UserTaskStatus.objects.filter(pk=run.pk).update(created=created)
    return run


def record_link_update(user, course_key, state, results=None, created=None):
    """Store an update of links to the previous run of ``course_key`` started by ``user``."""
    run = UserTaskStatus.objects.create(
        user=user,
        task_id=str(uuid.uuid4()),
        task_class="cms.djangoapps.contentstore.tasks.update_course_rerun_links",
        name=CourseLinkUpdateTask.generate_name({"course_id": str(course_key)}),
        total_steps=2,
        state=state,
    )
    if results is not None:
        UserTaskArtifact.objects.create(
            status=run, name="LinkUpdateResults", file=ContentFile(json.dumps(results).encode(), name="results.json"),
        )
    if created is not None:
        UserTaskStatus.objects.filter(pk=run.pk).update(created=created)
    return run


def _hours_ago(hours):
    return datetime.now(UTC) - timedelta(hours=hours)


# v0 member names and the v1 names that replace them.
LEGACY_MEMBER_NAMES = {
    "LinkCheckStatus": "status",
    "LinkCheckCreatedAt": "created_at",
    "LinkCheckOutput": "output",
    "LinkCheckError": "error",
    "displayName": "display_name",
    "brokenLinks": "broken_links",
    "lockedLinks": "locked_links",
    "externalForbiddenLinks": "external_forbidden_links",
    "previousRunLinks": "previous_run_links",
    "originalLink": "original_link",
    "isUpdated": "is_updated",
    "updatedLink": "updated_link",
}


def in_v1_names(body):
    """``body`` with every v0 member name replaced by its v1 name, at every depth."""
    if isinstance(body, dict):
        return {LEGACY_MEMBER_NAMES.get(key, key): in_v1_names(value) for key, value in body.items()}
    if isinstance(body, list):
        return [in_v1_names(item) for item in body]
    return body


def member_names(body):
    """Every member name in ``body``, at every depth."""
    if isinstance(body, dict):
        return set(body) | {name for value in body.values() for name in member_names(value)}
    if isinstance(body, list):
        return {name for item in body for name in member_names(item)}
    return set()


def test_v0_names_are_translated_at_every_depth():
    body = {"LinkCheckOutput": {"sections": [{"displayName": "A", "x": [{"previousRunLinks": [{"isUpdated": True}]}]}]}}
    assert in_v1_names(body) == {
        "output": {"sections": [{"display_name": "A", "x": [{"previous_run_links": [{"is_updated": True}]}]}]},
    }
    assert member_names(in_v1_names(body)) == {"output", "sections", "display_name", "x", "previous_run_links",
                                               "is_updated"}


# ---------------------------------------------------------------------------
# Who may call the endpoints
# ---------------------------------------------------------------------------

class _PatchedTasksMixin:
    """Replace the link check and link update tasks with mocks, on both versions."""

    def setUp(self):
        super().setUp()
        self.tasks = {}
        for version, module in (("v1", SERVICE), ("v0", LEGACY_VIEWS)):
            for task in TASK_OF.values():
                patcher = patch(f"{module}.{task}")
                self.tasks[(version, task)] = patcher.start()
                self.addCleanup(patcher.stop)

    def delay(self, version, operation):
        """The mocked ``delay`` of the task ``operation`` queues on ``version``."""
        return self.tasks[(version, TASK_OF[operation])].delay

    def reset_tasks(self):
        """Forget every call the task mocks have recorded."""
        for task in self.tasks.values():
            task.reset_mock()


class _AccessMixin(_PatchedTasksMixin):
    """
    Checks a caller's status on an operation against the expected one, that a
    run is queued exactly when the start is accepted, and that v0 decides the
    same for the same caller and queues the same run.
    """

    def assert_access(self, user, operation, expected, course_key=None):
        """Assert ``expected`` for ``operation`` by ``user`` on v1, and agreement with v0."""
        course_key = course_key or self.course.id
        self.reset_tasks()
        client = api_client(user)
        response = call(client, operation, course_key)
        assert response.status_code == expected, (operation, response.content)
        if expected in (200, 202):
            assert response.json() == EMPTY_BODIES[operation]
        else:
            assert_refused(response, expected)
        if operation in STARTS:
            delay = self.delay("v1", operation)
            if expected == 202:
                delay.assert_called_once()
                assert delay.call_args.args[:2] == (user.id, str(course_key))
            else:
                delay.assert_not_called()
        if expected == 404 and operation not in STARTS:
            # v0 does not look the course up on reads; ResponseDifferencesTest covers them.
            return
        legacy = call(client, operation, course_key, legacy=True)
        legacy_expected = 200 if expected == 202 else expected
        assert legacy.status_code == legacy_expected, f"v0 decided {legacy.status_code} for {operation}"
        if expected in (200, 202):
            assert legacy.json() == LEGACY_EMPTY_BODIES[operation]
        if operation in STARTS:
            assert self.delay("v0", operation).call_args == self.delay("v1", operation).call_args


@ddt.ddt
@previous_run_links(True)
class CourseOptimizerAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may start and read runs when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    @ddt.data(*OPERATIONS)
    def test_anonymous_caller_is_refused_with_401(self, operation):
        self.assert_access(None, operation, 401)

    @ddt.data(*OPERATIONS)
    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self, operation):
        self.assert_access(None, operation, 401, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*OPERATIONS)
    def test_user_without_a_role_is_refused_with_403(self, operation):
        self.assert_access(UserFactory(), operation, 403)

    @ddt.data(*OPERATIONS)
    def test_unknown_course_is_404_before_permission_is_checked(self, operation):
        self.assert_access(UserFactory(), operation, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*OPERATIONS)
    def test_limited_staff_is_refused(self, operation):
        self.assert_access(self._user_with(CourseLimitedStaffRole(self.course.id)), operation, 403)

    @ddt.data(*OPERATIONS)
    def test_staff_and_instructors_of_another_course_are_refused(self, operation):
        self.assert_access(self._user_with(CourseStaffRole(self.other_course.id)), operation, 403)
        self.assert_access(self._user_with(CourseInstructorRole(self.other_course.id)), operation, 403)

    @ddt.data(*OPERATIONS)
    def test_course_staff_is_allowed(self, operation):
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), operation, OPERATIONS[operation][4])

    @ddt.data(*OPERATIONS)
    def test_course_instructor_is_allowed(self, operation):
        user = self._user_with(CourseInstructorRole(self.course.id))
        self.assert_access(user, operation, OPERATIONS[operation][4])

    @ddt.data(*OPERATIONS)
    def test_org_staff_and_org_instructor_are_allowed(self, operation):
        expected = OPERATIONS[operation][4]
        self.assert_access(self._user_with(OrgStaffRole(self.course.id.org)), operation, expected)
        self.assert_access(self._user_with(OrgInstructorRole(self.course.id.org)), operation, expected)

    @ddt.data(*OPERATIONS)
    def test_global_staff_is_allowed(self, operation):
        self.assert_access(UserFactory(is_staff=True), operation, OPERATIONS[operation][4])

    @ddt.data(*OPERATIONS)
    def test_a_superuser_who_is_not_global_staff_is_allowed_as_on_v0(self, operation):
        """The course role lookup consults the permission library, which admits superusers, on v1 as on v0."""
        self.assert_access(UserFactory(is_superuser=True, is_staff=False), operation, OPERATIONS[operation][4])

    @ddt.data(*OPERATIONS)
    def test_a_version_only_course_key_is_404(self, operation):
        """The key parses but names no course run; v0 has no address that takes it."""
        response = call(api_client(UserFactory(is_staff=True)), operation, VERSION_ONLY_KEY)
        assert_refused(response, 404)

    @ddt.data(*OPERATIONS)
    def test_a_library_is_not_a_course(self, operation):
        library_key = LibraryFactory.create().location.library_key
        assert_refused(call(api_client(UserFactory(is_staff=True)), operation, library_key), 404)

    def test_starting_a_check_needs_only_read_access_and_the_rest_write_access(self):
        """
        With Studio write access refused and read access kept, a check may be
        started but not read, and nothing about link updates is allowed; v0
        decides the same.
        """
        staff = self._user_with(CourseStaffRole(self.course.id))
        refuse_write = {LegacyAuthoringPermission.WRITE: lambda *_: False}
        with patch.dict(LEGACY_PERMISSION_HANDLER_MAP, refuse_write):
            self.assert_access(staff, "start_check", 202)
            for operation in ("read_check", "start_update", "read_update"):
                self.assert_access(staff, operation, 403)

    def test_starting_a_check_is_refused_without_read_access(self):
        staff = self._user_with(CourseStaffRole(self.course.id))
        refuse_read = {LegacyAuthoringPermission.READ: lambda *_: False}
        with patch.dict(LEGACY_PERMISSION_HANDLER_MAP, refuse_read):
            self.assert_access(staff, "start_check", 403)
            for operation in ("read_check", "start_update", "read_update"):
                self.assert_access(staff, operation, OPERATIONS[operation][4])


@ddt.ddt
@previous_run_links(True)
class CourseOptimizerAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    Who may start and read runs when policy-based authoring permissions are
    on: every operation needs the permission to edit course content.
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

    @ddt.data(*OPERATIONS)
    def test_anonymous_caller_is_refused_with_401(self, operation):
        self.assert_access(None, operation, 401)

    @ddt.data(*OPERATIONS)
    def test_user_without_a_role_is_refused_with_403(self, operation):
        self.assert_access(self.unauthorized_user, operation, 403)

    @ddt.data(*OPERATIONS)
    def test_unknown_course_is_404_before_permission_is_checked(self, operation):
        self.assert_access(self.unauthorized_user, operation, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*[(operation, role) for operation in OPERATIONS for role in (
        COURSE_EDITOR.external_key, COURSE_STAFF.external_key, COURSE_ADMIN.external_key,
    )])
    @ddt.unpack
    def test_roles_that_may_edit_content_are_allowed(self, operation, role):
        self.assert_access(self._user_with(role), operation, OPERATIONS[operation][4])

    @ddt.data(*[(operation, role) for operation in OPERATIONS for role in (
        COURSE_AUDITOR.external_key,
        COURSE_LIMITED_STAFF.external_key,
        COURSE_DATA_RESEARCHER.external_key,
        COURSE_BETA_TESTER.external_key,
    )])
    @ddt.unpack
    def test_roles_that_may_not_edit_content_are_refused(self, operation, role):
        """An auditor may view the course, but starting a check needs the editing permission too."""
        self.assert_access(self._user_with(role), operation, 403)

    @ddt.data(*OPERATIONS)
    def test_a_role_on_another_course_is_refused(self, operation):
        user = self._user_with(COURSE_STAFF.external_key, self.other_course.id)
        self.assert_access(user, operation, OPERATIONS[operation][4], course_key=self.other_course.id)
        self.assert_access(user, operation, 403)

    @ddt.data(*OPERATIONS)
    def test_legacy_course_staff_without_a_policy_role_is_refused(self, operation):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_access(user, operation, 403)

    @ddt.data(*OPERATIONS)
    def test_staff_and_superuser_are_allowed_without_a_role(self, operation):
        self.assert_access(self.staff_user, operation, OPERATIONS[operation][4])
        self.assert_access(self.super_user, operation, OPERATIONS[operation][4])

    def test_an_inactive_session_with_a_role_is_accepted(self):
        """The policy does not look at whether the account is activated, on v1 as on v0."""
        user = self._user_with(COURSE_EDITOR.external_key)
        client = session_client(user)
        user.is_active = False
        user.save()
        for operation in OPERATIONS:
            assert call(client, operation, self.course.id).status_code == OPERATIONS[operation][4], operation
            assert call(client, operation, self.course.id, legacy=True).status_code == 200, operation


class _FeatureOffMixin(_PatchedTasksMixin):
    """
    Where updating links to a previous run is not enabled, both link update
    operations answer 404 to a caller who may use them, and nothing is
    queued; a caller who may not is still refused with 403. v0 answers 400
    and 403. Link checks are not affected.
    """

    def editor(self):
        """Return a user who may edit the course's content."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user with no access to the course."""
        raise NotImplementedError

    def test_link_updates_are_404_for_a_caller_who_may_use_them(self):
        client = api_client(self.editor())
        for operation in ("start_update", "read_update"):
            response = call(client, operation, self.course.id)
            assert_feature_off(response)
            legacy = call(client, operation, self.course.id, legacy=True)
            assert legacy.status_code == 400
            assert legacy.json() == {"error": "Course optimizer check for previous run links is not enabled."}
        self.delay("v1", "start_update").assert_not_called()
        self.delay("v0", "start_update").assert_not_called()

    def test_the_feature_is_checked_before_the_body(self):
        response = call(api_client(self.editor()), "start_update", self.course.id, body={"action": "neither"})
        assert_feature_off(response)

    def test_a_caller_who_may_not_use_them_is_still_refused_with_403(self):
        client = api_client(self.outsider())
        for operation in ("start_update", "read_update"):
            assert_refused(call(client, operation, self.course.id), 403)
            assert call(client, operation, self.course.id, legacy=True).status_code == 403

    def test_link_checks_are_not_affected(self):
        client = api_client(self.editor())
        assert call(client, "start_check", self.course.id).status_code == 202
        self.delay("v1", "start_check").assert_called_once()
        assert call(client, "read_check", self.course.id).json() == EMPTY_BODIES["read_check"]


@previous_run_links(False)
class CourseOptimizerFeatureOffLegacyPermissionsTest(_FeatureOffMixin, SharedModuleStoreTestCase):
    """The feature off, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def editor(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def outsider(self):
        return UserFactory()

    def test_instructors_and_global_staff_get_404_too(self):
        instructor = UserFactory()
        CourseInstructorRole(self.course.id).add_users(instructor)
        for user in (instructor, UserFactory(is_staff=True)):
            for operation in ("start_update", "read_update"):
                response = call(api_client(user), operation, self.course.id)
                assert_feature_off(response)
        self.delay("v1", "start_update").assert_not_called()


@previous_run_links(False)
class CourseOptimizerFeatureOffAuthzTest(CourseAuthoringAuthzTestMixin, _FeatureOffMixin, SharedModuleStoreTestCase):
    """The feature off, with policy-based authoring permissions on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def editor(self):
        user = UserFactory()
        self.add_user_to_role_in_course(user, COURSE_EDITOR.external_key, self.course.id)
        return user

    def outsider(self):
        return self.unauthorized_user


class CourseOptimizerPermissionWiringTest(SimpleTestCase):
    """Which permission each action checks, and which access each permission asks for."""

    def test_each_action_checks_the_course_then_its_own_permission(self):
        for viewset, action, expected in (
            (CourseLinkCheckViewSet, "create", [IsAuthenticated, IsExistingCourse, CanStartLinkCheck]),
            (CourseLinkCheckViewSet, "retrieve", [IsAuthenticated, IsExistingCourse, CanEditCourseContent]),
            (CoursePreviousRunLinkUpdateViewSet, "create", [IsAuthenticated, IsExistingCourse, CanEditCourseContent]),
            (CoursePreviousRunLinkUpdateViewSet, "retrieve", [IsAuthenticated, IsExistingCourse, CanEditCourseContent]),
            (CourseLinkCheckViewSet, None, [IsAuthenticated, IsExistingCourse]),
            (CoursePreviousRunLinkUpdateViewSet, "metadata", [IsAuthenticated, IsExistingCourse]),
        ):
            view = viewset()
            view.action = action
            # The JWT middleware adds NotJwtRestrictedApplication to JWT views on their first request.
            got = [type(p) for p in view.get_permissions() if type(p).__name__ != "NotJwtRestrictedApplication"]
            assert got == expected, (viewset, action)

    def test_an_action_without_a_declared_permission_is_refused(self):
        for viewset in (CourseLinkCheckViewSet, CoursePreviousRunLinkUpdateViewSet):
            view = viewset()
            for action in ("destroy", "update", "partial_update", "list"):
                view.action = action
                with pytest.raises(ImproperlyConfigured):
                    view.get_permissions()

    def test_a_routed_action_without_a_declared_permission_is_never_served(self):
        """A request reaching such an action is answered with a server error, not the handler's body."""

        class Unchecked(CourseLinkCheckViewSet):
            permission_classes = CourseLinkCheckViewSet.permission_classes

            def destroy(self, request, course_key):  # pylint: disable=unused-argument
                raise AssertionError("the handler ran without a permission check")

        course_key = "course-v1:edX+Wiring+Run"
        view = Unchecked.as_view({"delete": "destroy"})
        request = APIRequestFactory().delete(url(LINK_CHECK, course_key))
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_active=True))
        response = view(request, course_key=CourseKey.from_string(course_key))
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")

    def test_the_permissions_ask_for_the_content_editing_permission_with_read_or_write_access(self):
        course_key = "course-v1:edX+Wiring+Run"
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={"course_key": course_key})
        target = "cms.djangoapps.contentstore.views.permissions.user_has_course_permission"
        for permission, access in (
            (CanStartLinkCheck, LegacyAuthoringPermission.READ),
            (CanEditCourseContent, LegacyAuthoringPermission.WRITE),
        ):
            for decision in (True, False):
                with patch(target, return_value=decision) as check:
                    assert permission().has_permission(request, view) is decision
                check.assert_called_once_with(request.user, "courses.edit_course_content", course_key, access)

    def test_the_permissions_refuse_a_view_without_a_course(self):
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={})
        for permission in (CanStartLinkCheck, CanEditCourseContent):
            assert permission().has_permission(request, view) is False


@previous_run_links(True)
class CourseOptimizerAuthenticationTest(_PatchedTasksMixin, SharedModuleStoreTestCase):
    """How callers authenticate, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _course_staff(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def test_a_jwt_caller_may_start_and_read(self):
        user = self._course_staff()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(user)}")
        for operation in OPERATIONS:
            response = call(client, operation, self.course.id)
            assert response.status_code == OPERATIONS[operation][4], (operation, response.content)
        self.delay("v1", "start_check").assert_called_once()
        assert self.delay("v1", "start_check").call_args.args[0] == user.id

    def test_a_session_caller_may_start_and_read(self):
        user = self._course_staff()
        client = session_client(user)
        for operation in OPERATIONS:
            assert call(client, operation, self.course.id).status_code == OPERATIONS[operation][4], operation
        assert self.delay("v1", "start_update").call_args.args[0] == user.id

    def test_an_inactive_session_is_authenticated(self):
        """
        A session whose account is not activated is accepted, as on v0. Global
        staff then pass; course staff are refused (403, not 401) because a
        course role does not count while the account is inactive.
        """
        for user, allowed in ((UserFactory(is_staff=True), True), (self._course_staff(), False)):
            client = session_client(user)
            user.is_active = False
            user.save()
            for operation in OPERATIONS:
                expected = OPERATIONS[operation][4] if allowed else 403
                response = call(client, operation, self.course.id)
                assert response.status_code == expected, (operation, response.content)
                if not allowed:
                    assert_refused(response, 403)
                legacy_expected = 200 if allowed else 403
                assert call(client, operation, self.course.id, legacy=True).status_code == legacy_expected

    def test_a_bearer_token_is_refused(self):
        """Bearer tokens are not accepted here, though v0 takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert_refused(APIClient().get(url(LINK_CHECK, self.course.id), **header), 401)
        assert_refused(APIClient().post(url(LINK_CHECK, self.course.id), **header), 401)
        self.delay("v1", "start_check").assert_not_called()
        assert APIClient().get(legacy_url("link_check_status", self.course.id), **header).status_code == 200


# ---------------------------------------------------------------------------
# Whose runs a caller sees
# ---------------------------------------------------------------------------

class _VisibilityMixin:
    """
    A read returns the latest run of the course started by the caller, never
    another user's; a superuser sees the latest run of anyone. v0 shows the
    same run.
    """

    def editor(self):
        """Return a new user who may start and read runs of the course."""
        raise NotImplementedError

    def superuser(self):
        """Return a superuser who may read runs of the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.author, self.other = self.editor(), self.editor()

    def read_status(self, user, operation):
        """The state ``user`` reads on v1, after asserting v0 reads the same one."""
        client = api_client(user)
        new = call(client, operation, self.course.id)
        legacy = call(client, operation, self.course.id, legacy=True)
        assert new.status_code == legacy.status_code == 200, (new.content, legacy.content)
        state = new.json()["status"]
        legacy_body = in_v1_names(legacy.json())
        legacy_state = "Uninitiated" if legacy_body["status"] == "uninitiated" else legacy_body["status"]
        assert legacy_state == state
        return state

    def _record(self, operation, user, state, created, course_key=None):
        course_key = course_key or self.course.id
        if operation == "read_check":
            return record_link_check(user, course_key, state, created=created)
        return record_link_update(user, course_key, state, created=created)

    def _assert_visibility(self, operation):
        """Each caller with a run reads their own, an editor without one reads Uninitiated, a superuser the latest."""
        self._record(operation, self.other, UserTaskStatus.FAILED, created=_hours_ago(2))
        self._record(operation, self.author, UserTaskStatus.IN_PROGRESS, created=_hours_ago(1))
        assert self.read_status(self.author, operation) == UserTaskStatus.IN_PROGRESS
        assert self.read_status(self.other, operation) == UserTaskStatus.FAILED
        assert self.read_status(self.editor(), operation) == "Uninitiated"
        assert self.read_status(self.superuser(), operation) == UserTaskStatus.IN_PROGRESS

    def test_each_caller_sees_only_their_own_link_checks(self):
        self._assert_visibility("read_check")

    def test_each_caller_sees_only_their_own_link_updates(self):
        self._assert_visibility("read_update")

    def test_a_run_of_another_course_is_not_seen(self):
        other_course = CourseFactory.create()
        for operation in ("read_check", "read_update"):
            self._record(operation, self.author, UserTaskStatus.SUCCEEDED, _hours_ago(1), other_course.id)
            assert call(api_client(self.author), operation, self.course.id).json()["status"] == "Uninitiated"

    def test_the_latest_of_the_callers_runs_is_returned(self):
        for operation in ("read_check", "read_update"):
            self._record(operation, self.author, UserTaskStatus.FAILED, created=_hours_ago(1))
            self._record(operation, self.author, UserTaskStatus.PENDING, created=_hours_ago(3))
            assert self.read_status(self.author, operation) == UserTaskStatus.FAILED


@previous_run_links(True)
class CourseOptimizerVisibilityLegacyPermissionsTest(_VisibilityMixin, ModuleStoreTestCase):
    """Visibility with policy-based authoring permissions off."""

    def editor(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def superuser(self):
        return UserFactory(is_superuser=True, is_staff=False)

    def test_global_staff_who_is_not_a_superuser_sees_only_their_own_runs(self):
        for operation in ("read_check", "read_update"):
            self._record(operation, self.author, UserTaskStatus.IN_PROGRESS, created=_hours_ago(1))
            assert self.read_status(UserFactory(is_staff=True), operation) == "Uninitiated"


@previous_run_links(True)
class CourseOptimizerVisibilityAuthzTest(_VisibilityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Visibility with policy-based authoring permissions on: a superuser needs no role."""

    def editor(self):
        user = UserFactory()
        self.add_user_to_role_in_course(user, COURSE_EDITOR.external_key, self.course.id)
        return user

    def superuser(self):
        return self.super_user


# ---------------------------------------------------------------------------
# What reads return
# ---------------------------------------------------------------------------

def _reported_blocks(report):
    """Every component reported under the sections of a link check report."""
    for section in report["sections"]:
        for subsection in section["subsections"]:
            for unit in subsection["units"]:
                yield from unit["blocks"]


@ddt.ddt
@previous_run_links(True)
class CourseOptimizerReadTest(ModuleStoreTestCase):
    """What a read returns, by state and by view."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.editor = UserFactory()
        CourseStaffRole(self.course.id).add_users(self.editor)
        self.client = api_client(self.editor)

    def read(self, operation, legacy=False, **params):
        response = call(self.client, operation, self.course.id, legacy=legacy, params=params)
        assert response.status_code == 200, response.content
        return response.json()

    def _course_with_flagged_links(self):
        """Give the course one component and record a successful check that flagged four links in it."""
        chapter = BlockFactory.create(parent_location=self.course.location, category="chapter", display_name="Week 1")
        sequential = BlockFactory.create(parent_location=chapter.location, category="sequential", display_name="Lesson")
        vertical = BlockFactory.create(parent_location=sequential.location, category="vertical", display_name="Unit")
        html = BlockFactory.create(parent_location=vertical.location, category="html", display_name="Reading")
        block_id = str(html.location)
        links = [
            [block_id, "https://broken.example.com/page", LinkState.BROKEN],
            [block_id, "/static/handout.pdf", LinkState.LOCKED],
            [block_id, "https://forbidden.example.com/", LinkState.EXTERNAL_FORBIDDEN],
            [block_id, "https://studio.example.com/course/course-v1:Old+Run+2025/info", LinkState.PREVIOUS_RUN],
        ]
        record_link_check(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, links=links)
        return chapter, sequential, vertical, html

    @ddt.data(*LINK_CHECK_STATES)
    def test_a_link_check_reports_its_state_verbatim(self, state):
        links = [] if state == UserTaskStatus.SUCCEEDED else None
        run = record_link_check(self.editor, self.course.id, state, links=links)
        body = self.read("read_check")
        assert body["status"] == state
        assert body["created_at"] == self.read("read_check", legacy=True)["LinkCheckCreatedAt"]
        assert body["created_at"].startswith(run.created.strftime("%Y-%m-%dT%H:%M:%S"))
        assert set(body) == {"status", "created_at", "error"}

    @ddt.data(*LINK_UPDATE_STATES)
    def test_a_link_update_reports_its_state_verbatim(self, state):
        record_link_update(self.editor, self.course.id, state)
        assert self.read("read_update") == {"status": state}
        assert self.read("read_update", legacy=True)["status"] == state

    def test_every_reported_state_is_published(self):
        assert set(LINK_UPDATE_STATES) | {"Uninitiated"} == set(RUN_STATES)

    def test_a_failed_link_check_reports_a_fixed_message_not_the_recorded_text(self):
        record_link_check(self.editor, self.course.id, UserTaskStatus.FAILED, error=RECORDED_FAILURE)
        response = call(self.client, "read_check", self.course.id)
        assert response.json()["error"] == LINK_CHECK_ERROR_MESSAGE
        assert "10.0.0.7" not in response.content.decode()
        assert self.read("read_check", legacy=True)["LinkCheckError"] == RECORDED_FAILURE

    def test_a_canceled_link_check_with_an_error_recorded_reports_the_fixed_message(self):
        record_link_check(self.editor, self.course.id, UserTaskStatus.CANCELED, error=RECORDED_FAILURE)
        assert self.read("read_check")["error"] == LINK_CHECK_ERROR_MESSAGE

    @ddt.data(
        (UserTaskStatus.FAILED, None),
        (UserTaskStatus.FAILED, ""),
        (UserTaskStatus.IN_PROGRESS, RECORDED_FAILURE),
    )
    @ddt.unpack
    def test_no_error_is_reported_unless_a_stopped_run_recorded_one(self, state, error):
        record_link_check(self.editor, self.course.id, state, error=error)
        assert self.read("read_check")["error"] is None
        assert "LinkCheckError" not in self.read("read_check", legacy=True)

    def test_the_full_view_reports_the_flagged_links_in_the_course(self):
        chapter, sequential, vertical, html = self._course_with_flagged_links()
        body = self.read("read_check", **FULL)
        report = body["output"]
        assert [section["display_name"] for section in report["sections"]] == ["Week 1"]
        (section,) = report["sections"]
        assert UsageKey.from_string(section["id"]).block_id == chapter.location.block_id
        (subsection,) = section["subsections"]
        assert UsageKey.from_string(subsection["id"]).block_id == sequential.location.block_id
        (unit,) = subsection["units"]
        assert (UsageKey.from_string(unit["id"]).block_id, unit["display_name"]) == (vertical.location.block_id, "Unit")
        (block,) = unit["blocks"]
        assert UsageKey.from_string(block["id"]).block_id == html.location.block_id
        assert block["display_name"] == "Reading"
        assert block["broken_links"] == ["https://broken.example.com/page"]
        assert block["locked_links"] == ["/static/handout.pdf"]
        assert block["external_forbidden_links"] == ["https://forbidden.example.com/"]
        assert block["previous_run_links"] == [{
            "original_link": "https://studio.example.com/course/course-v1:Old+Run+2025/info",
            "is_updated": False,
            "updated_link": None,
        }]
        assert (report["course_updates"], report["custom_pages"]) == ([], [])
        assert_matches_published_schema(body, "LinkCheck.v1")

    def test_the_full_report_is_v0s_report_in_snake_case(self):
        self._course_with_flagged_links()
        new = self.read("read_check", **FULL)["output"]
        legacy = self.read("read_check", legacy=True)["LinkCheckOutput"]
        assert new == in_v1_names(legacy)
        assert list(_reported_blocks(new)) != []
        assert [name for name in member_names(new) if not re.fullmatch(r"[a-z][a-z0-9_]*", name)] == []

    def test_the_default_view_does_not_build_the_report(self):
        self._course_with_flagged_links()
        with patch(f"{SERVICE}.generate_broken_links_descriptor", side_effect=AssertionError("report built")):
            assert set(self.read("read_check")) == {"status", "created_at", "error"}
            assert self.read("read_check", view="minimal")["status"] == UserTaskStatus.SUCCEEDED
            assert call(self.client, "read_check", self.course.id, params=FULL).status_code == 500

    @ddt.data(UserTaskStatus.PENDING, UserTaskStatus.FAILED)
    def test_the_full_view_of_a_run_that_did_not_succeed_has_no_report(self, state):
        record_link_check(self.editor, self.course.id, state, error=RECORDED_FAILURE)
        body = self.read("read_check", **FULL)
        assert body["output"] is None
        assert_matches_published_schema(body, "LinkCheck.v1")

    def test_the_full_view_without_a_run(self):
        assert self.read("read_check", **FULL) == {**EMPTY_BODIES["read_check"], "output": None}
        assert self.read("read_update", **FULL) == {"status": "Uninitiated", "results": []}

    def _link_update_results(self):
        return [
            {
                "id": "1",
                "type": "course_updates",
                "original_url": "https://studio.example.com/course/course-v1:Old+Run+2025/info",
                "new_url": f"https://studio.example.com/course/{self.course.id}/info",
                "success": True,
            },
            {
                "id": "block-v1:Org+Course+Run+type@html+block@reading",
                "type": "course_content",
                "original_url": "https://studio.example.com/course/course-v1:Old+Run+2025/progress",
                "new_url": "https://studio.example.com/course/course-v1:Old+Run+2025/progress",
                "success": False,
                "error_message": "KeyError: 'children' at /edx/app/edxapp/xblock.py:88",
            },
        ]

    def test_the_full_view_reports_each_links_outcome_with_a_fixed_failure_message(self):
        results = self._link_update_results()
        record_link_update(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, results=results)
        response = call(self.client, "read_update", self.course.id, params=FULL)
        body = response.json()
        assert body == {
            "status": UserTaskStatus.SUCCEEDED,
            "results": [
                {**results[0], "error_message": None},
                {**results[1], "error_message": LINK_NOT_UPDATED_MESSAGE},
            ],
        }
        assert "xblock.py" not in response.content.decode()
        assert_matches_published_schema(body, "PreviousRunLinkUpdate.v1")
        legacy = self.read("read_update", legacy=True)
        assert legacy["results"][1]["error_message"] == results[1]["error_message"]

    def test_the_default_view_of_a_link_update_is_its_state(self):
        record_link_update(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, results=self._link_update_results())
        assert self.read("read_update") == {"status": UserTaskStatus.SUCCEEDED}
        assert self.read("read_update", view="minimal") == {"status": UserTaskStatus.SUCCEEDED}

    def test_the_default_view_of_a_link_update_does_not_read_its_results(self):
        record_link_update(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, results=self._link_update_results())
        with patch(f"{SERVICE}.get_course_link_update_data", side_effect=AssertionError("results read")):
            assert self.read("read_update") == {"status": UserTaskStatus.SUCCEEDED}
            assert call(self.client, "read_update", self.course.id, params=FULL).status_code == 500

    def test_a_flagged_link_below_a_unit_child_fails_the_full_read_on_both_versions(self):
        """
        A known gap of the report, kept as v0 has it: a link in a component
        nested below a unit's child, such as one inside a library content
        block, gives the report a fifth level the four-level shape cannot
        hold, and the full read answers 500. The default view still works.
        """
        chapter = BlockFactory.create(parent_location=self.course.location, category="chapter")
        sequential = BlockFactory.create(parent_location=chapter.location, category="sequential")
        vertical = BlockFactory.create(parent_location=sequential.location, category="vertical")
        library_content = BlockFactory.create(parent_location=vertical.location, category="library_content")
        html = BlockFactory.create(parent_location=library_content.location, category="html")
        links = [[str(html.location), "https://broken.example.com/", LinkState.BROKEN]]
        record_link_check(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, links=links)
        assert call(self.client, "read_check", self.course.id, params=FULL).status_code == 500
        assert call(self.client, "read_check", self.course.id, legacy=True).status_code == 500
        assert self.read("read_check")["status"] == UserTaskStatus.SUCCEEDED

    def test_a_link_update_that_succeeded_without_results_reports_none(self):
        record_link_update(self.editor, self.course.id, UserTaskStatus.SUCCEEDED)
        assert self.read("read_update", **FULL) == {"status": UserTaskStatus.SUCCEEDED, "results": []}

    @ddt.data("read_check", "read_update")
    def test_an_unknown_view_is_refused(self, operation):
        response = call(self.client, operation, self.course.id, params={"view": "default"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"view": ['"default" is not a valid choice.']}

    @ddt.data("read_check", "read_update")
    def test_head_answers_without_a_body(self, operation):
        name = OPERATIONS[operation][1]
        for params in ({}, FULL):
            response = self.client.head(url(name, self.course.id), params)
            assert response.status_code == 200
            assert response.content == b""

    @ddt.data("read_check", "read_update")
    def test_the_bodies_match_the_published_schema(self, operation):
        component = {"read_check": "LinkCheck.v1", "read_update": "PreviousRunLinkUpdate.v1"}[operation]
        for params in ({}, FULL):
            assert_matches_published_schema(self.read(operation, **params), component)


# ---------------------------------------------------------------------------
# Starting runs
# ---------------------------------------------------------------------------

@previous_run_links(True)
class CourseOptimizerStartTest(_PatchedTasksMixin, ModuleStoreTestCase):
    """What starting a run queues, accepts and refuses."""

    LINKS = [
        {"url": "https://studio.example.com/course/course-v1:Old+Run+2025/info", "type": "course_updates", "id": "1"},
        {
            "url": "/course/course-v1:Old+Run+2025/progress",
            "type": "custom_pages",
            "id": "block-v1:Old+Run+2025+type@static_tab+block@syllabus",
        },
    ]

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.editor = UserFactory()
        CourseStaffRole(self.course.id).add_users(self.editor)
        self.client = api_client(self.editor)

    def start(self, operation, body=_DEFAULT, legacy=False):
        return call(self.client, operation, self.course.id, legacy=legacy, body=body)

    def test_a_link_check_is_queued_as_v0_queues_it(self):
        response = self.start("start_check")
        assert response.status_code == 202
        assert response.json() == {"status": UserTaskStatus.PENDING}
        assert_matches_published_schema(response.json(), "CourseOptimizerQueuedRun.v1")
        delay = self.delay("v1", "start_check")
        delay.assert_called_once()
        assert delay.call_args.args[:2] == (self.editor.id, str(self.course.id))
        assert self.start("start_check", legacy=True).status_code == 200
        assert self.delay("v0", "start_check").call_args == delay.call_args

    def test_a_link_check_ignores_its_body(self):
        response = self.client.post(url(LINK_CHECK, self.course.id), data="{not json", content_type="application/json")
        assert response.status_code == 202
        self.delay("v1", "start_check").assert_called_once()

    def test_every_link_is_queued_as_v0_queues_it(self):
        response = self.start("start_update", {"action": "all"})
        assert response.status_code == 202
        assert response.json() == {"status": UserTaskStatus.PENDING}
        delay = self.delay("v1", "start_update")
        delay.assert_called_once()
        assert delay.call_args.args[:4] == (self.editor.id, str(self.course.id), "all", [])
        assert self.start("start_update", {"action": "all"}, legacy=True).status_code == 200
        assert self.delay("v0", "start_update").call_args == delay.call_args

    def test_the_named_links_are_queued_as_v0_queues_them(self):
        body = {"action": "single", "data": self.LINKS}
        assert self.start("start_update", body).status_code == 202
        delay = self.delay("v1", "start_update")
        assert delay.call_args.args[:4] == (self.editor.id, str(self.course.id), "single", self.LINKS)
        assert self.start("start_update", body, legacy=True).status_code == 200
        assert self.delay("v0", "start_update").call_args == delay.call_args

    def test_links_sent_with_action_all_are_not_read(self):
        """Every link is changed then, so links in the body are neither validated nor queued, as on v0."""
        for links in ("not a list", [{"type": "course_updates"}], self.LINKS):
            self.reset_tasks()
            body = {"action": "all", "data": links}
            assert self.start("start_update", body).status_code == 202, links
            assert self.delay("v1", "start_update").call_args.args[2:4] == ("all", [])
            assert self.start("start_update", body, legacy=True).status_code == 200, links
            self.delay("v0", "start_update").assert_called_once()

    def _refused(self, body):
        errors = assert_invalid(self.start("start_update", body))
        self.delay("v1", "start_update").assert_not_called()
        return errors

    def test_an_action_is_required(self):
        assert self._refused({}) == {"action": ["This field is required."]}
        assert self.start("start_update", {}, legacy=True).status_code == 400

    def test_an_unknown_action_is_refused(self):
        assert self._refused({"action": "some"}) == {"action": ['"some" is not a valid choice.']}

    def test_single_needs_links(self):
        expected = {"data": ["This field is required when action is 'single'."]}
        assert self._refused({"action": "single"}) == expected
        assert self._refused({"action": "single", "data": []}) == expected
        assert self.start("start_update", {"action": "single"}, legacy=True).status_code == 400

    # The error library renders the errors of each link as Python text rather
    # than as an object of members. Change these to per-link objects once it
    # normalizes nested errors.

    def test_each_link_needs_a_url_a_type_and_an_id(self):
        errors = self._refused({"action": "single", "data": [{"type": "course_updates", "id": "1"}]})
        assert errors == {"data": ["{'url': [ErrorDetail(string='This field is required.', code='required')]}"]}
        link, blank_type = self.LINKS[0], {**self.LINKS[0], "type": ""}
        errors = self._refused({"action": "single", "data": [link, blank_type]})
        assert errors == {
            "data": ["{}", "{'type': [ErrorDetail(string='This field may not be blank.', code='blank')]}"],
        }

    def test_the_links_must_be_a_list(self):
        errors = self._refused({"action": "single", "data": {"url": "x", "type": "y", "id": "z"}})
        assert errors == {"data": [
            "{'non_field_errors': [ErrorDetail(string='Expected a list of items but got type \"dict\".', "
            "code='not_a_list')]}"
        ]}

    def test_a_body_that_is_not_an_object_is_refused(self):
        response = self.client.post(url(LINK_UPDATE, self.course.id), data="[1, 2]", content_type="application/json")
        assert assert_invalid(response) == {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]}
        self.delay("v1", "start_update").assert_not_called()


# ---------------------------------------------------------------------------
# Differences from v0
# ---------------------------------------------------------------------------

# Requests v0 and v1 answer differently beyond the error body and the member
# names, as (request, v0 answer, v1 answer, test in CourseOptimizerResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("start a link check or a link update", "200", "202", "test_starting_a_run_is_accepted"),
    ("read a link check of an unknown course, as global staff", "500", "404", "test_reads_of_an_unknown_course"),
    ("read a link update of an unknown course, as global staff", "200, uninitiated", "404",
     "test_reads_of_an_unknown_course"),
    ("read either of an unknown course, without a role", "403", "404", "test_reads_of_an_unknown_course"),
    ("start or read a link update where the feature is off", "400", "404", "test_the_feature_off"),
    ("start a link update naming a link with a missing or blank url, type or id", "200, queued", "400",
     "test_a_link_missing_a_member"),
    ("start a link update whose body is not a JSON object", "500", "400", "test_a_body_that_is_not_an_object"),
    ("read with a view other than minimal or full", "200, view ignored", "400", "test_an_unknown_view"),
    ("read a link check after a status was stored in the session", "the stored status", "Uninitiated",
     "test_a_status_stored_in_the_session"),
    ("read a link update with no run", "uninitiated, results []", "Uninitiated", "test_no_link_update_yet"),
    ("start or read with a Bearer token", "200", "401", "test_a_bearer_token"),
    ("slash-separated course key", "404 JSON", "404 HTML", "test_a_slash_separated_course_key"),
]


@previous_run_links(True)
class CourseOptimizerResponseDifferencesTest(_PatchedTasksMixin, ModuleStoreTestCase):
    """Each request v0 and v1 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.editor = UserFactory()
        CourseStaffRole(self.course.id).add_users(self.editor)
        self.client = api_client(self.editor)

    def both(self, operation, course_key=None, client=None, **kwargs):
        """The v0 and v1 responses to the same request."""
        client, course_key = client or self.client, course_key or self.course.id
        return call(client, operation, course_key, legacy=True, **kwargs), call(client, operation, course_key, **kwargs)

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_starting_a_run_is_accepted(self):
        for operation in STARTS:
            legacy, new = self.both(operation)
            assert (legacy.status_code, new.status_code) == (200, 202)

    def test_reads_of_an_unknown_course(self):
        """
        v0 does not look the course up on reads. For global staff its link
        check read fails sorting the report by the course outline the
        modulestore cannot find; its link update read reports no run.
        """
        global_staff = api_client(UserFactory(is_staff=True))
        legacy, new = self.both("read_check", UNKNOWN_COURSE_KEY, client=global_staff)
        assert legacy.status_code == 500
        assert_refused(new, 404)
        legacy, new = self.both("read_update", UNKNOWN_COURSE_KEY, client=global_staff)
        assert legacy.status_code == 200
        assert legacy.json() == {"status": "uninitiated", "results": []}
        assert_refused(new, 404)
        for operation in ("read_check", "read_update"):
            legacy, new = self.both(operation, UNKNOWN_COURSE_KEY, client=api_client(UserFactory()))
            assert legacy.status_code == 403
            assert_refused(new, 404)

    def test_the_feature_off(self):
        with previous_run_links(False):
            for operation in ("start_update", "read_update"):
                legacy, new = self.both(operation)
                assert legacy.status_code == 400
                assert_feature_off(new)

    def test_a_link_missing_a_member(self):
        complete = {"url": "/course/course-v1:Old+Run+2025/info", "type": "course_updates", "id": "1"}
        for link in (
            {"type": "course_updates", "id": "1"},
            {"url": complete["url"], "id": "1"},
            {"url": complete["url"], "type": "course_updates"},
            {**complete, "type": ""},
            {**complete, "id": ""},
        ):
            self.reset_tasks()
            legacy, new = self.both("start_update", body={"action": "single", "data": [link]})
            assert legacy.status_code == 200, link
            self.delay("v0", "start_update").assert_called_once()
            assert list(assert_invalid(new)) == ["data"], link
            self.delay("v1", "start_update").assert_not_called()

    def test_a_body_that_is_not_an_object(self):
        address = {"legacy": legacy_url("rerun_link_update", self.course.id), "new": url(LINK_UPDATE, self.course.id)}
        legacy = self.client.post(address["legacy"], data="[1, 2]", content_type="application/json")
        new = self.client.post(address["new"], data="[1, 2]", content_type="application/json")
        assert legacy.status_code == 500
        assert_invalid(new)

    def test_an_unknown_view(self):
        for operation in ("read_check", "read_update"):
            legacy, new = self.both(operation, params={"view": "default"})
            assert legacy.status_code == 200, operation
            assert assert_invalid(new) == {"view": ['"default" is not a valid choice.']}

    def test_a_status_stored_in_the_session(self):
        client = session_client(self.editor)
        session = client.session
        session["link_check_status"] = {str(self.course.id): "Stored"}
        session.save()
        legacy, new = self.both("read_check", client=client)
        assert legacy.json() == {"LinkCheckStatus": "Stored"}
        assert new.json() == EMPTY_BODIES["read_check"]

    def test_no_link_update_yet(self):
        legacy, new = self.both("read_update")
        assert legacy.json() == {"status": "uninitiated", "results": []}
        assert new.json() == {"status": "Uninitiated"}

    def test_a_bearer_token(self):
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        for operation in OPERATIONS:
            legacy, new = self.both(operation, client=client)
            assert legacy.status_code == 200, operation
            assert_refused(new, 401)

    def test_a_slash_separated_course_key(self):
        """v0 routes the slash form and reports the course unknown in JSON; v1 has no route for it."""
        legacy = self.client.post("/api/contentstore/v0/link_check/edX/NoSuch/Course")
        new = self.client.post("/api/authoring/v1/courses/edX/NoSuch/Course/link_check/")
        assert legacy.status_code == new.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        assert new["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Parity with v0
# ---------------------------------------------------------------------------

# Members of an error body that differ between v0 and v1 for the same refused
# request. The comparison asserts nothing else differs, and that each occurs.
ENVELOPE_DIFFERENCES = [
    ("type", "errors use the standardized envelope"),
    ("title", "errors use the standardized envelope"),
    ("status", "errors use the standardized envelope"),
    ("detail", "errors use the standardized envelope"),
    ("errors", "a refused body maps each refused member to its messages"),
    ("developer_message", "v0 error bodies carry developer_message; the envelope does not"),
    ("error_code", "the v0 not-found body carries error_code; the envelope does not"),
    ("error", "v0 refuses a link update body with {'error': ...}; the envelope replaces it"),
]
# Members of a successful body, in v1's names, that differ from v0's, each with
# the test asserting exactly where it occurs.
BODY_DIFFERENCES = [
    ("created_at", "always present, null without a run; v0 omits it", "test_reads_without_a_run"),
    ("error", "always present: null, or a fixed message where v0 returns the recorded text",
     "test_a_failed_link_check"),
    ("output", "with view=full, null for a run that did not succeed; v0 omits it", "test_reads_without_a_run"),
    ("status", "no link update yet is Uninitiated; v0 spells it uninitiated", "test_reads_without_a_run"),
    ("results", "only with view=full; v0 always returns it", "test_reads_without_a_run"),
    ("results[1].error_message", "a fixed message where v0 returns the recorded text",
     "test_a_link_update_with_a_failed_link"),
    ("results[0].error_message", "null for a link that was changed; v0 omits it",
     "test_a_link_update_with_a_failed_link"),
]


def _occurs(path, observed):
    """Whether ``path`` or a member below it is among the ``observed`` changed paths."""
    return any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in observed)


def test_only_declared_differences_are_tolerated():
    assert undeclared_paths({"type", "errors.action[0]", "developer_message"}, ENVELOPE_DIFFERENCES) == set()
    undeclared = undeclared_paths({"output.sections", "results[0].id"}, ENVELOPE_DIFFERENCES)
    assert undeclared == {"output.sections", "results[0].id"}


class _ParityMixin(_PatchedTasksMixin):
    """
    v1 against v0 for the same course and caller: bodies, with v0's member
    names translated, differ only where a declared difference says, and
    errors only by the envelope.
    """

    def editor_for(self, course):
        """Return a user who may start and read runs of the course."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user with no access to the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.editor = self.editor_for(self.course)
        self.observed = set()

    def pair(self, operation, client=None, course_key=None, **kwargs):
        client, course_key = client or api_client(self.editor), course_key or self.course.id
        return call(client, operation, course_key, legacy=True, **kwargs), call(client, operation, course_key, **kwargs)

    def changed(self, legacy, new, expected_status):
        """The paths that differ between the bodies, v0's translated to v1's names, after checking statuses."""
        legacy_status = 200 if expected_status == 202 else expected_status
        assert legacy.status_code == legacy_status, legacy.content
        assert new.status_code == expected_status, new.content
        changed = changed_paths(in_v1_names(json.loads(legacy.content or b"null")), json.loads(new.content or b"null"))
        self.observed |= changed
        return changed

    def test_starts_return_the_same_body(self):
        for operation in STARTS:
            assert self.changed(*self.pair(operation), 202) == set(), operation

    def test_reads_without_a_run(self):
        assert self.changed(*self.pair("read_check"), 200) == {"created_at", "error"}
        assert self.changed(*self.pair("read_check", params=FULL), 200) == {"created_at", "error", "output"}
        assert self.changed(*self.pair("read_update"), 200) == {"status", "results"}
        assert self.changed(*self.pair("read_update", params=FULL), 200) == {"status"}

    def test_reads_of_a_run_in_progress(self):
        record_link_check(self.editor, self.course.id, UserTaskStatus.IN_PROGRESS)
        record_link_update(self.editor, self.course.id, "Scanning")
        assert self.changed(*self.pair("read_check"), 200) == {"error"}
        assert self.changed(*self.pair("read_update"), 200) == {"results"}
        assert self.changed(*self.pair("read_update", params=FULL), 200) == set()

    def test_a_failed_link_check(self):
        record_link_check(self.editor, self.course.id, UserTaskStatus.FAILED, error=RECORDED_FAILURE)
        assert self.changed(*self.pair("read_check"), 200) == {"error"}

    def test_a_successful_link_check_has_the_same_report(self):
        record_link_check(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, links=[])
        assert self.changed(*self.pair("read_check", params=FULL), 200) == {"error"}

    def test_a_link_update_with_a_failed_link(self):
        results = [
            {"id": "1", "type": "course_updates", "original_url": "a", "new_url": "b", "success": True},
            {"id": "2", "type": "course_updates", "original_url": "c", "new_url": "c", "success": False,
             "error_message": "Traceback internals"},
        ]
        record_link_update(self.editor, self.course.id, UserTaskStatus.SUCCEEDED, results=results)
        changed = self.changed(*self.pair("read_update", params=FULL), 200)
        assert changed == {"results[0].error_message", "results[1].error_message"}

    def _error_pairs(self):
        """``(legacy, new, status)`` for each refusal both addresses produce."""
        outsider = api_client(self.outsider())
        yield (*self.pair("read_check", client=api_client()), 401)
        yield (*self.pair("start_update", client=api_client()), 401)
        for operation in OPERATIONS:
            yield (*self.pair(operation, client=outsider), 403)
        for operation in STARTS:
            yield (*self.pair(operation, client=outsider, course_key=UNKNOWN_COURSE_KEY), 404)
        yield (*self.pair("start_update", body={"action": "neither"}), 400)

    def test_errors_differ_only_by_the_envelope(self):
        for legacy, new, status in self._error_pairs():
            changed = self.changed(legacy, new, status)
            assert undeclared_paths(changed, ENVELOPE_DIFFERENCES) == set(), (legacy.content, new.content)

    def test_every_declared_difference_occurs(self):
        # In this order: the reads without a run come before any run is recorded.
        names = [test for *_, test in BODY_DIFFERENCES] + ["test_errors_differ_only_by_the_envelope"]
        for name in dict.fromkeys(names):
            getattr(self, name)()
        for path, *_ in BODY_DIFFERENCES + ENVELOPE_DIFFERENCES:
            assert _occurs(path, self.observed), path


@previous_run_links(True)
class CourseOptimizerParityLegacyPermissionsTest(_ParityMixin, ModuleStoreTestCase):
    """Parity for course staff, with policy-based authoring permissions off."""

    def editor_for(self, course):
        editor = UserFactory()
        CourseStaffRole(course.id).add_users(editor)
        return editor

    def outsider(self):
        return UserFactory()


@previous_run_links(True)
class CourseOptimizerParityAuthzTest(_ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Parity for a course editor role, with policy-based authoring permissions on."""

    def editor_for(self, course):
        editor = UserFactory()
        self.add_user_to_role_in_course(editor, COURSE_EDITOR.external_key, course.id)
        return editor

    def outsider(self):
        return self.unauthorized_user


# ---------------------------------------------------------------------------
# Side effects of reads
# ---------------------------------------------------------------------------

READS = [
    (operation, method, params)
    for operation in ("read_check", "read_update")
    for method in ("get", "head")
    for params in ({}, FULL)
]


@contextmanager
def record_item_creations():
    """
    Record the block type of each modulestore item created inside the block.
    A full link check read can create a missing course info block, so reads
    are checked for creations as well as for the writes ``record_effects`` sees.
    """
    created = []
    create_item = MixedModuleStore.create_item

    def spy_create_item(store, user_id, course_key, block_type, *args, **kwargs):
        created.append(block_type)
        return create_item(store, user_id, course_key, block_type, *args, **kwargs)

    with patch.object(MixedModuleStore, "create_item", spy_create_item):
        yield created


class _ReadsAreSideEffectFreeMixin:
    """
    Every read (both resources, both views, GET and HEAD) of runs that
    succeeded writes no row and no Mongo document, creates or updates no
    modulestore item and sends no signal or event. The reads start on a
    database that has never served a role or permission lookup, so the first
    read may insert the permission library's one policy version row and
    nothing else; v0's first read writes the same.
    """

    def editor_for(self, course):
        """Return a user who may read ``course``'s runs."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        chapter = BlockFactory.create(parent_location=self.course.location, category="chapter")
        sequential = BlockFactory.create(parent_location=chapter.location, category="sequential")
        vertical = BlockFactory.create(parent_location=sequential.location, category="vertical")
        self.html = BlockFactory.create(parent_location=vertical.location, category="html")

    def _reader_with_runs(self):
        """Return an editor of the course with a succeeded link check and link update, and no policy version row."""
        reader = self.editor_for(self.course)
        links = [[str(self.html.location), "https://broken.example.com/", LinkState.BROKEN]]
        record_link_check(reader, self.course.id, UserTaskStatus.SUCCEEDED, links=links)
        results = [{"id": "1", "type": "course_updates", "original_url": "a", "new_url": "b", "success": True}]
        record_link_update(reader, self.course.id, UserTaskStatus.SUCCEEDED, results=results)
        PolicyCacheControl.objects.all().delete()
        return reader

    def test_reads_have_no_side_effects(self):
        client = api_client(self._reader_with_runs())
        for index, (operation, method, params) in enumerate(READS):
            with record_item_creations() as created, record_effects(self) as effects:
                response = call(client, operation, self.course.id, params=params) if method == "get" else \
                    client.head(url(OPERATIONS[operation][1], self.course.id), params)
            read = (index, operation, method, params)
            assert response.status_code == 200, (read, response.content)
            if index == 0:
                assert effects["sql_writes"] in ([], FIRST_LOOKUP_WRITES), read
                assert effects["signals"] == (FIRST_LOOKUP_SIGNALS if effects["sql_writes"] else []), read
            else:
                assert effects["sql_writes"] == [], read
                assert effects["signals"] == [], read
            assert effects["events"] == [], read
            assert effects["mongo_writes"] == 0, read
            assert effects["update_item"] == [], read
            assert created == [], read

    def test_the_first_v0_read_writes_what_the_first_v1_read_writes(self):
        """
        On a database without the policy version row, a v0 read and a v1 read each write only that row.

        Each read gets its own user instance, because the role lookup is cached on the
        user object; reusing one would leave the second read with no lookup to make.
        """
        reader = self._reader_with_runs()
        first_lookup = (FIRST_LOOKUP_WRITES, FIRST_LOOKUP_SIGNALS, 0)
        for operation in ("read_check", "read_update"):
            seen = []
            for legacy in (True, False):
                PolicyCacheControl.objects.all().delete()
                fresh_reader = get_user_model().objects.get(pk=reader.pk)
                with record_effects(self) as effects:
                    response = call(api_client(fresh_reader), operation, self.course.id, legacy=legacy)
                assert response.status_code == 200, response.content
                seen.append((effects["sql_writes"], effects["signals"], effects["mongo_writes"]))
            assert seen == [first_lookup, first_lookup], operation

    def test_the_creation_recorder_sees_an_item_created(self):
        """The check above would notice a creation: one made inside the recorder is seen."""
        with record_item_creations() as created:
            modulestore().create_item(self.user.id, self.course.id, "course_info", block_id="probe")
        assert created == ["course_info"]


@previous_run_links(True)
class CourseOptimizerReadSideEffectsLegacyPermissionsTest(_ReadsAreSideEffectFreeMixin, ModuleStoreTestCase):
    """Reads by course staff with policy-based authoring permissions off."""

    def editor_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


@previous_run_links(True)
class CourseOptimizerReadSideEffectsAuthzTest(
    _ReadsAreSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """
    Reads by a course editor with policy-based authoring permissions on. The
    role is assigned through the permission library, which creates the policy
    version row; it is deleted before the reads.
    """

    def editor_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_EDITOR.external_key, course.id)
        return reader


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------

class _QueryCountMixin:
    """
    The default view of each read costs no more SQL queries than v0, and of a
    link check fewer Mongo reads, since v0 always builds the report. The full
    view costs at most one SQL query more than v0: v1 looks the course up,
    which v0's reads do not.
    """

    def editor_for(self, course):
        raise NotImplementedError

    def test_reads_cost_no_more_than_v0(self):
        course = CourseFactory.create()
        # With its overview built, the course lookup is one row read, as for a published course.
        CourseOverview.get_from_id(course.id)
        chapter = BlockFactory.create(parent_location=course.location, category="chapter")
        sequential = BlockFactory.create(parent_location=chapter.location, category="sequential")
        vertical = BlockFactory.create(parent_location=sequential.location, category="vertical")
        html = BlockFactory.create(parent_location=vertical.location, category="html")
        editor = self.editor_for(course)
        links = [[str(html.location), "https://broken.example.com/", LinkState.BROKEN]]
        record_link_check(editor, course.id, UserTaskStatus.SUCCEEDED, links=links)
        results = [{"id": "1", "type": "course_updates", "original_url": "a", "new_url": "b", "success": True}]
        record_link_update(editor, course.id, UserTaskStatus.SUCCEEDED, results=results)
        client = api_client(editor)
        for operation in ("read_check", "read_update"):
            legacy = measure(lambda op=operation: call(client, op, course.id, legacy=True))
            minimal = measure(lambda op=operation: call(client, op, course.id))
            full = measure(lambda op=operation: call(client, op, course.id, params=FULL))
            assert minimal[0] <= legacy[0], (operation, minimal, legacy)
            assert full[0] <= legacy[0] + 1, (operation, full, legacy)
            assert minimal[1] <= full[1] <= legacy[1], (operation, minimal, full, legacy)
            if operation == "read_check":
                assert minimal[1] < legacy[1], (operation, minimal, legacy)
            assert minimal[2] == full[2] == legacy[2] == 0, (operation, minimal, full, legacy)


@previous_run_links(True)
class CourseOptimizerQueryCountLegacyPermissionsTest(_QueryCountMixin, ModuleStoreTestCase):
    """Costs read by course staff, with policy-based authoring permissions off."""

    def editor_for(self, course):
        editor = UserFactory()
        CourseStaffRole(course.id).add_users(editor)
        return editor


@previous_run_links(True)
class CourseOptimizerQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
    """Costs read by a course editor, with policy-based authoring permissions on."""

    def editor_for(self, course):
        editor = UserFactory()
        self.add_user_to_role_in_course(editor, COURSE_EDITOR.external_key, course.id)
        return editor


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"
LEGACY_VIEW_NAMES = {
    "link_check": "LinkCheckView",
    "link_check_status": "LinkCheckStatusView",
    "rerun_link_update": "RerunLinkUpdateView",
    "rerun_link_update_status": "RerunLinkUpdateStatusView",
}


@ddt.ddt
@previous_run_links(True)
class CourseOptimizerRoutingTest(_PatchedTasksMixin, SharedModuleStoreTestCase):
    """The addresses, the v0 addresses, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_the_addresses(self):
        for name, path, viewset in (
            (LINK_CHECK, "link_check", CourseLinkCheckViewSet),
            (LINK_UPDATE, "previous_run_link_update", CoursePreviousRunLinkUpdateViewSet),
        ):
            assert url(name, COURSE_KEY) == f"/api/authoring/v1/courses/{COURSE_KEY}/{path}/"
            match = resolve(url(name, COURSE_KEY))
            assert match.func.cls is viewset
            assert routed_actions(match.func) == {"get": "retrieve", "post": "create"}

    def test_the_v0_addresses_are_unchanged(self):
        for name, view_name in LEGACY_VIEW_NAMES.items():
            assert legacy_url(name, COURSE_KEY) == f"/api/contentstore/v0/{name}/{COURSE_KEY}"
            view = resolve(legacy_url(name, COURSE_KEY)).func.cls
            assert (view.__module__, view.__name__) == (LEGACY_VIEWS, view_name)

    def test_v0_still_takes_a_slash_separated_key_that_v1_does_not(self):
        view = resolve("/api/contentstore/v0/link_check/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == (LEGACY_VIEWS, "LinkCheckView")
        for path in ("link_check", "previous_run_link_update"):
            with pytest.raises(Resolver404):
                resolve(f"/api/authoring/v1/courses/edX/DemoX/Demo_Course/{path}/")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v1 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert {"course_link_check", "course_previous_run_link_update"} <= set(names)

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data(*[(name, method) for name in (LINK_CHECK, LINK_UPDATE) for method in ("put", "patch", "delete")])
    @ddt.unpack
    def test_other_methods_are_not_allowed(self, name, method):
        response = getattr(api_client(UserFactory(is_staff=True)), method)(url(name, self.course.id), {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    @ddt.data(LINK_CHECK, LINK_UPDATE)
    def test_unsupported_accept_is_refused_with_406(self, name):
        response = api_client(UserFactory(is_staff=True)).get(url(name, self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # Nor ParseError or UnsupportedMediaType: a malformed or unsupported body is
    # answered with its own status but typed internal. Change the expected type
    # here once the library catalogues them.

    def test_a_malformed_json_body_is_a_400_typed_internal(self):
        response = api_client(UserFactory(is_staff=True)).post(
            url(LINK_UPDATE, self.course.id), data="{", content_type="application/json",
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="internal")
        assert envelope["title"] == "Internal Server Error"
        self.delay("v1", "start_update").assert_not_called()

    def test_an_unsupported_media_type_is_a_415_typed_internal(self):
        response = api_client(UserFactory(is_staff=True)).post(
            url(LINK_UPDATE, self.course.id), data="action=all", content_type="text/plain",
        )
        envelope = assert_error_envelope(response, expected_status=415, expected_type_slug="internal")
        assert envelope["title"] == "Internal Server Error"
        self.delay("v1", "start_update").assert_not_called()

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data(*[(key, path) for key in ("not-a-course-key", "edX/DemoX/Demo_Course", "edX+DemoX+Demo_Course")
                for path in ("link_check", "previous_run_link_update")])
    @ddt.unpack
    def test_an_unparsable_or_slash_form_key_is_an_html_404(self, key, path):
        response = api_client(UserFactory(is_staff=True)).get(f"/api/authoring/v1/courses/{key}/{path}/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        address = f"/api/authoring/v1/courses/{self.course.id}/link_check"
        response = api_client(UserFactory(is_staff=True)).get(address)
        assert response.status_code == 301
        assert response["Location"] == url(LINK_CHECK, self.course.id)
        with pytest.raises(Resolver404):
            resolve(address)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

LINK_CHECK_PATH = "/api/authoring/v1/courses/{course_key}/link_check/"
LINK_UPDATE_PATH = "/api/authoring/v1/courses/{course_key}/previous_run_link_update/"
LEGACY_PATHS = {
    "/api/contentstore/v0/link_check/{course_id}": ("post", "v0_link_check_create"),
    "/api/contentstore/v0/link_check_status/{course_id}": ("get", "v0_link_check_status_retrieve"),
    "/api/contentstore/v0/rerun_link_update/{course_id}": ("post", "v0_rerun_link_update_create"),
    "/api/contentstore/v0/rerun_link_update_status/{course_id}": ("get", "v0_rerun_link_update_status_retrieve"),
}
NEW_COMPONENTS = (
    "CourseOptimizerQueuedRun.v1",
    "LinkCheck.v1",
    "LinkCheckReport.v1",
    "LinkCheckReportSection.v1",
    "LinkCheckReportSubsection.v1",
    "LinkCheckReportUnit.v1",
    "LinkCheckReportBlock.v1",
    "LinkCheckPreviousRunLink.v1",
    "PreviousRunLinkUpdate.v1",
    "LinkUpdateResult.v1",
    "PreviousRunLinkUpdateRequest.v1",
    "LinkToUpdate.v1",
)


class CourseOptimizerSchemaTest(TestCase):
    """The four operations as published in the CMS authoring schema."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = published_schema()
        cls.components = cls.schema["components"]["schemas"]

    @staticmethod
    def _ref(node):
        return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    @staticmethod
    def _enum_ref(node):
        """The enum component a property refers to; a described property wraps the reference in allOf."""
        return node.get("$ref") or node["allOf"][0]["$ref"]

    def _enum(self, node):
        return set(resolve_ref(self.schema, {"$ref": self._enum_ref(node)})["enum"])

    def test_the_operations_are_published_for_the_sdk(self):
        for path, name, ok in (
            (LINK_CHECK_PATH, "link_check", "LinkCheck.v1"),
            (LINK_UPDATE_PATH, "previous_run_link_update", "PreviousRunLinkUpdate.v1"),
        ):
            operations = self.schema["paths"][path]
            assert sorted(operations) == ["get", "post"]
            assert operations["get"]["operationId"] == f"v1_courses_{name}_retrieve"
            assert operations["post"]["operationId"] == f"v1_courses_{name}_create"
            for operation in operations.values():
                assert operation["tags"] == ["openedx-platform-sdk"]
                assert "deprecated" not in operation
            assert self._ref(operations["get"]["responses"]["200"]) == ok
            assert self._ref(operations["post"]["responses"]["202"]) == "CourseOptimizerQueuedRun.v1"
            assert "200" not in operations["post"]["responses"]
            for method, statuses in (("get", ("400", "401", "403", "404")), ("post", ("401", "403", "404"))):
                for status in statuses:
                    assert self._ref(operations[method]["responses"][status]) == "ErrorResponse", (path, status)
        assert self._ref(self.schema["paths"][LINK_UPDATE_PATH]["post"]["responses"]["400"]) == "ErrorResponse"

    def test_the_read_parameters(self):
        for path in (LINK_CHECK_PATH, LINK_UPDATE_PATH):
            parameters = {p["name"]: p for p in self.schema["paths"][path]["get"]["parameters"]}
            assert set(parameters) == {"course_key", "view"}
            assert parameters["course_key"]["in"] == "path"
            assert parameters["course_key"]["schema"] == {"type": "string", "pattern": "^[^/]+$"}
            assert parameters["view"]["in"] == "query"
            assert not parameters["view"].get("required")
            assert parameters["view"]["schema"] == {"type": "string", "enum": ["full", "minimal"], "default": "minimal"}

    def test_starting_a_link_check_takes_no_body(self):
        assert "requestBody" not in self.schema["paths"][LINK_CHECK_PATH]["post"]

    def test_starting_a_link_update_takes_an_action_and_links(self):
        body = self.schema["paths"][LINK_UPDATE_PATH]["post"]["requestBody"]
        assert list(body["content"]) == ["application/json"]
        assert self._ref(body) == "PreviousRunLinkUpdateRequest.v1"
        request = self.components["PreviousRunLinkUpdateRequest.v1"]
        assert request["required"] == ["action"]
        assert self._enum(request["properties"]["action"]) == {"all", "single"}
        assert request["properties"]["data"]["items"] == {"$ref": "#/components/schemas/LinkToUpdate.v1"}
        assert sorted(self.components["LinkToUpdate.v1"]["required"]) == ["id", "type", "url"]

    def test_the_states_are_one_published_enum(self):
        """Both reads and both starts publish the same states, as one component."""
        nodes = [
            self.components["LinkCheck.v1"]["properties"]["status"],
            self.components["PreviousRunLinkUpdate.v1"]["properties"]["status"],
            self.components["CourseOptimizerQueuedRun.v1"]["properties"]["status"],
        ]
        assert [self._enum(node) for node in nodes] == [set(RUN_STATES)] * 3
        assert {self._enum_ref(node) for node in nodes} == {"#/components/schemas/CourseOptimizerStatusEnum"}

    def test_the_states_enum_keeps_its_name_beside_another_status_enum(self):
        """
        A ``status`` field with other choices elsewhere in the service leaves the states' name alone.

        Without a pinned name, drf-spectacular names both enums after their components instead.
        """
        def enum(values):
            """An enum property as drf-spectacular emits it before naming the enum components."""
            return {"type": "string", "enum": list(values), "x-spec-enum-id": list_hash([(v, v) for v in values])}

        schema = {"components": {"schemas": {
            "LinkCheck.v1": {"type": "object", "properties": {"status": enum(RUN_STATES)}},
            "Other": {"type": "object", "properties": {"status": enum(["open", "closed"])}},
        }}}
        result = postprocess_schema_enums(schema, SimpleNamespace(registry=ComponentRegistry()))
        assert sorted(result["components"]["schemas"]) == ["CourseOptimizerStatusEnum", "OtherStatusEnum"]

    def test_the_minimal_and_full_members(self):
        link_check = self.components["LinkCheck.v1"]
        assert set(link_check["properties"]) == {"status", "created_at", "error", "output"}
        assert sorted(link_check["required"]) == ["created_at", "error", "status"]
        nullable = {name for name, field in link_check["properties"].items() if field.get("nullable")}
        assert nullable == {"created_at", "error", "output"}
        link_update = self.components["PreviousRunLinkUpdate.v1"]
        assert set(link_update["properties"]) == {"status", "results"}
        assert link_update["required"] == ["status"]
        result = self.components["LinkUpdateResult.v1"]
        nullable = {name for name, field in result["properties"].items() if field.get("nullable")}
        assert nullable == {"original_url", "error_message"}

    def test_the_link_types_are_documented(self):
        description = self.components["LinkToUpdate.v1"]["properties"]["type"]["description"]
        for link_type in ("course_content", "course_updates", "handouts", "custom_pages"):
            assert f"'{link_type}'" in description, link_type

    def test_every_member_is_described(self):
        for name in NEW_COMPONENTS:
            component = self.components[name]
            assert component.get("description"), name
            properties = component.get("properties", {})
            undescribed = [field for field, spec in properties.items() if not spec.get("description")]
            assert undescribed == [], name

    def test_the_examples_are_valid_bodies(self):
        content = self.schema["paths"][LINK_UPDATE_PATH]["post"]["requestBody"]["content"]["application/json"]
        assert sorted(content["examples"]) == ["EveryLink", "SomeLinks"]
        for example in content["examples"].values():
            assert_matches_published_schema(example["value"], "PreviousRunLinkUpdateRequest.v1")
        content = self.schema["paths"][LINK_CHECK_PATH]["get"]["responses"]["200"]["content"]["application/json"]
        assert sorted(content["examples"]) == ["FullReport"]
        report = content["examples"]["FullReport"]["value"]
        assert_matches_published_schema(report, "LinkCheck.v1")
        assert set(report) == {"status", "created_at", "error", "output"}

    def test_the_v0_operations_are_deprecated_and_left_out_of_the_sdk(self):
        for path, (method, operation_id) in LEGACY_PATHS.items():
            operations = self.schema["paths"][path]
            assert sorted(operations) == [method], path
            assert operations[method]["operationId"] == operation_id
            assert operations[method]["deprecated"] is True
            assert "openedx-platform-sdk" not in operations[method].get("tags", [])

    def test_only_superseded_or_migrated_addresses_are_deprecated(self):
        """No operation is marked deprecated except by the legacy-address lists, so a stray mark is caught."""
        deprecated = {
            (path, method)
            for path, operations in self.schema["paths"].items()
            for method, operation in operations.items()
            if isinstance(operation, dict) and operation.get("deprecated")
        }
        assert {(path, method) for path, (method, _) in LEGACY_PATHS.items()} <= deprecated
        assert {(path, method) for path in (LINK_CHECK_PATH, LINK_UPDATE_PATH) for method in ("get", "post")} & \
            deprecated == set()
        stray = sorted(
            (path, method) for path, method in deprecated
            if path not in SUPERSEDED_PATHS and not path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        )
        assert stray == []
