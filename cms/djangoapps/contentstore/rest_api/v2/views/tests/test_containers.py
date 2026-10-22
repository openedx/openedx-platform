"""Tests for the v2 container API."""

import re
from collections import Counter
from functools import cache
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import quote_plus

import ddt
import jsonschema
import pytest
from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.test.utils import CaptureQueriesContext, override_settings
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
from rest_framework.exceptions import NotFound
from rest_framework.test import APIClient
from xblock.core import XBlock
from xblock.utils.studio_editable import NestedXBlockSpec, StudioContainerWithNestedXBlocksMixin

from cms.djangoapps.contentstore.rest_api.v1.views.vertical_block import ContainerHandlerView
from cms.djangoapps.contentstore.rest_api.v2 import authoring_urls
from cms.djangoapps.contentstore.rest_api.v2.views.containers import CourseContainerViewSet
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
from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.contentstore.views.permissions import CanViewCourse
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.xblock_django.models import XBlockStudioConfiguration, XBlockStudioConfigurationFlag
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.signals import IMPORT_COURSE_DETAILS
from openedx.core.djangoapps.content_staging import api as content_staging_api
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.content_tagging.toggles import DISABLE_TAGGING_FEATURE
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.djangoapps.video_config.toggles import PUBLIC_VIDEO_SHARE
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

UNKNOWN_COURSE_UNIT = "block-v1:NoSuchOrg+NoSuchCourse+NoSuchRun+type@vertical+block@unit"
VERSION_GUID = "0123456789abcdef01234567"
LIBRARY_KEYS = (
    "lb:Org:lib:problem:1",                                           # a block of a content library
    "lib-block-v1:Org+Lib+type@problem+block@x",                      # a block of a legacy library
    f"lib-block-v1:version@{VERSION_GUID}+type@problem+block@x",      # a legacy library key naming only a version
)
COURSE_NOT_FOUND = "The requested course does not exist."
CONTAINER_NOT_FOUND = "No container in this course has this key."
FULL_VIEW_NOT_AVAILABLE = "The full view is available only for a unit or a container inside a unit."
INVALID_VIEW = "Must be minimal or full."
VIEWS = ("default", "full")

DEFAULT_SHAPE = "CourseContainer"
FULL_SHAPE = "CourseContainerFull"
READ_SHAPE = "CourseContainerRead"


def container_url(usage_key):
    """Return the address of the container ``usage_key``."""
    return reverse("authoring_v2:container_detail", kwargs={"usage_key": usage_key})


def v1_url(usage_key):
    """Return the deprecated container handler address of ``usage_key``."""
    return reverse("cms.djangoapps.contentstore:v1:container_handler", kwargs={"usage_key_string": str(usage_key)})


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


def assert_matches_one_read_shape(body, shape):
    """Assert ``body`` matches the read response, and of its two shapes exactly ``shape``."""
    assert_matches_published_schema(body, READ_SHAPE)
    assert_matches_published_schema(body, shape)
    other = FULL_SHAPE if shape == DEFAULT_SHAPE else DEFAULT_SHAPE
    with pytest.raises(jsonschema.ValidationError):
        assert_matches_published_schema(body, other)


class NestedContainerBlock(StudioContainerWithNestedXBlocksMixin, XBlock):
    """A container that declares the block types it holds, as some third-party containers do."""

    @property
    def allowed_nested_blocks(self):
        return [
            NestedXBlockSpec(None, category="html", label="HTML", single_instance=True),
            NestedXBlockSpec(None, category="video", label="Video", disabled=True, disabled_reason="Not available"),
        ]


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

def assert_inactive_jwt_reads(user, usage_key, view):
    """Deactivate ``user``; assert a JWT for it reads both views of v2 and the v1 read."""
    user.is_active = False
    user.save()
    header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}
    response = APIClient().get(container_url(usage_key), _params(view), **header)
    assert response.status_code == 200, response.content
    assert_matches_one_read_shape(response.json(), FULL_SHAPE if view == "full" else DEFAULT_SHAPE)
    assert APIClient().get(v1_url(usage_key), **header).status_code == 200
    user.refresh_from_db()
    assert user.is_active is False


class _AccessMatrixMixin:
    """Checks a caller's status on both views, on GET and HEAD, against the expected one and the v1 decision."""

    def assert_access(self, user, view, expected, usage_key=None, v1_expected=None):
        """
        Assert the status, the error envelope when refused, and the v1 status:
        ``v1_expected`` where v1 answers differently, otherwise ``expected``.
        """
        usage_key = usage_key or self.unit.location
        response = _client(user).get(container_url(usage_key), _params(view))
        head = _client(user).head(container_url(usage_key), _params(view))
        v1_status = _client(user).get(v1_url(usage_key)).status_code
        assert response.status_code == expected, response.content
        assert head.status_code == expected
        if expected != 200:
            slug = {401: "authn", 403: "authz", 404: "not-found"}[expected]
            assert_error_envelope(response, expected_status=expected, expected_type_slug=slug)
        else:
            assert_matches_one_read_shape(response.json(), FULL_SHAPE if view == "full" else DEFAULT_SHAPE)
        v1_expected = expected if v1_expected is None else v1_expected
        assert v1_status == v1_expected, f"v1 decided {v1_status} for the same caller"


@ddt.ddt
class ContainerAccessLegacyPermissionsTest(_AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may read a container when policy-based authoring permissions are off."""

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
        response = _client().get(container_url(self.unit.location), _params(view))
        assert response["WWW-Authenticate"] == 'JWT realm="api"'

    @ddt.data(*VIEWS)
    def test_user_without_a_role_is_refused_with_403(self, view):
        self.assert_access(UserFactory(), view, 403)

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        """v1 checks permission first, so it refuses a caller without a role and fails to find the block for staff."""
        self.assert_access(UserFactory(), view, 404, usage_key=UNKNOWN_COURSE_UNIT, v1_expected=403)
        self.assert_access(UserFactory(is_staff=True), view, 404, usage_key=UNKNOWN_COURSE_UNIT, v1_expected=400)

    @ddt.data(*VIEWS)
    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self, view):
        self.assert_access(None, view, 401, usage_key=UNKNOWN_COURSE_UNIT)

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
    def test_a_missing_block_is_404_for_a_reader_and_403_for_anyone_else(self, view):
        """v1 answers a reader with an empty 400."""
        missing = self.unit.location.replace(block_id="missing")
        reader = self._user_with(CourseStaffRole(self.course.id))
        self.assert_access(reader, view, 404, usage_key=missing, v1_expected=400)
        self.assert_access(UserFactory(), view, 403, usage_key=missing)
        assert _client(reader).get(v1_url(missing)).content == b""

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
            assert client.get(container_url(self.unit.location), _params(view)).status_code == 200, "active control"
            user.is_active = False
            user.save()
            response = client.get(container_url(self.unit.location), _params(view))
            assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
            assert client.get(v1_url(self.unit.location)).status_code == v1_expected

    @ddt.data(*VIEWS)
    def test_jwt_caller_may_read(self, view):
        user = self._user_with(CourseStaffRole(self.course.id))
        response = APIClient().get(
            container_url(self.unit.location), _params(view), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(user)}",
        )
        assert response.status_code == 200, response.content

    @ddt.data(*VIEWS)
    def test_an_inactive_jwt_caller_who_is_global_staff_may_read(self, view):
        """A JWT is not checked for an activated account, on v2 as on v1."""
        assert_inactive_jwt_reads(UserFactory(is_staff=True), self.unit.location, view)

    @ddt.data(*VIEWS)
    def test_bearer_token_is_refused(self, view):
        """Bearer tokens are not accepted here, though the v1 read takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        response = APIClient().get(
            container_url(self.unit.location), _params(view), HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert APIClient().get(v1_url(self.unit.location), HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200

    @ddt.data(*VIEWS)
    def test_a_custom_course_is_refused_to_everyone(self, view):
        """
        Studio read access is never granted on a custom course (CCX), global
        staff included. Its key names the course run, so the course is found
        and the caller is refused, on v2 as on v1.
        """
        key = _ccx_key(self.unit.location)
        self.assert_access(UserFactory(is_staff=True), view, 403, usage_key=key)
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), view, 403, usage_key=key)

    @ddt.data("post", "put", "patch", "delete")
    def test_other_methods_are_refused_after_authentication(self, method):
        """Anonymous callers are refused before the method is; signed-in readers learn it is not allowed."""
        url = container_url(self.unit.location)
        anonymous = getattr(_client(), method)(url, {}, format="json")
        assert_error_envelope(anonymous, expected_status=401, expected_type_slug="authn")
        reader = self._user_with(CourseStaffRole(self.course.id))
        refused = getattr(_client(reader), method)(url, {}, format="json")
        # The error library does not catalogue MethodNotAllowed, so it is typed
        # internal. Change the expected type here once it does.
        assert_error_envelope(refused, expected_status=405, expected_type_slug="internal")


@ddt.ddt
class ContainerAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may read a container when policy-based authoring permissions are on."""

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
        self.assert_access(self.unauthorized_user, view, 404, usage_key=UNKNOWN_COURSE_UNIT, v1_expected=403)

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
        self.assert_access(self._user_with(COURSE_AUDITOR.external_key), view, 404, usage_key=missing, v1_expected=400)
        self.assert_access(self.unauthorized_user, view, 403, usage_key=missing)

    @ddt.data(*VIEWS)
    def test_an_inactive_session_with_a_role_is_not_authenticated(self, view):
        """v1 admits it, as the permission policy does not look at whether the account is activated."""
        user = self._user_with(COURSE_AUDITOR.external_key)
        client = _session_client(user)
        assert client.get(container_url(self.unit.location), _params(view)).status_code == 200, "active control"
        user.is_active = False
        user.save()
        response = client.get(container_url(self.unit.location), _params(view))
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert client.get(v1_url(self.unit.location)).status_code == 200

    @ddt.data(*VIEWS)
    def test_an_inactive_jwt_caller_with_a_role_may_read(self, view):
        """A JWT is not checked for an activated account, on v2 as on v1."""
        assert_inactive_jwt_reads(self._user_with(COURSE_AUDITOR.external_key), self.unit.location, view)

    @ddt.data(*VIEWS)
    def test_a_custom_course_is_refused_to_staff_and_course_staff(self, view):
        """
        The permission policy grants staff and superusers every course, but a
        custom course (CCX) is not one of those scopes, and a role on the
        course does not extend to its custom courses: refused on v2 as on v1.
        """
        key = _ccx_key(self.unit.location)
        self.assert_access(self.staff_user, view, 403, usage_key=key)
        self.assert_access(self._user_with(COURSE_STAFF.external_key), view, 403, usage_key=key)

    @ddt.data(*VIEWS)
    def test_staff_and_superuser_without_a_role_are_decided_as_v1_decides(self, view):
        """Only the permission policy is consulted; it admits them, on v2 as on v1."""
        self.assert_access(self.staff_user, view, 200)
        self.assert_access(self.super_user, view, 200)


class CanViewCourseFromAUsageKeyTest(TestCase):
    """The permission class on a route that names a block rather than a course."""

    def test_a_route_with_neither_key_is_refused(self):
        assert CanViewCourse().has_permission(None, SimpleNamespace(kwargs={})) is False

    def test_a_key_outside_any_course_is_not_found_without_a_course_lookup(self):
        with patch.object(CourseOverview, "course_exists") as course_exists:
            for key in LIBRARY_KEYS:
                view = SimpleNamespace(kwargs={"usage_key": UsageKey.from_string(key)})
                with pytest.raises(NotFound) as raised:
                    CanViewCourse().has_permission(None, view)
                assert str(raised.value.detail) == COURSE_NOT_FOUND
        course_exists.assert_not_called()

    def test_a_key_naming_only_a_course_version_is_not_found_without_a_course_lookup(self):
        key = UsageKey.from_string(f"block-v1:version@{VERSION_GUID}+type@vertical+block@x")
        with patch.object(CourseOverview, "course_exists") as course_exists:
            with pytest.raises(NotFound):
                CanViewCourse().has_permission(None, SimpleNamespace(kwargs={"usage_key": key}))
        course_exists.assert_not_called()


# ---------------------------------------------------------------------------
# What is a container
# ---------------------------------------------------------------------------

class ContainerMembershipTest(ModuleStoreTestCase):
    """
    A container is a block other than the course that can hold children and
    that the course reaches. Anything else is answered with 404 without
    naming the block. The unit page data is offered only for a unit or a
    container inside a unit.
    """

    def setUp(self):
        super().setUp()
        course = build_course(sections=1, subsections=1, units=1)
        self.course_key = course.id
        store = modulestore()
        self.course = store.get_course(course.id)
        self.section = self.course.get_children()[0]
        self.subsection = self.section.get_children()[0]
        self.unit = self.subsection.get_children()[0]
        self.problem = self.unit.get_children()[0]
        self.split_in_subsection = _block(self.subsection, "split_test", "Split test in a subsection")
        self.group_in_subsection_split = _block(self.split_in_subsection, "vertical", "Group")
        self.vertical_in_section = _block(self.section, "vertical", "Vertical in a section")
        self.orphan_unit = store.create_item(self.user.id, course.id, "vertical", "orphan_unit")
        self.orphan_section = store.create_item(self.user.id, course.id, "chapter", "orphan_section")
        orphan_subsection = store.create_item(self.user.id, course.id, "sequential", "orphan_subsection")
        self.unit_of_orphan_subsection = _block(orphan_subsection, "vertical", "Unit of an orphan subsection")
        subsection_of_orphan_section = _block(self.orphan_section, "sequential", "Subsection of an orphan section")
        self.unit_of_orphan_section = _block(subsection_of_orphan_section, "vertical", "Unit of an orphan section")
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)
        self.client = _client(self.reader)

    def _assert_not_a_container(self, key, v1_status):
        for params in ({}, {"view": "full"}):
            response = self.client.get(container_url(key), params)
            envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
            assert envelope["detail"] == CONTAINER_NOT_FOUND
            assert envelope["instance"] == container_url(key)
            assert str(key) not in str(_without_instance(envelope))
        assert self.client.get(v1_url(key)).status_code == v1_status

    def _assert_no_unit_page(self, key, v1_status):
        """The default view answers; view=full is refused with 404, as no unit holds the container."""
        default = self.client.get(container_url(key))
        assert default.status_code == 200, default.content
        assert_matches_one_read_shape(default.json(), DEFAULT_SHAPE)
        assert default.json()["unit_id"] is None
        assert default.json()["position"] is None
        assert default.json()["is_unit"] is False
        full = self.client.get(container_url(key), {"view": "full"})
        envelope = assert_error_envelope(full, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == FULL_VIEW_NOT_AVAILABLE
        assert str(key) not in str(_without_instance(envelope))
        assert self.client.get(v1_url(key)).status_code == v1_status

    def test_sections_and_subsections_have_no_unit_page(self):
        """v1 fails on each with a server error."""
        for block in (self.section, self.subsection):
            self._assert_no_unit_page(block.location, 500)

    def test_containers_placed_directly_in_a_section_or_subsection_have_no_unit_page(self):
        """v1 fails on each with a server error."""
        for block in (self.split_in_subsection, self.group_in_subsection_split, self.vertical_in_section):
            self._assert_no_unit_page(block.location, 500)

    def test_the_course_is_not_a_container(self):
        """v1 fails on it with a server error."""
        self._assert_not_a_container(self.course.location, 500)

    def test_a_component_is_not_a_container(self):
        """v1 answers a component's key with the unit page data of the component."""
        self._assert_not_a_container(self.problem.location, 200)

    def test_blocks_the_course_does_not_reach_are_not_containers(self):
        """v1 fails on each with a server error."""
        for block in (self.orphan_section, self.orphan_unit, self.unit_of_orphan_subsection):
            self._assert_not_a_container(block.location, 500)

    def test_a_unit_of_a_section_the_course_does_not_reach_is_not_a_container(self):
        """v1 needs only a section above the unit's subsection, so it answers with the unit page data."""
        self._assert_not_a_container(self.unit_of_orphan_section.location, 200)

    def test_a_missing_block_is_not_a_container(self):
        """v1 answers it with an empty 400."""
        self._assert_not_a_container(self.unit.location.replace(block_id="missing"), 400)

    def test_keys_outside_any_course_are_not_found(self):
        for key in (*LIBRARY_KEYS, f"block-v1:version@{VERSION_GUID}+type@vertical+block@x"):
            for params in ({}, {"view": "full"}):
                response = self.client.get(container_url(key), params)
                envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
                assert envelope["detail"] == COURSE_NOT_FOUND
                assert VERSION_GUID not in str(_without_instance(envelope))
            assert_error_envelope(_client().get(container_url(key)), expected_status=401, expected_type_slug="authn")
            # v1 passes these keys to the modulestore and the permission check
            # unguarded; only that it does not answer with a body is pinned.
            assert self.client.get(v1_url(key)).status_code != 200, key

    def test_a_unit_and_containers_inside_it_have_a_unit_page(self):
        split = _block(self.unit, "split_test", "Split test in a unit")
        group = _block(split, "vertical", "Group")
        library_content = _block(self.unit, "library_content", "Library content in a unit")
        nested_unit = _block(self.unit, "vertical", "Unit in a unit")
        empty_unit = _block(self.subsection, "vertical", "Empty unit")
        for block in (self.unit, split, group, library_content, nested_unit, empty_unit):
            for params, shape in (({}, DEFAULT_SHAPE), ({"view": "full"}, FULL_SHAPE)):
                response = self.client.get(container_url(block.location), params)
                assert response.status_code == 200, (block.location, response.content)
                assert_matches_one_read_shape(response.json(), shape)


# ---------------------------------------------------------------------------
# The default view
# ---------------------------------------------------------------------------

@ddt.ddt
class ContainerDefaultViewTest(ModuleStoreTestCase):
    """The container and the blocks above it."""

    def setUp(self):
        super().setUp()
        course = build_course(sections=2, subsections=2, units=2)
        self.course_key = course.id
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)
        self.client = _client(self.reader)
        self.units = units_of(course.id)

    def _read(self, usage_key, **params):
        response = self.client.get(container_url(usage_key), params)
        assert response.status_code == 200, response.content
        return response.json()

    @staticmethod
    def _node(block):
        return {"id": str(block.location), "category": block.category, "display_name": block.display_name}

    @ddt.data(0, 1, 5, 7)
    def test_a_unit(self, index):
        unit = self.units[index]
        subsection = unit.get_parent()
        section = subsection.get_parent()
        assert self._read(unit.location) == {
            "id": str(unit.location),
            "category": "vertical",
            "display_name": unit.display_name,
            "display_type": "Unit",
            "course_key": str(self.course_key),
            "is_unit": True,
            "unit_id": str(unit.location),
            "position": index % 2 + 1,
            "ancestors": [self._node(section), self._node(subsection)],
        }

    def test_a_section(self):
        section = self.units[4].get_parent().get_parent()
        body = self._read(section.location)
        assert body == {
            "id": str(section.location),
            "category": "chapter",
            "display_name": "Section 1",
            "display_type": "Section",
            "course_key": str(self.course_key),
            "is_unit": False,
            "unit_id": None,
            "position": None,
            "ancestors": [],
        }
        assert_matches_one_read_shape(body, DEFAULT_SHAPE)

    def test_a_subsection(self):
        subsection = self.units[6].get_parent()
        body = self._read(subsection.location)
        assert body == {
            "id": str(subsection.location),
            "category": "sequential",
            "display_name": "Subsection 1.1",
            "display_type": "Subsection",
            "course_key": str(self.course_key),
            "is_unit": False,
            "unit_id": None,
            "position": None,
            "ancestors": [self._node(subsection.get_parent())],
        }
        assert_matches_one_read_shape(body, DEFAULT_SHAPE)

    def test_a_container_two_levels_inside_a_unit(self):
        unit = self.units[3]
        split = _block(unit, "split_test", "Split test")
        group = _block(split, "vertical", "Group")
        subsection = unit.get_parent()
        assert self._read(group.location) == {
            "id": str(group.location),
            "category": "vertical",
            "display_name": "Group",
            "display_type": "Vertical",
            "course_key": str(self.course_key),
            "is_unit": False,
            "unit_id": str(unit.location),
            "position": 2,
            "ancestors": [
                self._node(subsection.get_parent()), self._node(subsection), self._node(unit), self._node(split),
            ],
        }

    def test_bodies_match_the_published_shape(self):
        unit = self.units[0]
        group = _block(_block(unit, "split_test", "Split test"), "vertical", "Group")
        for key in (unit.location, group.location):
            assert_matches_one_read_shape(self._read(key), DEFAULT_SHAPE)

    def test_minimal_view_is_the_default(self):
        key = self.units[0].location
        assert self._read(key, view="minimal") == self._read(key)

    def test_a_blank_view_is_the_default_view(self):
        key = self.units[0].location
        assert self._read(key, view="") == self._read(key)

    def test_unknown_parameters_are_ignored(self):
        """action, a v1 parameter, is not one of this endpoint's parameters."""
        key = self.units[0].location
        assert self._read(key, action="edit", foo="bar") == self._read(key)
        full = self._read(key, view="full", action="edit")
        assert "action" not in full

    def test_an_unknown_view_is_refused_with_400(self):
        response = self.client.get(container_url(self.units[0].location), {"view": "bogus"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"view": [INVALID_VIEW]}
        assert "bogus" not in response.content.decode()

    def test_head_answers_without_a_body(self):
        for params in ({}, {"view": "full"}):
            response = self.client.head(container_url(self.units[0].location), params)
            assert response.status_code == 200
            assert response.content == b""


# ---------------------------------------------------------------------------
# The full view against v1
# ---------------------------------------------------------------------------

# Differences between the v1 read and view=full: (json path pattern, reason).
# The comparison asserts that each occurs and that nothing else differs.
INTENTIONAL_DIFFERENCES = [
    (r"user_clipboard([.\[].*)?", "the clipboard is its own resource at /api/content-staging/v1/clipboard/"),
    (r"action", "v1 echoes its unvalidated action parameter, which v2 does not take"),
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


def test_the_comparison_sees_types_and_empty_containers():
    assert _differences({"a": {"b": []}}, {"a": {}})[0] == {"a", "a.b"}
    assert _differences({"x": True}, {"x": 1})[0] == {"x"}
    assert _differences({"x": 1.0}, {"x": 1})[0] == {"x"}
    assert _differences({"x": {}}, {"x": []})[0] == {"x"}
    assert _differences({"x": None, "y": 1}, {"y": 1})[0] == {"x"}
    assert _differences({"x": [1, {"y": False}]}, {"x": [1, {"y": False}]})[0] == set()


def test_each_declared_difference_must_occur_and_nothing_else_may():
    legacy = {"action": "view", "user_clipboard": {"content": None}, "position": 1}
    _, unexpected, missing = _differences(legacy, {"position": 1})
    assert (unexpected, missing) == (set(), set())
    _, unexpected, missing = _differences(legacy, {"action": "view", "position": 1})
    assert (unexpected, missing) == (set(), {"action"})
    _, unexpected, missing = _differences(legacy, {"position": 2})
    assert (unexpected, missing) == ({"position"}, set())


class _FullViewParityMixin:
    """Compares view=full against the v1 read of a container."""

    def _pair(self, user, usage_key, **params):
        """
        The v1 read and the view=full read of ``usage_key`` by ``user``, as
        JSON. view=full is read first, so nothing the v1 read stores can reach it.
        """
        client = _client(user)
        new = client.get(container_url(usage_key), {"view": "full", **params})
        legacy = client.get(v1_url(usage_key), params)
        assert legacy.status_code == new.status_code == 200, (legacy.content, new.content)
        return legacy.json(), new.json()

    def assert_parity(self, user, usage_key, **params):
        """Assert the two reads differ exactly by the declared differences and return the v2 body."""
        legacy, new = self._pair(user, usage_key, **params)
        _, unexpected, missing = _differences(legacy, new)
        assert unexpected == set(), sorted(unexpected)
        assert missing == set(), sorted(missing)
        assert set(legacy) - set(new) == {"user_clipboard", "action"}
        assert set(new) <= set(legacy)
        assert_matches_one_read_shape(new, FULL_SHAPE)
        return new


@ddt.ddt
class ContainerFullViewParityTest(_FullViewParityMixin, ModuleStoreTestCase):
    """view=full returns what v1 returns for the same caller, except the declared differences."""

    def setUp(self):
        super().setUp()
        course = build_course(sections=2, subsections=2, units=2)
        self.course_key = course.id
        self.units = units_of(course.id)
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)

    @staticmethod
    def _studio_url(block):
        return quote_plus(reverse_usage_url("container_handler", block.location))

    def test_every_unit_links_its_neighbours_across_subsections_and_sections(self):
        """The first unit has no previous unit and the last no next; the others link across boundaries."""
        for index, unit in enumerate(self.units):
            body = self.assert_parity(self.reader, unit.location)
            previous = self.units[index - 1] if index > 0 else None
            following = self.units[index + 1] if index + 1 < len(self.units) else None
            assert body["prev_url"] == (self._studio_url(previous) if previous else None), index
            assert body["next_url"] == (self._studio_url(following) if following else None), index
            assert body["position"] == index % 2 + 1
            assert body["is_unit_page"] is True
            assert body["unit_block_id"] == unit.location.block_id
            assert body["subsection_location"] == str(unit.get_parent().location)
            assert [entry["title"] for entry in body["ancestor_xblocks"]] == [
                unit.get_parent().get_parent().display_name, unit.get_parent().display_name,
            ]
            assert [entry["is_last"] for entry in body["ancestor_xblocks"]] == [False, True]

    def test_containers_inside_a_unit(self):
        """
        A split test, a group in it and a library content block: not unit pages,
        so neither ancestor information nor publishing information is built,
        and a component listed beside an ancestor has no page address.
        """
        unit = self.units[2]
        split = _block(unit, "split_test", "Split test")
        group = _block(split, "vertical", "Group")
        _block(group, "problem", "Problem in a group")
        library_content = _block(unit, "library_content", "Library content")
        unit = modulestore().get_item(unit.location)
        for block in (split, group, library_content):
            body = self.assert_parity(self.reader, block.location)
            assert body["is_unit_page"] is False
            assert body["position"] == 1
            assert body["unit_block_id"] == unit.location.block_id
            assert "ancestor_info" not in body["xblock_info"]
            assert "edited_by" not in body["xblock_info"]
            assert body["is_collapsible"] is body["is_fullwidth_content"] is (block is library_content)
        group_body = self.assert_parity(self.reader, group.location)
        beside_split = group_body["ancestor_xblocks"][-1]["children"]
        assert [child["usage_key"] for child in beside_split] == [str(key) for key in unit.children]
        assert {child["usage_key"]: child["url"] for child in beside_split}[str(unit.children[0])] is None

    @XBlock.register_temp_plugin(NestedContainerBlock, identifier="nested-container-test")
    def test_a_container_that_declares_its_block_types_offers_only_those(self):
        container = _block(self.units[0], "nested-container-test", "Nested container")
        body = self.assert_parity(self.reader, container.location)
        # The block type declares no display name.
        assert body["xblock"]["display_type"] is None
        default = _client(self.reader).get(container_url(container.location)).json()
        assert default["display_type"] is None
        assert_matches_one_read_shape(default, DEFAULT_SHAPE)
        assert body["component_templates"] == [
            {
                "type": "html",
                "templates": [{
                    "display_name": "HTML", "category": "html", "boilerplate_name": None, "hinted": False,
                    "tab": "common", "support_level": True, "single_instance": True,
                }],
                "display_name": "HTML",
                "support_legend": {"show_legend": False},
            },
            {
                "type": "video",
                "templates": [{
                    "display_name": "Video", "category": "video", "boilerplate_name": None, "hinted": False,
                    "tab": "common", "support_level": True, "disabled": True, "disabled_reason": "Not available",
                }],
                "display_name": "Video",
                "support_legend": {"show_legend": False},
            },
        ]

    def test_the_course_component_templates(self):
        body = self.assert_parity(self.reader, self.units[0].location)
        groups = {group["type"]: group for group in body["component_templates"]}
        assert {"html", "video", "problem", "advanced"} <= set(groups)
        assert "beta" not in groups["advanced"]
        assert groups["html"]["beta"] is False
        assert set(groups["html"]["support_legend"]) == {"show_legend", "allow_unsupported_xblocks", "documentation_label"}
        assert {template["support_level"] for group in groups.values() for template in group["templates"]} == {True}

    def test_configured_support_levels_are_returned_as_stored(self):
        XBlockStudioConfigurationFlag.objects.create(enabled=True)
        XBlockStudioConfiguration.objects.create(name="html", enabled=True, support_level="fs")
        XBlockStudioConfiguration.objects.create(name="video", enabled=True, support_level="ps")
        body = self.assert_parity(self.reader, self.units[0].location)
        levels = {
            group["type"]: {template["support_level"] for template in group["templates"]}
            for group in body["component_templates"]
        }
        assert levels["html"] == {"fs"}
        assert levels["video"] == {"ps"}
        assert all(group["support_legend"]["show_legend"] is True for group in body["component_templates"])

    def test_library_content_picker_follows_the_new_libraries(self):
        key = self.units[0].location
        assert self.assert_parity(self.reader, key)["library_content_picker_url"] is None
        with override_settings(MEILISEARCH_ENABLED=True):
            picker = self.assert_parity(self.reader, key)["library_content_picker_url"]
        assert picker.endswith("/component-picker?variant=published")

    @ddt.data(False, True)
    def test_unit_tags_follow_the_tagging_feature(self, tagging_disabled):
        taxonomy = tagging_api.create_taxonomy(name="Topics", export_id="topics")
        tagging_api.set_taxonomy_orgs(taxonomy, all_orgs=True)
        tagging_api.add_tag_to_taxonomy(taxonomy, "Algebra")
        unit = self.units[0]
        tagging_api.tag_object(str(unit.location), taxonomy, ["Algebra"])
        with override_waffle_flag(DISABLE_TAGGING_FEATURE, active=tagging_disabled):
            body = self.assert_parity(self.reader, unit.location)
        assert body["show_unit_tags"] is not tagging_disabled
        if tagging_disabled:
            assert "tags" not in body["xblock_info"]
        else:
            assert body["xblock_info"]["tags"]["count"] == 1
            assert [t["value"] for t in body["xblock_info"]["tags"]["taxonomies"]] == ["Topics"]

    @ddt.data(False, True)
    def test_video_sharing_keys_follow_public_video_sharing(self, enabled):
        with patch.object(PUBLIC_VIDEO_SHARE, "is_enabled", return_value=enabled):
            body = self.assert_parity(self.reader, self.units[0].location)
        sharing = {"video_sharing_enabled", "video_sharing_options", "video_sharing_doc_url"}
        assert (sharing & set(body["xblock_info"])) == (sharing if enabled else set())
        if enabled:
            assert body["xblock_info"]["video_sharing_enabled"] is True

    def test_no_neighbour_across_a_section_without_units(self):
        """
        Sections A and C hold one unit each; section B between them holds only
        an empty subsection. Neither unit has a neighbour to go to across B.
        """
        course = CourseFactory.create()
        unit_a = _block(_block(_block(course, "chapter", "A"), "sequential", "A1"), "vertical", "Unit A")
        _block(_block(course, "chapter", "B"), "sequential", "B1, without units")
        unit_c = _block(_block(_block(course, "chapter", "C"), "sequential", "C1"), "vertical", "Unit C")
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        body_a = self.assert_parity(reader, unit_a.location)
        body_c = self.assert_parity(reader, unit_c.location)
        assert (body_a["prev_url"], body_a["next_url"]) == (None, None)
        assert (body_c["prev_url"], body_c["next_url"]) == (None, None)

    def test_a_key_pinned_to_a_course_version_reads_as_on_v1(self):
        version = modulestore().get_course(self.course_key).course_version
        pinned_course = self.course_key.for_branch(ModuleStoreEnum.BranchName.draft).replace(version_guid=version)
        key = self.units[1].location.map_into_course(pinned_course)
        assert key.version_guid == version
        body = self.assert_parity(UserFactory(is_staff=True), key)
        assert body["unit_block_id"] == self.units[1].location.block_id

    def test_a_child_that_cannot_be_loaded_is_not_counted_in_the_position(self):
        """
        A subsection lists a child that no longer exists between its two
        units. The second unit is third in the list but second among the
        children that load, which is the position the unit page shows.
        """
        store = modulestore()
        unit = self.units[1]
        subsection = store.get_item(unit.get_parent().location)
        subsection.children.insert(1, self.course_key.make_usage_key("vertical", "dangling"))
        store.update_item(subsection, self.user.id)
        assert store.get_item(subsection.location).children.index(unit.location) == 2
        body = self.assert_parity(self.reader, unit.location)
        assert body["position"] == 2
        default = _client(self.reader).get(container_url(unit.location)).json()
        assert default["position"] == 2

    def test_preview_links(self):
        unit = self.units[0]
        body = self.assert_parity(self.reader, unit.location)
        assert body["published_preview_link"].endswith(f"/courses/{self.course_key}/jump_to/{unit.location}")
        assert body["draft_preview_link"] == f"{body['published_preview_link']}?preview=1"

    @override_settings(LMS_ROOT_URL=None)
    def test_preview_links_are_null_without_an_lms_address(self):
        body = self.assert_parity(self.reader, self.units[0].location)
        assert body["draft_preview_link"] is None
        assert body["published_preview_link"] is None

    def test_a_key_pinned_to_the_draft_branch_reads_as_on_v1(self):
        key = self.units[1].location.for_branch(ModuleStoreEnum.BranchName.draft)
        body = self.assert_parity(UserFactory(is_staff=True), key)
        assert body["unit_block_id"] == self.units[1].location.block_id

    def test_the_comparison_fails_when_a_field_changes(self):
        """The harness itself: an undeclared change in view=full is reported."""
        service = "cms.djangoapps.contentstore.rest_api.v2.container_service"
        with patch(f"{service}.get_lms_link_for_item", return_value="https://changed.example.com/"):
            legacy, new = self._pair(self.reader, self.units[0].location)
        _, unexpected, missing = _differences(legacy, new)
        assert unexpected == {"draft_preview_link", "published_preview_link"}
        assert missing == set()


# The top-level keys of view=full xblock_info, by block role, with tagging on
# and every other configuration-dependent key off. They come from the shared
# create_xblock_info builder; v2 publishes them as documented in
# CourseContainerFullSerializer.xblock_info.
CONTAINER_XBLOCK_INFO_KEYS = frozenset({
    "actions", "ancestor_has_staff_lock", "category", "course_authoring_url", "course_graders", "display_name",
    "due", "due_date", "edited_on", "edited_on_raw", "embed_lms_url", "enable_hide_from_toc_ui",
    "explanatory_message", "format", "graded", "group_access", "has_changes", "has_children",
    "has_explicit_staff_lock", "hide_from_toc", "id", "is_tagging_feature_disabled", "lms_url", "published",
    "published_on", "relative_weeks_due", "release_date", "released_to_students", "show_correctness", "start",
    "studio_url", "taxonomy_tags_widget_url", "upstream_info", "user_partition_info", "user_partitions",
    "visibility_state", "xblock_type",
})
UNIT_XBLOCK_INFO_KEYS = CONTAINER_XBLOCK_INFO_KEYS | {
    "ancestor_info", "currently_visible_to_students", "discussion_enabled", "edited_by", "enable_copy_paste_units",
    "has_partition_group_components", "published_by", "staff_lock_from", "tags",
}
TAGGING_KEYS = frozenset({"course_authoring_url", "taxonomy_tags_widget_url", "tags"})
CONDITIONAL_KEYS = frozenset({
    "release_date_from", "summary_configuration_enabled", "video_sharing_doc_url", "video_sharing_enabled",
    "video_sharing_options", "child_info", "ancestors",
})


@ddt.ddt
class ContainerFullViewXBlockInfoKeysTest(ModuleStoreTestCase):
    """The keys view=full xblock_info carries are the published contract."""

    def setUp(self):
        super().setUp()
        course = build_course(sections=1, subsections=1, units=2)
        self.unit = units_of(course.id)[0]
        self.split = _block(self.unit, "split_test", "Split test")
        self.group = _block(self.split, "vertical", "Group")
        self.reader = UserFactory()
        CourseStaffRole(course.id).add_users(self.reader)

    def _xblock_info(self, block):
        response = _client(self.reader).get(container_url(block.location), {"view": "full"})
        assert response.status_code == 200, response.content
        return response.json()["xblock_info"]

    @ddt.data(False, True)
    def test_every_role_carries_exactly_the_documented_keys(self, tagging_disabled):
        removed = TAGGING_KEYS if tagging_disabled else set()
        with override_waffle_flag(DISABLE_TAGGING_FEATURE, active=tagging_disabled):
            for role, block, expected in (
                ("unit", self.unit, UNIT_XBLOCK_INFO_KEYS),
                ("container in a unit", self.split, CONTAINER_XBLOCK_INFO_KEYS),
                ("container two levels inside a unit", self.group, CONTAINER_XBLOCK_INFO_KEYS),
            ):
                info = self._xblock_info(block)
                keys = set(info) - {"release_date_from"}
                assert keys == expected - removed, (
                    f"The keys of a {role} in view=full xblock_info changed: added {sorted(keys - expected)}, "
                    f"removed {sorted((expected - removed) - keys)}. They are built by the shared "
                    "create_xblock_info and are part of the published v2 container contract. A removed or "
                    "renamed key breaks v2 callers and needs a new version; an added key must be documented in "
                    "CourseContainerFullSerializer.xblock_info and listed here."
                )
                if role == "unit":
                    assert ("release_date_from" in info) is (info["release_date"] is not None)
                else:
                    assert "release_date_from" not in info

    def test_a_units_ancestor_info_lists_its_subsection_with_its_units(self):
        info = self._xblock_info(self.unit)
        assert list(info["ancestor_info"]) == ["ancestors"]
        ancestors = info["ancestor_info"]["ancestors"]
        assert [ancestor["category"] for ancestor in ancestors] == ["sequential", "chapter", "course"]
        subsection = self.unit.get_parent()
        assert [child["id"] for child in ancestors[0]["child_info"]["children"]] == [
            str(key) for key in subsection.children
        ]
        assert all("child_info" not in ancestor for ancestor in ancestors[1:])

    def test_the_documented_keys_are_named_in_the_published_description(self):
        description = _published_schema()["components"]["schemas"][FULL_SHAPE]["properties"]["xblock_info"][
            "description"
        ]
        named = set(re.findall(r"[a-z]+(?:_[a-z]+)+|\b[a-z]{2,}\b", description))
        assert (UNIT_XBLOCK_INFO_KEYS | CONDITIONAL_KEYS) - named == set()


# ---------------------------------------------------------------------------
# Reads write nothing
# ---------------------------------------------------------------------------

class _ReadIsSideEffectFreeMixin:
    """
    Every read (both views, GET and HEAD, of a unit, a container inside it, a
    subsection and a section) by a reader whose clipboard holds a block of a
    course without an overview record writes no row and no Mongo document,
    stores nothing through the modulestore, saves no model and sends no Django
    signal or openedx-event. The v1 read of the same unit builds that course's
    overview, which announces the import.

    The one exception: the reads start on a database that has never served a
    role or permission lookup, and the first lookup stores the permission
    library's policy version row (``PolicyCacheControl``), sending that
    model's pre_save and post_save. Nothing else is written or sent.
    """

    def reader_for(self, course):
        """Return a user who may read ``course``."""
        raise NotImplementedError

    def _reader_with_a_clipboard_from_a_course_without_an_overview(self, course):
        reader = self.reader_for(course)
        source = build_course(sections=1, subsections=1, units=1)
        content_staging_api.save_xblock_to_user_clipboard(units_of(source.id)[0], reader.id)
        CourseOverview.objects.filter(id=source.id).delete()
        PolicyCacheControl.objects.all().delete()
        return reader, source

    def _record_imports(self):
        imported = []

        def on_import(sender, **kwargs):  # pylint: disable=unused-argument
            imported.append(kwargs["courserun_key"])

        IMPORT_COURSE_DETAILS.connect(on_import, weak=False)
        self.addCleanup(IMPORT_COURSE_DETAILS.disconnect, on_import)
        return imported

    def _assert_reads_have_no_side_effects(self, **course_kwargs):
        """Make every read of a course built with ``course_kwargs``; return the unit's full view."""
        course = build_course(sections=1, subsections=1, units=2, **course_kwargs)
        unit = units_of(course.id)[0]
        group = _block(_block(unit, "split_test", "Split test"), "vertical", "Group")
        subsection = unit.get_parent()
        section = subsection.get_parent()
        reader, source = self._reader_with_a_clipboard_from_a_course_without_an_overview(course)
        client = _client(reader)
        imported = self._record_imports()
        full = {"view": "full"}
        reads = [
            ("get", unit, {}), ("get", unit, full), ("head", unit, {}), ("head", unit, full),
            ("get", group, {}), ("get", group, full), ("head", group, full),
            ("get", subsection, {}), ("get", section, {}), ("head", section, {}),
        ]
        assert not PolicyCacheControl.objects.exists()
        for index, (method, block, params) in enumerate(reads):
            # The signal recorder is outermost, so callbacks run on commit as
            # the effects recorder exits are still seen.
            with SignalRecorder() as signals, record_effects(self) as effects:
                response = getattr(client, method)(container_url(block.location), params)
            first = index == 0
            request = (index, method, block.location.block_type, params)
            assert response.status_code == 200, (request, response.content)
            # The permission library's policy version row, stored on the first
            # lookup, is the only write and the only signal sender allowed.
            assert effects["sql_writes"] == (FIRST_LOOKUP_WRITES if first else []), request
            assert effects["saves"] == (["PolicyCacheControl"] if first else []), request
            assert signals.unexpected() == ([("send", pre_save), ("send", post_save)] if first else []), request
            assert effects["mongo_writes"] == 0, request
            assert effects["update_item"] == [], request
            assert effects["xblock_updated"] == [], request
            assert effects["published"] == [], request
        assert imported == []
        assert not CourseOverview.objects.filter(id=source.id).exists()
        return client.get(container_url(unit.location), full).json()

    def test_reads_have_no_side_effects(self):
        self._assert_reads_have_no_side_effects()

    @override_settings(ENABLE_SPECIAL_EXAMS=True)
    def test_reads_of_a_course_with_prerequisites_and_special_exams_have_no_side_effects(self):
        """The unit page looks up the subsection's prerequisites and exam settings."""
        body = self._assert_reads_have_no_side_effects(enable_subsection_gating=True, enable_proctored_exams=True)
        subsection, _, course = body["xblock_info"]["ancestor_info"]["ancestors"]
        assert {"is_prereq", "prereqs", "prereq", "is_proctored_exam", "supports_onboarding"} <= set(subsection)
        assert course["enable_proctored_exams"] is True

    def test_v1_still_builds_the_clipboard_overview(self):
        course = build_course(sections=1, subsections=1, units=1)
        unit = units_of(course.id)[0]
        reader, source = self._reader_with_a_clipboard_from_a_course_without_an_overview(course)
        imported = self._record_imports()
        with record_effects(self) as effects:
            response = _client(reader).get(v1_url(unit.location))
        assert response.status_code == 200, response.content
        assert response.json()["user_clipboard"]["source_usage_key"] == str(units_of(source.id)[0].location)
        assert ("INSERT", CourseOverview._meta.db_table) in effects["sql_writes"]  # pylint: disable=protected-access
        assert imported == [str(source.id)]
        assert CourseOverview.objects.filter(id=source.id).exists()


class ContainerReadSideEffectsLegacyPermissionsTest(_ReadIsSideEffectFreeMixin, ModuleStoreTestCase):
    """
    Reads by course staff with policy-based authoring permissions off. Course
    staff rather than global staff, whose access is decided without a role lookup.
    """

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class ContainerReadSideEffectsAuthzTest(
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


def _is_course_existence_lookup(sql):
    return sql.lstrip().upper().startswith("SELECT") and CourseOverview._meta.db_table in sql  # pylint: disable=protected-access


# (sections, subsections per section, units per subsection)
COURSE_SHAPES = {
    "one unit": (1, 1, 1),
    "ten units in the subsection": (1, 1, 10),
    "five sections": (5, 1, 1),
}


class _QueryCountMixin:
    """
    Request costs of the last unit of each course shape, against the v1 read of
    it. The course has an overview record, as every published course does.
    """

    def reader_for(self, course):
        raise NotImplementedError

    def costs(self, shape):
        """``{read: (sql statements, mongo reads, mongo writes)}`` for v1, view=full and the default view."""
        course = build_course(*shape)
        CourseOverview.get_from_id(course.id)
        client = _client(self.reader_for(course))
        key = units_of(course.id)[-1].location
        return {
            "v1": _measure(lambda: client.get(v1_url(key))),
            "full": _measure(lambda: client.get(container_url(key), {"view": "full"})),
            "default": _measure(lambda: client.get(container_url(key))),
        }

    def test_full_view_costs_no_more_than_v1(self):
        for shape in COURSE_SHAPES.values():
            costs = self.costs(shape)
            # view=full adds the course existence lookup and leaves out v1's clipboard query, so the
            # statements are compared rather than their count.
            extra = Counter(map(_statement_shape, costs["full"][0])) - Counter(map(_statement_shape, costs["v1"][0]))
            assert sum(extra.values()) <= 1, (shape, extra)
            assert all(_is_course_existence_lookup(sql) for sql in extra), (shape, extra)
            assert costs["full"][1] <= costs["v1"][1], (shape, costs)
            assert costs["v1"][2] == costs["full"][2] == costs["default"][2] == 0, (shape, costs)

    def test_the_default_view_costs_the_same_on_every_course_shape(self):
        default = {}
        for name, shape in COURSE_SHAPES.items():
            statements, reads, writes = self.costs(shape)["default"]
            default[name] = (len(statements), reads, writes)
        assert len(set(default.values())) == 1, default


class ContainerQueryCountTest(_QueryCountMixin, ModuleStoreTestCase):
    """Request costs with policy-based authoring permissions off, read by course staff."""

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class ContainerQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
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
class ContainerRoutingTest(SharedModuleStoreTestCase):
    """The address, the deprecated v1 address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = build_course(sections=1, subsections=1, units=1)
        cls.unit = units_of(cls.course.id)[0]

    def test_the_address(self):
        assert container_url(UNIT_KEY) == f"/api/authoring/v2/containers/{UNIT_KEY}/"
        match = resolve(container_url(UNIT_KEY))
        assert match.func.cls is CourseContainerViewSet
        assert routed_actions(match.func) == {"get": "retrieve"}
        assert match.kwargs == {"usage_key": UsageKey.from_string(UNIT_KEY)}

    def test_the_v1_address_is_unchanged(self):
        assert v1_url(UNIT_KEY) == f"/api/contentstore/v1/container_handler/{UNIT_KEY}"
        assert resolve(v1_url(UNIT_KEY)).func.cls is ContainerHandlerView

    def test_the_url_name_is_unique_in_its_namespace(self):
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert names.count("container_detail") == 1
        assert len(names) == len(set(names))

    def test_unsupported_accept_is_refused_with_406(self):
        # The error library does not catalogue NotAcceptable, so it is typed
        # internal. Change the expected type here once it does.
        response = _client(UserFactory(is_staff=True)).get(
            container_url(self.unit.location), HTTP_ACCEPT="application/xml",
        )
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    def test_options_describes_the_endpoint(self):
        response = _client(UserFactory(is_staff=True)).options(container_url(self.unit.location))
        assert response.status_code == 200
        assert response.json()["name"] == "Course Container"

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data("not-a-usage-key", "i4x://edX/ToyX/vertical/unit", "edX/ToyX/Toy_Course/vertical/unit")
    def test_unparsable_or_deprecated_key_is_404(self, key):
        response = _client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/containers/{key}/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        response = _client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/containers/{self.unit.location}")
        assert response.status_code == 301
        assert response["Location"] == container_url(self.unit.location)
        with pytest.raises(Resolver404):
            resolve(f"/api/authoring/v2/containers/{self.unit.location}")

    def test_v1_keeps_its_own_error_body(self):
        response = _client().get(v1_url(self.unit.location))
        assert response.status_code == 401
        assert response.json() == {"detail": "Authentication credentials were not provided."}


class ContainerSchemaTest(SharedModuleStoreTestCase):
    """The container read as published, generated from the real contentstore and authoring routes."""

    PATH = "/api/authoring/v2/containers/{usage_key}/"
    V1 = "/api/contentstore/v1/container_handler/{usage_key_string}"
    V1_CHILDREN = "/api/contentstore/v1/container/{usage_key_string}/children"

    @staticmethod
    def _ref(response):
        return response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    @staticmethod
    def _nullable(component):
        return {name for name, field in component["properties"].items() if field.get("nullable")}

    def test_the_read_is_published_for_the_sdk(self):
        schema = _published_schema()
        assert list(schema["paths"][self.PATH]) == ["get"]
        operation = schema["paths"][self.PATH]["get"]
        assert operation["operationId"] == "v2_containers_retrieve"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert "x-internal" not in operation
        parameters = {p["name"]: p for p in operation["parameters"]}
        assert set(parameters) == {"usage_key", "view"}
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
        assert set(operation["responses"]) == {"200", "400", "401", "403", "404", "406"}
        assert self._ref(operation["responses"]["200"]) == READ_SHAPE
        for status in ("400", "401", "403", "404", "406"):
            assert self._ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_read_offers_the_default_and_the_full_shape(self):
        components = _published_schema()["components"]["schemas"]
        assert components[READ_SHAPE] == {"oneOf": [
            {"$ref": f"#/components/schemas/{DEFAULT_SHAPE}"},
            {"$ref": f"#/components/schemas/{FULL_SHAPE}"},
        ]}
        default = components[DEFAULT_SHAPE]
        assert set(default["required"]) == set(default["properties"]) == {
            "id", "category", "display_name", "display_type", "course_key", "is_unit", "unit_id", "position",
            "ancestors",
        }
        assert self._nullable(default) == {"display_type", "unit_id", "position"}
        assert self._nullable(components["CourseContainerXBlock"]) == {"display_type"}
        full = components[FULL_SHAPE]
        assert set(full["required"]) == set(full["properties"])
        assert len(full["properties"]) == 21
        assert self._nullable(full) == {
            "prev_url", "next_url", "draft_preview_link", "published_preview_link", "library_content_picker_url",
        }
        assert {"user_clipboard", "action"} & set(full["properties"]) == set()
        assert self._nullable(components["CourseContainerAncestorChild"]) == {"url"}
        assert set(components["CourseContainerComponentTemplate"]["required"]) == {
            "display_name", "category", "boilerplate_name", "hinted", "tab", "support_level",
        }
        assert set(components["CourseContainerComponentGroup"]["required"]) == {
            "type", "templates", "display_name", "support_legend",
        }
        assert components["CourseContainerSupportLegend"]["required"] == ["show_legend"]

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
            READ_SHAPE, DEFAULT_SHAPE, FULL_SHAPE, "CourseContainerAncestor", "CourseContainerXBlock",
            "CourseContainerAncestorXBlock", "CourseContainerAncestorChild", "CourseContainerComponentGroup",
            "CourseContainerComponentTemplate", "CourseContainerSupportLegend",
        }
        undescribed = {
            f"{name}.{field}"
            for name in reached
            for field, spec in components[name].get("properties", {}).items()
            if not spec.get("description")
        }
        assert undescribed == set()

    def test_the_examples_are_valid_bodies_of_their_view(self):
        operation = _published_schema()["paths"][self.PATH]["get"]
        examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
        assert set(examples) == {"Container", "Subsection", "UnitPageData"}
        assert_matches_one_read_shape(examples["Container"]["value"], DEFAULT_SHAPE)
        subsection = examples["Subsection"]["value"]
        assert_matches_one_read_shape(subsection, DEFAULT_SHAPE)
        assert (subsection["unit_id"], subsection["position"]) == (None, None)
        page = examples["UnitPageData"]["value"]
        assert_matches_one_read_shape(page, FULL_SHAPE)
        assert set(page["xblock_info"]) < UNIT_XBLOCK_INFO_KEYS

    def test_the_v1_read_is_deprecated_and_the_children_read_is_not(self):
        paths = _published_schema()["paths"]
        operation = paths[self.V1]["get"]
        assert operation["deprecated"] is True
        assert operation["operationId"] == "v1_container_handler_retrieve"
        assert "deprecated" not in paths[self.V1_CHILDREN]["get"]
