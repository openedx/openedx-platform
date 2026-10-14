"""Tests for the v2 course outline API."""

import re
from contextlib import contextmanager
from datetime import datetime
from functools import cache
from importlib import import_module
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import ddt
import jsonschema
import pymongo
import pytest
import pytz
from django.conf import settings
from django.db import connection
from django.db.models.signals import post_save
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import Resolver404, include, path, resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import UsageKey
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
from openedx_events.content_authoring.signals import XBLOCK_UPDATED
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.config.waffle import CUSTOM_RELATIVE_DATES
from cms.djangoapps.contentstore.rest_api.serializers.common import ProctoringErrorListSerializer
from cms.djangoapps.contentstore.rest_api.v1.views.course_index import CourseIndexView
from cms.djangoapps.contentstore.rest_api.v2.course_outline_service import get_full_outline
from cms.djangoapps.contentstore.rest_api.v2.serializers.course_outline import CourseOutlineProctoringErrorSerializer
from cms.djangoapps.contentstore.rest_api.v2.views.course_outline import CourseOutlineViewSet
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.utils import get_pages_and_resources_url
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from common.djangoapps.course_action_state.models import CourseRerunState
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
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content_staging import api as content_staging_api
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.content_tagging.toggles import DISABLE_TAGGING_FEATURE
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.course_block import DEFAULT_START_DATE
from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory

UNKNOWN_COURSE_KEY = "course-v1:NoSuchOrg+NoSuchCourse+NoSuchRun"
VERSION_GUID = "0123456789abcdef01234567"
COURSE_NOT_FOUND = "The requested course does not exist."
NOT_AN_OUTLINE_CONTAINER = "No container in this course's outline has this key."
VIEWS = ("default", "full")
PRE_1900 = datetime(1800, 1, 1, tzinfo=pytz.UTC)


def outline_url(course_key):
    """Return the address of the outline of ``course_key``."""
    return reverse("authoring_v2:course_outline", kwargs={"course_key": course_key})


def v1_url(course_key):
    """Return the deprecated course index address of ``course_key``."""
    return reverse("cms.djangoapps.contentstore:v1:course_index", kwargs={"course_id": str(course_key)})


def _client(user=None):
    """An API client that returns server errors as responses, authenticated as ``user`` if given."""
    client = APIClient()
    client.raise_request_exception = False
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _session_client(user):
    """A client signed in to a browser session as ``user``."""
    user.set_password("password")
    user.save()
    client = APIClient()
    assert client.login(username=user.username, password="password")
    return client


def _params(view):
    return {"view": "full"} if view == "full" else {}


def build_course(sections=2, subsections=2, units=2, **course_kwargs):
    """
    Create a course holding ``sections`` sections of ``subsections`` subsections of
    ``units`` units, each unit holding one problem. Return the course as stored.
    """
    course = CourseFactory.create(**course_kwargs)
    # Each block is created under its parent's location, so the factory reads the
    # parent afresh rather than writing back a copy that misses earlier siblings.
    for s in range(sections):
        section = BlockFactory.create(
            parent_location=course.location, category="chapter", display_name=f"Section {s}",
        ).location
        for ss in range(subsections):
            subsection = BlockFactory.create(
                parent_location=section, category="sequential", display_name=f"Subsection {s}.{ss}",
            ).location
            for u in range(units):
                unit = BlockFactory.create(
                    parent_location=subsection, category="vertical", display_name=f"Unit {s}.{ss}.{u}",
                ).location
                BlockFactory.create(parent_location=unit, category="problem", display_name=f"Problem {s}.{ss}.{u}")
    return modulestore().get_course(course.id)


def outline_blocks(course_key):
    """``(id, category)`` of every outline block of the course in pre-order, down to units."""
    store = modulestore()
    blocks = []

    def walk(block):
        blocks.append((str(block.location), block.category))
        if block.has_children and block.category != "vertical":
            for child in block.get_children():
                walk(child)

    walk(store.get_course(course_key))
    return blocks


def full_structure_ids(node):
    """Pre-order ids of a full-view ``course_structure`` node and its descendants."""
    ids = [node["id"]]
    for child in node.get("child_info", {}).get("children", []):
        ids.extend(full_structure_ids(child))
    return ids


def tree_ids(node):
    """Pre-order ids of a default-view node and the descendants it holds."""
    ids = [node["id"]]
    for child in node.get("children", []):
        ids.extend(tree_ids(child))
    return ids


def tree_nodes(node):
    """Every default-view node of ``node``'s tree, in pre-order."""
    nodes = [node]
    for child in node.get("children", []):
        nodes.extend(tree_nodes(child))
    return nodes


def structure_nodes(node):
    """Every full-view ``course_structure`` node, in pre-order."""
    nodes = [node]
    for child in node.get("child_info", {}).get("children", []):
        nodes.extend(structure_nodes(child))
    return nodes


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _as_json_schema(node):
    """An OpenAPI 3.0 schema as JSON Schema: ``nullable`` becomes a null alternative."""
    if isinstance(node, list):
        return [_as_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    converted = {key: _as_json_schema(value) for key, value in node.items() if key != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


@cache
def _published_schema():
    """The schema the outline routes publish, generated from the real URLconfs."""
    # Importing the service URLconf registers the course key converter the authoring routes use.
    import_module(settings.ROOT_URLCONF)
    patterns = [
        path("api/contentstore/", include("cms.djangoapps.contentstore.rest_api.urls")),
        path("api/authoring/v2/", include("cms.djangoapps.contentstore.rest_api.v2.authoring_urls")),
    ]
    return SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)


DEFAULT_SHAPE = "CourseOutline"
FULL_SHAPE = "CourseOutlineFull"
READ_SHAPE = "CourseOutlineRead"


def assert_matches_published_schema(body, component):
    """Assert ``body`` is valid against the published schema component ``component``."""
    components = _as_json_schema(_published_schema()["components"]["schemas"])
    jsonschema.validate(body, {"$ref": f"#/components/schemas/{component}", "components": {"schemas": components}})


def assert_matches_one_read_shape(body, shape):
    """Assert ``body`` matches the read response, and of its two shapes exactly ``shape``."""
    assert_matches_published_schema(body, READ_SHAPE)
    assert_matches_published_schema(body, shape)
    other = FULL_SHAPE if shape == DEFAULT_SHAPE else DEFAULT_SHAPE
    with pytest.raises(jsonschema.ValidationError):
        assert_matches_published_schema(body, other)


# ---------------------------------------------------------------------------
# Side-effect recording
# ---------------------------------------------------------------------------

@contextmanager
def count_mongo_calls():
    """Count Mongo reads and writes made inside the block."""
    reads = ("find", "find_one", "count_documents", "aggregate")
    writes = ("insert_one", "insert_many", "replace_one", "update_one", "update_many", "bulk_write",
              "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace", "find_one_and_delete")
    counts = {"reads": 0, "writes": 0}

    def counting(kind, original):
        def wrapper(self, *args, **kwargs):
            counts[kind] += 1
            return original(self, *args, **kwargs)
        return wrapper

    collection = pymongo.collection.Collection
    with patch.multiple(collection, **{
        name: counting("reads" if name in reads else "writes", getattr(collection, name)) for name in reads + writes
    }):
        yield counts


def sql_writes(queries):
    """``(statement, table)`` for each INSERT, UPDATE and DELETE among the captured ``queries``."""
    writes = []
    for query in queries.captured_queries:
        words = query["sql"].split()
        statement = words[0].upper()
        if statement in ("INSERT", "DELETE"):
            writes.append((statement, words[2].strip('"`')))
        elif statement == "UPDATE":
            writes.append((statement, words[1].strip('"`')))
    return writes


@contextmanager
def record_effects(test_case):
    """
    Record, inside the block, SQL writes, Mongo writes, modulestore updates,
    model saves, xblock-updated events and course-published signals. Commit
    callbacks registered inside the block are run, so events sent on commit
    are seen.
    """
    effects = {"saves": [], "xblock_updated": [], "published": [], "update_item": []}
    update_item = MixedModuleStore.update_item

    def spy_update_item(store, xblock, *args, **kwargs):
        effects["update_item"].append(str(xblock.location))
        return update_item(store, xblock, *args, **kwargs)

    def on_save(sender, **kwargs):  # pylint: disable=unused-argument
        effects["saves"].append(sender.__name__)

    def on_xblock_updated(**kwargs):
        effects["xblock_updated"].append(str(kwargs["xblock_info"].usage_key))

    def on_published(sender, **kwargs):  # pylint: disable=unused-argument
        effects["published"].append(kwargs)

    post_save.connect(on_save, weak=False)
    XBLOCK_UPDATED.connect(on_xblock_updated, weak=False)
    SignalHandler.course_published.connect(on_published, weak=False)
    try:
        with test_case.captureOnCommitCallbacks(execute=True), \
                CaptureQueriesContext(connection) as queries, \
                count_mongo_calls() as mongo, \
                patch.object(MixedModuleStore, "update_item", spy_update_item):
            yield effects
            effects["sql_writes"] = sql_writes(queries)
            effects["mongo_writes"] = mongo["writes"]
    finally:
        post_save.disconnect(on_save)
        XBLOCK_UPDATED.disconnect(on_xblock_updated)
        SignalHandler.course_published.disconnect(on_published)


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

def assert_inactive_jwt_reads(user, course_key, view):
    """Deactivate ``user``; assert a JWT for it reads both views of v2 and the v1 read."""
    user.is_active = False
    user.save()
    header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}
    response = APIClient().get(outline_url(course_key), _params(view), **header)
    assert response.status_code == 200, response.content
    assert_matches_one_read_shape(response.json(), FULL_SHAPE if view == "full" else DEFAULT_SHAPE)
    assert APIClient().get(v1_url(course_key), **header).status_code == 200
    user.refresh_from_db()
    assert user.is_active is False


class _AccessMatrixMixin:
    """Checks a caller's status on both views against the expected one and the v1 decision."""

    def _call(self, user, view, course_key):
        return _client(user).get(outline_url(course_key), _params(view))

    def assert_access(self, user, view, expected, course_key=None):
        """Assert the status, the error envelope when refused, and agreement with v1."""
        course_key = course_key or self.course.id
        response = self._call(user, view, course_key)
        v1_status = _client(user).get(v1_url(course_key)).status_code
        assert response.status_code == expected, response.content
        if expected != 200:
            slug = {401: "authn", 403: "authz", 404: "not-found"}[expected]
            assert_error_envelope(response, expected_status=expected, expected_type_slug=slug)
        else:
            assert_matches_one_read_shape(response.json(), FULL_SHAPE if view == "full" else DEFAULT_SHAPE)
        assert v1_status == expected, f"v1 decided {v1_status} for the same caller"


@ddt.ddt
class OutlineAccessLegacyPermissionsTest(_AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may read the outline when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)
        cls.other_course = build_course(sections=1, subsections=1, units=1)

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    @ddt.data(*VIEWS)
    def test_anonymous_caller_is_refused_with_401(self, view):
        self.assert_access(None, view, 401)

    @ddt.data(*VIEWS)
    def test_user_without_a_role_is_refused_with_403(self, view):
        self.assert_access(UserFactory(), view, 403)

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        self.assert_access(UserFactory(), view, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*VIEWS)
    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self, view):
        self.assert_access(None, view, 401, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*VIEWS)
    def test_limited_staff_is_refused(self, view):
        self.assert_access(self._user_with(CourseLimitedStaffRole(self.course.id)), view, 403)

    @ddt.data(*VIEWS)
    def test_staff_of_another_course_is_refused(self, view):
        self.assert_access(self._user_with(CourseStaffRole(self.other_course.id)), view, 403)

    @ddt.data(*VIEWS)
    def test_course_staff_may_read(self, view):
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), view, 200)

    @ddt.data(*VIEWS)
    def test_course_instructor_may_read(self, view):
        self.assert_access(self._user_with(CourseInstructorRole(self.course.id)), view, 200)

    @ddt.data(*VIEWS)
    def test_org_staff_and_org_instructor_may_read(self, view):
        self.assert_access(self._user_with(OrgStaffRole(self.course.id.org)), view, 200)
        self.assert_access(self._user_with(OrgInstructorRole(self.course.id.org)), view, 200)

    @ddt.data(*VIEWS)
    def test_global_staff_and_superuser_may_read(self, view):
        self.assert_access(UserFactory(is_staff=True), view, 200)
        self.assert_access(UserFactory(is_superuser=True), view, 200)

    @ddt.data(*VIEWS)
    def test_an_inactive_session_is_not_authenticated(self, view):
        """
        A session whose account is not activated is refused with 401, global
        staff included. v1 authenticates such a session and decides access
        as for an active account, so it admits inactive global staff and
        refuses inactive course staff, whose course role does not count while
        inactive.
        """
        for user, v1_expected in (
            (UserFactory(is_staff=True), 200),
            (self._user_with(CourseStaffRole(self.course.id)), 403),
        ):
            client = _session_client(user)
            assert client.get(outline_url(self.course.id), _params(view)).status_code == 200, "active control"
            user.is_active = False
            user.save()
            response = client.get(outline_url(self.course.id), _params(view))
            assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
            assert client.get(v1_url(self.course.id)).status_code == v1_expected

    @ddt.data(*VIEWS)
    def test_jwt_caller_may_read(self, view):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        response = APIClient().get(
            outline_url(self.course.id), _params(view), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(user)}",
        )
        assert response.status_code == 200

    @ddt.data(*VIEWS)
    def test_an_inactive_jwt_caller_who_is_global_staff_may_read(self, view):
        """A JWT is not checked for an activated account, on v2 as on v1."""
        assert_inactive_jwt_reads(UserFactory(is_staff=True), self.course.id, view)

    @ddt.data(*VIEWS)
    def test_bearer_token_is_refused(self, view):
        """Bearer tokens are not accepted here, though the v1 read takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        response = APIClient().get(outline_url(self.course.id), _params(view), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert APIClient().get(v1_url(self.course.id), HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200

    def test_a_subtree_of_another_course_is_refused_without_naming_it(self):
        """A caller allowed on one course cannot read another course's blocks through it."""
        user = self._user_with(CourseStaffRole(self.course.id))
        other_section = modulestore().get_course(self.other_course.id).get_children()[0]
        response = _client(user).get(outline_url(self.course.id), {"usage_key": str(other_section.location)})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"usage_key": [NOT_AN_OUTLINE_CONTAINER]}
        body = response.content.decode()
        assert str(self.other_course.id.course) not in body
        assert other_section.location.block_id not in body
        # The same caller is refused the other course outright.
        assert _client(user).get(outline_url(self.other_course.id)).status_code == 403


@ddt.ddt
class OutlineAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may read the outline when policy-based authoring permissions are on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)
        cls.other_course = build_course(sections=1, subsections=1, units=1)

    def _user_with(self, role, course_key=None):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, course_key or self.course.id)
        return user

    @ddt.data(*VIEWS)
    def test_anonymous_caller_is_refused_with_401(self, view):
        self.assert_access(None, view, 401)

    @ddt.data(*VIEWS)
    def test_user_without_a_role_is_refused_with_403(self, view):
        self.assert_access(self.unauthorized_user, view, 403)

    @ddt.data(*[(view, role) for view in VIEWS for role in (COURSE_AUDITOR.external_key, COURSE_ADMIN.external_key)])
    @ddt.unpack
    def test_a_role_on_another_course_is_refused(self, view, role):
        user = self._user_with(role, self.other_course.id)
        self.assert_access(user, view, 200, course_key=self.other_course.id)
        self.assert_access(user, view, 403)

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        self.assert_access(self.unauthorized_user, view, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*[(view, role) for view in VIEWS for role in (
        COURSE_AUDITOR.external_key, COURSE_EDITOR.external_key, COURSE_STAFF.external_key, COURSE_ADMIN.external_key,
    )])
    @ddt.unpack
    def test_roles_that_may_view_the_course_may_read(self, view, role):
        self.assert_access(self._user_with(role), view, 200)

    @ddt.data(*[(view, role) for view in VIEWS for role in (
        COURSE_LIMITED_STAFF.external_key, COURSE_DATA_RESEARCHER.external_key, COURSE_BETA_TESTER.external_key,
    )])
    @ddt.unpack
    def test_other_roles_are_refused(self, view, role):
        self.assert_access(self._user_with(role), view, 403)

    @ddt.data(*VIEWS)
    def test_legacy_course_staff_without_a_policy_role_is_refused(self, view):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_access(user, view, 403)

    @ddt.data(*VIEWS)
    def test_an_inactive_session_with_a_role_is_not_authenticated(self, view):
        """v1 admits it, as the permission policy does not look at whether the account is activated."""
        user = self._user_with(COURSE_AUDITOR.external_key)
        client = _session_client(user)
        assert client.get(outline_url(self.course.id), _params(view)).status_code == 200, "active control"
        user.is_active = False
        user.save()
        response = client.get(outline_url(self.course.id), _params(view))
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert client.get(v1_url(self.course.id)).status_code == 200

    @ddt.data(*VIEWS)
    def test_an_inactive_jwt_caller_with_a_role_may_read(self, view):
        """A JWT is not checked for an activated account, on v2 as on v1."""
        user = self._user_with(COURSE_AUDITOR.external_key)
        assert_inactive_jwt_reads(user, self.course.id, view)

    @ddt.data(*VIEWS)
    def test_staff_and_superuser_may_read_without_a_role(self, view):
        self.assert_access(self.staff_user, view, 200)
        self.assert_access(self.super_user, view, 200)


# ---------------------------------------------------------------------------
# The default view
# ---------------------------------------------------------------------------

@ddt.ddt
class OutlineDefaultViewTest(ModuleStoreTestCase):
    """The outline tree, its depth cap and the child addresses."""

    def setUp(self):
        super().setUp()
        self.reader = UserFactory()
        self.course = build_course(sections=2, subsections=2, units=2)
        CourseStaffRole(self.course.id).add_users(self.reader)
        self.client = _client(self.reader)

    def _read(self, course_key=None, **params):
        response = self.client.get(outline_url(course_key or self.course.id), params)
        assert response.status_code == 200, response.content
        return response.json()

    def _expected_tree(self, block, levels_left):
        """The node the outline shows for ``block``, built from the modulestore."""
        node = {
            "id": str(block.location), "category": block.category, "display_name": block.display_name_with_default,
        }
        if block.category == "vertical":
            return node
        if levels_left > 0:
            node["children"] = [self._expected_tree(child, levels_left - 1) for child in block.get_children()]
        elif block.children:
            query = urlencode({"usage_key": str(block.location), "depth": 1})
            node["children_url"] = f"http://testserver{outline_url(self.course.id)}?{query}"
        else:
            node["children"] = []
        return node

    @ddt.data(1, 2, 3)
    def test_each_depth_cuts_at_its_level(self, depth):
        course = modulestore().get_course(self.course.id)
        assert self._read(depth=depth) == self._expected_tree(course, depth)

    def test_the_default_depth_is_sections_and_subsections(self):
        body = self._read()
        assert body == self._read(depth=2)
        assert [node["category"] for node in tree_nodes(body)] == [
            "course", "chapter", "sequential", "sequential", "chapter", "sequential", "sequential",
        ]
        subsections = [node for node in tree_nodes(body) if node["category"] == "sequential"]
        assert all("children" not in node and node["children_url"] for node in subsections)

    def test_minimal_view_is_the_default(self):
        assert self._read(view="minimal") == self._read()

    def test_children_url_is_on_exactly_the_cut_nodes_with_children(self):
        body = self._read(depth=1)
        assert "children_url" not in body
        sections = body["children"]
        assert [("children" in s, "children_url" in s) for s in sections] == [(False, True), (False, True)]
        units = [n for n in tree_nodes(self._read(depth=3)) if n["category"] == "vertical"]
        assert len(units) == 8
        assert all(set(unit) == {"id", "category", "display_name"} for unit in units)

    def test_following_children_url_returns_that_blocks_children(self):
        for depth in (1, 2):
            for node in tree_nodes(self._read(depth=depth)):
                if "children_url" not in node:
                    continue
                query = parse_qs(urlsplit(node["children_url"]).query)
                assert query == {"usage_key": [node["id"]], "depth": ["1"]}
                followed = self.client.get(node["children_url"])
                assert followed.status_code == 200, followed.content
                block = modulestore().get_item(UsageKey.from_string(node["id"]))
                assert followed.json() == self._expected_tree(block, 1)

    def test_the_tree_follows_the_full_outline_order(self):
        """The default tree to unit depth lists the same blocks in the same order as the full view."""
        default = self._read(depth=3)
        full = self.client.get(outline_url(self.course.id), {"view": "full"}).json()
        assert tree_ids(default) == full_structure_ids(full["course_structure"])
        assert tree_ids(default) == [block_id for block_id, _ in outline_blocks(self.course.id)]

    @ddt.data("chapter", "sequential", "vertical")
    def test_usage_key_returns_the_tree_under_that_block(self, category):
        block_id = next(block_id for block_id, cat in outline_blocks(self.course.id) if cat == category)
        block = modulestore().get_item(UsageKey.from_string(block_id))
        assert self._read(usage_key=block_id) == self._expected_tree(block, 2)
        assert self._read(usage_key=block_id, depth=1) == self._expected_tree(block, 1)

    def test_empty_course_and_empty_section(self):
        empty = CourseFactory.create()
        CourseStaffRole(empty.id).add_users(self.reader)
        root = self._read(course_key=empty.id)
        assert root == {
            "id": str(empty.location), "category": "course", "display_name": empty.display_name, "children": [],
        }
        assert_matches_one_read_shape(root, DEFAULT_SHAPE)
        section = BlockFactory.create(parent=empty, category="chapter", display_name="Empty")
        tree = self._read(course_key=empty.id, depth=1)
        assert tree["children"] == [
            {"id": str(section.location), "category": "chapter", "display_name": "Empty", "children": []},
        ]
        assert_matches_one_read_shape(tree, DEFAULT_SHAPE)

    def test_a_component_placed_directly_in_the_outline_is_a_leaf(self):
        subsection = modulestore().get_course(self.course.id).get_children()[0].get_children()[0]
        problem = BlockFactory.create(parent_location=subsection.location, category="problem", display_name="Loose")
        tree = self._read(usage_key=str(subsection.location), depth=1)
        assert tree["children"][-1] == {"id": str(problem.location), "category": "problem", "display_name": "Loose"}
        assert tree_ids(self._read(depth=3)) == [block_id for block_id, _ in outline_blocks(self.course.id)]

    def test_bodies_match_the_published_shape(self):
        for depth in (1, 2, 3):
            assert_matches_one_read_shape(self._read(depth=depth), DEFAULT_SHAPE)

    def test_blank_parameters_are_omitted_parameters(self):
        default = self._read()
        assert self._read(view="", depth="", usage_key="", show="") == default
        full = self.client.get(outline_url(self.course.id), {"view": "full", "show": ""})
        assert full.status_code == 200
        assert full.json()["initial_state"] is None

    def test_unknown_parameters_are_ignored(self):
        """format=concise, a v1 parameter, is not one of this endpoint's parameters; the tree is returned."""
        assert self._read(format="concise", foo="bar") == self._read()

    @ddt.data(
        ({"view": "bogus"}, ["view"]),
        ({"depth": "0"}, ["depth"]),
        ({"depth": "4"}, ["depth"]),
        ({"depth": "x"}, ["depth"]),
        ({"depth": "1.5"}, ["depth"]),
        ({"view": "full", "depth": "1"}, ["depth"]),
        ({"view": "full", "usage_key": "SECTION"}, ["usage_key"]),
        ({"view": "full", "depth": "1", "usage_key": "SECTION"}, ["depth", "usage_key"]),
        ({"show": "x"}, ["show"]),
        ({"view": "minimal", "show": "x"}, ["show"]),
        ({"usage_key": "not-a-key"}, ["usage_key"]),
    )
    @ddt.unpack
    def test_invalid_parameters_are_refused_with_400(self, params, fields):
        section = str(modulestore().get_course(self.course.id).get_children()[0].location)
        params = {name: section if value == "SECTION" else value for name, value in params.items()}
        response = self.client.get(outline_url(self.course.id), params)
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert sorted(envelope["errors"]) == fields

    def test_usage_keys_that_are_not_an_outline_block_of_this_course_are_refused(self):
        course = modulestore().get_course(self.course.id)
        problem = next(b for b in modulestore().get_items(self.course.id) if b.category == "problem")
        section = course.get_children()[0]
        missing = section.location.replace(block_id="missing")
        for key in (
            course.location,                                    # the course itself
            problem.location,                                   # a component, not a container
            missing,                                            # a section that does not exist
            "lb:Org:lib:problem:1",                             # a library block
            "i4x://Org/C/chapter/x",                            # a deprecated key
            f"block-v1:version@{VERSION_GUID}+type@chapter+block@x",       # a key naming only a version
            f"lib-block-v1:version@{VERSION_GUID}+type@problem+block@x",   # a library key naming only a version
        ):
            response = self.client.get(outline_url(self.course.id), {"usage_key": str(key)})
            envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
            assert envelope["errors"] == {"usage_key": [NOT_AN_OUTLINE_CONTAINER]}
            assert VERSION_GUID not in response.content.decode()

    def test_head_answers_without_a_body(self):
        for params in ({}, {"view": "full"}):
            response = self.client.head(outline_url(self.course.id), params)
            assert response.status_code == 200
            assert response.content == b""

    def test_a_course_missing_from_the_modulestore_is_404(self):
        """A course known only to its overview record is reported missing, not a server error."""
        with patch.object(CourseOverview, "course_exists", return_value=True):
            for params in ({}, {"view": "full"}):
                response = _client(UserFactory(is_staff=True)).get(outline_url(UNKNOWN_COURSE_KEY), params)
                envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
                assert envelope["detail"] == COURSE_NOT_FOUND


@ddt.ddt
class OutlineOtherContainersTest(ModuleStoreTestCase):
    """
    Outlines holding containers other than sections, subsections and units,
    and blocks of the course that are not in its outline.
    """

    def setUp(self):
        super().setUp()
        self.reader = UserFactory()
        self.client = _client(self.reader)
        course = CourseFactory.create()
        CourseStaffRole(course.id).add_users(self.reader)

        def block(parent, category, name):
            return BlockFactory.create(parent_location=parent, category=category, display_name=name).location

        section_a = block(course.location, "chapter", "Section A")
        subsection = block(section_a, "sequential", "Subsection A1")
        block(block(subsection, "vertical", "Unit A1.1"), "problem", "Problem")
        block(block(subsection, "conditional", "Conditional in a subsection"), "html", "Shown A")
        block(block(subsection, "library_content", "Library content in a subsection"), "problem", "Picked A")
        split = block(subsection, "split_test", "Split test in a subsection")
        block(block(split, "vertical", "Group 0"), "problem", "Problem 0")
        block(block(split, "vertical", "Group 1"), "html", "Text 1")
        block(block(section_a, "split_test", "Split test in a section"), "vertical", "Group")
        section_b = block(course.location, "chapter", "Section B")
        block(block(section_b, "conditional", "Conditional in a section"), "html", "Shown B")
        block(block(section_b, "library_content", "Library content in a section"), "problem", "Picked B")
        unit = block(block(section_b, "sequential", "Subsection B1"), "vertical", "Unit B1.1")
        self.nested_unit = block(unit, "vertical", "Unit inside a unit")
        block(self.nested_unit, "problem", "Nested problem")
        self.orphan_section = modulestore().create_item(self.user.id, course.id, "chapter", "orphan").location
        self.course_key = course.id

    def _read(self, **params):
        response = self.client.get(outline_url(self.course_key), params)
        assert response.status_code == 200, response.content
        return response.json()

    def _full_structure(self):
        return self._read(view="full")["course_structure"]

    def _follow(self, node):
        """Follow ``node``'s children_url; assert it returns that block with exactly its children."""
        response = self.client.get(node["children_url"])
        assert response.status_code == 200, (node["category"], response.content)
        followed = response.json()
        block = modulestore().get_item(UsageKey.from_string(node["id"]))
        assert {key: followed[key] for key in ("id", "category", "display_name")} == {
            key: node[key] for key in ("id", "category", "display_name")
        }
        assert [(child["id"], child["category"]) for child in followed["children"]] == [
            (str(child), child.block_type) for child in block.children
        ]
        return followed["children"]

    def _walk(self, **params):
        """Ids of every node reached from a read by following each children_url, recursively."""
        seen = []

        def visit(node):
            for each in tree_nodes(node):
                seen.append(each["id"])
                if "children_url" in each:
                    for child in self._follow(each):
                        visit(child)

        visit(self._read(**params))
        return seen

    def test_the_fixture_cuts_other_containers_at_every_depth(self):
        """Each kind of container is cut with a children_url at some depth, so the walks below follow one."""
        cut = {
            node["category"]
            for depth in (1, 2, 3)
            for node in tree_nodes(self._read(depth=depth))
            if "children_url" in node
        }
        assert cut == {"chapter", "sequential", "conditional", "library_content", "split_test"}

    @ddt.data(1, 2, 3)
    def test_every_children_url_from_the_course_is_followable(self, depth):
        seen = self._walk(depth=depth)
        assert sorted(seen) == sorted(full_structure_ids(self._full_structure()))

    @ddt.data(1, 2, 3)
    def test_every_children_url_from_each_reroot_is_followable(self, depth):
        structure = {node["id"]: node for node in structure_nodes(self._full_structure())}
        containers = [
            block_id for block_id, category in outline_blocks(self.course_key)
            if category != "course" and modulestore().get_item(UsageKey.from_string(block_id)).has_children
        ]
        assert len(containers) == 15
        for block_id in containers:
            seen = self._walk(usage_key=block_id, depth=depth)
            assert sorted(seen) == sorted(full_structure_ids(structure[block_id])), block_id

    def test_usage_key_accepts_exactly_the_containers_in_the_outline(self):
        """Every block of the course is tried; only containers the outline walk reaches are accepted."""
        outline = {block_id for block_id, _ in outline_blocks(self.course_key)}
        everything = modulestore().get_items(self.course_key)
        assert str(self.orphan_section) in {str(block.location) for block in everything}
        accepted = []
        for block in everything:
            response = self.client.get(outline_url(self.course_key), {"usage_key": str(block.location)})
            if response.status_code == 200:
                assert response.json()["id"] == str(block.location)
                accepted.append(str(block.location))
            else:
                envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
                assert envelope["errors"] == {
                    "usage_key": [NOT_AN_OUTLINE_CONTAINER],
                }
        expected = sorted(
            str(block.location) for block in everything
            if str(block.location) in outline and block.has_children and block.category != "course"
        )
        assert sorted(accepted) == expected
        assert len(expected) == 15

    def test_blocks_outside_the_outline_are_refused(self):
        """A section no block links to, and a unit inside a unit, are not in the outline."""
        for key in (self.orphan_section, self.nested_unit):
            response = self.client.get(outline_url(self.course_key), {"usage_key": str(key)})
            envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
            assert envelope["errors"] == {"usage_key": [NOT_AN_OUTLINE_CONTAINER]}
            assert key.block_id not in response.content.decode()
        assert str(self.orphan_section) not in full_structure_ids(self._full_structure())
        assert str(self.nested_unit) not in full_structure_ids(self._full_structure())


# ---------------------------------------------------------------------------
# The full view against v1
# ---------------------------------------------------------------------------

PRE_1900_DATES = "a start date before 1900"

# A course_structure node at any depth.
_NODE = r"course_structure(\.child_info\.children\[\d+\])*"

# Differences between the v1 read and view=full: (json path pattern, the
# condition under which it occurs, or None when it always does, the reason).
# The comparison asserts that each difference whose condition holds occurs and
# that nothing else differs.
INTENTIONAL_DIFFERENCES = [
    (r"initial_user_clipboard([.\[].*)?", None,
     "the clipboard is its own resource at /api/content-staging/v1/clipboard/"),
    (r"course_release_date", PRE_1900_DATES,
     "v1 stores the unset start date over a date before 1900 and reports 'Set Date'; view=full reports the date"),
    (rf"{_NODE}\.start", PRE_1900_DATES, "view=full reports the stored start date, v1 the unset date it stores"),
    (rf"{_NODE}\.release_date", PRE_1900_DATES, "the release date is formatted from the start date"),
    (rf"{_NODE}\.released_to_students", PRE_1900_DATES,
     "a block dated 1800 has been released; one dated with the unset date has not"),
    (rf"{_NODE}\.visibility_state", PRE_1900_DATES, "it follows whether the block has been released"),
]

_ABSENT = object()


def _flatten(obj, prefix=""):
    """
    A ``{json_path: value}`` map of every leaf of ``obj``. An empty object or
    array is a leaf, so a container that empties or changes kind is seen.
    """
    flat = {}
    if isinstance(obj, dict) and obj:
        for key in sorted(obj):
            flat.update(_flatten(obj[key], f"{prefix}.{key}" if prefix else key))
    elif isinstance(obj, list) and obj:
        for index, item in enumerate(obj):
            flat.update(_flatten(item, f"{prefix}[{index}]"))
    else:
        flat[prefix] = obj
    return flat


def _same(a, b):
    """Whether two leaves are equal and of the same JSON type: true is not 1, 1.0 is not 1, {} is not []."""
    return type(a) is type(b) and a == b


def _differences(legacy, new, conditions=frozenset()):
    """
    Compare two bodies under ``conditions``. Return the json paths that
    differ, those among them no applicable declared difference explains, and
    the applicable declared differences that did not occur.
    """
    a, b = _flatten(legacy), _flatten(new)
    changed = {key for key in set(a) | set(b) if not _same(a.get(key, _ABSENT), b.get(key, _ABSENT))}
    applicable = [pattern for pattern, when, _ in INTENTIONAL_DIFFERENCES if when is None or when in conditions]
    unexpected = {key for key in changed if not any(re.fullmatch(pattern, key) for pattern in applicable)}
    missing = {pattern for pattern in applicable if not any(re.fullmatch(pattern, key) for key in changed)}
    return changed, unexpected, missing


def test_the_comparison_sees_types_and_empty_containers():
    assert _differences({"a": {"b": []}}, {"a": {}})[0] == {"a", "a.b"}
    assert _differences({"x": True}, {"x": 1})[0] == {"x"}
    assert _differences({"x": 1.0}, {"x": 1})[0] == {"x"}
    assert _differences({"x": {}}, {"x": []})[0] == {"x"}
    assert _differences({"x": None, "y": 1}, {"y": 1})[0] == {"x"}
    assert _differences({"x": [1, {"y": False}]}, {"x": [1, {"y": False}]})[0] == set()


def test_a_declared_difference_is_allowed_only_when_its_condition_holds():
    legacy = {"course_release_date": "Set Date", "initial_user_clipboard": {}}
    new = {"course_release_date": "Jan 01, 1800 at 00:00 UTC"}
    _, unexpected, missing = _differences(legacy, new)
    assert unexpected == {"course_release_date"}
    assert missing == set()
    _, unexpected, missing = _differences(legacy, new, {PRE_1900_DATES})
    assert unexpected == set()
    assert missing == {pattern for pattern, when, _ in INTENTIONAL_DIFFERENCES if when == PRE_1900_DATES} - {
        "course_release_date",
    }


class _FullViewParityMixin:
    """Compares view=full against the v1 read of ``self.course``."""

    def _pair(self, user, **params):
        """
        The v1 read and the view=full read of the course by ``user``, as JSON.
        view=full is read first, so nothing the v1 read stores can reach it.
        """
        client = _client(user)
        new = client.get(outline_url(self.course.id), {"view": "full", **params})
        legacy = client.get(v1_url(self.course.id), params)
        assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
        return legacy.json(), new.json()

    def compare(self, user, conditions=frozenset(), **params):
        """
        Assert that the two reads differ exactly by the declared differences
        that apply under ``conditions``, each of which occurs. Return the v1
        body, the v2 body and the json paths that differ.
        """
        legacy, new = self._pair(user, **params)
        changed, unexpected, missing = _differences(legacy, new, conditions)
        assert unexpected == set(), sorted(unexpected)
        assert missing == set(), sorted(missing)
        assert set(legacy) - set(new) == {"initial_user_clipboard"}
        assert set(new) <= set(legacy)
        assert_matches_one_read_shape(new, FULL_SHAPE)
        return legacy, new, changed

    def assert_parity(self, user, **params):
        """Assert parity with no condition-dependent difference and return the v2 body."""
        return self.compare(user, **params)[1]


@ddt.ddt
class OutlineFullViewParityTest(_FullViewParityMixin, ModuleStoreTestCase):
    """view=full returns what v1 returns for the same caller, except the declared differences."""

    def setUp(self):
        super().setUp()
        self.course = build_course(sections=2, subsections=1, units=2)

    def _course_staff(self):
        user = UserFactory()
        CourseStaffRole(self.course.id).add_users(user)
        return user

    @ddt.data(True, False)
    def test_plain_course(self, relative_dates):
        with override_waffle_flag(CUSTOM_RELATIVE_DATES, active=relative_dates):
            body = self.assert_parity(self._course_staff())
        assert body["is_custom_relative_dates_active"] is relative_dates
        assert body["created_on"] is None
        assert body["rerun_notification_id"] is None
        assert body["notification_dismiss_url"] is None
        assert body["initial_state"] is None
        assert body["reindex_link"] is None

    def test_course_with_a_start_date(self):
        self.course = build_course(sections=1, subsections=1, units=1, start=datetime(2021, 3, 4, tzinfo=pytz.UTC))
        body = self.assert_parity(self._course_staff())
        assert body["course_release_date"] == "Mar 04, 2021 at 00:00 UTC"

    def test_course_without_a_start_date(self):
        self.course = build_course(sections=1, subsections=1, units=1, start=DEFAULT_START_DATE)
        assert self.assert_parity(self._course_staff())["course_release_date"] == "Set Date"

    def test_dates_before_1900_are_reported_as_stored(self):
        """
        v1 replaces each start date before 1900 with the unset date and reports
        that; view=full reports the stored date.
        """
        self.course = build_course(sections=1, subsections=1, units=1, start=PRE_1900)
        reader = self._course_staff()
        legacy, new, changed = self.compare(reader, conditions={PRE_1900_DATES})
        assert new["course_release_date"] == "Jan 01, 1800 at 00:00 UTC"
        assert structure_nodes(new["course_structure"])[0]["start"] == "1800-01-01T00:00:00+00:00"
        assert legacy["course_release_date"] == "Set Date"
        assert {key.rsplit(".", 1)[-1] for key in changed if not key.startswith("initial_user_clipboard")} == {
            "course_release_date", "start", "release_date", "released_to_students", "visibility_state",
        }

    def test_course_with_an_overview_and_a_rerun_notification(self):
        CourseOverview.load_from_module_store(self.course.id)
        CourseRerunState.objects.initiated(self.course.id, self.course.id, self.user, "Rerun")
        state = CourseRerunState.objects.find_first(course_key=self.course.id)
        body = self.assert_parity(self._course_staff())
        created = CourseOverview.objects.get(id=self.course.id).created
        assert body["created_on"] == created.isoformat().replace("+00:00", "Z")
        assert body["rerun_notification_id"] == state.id
        assert body["notification_dismiss_url"] == f"/course_notifications/{self.course.id}/{state.id}"

    @ddt.data("section", "unit", "unknown")
    def test_show(self, which):
        course = modulestore().get_course(self.course.id)
        section = course.get_children()[0]
        unit = section.get_children()[0].get_children()[0]
        locator = {
            "section": str(section.location),
            "unit": str(unit.location),
            "unknown": str(section.location.replace(block_id="missing")),
        }[which]
        body = self.assert_parity(self._course_staff(), show=locator)
        if which == "unknown":
            assert body["initial_state"] is None
        elif which == "unit":
            assert body["initial_state"] == {"locator_to_show": locator, "expanded_locators": [locator]}
        else:
            expected = [b for b, _ in outline_blocks(self.course.id)]
            start = expected.index(locator)
            assert body["initial_state"] == {
                "locator_to_show": locator, "expanded_locators": expected[start:start + 4],
            }

    def test_empty_show_is_no_show(self):
        assert self.assert_parity(self._course_staff(), show="")["initial_state"] is None

    @override_settings(ENABLE_COURSEWARE_INDEX=True)
    @override_waffle_flag(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, active=False)
    def test_reindex_link_follows_the_reindex_permission(self):
        """Global staff may reindex; course staff may not, while policy-based permissions are off."""
        reindex = f"/course/{self.course.id}/search_reindex"
        assert self.assert_parity(UserFactory(is_staff=True))["reindex_link"] == reindex
        assert self.assert_parity(self._course_staff())["reindex_link"] is None
        with override_settings(ENABLE_COURSEWARE_INDEX=False):
            assert self.assert_parity(UserFactory(is_staff=True))["reindex_link"] is None

    @override_settings(LMS_ROOT_URL=None)
    def test_lms_link_is_null_without_an_lms_address(self):
        assert self.assert_parity(self._course_staff())["lms_link"] is None

    def test_every_node_carries_the_course_tag_counts(self):
        taxonomy = tagging_api.create_taxonomy("Topics", allow_free_text=True)
        tagging_api.set_taxonomy_orgs(taxonomy, all_orgs=True)
        unit = modulestore().get_course(self.course.id).get_children()[0].get_children()[0].get_children()[0]
        tagging_api.tag_object(str(unit.location), taxonomy, ["Algebra", "Geometry"])
        body = self.assert_parity(self._course_staff())
        nodes = structure_nodes(body["course_structure"])
        assert len(nodes) == len(outline_blocks(self.course.id)) == 9
        assert {str(node["tag_counts_by_block"]) for node in nodes} == {str({str(unit.location): 2})}

    def test_deprecated_blocks_are_reported(self):
        course = modulestore().get_course(self.course.id)
        course.advanced_modules = ["poll", "survey"]
        modulestore().update_item(course, self.user.id)
        unit = course.get_children()[0].get_children()[0].get_children()[0]
        BlockFactory.create(parent=unit, category="poll", display_name="Old poll")
        with patch("cms.djangoapps.contentstore.rest_api.v2.course_outline_service.deprecated_xblocks") as v2_types, \
                patch("cms.djangoapps.contentstore.utils.deprecated_xblocks") as v1_types:
            v2_types.return_value = v1_types.return_value = [type("Deprecated", (), {"name": "poll"})]
            body = self.assert_parity(self._course_staff())
        assert body["deprecated_blocks_info"]["deprecated_enabled_block_types"] == ["poll"]
        assert [name for _, name in body["deprecated_blocks_info"]["blocks"]] == ["Old poll"]

    @ddt.data("unavailable_provider", "missing_escalation_email")
    def test_proctoring_errors_are_reported_as_v1_reports_them(self, problem):
        """
        Each kind of stored proctoring problem is reported with the same bytes the
        shared proctoring error serializers produce for v1.
        """
        course = modulestore().get_course(self.course.id)
        course.enable_proctored_exams = True
        backends = settings.PROCTORING_BACKENDS
        if problem == "unavailable_provider":
            available = ["lti_external"]
            message = (
                f"The proctoring provider configured for this course, '{course.proctoring_provider}', is not valid."
            )
        else:
            backends = {"DEFAULT": "proctortrack", "proctortrack": {"requires_escalation_email": True}}
            course.proctoring_provider = "proctortrack"
            available = ["proctortrack"]
            message = "Provider 'proctortrack' requires an exam escalation contact."
        modulestore().update_item(course, self.user.id)
        reader = self._course_staff()
        with override_settings(PROCTORING_BACKENDS=backends), patch(
            "cms.djangoapps.models.settings.course_metadata.get_available_providers", return_value=available,
        ):
            body = self.assert_parity(reader)
            course = modulestore().get_course(self.course.id)
            stored = CourseMetadata.validate_proctoring_settings(course, CourseMetadata.fetch(course), reader)
        assert [(error["key"], error["message"]) for error in body["proctoring_errors"]] == [
            ("proctoring_provider", message),
        ]
        assert body["proctoring_errors"][0]["model"]["value"] == course.proctoring_provider
        shared = JSONRenderer().render(ProctoringErrorListSerializer(stored, many=True).data)
        local = JSONRenderer().render(CourseOutlineProctoringErrorSerializer(stored, many=True).data)
        assert local == shared
        assert JSONRenderer().render(body["proctoring_errors"]) == shared

    def test_empty_course(self):
        self.course = CourseFactory.create()
        body = self.assert_parity(self._course_staff())
        assert body["course_structure"]["id"] == str(self.course.location)
        assert body["course_structure"]["child_info"] == {
            "category": "chapter", "display_name": "Section", "children": [],
        }


@ddt.ddt
@override_settings(ENABLE_COURSEWARE_INDEX=True)
class OutlineFullViewReindexLinkAuthzTest(CourseAuthoringAuthzTestMixin, _FullViewParityMixin, ModuleStoreTestCase):
    """
    With policy-based authoring permissions on, the reindex link is shown to
    callers allowed to publish the course's content, as v1 shows it.
    """

    def setUp(self):
        super().setUp()
        self.course = build_course(sections=1, subsections=1, units=1)

    def _user_with(self, role):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, self.course.id)
        return user

    @ddt.data(
        (COURSE_AUDITOR.external_key, False),
        (COURSE_EDITOR.external_key, False),
        (COURSE_STAFF.external_key, True),
        (COURSE_ADMIN.external_key, True),
    )
    @ddt.unpack
    def test_the_link_follows_the_publish_permission(self, role, may_publish):
        """Every one of these roles may view the course; only those that may publish get the link."""
        reindex = f"/course/{self.course.id}/search_reindex"
        body = self.assert_parity(self._user_with(role))
        assert body["reindex_link"] == (reindex if may_publish else None)

    def test_global_staff_and_superuser_get_the_link_without_a_role(self):
        reindex = f"/course/{self.course.id}/search_reindex"
        assert self.assert_parity(self.staff_user)["reindex_link"] == reindex
        assert self.assert_parity(self.super_user)["reindex_link"] == reindex

    def test_a_role_that_may_publish_another_course_does_not_get_the_link(self):
        other = build_course(sections=1, subsections=1, units=1)
        user = self._user_with(COURSE_AUDITOR.external_key)
        self.add_user_to_role_in_course(user, COURSE_ADMIN.external_key, other.id)
        assert self.assert_parity(user)["reindex_link"] is None

    @override_settings(ENABLE_COURSEWARE_INDEX=False)
    def test_no_link_while_course_indexing_is_off(self):
        assert self.assert_parity(self._user_with(COURSE_ADMIN.external_key))["reindex_link"] is None


# The keys of each full-view course_structure node, by block type, on a course
# with every configuration-dependent key off except tagging. The nodes come
# from the shared create_xblock_info builder; v2 publishes them as documented
# in CourseOutlineFullSerializer.course_structure.
_EVERY_NODE_KEYS = frozenset({
    "actions", "ancestor_has_staff_lock", "category", "course_authoring_url", "course_graders",
    "course_tags_count", "display_name", "due", "due_date", "edited_on", "edited_on_raw", "embed_lms_url",
    "enable_copy_paste_units", "enable_hide_from_toc_ui", "explanatory_message", "format", "graded",
    "group_access", "has_changes", "has_children", "has_explicit_staff_lock", "has_partition_group_components",
    "hide_from_toc", "hide_from_toc_message", "id", "is_tagging_feature_disabled", "lms_url", "published",
    "published_on", "relative_weeks_due", "release_date", "released_to_students", "show_correctness",
    "staff_only_message", "start", "studio_url", "tag_counts_by_block", "taxonomy_tags_widget_url",
    "upstream_info", "user_partition_info", "user_partitions", "visibility_state", "xblock_type",
})
_HIGHLIGHTS_KEYS = frozenset({"highlights_doc_url", "highlights_enabled", "highlights_preview_only"})
FULL_VIEW_NODE_KEYS = {
    "course": _EVERY_NODE_KEYS | _HIGHLIGHTS_KEYS | {
        "child_info", "end", "highlights_enabled_for_messaging", "unit_level_discussions",
    },
    "chapter": _EVERY_NODE_KEYS | _HIGHLIGHTS_KEYS | {"child_info", "highlights"},
    "sequential": _EVERY_NODE_KEYS | {"child_info", "hide_after_due"},
    "vertical": _EVERY_NODE_KEYS | {"discussion_enabled"},
    "split_test": _EVERY_NODE_KEYS | {"child_info"},
    "vertical outside a subsection": _EVERY_NODE_KEYS,
    "problem": _EVERY_NODE_KEYS,
}
_TAGGING_KEYS = frozenset({
    "course_authoring_url", "course_tags_count", "tag_counts_by_block", "taxonomy_tags_widget_url",
})
FULL_VIEW_CHILD_INFO_KEYS = {
    "course": {"category", "display_name", "children"},
    "chapter": {"category", "display_name", "children"},
    "sequential": {"category", "display_name", "children"},
    "split_test": {"children"},
}


@ddt.ddt
class OutlineFullViewNodeKeysTest(ModuleStoreTestCase):
    """The keys each view=full course_structure node carries are the published contract."""

    @ddt.data(False, True)
    def test_every_node_carries_exactly_the_documented_keys(self, tagging_disabled):
        course = build_course(sections=1, subsections=1, units=1)
        subsection = modulestore().get_course(course.id).get_children()[0].get_children()[0]
        BlockFactory.create(parent_location=subsection.location, category="problem", display_name="Loose")
        split = BlockFactory.create(parent_location=subsection.location, category="split_test", display_name="Split")
        BlockFactory.create(parent_location=split.location, category="vertical", display_name="Group")
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        with override_waffle_flag(DISABLE_TAGGING_FEATURE, active=tagging_disabled):
            body = _client(reader).get(outline_url(course.id), {"view": "full"}).json()

        def kinds(node, parent_category=None):
            """``(kind, node)`` of every node; a vertical is a unit only directly in a subsection."""
            kind = node["category"]
            if kind == "vertical" and parent_category != "sequential":
                kind = "vertical outside a subsection"
            yield kind, node
            for child in node.get("child_info", {}).get("children", []):
                yield from kinds(child, node["category"])

        nodes = list(kinds(body["course_structure"]))
        assert sorted({kind for kind, _ in nodes}) == sorted(FULL_VIEW_NODE_KEYS)
        for category, node in nodes:
            keys = set(node)
            expected = FULL_VIEW_NODE_KEYS[category] - (_TAGGING_KEYS if tagging_disabled else set())
            assert keys == expected, (
                f"The keys of a {category} node in view=full course_structure changed: added "
                f"{sorted(keys - expected)}, removed {sorted(expected - keys)}. These nodes are built by the "
                "shared create_xblock_info and are part of the published v2 outline contract. A removed or "
                "renamed key breaks v2 callers and needs a new version; an added key must be documented in "
                "CourseOutlineFullSerializer.course_structure and listed here."
            )
            assert set(node.get("child_info", {})) == FULL_VIEW_CHILD_INFO_KEYS.get(category, set()), category

    def test_the_documented_keys_are_named_in_the_published_description(self):
        description = _published_schema()["components"]["schemas"][FULL_SHAPE]["properties"]["course_structure"][
            "description"
        ]
        named = set(re.findall(r"[a-z]+(?:_[a-z]+)+|\b[a-z]{2,}\b", description))
        missing = set().union(*FULL_VIEW_NODE_KEYS.values(), *FULL_VIEW_CHILD_INFO_KEYS.values()) - named
        assert missing == set()


# ---------------------------------------------------------------------------
# Reads write nothing
# ---------------------------------------------------------------------------

# The one write the permission library makes on a database's first role or
# permission lookup, whichever API makes it.
FIRST_LOOKUP_WRITES = [("INSERT", PolicyCacheControl._meta.db_table)]  # pylint: disable=protected-access


class _ReadIsSideEffectFreeMixin:
    """
    Every read (default, full, a child address, HEAD on both views) of a course
    holding blocks dated before 1900 writes no row and no Mongo document,
    stores nothing through the modulestore, saves no model and sends no
    xblock-updated or publishing signal. The stored dates are left as they are.

    The reads start on a database that has never served a role or permission
    lookup, so the first read may insert the permission library's one policy
    version row and nothing else; the v1 read inserts the same row.
    """

    # Whether the course has an overview row when the reads start. Without
    # one, a read that builds it would be seen writing it.
    overview_before_reads = False

    def reader_for(self, course):
        """Return a user who may read ``course``, leaving the database as no lookup had touched it."""
        raise NotImplementedError

    def _course_with_old_dates(self):
        """A course whose course block, section, subsection and unit all start in 1800."""
        course = CourseFactory.create(start=PRE_1900)
        section = BlockFactory.create(
            parent_location=course.location, category="chapter", display_name="Old", start=PRE_1900,
        )
        subsection = BlockFactory.create(parent_location=section.location, category="sequential", start=PRE_1900)
        BlockFactory.create(parent_location=subsection.location, category="vertical", start=PRE_1900)
        return course, section

    def _stored_starts(self, course_key):
        return {
            block_id: modulestore().get_item(UsageKey.from_string(block_id)).start
            for block_id, _ in outline_blocks(course_key)
        }

    def test_reads_have_no_side_effects(self):
        course, section = self._course_with_old_dates()
        client = _client(self.reader_for(course))
        starts = self._stored_starts(course.id)
        assert {start.year for start in starts.values()} == {1800}
        url = outline_url(course.id)
        child_url = f"{url}?{urlencode({'usage_key': str(section.location), 'depth': 1})}"
        reads = [("get", url, {}), ("get", url, {"view": "full"}), ("get", child_url, None),
                 ("head", url, {}), ("head", url, {"view": "full"})]
        assert not PolicyCacheControl.objects.exists()
        assert CourseOverview.objects.filter(id=course.id).exists() is self.overview_before_reads
        for index, (method, address, params) in enumerate(reads):
            with record_effects(self) as effects:
                response = getattr(client, method)(address, params)
            request = (index, method, address, params)
            assert response.status_code == 200, (request, response.content)
            assert effects["sql_writes"] == (FIRST_LOOKUP_WRITES if index == 0 else []), request
            assert effects["saves"] == (["PolicyCacheControl"] if index == 0 else []), request
            assert effects["mongo_writes"] == 0, request
            assert effects["update_item"] == [], request
            assert effects["xblock_updated"] == [], request
            assert effects["published"] == [], request
        assert self._stored_starts(course.id) == starts
        full = client.get(url, {"view": "full"}).json()
        assert {node["start"][:4] for node in structure_nodes(full["course_structure"])} == {"1800"}

    def test_a_clipboard_from_a_course_without_an_overview_is_not_read(self):
        """The full view does not read the reader's clipboard, so it never builds a course overview."""
        course = build_course(sections=1, subsections=1, units=1)
        reader = self.reader_for(course)
        source = build_course(sections=1, subsections=1, units=1)
        unit = modulestore().get_course(source.id).get_children()[0].get_children()[0].get_children()[0]
        content_staging_api.save_xblock_to_user_clipboard(unit, reader.id)
        CourseOverview.objects.filter(id=source.id).delete()
        PolicyCacheControl.objects.all().delete()
        with record_effects(self) as effects:
            response = _client(reader).get(outline_url(course.id), {"view": "full"})
        assert response.status_code == 200, response.content
        assert effects["sql_writes"] == FIRST_LOOKUP_WRITES
        assert not CourseOverview.objects.filter(id=source.id).exists()

    def test_the_course_block_read_is_left_unchanged(self):
        """The discussion configuration address is added to a copy of the course's discussion settings."""
        course = build_course(sections=1, subsections=1, units=1)
        request = RequestFactory().get("/")
        request.user = self.reader_for(course)
        request.LANGUAGE_CODE = "en"
        store = modulestore()
        with store.bulk_operations(course.id):
            stored = dict(store.get_course(course.id).discussions_settings)
            outline = get_full_outline(request, course.id)
            assert store.get_course(course.id).discussions_settings == stored
        assert "discussion_configuration_url" not in stored
        assert outline["discussions_settings"] == {
            **stored, "discussion_configuration_url": f"{get_pages_and_resources_url(course.id)}/discussion/settings",
        }

    def test_v1_still_resaves_old_dates_and_builds_the_clipboard_overview(self):
        """
        The deprecated v1 read keeps both writes: it stores the default start
        date over each date before 1900, announcing every block it updates,
        and it builds a missing course overview for the clipboard's source.
        """
        course, _ = self._course_with_old_dates()
        reader = self.reader_for(course)
        source = build_course(sections=1, subsections=1, units=1)
        unit = modulestore().get_course(source.id).get_children()[0].get_children()[0].get_children()[0]
        content_staging_api.save_xblock_to_user_clipboard(unit, reader.id)
        CourseOverview.objects.filter(id=source.id).delete()
        PolicyCacheControl.objects.all().delete()
        with record_effects(self) as effects:
            response = _client(reader).get(v1_url(course.id))
        assert response.status_code == 200, response.content
        outline = [block_id for block_id, _ in outline_blocks(course.id)]
        assert sorted(effects["update_item"]) == sorted(outline)
        assert effects["mongo_writes"] > 0
        assert sorted(effects["xblock_updated"]) == sorted(outline)
        assert ("INSERT", CourseOverview._meta.db_table) in effects["sql_writes"]  # pylint: disable=protected-access
        assert CourseOverview.objects.filter(id=source.id).exists()
        assert {start.year for start in self._stored_starts(course.id).values()} == {DEFAULT_START_DATE.year}


class OutlineReadSideEffectsLegacyPermissionsTest(_ReadIsSideEffectFreeMixin, ModuleStoreTestCase):
    """
    Reads by course staff with policy-based authoring permissions off. Course
    staff rather than global staff, whose access is decided without a role lookup.
    """

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class OutlineReadSideEffectsAuthzTest(CourseAuthoringAuthzTestMixin, _ReadIsSideEffectFreeMixin, ModuleStoreTestCase):
    """
    Reads by a course auditor with policy-based authoring permissions on. The
    test setup loads the policy and assigns roles through the permission
    library, which creates the policy version row; it is deleted before the
    reads so they start on a database no lookup has touched. Assigning a role
    on a course builds the course's overview, which the reads need: the
    library grants a course role only on a course that has one.
    """

    overview_before_reads = True

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        PolicyCacheControl.objects.all().delete()
        return reader

    def test_a_role_on_a_course_without_an_overview_is_refused_without_building_one(self):
        course = build_course(sections=1, subsections=1, units=1)
        client = _client(self.reader_for(course))
        CourseOverview.objects.filter(id=course.id).delete()
        for params in ({}, {"view": "full"}):
            with record_effects(self) as effects:
                response = client.get(outline_url(course.id), params)
            assert_error_envelope(response, expected_status=403, expected_type_slug="authz")
            assert ("INSERT", CourseOverview._meta.db_table) not in effects["sql_writes"]  # pylint: disable=protected-access
        assert client.get(v1_url(course.id)).status_code == 403
        assert not CourseOverview.objects.filter(id=course.id).exists()


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------

def _measure(request):
    """SQL queries, Mongo reads and Mongo writes of the second of two identical requests."""
    request()
    with CaptureQueriesContext(connection) as queries, count_mongo_calls() as mongo:
        response = request()
    assert response.status_code == 200, response.content
    return len(queries.captured_queries), mongo["reads"], mongo["writes"]


SMALL = (1, 1, 1)
LARGE = (4, 3, 3)


def v2_costs(costs):
    """The v2 reads' costs; the v1 read is compared, not pinned, as its cost is not this endpoint's."""
    return {read: cost for read, cost in costs.items() if read != "v1"}


class _QueryCountMixin:
    """Request costs against the v1 read, on a small and a larger course."""

    def reader_for(self, course):
        raise NotImplementedError

    def costs(self, size):
        """``{read: (sql, mongo reads, mongo writes)}`` for v1, view=full and the default view."""
        course = build_course(*size)
        client = _client(self.reader_for(course))
        return {
            "v1": _measure(lambda: client.get(v1_url(course.id))),
            "full": _measure(lambda: client.get(outline_url(course.id), {"view": "full"})),
            "default": _measure(lambda: client.get(outline_url(course.id))),
            "unit_depth": _measure(lambda: client.get(outline_url(course.id), {"depth": 3})),
        }

    def test_full_view_costs_no_more_than_v1_and_the_default_view_does_not_grow(self):
        small, large = self.costs(SMALL), self.costs(LARGE)
        for costs in (small, large):
            assert costs["full"][0] <= costs["v1"][0], costs
            assert costs["full"][1] <= costs["v1"][1], costs
            assert costs["v1"][2] == costs["full"][2] == costs["default"][2] == 0, costs
        assert large["default"] == small["default"]
        assert large["unit_depth"] == small["unit_depth"]


class OutlineQueryCountTest(_QueryCountMixin, ModuleStoreTestCase):
    """
    Pinned costs with policy-based authoring permissions off, read by course
    staff: SQL queries, Mongo reads and Mongo writes per request, the same on a
    course of 1 and of 36 units.
    """

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader

    def test_pinned_costs(self):
        expected = {"full": (34, 6, 0), "default": (11, 4, 0), "unit_depth": (11, 4, 0)}
        assert v2_costs(self.costs(SMALL)) == expected
        assert v2_costs(self.costs(LARGE)) == expected


class OutlineQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
    """
    Pinned costs with policy-based authoring permissions on, read by a course
    auditor, the same on a course of 1 and of 36 units.
    """

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        return reader

    def test_pinned_costs(self):
        expected = {"full": (35, 6, 0), "default": (11, 4, 0), "unit_depth": (11, 4, 0)}
        assert v2_costs(self.costs(SMALL)) == expected
        assert v2_costs(self.costs(LARGE)) == expected


class OutlineRerootQueryCountTest(ModuleStoreTestCase):
    """
    Re-rooting the outline costs what reading it from the course costs: the
    check that the block is in the outline reads nothing more.
    """

    def _costs(self, size):
        """``(sql, mongo reads, mongo writes)`` of reading from the course and from each depth of block."""
        course = build_course(*size)
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        client = _client(reader)
        section = course.get_children()[-1]
        subsection = section.get_children()[-1]
        unit = subsection.get_children()[-1]
        orphan = modulestore().create_item(self.user.id, course.id, "chapter", "orphan").location
        costs = {"course": _measure(lambda: client.get(outline_url(course.id), {"depth": 1}))}
        for name, block in (("section", section), ("subsection", subsection), ("unit", unit)):
            costs[name] = _measure(
                lambda key=block.location: client.get(outline_url(course.id), {"usage_key": str(key), "depth": 1})
            )
        client.get(outline_url(course.id), {"usage_key": str(orphan)})
        with CaptureQueriesContext(connection) as queries, count_mongo_calls() as mongo:
            refused = client.get(outline_url(course.id), {"usage_key": str(orphan)})
        assert refused.status_code == 400, refused.content
        costs["refused"] = (len(queries.captured_queries), mongo["reads"], mongo["writes"])
        return costs

    def test_pinned_costs(self):
        # A refusal adds only the request transaction's rollback statement.
        expected = {
            "course": (11, 4, 0), "section": (11, 4, 0), "subsection": (11, 4, 0), "unit": (11, 4, 0),
            "refused": (12, 4, 0),
        }
        assert self._costs(SMALL) == expected
        assert self._costs(LARGE) == expected


# ---------------------------------------------------------------------------
# Addresses, methods and the published schema
# ---------------------------------------------------------------------------

COURSE_KEY = "course-v1:edX+ToyX+Toy_Course"


@ddt.ddt
class OutlineRoutingTest(SharedModuleStoreTestCase):
    """The address, the deprecated v1 address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)

    def test_the_address(self):
        assert outline_url(COURSE_KEY) == f"/api/authoring/v2/courses/{COURSE_KEY}/outline/"
        match = resolve(outline_url(COURSE_KEY))
        assert match.func.cls is CourseOutlineViewSet
        assert routed_actions(match.func) == {"get": "retrieve"}

    def test_the_v1_address_is_unchanged(self):
        assert v1_url(COURSE_KEY) == f"/api/contentstore/v1/course_index/{COURSE_KEY}"
        assert resolve(v1_url(COURSE_KEY)).func.cls is CourseIndexView

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data("post", "put", "patch", "delete")
    def test_other_methods_are_not_allowed(self, method):
        response = getattr(_client(UserFactory(is_staff=True)), method)(outline_url(self.course.id), {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_unsupported_accept_is_refused_with_406(self):
        response = _client(UserFactory(is_staff=True)).get(outline_url(self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    def test_options_describes_the_endpoint(self):
        response = _client(UserFactory(is_staff=True)).options(outline_url(self.course.id))
        assert response.status_code == 200
        assert response.json()["name"] == "Course Outline"

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data("not-a-course-key", "edX/ToyX/Toy_Course", "edX+ToyX+Toy_Course")
    def test_unparsable_or_slash_form_key_is_404(self, key):
        response = _client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/courses/{key}/outline/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        response = _client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/courses/{self.course.id}/outline")
        assert response.status_code == 301
        assert response["Location"] == outline_url(self.course.id)
        with pytest.raises(Resolver404):
            resolve(f"/api/authoring/v2/courses/{self.course.id}/outline")

    def test_version_only_course_key_is_404(self):
        # The converter accepts a key that names only a version. No course run is
        # behind it, so the view answers 404 rather than 500.
        version_only = "course-v1:version@0123456789abcdef01234567"
        response = _client(UserFactory()).get(f"/api/authoring/v2/courses/{version_only}/outline/")
        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == COURSE_NOT_FOUND

    def test_a_library_is_not_a_course(self):
        library = LibraryFactory.create()
        for params in ({}, {"view": "full"}):
            response = _client(UserFactory(is_staff=True)).get(outline_url(library.location.library_key), params)
            envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
            assert envelope["detail"] == COURSE_NOT_FOUND

    def test_v1_keeps_its_own_error_body(self):
        response = _client().get(v1_url(self.course.id))
        assert response.status_code == 401
        assert response.json() == {"developer_message": "Authentication credentials were not provided."}


class OutlineSchemaTest(SharedModuleStoreTestCase):
    """The outline read as published, generated from the real contentstore and authoring routes."""

    PATH = "/api/authoring/v2/courses/{course_key}/outline/"
    V1 = "/api/contentstore/v1/course_index/{course_id}"

    @staticmethod
    def _ref(response):
        return response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def test_the_read_is_published_for_the_sdk(self):
        schema = _published_schema()
        assert list(schema["paths"][self.PATH]) == ["get"]
        operation = schema["paths"][self.PATH]["get"]
        assert operation["operationId"] == "v2_courses_outline_retrieve"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert "x-internal" not in operation
        parameters = {p["name"]: p for p in operation["parameters"]}
        assert set(parameters) == {"course_key", "view", "depth", "usage_key", "show"}
        assert all(parameters[name]["in"] == "query" for name in ("view", "depth", "usage_key", "show"))
        assert not any(parameters[name].get("required") for name in ("view", "depth", "usage_key", "show"))
        assert parameters["course_key"] == {
            "in": "path",
            "name": "course_key",
            "schema": {"type": "string", "pattern": "^[^/]+$"},
            "description": "Key of the course, such as course-v1:edX+DemoX+2026.",
            "required": True,
        }
        assert parameters["view"]["schema"] == {"type": "string", "enum": ["full", "minimal"], "default": "minimal"}
        assert parameters["depth"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 3, "default": 2}
        assert parameters["usage_key"]["schema"] == parameters["show"]["schema"] == {"type": "string"}
        assert self._ref(operation["responses"]["200"]) == READ_SHAPE
        for status in ("400", "401", "403", "404"):
            assert self._ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_read_offers_the_tree_and_the_full_shape(self):
        components = _published_schema()["components"]["schemas"]
        assert components[READ_SHAPE] == {"oneOf": [
            {"$ref": f"#/components/schemas/{DEFAULT_SHAPE}"},
            {"$ref": f"#/components/schemas/{FULL_SHAPE}"},
        ]}
        node = components[DEFAULT_SHAPE]
        assert node["required"] == ["category", "display_name", "id"]
        assert node["properties"]["children"]["items"] == {"$ref": f"#/components/schemas/{DEFAULT_SHAPE}"}
        full = components[FULL_SHAPE]
        nullable = {name for name, field in full["properties"].items() if field.get("nullable")}
        assert nullable == {
            "initial_state", "lms_link", "notification_dismiss_url", "reindex_link", "rerun_notification_id",
            "created_on",
        }
        assert set(full["required"]) == set(full["properties"])
        assert "initial_user_clipboard" not in full["properties"]

    def test_every_field_of_the_read_is_described(self):
        """Every property of every component the read references, however deep, has a description."""
        components = _published_schema()["components"]["schemas"]
        reached, pending = set(), [READ_SHAPE]
        while pending:
            name = pending.pop()
            if name not in reached:
                reached.add(name)
                refs = re.findall(r"'\$ref': '([^']+)'", str(components[name]))
                pending.extend(ref.rsplit("/", 1)[-1] for ref in refs)
        assert reached == {
            READ_SHAPE, DEFAULT_SHAPE, FULL_SHAPE, "CourseOutlineDeprecatedBlocks", "CourseOutlineInitialState",
            "CourseOutlineProctoringError", "CourseOutlineProctoringSetting",
        }
        undescribed = {
            f"{name}.{field}"
            for name in reached
            for field, spec in components[name].get("properties", {}).items()
            if not spec.get("description")
        }
        assert undescribed == set()

    def test_proctoring_errors_publish_the_shape_v1_publishes(self):
        """The outline's proctoring error components are v1's, with descriptions and their own names."""
        components = _published_schema()["components"]["schemas"]

        def shape(name, renames):
            """
            The component without descriptions, its references renamed by ``renames``.
            A described reference is published as a one-entry allOf; that is the reference.
            """
            def strip(node):
                if isinstance(node, dict):
                    node = {key: value for key, value in node.items() if key != "description"}
                    if list(node) == ["allOf"] and len(node["allOf"]) == 1:
                        return strip(node["allOf"][0])
                    return {
                        key: renames.get(value, value) if key == "$ref" else strip(value)
                        for key, value in node.items()
                    }
                return [strip(item) for item in node] if isinstance(node, list) else node
            return strip(components[name])

        ours = {"#/components/schemas/CourseOutlineProctoringSetting": "#/components/schemas/ProctoringErrorModel"}
        assert shape("CourseOutlineProctoringError", ours) == shape("ProctoringErrorList", {})
        assert shape("CourseOutlineProctoringSetting", ours) == shape("ProctoringErrorModel", {})
        assert components[FULL_SHAPE]["properties"]["proctoring_errors"]["items"] == {
            "$ref": "#/components/schemas/CourseOutlineProctoringError",
        }

    def test_the_examples_are_valid_bodies_of_their_view(self):
        operation = _published_schema()["paths"][self.PATH]["get"]
        examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
        assert set(examples) == {"OutlineTree", "OutlinePageData"}
        tree = examples["OutlineTree"]["value"]
        assert_matches_one_read_shape(tree, DEFAULT_SHAPE)
        assert [node["category"] for node in tree_nodes(tree)] == ["course", "chapter", "sequential", "sequential"]
        cut = [node for node in tree_nodes(tree) if "children_url" in node]
        assert len(cut) == 1
        assert parse_qs(urlsplit(cut[0]["children_url"]).query) == {"usage_key": [cut[0]["id"]], "depth": ["1"]}
        page = examples["OutlinePageData"]["value"]
        assert_matches_one_read_shape(page, FULL_SHAPE)
        node_keys = set(page["course_structure"]) - {"child_info"}
        assert node_keys < FULL_VIEW_NODE_KEYS["course"]

    def test_the_v1_read_is_deprecated(self):
        operation = _published_schema()["paths"][self.V1]["get"]
        assert operation["deprecated"] is True
        assert operation["operationId"] == "v1_course_index_retrieve"
