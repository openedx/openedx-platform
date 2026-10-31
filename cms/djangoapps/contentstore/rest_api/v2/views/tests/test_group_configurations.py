"""Tests for the v2 course group configurations API."""

import json
from datetime import UTC, datetime
from functools import cached_property
from types import SimpleNamespace
from unittest.mock import patch

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.keys import CourseKey
from openedx_authz.constants.permissions import COURSES_MANAGE_GROUP_CONFIGURATIONS, COURSES_VIEW_GROUP_CONFIGURATIONS
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

from cms.djangoapps.contentstore.rest_api.pagination import ENVELOPE_KEYS
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
from cms.djangoapps.contentstore.rest_api.v2.serializers.group_configurations import VIEW_HELP_TEXT
from cms.djangoapps.contentstore.rest_api.v2.views.group_configurations import CourseGroupConfigurationsViewSet
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.contentstore.views.permissions import CanViewGroupConfigurations, IsExistingCourse
from cms.lib.spectacular import LEGACY_MIGRATED_PATH_PREFIXES, SUPERSEDED_PATHS
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
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
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel
from openedx.core.lib.teams_config import CONTENT_GROUPS_FOR_TEAMS, TeamsConfig
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory
from xmodule.partitions.partitions import ENROLLMENT_TRACK_PARTITION_ID, Group, UserPartition
from xmodule.partitions.partitions_service import get_all_partitions_for_course
from xmodule.split_test_block import SplitTestBlock

URL_NAME = "authoring_v2:course_group_configuration_list"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:group_configurations"
LEGACY_MODULE = "cms.djangoapps.contentstore.rest_api.v1.views.group_configurations"
SERVICE_MODULE = "cms.djangoapps.contentstore.rest_api.v2.group_configurations_service"
# The placeholder content group configuration's identifier is drawn by this
# function each time the configurations are read.
ID_GENERATOR = "cms.djangoapps.contentstore.course_group_config.generate_int_id"
TEAMS_LOOKUP = "lms.djangoapps.teams.team_partition_scheme.get_teams_in_teamset"
COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"

CONTENT_GROUPS_ID = 10
EXPERIMENT_ID = 20
UNUSED_EXPERIMENT_ID = 21
TEAM_PARTITION_ID = 30
TEAM_SET_ID = "debaters"
FIRST_TEAM_ID = 501
PLACEHOLDER_ID = 1234567
FULL = {"view": "full"}
ALL = {"page_size": 100}
CONFIGURATION_MEMBERS = {
    "id", "name", "scheme", "description", "parameters", "groups", "version", "active", "can_manage",
}
GROUP_MEMBERS = {"id", "name", "version"}


def url(course_key):
    """Return the address of the group configurations of ``course_key``."""
    return reverse(URL_NAME, kwargs={"course_key": str(course_key)})


def legacy_url(course_key):
    """Return the deprecated address of the group configurations of ``course_key``."""
    return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key)})


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

# The teams of each team-set, by course key and team-set id. Studio reads a
# team-set's teams from the teams app's tables, which the CMS test database
# does not have, so every test reads them from here.
TEAMS = {}


@pytest.fixture(autouse=True)
def _teams_from_memory():
    """Serve the teams of each team-set from ``TEAMS`` during a test, and forget them after it."""
    def teams_in(course_id, topic_id):
        return TEAMS.get((course_id, topic_id), [])

    with patch(TEAMS_LOOKUP, side_effect=teams_in):
        yield
    TEAMS.clear()


def _stored_course(course_key):
    return modulestore().get_course(CourseKey.from_string(str(course_key)))


def store_partitions(course_key, partitions):
    """Store ``partitions`` as the course's own user partitions."""
    course = _stored_course(course_key)
    course.user_partitions = partitions
    modulestore().update_item(course, ModuleStoreEnum.UserID.test)


def turn_experiments(course_key, on):
    """Turn content experiments on or off for the course, as Studio's advanced settings do."""
    course = _stored_course(course_key)
    course.advanced_modules = ["split_test"] if on else []
    modulestore().update_item(course, ModuleStoreEnum.UserID.test)


def content_groups(partition_id=CONTENT_GROUPS_ID, name="Content Groups"):
    return UserPartition(
        partition_id, name, "Groups for honors content", [Group(1, "Honors"), Group(2, "Regular")],
        scheme_id="cohort",
    )


def experiment(partition_id, name):
    return UserPartition(partition_id, name, "Which variant learners see", [Group(0, "Blue"), Group(1, "Green")])


def build_course(
    *, groups=False, experiments=False, experiments_on=False, tracks=0, gating=False, teams=None, team_groups_on=False,
):
    """
    Create a course and return its key and blocks.

    ``groups``: a content group configuration, one of whose groups a problem
    is restricted to. ``experiments``: two experiment configurations, one used
    by a content experiment. ``experiments_on``: content experiments turned on.
    ``tracks``: how many enrollment tracks (0 leaves the default track only).
    ``gating``: content type gating configurable in Studio, site-wide.
    ``teams``: a team-set with a user partition and this many teams, the
    first of which a problem is restricted to; ``None`` for no team-set.
    ``team_groups_on``: content groups for teams turned on for the course.
    """
    if teams is None:
        course = CourseFactory.create()
    else:
        team_set = {
            "id": TEAM_SET_ID, "name": "Debaters", "description": "Teams of the debate",
            "user_partition_id": TEAM_PARTITION_ID,
        }
        course = CourseFactory.create(teams_configuration=TeamsConfig({"team_sets": [team_set]}))
        TEAMS[(str(course.id), TEAM_SET_ID)] = [
            SimpleNamespace(id=FIRST_TEAM_ID + index, name=f"Debate team {index}") for index in range(teams)
        ]
    if team_groups_on:
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=CONTENT_GROUPS_FOR_TEAMS.name, course_id=course.id, enabled=True,
        )
    partitions = []
    if groups:
        partitions.append(content_groups())
    if experiments:
        partitions += [experiment(EXPERIMENT_ID, "Button colour"), experiment(UNUSED_EXPERIMENT_ID, "Unused")]
    store_partitions(course.id, partitions)
    turn_experiments(course.id, experiments_on)
    chapter = BlockFactory.create(parent_location=course.location, category="chapter", display_name="Week 1")
    sequential = BlockFactory.create(parent_location=chapter.location, category="sequential", display_name="Lesson")
    unit = BlockFactory.create(parent_location=sequential.location, category="vertical", display_name="Unit 1")
    problem = split_test = None
    if groups:
        problem = BlockFactory.create(
            parent_location=unit.location, category="problem", display_name="Honors problem",
            group_access={CONTENT_GROUPS_ID: [1]},
        )
    if teams:
        BlockFactory.create(
            parent_location=unit.location, category="problem", display_name="Team problem",
            group_access={TEAM_PARTITION_ID: [FIRST_TEAM_ID]},
        )
    if experiments:
        split_test = BlockFactory.create(
            parent_location=unit.location, category="split_test", display_name="Experiment",
            user_partition_id=EXPERIMENT_ID,
        )
    for mode in ("audit", "verified", "honor")[:tracks]:
        CourseModeFactory.create(course_id=course.id, mode_slug=mode)
    if gating:
        ContentTypeGatingConfig.objects.create(enabled=False, studio_override_enabled=True)
    return SimpleNamespace(id=course.id, unit=unit, problem=problem, split_test=split_test)


# Courses each read and comparison runs on: between them, every way the
# configurations listed can change.
SHAPES = {
    # Only the unsaved content group configuration.
    "bare": {},
    # Experiments turned on, none defined: still only the unsaved configuration.
    "experiments_on_none_defined": {"experiments_on": True},
    # Experiment configurations defined, experiments turned off: not listed.
    "experiments_off": {"experiments": True},
    # One enrollment track: its configuration is not listed.
    "one_track": {"groups": True, "tracks": 1},
    # Everything listed: two tracks, Studio-configurable gating, content groups, experiments.
    "rich": {"groups": True, "experiments": True, "experiments_on": True, "tracks": 2, "gating": True},
    # Content groups for teams on, and a team-set with two teams: its configuration is listed after content groups.
    "team_groups": {"groups": True, "teams": 2, "team_groups_on": True},
}


def legacy_items(body):
    """The configurations of a v1 body in the order v2 lists them."""
    return [*body["all_group_configurations"], *(body["experiment_group_configurations"] or [])]


def without_usage(configurations):
    """``configurations`` without ``usage``, on configurations and groups alike."""
    stripped = []
    for configuration in configurations:
        item = {key: value for key, value in configuration.items() if key != "usage"}
        item["groups"] = [{key: value for key, value in group.items() if key != "usage"} for group in item["groups"]]
        stripped.append(item)
    return stripped


def without_can_manage(configurations):
    return [{key: value for key, value in item.items() if key != "can_manage"} for item in configurations]


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

def superuser_without_staff_status():
    """
    Return a superuser whose staff status is off.

    Saving a superuser turns its staff status on (a pre-save receiver of the
    student app does), so the status is cleared with an update, which skips it.
    """
    user = UserFactory(is_superuser=True)
    get_user_model().objects.filter(pk=user.pk).update(is_staff=False)
    user.refresh_from_db()
    assert (user.is_superuser, user.is_staff) == (True, False)
    return user


class _AccessMixin:
    """Checks a caller's status against the expected one, and that v1 decides the same for the same caller."""

    def assert_list(self, user, expected, course_key=None, can_manage=None):
        """
        Assert the status of a listing by ``user``; on success that every
        configuration carries ``can_manage``, as v1 reports it once.
        """
        course_key = course_key or self.course.id
        response = api_client(user).get(url(course_key), ALL)
        legacy = api_client(user).get(legacy_url(course_key))
        assert response.status_code == expected, response.content
        assert legacy.status_code == expected, f"v1 decided {legacy.status_code} for the same caller"
        if expected != 200:
            assert_refused(response, expected)
            return
        results = response.json()["results"]
        assert [configuration["id"] for configuration in results] == [CONTENT_GROUPS_ID]
        assert [configuration["can_manage"] for configuration in results] == [can_manage]
        assert legacy.json()["can_manage"] is can_manage


@ddt.ddt
class GroupConfigurationsAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may list group configurations when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        store_partitions(cls.course.id, [content_groups()])
        cls.other_course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    def test_anonymous_caller_is_refused_with_401(self):
        self.assert_list(None, 401)

    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self):
        self.assert_list(None, 401, course_key=UNKNOWN_COURSE_KEY)

    def test_user_without_a_role_is_refused_with_403(self):
        self.assert_list(UserFactory(), 403)

    def test_unknown_course_is_404_before_permission_is_checked(self):
        self.assert_list(UserFactory(), 404, course_key=UNKNOWN_COURSE_KEY)

    def test_limited_staff_is_refused(self):
        self.assert_list(self._user_with(CourseLimitedStaffRole(self.course.id)), 403)

    def test_staff_and_instructors_of_another_course_are_refused(self):
        self.assert_list(self._user_with(CourseStaffRole(self.other_course.id)), 403)
        self.assert_list(self._user_with(CourseInstructorRole(self.other_course.id)), 403)

    def test_course_staff_may_list_and_manage(self):
        self.assert_list(self._user_with(CourseStaffRole(self.course.id)), 200, can_manage=True)

    def test_course_instructor_may_list_and_manage(self):
        self.assert_list(self._user_with(CourseInstructorRole(self.course.id)), 200, can_manage=True)

    def test_org_staff_and_org_instructor_may_list_and_manage(self):
        self.assert_list(self._user_with(OrgStaffRole(self.course.id.org)), 200, can_manage=True)
        self.assert_list(self._user_with(OrgInstructorRole(self.course.id.org)), 200, can_manage=True)

    def test_global_staff_may_list_and_manage(self):
        self.assert_list(UserFactory(is_staff=True), 200, can_manage=True)

    def test_a_superuser_saved_through_the_model_is_global_staff_and_may_list_and_manage(self):
        """Saving a superuser turns its staff status on, so it passes as global staff, on v1 as on v2."""
        user = UserFactory(is_superuser=True, is_staff=False)
        assert user.is_staff is True
        self.assert_list(user, 200, can_manage=True)

    def test_a_superuser_without_staff_status_is_refused(self):
        """
        Studio read access comes from staff status or a course or organization
        role, and being a superuser is neither; v1 refuses the same caller.
        """
        self.assert_list(superuser_without_staff_status(), 403)

    def test_a_version_only_course_key_is_404(self):
        """The key parses but names no course run; v1 has no address that takes it."""
        assert_refused(api_client(UserFactory(is_staff=True)).get(url(VERSION_ONLY_KEY)), 404)

    def test_a_library_is_not_a_course(self):
        library_key = LibraryFactory.create().location.library_key
        assert_refused(api_client(UserFactory(is_staff=True)).get(url(library_key)), 404)


@ddt.ddt
class GroupConfigurationsAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    Who may list group configurations when policy-based authoring permissions
    are on: listing needs the permission to view them, and can_manage reports
    the permission to manage them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        store_partitions(cls.course.id, [content_groups()])
        cls.other_course = CourseFactory.create()

    def _user_with(self, role, course_key=None):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, course_key or self.course.id)
        return user

    def test_anonymous_caller_is_refused_with_401(self):
        self.assert_list(None, 401)

    def test_user_without_a_role_is_refused_with_403(self):
        self.assert_list(self.unauthorized_user, 403)

    def test_unknown_course_is_404_before_permission_is_checked(self):
        self.assert_list(self.unauthorized_user, 404, course_key=UNKNOWN_COURSE_KEY)

    def test_a_role_that_may_only_view_lists_without_can_manage(self):
        self.assert_list(self._user_with(COURSE_AUDITOR.external_key), 200, can_manage=False)

    @ddt.data(COURSE_EDITOR.external_key, COURSE_STAFF.external_key, COURSE_ADMIN.external_key)
    def test_roles_that_may_manage_list_with_can_manage(self, role):
        self.assert_list(self._user_with(role), 200, can_manage=True)

    @ddt.data(COURSE_LIMITED_STAFF.external_key, COURSE_DATA_RESEARCHER.external_key, COURSE_BETA_TESTER.external_key)
    def test_other_roles_are_refused(self, role):
        self.assert_list(self._user_with(role), 403)

    def test_a_role_on_another_course_is_refused(self):
        self.assert_list(self._user_with(COURSE_ADMIN.external_key, self.other_course.id), 403)

    def test_legacy_course_staff_without_a_policy_role_is_refused(self):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_list(user, 403)

    def test_staff_and_superuser_may_list_and_manage_without_a_role(self):
        """The mixin's superuser was saved through the model, which turned its staff status on."""
        self.assert_list(self.staff_user, 200, can_manage=True)
        self.assert_list(self.super_user, 200, can_manage=True)

    def test_a_superuser_without_staff_status_may_list_and_manage_without_a_role(self):
        """The permission policy admits superusers on a course whether or not they have staff status, as on v1."""
        self.assert_list(superuser_without_staff_status(), 200, can_manage=True)

    def test_an_inactive_session_with_a_role_is_accepted(self):
        """The policy does not look at whether the account is activated, on v2 as on v1."""
        user = self._user_with(COURSE_AUDITOR.external_key)
        client = session_client(user)
        user.is_active = False
        user.save()
        assert client.get(url(self.course.id)).status_code == 200
        assert client.get(legacy_url(self.course.id)).status_code == 200


class GroupConfigurationsPermissionWiringTest(SimpleTestCase):
    """Which permissions the viewset checks, and what the view permission asks for."""

    @staticmethod
    def _declared(view):
        # The JWT middleware appends NotJwtRestrictedApplication to views that take JWTs.
        return [
            type(permission) for permission in view.get_permissions()
            if not isinstance(permission, NotJwtRestrictedApplication)
        ]

    def test_the_listing_checks_the_course_then_the_permission_to_view(self):
        view = CourseGroupConfigurationsViewSet()
        for action in ("list", None, "metadata"):
            view.action = action
            assert self._declared(view) == [IsAuthenticated, IsExistingCourse, CanViewGroupConfigurations], action

    def test_any_other_action_is_refused(self):
        view = CourseGroupConfigurationsViewSet()
        for action in ("retrieve", "create", "update", "partial_update", "destroy"):
            view.action = action
            with pytest.raises(ImproperlyConfigured):
                view.get_permissions()

    def test_a_routed_action_the_viewset_does_not_check_is_never_served(self):
        """A request reaching such an action is answered with a server error, not the handler's body."""

        class Unchecked(CourseGroupConfigurationsViewSet):
            permission_classes = CourseGroupConfigurationsViewSet.permission_classes

            def destroy(self, request, **kwargs):  # pylint: disable=unused-argument
                raise AssertionError("the handler ran without a permission check")

        request = APIRequestFactory().delete(url(COURSE_KEY))
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_active=True))
        response = Unchecked.as_view({"delete": "destroy"})(request, course_key=CourseKey.from_string(COURSE_KEY))
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")

    def test_the_view_permission_asks_for_its_policy_and_studio_read_access(self):
        course_key = CourseKey.from_string("course-v1:edX+Wiring+Run")
        request = SimpleNamespace(user=object())
        view = SimpleNamespace(kwargs={"course_key": course_key})
        target = "cms.djangoapps.contentstore.views.permissions.user_has_course_permission"
        for decision in (True, False):
            with patch(target, return_value=decision) as check:
                assert CanViewGroupConfigurations().has_permission(request, view) is decision
            check.assert_called_once_with(
                request.user, COURSES_VIEW_GROUP_CONFIGURATIONS.identifier, course_key, LegacyAuthoringPermission.READ,
            )

    def test_the_view_permission_refuses_a_view_without_a_course(self):
        target = "cms.djangoapps.contentstore.views.permissions.user_has_course_permission"
        with patch(target, return_value=True) as check:
            permission = CanViewGroupConfigurations()
            assert permission.has_permission(SimpleNamespace(user=object()), SimpleNamespace(kwargs={})) is False
        check.assert_not_called()


class GroupConfigurationsAuthenticationTest(SharedModuleStoreTestCase):
    """How callers authenticate, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _course_staff(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def test_a_jwt_caller_may_list(self):
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self._course_staff())}"}
        response = APIClient().get(url(self.course.id), **header)
        assert response.status_code == 200, response.content
        assert response.json()["count"] == 1

    def test_a_session_caller_may_list(self):
        response = session_client(self._course_staff()).get(url(self.course.id))
        assert response.status_code == 200, response.content
        assert response.json()["count"] == 1

    def test_an_inactive_session_is_authenticated(self):
        """
        A session whose account is not activated is accepted, as on v1. Global
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
        """Bearer tokens are not accepted here, though v1 takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert_refused(APIClient().get(url(self.course.id), **header), 401)
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@ddt.ddt
class GroupConfigurationsListTest(ModuleStoreTestCase):
    """What a listing returns, in which order, and how it pages."""

    def setUp(self):
        super().setUp()
        self.client = api_client(UserFactory(is_staff=True))

    def get(self, course_key, **params):
        response = self.client.get(url(course_key), params)
        assert response.status_code == 200, response.content
        return response.json()

    def test_the_configurations_are_listed_in_studio_order(self):
        """Enrollment tracks, content type gating, content groups, then the experiments in course order."""
        course = build_course(**SHAPES["rich"])
        results = self.get(course.id, **ALL)["results"]
        assert [(c["id"], c["scheme"]) for c in results] == [
            (ENROLLMENT_TRACK_PARTITION_ID, "enrollment_track"),
            (CONTENT_GATING_PARTITION_ID, "content_type_gate"),
            (CONTENT_GROUPS_ID, "cohort"),
            (EXPERIMENT_ID, "random"),
            (UNUSED_EXPERIMENT_ID, "random"),
        ]
        assert [c["read_only"] for c in results[:3]] == [True, True, False]
        assert ["read_only" in c for c in results[3:]] == [False, False]

    def test_the_default_view_leaves_out_usage(self):
        course = build_course(**SHAPES["rich"])
        for configuration in self.get(course.id, **ALL)["results"]:
            assert set(configuration) == CONFIGURATION_MEMBERS | ({"read_only"} & set(configuration))
            assert configuration["groups"]
            assert [set(group) for group in configuration["groups"]] == [GROUP_MEMBERS] * len(configuration["groups"])

    def test_the_minimal_view_is_the_default(self):
        course = build_course(**SHAPES["rich"])
        assert self.get(course.id, view="minimal", **ALL) == self.get(course.id, **ALL)

    def test_the_full_view_reports_where_each_configuration_and_group_is_used(self):
        course = build_course(**SHAPES["rich"])
        by_id = {c["id"]: c for c in self.get(course.id, **FULL, **ALL)["results"]}
        unit_key = course.id.make_usage_key("vertical", course.unit.location.block_id)
        unit_url = reverse_usage_url("container_handler", unit_key)
        groups = by_id[CONTENT_GROUPS_ID]
        assert groups["usage"] is None
        assert [(group["id"], group["usage"]) for group in groups["groups"]] == [
            (1, [{"label": "Unit 1 / Honors problem", "url": unit_url}]),
            (2, []),
        ]
        # The experiment has no content for its groups, so Studio reports a problem with it.
        message = modulestore().get_item(course.split_test.location).general_validation_message()
        assert by_id[EXPERIMENT_ID]["usage"] == [{
            "label": "Unit 1 / Experiment",
            "url": reverse_usage_url(
                "container_handler", course.id.make_usage_key("split_test", course.split_test.location.block_id),
            ),
            "validation": {"type": message.type, "text": str(message.text)},
        }]
        assert [group["usage"] for group in by_id[EXPERIMENT_ID]["groups"]] == [None, None]
        assert by_id[UNUSED_EXPERIMENT_ID]["usage"] == []
        tracks = by_id[ENROLLMENT_TRACK_PARTITION_ID]
        assert tracks["usage"] is None
        assert [group["usage"] for group in tracks["groups"]] == [[], []]

    def test_an_experiment_without_problems_has_a_null_validation(self):
        course = build_course(**SHAPES["rich"])
        with patch.object(SplitTestBlock, "general_validation_message", return_value=None):
            legacy = self.client.get(legacy_url(course.id)).json()
            by_id = {c["id"]: c for c in self.get(course.id, **FULL, **ALL)["results"]}
        assert [use["validation"] for use in by_id[EXPERIMENT_ID]["usage"]] == [None]
        assert by_id[EXPERIMENT_ID]["usage"] == legacy["experiment_group_configurations"][0]["usage"]

    def test_whether_the_caller_may_manage_is_asked_once_per_request(self):
        course = build_course(**SHAPES["rich"])
        with patch(f"{SERVICE_MODULE}.user_has_course_permission", return_value=False) as check:
            response = self.client.get(url(course.id), {**FULL, **ALL})
        assert response.status_code == 200, response.content
        assert [c["can_manage"] for c in response.json()["results"]] == [False] * 5
        check.assert_called_once()
        user, permission, course_key, legacy = check.call_args.args
        assert (permission, course_key, legacy) == (
            COURSES_MANAGE_GROUP_CONFIGURATIONS.identifier, course.id, LegacyAuthoringPermission.WRITE,
        )
        assert user.is_staff is True

    def test_a_course_without_content_groups_lists_an_unsaved_content_group_configuration(self):
        course = build_course()
        with patch(ID_GENERATOR, side_effect=[111111, 222222]):
            first = self.get(course.id)["results"]
            second = self.get(course.id)["results"]
        assert [(c["id"], c["scheme"], c["name"], c["groups"]) for c in first] == [
            (111111, "cohort", "Content Groups", []),
        ]
        assert [c["id"] for c in second] == [222222]
        assert "read_only" not in first[0]
        assert _stored_course(course.id).user_partitions == []

    def test_saved_content_groups_replace_the_unsaved_configuration(self):
        course = build_course(groups=True)
        with patch(ID_GENERATOR) as generate:
            results = self.get(course.id)["results"]
        generate.assert_not_called()
        assert [(c["id"], c["scheme"]) for c in results] == [(CONTENT_GROUPS_ID, "cohort")]

    @ddt.data(True, False)
    def test_experiment_configurations_are_listed_only_when_experiments_are_on(self, on):
        course = build_course(groups=True, experiments=True, experiments_on=on)
        schemes = [c["scheme"] for c in self.get(course.id, **ALL)["results"]]
        assert schemes == (["cohort", "random", "random"] if on else ["cohort"])

    @ddt.data((0, False), (1, False), (2, True), (3, True))
    @ddt.unpack
    def test_the_enrollment_track_configuration_is_listed_with_two_tracks_or_more(self, tracks, listed):
        course = build_course(groups=True, tracks=tracks)
        results = self.get(course.id)["results"]
        assert any(c["scheme"] == "enrollment_track" for c in results) is listed
        if listed:
            assert len(results[0]["groups"]) == tracks

    @ddt.data(True, False)
    def test_content_type_gating_is_listed_only_when_studio_may_set_it(self, studio_override):
        """Gating is on for the course either way; Studio lists its configuration only when it may set it."""
        course = build_course(groups=True, tracks=2)
        ContentTypeGatingConfig.objects.create(
            enabled=True, enabled_as_of=datetime(2018, 1, 1, tzinfo=UTC), studio_override_enabled=studio_override,
        )
        partitions = get_all_partitions_for_course(_stored_course(course.id))
        assert "content_type_gate" in [partition.scheme.name for partition in partitions]
        schemes = [c["scheme"] for c in self.get(course.id)["results"]]
        gating = ["content_type_gate"] if studio_override else []
        assert schemes == ["enrollment_track", *gating, "cohort"]

    @ddt.data((True, 2, True), (False, 2, False), (True, 0, False))
    @ddt.unpack
    def test_a_team_set_is_listed_only_with_content_groups_for_teams_on_and_a_team_in_it(self, on, teams, listed):
        """
        With content groups for teams off the team-set has no configuration;
        with them on, its configuration is listed once it has a team. v1 lists
        the same configurations.
        """
        course = build_course(groups=True, teams=teams, team_groups_on=on)
        legacy = self.client.get(legacy_url(course.id))
        assert legacy.status_code == 200, legacy.content
        results = self.get(course.id, **FULL, **ALL)["results"]
        listing = [(c["id"], c["scheme"]) for c in results]
        assert listing == [(c["id"], c["scheme"]) for c in legacy_items(legacy.json())]
        assert listing == [(CONTENT_GROUPS_ID, "cohort")] + ([(TEAM_PARTITION_ID, "team")] if listed else [])
        if listed:
            team_set = results[1]
            assert (team_set["read_only"], team_set["parameters"]) == (
                True, {"course_id": str(course.id), "team_set_id": TEAM_SET_ID},
            )
            assert [(group["id"], group["name"]) for group in team_set["groups"]] == [
                (FIRST_TEAM_ID, "Debate team 0"), (FIRST_TEAM_ID + 1, "Debate team 1"),
            ]
            unit_key = course.id.make_usage_key("vertical", course.unit.location.block_id)
            unit_url = reverse_usage_url("container_handler", unit_key)
            assert [group["usage"] for group in team_set["groups"]] == [
                [{"label": "Unit 1 / Team problem", "url": unit_url}], [],
            ]

    def test_the_envelope_has_the_seven_keys(self):
        course = build_course(**SHAPES["rich"])
        body = self.get(course.id)
        assert set(body) == set(ENVELOPE_KEYS)
        assert (body["count"], body["num_pages"], body["current_page"], body["start"]) == (5, 1, 1, 0)
        assert (body["next"], body["previous"]) == (None, None)

    def test_page_size_splits_the_pages_in_order(self):
        course = build_course(**SHAPES["rich"])
        ids = [c["id"] for c in self.get(course.id, **ALL)["results"]]
        first = self.get(course.id, page_size=2)
        assert [c["id"] for c in first["results"]] == ids[:2]
        assert (first["count"], first["num_pages"], first["current_page"], first["start"]) == (5, 3, 1, 0)
        assert "page=2" in first["next"]
        last = self.get(course.id, page_size=2, page=3, **FULL)
        assert [c["id"] for c in last["results"]] == ids[4:]
        assert (last["current_page"], last["start"], last["next"]) == (3, 4, None)
        assert "usage" in last["results"][0]

    def test_the_default_page_size_is_ten(self):
        course = build_course()
        store_partitions(course.id, [content_groups(100 + index, f"Groups {index}") for index in range(12)])
        body = self.get(course.id)
        assert (len(body["results"]), body["count"], body["num_pages"]) == (10, 12, 2)
        assert [c["id"] for c in body["results"]] == list(range(100, 110))

    @ddt.data("99", "0", "abc")
    def test_a_page_out_of_range_is_404(self, page):
        course = build_course()
        response = self.client.get(url(course.id), {"page": page})
        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == "Invalid page."

    def test_an_unknown_view_is_refused(self):
        course = build_course()
        response = self.client.get(url(course.id), {"view": "default"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"view": ['"default" is not a valid choice.']}

    @ddt.data({}, FULL)
    def test_the_body_matches_the_published_schema(self, params):
        course = build_course(**SHAPES["rich"])
        for page in (1, 2):
            body = self.get(course.id, page=page, page_size=3, **params)
            assert_matches_published_schema(body, "PaginatedGroupConfiguration.v2List")

    def test_other_query_parameters_are_ignored(self):
        course = build_course(**SHAPES["rich"])
        assert self.get(course.id, ordering="-name", fields="id", scheme="random") == self.get(course.id)

    def test_head_answers_without_a_body(self):
        course = build_course()
        for params in ({}, FULL):
            response = self.client.head(url(course.id), params)
            assert response.status_code == 200
            assert response.content == b""


# ---------------------------------------------------------------------------
# Requests v1 and v2 answer differently
# ---------------------------------------------------------------------------

# Requests v1 and v2 answer differently beyond the error body, as
# (request, v1 answer, v2 answer, test in GroupConfigurationsResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("any read", "an object holding all_group_configurations and experiment_group_configurations",
     "a page of configurations, the experiment configurations last", "test_the_two_lists_become_one_page"),
    ("any read", "can_manage once, beside the lists", "can_manage on every configuration",
     "test_can_manage_is_on_every_configuration"),
    ("read without view", "usage on every configuration and group", "no usage; view=full includes it",
     "test_usage_is_returned_only_with_view_full"),
    ("any read", "should_show_enrollment_track",
     "not returned; the enrollment_track configuration is listed exactly when it was true",
     "test_should_show_enrollment_track"),
    ("any read", "should_show_experiment_groups", "not returned: it describes the course",
     "test_should_show_experiment_groups"),
    ("any read", "mfe_proctored_exam_settings_url", "not returned: it describes the course",
     "test_mfe_proctored_exam_settings_url"),
    ("read with view=default", "200, the parameter ignored", "400", "test_view_default"),
    ("read with a page past the last, zero or not a number", "200, the parameter ignored", "404",
     "test_an_invalid_page"),
    ("any read for a course the modulestore returns nothing for", "500", "404",
     "test_a_course_the_modulestore_returns_nothing_for"),
    ("OPTIONS by a signed-in caller without access, or for an unknown course", "200", "403 or 404",
     "test_options"),
    ("POST, PUT, PATCH or DELETE by a signed-in caller without access, or for an unknown course", "405",
     "403 or 404", "test_a_method_the_endpoint_does_not_serve"),
    ("any read with a Bearer token", "200", "401", "test_a_bearer_token"),
    ("read for a slash-separated course key", "404 JSON", "HTML 404 at routing", "test_a_slash_separated_course_key"),
]


class GroupConfigurationsResponseDifferencesTest(ModuleStoreTestCase):
    """Each request v1 and v2 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.client = api_client(UserFactory(is_staff=True))

    @cached_property
    def course(self):
        """
        The course listing every kind of configuration, built on first use.

        Building it makes content type gating configurable in Studio for the
        whole site, which lists that configuration on every course read
        afterwards; a test about other courses must not touch it.
        """
        return build_course(**SHAPES["rich"])

    def both(self, course_key=None, **params):
        """Read the course through v1 and v2 (with ``params``), assert both succeed and return both bodies."""
        course_key = course_key or self.course.id
        legacy = self.client.get(legacy_url(course_key))
        new = self.client.get(url(course_key), {**ALL, **params})
        assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
        return legacy.json(), new.json()

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_the_two_lists_become_one_page(self):
        legacy, new = self.both(**FULL)
        assert len(legacy["all_group_configurations"]) == 3
        assert len(legacy["experiment_group_configurations"]) == 2
        assert set(new) == set(ENVELOPE_KEYS)
        assert [c["id"] for c in new["results"]] == [c["id"] for c in legacy_items(legacy)]

    def test_can_manage_is_on_every_configuration(self):
        legacy, new = self.both()
        assert legacy["can_manage"] is True
        assert [c["can_manage"] for c in new["results"]] == [True] * 5
        assert all("can_manage" not in c for c in legacy_items(legacy))

    def test_usage_is_returned_only_with_view_full(self):
        legacy, new = self.both()
        assert all("usage" in c for c in legacy_items(legacy))
        assert all("usage" in g for c in legacy["all_group_configurations"] for g in c["groups"])
        assert all("usage" not in c and all("usage" not in g for g in c["groups"]) for c in new["results"])
        _, full = self.both(**FULL)
        assert without_can_manage(full["results"]) == legacy_items(legacy)

    def test_should_show_enrollment_track(self):
        one_track = build_course(groups=True, tracks=1)
        for course_key, shown in ((self.course.id, True), (one_track.id, False)):
            legacy, new = self.both(course_key)
            assert legacy["should_show_enrollment_track"] is shown
            assert any(c["scheme"] == "enrollment_track" for c in new["results"]) is shown
            assert "should_show_enrollment_track" not in new

    def test_should_show_experiment_groups(self):
        """Experiments turned on with none defined lists the same as turned off; only v1's flag tells them apart."""
        on_none_defined = build_course(**SHAPES["experiments_on_none_defined"])
        off = build_course(**SHAPES["experiments_off"])
        assert not ContentTypeGatingConfig.objects.exists()
        with patch(ID_GENERATOR, return_value=PLACEHOLDER_ID):
            for course_key, shown, experiments in ((on_none_defined.id, True, []), (off.id, False, None)):
                legacy, new = self.both(course_key)
                assert legacy["should_show_experiment_groups"] is shown
                assert legacy["experiment_group_configurations"] == experiments
                assert [c["id"] for c in legacy["all_group_configurations"]] == [PLACEHOLDER_ID]
                assert [c["id"] for c in new["results"]] == [PLACEHOLDER_ID]
                assert "should_show_experiment_groups" not in new

    def test_mfe_proctored_exam_settings_url(self):
        legacy, new = self.both()
        assert "mfe_proctored_exam_settings_url" in legacy
        assert "mfe_proctored_exam_settings_url" not in new
        assert all("mfe_proctored_exam_settings_url" not in c for c in new["results"])

    def test_view_default(self):
        """v1 takes no view parameter and ignores it; v2 refuses a value it does not know."""
        legacy = self.client.get(legacy_url(self.course.id), {"view": "default"})
        assert legacy.status_code == 200, legacy.content
        assert legacy.json() == self.client.get(legacy_url(self.course.id)).json()
        response = self.client.get(url(self.course.id), {"view": "default"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"view": ['"default" is not a valid choice.']}

    def test_an_invalid_page(self):
        """v1 is not paged and ignores page; v2 answers a page it does not have with 404."""
        whole = self.client.get(legacy_url(self.course.id)).json()
        for page in ("99", "0", "abc"):
            legacy = self.client.get(legacy_url(self.course.id), {"page": page})
            assert legacy.status_code == 200, (page, legacy.content)
            assert legacy.json() == whole, page
            response = self.client.get(url(self.course.id), {"page": page})
            envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
            assert envelope["detail"] == "Invalid page.", page

    def test_a_course_the_modulestore_returns_nothing_for(self):
        with patch.object(MixedModuleStore, "get_course", return_value=None):
            legacy = self.client.get(legacy_url(self.course.id))
            new = self.client.get(url(self.course.id))
        assert legacy.status_code == 500, legacy.content
        assert_refused(new, 404)

    def test_options(self):
        """v1 answers OPTIONS to any signed-in caller; v2 checks the course and the caller first."""
        outsider = api_client(UserFactory())
        assert outsider.options(legacy_url(self.course.id)).status_code == 200
        assert_refused(outsider.options(url(self.course.id)), 403)
        assert self.client.options(url(self.course.id)).status_code == 200
        assert outsider.options(legacy_url(UNKNOWN_COURSE_KEY)).status_code == 200
        assert_refused(outsider.options(url(UNKNOWN_COURSE_KEY)), 404)

    def test_a_method_the_endpoint_does_not_serve(self):
        """
        v1 answers any method but GET with 405; v2 checks the course and the
        caller first, so only a caller who may read the course learns of 405.
        """
        outsider = api_client(UserFactory())
        for method in ("post", "put", "patch", "delete"):
            send = getattr(outsider, method)
            assert send(legacy_url(self.course.id), {}, format="json").status_code == 405, method
            assert_refused(send(url(self.course.id), {}, format="json"), 403)
            assert send(legacy_url(UNKNOWN_COURSE_KEY), {}, format="json").status_code == 405, method
            assert_refused(send(url(UNKNOWN_COURSE_KEY), {}, format="json"), 404)
            response = getattr(self.client, method)(url(self.course.id), {}, format="json")
            assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_a_bearer_token(self):
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200
        assert_refused(APIClient().get(url(self.course.id), **header), 401)

    def test_a_slash_separated_course_key(self):
        """v1 routes the slash form and reports the course unknown in JSON; v2 has no route for it."""
        legacy = self.client.get("/api/contentstore/v1/group_configurations/edX/NoSuch/Course")
        assert legacy.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        new = self.client.get("/api/authoring/v2/courses/edX/NoSuch/Course/group_configurations/")
        assert new.status_code == 404
        assert new["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Parity with v1
# ---------------------------------------------------------------------------

# Members of an error body that differ between v1 and v2 for the same
# request. The comparison asserts nothing else differs, and that each of these
# occurs in the error cases below.
INTENTIONAL_DIFFERENCES = [
    ("type", "errors use the standardized envelope"),
    ("title", "errors use the standardized envelope"),
    ("status", "errors use the standardized envelope"),
    ("detail", "errors use the standardized envelope"),
    ("developer_message", "the v1 error body carries developer_message; the envelope does not"),
    ("error_code", "the v1 refusal and not-found bodies carry error_code; the envelope does not"),
]


def _undeclared(changed):
    """The changed paths no declared intentional difference of this module covers."""
    return undeclared_paths(changed, INTENTIONAL_DIFFERENCES)


def test_only_declared_differences_are_tolerated():
    assert _undeclared({"type", "developer_message", "error_code"}) == set()
    changed = {"[0].usage", "can_manage", "results", "errors"}
    assert _undeclared(changed) == changed


def test_the_comparison_sees_a_usage_that_turns_null_or_is_dropped():
    """The comparison the parity checks rely on reports a usage list that becomes null, or is dropped."""
    used = [{"label": "a", "url": "b"}]
    legacy = [{"id": 1, "usage": [], "groups": [{"id": 2, "usage": used}]}]
    turned_null = [{"id": 1, "usage": None, "groups": [{"id": 2, "usage": used}]}]
    dropped = [{"id": 1, "usage": [], "groups": [{"id": 2}]}]
    assert changed_paths(legacy, turned_null) == {"[0].usage"}
    assert changed_paths(legacy, dropped) == {"[0].groups[0].usage[0].label", "[0].groups[0].usage[0].url"}


@ddt.ddt
class _ParityMixin:
    """
    v2 against v1 for the same course and caller: v2's configurations are v1's
    two lists in order, each with v1's can_manage, identical with view=full
    and without usage otherwise; errors differ only by the declared
    differences.
    """

    def reader_for(self, course_key):
        """Return a user who may view the course's group configurations."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user with no access to the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.observed = set()

    @ddt.data(*SHAPES)
    def test_the_configurations_are_v1s_on_both_views(self, shape):
        course = build_course(**SHAPES[shape])
        client = api_client(self.reader_for(course.id))
        # The unsaved configuration's identifier is drawn afresh by each read; fixed here so reads compare.
        with patch(ID_GENERATOR, return_value=PLACEHOLDER_ID):
            legacy = client.get(legacy_url(course.id))
            full = client.get(url(course.id), {**FULL, **ALL})
            minimal = client.get(url(course.id), ALL)
        assert legacy.status_code == full.status_code == minimal.status_code == 200
        legacy, full, minimal = legacy.json(), full.json()["results"], minimal.json()["results"]
        expected = legacy_items(legacy)
        assert expected, shape
        assert changed_paths(expected, without_can_manage(full)) == set(), (expected, full)
        assert changed_paths(without_usage(expected), without_can_manage(minimal)) == set(), (expected, minimal)
        assert {c["can_manage"] for c in full + minimal} == {legacy["can_manage"]}

    def _error_pairs(self, course_key):
        """``(legacy, new, status)`` for each error both versions produce."""
        cases = [
            (api_client(), course_key, 401),
            (api_client(self.outsider()), course_key, 403),
            (api_client(self.outsider()), UNKNOWN_COURSE_KEY, 404),
        ]
        for client, key, status in cases:
            yield client.get(legacy_url(key)), client.get(url(key)), status

    def compare(self, legacy, new, expected_status):
        """Assert both statuses and that the bodies differ only as declared; return both bodies."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_body, new_body = json.loads(legacy.content), json.loads(new.content)
        changed = changed_paths(legacy_body, new_body)
        assert _undeclared(changed) == set(), f"undeclared differences: legacy={legacy_body} new={new_body}"
        self.observed |= changed
        return legacy_body, new_body

    def test_errors_differ_only_by_the_envelope_and_every_declared_difference_occurs(self):
        course = build_course(groups=True)
        for legacy, new, status in self._error_pairs(course.id):
            self.compare(legacy, new, status)
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in self.observed)
        ]
        assert missing == []


class GroupConfigurationsParityLegacyPermissionsTest(_ParityMixin, ModuleStoreTestCase):
    """Parity for course staff, with policy-based authoring permissions off."""

    def reader_for(self, course_key):
        reader = UserFactory()
        CourseStaffRole(course_key).add_users(reader)
        return reader

    def outsider(self):
        return UserFactory()


class GroupConfigurationsParityAuthzTest(_ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Parity for a course auditor, with policy-based authoring permissions on."""

    def reader_for(self, course_key):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course_key)
        return reader

    def outsider(self):
        return self.unauthorized_user


# ---------------------------------------------------------------------------
# Side effects and cost of listing
# ---------------------------------------------------------------------------

READS = [("get", {}), ("get", FULL), ("head", {}), ("head", FULL)]
# Read only on a course listing more than two configurations: a page past the
# last is answered with 404.
SECOND_PAGE_READ = ("get", {"page_size": 2, "page": 2, **FULL})


@ddt.ddt
class _ListingIsSideEffectFreeMixin:
    """
    Every listing (default, full, and HEAD on both views), on a course
    listing only the unsaved content group configuration, on one listing
    every kind (where a second page is read too) and on one listing a
    team-set's configuration, writes no row and no Mongo document, stores
    nothing through the modulestore and sends no signal or event. The
    listings start on a database that has never served a role or permission
    lookup, so the first may insert the permission library's one policy
    version row and nothing else; v1 inserts the same row.
    """

    def reader_for(self, course_key):
        """Return a user who may view the course, leaving the database as no lookup had touched it."""
        raise NotImplementedError

    @ddt.data(("bare", False), ("rich", True), ("team_groups", False))
    @ddt.unpack
    def test_listings_have_no_side_effects(self, shape, paged):
        """``paged``: the course lists more than two configurations, so a second page of two is read too."""
        course = build_course(**SHAPES[shape])
        partitions = _stored_course(course.id).user_partitions
        client = api_client(self.reader_for(course.id))
        assert not PolicyCacheControl.objects.exists()
        for index, (method, params) in enumerate([*READS, SECOND_PAGE_READ] if paged else READS):
            with record_effects(self) as effects:
                response = getattr(client, method)(url(course.id), params)
            read = (shape, index, method, params)
            first = index == 0
            assert response.status_code == 200, (read, response.content)
            if first:
                assert (response.json()["count"] > 2) is paged, read
            if (method, params) == SECOND_PAGE_READ:
                assert response.json()["current_page"] == 2, read
            assert effects["sql_writes"] == (FIRST_LOOKUP_WRITES if first else []), read
            assert effects["signals"] == (FIRST_LOOKUP_SIGNALS if first else []), read
            assert effects["events"] == [], read
            assert effects["mongo_writes"] == 0, read
            assert effects["update_item"] == [], read
        assert _stored_course(course.id).user_partitions == partitions

    def test_the_v1_listing_makes_the_same_first_lookup_write(self):
        course = build_course(**SHAPES["rich"])
        client = api_client(self.reader_for(course.id))
        with record_effects(self) as effects:
            response = client.get(legacy_url(course.id))
        assert response.status_code == 200, response.content
        assert effects["sql_writes"] == FIRST_LOOKUP_WRITES
        assert effects["signals"] == FIRST_LOOKUP_SIGNALS
        assert effects["mongo_writes"] == 0


class GroupConfigurationsSideEffectsLegacyPermissionsTest(_ListingIsSideEffectFreeMixin, ModuleStoreTestCase):
    """Listings by course staff with policy-based authoring permissions off."""

    def reader_for(self, course_key):
        reader = UserFactory()
        CourseStaffRole(course_key).add_users(reader)
        PolicyCacheControl.objects.all().delete()
        return reader


class GroupConfigurationsSideEffectsAuthzTest(
    _ListingIsSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """
    Listings by a course auditor with policy-based authoring permissions on.
    The role is assigned through the permission library, which creates the
    policy version row; it is deleted before the listings.
    """

    def reader_for(self, course_key):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course_key)
        PolicyCacheControl.objects.all().delete()
        return reader


@ddt.ddt
class _ListingCostMixin:
    """A listing, default or full, costs no more SQL queries or Mongo reads than the v1 read of the same course."""

    def reader_for(self, course_key):
        raise NotImplementedError

    @ddt.data("bare", "rich", "team_groups")
    def test_a_listing_costs_no_more_than_the_v1_read(self, shape):
        course = build_course(**SHAPES[shape])
        client = api_client(self.reader_for(course.id))
        legacy = measure(lambda: client.get(legacy_url(course.id)))
        minimal = measure(lambda: client.get(url(course.id), ALL))
        full = measure(lambda: client.get(url(course.id), {**FULL, **ALL}))
        for new in (minimal, full):
            assert new[0] <= legacy[0], (shape, new, legacy)
            assert new[1] <= legacy[1], (shape, new, legacy)
            assert new[2] == legacy[2] == 0, (shape, new, legacy)

    def test_the_sql_cost_stays_flat_and_mongo_reads_stay_within_v1s_as_configurations_are_added(self):
        """
        One content group configuration and eight cost the same SQL queries,
        on both views; at each size, neither view makes more Mongo reads than
        v1 does for the same course.
        """
        course = build_course(groups=True)
        client = api_client(self.reader_for(course.id))

        def costs():
            legacy = measure(lambda: client.get(legacy_url(course.id)))
            new = [
                measure(lambda params=params: client.get(url(course.id), params)) for params in (ALL, {**FULL, **ALL})
            ]
            for _, mongo_reads, mongo_writes in new:
                assert mongo_reads <= legacy[1], (new, legacy)
                assert mongo_writes == 0, (new, legacy)
            return [sql for sql, _, _ in new]

        few = costs()
        store_partitions(course.id, [content_groups(100 + index, f"Groups {index}") for index in range(8)])
        assert costs() == few


class GroupConfigurationsCostLegacyPermissionsTest(_ListingCostMixin, ModuleStoreTestCase):
    """Costs for course staff, with policy-based authoring permissions off."""

    def reader_for(self, course_key):
        reader = UserFactory()
        CourseStaffRole(course_key).add_users(reader)
        return reader


class GroupConfigurationsCostAuthzTest(CourseAuthoringAuthzTestMixin, _ListingCostMixin, ModuleStoreTestCase):
    """Costs for a course auditor, with policy-based authoring permissions on."""

    def reader_for(self, course_key):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course_key)
        return reader


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

@ddt.ddt
class GroupConfigurationsRoutingTest(SharedModuleStoreTestCase):
    """The address, the v1 address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.client = api_client(UserFactory(is_staff=True))

    def test_the_address(self):
        assert url(COURSE_KEY) == f"/api/authoring/v2/courses/{COURSE_KEY}/group_configurations/"
        match = resolve(url(COURSE_KEY))
        assert match.func.cls is CourseGroupConfigurationsViewSet
        assert routed_actions(match.func) == {"get": "list"}
        assert match.kwargs == {"course_key": CourseKey.from_string(COURSE_KEY)}

    def test_the_v1_address_is_unchanged(self):
        assert legacy_url(COURSE_KEY) == f"/api/contentstore/v1/group_configurations/{COURSE_KEY}"
        view = resolve(legacy_url(COURSE_KEY)).func.cls
        assert (view.__module__, view.__name__) == (LEGACY_MODULE, "CourseGroupConfigurationsView")

    def test_v1_still_takes_a_slash_separated_key_that_v2_does_not(self):
        view = resolve("/api/contentstore/v1/group_configurations/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == (LEGACY_MODULE, "CourseGroupConfigurationsView")
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v2/courses/edX/DemoX/Demo_Course/group_configurations/")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v2 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert "course_group_configuration_list" in names

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data("post", "put", "patch", "delete")
    def test_other_methods_are_not_allowed(self, method):
        response = getattr(self.client, method)(url(self.course.id), {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_unsupported_accept_is_refused_with_406(self):
        response = self.client.get(url(self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data("not-a-course-key", "edX/DemoX/Demo_Course", "edX+DemoX+Demo_Course")
    def test_an_unparsable_or_slash_form_key_is_an_html_404(self, key):
        response = self.client.get(f"/api/authoring/v2/courses/{key}/group_configurations/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        address = f"/api/authoring/v2/courses/{self.course.id}/group_configurations"
        response = self.client.get(address)
        assert response.status_code == 301
        assert response["Location"] == url(self.course.id)
        with pytest.raises(Resolver404):
            resolve(address)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

class GroupConfigurationsSchemaTest(TestCase):
    """The listing as published in the CMS authoring schema."""

    PATH = "/api/authoring/v2/courses/{course_key}/group_configurations/"
    LEGACY = "/api/contentstore/v1/group_configurations/{course_id}"
    COMPONENTS = (
        "GroupConfiguration.v2", "GroupConfigurationGroup.v2", "GroupConfigurationUsage.v2",
        "GroupConfigurationValidation.v2",
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = published_schema()
        cls.components = cls.schema["components"]["schemas"]

    @staticmethod
    def _ref(node):
        return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def _operation(self):
        operations = self.schema["paths"][self.PATH]
        assert sorted(operations) == ["get"]
        return operations["get"]

    def test_the_listing_is_published_for_the_sdk(self):
        operation = self._operation()
        assert operation["operationId"] == "v2_courses_group_configurations_list"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert sorted(operation["responses"]) == ["200", "400", "401", "403", "404"]
        for status in ("400", "401", "403", "404"):
            assert self._ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_listing_publishes_the_seven_envelope_keys(self):
        name = self._ref(self._operation()["responses"]["200"])
        assert name == "PaginatedGroupConfiguration.v2List"
        envelope = self.components[name]
        assert list(envelope["properties"]) == list(ENVELOPE_KEYS)
        assert envelope["required"] == list(ENVELOPE_KEYS)
        assert envelope["properties"]["results"] == {
            "type": "array", "items": {"$ref": "#/components/schemas/GroupConfiguration.v2"},
        }

    def test_the_parameters(self):
        parameters = {p["name"]: p for p in self._operation()["parameters"]}
        assert set(parameters) == {"course_key", "view", "page", "page_size"}
        assert parameters["course_key"]["in"] == "path"
        assert parameters["course_key"]["schema"] == {"type": "string", "pattern": "^[^/]+$"}
        assert parameters["view"]["in"] == "query"
        assert parameters["view"]["description"] == VIEW_HELP_TEXT
        assert not parameters["view"].get("required")
        assert parameters["view"]["schema"] == {"type": "string", "enum": ["full", "minimal"], "default": "minimal"}
        for name in ("page", "page_size"):
            assert (parameters[name]["in"], parameters[name]["schema"]) == ("query", {"type": "integer"})

    def test_usage_validation_and_read_only_are_optional_and_usage_is_nullable(self):
        configuration = self.components["GroupConfiguration.v2"]
        assert set(configuration["required"]) == CONFIGURATION_MEMBERS
        assert set(configuration["properties"]) == CONFIGURATION_MEMBERS | {"usage", "read_only"}
        group = self.components["GroupConfigurationGroup.v2"]
        assert set(group["required"]) == GROUP_MEMBERS
        assert set(group["properties"]) == GROUP_MEMBERS | {"usage"}
        for component in (configuration, group):
            assert component["properties"]["usage"]["nullable"] is True
            assert component["properties"]["usage"]["items"] == {
                "$ref": "#/components/schemas/GroupConfigurationUsage.v2",
            }
        usage = self.components["GroupConfigurationUsage.v2"]
        assert set(usage["required"]) == {"label", "url"}
        assert usage["properties"]["validation"]["nullable"] is True
        assert set(self.components["GroupConfigurationValidation.v2"]["required"]) == {"type", "text"}

    def test_the_description_leaves_out_the_course_flags_and_says_the_placeholder_id_changes(self):
        description = self._operation()["description"]
        assert (
            "Flags that describe the course rather than its group configurations, such as whether content "
            "experiments are turned on, are not part of this resource."
        ) in description
        assert "whose id changes from request to request" in description
        assert "/settings/" not in description

    def test_every_member_is_described(self):
        for name in self.COMPONENTS:
            component = self.components[name]
            assert component.get("description"), name
            assert [field for field, spec in component["properties"].items() if not spec.get("description")] == [], name

    def test_the_example_is_a_valid_page(self):
        content = self._operation()["responses"]["200"]["content"]["application/json"]
        page = content["examples"]["ContentGroups"]["value"]
        assert set(page) == set(ENVELOPE_KEYS)
        assert len(page["results"]) == 1
        assert_matches_published_schema(page, "PaginatedGroupConfiguration.v2List")

    def test_the_v1_operation_is_deprecated_and_left_out_of_the_sdk(self):
        operations = self.schema["paths"][self.LEGACY]
        assert sorted(operations) == ["get"]
        assert operations["get"]["operationId"] == "v1_group_configurations_retrieve"
        assert operations["get"]["deprecated"] is True
        assert "openedx-platform-sdk" not in operations["get"].get("tags", [])

    def test_only_superseded_or_migrated_addresses_are_deprecated(self):
        """No operation is marked deprecated except by the legacy-address lists, so a stray mark is caught."""
        deprecated = {
            (path, method)
            for path, operations in self.schema["paths"].items()
            for method, operation in operations.items()
            if isinstance(operation, dict) and operation.get("deprecated")
        }
        assert (self.LEGACY, "get") in deprecated
        assert (self.PATH, "get") not in deprecated
        stray = sorted(
            (path, method) for path, method in deprecated
            if path not in SUPERSEDED_PATHS and not path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        )
        assert stray == []
