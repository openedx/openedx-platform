"""
Unit tests for the course video usages API.
"""
import json
from unittest.mock import patch

import ddt
import pytest
from crum.signals import current_user_getter, current_user_setter
from django.core.signals import got_request_exception, request_finished, request_started
from django.db import connection
from django.db.models.signals import post_init, pre_init
from django.dispatch import Signal
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.auth.jwt.tests.utils import generate_jwt
from edx_rest_framework_extensions.testing import assert_error_envelope
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v2.views.video_usages import CourseVideoUsageViewSet
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.django import SwitchedSignal
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

DETAIL_URL_NAME = "authoring_v2:course_video_usage_detail"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:video_usage"

MISSING_COURSE_KEY = "course-v1:Org+Missing+Run"
CCX_COURSE_KEY = "ccx-v1:edX+DemoX+Demo_Course+ccx@1"

# Keys the course_key path converter accepts that carry only a content version,
# with no organization, course or run.
VERSION_ONLY_COURSE_KEYS = (
    "course-v1:version@" + "a" * 24,
    "course-v1:branch@published-branch+version@" + "a" * 24,
    "ccx-v1:version@" + "a" * 24 + "+ccx@1",
    "library-v1:version@" + "a" * 24,
)

# Fields that legitimately differ per request and are ignored when diffing.
VOLATILE_FIELDS = {"instance"}

# Every way a successful body of this version may differ from the legacy one,
# as (json_path, reason). The usage body is carried over unchanged, so there are
# none; the parity tests fail on any difference at all.
INTENTIONAL_DIFFERENCES = []

# The members of an error body at each address. The legacy address answers with
# its own error format, this version with the standard envelope.
LEGACY_ERROR_KEYS = {
    401: {"developer_message"},
    403: {"developer_message"},
    404: {"developer_message", "error_code"},
}
ENVELOPE_KEYS = {"type", "title", "status", "detail", "instance"}

# Signals sent while serving any request, whatever it reads: the request
# starting and finishing, model instances being built, and the current user
# being recorded and looked up.
ROUTINE_SIGNALS = (
    request_started,
    request_finished,
    pre_init,
    post_init,
    current_user_setter,
    current_user_getter,
)

# The usage lookup each address calls.
SERVICE_USAGE_LOOKUP = "cms.djangoapps.contentstore.rest_api.v2.video_management_service.get_video_usage_path"
LEGACY_USAGE_LOOKUP = "cms.djangoapps.contentstore.rest_api.v1.views.videos.get_video_usage_path"


def _normalize(obj, path=""):
    """Sort keys, drop volatile fields, and collect a {json_path: value} map for diffing."""
    flat = {}
    if isinstance(obj, dict):
        for key in sorted(obj):
            if key in VOLATILE_FIELDS:
                continue
            flat.update(_normalize(obj[key], f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            flat.update(_normalize(item, f"{path}[{index}]"))
    else:
        flat[path] = obj
    return flat


def _matches(json_path, declared):
    return json_path == declared or json_path.startswith((declared + ".", declared + "["))


def _diff(legacy, new):
    """Return every JSON path that differs, and those of them not declared intentional."""
    flat_legacy, flat_new = _normalize(legacy), _normalize(new)
    changed = {
        key for key in set(flat_legacy) | set(flat_new)
        if flat_legacy.get(key, "<absent>") != flat_new.get(key, "<absent>")
    }
    unexpected = {
        key for key in changed
        if not any(_matches(key, declared) for declared, __ in INTENTIONAL_DIFFERENCES)
    }
    return changed, unexpected


class VideoUsageTestBase(CourseTestCase):
    """
    A course whose content exercises every path of the usage lookup.

    ``single-use`` is placed once, ``triple-use`` three times, ``draft-use`` in a
    unit that was never published, and ``orphan-use`` once in a unit and once in
    a component that has no parent. ``unused`` is referenced by nothing.
    """

    ENABLED_CACHES = ["default"]

    def setUp(self):
        super().setUp()
        chapter = BlockFactory.create(parent=self.course, category="chapter", display_name="Week 1")
        self.subsection = BlockFactory.create(parent=chapter, category="sequential", display_name="Welcome")
        self.placements = {
            "single-use": [self.place_video("Unit A", "Clip A", "single-use")],
            "triple-use": [
                self.place_video(f"Unit T{number}", f"Clip T{number}", "triple-use")
                for number in (1, 2, 3)
            ],
            "orphan-use": [self.place_video("Unit O", "Clip O", "orphan-use")],
            "unused": [],
        }
        # Publishing a unit publishes its whole subsection, so the unpublished
        # unit is added after every published one.
        self.placements["draft-use"] = [self.place_video("Draft unit", "Draft clip", "draft-use", publish=False)]
        self.orphan = self.store.create_item(
            self.user.id,
            self.course.id,
            "video",
            block_id="orphan_clip",
            fields={"edx_video_id": "orphan-use", "display_name": "Orphan clip"},
        )

    def place_video(self, unit_name, video_name, edx_video_id, publish=True):
        """Add a unit holding one video component to the subsection; return both."""
        unit = BlockFactory.create(
            parent=self.subsection, category="vertical", display_name=unit_name, publish_item=publish,
        )
        video = BlockFactory.create(
            parent=unit, category="video", display_name=video_name, edx_video_id=edx_video_id,
            publish_item=publish,
        )
        return unit, video

    def expected_body(self, edx_video_id):
        """The usage body the lookup must produce for ``edx_video_id``."""
        return {
            "usage_locations": [
                {
                    "display_location": f"Welcome - {unit.display_name} / {video.display_name}",
                    "url": f"/container/{unit.location}#{video.location}",
                }
                for unit, video in self.placements[edx_video_id]
            ]
        }

    def url(self, edx_video_id, course_key=None):
        return reverse(DETAIL_URL_NAME, kwargs={
            "course_key": course_key or self.course.id,
            "edx_video_id": edx_video_id,
        })

    def legacy_url(self, edx_video_id, course_key=None):
        return reverse(LEGACY_URL_NAME, kwargs={
            "course_id": course_key or self.course.id,
            "edx_video_id": edx_video_id,
        })

    def client_for(self, user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def user_with_role(self, role_class, course_key=None):
        user = UserFactory.create()
        role_class(course_key or self.course.id).add_users(user)
        return user


@ddt.ddt
class VideoUsageResponseTest(VideoUsageTestBase):
    """The body names every component that plays the video, and nothing else."""

    @ddt.data("single-use", "triple-use", "draft-use", "orphan-use", "unused")
    def test_body_lists_each_placement(self, edx_video_id):
        response = self.client_for(self.user).get(self.url(edx_video_id))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == self.expected_body(edx_video_id)

    def test_a_video_used_three_times_is_listed_three_times_in_content_order(self):
        response = self.client_for(self.user).get(self.url("triple-use"))

        assert [entry["display_location"] for entry in response.json()["usage_locations"]] == [
            "Welcome - Unit T1 / Clip T1",
            "Welcome - Unit T2 / Clip T2",
            "Welcome - Unit T3 / Clip T3",
        ]

    def test_a_component_in_an_unpublished_unit_is_listed(self):
        unit, __ = self.placements["draft-use"][0]
        assert not self.store.has_published_version(unit)

        response = self.client_for(self.user).get(self.url("draft-use"))

        assert response.json()["usage_locations"] == [{
            "display_location": "Welcome - Draft unit / Draft clip",
            "url": f"/container/{unit.location}#{self.placements['draft-use'][0][1].location}",
        }]

    def test_a_component_without_a_parent_is_skipped(self):
        referencing = [
            block.location.block_id
            for block in self.store.get_items(self.course.id, qualifiers={"category": "video"})
            if block.edx_video_id == "orphan-use"
        ]
        assert sorted(referencing) == sorted(["orphan_clip", self.placements["orphan-use"][0][1].location.block_id])

        response = self.client_for(self.user).get(self.url("orphan-use"))

        assert response.json() == self.expected_body("orphan-use")
        assert len(response.json()["usage_locations"]) == 1

    @ddt.data("url", "legacy_url")
    def test_names_the_content_does_not_set_appear_as_their_field_defaults(self, address):
        chapter = BlockFactory.create(parent=self.course, category="chapter", display_name="Week 2")
        subsection = BlockFactory.create(parent=chapter, category="sequential", display_name=None)
        unit = BlockFactory.create(parent=subsection, category="vertical", display_name=None)
        BlockFactory.create(parent=unit, category="video", display_name=None, edx_video_id="unnamed-use")

        response = self.client_for(self.user).get(getattr(self, address)("unnamed-use"))

        assert [entry["display_location"] for entry in response.json()["usage_locations"]] == [
            "None - None / Video",
        ]

    @ddt.data("url", "legacy_url")
    def test_a_unit_in_no_subsection_leaves_the_subsection_part_empty(self, address):
        unit = self.store.create_item(
            self.user.id, self.course.id, "vertical", block_id="loose_unit", fields={"display_name": "Loose unit"},
        )
        BlockFactory.create(parent=unit, category="video", display_name="Loose clip", edx_video_id="loose-use")

        response = self.client_for(self.user).get(getattr(self, address)("loose-use"))

        assert [entry["display_location"] for entry in response.json()["usage_locations"]] == [
            " - Loose unit / Loose clip",
        ]

    def test_an_unreferenced_video_answers_with_an_empty_list(self):
        response = self.client_for(self.user).get(self.url("unused"))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"usage_locations": []}

    def test_head_answers_like_get_without_a_body(self):
        response = self.client_for(self.user).head(self.url("single-use"))

        assert response.status_code == status.HTTP_200_OK
        assert response.content == b""


@ddt.ddt
class VideoUsageParityTest(VideoUsageTestBase):
    """For the same course, video and caller, both addresses answer alike."""

    def pair(self, edx_video_id, user, course_key=None, method="get"):
        client = self.client_for(user)
        call = getattr(client, method)
        return (
            call(self.legacy_url(edx_video_id, course_key)),
            call(self.url(edx_video_id, course_key)),
        )

    def assert_parity(self, legacy, new, expected_status):
        """Assert both answered ``expected_status`` and differ only where declared."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_json = json.loads(legacy.content or b"null")
        new_json = json.loads(new.content or b"null")
        changed, unexpected = _diff(legacy_json, new_json)
        assert not unexpected, f"unexpected differences: {sorted(unexpected)}\nlegacy={legacy_json}\nnew={new_json}"
        for declared, reason in INTENTIONAL_DIFFERENCES:
            assert any(_matches(key, declared) for key in changed), (
                f"declared difference did not occur: {declared} ({reason})"
            )

    @ddt.data("single-use", "triple-use", "draft-use", "orphan-use", "unused")
    def test_success_bodies_are_identical(self, edx_video_id):
        legacy, new = self.pair(edx_video_id, self.user)

        self.assert_parity(legacy, new, status.HTTP_200_OK)
        assert new.json() == self.expected_body(edx_video_id)
        assert new.content == legacy.content

    def test_course_staff_sees_the_same_body_at_both_addresses(self):
        legacy, new = self.pair("triple-use", self.user_with_role(CourseStaffRole))

        self.assert_parity(legacy, new, status.HTTP_200_OK)
        assert new.json() == self.expected_body("triple-use")

    def test_head_answers_alike(self):
        legacy, new = self.pair("single-use", self.user, method="head")

        assert (legacy.status_code, legacy.content) == (status.HTTP_200_OK, b"")
        assert (new.status_code, new.content) == (status.HTTP_200_OK, b"")

    def assert_error_keys(self, legacy, new, expected_status):
        assert legacy.status_code == new.status_code == expected_status
        assert set(legacy.json()) == LEGACY_ERROR_KEYS[expected_status]
        assert set(new.json()) == ENVELOPE_KEYS

    def test_unauthenticated_error_bodies(self):
        legacy, new = self.pair("single-use", None)

        self.assert_error_keys(legacy, new, status.HTTP_401_UNAUTHORIZED)
        assert_error_envelope(new, expected_status=401, expected_type_slug="authn")

    def test_forbidden_error_bodies(self):
        legacy, new = self.pair("single-use", UserFactory.create())

        self.assert_error_keys(legacy, new, status.HTTP_403_FORBIDDEN)
        assert_error_envelope(new, expected_status=403, expected_type_slug="authz")

    def test_missing_course_error_bodies(self):
        legacy, new = self.pair("single-use", self.user, course_key=MISSING_COURSE_KEY)

        self.assert_error_keys(legacy, new, status.HTTP_404_NOT_FOUND)
        assert legacy.json()["error_code"] == "course_does_not_exist"
        assert_error_envelope(new, expected_status=404, expected_type_slug="not-found")


@ddt.ddt
class VideoUsageAuthorizationTest(VideoUsageTestBase):
    """Studio read access to the course is required; everyone else is refused."""

    def assert_allowed(self, user):
        response = self.client_for(user).get(self.url("triple-use"))
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == self.expected_body("triple-use")

    def test_global_staff_is_allowed(self):
        self.assert_allowed(self.user)

    def test_course_staff_is_allowed(self):
        self.assert_allowed(self.user_with_role(CourseStaffRole))

    def test_course_instructor_is_allowed(self):
        self.assert_allowed(self.user_with_role(CourseInstructorRole))

    @ddt.data("get", "head")
    def test_anonymous_is_refused(self, method):
        response = getattr(self.client_for(None), method)(self.url("single-use"))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response["WWW-Authenticate"] == 'JWT realm="api"'
        if method == "get":
            assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    @ddt.data("get", "head")
    def test_user_without_a_role_is_refused(self, method):
        response = getattr(self.client_for(UserFactory.create()), method)(self.url("single-use"))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        if method == "get":
            assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_limited_staff_is_refused(self):
        response = self.client_for(self.user_with_role(CourseLimitedStaffRole)).get(self.url("single-use"))

        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_staff_of_another_course_is_refused(self):
        other_course = CourseFactory.create()
        author = self.user_with_role(CourseStaffRole, course_key=other_course.id)

        response = self.client_for(author).get(self.url("single-use"))

        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_ccx_course_is_refused_even_to_global_staff(self):
        response = self.client_for(self.user).get(self.url("single-use", course_key=CCX_COURSE_KEY))

        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_missing_course_is_refused_to_a_caller_without_access(self):
        response = self.client_for(UserFactory.create()).get(self.url("single-use", course_key=MISSING_COURSE_KEY))

        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_missing_course_is_reported_to_global_staff(self):
        response = self.client_for(self.user).get(self.url("single-use", course_key=MISSING_COURSE_KEY))

        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == "The course does not exist."

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_is_not_found_by_global_staff(self, key):
        response = self.client_for(self.user).get(self.url("single-use", course_key=key))

        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == "Not found."

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_is_not_found_by_a_caller_without_a_role(self, key):
        response = self.client_for(UserFactory.create()).get(self.url("single-use", course_key=key))

        envelope = assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert envelope["detail"] == "Not found."

    @ddt.data(*VERSION_ONLY_COURSE_KEYS)
    def test_a_key_carrying_only_a_version_still_needs_authentication(self, key):
        response = self.client_for(None).get(self.url("single-use", course_key=key))

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_the_legacy_address_answers_404_for_a_version_only_key(self):
        # The legacy pattern needs a separator inside the key, so the address
        # falls through to the course videos listing, which cannot parse the
        # rest of the path as a course key.
        address = f"/api/contentstore/v1/videos/{VERSION_ONLY_COURSE_KEYS[0]}/single-use/usage"

        response = self.client_for(self.user).get(address)

        assert resolve(address).func.view_class.__name__ == "CourseVideosView"
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["error_code"] == "invalid_course_key"

    def test_the_legacy_address_reports_a_missing_course_to_anyone(self):
        response = self.client_for(UserFactory.create()).get(
            self.legacy_url("single-use", course_key=MISSING_COURSE_KEY)
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["error_code"] == "course_does_not_exist"

    def test_the_view_declares_its_checks_in_order(self):
        # The JWT middleware appends its own restricted-application check to
        # views that accept JWTs, so only the declared head of the list is fixed.
        assert [klass.__name__ for klass in CourseVideoUsageViewSet.permission_classes[:3]] == [
            "IsAuthenticated", "HasFullCourseKey", "HasStudioReadAccess",
        ]


class VideoUsageUnexpectedFailureTest(VideoUsageTestBase):
    """
    An unexpected failure behind the lookup, at both addresses.

    The new address answers with the generic 500 body from inside the view, so
    the exception never reaches Django: ``got_request_exception`` is not sent,
    and the only log record is Django's one-line 500 notice, without the
    traceback. The legacy address lets the exception reach Django, which sends
    the signal and logs the traceback. Both sides are pinned, so a change in how
    the error handling reports failures shows up here.
    """

    def request_while_failing(self, address, target):
        """GET ``address`` as global staff while ``target`` raises; return the response, signals and log records."""
        signals = []

        def receiver(sender, **kwargs):
            signals.append(sender)

        got_request_exception.connect(receiver)
        self.addCleanup(got_request_exception.disconnect, receiver)
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.user)
        with patch(target, side_effect=RuntimeError("content store unreachable at mongo-7")):
            with self.assertLogs(level="ERROR") as logs:
                response = client.get(address)
        return response, signals, logs.records

    def test_the_failure_is_answered_with_the_generic_body(self):
        response, __, __ = self.request_while_failing(self.url("single-use"), SERVICE_USAGE_LOOKUP)

        envelope = assert_error_envelope(response, expected_status=500, expected_type_slug="internal")
        assert envelope["detail"] == "An unexpected error occurred. Please try again later."
        assert "mongo-7" not in response.content.decode()

    def test_the_failure_does_not_reach_django(self):
        address = self.url("single-use")

        __, signals, records = self.request_while_failing(address, SERVICE_USAGE_LOOKUP)

        assert not signals
        assert [(record.name, record.getMessage(), record.exc_info) for record in records] == [
            ("django.request", f"Internal Server Error: {address}", None),
        ]

    def test_the_legacy_address_hands_the_failure_to_django(self):
        __, signals, records = self.request_while_failing(self.legacy_url("single-use"), LEGACY_USAGE_LOOKUP)

        assert len(signals) == 1
        assert "django.request" in {
            record.name for record in records if record.exc_info and record.exc_info[0] is RuntimeError
        }


class VideoUsageAuthenticationTest(VideoUsageTestBase):
    """The endpoint uses the platform's default authentication."""

    def test_session_caller_succeeds(self):
        client = APIClient()
        assert client.login(username=self.user.username, password=self.user_password)

        response = client.get(self.url("single-use"))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == self.expected_body("single-use")

    def test_jwt_caller_succeeds(self):
        response = APIClient().get(self.url("single-use"), HTTP_AUTHORIZATION=f"JWT {generate_jwt(self.user)}")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == self.expected_body("single-use")

    def bearer_header(self, user):
        token = AccessTokenFactory(user=user, application=ApplicationFactory()).token
        return f"Bearer {token}"

    def test_bearer_token_is_refused(self):
        response = APIClient().get(self.url("single-use"), HTTP_AUTHORIZATION=self.bearer_header(self.user))

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_the_legacy_address_still_accepts_a_bearer_token(self):
        response = APIClient().get(self.legacy_url("single-use"), HTTP_AUTHORIZATION=self.bearer_header(self.user))

        assert response.status_code == status.HTTP_200_OK

    def deactivated_global_staff(self):
        """Log a global staff member in, then deactivate the account behind the live session."""
        password = "password-12345"
        staff = UserFactory.create(is_staff=True, password=password)
        session_client = APIClient()
        assert session_client.login(username=staff.username, password=password)
        staff.is_active = False
        staff.save()
        return staff, session_client

    def test_inactive_global_staff_is_refused_over_a_session(self):
        __, session_client = self.deactivated_global_staff()

        response = session_client.get(self.url("single-use"))

        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_inactive_global_staff_still_reaches_the_legacy_address_over_a_session(self):
        __, session_client = self.deactivated_global_staff()

        response = session_client.get(self.legacy_url("single-use"))

        assert response.status_code == status.HTTP_200_OK

    def test_inactive_global_staff_is_admitted_over_jwt_at_both_addresses(self):
        staff, __ = self.deactivated_global_staff()
        header = f"JWT {generate_jwt(staff)}"

        new = APIClient().get(self.url("single-use"), HTTP_AUTHORIZATION=header)
        legacy = APIClient().get(self.legacy_url("single-use"), HTTP_AUTHORIZATION=header)

        assert (new.status_code, legacy.status_code) == (status.HTTP_200_OK, status.HTTP_200_OK)
        assert new.json() == self.expected_body("single-use")

    def test_the_viewset_does_not_declare_authentication_classes(self):
        assert "authentication_classes" not in CourseVideoUsageViewSet.__dict__
        assert [klass.__name__ for klass in CourseVideoUsageViewSet.authentication_classes] == [
            "DefaultJwtAuthentication", "DefaultSessionAuthentication",
        ]


@ddt.ddt
class VideoUsageReadOnlyTest(VideoUsageTestBase):
    """
    Reading usages, at either address, writes nothing to the database and sends
    no signal or event beyond the ones every request sends.
    """

    def setUp(self):
        super().setUp()
        # The authorization engine stores a one-row policy cache marker the
        # first time any role is checked against an empty database. That is the
        # engine starting up, not this endpoint, so it is created up front.
        PolicyCacheControl.get_version()

    def session_client(self, role_class=None):
        """Return a client logged in over a session as a new user holding ``role_class``, if any."""
        password = "password-12345"
        user = UserFactory.create(password=password)
        if role_class:
            role_class(self.course.id).add_users(user)
        client = APIClient()
        assert client.login(username=user.username, password=password)
        return client

    def side_effects_during(self, request):
        """
        Run ``request``; return its response, every INSERT, UPDATE or DELETE it
        issued, and every signal it sent beyond ``ROUTINE_SIGNALS``, as
        (signal, sender) pairs.

        Signals are recorded as they are sent, whether or not anything receives
        them, and Open edX events are sent as signals, so an event published with
        no receiver writing to the database is still seen.
        """
        sent = []

        def recording(send):
            def record(signal, *args, **named):
                if signal not in ROUTINE_SIGNALS:
                    sent.append((signal, args[0] if args else named.get("sender")))
                return send(signal, *args, **named)
            return record

        with (
            patch.object(Signal, "send", recording(Signal.send)),
            patch.object(Signal, "send_robust", recording(Signal.send_robust)),
            # The test case switches the content store's signals off, and a
            # switched-off signal returns before it reaches Signal.send.
            patch.object(SwitchedSignal, "send", recording(SwitchedSignal.send)),
            patch.object(SwitchedSignal, "send_robust", recording(SwitchedSignal.send_robust)),
            CaptureQueriesContext(connection) as captured,
        ):
            response = request()
        writes = [
            query["sql"] for query in captured.captured_queries
            if query["sql"].lstrip().split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE")
        ]
        return response, writes, sent

    @ddt.data(
        *[
            (edx_video_id, method, address)
            for edx_video_id in ("single-use", "triple-use", "draft-use", "orphan-use", "unused")
            for method in ("get", "head")
            for address in ("url", "legacy_url")
        ]
    )
    @ddt.unpack
    def test_course_staff_over_a_session_writes_nothing(self, edx_video_id, method, address):
        client = self.session_client(CourseStaffRole)

        response, writes, signals = self.side_effects_during(
            lambda: getattr(client, method)(getattr(self, address)(edx_video_id))
        )

        assert response.status_code == status.HTTP_200_OK
        assert writes == []
        assert not signals

    @ddt.data(
        *[(method, address) for method in ("get", "head") for address in ("url", "legacy_url")]
    )
    @ddt.unpack
    def test_global_staff_writes_nothing(self, method, address):
        client = self.client_for(self.user)

        response, writes, signals = self.side_effects_during(
            lambda: getattr(client, method)(getattr(self, address)("triple-use"))
        )

        assert response.status_code == status.HTTP_200_OK
        assert writes == []
        assert not signals

    @ddt.data("url", "legacy_url")
    def test_a_missing_course_is_not_created_by_looking_it_up(self, address):
        response, writes, signals = self.side_effects_during(
            lambda: self.client_for(self.user).get(getattr(self, address)("single-use", MISSING_COURSE_KEY))
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert writes == []
        assert not signals

    def test_a_refused_caller_writes_nothing(self):
        client = self.session_client()

        response, writes, signals = self.side_effects_during(lambda: client.get(self.url("single-use")))

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert writes == []
        assert not signals


class VideoUsageQueryCountTest(VideoUsageTestBase):
    """
    The new address costs no more queries than the legacy one for the same caller.

    Each error answered by the new address ends with one extra statement, a
    rollback of the request's savepoint, issued by the standard error handling.
    """

    #: Set from the pytest fixture below, which class-based tests cannot request directly.
    django_assert_num_queries = None

    @pytest.fixture(autouse=True)
    def _assert_num_queries(self, django_assert_num_queries):
        self.django_assert_num_queries = django_assert_num_queries

    def measure(self, client, legacy_count, new_count, course_key=None):
        """Warm the per-process caches, then count the queries of each address."""
        for __ in range(2):
            client.get(self.legacy_url("triple-use", course_key))
            client.get(self.url("triple-use", course_key))
        with self.django_assert_num_queries(legacy_count):
            client.get(self.legacy_url("triple-use", course_key))
        with self.django_assert_num_queries(new_count):
            client.get(self.url("triple-use", course_key))

    def test_global_staff(self):
        self.measure(self.client_for(self.user), legacy_count=11, new_count=11)

    def test_global_staff_over_a_session(self):
        self.measure(self.client, legacy_count=13, new_count=13)

    def test_course_staff(self):
        self.measure(self.client_for(self.user_with_role(CourseStaffRole)), legacy_count=11, new_count=11)

    def test_refused_caller(self):
        self.measure(self.client_for(UserFactory.create()), legacy_count=8, new_count=7)

    def test_missing_course(self):
        self.measure(self.client_for(self.user), legacy_count=8, new_count=9, course_key=MISSING_COURSE_KEY)

    def test_anonymous(self):
        self.measure(self.client_for(None), legacy_count=5, new_count=6)


@ddt.ddt
class VideoUsageUrlContractTest(VideoUsageTestBase):
    """The address of a video's usages, and how unusable addresses are answered."""

    def test_conforming_address(self):
        assert self.url("abc-123") == f"/api/authoring/v2/courses/{self.course.id}/video_usages/abc-123/"

    def test_the_address_resolves_to_the_viewset(self):
        match = resolve(self.url("abc-123"))

        assert match.func.cls is CourseVideoUsageViewSet
        assert match.kwargs == {"course_key": self.course.id, "edx_video_id": "abc-123"}

    def test_the_legacy_address_is_unchanged(self):
        assert self.legacy_url("abc-123") == f"/api/contentstore/v1/videos/{self.course.id}/abc-123/usage"
        assert resolve(self.legacy_url("abc-123")).func.view_class.__name__ == "VideoUsageView"

    def assert_json_not_found(self, address):
        response = self.client_for(self.user).get(address, HTTP_ACCEPT="application/json")
        assert response["Content-Type"] == "application/json"
        return assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_malformed_course_key_is_answered_with_the_error_envelope(self):
        self.assert_json_not_found("/api/authoring/v2/courses/not-a-key/video_usages/abc-123/")

    def test_deprecated_course_key_is_answered_with_the_error_envelope(self):
        self.assert_json_not_found("/api/authoring/v2/courses/Org/Course/Run/video_usages/abc-123/")

    def test_a_block_key_in_place_of_a_course_key_is_answered_with_the_error_envelope(self):
        self.assert_json_not_found("/api/authoring/v2/courses/i4x://Org/Course/video/abc/video_usages/abc-123/")

    def test_an_unusable_course_key_is_claimed_by_the_fallback_route(self):
        address = "/api/authoring/v2/courses/Org/Course/Run/video_usages/abc-123/"

        assert resolve(address).func.cls is UnknownRouteView
        assert reverse(
            "authoring_v2:course_video_usage_detail_unmatched",
            kwargs={"course_key": "Org/Course/Run", "subpath": "abc-123"},
        ) == address
        assert reverse(
            "authoring_v2:course_video_usage_list_unmatched", kwargs={"course_key": "Org/Course/Run"},
        ) == "/api/authoring/v2/courses/Org/Course/Run/video_usages/"

    def test_the_fallback_needs_no_authentication(self):
        response = self.client_for(None).get("/api/authoring/v2/courses/not-a-key/video_usages/abc-123/")

        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_the_legacy_address_still_accepts_a_deprecated_course_key(self):
        match = resolve("/api/contentstore/v1/videos/Org/Course/Run/abc-123/usage")

        assert match.func.view_class.__name__ == "VideoUsageView"
        assert match.kwargs == {"course_id": "Org/Course/Run", "edx_video_id": "abc-123"}

    @ddt.data(
        "video_usages/",
        "video_usages//",
        "video_usages/abc-123/extra/",
        "video_usages/abc-123/extra/more/",
    )
    def test_other_addresses_under_the_collection_are_answered_with_the_error_envelope(self, rest):
        address = f"/api/authoring/v2/courses/{self.course.id}/{rest}"

        assert resolve(address).func.cls is UnknownRouteView
        self.assert_json_not_found(address)

    @ddt.data("video_usages", "video_usages/abc-123/extra")
    def test_an_address_under_the_collection_without_the_trailing_slash_is_redirected(self, rest):
        address = f"/api/authoring/v2/courses/{self.course.id}/{rest}"

        response = self.client_for(self.user).get(address, HTTP_ACCEPT="application/json")

        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response["Location"] == f"{address}/"

    def test_the_address_requires_the_trailing_slash(self):
        with pytest.raises(Resolver404):
            resolve(self.url("abc-123").rstrip("/"))

    def test_the_trailing_slash_is_appended_by_redirect(self):
        response = self.client_for(self.user).get(self.url("abc-123").rstrip("/"))

        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response["Location"] == self.url("abc-123")

    def test_an_unsupported_method_is_refused(self):
        response = self.client_for(self.user).post(self.url("single-use"), {}, format="json")

        assert_error_envelope(response, expected_status=405)
        assert response["Allow"] == "GET, HEAD, OPTIONS"

    def test_an_unsatisfiable_accept_header_is_refused(self):
        response = self.client_for(self.user).get(self.url("single-use"), HTTP_ACCEPT="application/zip")

        assert_error_envelope(response, expected_status=406)
