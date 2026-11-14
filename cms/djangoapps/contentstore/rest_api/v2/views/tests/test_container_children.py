"""Tests for the v2 container children API."""

import logging
import re
from collections import Counter
from functools import cache
from importlib import import_module
from unittest.mock import patch

import ddt
import jsonschema
import pytest
from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.test.signals import template_rendered
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
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
from rest_framework.test import APIClient
from xblock.core import XBlock

from cms.djangoapps.contentstore.rest_api.v1.views.tests.test_vertical_block import BaseXBlockContainer
from cms.djangoapps.contentstore.rest_api.v2.container_children_service import RENDER_ERROR_MESSAGE
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import (
    FIRST_LOOKUP_WRITES,
    PASSWORD,
    SignalRecorder,
    as_json_schema,
    build_course,
    count_mongo_calls,
    generate_cms_schema,
    logged_in_client,
    record_effects,
)
from cms.djangoapps.contentstore.tests.utils import routed_actions
from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content_tagging.toggles import DISABLE_TAGGING_FEATURE
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import BlockFactory  # pylint: disable=wrong-import-order
from xmodule.partitions.partitions import Group, UserPartition  # pylint: disable=wrong-import-order

UNKNOWN_COURSE_UNIT = "block-v1:NoSuchOrg+NoSuchCourse+NoSuchRun+type@vertical+block@unit"
VERSION_GUID = "0123456789abcdef01234567"
LIBRARY_KEYS = (
    "lb:Org:lib:problem:1",                                           # a block of a content library
    "lib-block-v1:Org+Lib+type@problem+block@x",                      # a block of a legacy library
    f"lib-block-v1:version@{VERSION_GUID}+type@problem+block@x",      # a legacy library key naming only a version
)
COURSE_NOT_FOUND = "The requested course does not exist."
CONTAINER_NOT_FOUND = "No container in this course has this key."
INVALID_VIEW = "Must be minimal or full."
VIEWS = ("default", "full")
ENVELOPE_KEYS = {"count", "next", "previous", "num_pages", "current_page", "start", "results"}
SUMMARY_KEYS = {"id", "category", "display_name", "display_type", "has_children"}
DETAIL_KEYS = {
    "name", "block_id", "block_type", "user_partition_info", "user_partitions", "upstream_link", "actions",
    "validation_messages", "render_error",
}

PAGE = "PaginatedCourseContainerChildReadList"
SUMMARY_SHAPE = "CourseContainerChild"
DETAIL_SHAPE = "CourseContainerChildFull"
ITEM_SHAPE = "CourseContainerChildRead"


def children_url(usage_key):
    """Return the address of the children of the container ``usage_key``."""
    return reverse("authoring_v2:container_child_list", kwargs={"usage_key": usage_key})


def v1_url(usage_key):
    """Return the deprecated address of the children of ``usage_key``."""
    return reverse("cms.djangoapps.contentstore:v1:container_children", kwargs={"usage_key_string": str(usage_key)})


def _client(user=None):
    """An API client that returns server errors as responses, authenticated as ``user`` if given."""
    client = APIClient()
    client.raise_request_exception = False
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _session_client(user):
    """A client signed in to a browser session as ``user``."""
    user.set_password(PASSWORD)
    user.save()
    return logged_in_client(user)


def _params(view):
    return {"view": "full"} if view == "full" else {}


def units_of(course_key):
    """Every unit of the course, in course order."""
    course = modulestore().get_course(course_key)
    return [
        unit
        for section in course.get_children()
        for subsection in section.get_children()
        for unit in subsection.get_children()
    ]


def _ccx_key(usage_key):
    """The key of ``usage_key``'s block in custom course (CCX) 1 of its course."""
    run = usage_key.course_key
    return UsageKey.from_string(
        f"ccx-block-v1:{run.org}+{run.course}+{run.run}+ccx@1+type@{usage_key.block_type}+block@{usage_key.block_id}"
    )


def _block(parent, category, display_name, **kwargs):
    """Create a block under ``parent`` and return it as stored."""
    location = BlockFactory.create(
        parent_location=parent.location, category=category, display_name=display_name, **kwargs,
    ).location
    return modulestore().get_item(location)


def _without_instance(envelope):
    """An error envelope without ``instance``, the request path, which holds the key the caller sent."""
    return {name: value for name, value in envelope.items() if name != "instance"}


@cache
def _published_schema():
    """The CMS authoring schema, generated once."""
    return generate_cms_schema()


@cache
def _published_components():
    """The schema's components as JSON Schema."""
    return as_json_schema(_published_schema()["components"]["schemas"])


def assert_matches_published_schema(body, component):
    """Assert ``body`` is valid against the published schema component ``component``."""
    jsonschema.validate(
        body, {"$ref": f"#/components/schemas/{component}", "components": {"schemas": _published_components()}},
    )


def assert_matches_one_item_shape(item, shape):
    """Assert ``item`` matches the published item type, and of its two shapes exactly ``shape``."""
    assert_matches_published_schema(item, ITEM_SHAPE)
    assert_matches_published_schema(item, shape)
    other = DETAIL_SHAPE if shape == SUMMARY_SHAPE else SUMMARY_SHAPE
    with pytest.raises(jsonschema.ValidationError):
        assert_matches_published_schema(item, other)


def assert_page(body, view):
    """Assert ``body`` is a page as published, each of its results of the shape ``view`` selects."""
    assert set(body) == ENVELOPE_KEYS
    assert_matches_published_schema(body, PAGE)
    for item in body["results"]:
        assert_matches_one_item_shape(item, DETAIL_SHAPE if view == "full" else SUMMARY_SHAPE)


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

def assert_inactive_jwt_reads(user, usage_key, view):
    """Deactivate ``user``; assert a JWT for it reads the children."""
    user.is_active = False
    user.save()
    header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}
    response = APIClient().get(children_url(usage_key), _params(view), **header)
    assert response.status_code == 200, response.content
    assert_page(response.json(), view)
    user.refresh_from_db()
    assert user.is_active is False


class _AccessMatrixMixin:
    """Checks a caller's status on both views, on GET and HEAD, against the expected one."""

    def assert_access(self, user, view, expected, usage_key=None):
        """Assert the status and, when refused, the error envelope."""
        usage_key = usage_key or self.unit.location
        response = _client(user).get(children_url(usage_key), _params(view))
        head = _client(user).head(children_url(usage_key), _params(view))
        assert response.status_code == expected, response.content
        assert head.status_code == expected
        if expected != 200:
            slug = {401: "authn", 403: "authz", 404: "not-found"}[expected]
            assert_error_envelope(response, expected_status=expected, expected_type_slug=slug)
        else:
            assert_page(response.json(), view)


@ddt.ddt
class ContainerChildrenAccessLegacyPermissionsTest(_AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may list a container's children when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)
        cls.other_course = build_course(sections=1, subsections=1, units=1)
        cls.unit = units_of(cls.course.id)[0]

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    @ddt.data(*VIEWS)
    def test_anonymous_caller_is_refused_with_401(self, view):
        self.assert_access(None, view, 401)
        response = _client().get(children_url(self.unit.location), _params(view))
        assert response["WWW-Authenticate"] == 'JWT realm="api"'

    @ddt.data(*VIEWS)
    def test_user_without_a_role_is_refused_with_403(self, view):
        self.assert_access(UserFactory(), view, 403)

    @ddt.data(*VIEWS)
    def test_an_enrolled_learner_is_refused_with_403(self, view):
        learner = UserFactory()
        CourseEnrollment.enroll(learner, self.course.id)
        self.assert_access(learner, view, 403)

    def test_v1_lists_the_children_for_a_user_without_a_role(self):
        """v1 checks only that the caller is signed in; v2 also requires permission to view the course."""
        user = UserFactory()
        assert _client(user).get(v1_url(self.unit.location)).status_code == 200
        assert _client(user).get(children_url(self.unit.location)).status_code == 403

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        self.assert_access(UserFactory(), view, 404, usage_key=UNKNOWN_COURSE_UNIT)
        self.assert_access(UserFactory(is_staff=True), view, 404, usage_key=UNKNOWN_COURSE_UNIT)

    @ddt.data(*VIEWS)
    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self, view):
        self.assert_access(None, view, 401, usage_key=UNKNOWN_COURSE_UNIT)

    @ddt.data(*VIEWS)
    def test_limited_staff_is_refused(self, view):
        self.assert_access(self._user_with(CourseLimitedStaffRole(self.course.id)), view, 403)

    @ddt.data(*VIEWS)
    def test_staff_of_another_course_is_refused(self, view):
        """A role on course A does not open a container of course B."""
        user = self._user_with(CourseStaffRole(self.other_course.id))
        self.assert_access(user, view, 200, usage_key=units_of(self.other_course.id)[0].location)
        self.assert_access(user, view, 403)

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
    def test_a_missing_block_is_404_for_a_reader_and_403_for_anyone_else(self, view):
        missing = self.unit.location.replace(block_id="missing")
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), view, 404, usage_key=missing)
        self.assert_access(UserFactory(), view, 403, usage_key=missing)

    @ddt.data(*VIEWS)
    def test_an_inactive_session_is_not_authenticated(self, view):
        """A session whose account is not activated is refused with 401, global staff included."""
        for user in (UserFactory(is_staff=True), self._user_with(CourseStaffRole(self.course.id))):
            client = _session_client(user)
            assert client.get(children_url(self.unit.location), _params(view)).status_code == 200, "active control"
            user.is_active = False
            user.save()
            response = client.get(children_url(self.unit.location), _params(view))
            assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    @ddt.data(*VIEWS)
    def test_jwt_caller_may_read(self, view):
        user = self._user_with(CourseStaffRole(self.course.id))
        response = APIClient().get(
            children_url(self.unit.location), _params(view), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(user)}",
        )
        assert response.status_code == 200, response.content

    @ddt.data(*VIEWS)
    def test_an_inactive_jwt_caller_who_is_global_staff_may_read(self, view):
        """A JWT is not checked for an activated account."""
        assert_inactive_jwt_reads(UserFactory(is_staff=True), self.unit.location, view)

    @ddt.data(*VIEWS)
    def test_bearer_token_is_refused(self, view):
        """Bearer tokens are not accepted here, though the v1 read takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        response = APIClient().get(
            children_url(self.unit.location), _params(view), HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert APIClient().get(v1_url(self.unit.location), HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200

    @ddt.data(*VIEWS)
    def test_a_custom_course_is_refused_to_everyone(self, view):
        """Studio read access is never granted on a custom course (CCX), global staff included."""
        key = _ccx_key(self.unit.location)
        self.assert_access(UserFactory(is_staff=True), view, 403, usage_key=key)
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), view, 403, usage_key=key)

    @ddt.data("post", "put", "patch", "delete")
    def test_other_methods_are_refused_after_authentication(self, method):
        """Anonymous callers are refused before the method is; signed-in readers learn it is not allowed."""
        url = children_url(self.unit.location)
        anonymous = getattr(_client(), method)(url, {}, format="json")
        assert_error_envelope(anonymous, expected_status=401, expected_type_slug="authn")
        reader = self._user_with(CourseStaffRole(self.course.id))
        refused = getattr(_client(reader), method)(url, {}, format="json")
        # The error library does not catalogue MethodNotAllowed, so it is typed
        # internal. Change the expected type here once it does.
        assert_error_envelope(refused, expected_status=405, expected_type_slug="internal")


@ddt.ddt
class ContainerChildrenAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may list a container's children when policy-based authoring permissions are on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)
        cls.other_course = build_course(sections=1, subsections=1, units=1)
        cls.unit = units_of(cls.course.id)[0]
        cls.other_unit = units_of(cls.other_course.id)[0]

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
        self.assert_access(user, view, 200, usage_key=self.other_unit.location)
        self.assert_access(user, view, 403)

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        self.assert_access(self.unauthorized_user, view, 404, usage_key=UNKNOWN_COURSE_UNIT)

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
    def test_a_missing_block_is_404_for_a_reader_and_403_for_anyone_else(self, view):
        missing = self.unit.location.replace(block_id="missing")
        self.assert_access(self._user_with(COURSE_AUDITOR.external_key), view, 404, usage_key=missing)
        self.assert_access(self.unauthorized_user, view, 403, usage_key=missing)

    @ddt.data(*VIEWS)
    def test_an_inactive_session_with_a_role_is_not_authenticated(self, view):
        user = self._user_with(COURSE_AUDITOR.external_key)
        client = _session_client(user)
        assert client.get(children_url(self.unit.location), _params(view)).status_code == 200, "active control"
        user.is_active = False
        user.save()
        response = client.get(children_url(self.unit.location), _params(view))
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    @ddt.data(*VIEWS)
    def test_an_inactive_jwt_caller_with_a_role_may_read(self, view):
        """A JWT is not checked for an activated account."""
        assert_inactive_jwt_reads(self._user_with(COURSE_AUDITOR.external_key), self.unit.location, view)

    @ddt.data(*VIEWS)
    def test_a_custom_course_is_refused_to_staff_and_course_staff(self, view):
        """A custom course (CCX) is not a scope the permission policy grants to staff or to a course role."""
        key = _ccx_key(self.unit.location)
        self.assert_access(self.staff_user, view, 403, usage_key=key)
        self.assert_access(self._user_with(COURSE_STAFF.external_key), view, 403, usage_key=key)

    @ddt.data(*VIEWS)
    def test_staff_and_superuser_without_a_role_are_admitted_by_the_policy(self, view):
        self.assert_access(self.staff_user, view, 200)
        self.assert_access(self.super_user, view, 200)


# ---------------------------------------------------------------------------
# What is a container
# ---------------------------------------------------------------------------

class ContainerChildrenMembershipTest(ModuleStoreTestCase):
    """
    A container is a block other than the course that can hold children.
    Anything else is answered with 404 without naming the block.
    """

    def setUp(self):
        super().setUp()
        course = build_course(sections=1, subsections=1, units=1)
        self.course_key = course.id
        self.store = modulestore()
        self.course = self.store.get_course(course.id)
        self.section = self.course.get_children()[0]
        self.subsection = self.section.get_children()[0]
        self.unit = self.subsection.get_children()[0]
        self.problem = self.unit.get_children()[0]
        self.split = _block(self.unit, "split_test", "Split test")
        self.group = _block(self.split, "vertical", "Group")
        self.empty_unit = _block(self.subsection, "vertical", "Empty unit")
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)
        self.client = _client(self.reader)

    def _assert_not_a_container(self, key):
        """Assert both views answer 404 for ``key`` without naming it."""
        for params in ({}, {"view": "full"}):
            response = self.client.get(children_url(key), params)
            envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
            assert envelope["detail"] == CONTAINER_NOT_FOUND
            assert envelope["instance"] == children_url(key)
            assert str(key) not in str(_without_instance(envelope))

    def test_every_kind_of_container_lists_its_children_in_order(self):
        for block in (self.section, self.subsection, self.unit, self.split, self.group, self.empty_unit):
            expected = [str(child.location) for child in self.store.get_item(block.location).get_children()]
            for view, key in (("default", "id"), ("full", "block_id")):
                response = self.client.get(children_url(block.location), _params(view))
                assert response.status_code == 200, (block.location, response.content)
                body = response.json()
                assert_page(body, view)
                assert [item[key] for item in body["results"]] == expected
                assert body["count"] == len(expected)

    def test_a_container_without_children_is_an_empty_page(self):
        """The unit holds nothing; it is a container all the same, so the answer is a page, not a 404."""
        body = self.client.get(children_url(self.empty_unit.location)).json()
        assert body == {"count": 0, "next": None, "previous": None, "num_pages": 1, "current_page": 1, "start": 0,
                        "results": []}

    def test_the_course_is_not_a_container(self):
        self._assert_not_a_container(self.course.location)

    def test_a_component_is_not_a_container(self):
        """v1 answers a component's key with an empty list of children."""
        self._assert_not_a_container(self.problem.location)
        legacy = self.client.get(v1_url(self.problem.location))
        assert (legacy.status_code, legacy.json()["children"]) == (200, [])

    def test_a_missing_block_is_not_a_container(self):
        """v1 answers it with a server error."""
        missing = self.unit.location.replace(block_id="missing")
        self._assert_not_a_container(missing)
        assert self.client.get(v1_url(missing)).status_code == 500

    def test_a_container_the_course_does_not_reach_is_listed(self):
        """Only the key's course is looked at, not the path from the course down to the block."""
        orphan = self.store.create_item(self.user.id, self.course_key, "chapter", "orphan_section")
        child = _block(orphan, "sequential", "Subsection of an orphan section")
        body = self.client.get(children_url(orphan.location)).json()
        assert [item["id"] for item in body["results"]] == [str(child.location)]

    def test_a_course_info_block_that_does_not_exist_is_not_created(self):
        """v1 creates it, which makes its GET a write."""
        key = self.course_key.make_usage_key("course_info", "updates")
        assert not self.store.has_item(key)
        # The first permission lookup on a database stores the policy version row; it is not under test.
        assert self.client.get(children_url(self.unit.location)).status_code == 200
        with record_effects(self) as effects:
            self._assert_not_a_container(key)
        assert (effects["sql_writes"], effects["mongo_writes"], effects["update_item"]) == ([], 0, [])
        assert not self.store.has_item(key)
        assert self.client.get(v1_url(key)).status_code == 200
        assert self.store.has_item(key)

    def test_keys_outside_any_course_are_not_found(self):
        for key in (*LIBRARY_KEYS, f"block-v1:version@{VERSION_GUID}+type@vertical+block@x"):
            for params in ({}, {"view": "full"}):
                response = self.client.get(children_url(key), params)
                envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
                assert envelope["detail"] == COURSE_NOT_FOUND
                assert VERSION_GUID not in str(_without_instance(envelope))
            assert_error_envelope(_client().get(children_url(key)), expected_status=401, expected_type_slug="authn")


# ---------------------------------------------------------------------------
# The default view and the page
# ---------------------------------------------------------------------------

@ddt.ddt
class ContainerChildrenDefaultViewTest(ModuleStoreTestCase):
    """A brief description of each child, in a page."""

    def setUp(self):
        super().setUp()
        course = build_course(sections=1, subsections=1, units=1)
        self.store = modulestore()
        self.section = self.store.get_course(course.id).get_children()[0]
        self.subsection = self.section.get_children()[0]
        self.unit = self.subsection.get_children()[0]
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)
        self.client = _client(self.reader)

    def _get(self, block, **params):
        return self.client.get(children_url(block.location), params)

    def test_each_kind_of_child_is_described_by_its_type(self):
        html = _block(self.unit, "html", "Introduction")
        split = _block(self.unit, "split_test", "Experiment")
        group = _block(split, "vertical", "Group")
        problem = self.unit.get_children()[0]
        assert self._get(self.unit).json()["results"] == [
            {"id": str(problem.location), "category": "problem", "display_name": "Problem 0.0.0",
             "display_type": "Problem", "has_children": False},
            {"id": str(html.location), "category": "html", "display_name": "Introduction",
             "display_type": "Text", "has_children": False},
            {"id": str(split.location), "category": "split_test", "display_name": "Experiment",
             "display_type": "Content Experiment", "has_children": True},
        ]
        assert self._get(split).json()["results"] == [
            {"id": str(group.location), "category": "vertical", "display_name": "Group",
             "display_type": "Vertical", "has_children": True},
        ]
        assert self._get(self.subsection).json()["results"] == [
            {"id": str(self.unit.location), "category": "vertical", "display_name": "Unit 0.0.0",
             "display_type": "Unit", "has_children": True},
        ]
        assert self._get(self.section).json()["results"] == [
            {"id": str(self.subsection.location), "category": "sequential", "display_name": "Subsection 0.0",
             "display_type": "Subsection", "has_children": True},
        ]

    def test_a_block_type_that_declares_no_display_name_has_a_null_display_type(self):
        """The published schema marks display_type nullable, so the nullable case has to be real."""
        with patch(
            "cms.djangoapps.contentstore.rest_api.v2.container_children_service.xblock_type_display_name",
            return_value=None,
        ):
            body = self._get(self.unit).json()
        assert [item["display_type"] for item in body["results"]] == [None]
        assert_page(body, "default")

    @ddt.data({}, {"view": "minimal"}, {"view": ""})
    def test_the_brief_description_is_the_default(self, params):
        body = self._get(self.unit, **params).json()
        assert set(body["results"][0]) == SUMMARY_KEYS

    def test_the_view_is_validated(self):
        for value in ("bogus", "FULL", "full,minimal"):
            response = self._get(self.unit, view=value)
            envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
            assert envelope["errors"] == {"view": [INVALID_VIEW]}

    def test_the_order_is_the_order_the_container_stores(self):
        first = _block(self.unit, "html", "First")
        second = _block(self.unit, "html", "Second")
        unit = self.store.get_item(self.unit.location)
        unit.children = [second.location, unit.children[0], first.location]
        self.store.update_item(unit, self.user.id)
        expected = [str(second.location), str(unit.children[1]), str(first.location)]
        assert [item["id"] for item in self._get(self.unit).json()["results"]] == expected
        legacy = self.client.get(v1_url(self.unit.location)).json()
        assert [item["block_id"] for item in legacy["children"]] == expected

    def test_a_reference_to_a_block_that_cannot_be_loaded_is_left_out(self):
        """v1 fails on the whole container."""
        unit = self.store.get_item(self.unit.location)
        unit.children.append(self.unit.location.replace(block_type="html", block_id="gone"))
        self.store.update_item(unit, self.user.id)
        for params in ({}, {"view": "full"}):
            body = self._get(self.unit, **params).json()
            assert body["count"] == 1
            assert len(body["results"]) == 1
        assert self.client.get(v1_url(self.unit.location)).status_code == 500

    def test_a_page_holds_ten_children_by_default_and_links_to_the_next(self):
        for index in range(11):
            _block(self.unit, "html", f"Text {index}")
        first = self._get(self.unit).json()
        assert (first["count"], first["num_pages"], first["current_page"], first["start"]) == (12, 2, 1, 0)
        assert len(first["results"]) == 10
        assert first["previous"] is None
        assert first["next"] == f"http://testserver{children_url(self.unit.location)}?page=2"
        second = self._get(self.unit, page=2).json()
        assert (second["current_page"], second["start"], len(second["results"])) == (2, 10, 2)
        assert second["next"] is None
        assert second["previous"] == f"http://testserver{children_url(self.unit.location)}"
        assert [item["display_name"] for item in first["results"] + second["results"]][1:] == [
            f"Text {index}" for index in range(11)
        ]

    def test_the_page_links_keep_the_view_and_the_page_size(self):
        for index in range(4):
            _block(self.unit, "html", f"Text {index}")
        body = self._get(self.unit, view="full", page_size=2, page=2).json()
        assert (body["count"], body["num_pages"], body["start"]) == (5, 3, 2)
        for link, page in ((body["previous"], None), (body["next"], "3")):
            query = dict(part.split("=") for part in link.split("?")[1].split("&"))
            assert query == {"view": "full", "page_size": "2", **({"page": page} if page else {})}

    def test_a_page_never_holds_more_than_a_hundred_children(self):
        with self.store.bulk_operations(self.unit.location.course_key):
            for index in range(100):
                _block(self.unit, "html", f"Text {index}")
        body = self._get(self.unit, page_size=101).json()
        assert (body["count"], len(body["results"]), body["num_pages"]) == (101, 100, 2)
        assert len(self._get(self.unit, page=2, page_size=101).json()["results"]) == 1

    @ddt.data({"page": 2}, {"page": 0}, {"page": "x"}, {"page": -1}, {"page": 2, "view": "full"})
    def test_a_page_that_does_not_exist_is_404(self, params):
        response = self._get(self.unit, **params)
        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == "Invalid page."

    def test_the_details_are_worked_out_for_the_requested_page_only(self):
        """The cost of view=full follows the page size, not the number of children."""
        for index in range(5):
            _block(self.unit, "html", f"Text {index}")
        service = "cms.djangoapps.contentstore.rest_api.v2.container_children_service"
        with patch(f"{service}.get_xblock_render_error", return_value="") as render, \
                patch(f"{service}.get_user_partition_info", return_value=[]) as partitions, \
                patch(f"{service}.UpstreamLink.try_get_for_block") as upstream:
            upstream.return_value.upstream_ref = None
            body = self._get(self.unit, view="full", page_size=2, page=2).json()
        assert (body["count"], len(body["results"])) == (6, 2)
        assert render.call_count == partitions.call_count == upstream.call_count == 2


# ---------------------------------------------------------------------------
# The full view
# ---------------------------------------------------------------------------

RENDER_SECRET = "secret detail of /var/lib/studio/render.py"
SERVICE = "cms.djangoapps.contentstore.rest_api.v2.container_children_service"


class FailingRenderBlock(XBlock):
    """A block whose rendering fails with a message that must not reach the caller."""

    def student_view(self, context=None):  # pylint: disable=unused-argument
        raise RuntimeError(RENDER_SECRET)


@ddt.ddt
class ContainerChildrenFullViewTest(ModuleStoreTestCase):
    """What view=full says about each child, for each way the data can vary."""

    def setUp(self):
        super().setUp()
        course = build_course(sections=1, subsections=1, units=1)
        self.store = modulestore()
        self.subsection = self.store.get_course(course.id).get_children()[0].get_children()[0]
        self.unit = self.subsection.get_children()[0]
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)
        self.client = _client(self.reader)

    def _full(self, block):
        """The full view of ``block``'s children by name."""
        response = self.client.get(children_url(block.location), {"view": "full"})
        assert response.status_code == 200, response.content
        body = response.json()
        assert_page(body, "full")
        return {item["name"]: item for item in body["results"]}

    def test_a_child_that_is_not_linked_to_a_library_has_a_null_link(self):
        child = self._full(self.unit)["Problem 0.0.0"]
        assert set(child) == DETAIL_KEYS
        assert child["upstream_link"] is None
        assert child["validation_messages"] == []
        assert child["render_error"] == ""

    def test_a_link_to_a_library_that_cannot_be_followed_reports_why_and_nothing_else(self):
        """Every field of the link that is null in practice is declared nullable."""
        _block(self.unit, "html", "Broken", upstream="lb:Org:nolib:html:missing", upstream_version=3)
        _block(self.unit, "html", "Unversioned", upstream="lb:Org:nolib:html:other")
        link = self._full(self.unit)["Broken"]["upstream_link"]
        assert link["upstream_ref"] == "lb:Org:nolib:html:missing"
        assert link["version_synced"] == 3
        assert link["version_available"] is None
        assert link["version_declined"] is None
        assert isinstance(link["error_message"], str)
        assert link["error_message"] != ""
        assert link["ready_to_sync"] is False
        assert link["downstream_customized"] == []
        assert link["top_level_parent_key"] is None
        assert "ready_to_sync_children" not in link
        assert self._full(self.unit)["Unversioned"]["upstream_link"]["version_synced"] is None

    def test_the_actions_follow_where_the_child_sits(self):
        """
        A child that holds children and has no page of its own is edited inline,
        so whole-block actions do not apply to it; everything else can be copied.
        """
        split = _block(self.unit, "split_test", "Split")
        _block(split, "vertical", "Group")
        everything = {
            "can_copy": True, "can_duplicate": True, "can_move": True, "can_manage_access": True,
            "can_delete": True, "can_manage_tags": True,
        }
        inline = {**everything, "can_copy": False, "can_duplicate": False, "can_move": False,
                  "can_manage_access": False}
        assert self._full(self.unit)["Problem 0.0.0"]["actions"] == everything
        assert self._full(self.unit)["Split"]["actions"] == everything
        assert self._full(split)["Group"]["actions"] == inline
        assert self._full(self.subsection)["Unit 0.0.0"]["actions"] == everything

    @ddt.data(True, False)
    def test_tagging_can_be_turned_off(self, disabled):
        with override_waffle_flag(DISABLE_TAGGING_FEATURE, disabled):
            actions = self._full(self.unit)["Problem 0.0.0"]["actions"]
        assert actions["can_manage_tags"] is (not disabled)

    def test_the_visibility_groups_and_what_contradicts_them_are_reported(self):
        course = self.store.get_course(self.unit.location.course_key)
        course.user_partitions = [
            UserPartition(0, "Cohorts", "Test partition", [Group("0", "alpha"), Group("1", "beta")]),
        ]
        self.store.update_item(course, self.user.id)
        unit = self.store.get_item(self.unit.location)
        unit.group_access = {0: [1]}
        self.store.update_item(unit, self.user.id)
        problem = self.store.get_item(self.unit.get_children()[0].location)
        problem.group_access = {0: [0]}
        self.store.update_item(problem, self.user.id)
        child = self._full(self.unit)["Problem 0.0.0"]
        assert [(message["type"], message["text"]) for message in child["validation_messages"]] == [
            ("error", "This component's access settings contradict its parent's access settings."),
        ]
        assert [
            (partition["name"], [(group["name"], group["selected"]) for group in partition["groups"]])
            for partition in child["user_partitions"]
        ] == [("Cohorts", [("alpha", True), ("beta", False)]), ("Enrollment Track Groups", [("Audit", False)])]
        assert child["user_partition_info"] == {
            "selectable_partitions": [], "selected_partition_index": -1, "selected_groups_label": "",
        }

    @XBlock.register_temp_plugin(FailingRenderBlock, "failing_render")
    def test_a_child_that_fails_to_render_is_reported_without_the_cause(self):
        """The cause comes from the block's own code, so it is logged with the child's key and not returned."""
        broken = _block(self.unit, "failing_render", "Broken")
        with self.assertLogs(SERVICE, level="WARNING") as logs:
            response = self.client.get(children_url(self.unit.location), {"view": "full"})
        body = response.json()
        assert_page(body, "full")
        by_name = {item["name"]: item for item in body["results"]}
        assert by_name["Broken"]["render_error"] == RENDER_ERROR_MESSAGE
        assert by_name["Problem 0.0.0"]["render_error"] == ""
        assert RENDER_SECRET not in response.content.decode()
        assert len(logs.records) == 1
        assert str(broken.location) in logs.records[0].getMessage()
        assert RENDER_SECRET in logs.records[0].getMessage()
        assert logs.records[0].levelno == logging.WARNING
        legacy = self.client.get(v1_url(self.unit.location)).json()
        assert {child["name"]: child["render_error"] for child in legacy["children"]}["Broken"] == RENDER_SECRET

    def test_the_default_view_neither_renders_nor_loads_the_course(self):
        with patch(f"{SERVICE}.get_xblock_render_error") as render, patch.object(
            self.store.__class__, "get_course", side_effect=AssertionError("the course is not needed")
        ):
            assert self.client.get(children_url(self.unit.location)).status_code == 200
        render.assert_not_called()


# ---------------------------------------------------------------------------
# Parity with the v1 read
# ---------------------------------------------------------------------------

# Differences between the v1 read and view=full: (json path pattern, reason).
# The comparison asserts that each occurs and that nothing else differs.
INTENTIONAL_DIFFERENCES = [
    (
        r"is_published|can_paste_component|display_name|upstream_ready_to_sync_children_info(\[\d+\].*)?",
        "v1 also describes the container itself; the page lists only its children",
    ),
    (r"count|next|previous|num_pages|current_page|start", "the page envelope"),
    (
        r"results\[\d+\]\.render_error",
        "the cause of a render failure is logged and a fixed message returned",
    ),
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


def _differences(legacy, new):
    """
    Compare two bodies. Return the json paths that differ, those among them no
    declared difference explains, and the declared differences that did not occur.
    """
    a, b = _flatten(legacy), _flatten(new)
    changed = {key for key in set(a) | set(b) if not _same(a.get(key, _ABSENT), b.get(key, _ABSENT))}
    declared = [pattern for pattern, _ in INTENTIONAL_DIFFERENCES]
    unexpected = {key for key in changed if not any(re.fullmatch(pattern, key) for pattern in declared)}
    missing = {pattern for pattern in declared if not any(re.fullmatch(pattern, key) for key in changed)}
    return changed, unexpected, missing


def _as_page(legacy):
    """The v1 body with its list of children where the page holds its results."""
    body = dict(legacy)
    body["results"] = body.pop("children")
    return body


def test_the_comparison_sees_types_and_empty_containers():
    assert _differences({"a": {"b": []}}, {"a": {}})[0] == {"a", "a.b"}
    assert _differences({"x": True}, {"x": 1})[0] == {"x"}
    assert _differences({"x": 1.0}, {"x": 1})[0] == {"x"}
    assert _differences({"x": {}}, {"x": []})[0] == {"x"}
    assert _differences({"x": None, "y": 1}, {"y": 1})[0] == {"x"}
    assert _differences({"x": [1, {"y": False}]}, {"x": [1, {"y": False}]})[0] == set()


def test_each_declared_difference_must_occur_and_nothing_else_may():
    legacy = {"results": [{"render_error": "x", "name": "a"}], "is_published": False, "display_name": "u"}
    new = {"results": [{"render_error": "fixed", "name": "a"}], "count": 1}
    _, unexpected, missing = _differences(legacy, new)
    assert unexpected == set()
    assert missing == {pattern for pattern, _ in INTENTIONAL_DIFFERENCES} - {
        INTENTIONAL_DIFFERENCES[0][0], INTENTIONAL_DIFFERENCES[1][0], INTENTIONAL_DIFFERENCES[2][0],
    }
    _, unexpected, missing = _differences(legacy, {**new, "results": [{"render_error": "fixed", "name": "b"}]})
    assert unexpected == {"results[0].name"}
    _, unexpected, missing = _differences(
        {"results": [{"render_error": ""}], "is_published": False, "display_name": "u"},
        {"results": [{"render_error": ""}], "count": 1},
    )
    assert (unexpected, missing) == (set(), {INTENTIONAL_DIFFERENCES[2][0]})


class _ParityMixin:
    """Compares view=full against the v1 read of the same container for the same caller."""

    def pair(self, client, usage_key, **params):
        """The v1 and view=full reads of ``usage_key`` as JSON; v2 is read first so nothing v1 stores reaches it."""
        new = client.get(children_url(usage_key), {"view": "full", **params})
        legacy = client.get(v1_url(usage_key))
        assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
        return _as_page(legacy.json()), new.json()

    def compare(self, client, usage_key, **params):
        """Return the paths that differ between the two reads of ``usage_key``; ``unexpected`` ones fail here."""
        legacy, new = self.pair(client, usage_key, **params)
        changed, unexpected, _ = _differences(legacy, new)
        assert unexpected == set(), (str(usage_key), sorted(unexpected))
        assert_page(new, "full")
        assert [child["block_id"] for child in legacy["results"]] == [item["block_id"] for item in new["results"]]
        # Where v1 reports a render failure the page carries the fixed message, and nowhere else.
        for old, item in zip(legacy["results"], new["results"], strict=True):
            assert item["render_error"] == (RENDER_ERROR_MESSAGE if old["render_error"] else ""), item["block_id"]
        return changed


class ContainerChildrenParityTest(_ParityMixin, ModuleStoreTestCase):
    """view=full returns what v1 returns for each child, except the declared differences."""

    @XBlock.register_temp_plugin(FailingRenderBlock, "failing_render")
    def test_every_kind_of_container_is_described_as_v1_describes_it(self):
        course = build_course(sections=1, subsections=2, units=1)
        store = modulestore()
        course = store.get_course(course.id)
        course.user_partitions = [
            UserPartition(0, "Cohorts", "Test partition", [Group("0", "alpha"), Group("1", "beta")]),
        ]
        store.update_item(course, self.user.id)
        section = store.get_course(course.id).get_children()[0]
        subsection = section.get_children()[0]
        unit = subsection.get_children()[0]
        _block(unit, "html", "Introduction")
        _block(unit, "html", "Broken link", upstream="lb:Org:nolib:html:missing", upstream_version=2)
        _block(unit, "failing_render", "Fails to render")
        split = _block(unit, "split_test", "Split")
        _block(split, "vertical", "Group A")
        _block(split, "vertical", "Group B")
        restricted_unit = store.get_item(unit.location)
        restricted_unit.group_access = {0: [1]}
        store.update_item(restricted_unit, self.user.id)
        restricted_problem = store.get_item(unit.get_children()[0].location)
        restricted_problem.group_access = {0: [0]}
        store.update_item(restricted_problem, self.user.id)
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        client = _client(reader)

        changed = set()
        containers = [section, subsection, unit, split, store.get_item(unit.location).get_children()[-1]]
        for container in containers + [section.get_children()[1].get_children()[0]]:
            changed |= self.compare(client, container.location)
        with override_waffle_flag(DISABLE_TAGGING_FEATURE, True):
            changed |= self.compare(client, unit.location)

        declared = [pattern for pattern, _ in INTENTIONAL_DIFFERENCES]
        for pattern in declared:
            assert any(re.fullmatch(pattern, key) for key in changed), f"declared difference never occurred: {pattern}"

    def test_pagination_does_not_change_what_is_described(self):
        course = build_course(sections=1, subsections=1, units=1)
        unit = units_of(course.id)[0]
        for index in range(4):
            _block(unit, "html", f"Text {index}")
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        client = _client(reader)
        whole, _ = self.pair(client, unit.location)[0], None
        pages = [
            client.get(children_url(unit.location), {"view": "full", "page_size": 2, "page": page}).json()["results"]
            for page in (1, 2, 3)
        ]
        assert [item for page in pages for item in page] == whole["results"]
        assert [len(page) for page in pages] == [2, 2, 1]


class ContainerChildrenLibraryLinkedParityTest(_ParityMixin, BaseXBlockContainer):
    """Children linked to library blocks and to a library container are described as v1 describes them."""

    view_name = "container_children"

    def test_a_linked_container_reports_which_of_its_blocks_are_ready_to_sync(self):
        changed = self.compare(self.client, self.sequential.location)
        body = self.client.get(children_url(self.sequential.location), {"view": "full"}).json()
        link = body["results"][0]["upstream_link"]
        assert body["results"][0]["block_id"] == str(self.vertical.location)
        assert link["upstream_ref"] == self.unit["id"]
        assert link["ready_to_sync_children"] == [{
            "id": str(self.html_unit_second.usage_key),
            "name": "Html Content 2",
            "upstream": self.html_block["id"],
        }]
        assert not any(key.startswith("results[0].upstream_link") for key in changed)

    def test_a_linked_block_reports_the_version_the_library_has_moved_to(self):
        self.compare(self.client, self.vertical.location)
        body = self.client.get(children_url(self.vertical.location), {"view": "full"}).json()
        first, second = body["results"]
        assert first["upstream_link"] is None
        assert second["upstream_link"] == {
            "upstream_ref": self.html_block["id"],
            "version_synced": 1,
            "version_available": 2,
            "version_declined": None,
            "error_message": None,
            "ready_to_sync": True,
            "downstream_customized": [],
            "top_level_parent_key": None,
        }


# ---------------------------------------------------------------------------
# Reads write nothing
# ---------------------------------------------------------------------------

class _ReadIsSideEffectFreeMixin:
    """
    Every read (both views, GET and HEAD, of a unit holding a block linked to
    a library, a split test and a block that fails to render, of a subsection
    and of a section) writes no domain row and no Mongo document, stores
    nothing through the modulestore, and sends no openedx-event.

    Two things are written, and only these:

    * The reads start on a database that has never served a role or
      permission lookup, and the first lookup stores the permission library's
      policy version row (``PolicyCacheControl``).
    * Rendering a child for the full view can store state for the previewing
      user in the session, and the session is saved with the response. The
      default view renders nothing and never touches the session.

    Each save sends the model's pre_save and post_save, and nothing else is
    sent beyond the test client's own report of rendered templates.
    """

    SESSION_TABLE = "django_session"

    def reader_for(self, course):
        """Return a user who may read ``course``."""
        raise NotImplementedError

    def _course(self):
        """A course whose first unit holds children of each kind that reads differently."""
        course = build_course(sections=1, subsections=1, units=2)
        unit = units_of(course.id)[0]
        _block(unit, "html", "Linked", upstream="lb:Org:nolib:html:missing", upstream_version=1)
        _block(_block(unit, "split_test", "Split"), "vertical", "Group")
        _block(unit, "failing_render", "Fails to render")
        return course, unit

    def _assert_reads_have_no_side_effects(self):
        """Make every read of the course and check what each one wrote and sent."""
        course, unit = self._course()
        subsection = unit.get_parent()
        section = subsection.get_parent()
        client = _client(self.reader_for(course))
        PolicyCacheControl.objects.all().delete()
        full = {"view": "full"}
        reads = [
            ("get", unit, {}), ("get", unit, full), ("head", unit, {}), ("head", unit, full),
            ("get", subsection, {}), ("get", subsection, full), ("get", section, {}), ("head", section, full),
        ]
        assert not PolicyCacheControl.objects.exists()
        for index, (method, block, params) in enumerate(reads):
            # The signal recorder is outermost, so callbacks run on commit as
            # the effects recorder exits are still seen.
            with SignalRecorder() as signals, record_effects(self) as effects:
                response = getattr(client, method)(children_url(block.location), params)
            first = index == 0
            request = (index, method, block.location.block_type, params)
            assert response.status_code == 200, (request, response.content)
            session = [write for write in effects["sql_writes"] if write[1] == self.SESSION_TABLE]
            others = [write for write in effects["sql_writes"] if write[1] != self.SESSION_TABLE]
            assert others == (FIRST_LOOKUP_WRITES if first else []), request
            assert len(session) <= 1, request
            if not params:
                assert session == [], request
            saved = (["PolicyCacheControl"] if first else []) + ["Session"] * len(session)
            assert sorted(effects["saves"]) == sorted(saved), request
            expected_signals = ([pre_save, post_save] if first else []) + [pre_save, post_save] * len(session)
            sent = [signal for _, signal in signals.unexpected()]
            if params:
                # The test instrumentation reports each template a rendered child uses; nothing else receives it.
                sent = [signal for signal in sent if signal is not template_rendered]
            assert Counter(sent) == Counter(expected_signals), request
            assert effects["mongo_writes"] == 0, request
            assert effects["update_item"] == [], request
            assert effects["xblock_updated"] == [], request
            assert effects["published"] == [], request

    @XBlock.register_temp_plugin(FailingRenderBlock, "failing_render")
    def test_reads_have_no_side_effects(self):
        self._assert_reads_have_no_side_effects()

    @XBlock.register_temp_plugin(FailingRenderBlock, "failing_render")
    def test_a_signed_in_session_is_written_by_the_full_view_only(self):
        """The session of a browser caller is the one thing a read stores, and only by rendering."""
        course, unit = self._course()
        client = _session_client(self.reader_for(course))
        client.get(children_url(unit.location))
        with record_effects(self) as effects:
            assert client.get(children_url(unit.location)).status_code == 200
        assert effects["sql_writes"] == []
        with record_effects(self) as effects:
            assert client.get(children_url(unit.location), {"view": "full"}).status_code == 200
        assert {table for _, table in effects["sql_writes"]} == {self.SESSION_TABLE}
        assert effects["mongo_writes"] == 0


class ContainerChildrenSideEffectsLegacyPermissionsTest(_ReadIsSideEffectFreeMixin, ModuleStoreTestCase):
    """
    Reads by course staff with policy-based authoring permissions off. Course
    staff rather than global staff, whose access is decided without a role lookup.
    """

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class ContainerChildrenSideEffectsAuthzTest(
    CourseAuthoringAuthzTestMixin, _ReadIsSideEffectFreeMixin, ModuleStoreTestCase,
):
    """Reads by a course auditor with policy-based authoring permissions on."""

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        return reader


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------

def _measure(request):
    """SQL statements, Mongo reads and Mongo writes of the second of two identical requests."""
    request()
    with CaptureQueriesContext(connection) as queries, count_mongo_calls() as mongo:
        response = request()
    assert response.status_code == 200, response.content
    return [query["sql"] for query in queries.captured_queries], mongo["reads"], mongo["writes"]


def _statement_shape(sql):
    """A SQL statement with its literal values and savepoint names blanked, so two requests compare."""
    return re.sub(r"'(?:[^']|'')*'|\bs\d+_x\d+\b|\b\d+(?:\.\d+)?\b", "?", sql)


def _is_permission_lookup(sql):
    """A statement that checks the caller may view the course, which v1 does not do."""
    tables = (  # pylint: disable=protected-access
        CourseOverview._meta.db_table, "waffle_utils_waffleflag", "openedx_authz", '"auth_user"',
        "user_api_userretirementrequest",
    )
    return sql.lstrip().upper().startswith("SELECT") and any(table in sql for table in tables)


CHILDREN = (1, 5, 20)


class _QueryCountMixin:
    """
    Request costs of a unit holding 1, 5 and 20 children (and of a subsection
    holding as many units), against the v1 read of it. The course has an
    overview record, as every published course does.
    """

    def reader_for(self, course):
        raise NotImplementedError

    def costs(self, count, category="html"):
        """``{read: (sql statements, mongo reads, mongo writes)}`` for v1 and the views of ``count`` children."""
        course = build_course(1, 1, 1)
        CourseOverview.get_from_id(course.id)
        unit = units_of(course.id)[0]
        container = unit if category == "html" else unit.get_parent()
        for index in range(count - len(container.get_children())):
            _block(container, category, f"Child {index}")
        client = _client(self.reader_for(course))
        key = container.location
        return {
            "v1": _measure(lambda: client.get(v1_url(key))),
            "default": _measure(lambda: client.get(children_url(key))),
            "full": _measure(lambda: client.get(children_url(key), {"view": "full"})),
            "full_page_of_5": _measure(lambda: client.get(children_url(key), {"view": "full", "page_size": 5})),
        }

    def test_the_default_view_costs_the_same_for_every_number_of_children(self):
        for category in ("html", "vertical"):
            default = {}
            for count in CHILDREN:
                statements, reads, writes = self.costs(count, category)["default"]
                default[count] = (len(statements), reads, writes)
            assert len(set(default.values())) == 1, (category, default)
            assert default[1][2] == 0

    def test_the_full_view_costs_no_more_than_v1_beyond_the_permission_check(self):
        for category in ("html", "vertical"):
            for count in CHILDREN:
                costs = self.costs(count, category)
                full, v1 = (Counter(map(_statement_shape, costs[read][0])) for read in ("full", "v1"))
                extra = full - v1
                assert all(_is_permission_lookup(sql) for sql in extra), (category, count, extra)
                assert sum(extra.values()) <= 4, (category, count, extra)
                assert costs["full"][1] <= costs["v1"][1], (category, count, costs)
                assert costs["v1"][2] == costs["full"][2] == costs["full_page_of_5"][2] == 0, (category, count, costs)

    def test_the_full_view_costs_what_the_page_holds_not_what_the_container_holds(self):
        """A page of five of twenty children costs as much as a container of five."""
        five = self.costs(5)["full"]
        paged = self.costs(20)["full_page_of_5"]
        assert (len(paged[0]), paged[1]) == (len(five[0]), five[1])


class ContainerChildrenQueryCountTest(_QueryCountMixin, ModuleStoreTestCase):
    """Request costs with policy-based authoring permissions off, read by course staff."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class ContainerChildrenQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
    """Request costs with policy-based authoring permissions on, read by a course auditor."""

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        return reader


# ---------------------------------------------------------------------------
# Addresses, methods and the published schema
# ---------------------------------------------------------------------------

UNIT_KEY = "block-v1:edX+ToyX+Toy_Course+type@vertical+block@unit"


@ddt.ddt
class ContainerChildrenRoutingTest(SharedModuleStoreTestCase):
    """The address, the deprecated v1 addresses, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)
        cls.unit = units_of(cls.course.id)[0]

    def test_the_address(self):
        assert children_url(UNIT_KEY) == f"/api/authoring/v2/containers/{UNIT_KEY}/children/"
        match = resolve(children_url(UNIT_KEY))
        assert match.view_name == "authoring_v2:container_child_list"
        assert routed_actions(match.func) == {"get": "list"}
        assert match.kwargs == {"usage_key": UsageKey.from_string(UNIT_KEY)}

    def test_the_container_detail_address_is_not_shadowed(self):
        match = resolve(f"/api/authoring/v2/containers/{UNIT_KEY}/")
        assert match.view_name == "authoring_v2:container_detail"
        assert routed_actions(match.func) == {"get": "retrieve"}

    def test_the_v1_addresses_are_unchanged(self):
        assert v1_url(UNIT_KEY) == f"/api/contentstore/v1/container/{UNIT_KEY}/children"
        assert resolve(v1_url(UNIT_KEY)).view_name == "cms.djangoapps.contentstore:v1:container_children"
        vertical = reverse("cms.djangoapps.contentstore:v1:container_vertical", kwargs={"usage_key_string": UNIT_KEY})
        assert vertical == f"/api/contentstore/v1/container/vertical/{UNIT_KEY}/children"
        assert resolve(vertical).view_name == "cms.djangoapps.contentstore:v1:container_vertical"

    def test_the_v1_vertical_address_still_redirects_to_the_v1_children(self):
        reader = UserFactory(is_staff=True)
        vertical = reverse("cms.djangoapps.contentstore:v1:container_vertical", kwargs={"usage_key_string": UNIT_KEY})
        response = _client(reader).get(vertical)
        assert response.status_code == 301
        assert response["Location"] == v1_url(UNIT_KEY)

    def test_v1_keeps_its_own_error_bodies(self):
        response = _client().get(v1_url(self.unit.location))
        assert response.status_code == 401
        assert response.json() == {"detail": "Authentication credentials were not provided."}
        response = _client(UserFactory()).get(v1_url("i4x://InvalidOrg/InvalidCourse/vertical/static/InvalidContent"))
        assert response.status_code == 404

    def test_the_url_name_is_unique_in_its_namespace(self):
        # Imported here because it parses its converters at import time, which needs the URL conf loaded first.
        authoring_urls = import_module("cms.djangoapps.contentstore.rest_api.v2.authoring_urls")
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert names.count("container_child_list") == 1
        assert len(names) == len(set(names))

    def test_unsupported_accept_is_refused_with_406(self):
        # The error library does not catalogue NotAcceptable, so it is typed
        # internal. Change the expected type here once it does.
        response = _client(UserFactory(is_staff=True)).get(
            children_url(self.unit.location), HTTP_ACCEPT="application/xml",
        )
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    def test_options_describes_the_endpoint(self):
        response = _client(UserFactory(is_staff=True)).options(children_url(self.unit.location))
        assert response.status_code == 200
        assert response.json()["name"] == "Course Container Children"

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data("not-a-usage-key", "i4x://edX/ToyX/vertical/unit", "edX/ToyX/Toy_Course/vertical/unit")
    def test_unparsable_or_deprecated_key_is_404(self, key):
        response = _client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/containers/{key}/children/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        address = f"/api/authoring/v2/containers/{self.unit.location}/children"
        response = _client(UserFactory(is_staff=True)).get(address)
        assert response.status_code == 301
        assert response["Location"] == children_url(self.unit.location)
        with pytest.raises(Resolver404):
            resolve(f"/api/authoring/v2/containers/{self.unit.location}/children")


class ContainerChildrenSchemaTest(SharedModuleStoreTestCase):
    """The read as published, generated from the real contentstore and authoring routes."""

    PATH = "/api/authoring/v2/containers/{usage_key}/children/"
    V1 = "/api/contentstore/v1/container/{usage_key_string}/children"
    V1_VERTICAL = "/api/contentstore/v1/container/vertical/{usage_key_string}/children"
    V1_HANDLER = "/api/contentstore/v1/container_handler/{usage_key_string}"

    @staticmethod
    def _ref(node):
        return node["$ref"].rsplit("/", 1)[-1]

    def _response_ref(self, response):
        return self._ref(response["content"]["application/json"]["schema"])

    @staticmethod
    def _nullable(component):
        return {name for name, field in component["properties"].items() if field.get("nullable")}

    @staticmethod
    def _components():
        return _published_schema()["components"]["schemas"]

    def test_the_read_is_published_for_the_sdk(self):
        schema = _published_schema()
        assert list(schema["paths"][self.PATH]) == ["get"]
        operation = schema["paths"][self.PATH]["get"]
        assert operation["operationId"] == "v2_containers_children_list"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert "x-internal" not in operation
        parameters = {p["name"]: p for p in operation["parameters"]}
        assert set(parameters) == {"usage_key", "view", "page", "page_size"}
        assert parameters["usage_key"] == {
            "in": "path",
            "name": "usage_key",
            "schema": {"type": "string", "pattern": "^[^/]+$"},
            "description": (
                "Usage key of a section, subsection, unit or other container of a course, such as "
                "block-v1:edX+DemoX+2026+type@vertical+block@welcome."
            ),
            "required": True,
        }
        assert parameters["view"]["in"] == "query"
        assert not parameters["view"].get("required")
        assert parameters["view"]["schema"] == {"type": "string", "enum": ["full", "minimal"], "default": "minimal"}
        assert [(name, parameters[name]["in"], parameters[name]["schema"]) for name in ("page", "page_size")] == [
            ("page", "query", {"type": "integer"}), ("page_size", "query", {"type": "integer"}),
        ]
        assert set(operation["responses"]) == {"200", "400", "401", "403", "404", "406"}
        assert self._response_ref(operation["responses"]["200"]) == PAGE
        for status in ("400", "401", "403", "404", "406"):
            assert self._response_ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_page_has_all_seven_keys_around_the_two_item_shapes(self):
        components = self._components()
        page = components[PAGE]
        assert set(page["required"]) == set(page["properties"]) == ENVELOPE_KEYS
        item = {"$ref": f"#/components/schemas/{ITEM_SHAPE}"}
        assert page["properties"]["results"] == {"type": "array", "items": item}
        assert self._nullable(page) == {"next", "previous"}
        assert {name: page["properties"][name]["type"] for name in ENVELOPE_KEYS - {"results"}} == {
            "count": "integer", "num_pages": "integer", "current_page": "integer", "start": "integer",
            "next": "string", "previous": "string",
        }
        assert components[ITEM_SHAPE] == {"oneOf": [
            {"$ref": f"#/components/schemas/{SUMMARY_SHAPE}"},
            {"$ref": f"#/components/schemas/{DETAIL_SHAPE}"},
        ]}

    def test_the_two_item_shapes(self):
        components = self._components()
        summary = components[SUMMARY_SHAPE]
        assert set(summary["required"]) == set(summary["properties"]) == SUMMARY_KEYS
        assert self._nullable(summary) == {"display_type"}
        detail = components[DETAIL_SHAPE]
        assert set(detail["required"]) == set(detail["properties"]) == DETAIL_KEYS
        assert self._nullable(detail) == {"upstream_link"}
        link = components["CourseContainerChildUpstreamLink"]
        assert set(link["required"]) == set(link["properties"]) - {"ready_to_sync_children"}
        assert self._nullable(link) == {
            "version_synced", "version_available", "version_declined", "error_message", "top_level_parent_key",
        }
        actions = components["CourseContainerChildActions"]
        assert set(actions["required"]) == set(actions["properties"]) == {
            "can_copy", "can_duplicate", "can_move", "can_manage_access", "can_delete", "can_manage_tags",
        }
        assert {field["type"] for field in actions["properties"].values()} == {"boolean"}
        assert self._nullable(components["CourseContainerChildUpstreamChild"]) == {"name", "upstream"}

    def test_every_field_of_the_read_is_described(self):
        """Every property of every component the read references, however deep, has a description."""
        components = self._components()
        reached, pending = set(), [PAGE]
        while pending:
            name = pending.pop()
            if name not in reached:
                reached.add(name)
                refs = re.findall(r"'\$ref': '([^']+)'", str(components[name]))
                pending.extend(ref.rsplit("/", 1)[-1] for ref in refs)
        assert reached == {
            PAGE, ITEM_SHAPE, SUMMARY_SHAPE, DETAIL_SHAPE, "CourseContainerChildActions", "CourseContainerChildMessage",
            "CourseContainerChildUpstreamLink", "CourseContainerChildUpstreamChild",
        }
        # The generator swaps its own placeholder for the item type inside ``results``, so that
        # one property cannot carry a description of its own.
        undescribed = {
            f"{name}.{field}"
            for name in reached
            for field, spec in components[name].get("properties", {}).items()
            if not spec.get("description")
        }
        assert undescribed == {f"{PAGE}.results"}

    def test_the_examples_are_valid_bodies_of_their_view(self):
        """Each example is one child, which the generator places in a page."""
        operation = _published_schema()["paths"][self.PATH]["get"]
        examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
        assert set(examples) == {"Child", "ChildWithUnitPageData"}
        brief = examples["Child"]["value"]
        assert_page(brief, "default")
        assert [item["has_children"] for item in brief["results"]] == [True]
        full = examples["ChildWithUnitPageData"]["value"]
        assert_page(full, "full")
        assert [item["upstream_link"]["ready_to_sync"] for item in full["results"]] == [True]
        assert full["results"][0]["validation_messages"][0]["type"] == "error"

    def test_the_v1_read_is_deprecated_and_the_replacement_is_not(self):
        paths = _published_schema()["paths"]
        operation = paths[self.V1]["get"]
        assert operation["deprecated"] is True
        assert operation["operationId"] == "v1_container_children_retrieve"
        assert "deprecated" not in paths[self.PATH]["get"]

    def test_other_operations_are_left_as_they_were(self):
        """The hook marks the exact superseded address only."""
        paths = _published_schema()["paths"]
        assert paths[self.V1_HANDLER]["get"]["deprecated"] is True
        assert self.V1_VERTICAL not in paths
        for path in (
            "/api/authoring/v2/containers/{usage_key}/",
            "/api/authoring/v2/courses/{course_key}/textbooks/",
            "/api/contentstore/v0/advanced_settings/{course_id}",
        ):
            assert "deprecated" not in paths[path]["get"], path

    def test_the_names_the_v1_read_published_remain(self):
        components = self._components()
        assert {"ContainerChildren", "ContainerChild", "MessageValidation"} <= set(components)
        assert {
            "CourseContainerChild", "CourseContainerChildFull", "CourseContainerChildRead", PAGE,
        } <= set(components)
