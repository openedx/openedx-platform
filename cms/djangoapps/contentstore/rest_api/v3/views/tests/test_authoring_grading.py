"""Tests for the v3 course grading API."""

import json
from contextlib import contextmanager
from datetime import timedelta
from functools import cache
from importlib import import_module
from unittest import TestCase
from unittest.mock import patch

import ddt
import jsonschema
import pymongo
import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import include, path, resolve, reverse
from drf_spectacular.generators import SchemaGenerator
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_authz.constants.roles import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_STAFF,
)
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v3.serializers.grading import CourseGradingSerializer
from cms.djangoapps.contentstore.signals.signals import GRADING_POLICY_CHANGED
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.toggles import LEGACY_STUDIO_EXAM_SETTINGS
from cms.djangoapps.models.settings.course_grading import GRADING_POLICY_CHANGED_EVENT_TYPE, CourseGradingModel
from cms.djangoapps.models.settings.waffle import MATERIAL_RECOMPUTE_ONLY_FLAG
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.credit.tests.factories import CreditCourseFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

LEGACY = "legacy"
CONFORMING = "conforming"
MOUNTS = (LEGACY, CONFORMING)
UNKNOWN_COURSE_KEY = "course-v1:NoSuchOrg+NoSuchCourse+NoSuchRun"
CREDIT_TASK = "openedx.core.djangoapps.credit.tasks.update_credit_course_requirements.delay"
UPDATE_ITEM = "xmodule.modulestore.mixed.MixedModuleStore.update_item"

# Stored on every seeded course. 0.29 does not survive the percentage round trip
# (it is shown as 28), so it reveals any write-back of graders nobody sent.
SEED_GRADERS = [
    {"type": "Homework", "min_count": 3, "drop_count": 1, "short_label": "HW", "weight": 0.29},
    {"type": "Lab", "min_count": 2, "drop_count": 0, "short_label": "Lab", "weight": 0.07},
    {"type": "Exam", "min_count": 1, "drop_count": 0, "short_label": None, "weight": 0.64},
]
SEED_CUTOFFS = {"A": 0.9, "B": 0.8, "Pass": 0.5}
SEED_GRACE = timedelta(hours=2, minutes=5)
SEED_CREDIT = 0.8

GRADERS = [
    {"type": "Homework", "min_count": 3, "drop_count": 1, "short_label": "HW", "weight": 28, "id": 0},
    {"type": "Lab", "min_count": 2, "drop_count": 0, "short_label": "Lab", "weight": 7, "id": 1},
    {"type": "Exam", "min_count": 1, "drop_count": 0, "short_label": "", "weight": 64, "id": 2},
]
# The seeded graders exactly as stored: sending them changes no weight or count.
SEED_GRADERS_AS_SENT = [
    {"type": "Homework", "min_count": 3, "drop_count": 1, "short_label": "HW", "weight": 29},
    {"type": "Lab", "min_count": 2, "drop_count": 0, "short_label": "Lab", "weight": 7},
    {"type": "Exam", "min_count": 1, "drop_count": 0, "short_label": None, "weight": 64},
]
CUTOFFS = {"A": 0.85, "B": 0.7, "Pass": 0.45}


def _full(**overrides):
    """A whole-policy body, with ``overrides`` replacing its fields."""
    body = {
        "graders": GRADERS,
        "grade_cutoffs": CUTOFFS,
        "grace_period": {"hours": 3, "minutes": 15},
        "minimum_grade_credit": 0.75,
    }
    body.update(overrides)
    return body


# Whole-policy bodies the grading model accepts, picked by hand. Each must produce
# the same response bytes, stored policy, events and credit task as the
# whole-policy update.
HAND_PICKED_FULL_BODIES = {
    "authoring_app": _full(),
    "end_to_end_post": {
        "graders": [{"type": "Homework", "min_count": 2, "drop_count": 0, "short_label": "HW", "weight": 100}],
        "grade_cutoffs": {"Pass": 0.6},
        "grace_period": {"hours": 0, "minutes": 30},
        "minimum_grade_credit": 0.8,
        "is_credit_course": False,
    },
    "documented_example_with_credit_flag": {
        "graders": [{"type": "Homework", "min_count": 1, "drop_count": 0, "short_label": "", "weight": 100, "id": 0}],
        "grade_cutoffs": {"A": 0.75, "B": 0.63, "C": 0.57, "D": 0.5},
        "grace_period": {"hours": 12, "minutes": 0},
        "minimum_grade_credit": 0.7,
        "is_credit_course": True,
    },
    "graders_without_id": _full(graders=[{k: v for k, v in g.items() if k != "id"} for g in GRADERS]),
    "numbers_as_strings": _full(graders=[
        {**g, "min_count": "1", "drop_count": "0", "weight": str(g["weight"])} for g in GRADERS
    ]),
    "grace_period_null": _full(grace_period=None),
    "minimum_grade_credit_null": _full(minimum_grade_credit=None),
    "grace_period_with_seconds": _full(grace_period={"hours": 1, "minutes": 2, "seconds": 3}),
    "grace_period_with_days": _full(grace_period={"days": 1, "hours": 2}),
    "grace_period_wrapped": _full(grace_period={"grace_period": {"hours": 2}}),
    "grace_period_empty": _full(grace_period={}),
    "grace_period_fractional": _full(grace_period={"hours": 1.5}),
    "displayed_weights_resent": _full(graders=[{**GRADERS[0], "weight": 28}, GRADERS[1], GRADERS[2]]),
    "stored_policy_resent": _full(graders=SEED_GRADERS_AS_SENT, grade_cutoffs=SEED_CUTOFFS),
    "unknown_key": _full(foo="bar"),
    "graders_empty_list": _full(graders=[]),
    "graders_empty_object": _full(graders={}),
    "graders_empty_string": _full(graders=""),
    "grader_type_null": _full(graders=[{**GRADERS[0], "type": None}]),
    "grader_type_number": _full(graders=[{**GRADERS[0], "type": 123}]),
    "grader_count_fractional": _full(graders=[{**GRADERS[0], "min_count": 1.5}]),
    "grader_count_boolean": _full(graders=[{**GRADERS[0], "min_count": True}]),
    "grader_weight_boolean": _full(graders=[{**GRADERS[0], "weight": True}]),
    "grader_label_number": _full(graders=[{**GRADERS[0], "short_label": 5}]),
    "grader_extra_key": _full(graders=[{**GRADERS[0], "extra": 1}]),
    "cutoffs_null": _full(grade_cutoffs=None),
    "cutoffs_empty": _full(grade_cutoffs={}),
    "cutoff_as_string": _full(grade_cutoffs={"A": "0.5"}),
    "credit_as_string": _full(minimum_grade_credit="0.5"),
    "credit_as_integer": _full(minimum_grade_credit=1),
}

# Every position of a whole-policy body was sent each of these values, plus 10 ** 30.
# WHOLE_POLICY_VALUES lists, per position, the ones the whole-policy update stores
# and answers 200 for; every other combination fails inside the grading model.
_SCALARS = (True, False, 0, 1, -1, 0.5)
_ANY_JSON = (
    None, *_SCALARS, 1e308, "", "x", "1", "0.5", "inf", "nan", "1" * 30, [], ["a"], [1], {}, {"x": 1},
)
GRACE_PERIOD_UNITS = ("hours", "minutes", "seconds", "days", "weeks", "milliseconds", "microseconds")
WHOLE_POLICY_VALUES = {
    "graders": ([], {}, ""),
    "grader.type": _ANY_JSON,
    "grader.short_label": _ANY_JSON,
    "grader.id": (*_ANY_JSON, 10 ** 30),
    "grader.min_count": (*_SCALARS, "1"),
    "grader.drop_count": (*_SCALARS, "1"),
    "grader.weight": (*_SCALARS, 1e308, 10 ** 30, "1", "0.5", "1" * 30),
    "grade_cutoffs": (None, {}, {"x": 1}),
    "cutoff": (None, *_SCALARS, 1e308, "1", "0.5", "1" * 30),
    "grace_period": (None, {}),
    "grace_period.unit": _SCALARS,
    "minimum_grade_credit": (None, *_SCALARS, 1e308, "", "1", "0.5", "1" * 30),
    "unknown_field": (*_ANY_JSON, 10 ** 30),
}


def _whole_policy_bodies():
    """One whole-policy body per position and accepted value in WHOLE_POLICY_VALUES."""
    bodies = {}
    for position, values in WHOLE_POLICY_VALUES.items():
        for value in values:
            name = f"{position}={value!r}"
            if position == "graders":
                bodies[name] = _full(graders=value)
            elif position.startswith("grader."):
                field = position.split(".")[1]
                bodies[name] = _full(graders=[{**GRADERS[0], field: value}, *GRADERS[1:]])
            elif position == "cutoff":
                bodies[name] = _full(grade_cutoffs={"A": value, "Pass": 0.45})
                bodies[f"only_{name}"] = _full(grade_cutoffs={"A": value})
            elif position == "grace_period.unit":
                for unit in GRACE_PERIOD_UNITS:
                    bodies[f"grace_period.{unit}={value!r}"] = _full(grace_period={unit: value})
            elif position == "unknown_field":
                bodies[name] = _full(unknown=value)
            else:
                bodies[name] = _full(**{position: value})
    for field in ("min_count", "drop_count", "short_label", "weight", "id"):
        bodies[f"grader_without_{field}"] = _full(
            graders=[{key: value for key, value in GRADERS[0].items() if key != field}],
        )
    bodies["cutoff_named_with_empty_string"] = _full(grade_cutoffs={"": 0.5})
    return bodies


GENERATED_FULL_BODIES = _whole_policy_bodies()
FULL_BODIES = {**HAND_PICKED_FULL_BODIES, **GENERATED_FULL_BODIES}

# The whole-policy bodies replayed per mount. Both mounts route to one view, so
# each generated body is replayed on one of them, alternating; the hand-picked
# bodies are replayed on both.
FULL_BODY_CASES = [
    *((mount, name, body) for mount in MOUNTS for name, body in HAND_PICKED_FULL_BODIES.items()),
    *((MOUNTS[index % len(MOUNTS)], name, body) for index, (name, body) in enumerate(GENERATED_FULL_BODIES.items())),
]

# Bodies that make the grading model raise. Each is refused with a 400 naming the
# field, before anything is written.
REJECTED_BODIES = {
    "graders_null": ({"graders": None}, "graders"),
    "graders_not_a_list": ({"graders": 5}, "graders"),
    "graders_a_string": ({"graders": "x"}, "graders"),
    "grader_not_an_object": (_full(graders=["Homework"]), "graders"),
    "grader_without_type": ({"graders": [{"min_count": 1, "weight": 100}]}, "graders"),
    "grader_count_not_a_number": ({"graders": [{**GRADERS[0], "min_count": "many"}]}, "graders"),
    "grader_count_fractional_string": (_full(graders=[{**GRADERS[0], "min_count": "1.5"}]), "graders"),
    "grader_count_null": (_full(graders=[{**GRADERS[0], "min_count": None}]), "graders"),
    "grader_drop_count_not_a_number": (_full(graders=[{**GRADERS[0], "drop_count": "x"}]), "graders"),
    "grader_weight_not_a_number": (_full(graders=[{**GRADERS[0], "weight": "abc"}]), "graders"),
    "grader_weight_null": (_full(graders=[{**GRADERS[0], "weight": None}]), "graders"),
    "grader_weight_infinite": (_full(graders=[{**GRADERS[0], "weight": "inf"}]), "graders"),
    "cutoffs_a_string": (_full(grade_cutoffs="x"), "grade_cutoffs"),
    "cutoffs_a_list": (_full(grade_cutoffs=[]), "grade_cutoffs"),
    "cutoff_not_a_number": ({"grade_cutoffs": {"A": "x"}}, "grade_cutoffs"),
    "cutoff_an_object": (_full(grade_cutoffs={"A": {"x": 1}}), "grade_cutoffs"),
    "grace_period_unknown_unit": (_full(grace_period={"foo": 1}), "grace_period"),
    "grace_period_string_value": (_full(grace_period={"hours": "1"}), "grace_period"),
    "grace_period_a_string": (_full(grace_period="x"), "grace_period"),
    "grace_period_a_list": (_full(grace_period=[]), "grace_period"),
    "grace_period_too_long": (_full(grace_period={"days": 10 ** 10}), "grace_period"),
    "credit_not_a_number": (_full(minimum_grade_credit="x"), "minimum_grade_credit"),
    "credit_a_list": (_full(minimum_grade_credit=[1]), "minimum_grade_credit"),
    "body_a_list": ([1, 2], "non_field_errors"),
    # A body without every policy field is held to the documented shape: a grader
    # type is a string, and the cutoffs are at least one JSON number.
    "partial_cutoffs_null": ({"grade_cutoffs": None}, "grade_cutoffs"),
    "partial_cutoffs_empty": ({"grade_cutoffs": {}}, "grade_cutoffs"),
    "partial_cutoff_null": ({"grade_cutoffs": {"A": None, "Pass": 0.5}}, "grade_cutoffs"),
    "partial_only_cutoff_null": ({"grade_cutoffs": {"A": None}}, "grade_cutoffs"),
    "partial_cutoff_a_numeric_string": ({"grade_cutoffs": {"A": "0.5"}}, "grade_cutoffs"),
    "partial_cutoff_a_boolean": ({"grade_cutoffs": {"A": True}}, "grade_cutoffs"),
    "partial_graders_and_cutoffs_null": ({"graders": GRADERS, "grade_cutoffs": None}, "grade_cutoffs"),
    "partial_three_fields_cutoffs_empty": (
        {"graders": GRADERS, "grade_cutoffs": {}, "grace_period": {"hours": 1}}, "grade_cutoffs",
    ),
    "partial_grader_type_null": ({"graders": [{**GRADERS[0], "type": None}]}, "graders"),
    "partial_grader_type_a_number": ({"graders": [{**GRADERS[0], "type": 123}]}, "graders"),
    "partial_grader_type_a_list": ({"graders": [{**GRADERS[0], "type": ["a"]}]}, "graders"),
    "partial_grader_type_an_object": ({"graders": [{**GRADERS[0], "type": {}}]}, "graders"),
    "partial_credit_empty_string": ({"minimum_grade_credit": ""}, "minimum_grade_credit"),
}

# Bodies holding an integer wider than the 8 bytes the modulestore can store, at a
# position where the grading model stores it. The whole-policy update fails part-way
# through its write on these and leaves the modulestore unable to read the course for
# the rest of the process, so they are not replayed against it here.
TOO_LARGE_BODIES = {
    "grader_count_huge": (_full(graders=[{**GRADERS[0], "min_count": 10 ** 30}]), "graders"),
    "grader_count_huge_float": (_full(graders=[{**GRADERS[0], "min_count": 1e308}]), "graders"),
    "grader_count_huge_string": (_full(graders=[{**GRADERS[0], "min_count": "1" * 30}]), "graders"),
    "grader_drop_count_huge": (_full(graders=[{**GRADERS[0], "drop_count": 10 ** 30}]), "graders"),
    "grader_drop_count_huge_float": (_full(graders=[{**GRADERS[0], "drop_count": 1e308}]), "graders"),
    "grader_drop_count_huge_string": (_full(graders=[{**GRADERS[0], "drop_count": "1" * 30}]), "graders"),
    "grader_type_huge": (_full(graders=[{**GRADERS[0], "type": 10 ** 30}]), "graders"),
    "grader_label_huge": (_full(graders=[{**GRADERS[0], "short_label": 10 ** 30}]), "graders"),
    "grader_label_list_holding_huge": (_full(graders=[{**GRADERS[0], "short_label": [10 ** 30]}]), "graders"),
    "cutoff_huge": (_full(grade_cutoffs={"A": 10 ** 30, "Pass": 0.45}), "grade_cutoffs"),
    "credit_huge": (_full(minimum_grade_credit=10 ** 30), "minimum_grade_credit"),
    "partial_grader_count_huge": ({"graders": [{**GRADERS[0], "min_count": 10 ** 30}]}, "graders"),
    "partial_cutoff_huge": ({"grade_cutoffs": {"A": 10 ** 30}}, "grade_cutoffs"),
    "partial_credit_huge": ({"minimum_grade_credit": 10 ** 30}, "minimum_grade_credit"),
}


def grading_url(mount, course_key):
    """Return the address of the grading resource of ``course_key`` on ``mount``."""
    if mount == LEGACY:
        return reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail", kwargs={"course_key": str(course_key)},
        )
    return reverse("authoring_v3:course_grading", kwargs={"course_key": str(course_key)})


def v0_url(course_key):
    return reverse("cms.djangoapps.contentstore:v0:cms_api_update_grading", kwargs={"course_id": str(course_key)})


def v1_url(course_key):
    return reverse("cms.djangoapps.contentstore:v1:course_grading", kwargs={"course_id": str(course_key)})


def _client(user=None):
    """An API client that returns server errors as responses, authenticated as ``user`` if given."""
    client = APIClient()
    client.raise_request_exception = False
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _patch(client, url, body):
    return client.patch(url, data=json.dumps(body), content_type="application/json")


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


def seed_course(user, **kwargs):
    """Create a course holding the seeded grading policy."""
    course = CourseFactory.create(**kwargs)
    course.raw_grader = [dict(grader) for grader in SEED_GRADERS]
    course.grade_cutoffs = dict(SEED_CUTOFFS)
    course.graceperiod = SEED_GRACE
    course.minimum_grade_credit = SEED_CREDIT
    modulestore().update_item(course, user.id)
    return course


def stored_policy(course_key):
    """The grading fields as the modulestore holds them, weights as raw fractions."""
    course = modulestore().get_course(course_key)
    return {
        "raw_grader": course.raw_grader,
        "grade_cutoffs": course.grade_cutoffs,
        "graceperiod": course.graceperiod,
        "minimum_grade_credit": course.minimum_grade_credit,
    }


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
def _published_components():
    """The schema components the grading routes publish, generated from the real URLconfs."""
    # Importing the service URLconf registers the course key converter the authoring routes use.
    import_module(settings.ROOT_URLCONF)
    patterns = [
        path("api/contentstore/", include("cms.djangoapps.contentstore.rest_api.urls")),
        path("api/authoring/v3/", include("cms.djangoapps.contentstore.rest_api.v3.authoring_urls")),
    ]
    schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    return _as_json_schema(schema["components"]["schemas"])


DEFAULT_SHAPE = "authoring_grading.CourseGrading.v3"
FULL_SHAPE = "authoring_grading.CourseGradingFull.v3"
READ_SHAPE = "authoring_grading.CourseGradingRead.v3"


def assert_matches_published_schema(body, component):
    """Assert ``body`` is valid against the published schema component ``component``."""
    jsonschema.validate(
        body, {"$ref": f"#/components/schemas/{component}", "components": {"schemas": _published_components()}},
    )


@contextmanager
def capture_grading_effects():
    """Record grading-policy signals, grading-policy tracking events and credit tasks."""
    effects = {"signals": 0, "events": 0, "credit_tasks": []}

    def receiver(sender, **kwargs):  # pylint: disable=unused-argument
        effects["signals"] += 1

    def emit(name, *args, **kwargs):  # pylint: disable=unused-argument
        if name == GRADING_POLICY_CHANGED_EVENT_TYPE:
            effects["events"] += 1

    GRADING_POLICY_CHANGED.connect(receiver, weak=False)
    try:
        with patch("eventtracking.tracker.emit", side_effect=emit), patch(CREDIT_TASK) as credit:
            yield effects
            effects["credit_tasks"] = [call.args for call in credit.call_args_list]
    finally:
        GRADING_POLICY_CHANGED.disconnect(receiver)


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


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

OPERATIONS = ("get", "get_full", "patch_full", "patch_partial")
CALLS = [(mount, operation) for mount in MOUNTS for operation in OPERATIONS]


class _AccessMatrixMixin:
    """
    Calls every operation on both mounts and checks the status against the
    expected one and against the decision v1 makes for the same caller.
    """

    def _call(self, user, mount, operation, course_key):
        """Call ``operation`` on ``mount`` as ``user``."""
        client = _client(user)
        url = grading_url(mount, course_key)
        if operation == "get":
            return client.get(url)
        if operation == "get_full":
            return client.get(url, {"view": "full"})
        if operation == "patch_full":
            return _patch(client, url, _full())
        return _patch(client, url, {"grade_cutoffs": CUTOFFS})

    def _v1_status(self, user, operation, course_key):
        client = _client(user)
        if operation.startswith("get"):
            return client.get(v1_url(course_key)).status_code
        return _post(client, v1_url(course_key), _full()).status_code

    def assert_access(self, user, mount, operation, expected, course_key=None):
        """Assert the status, the error envelope when refused, and agreement with v1."""
        course_key = course_key or self.course.id
        with patch(CREDIT_TASK):
            response = self._call(user, mount, operation, course_key)
            v1_status = self._v1_status(user, operation, course_key)
        assert response.status_code == expected, response.content
        if expected != 200:
            slug = {401: "authn", 403: "authz", 404: "not-found"}[expected]
            assert_error_envelope(response, expected_status=expected, expected_type_slug=slug)
        assert v1_status == expected, f"v1 decided {v1_status} for the same caller"


@ddt.ddt
class GradingAccessLegacyPermissionsTest(_AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may read and change the policy when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_anonymous_caller_is_refused_with_401(self, mount, operation):
        self.assert_access(None, mount, operation, 401)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_user_without_a_role_is_refused_with_403(self, mount, operation):
        self.assert_access(UserFactory(), mount, operation, 403)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_unknown_course_is_404_before_permission_is_checked(self, mount, operation):
        self.assert_access(UserFactory(), mount, operation, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_course_staff_may_read_and_change(self, mount, operation):
        self.assert_access(self._user_with(CourseStaffRole(self.course.id)), mount, operation, 200)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_course_instructor_may_read_and_change(self, mount, operation):
        self.assert_access(self._user_with(CourseInstructorRole(self.course.id)), mount, operation, 200)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_org_staff_may_read_and_change(self, mount, operation):
        self.assert_access(self._user_with(OrgStaffRole(self.course.id.org)), mount, operation, 200)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_global_staff_may_read_and_change(self, mount, operation):
        self.assert_access(UserFactory(is_staff=True), mount, operation, 200)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_superuser_may_read_and_change(self, mount, operation):
        self.assert_access(UserFactory(is_superuser=True), mount, operation, 200)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_limited_staff_is_refused(self, mount, operation):
        self.assert_access(self._user_with(CourseLimitedStaffRole(self.course.id)), mount, operation, 403)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_staff_of_another_course_is_refused(self, mount, operation):
        self.assert_access(self._user_with(CourseStaffRole(self.other_course.id)), mount, operation, 403)

    @ddt.data(*MOUNTS)
    def test_inactive_user_with_a_session_may_read_and_change(self, mount):
        """Session callers whose account is not activated are admitted."""
        user = UserFactory(is_staff=True, is_active=False, password="password")
        client = APIClient()
        assert client.login(username=user.username, password="password")
        assert client.get(grading_url(mount, self.course.id)).status_code == 200
        response = _patch(client, grading_url(mount, self.course.id), {"grade_cutoffs": CUTOFFS})
        assert response.status_code == 200

    @ddt.data(*MOUNTS)
    def test_jwt_caller_may_read(self, mount):
        user = UserFactory(is_staff=True)
        response = APIClient().get(
            grading_url(mount, self.course.id), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(user)}",
        )
        assert response.status_code == 200

    @ddt.data(*MOUNTS)
    def test_bearer_token_is_refused(self, mount):
        """Bearer tokens are not accepted here, though the v1 read takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        response = APIClient().get(grading_url(mount, self.course.id), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert APIClient().get(v1_url(self.course.id), HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200


@ddt.ddt
class GradingAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMatrixMixin, SharedModuleStoreTestCase):
    """Who may read and change the policy when policy-based authoring permissions are on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, self.course.id)
        return user

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_anonymous_caller_is_refused_with_401(self, mount, operation):
        self.assert_access(None, mount, operation, 401)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_user_without_a_role_is_refused_with_403(self, mount, operation):
        self.assert_access(self.unauthorized_user, mount, operation, 403)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_unknown_course_is_404_before_permission_is_checked(self, mount, operation):
        self.assert_access(self.unauthorized_user, mount, operation, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_auditor_may_read_but_not_change(self, mount, operation):
        expected = 200 if operation.startswith("get") else 403
        self.assert_access(self._user_with(COURSE_AUDITOR.external_key), mount, operation, expected)

    @ddt.data(*[(mount, operation, role) for mount, operation in CALLS for role in (
        COURSE_STAFF.external_key, COURSE_EDITOR.external_key, COURSE_ADMIN.external_key,
    )])
    @ddt.unpack
    def test_staff_editor_and_admin_may_read_and_change(self, mount, operation, role):
        self.assert_access(self._user_with(role), mount, operation, 200)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_data_researcher_is_refused(self, mount, operation):
        self.assert_access(self._user_with(COURSE_DATA_RESEARCHER.external_key), mount, operation, 403)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_legacy_course_staff_without_a_policy_role_is_refused(self, mount, operation):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_access(user, mount, operation, 403)

    @ddt.data(*CALLS)
    @ddt.unpack
    def test_superuser_may_read_and_change(self, mount, operation):
        self.assert_access(self.super_user, mount, operation, 200)


# ---------------------------------------------------------------------------
# Reading the policy
# ---------------------------------------------------------------------------

def _flatten_v1(v1_body, grace_seconds):
    """The v1 read with its policy lifted to the top level and the grace period's seconds added."""
    flat = {key: value for key, value in v1_body.items() if key != "course_details"}
    flat.update(v1_body["course_details"])
    if flat["grace_period"] is not None:
        flat["grace_period"] = {**flat["grace_period"], "seconds": grace_seconds}
    return flat


@ddt.ddt
class GradingReadTest(ModuleStoreTestCase):
    """The default and full representations."""

    def setUp(self):
        super().setUp()
        self.staff = UserFactory(is_staff=True)
        self.client = _client(self.staff)
        self.course = seed_course(self.staff)
        for name, assignment_type in (("One", "Homework"), ("Two", "Exam"), ("Three", "Homework")):
            section = BlockFactory.create(parent=self.course, category="chapter", display_name=f"Section {name}")
            BlockFactory.create(
                parent=section, category="sequential", display_name=f"Sub {name}", format=assignment_type, graded=True,
            )

    def _read(self, mount, **params):
        response = self.client.get(grading_url(mount, self.course.id), params)
        assert response.status_code == 200, response.content
        return response.json()

    @ddt.data(*MOUNTS)
    def test_default_view_is_the_stored_policy(self, mount):
        expected = CourseGradingSerializer(CourseGradingModel.fetch(self.course.id)).data
        assert self._read(mount) == expected
        assert self._read(mount) == {
            "graders": [
                {"type": "Homework", "min_count": 3, "drop_count": 1, "short_label": "HW", "weight": 28, "id": 0},
                {"type": "Lab", "min_count": 2, "drop_count": 0, "short_label": "Lab", "weight": 7, "id": 1},
                {"type": "Exam", "min_count": 1, "drop_count": 0, "short_label": None, "weight": 64, "id": 2},
            ],
            "grade_cutoffs": SEED_CUTOFFS,
            "grace_period": {"hours": 2, "minutes": 5, "seconds": 0},
            "minimum_grade_credit": SEED_CREDIT,
        }

    @ddt.data(*MOUNTS)
    def test_default_view_matches_what_an_update_returns(self, mount):
        with patch(CREDIT_TASK):
            updated = _patch(self.client, grading_url(mount, self.course.id), {}).json()
        assert self._read(mount) == updated

    @ddt.data(*MOUNTS)
    def test_full_view_equals_the_v1_read_flattened(self, mount):
        v1_body = self.client.get(v1_url(self.course.id)).json()
        full = self._read(mount, view="full")
        assert full == _flatten_v1(v1_body, grace_seconds=0)
        assert full["course_assignment_lists"] == {
            "Homework": ["Section One - Sub One", "Section Three - Sub Three"],
            "Exam": ["Section Two - Sub Two"],
        }

    @ddt.data(*[(mount, enabled) for mount in MOUNTS for enabled in (True, False)])
    @ddt.unpack
    def test_full_view_credit_context_equals_v1(self, mount, eligibility_enabled):
        CreditCourseFactory(course_key=self.course.id, enabled=True)
        with override_settings(ENABLE_CREDIT_ELIGIBILITY=eligibility_enabled):
            v1_body = self.client.get(v1_url(self.course.id)).json()
            full = self._read(mount, view="full")
        assert full == _flatten_v1(v1_body, grace_seconds=0)
        assert full["is_credit_course"] is True
        assert full["show_credit_eligibility"] is eligibility_enabled

    @ddt.data(*[(mount, legacy_exam_settings) for mount in MOUNTS for legacy_exam_settings in (True, False)])
    @ddt.unpack
    def test_full_view_proctoring_link_equals_v1(self, mount, legacy_exam_settings):
        with override_settings(COURSE_AUTHORING_MICROFRONTEND_URL="http://authoring.example"), \
                override_waffle_flag(LEGACY_STUDIO_EXAM_SETTINGS, active=legacy_exam_settings):
            v1_body = self.client.get(v1_url(self.course.id)).json()
            full = self._read(mount, view="full")
        assert full == _flatten_v1(v1_body, grace_seconds=0)
        expected = "" if legacy_exam_settings else f"/course/{self.course.id}/pages-and-resources/proctoring/settings"
        assert full["mfe_proctored_exam_settings_url"] == expected

    @ddt.data(*MOUNTS)
    def test_grace_period_seconds_are_shown_where_v1_drops_them(self, mount):
        self.course.graceperiod = timedelta(hours=1, seconds=30)
        modulestore().update_item(self.course, self.staff.id)
        v1_body = self.client.get(v1_url(self.course.id)).json()
        full = self._read(mount, view="full")
        assert v1_body["course_details"]["grace_period"] == {"hours": 1, "minutes": 0}
        assert full["grace_period"] == {"hours": 1, "minutes": 0, "seconds": 30}
        assert full == _flatten_v1(v1_body, grace_seconds=30)
        assert self._read(mount)["grace_period"] == {"hours": 1, "minutes": 0, "seconds": 30}

    @ddt.data(*MOUNTS)
    def test_full_view_without_a_grace_period_equals_v1(self, mount):
        course = CourseFactory.create()
        v1_body = self.client.get(v1_url(course.id)).json()
        full = self.client.get(grading_url(mount, course.id), {"view": "full"}).json()
        assert full["grace_period"] is None
        assert full == _flatten_v1(v1_body, grace_seconds=None)

    @ddt.data(*[(mount, value) for mount in MOUNTS for value in ("minimal", "bogus", "FULL")])
    @ddt.unpack
    def test_unknown_view_is_refused_with_400(self, mount, value):
        response = self.client.get(grading_url(mount, self.course.id), {"view": value})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert list(envelope["errors"]) == ["view"]

    @ddt.data(*MOUNTS)
    def test_empty_view_is_the_default_view(self, mount):
        assert self._read(mount, view="") == self._read(mount)

    @ddt.data(*MOUNTS)
    def test_reads_match_the_published_shapes(self, mount):
        """
        Including policies stored with each value the schema marks nullable, and a
        course never configured. The read publishes both shapes; a full response
        also matches the default one.
        """
        nullable_values = _full(
            graders=[{**GRADERS[0], "type": None, "short_label": None}],
            grade_cutoffs={"A": None, "Pass": 0.5},
            grace_period=None,
            minimum_grade_credit="",
        )
        cases = (
            (nullable_values, {
                "graders": [{**GRADERS[0], "type": None, "short_label": None}],
                "grade_cutoffs": {"A": None, "Pass": 0.5},
                "grace_period": {"hours": 2, "minutes": 5, "seconds": 0},
                "minimum_grade_credit": None,
            }),
            (_full(grade_cutoffs=None), {
                "graders": GRADERS,
                "grade_cutoffs": None,
                "grace_period": {"hours": 3, "minutes": 15, "seconds": 0},
                "minimum_grade_credit": 0.75,
            }),
        )
        for body, expected in cases:
            course = seed_course(self.staff)
            with patch(CREDIT_TASK):
                assert _patch(self.client, grading_url(mount, course.id), body).status_code == 200
            default = self.client.get(grading_url(mount, course.id)).json()
            full = self.client.get(grading_url(mount, course.id), {"view": "full"}).json()
            assert default == expected
            assert {key: full[key] for key in default} == expected
            assert_matches_published_schema(default, DEFAULT_SHAPE)
            assert_matches_published_schema(full, FULL_SHAPE)
            assert_matches_published_schema(default, READ_SHAPE)
            assert_matches_published_schema(full, READ_SHAPE)

        never_configured = CourseFactory.create()
        default = self.client.get(grading_url(mount, never_configured.id)).json()
        full = self.client.get(grading_url(mount, never_configured.id), {"view": "full"}).json()
        assert default["grace_period"] is None
        assert_matches_published_schema(default, DEFAULT_SHAPE)
        assert_matches_published_schema(full, FULL_SHAPE)
        assert_matches_published_schema(default, READ_SHAPE)
        assert_matches_published_schema(full, READ_SHAPE)
        with pytest.raises(jsonschema.ValidationError):
            assert_matches_published_schema(default, FULL_SHAPE)

    @ddt.data(*MOUNTS)
    def test_head_answers_without_a_body(self, mount):
        response = self.client.head(grading_url(mount, self.course.id))
        assert response.status_code == 200
        assert response.content == b""


# The one write a database's first role or permission lookup makes.
FIRST_LOOKUP_WRITES = [("INSERT", PolicyCacheControl._meta.db_table)]  # pylint: disable=protected-access
READS = [(mount, method, params) for mount in MOUNTS
         for method, params in (("get", {}), ("get", {"view": "full"}), ("head", {}))]


class _ReadIsSideEffectFreeMixin:
    """
    Every read (default, full, HEAD, both mounts) on a credit and a non-credit
    course writes no row and no Mongo document, stores nothing through the
    modulestore, sends no grading or publishing signal, and emits no event
    beyond the platform's request tracking.

    The reads start on a database that has never served a role or permission
    lookup. The permission library keeps a one-row policy version table and
    creates that row on its first lookup against a database, whichever API
    makes it, so the first read may insert it and nothing else; the v1 read
    inserts the same row.
    """

    def reader_for(self, course):
        """Return a user who may read ``course``, leaving the database as no lookup had touched it."""
        raise NotImplementedError

    def _assert_reads_have_no_effects(self, course):
        """Read ``course`` every way and assert nothing was written or announced."""
        published = []
        changed = []

        def on_published(sender, **kwargs):  # pylint: disable=unused-argument
            published.append(kwargs)

        def on_changed(sender, **kwargs):  # pylint: disable=unused-argument
            changed.append(kwargs)

        client = _client(self.reader_for(course))
        assert not PolicyCacheControl.objects.exists()
        SignalHandler.course_published.connect(on_published, weak=False)
        GRADING_POLICY_CHANGED.connect(on_changed, weak=False)
        try:
            for index, (mount, method, params) in enumerate(READS):
                url = grading_url(mount, course.id)
                with CaptureQueriesContext(connection) as queries, \
                        count_mongo_calls() as mongo, \
                        patch(UPDATE_ITEM) as update_item, \
                        patch("eventtracking.tracker.emit") as emit:
                    response = getattr(client, method)(url, params)
                request = (index, mount, method, params)
                assert response.status_code == 200, (request, response.content)
                assert sql_writes(queries) == (FIRST_LOOKUP_WRITES if index == 0 else []), request
                assert mongo["writes"] == 0, request
                assert update_item.call_count == 0, request
                # Only the platform's per-request tracking event, as for any request.
                assert [call.kwargs["name"] for call in emit.call_args_list] == [url], request
        finally:
            SignalHandler.course_published.disconnect(on_published)
            GRADING_POLICY_CHANGED.disconnect(on_changed)
        assert not published
        assert not changed

    def test_reading_a_course_has_no_side_effects(self):
        self._assert_reads_have_no_effects(seed_course(self.user))

    def test_reading_a_credit_course_has_no_side_effects(self):
        course = seed_course(self.user)
        CreditCourseFactory(course_key=course.id, enabled=True)
        with override_settings(ENABLE_CREDIT_ELIGIBILITY=True):
            self._assert_reads_have_no_effects(course)

    def test_the_first_v1_read_writes_the_same_row(self):
        course = seed_course(self.user)
        client = _client(self.reader_for(course))
        assert not PolicyCacheControl.objects.exists()
        with CaptureQueriesContext(connection) as queries, count_mongo_calls() as mongo:
            response = client.get(v1_url(course.id))
        assert response.status_code == 200, response.content
        assert sql_writes(queries) == FIRST_LOOKUP_WRITES
        assert mongo["writes"] == 0


class GradingReadSideEffectsLegacyPermissionsTest(_ReadIsSideEffectFreeMixin, ModuleStoreTestCase):
    """
    Reads by course staff are side-effect free with policy-based authoring
    permissions off. Course staff rather than global staff, whose access is
    decided without a role lookup.
    """

    def reader_for(self, course):
        reader = UserFactory()
        CourseStaffRole(course.id).add_users(reader)
        return reader


class GradingReadSideEffectsAuthzTest(CourseAuthoringAuthzTestMixin, _ReadIsSideEffectFreeMixin, ModuleStoreTestCase):
    """
    Reads by a course auditor are side-effect free with policy-based authoring
    permissions on. A course auditor rather than a superuser, whom the policy
    admits on the account's superuser flag without consulting role assignments.

    The test setup loads the policy and assigns roles through the permission
    library, which creates the policy version row; it is deleted before the
    reads so they start on a database no lookup has touched.
    """

    def reader_for(self, course):
        reader = UserFactory()
        self.add_user_to_role_in_course(reader, COURSE_AUDITOR.external_key, course.id)
        PolicyCacheControl.objects.all().delete()
        return reader


# ---------------------------------------------------------------------------
# Changing the policy
# ---------------------------------------------------------------------------

class _UpdateBehaviourMixin:
    """
    The update, checked against the v0 update as the reference: v0 hands any
    body to the grading model's whole-policy update as sent, so a whole-policy
    body must behave identically here.

    Each concrete class runs on one side of the flag that decides whether a
    grading change with no new weights or counts is announced.
    """

    recompute_only = None

    def setUp(self):
        super().setUp()
        self.enterContext(override_waffle_flag(MATERIAL_RECOMPUTE_ONLY_FLAG, active=self.recompute_only))
        self.staff = UserFactory(is_staff=True)
        self.client = _client(self.staff)

    def _twin_courses(self):
        return seed_course(self.staff), seed_course(self.staff)

    def _apply(self, request, course, body):
        """Send ``body`` through ``request``; return status, bytes, policy before and after, and effects."""
        before = stored_policy(course.id)
        with capture_grading_effects() as effects:
            response = request(course, body)
        effects["credit_tasks"] = [tuple(arg.replace(str(course.id), "<course>") for arg in args)
                                   for args in effects["credit_tasks"]]
        content = response.content.replace(str(course.id).encode(), b"<course>")
        return response.status_code, content, before, stored_policy(course.id), effects

    def _v0(self, course, body):
        return _post(self.client, v0_url(course.id), body)

    def _v3(self, mount):
        return lambda course, body: _patch(self.client, grading_url(mount, course.id), body)

    def test_whole_policy_bodies_behave_as_the_v0_update(self):
        for mount, name, body in FULL_BODY_CASES:
            with self.subTest(mount=mount, body=name):
                reference_course, course = self._twin_courses()
                expected = self._apply(self._v0, reference_course, body)
                actual = self._apply(self._v3(mount), course, body)
                assert expected[0] == 200, expected[1]
                assert actual[1] == expected[1]           # response bytes
                assert actual[3] == expected[3]           # stored policy
                assert actual[4] == expected[4]           # signals, events, credit task
                assert_matches_published_schema(json.loads(actual[1]), DEFAULT_SHAPE)

    def test_resending_the_stored_policy_is_announced_only_without_recompute_only(self):
        """Pins which side of the flag this class runs on."""
        for mount in MOUNTS:
            with self.subTest(mount=mount):
                _, course = self._twin_courses()
                effects = self._apply(self._v3(mount), course, FULL_BODIES["stored_policy_resent"])[4]
                expected = 0 if self.recompute_only else 1
                assert (effects["signals"], effects["events"]) == (expected, expected)

    def test_displayed_weights_are_stored_truncated(self):
        """A weight of 0.29 is shown as 28, so sending back what was shown stores 0.28."""
        _, course = self._twin_courses()
        body = FULL_BODIES["displayed_weights_resent"]
        status, content, _, after, _ = self._apply(self._v3(CONFORMING), course, body)
        assert status == 200
        assert json.loads(content)["graders"][0]["weight"] == 28
        assert after["raw_grader"][0]["weight"] == 0.28

    def test_partial_bodies_change_only_the_fields_sent(self):
        seeded_weights = [grader["weight"] for grader in SEED_GRADERS]
        cases = {
            # name: (body, fields that change, grading signals)
            "graders_only": ({"graders": GRADERS}, {"raw_grader"}, 1),
            "stored_graders_only": ({"graders": SEED_GRADERS_AS_SENT}, set(), 0 if self.recompute_only else 1),
            "cutoffs_only": ({"grade_cutoffs": CUTOFFS}, {"grade_cutoffs"}, 1),
            "stored_cutoffs_only": ({"grade_cutoffs": SEED_CUTOFFS}, set(), 0),
            "grace_period_only": ({"grace_period": {"hours": 5}}, {"graceperiod"}, 0),
            "grace_period_null_only": ({"grace_period": None}, set(), 0),
            "credit_only": ({"minimum_grade_credit": 0.6}, {"minimum_grade_credit"}, 0),
            "credit_null_only": ({"minimum_grade_credit": None}, set(), 0),
            "empty": ({}, set(), 0),
            "graders_and_credit": ({"graders": GRADERS, "minimum_grade_credit": 0.55},
                                   {"raw_grader", "minimum_grade_credit"}, 1),
            "cutoffs_and_grace_period": ({"grade_cutoffs": CUTOFFS, "grace_period": {"hours": 1}},
                                         {"grade_cutoffs", "graceperiod"}, 1),
            "graders_cutoffs_and_grace_period": ({"graders": GRADERS, "grade_cutoffs": CUTOFFS,
                                                  "grace_period": {"minutes": 9}},
                                                 {"raw_grader", "grade_cutoffs", "graceperiod"}, 1),
        }
        for mount in MOUNTS:
            for name, (body, changed, signals) in cases.items():
                with self.subTest(mount=mount, body=name):
                    reference_course, course = self._twin_courses()
                    if set(body) != {"graders", "grade_cutoffs", "grace_period", "minimum_grade_credit"}:
                        # The whole-policy update cannot take a partial body.
                        assert self._apply(self._v0, reference_course, body)[0] == 500
                    status, content, before, after, effects = self._apply(self._v3(mount), course, body)
                    assert status == 200, content
                    assert {field for field in before if before[field] != after[field]} == changed
                    if "graders" not in body:
                        assert [grader["weight"] for grader in after["raw_grader"]] == seeded_weights
                    assert (effects["signals"], effects["events"]) == (signals, signals)
                    expected_tasks = [("<course>",)] if "minimum_grade_credit" in body else []
                    assert effects["credit_tasks"] == expected_tasks
                    assert json.loads(content) == json.loads(
                        self.client.get(grading_url(mount, course.id)).content.replace(
                            str(course.id).encode(), b"<course>")
                    )

    def test_partial_bodies_without_graders_never_rewrite_stored_weights(self):
        """
        A weight such as 0.1019 does not survive the model's own percentage round trip
        (0.1019 * 100 / 100 == 0.10189999...), so any write-back of unsent graders shows.
        """
        drifting = [{**SEED_GRADERS[0], "weight": 0.1019}, *SEED_GRADERS[1:]]
        assert drifting[0]["weight"] * 100 / 100 != drifting[0]["weight"]
        bodies = (
            {}, {"grade_cutoffs": CUTOFFS}, {"grade_cutoffs": SEED_CUTOFFS}, {"grace_period": {"hours": 1}},
            {"grace_period": None}, {"minimum_grade_credit": 0.6}, {"minimum_grade_credit": None},
        )
        for mount in MOUNTS:
            for body in bodies:
                with self.subTest(mount=mount, body=body):
                    course = seed_course(self.staff)
                    course.raw_grader = [dict(grader) for grader in drifting]
                    modulestore().update_item(course, self.staff.id)
                    status = self._apply(self._v3(mount), course, body)[0]
                    assert status == 200
                    assert stored_policy(course.id)["raw_grader"] == drifting

    def test_partial_graders_keep_the_stored_cutoffs_grace_period_and_credit_minimum(self):
        _, course = self._twin_courses()
        _, _, before, after, _ = self._apply(self._v3(LEGACY), course, {"graders": GRADERS})
        assert after["grade_cutoffs"] == SEED_CUTOFFS
        assert after["graceperiod"] == SEED_GRACE
        assert after["minimum_grade_credit"] == SEED_CREDIT
        assert after["raw_grader"] != before["raw_grader"]

    def test_bodies_the_model_cannot_store_are_refused_before_anything_is_written(self):
        for mount in MOUNTS:
            for name, (body, field) in REJECTED_BODIES.items():
                with self.subTest(mount=mount, body=name):
                    reference_course, course = self._twin_courses()
                    assert self._apply(self._v0, reference_course, body)[0] == 500
                    with capture_grading_effects() as effects:
                        response = _patch(self.client, grading_url(mount, course.id), body)
                    envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
                    assert list(envelope["errors"]) == [field]
                    assert effects == {"signals": 0, "events": 0, "credit_tasks": []}
                    _, fresh = self._twin_courses()
                    assert stored_policy(course.id) == stored_policy(fresh.id)
                    assert modulestore().get_course(course.id).lowest_passing_grade == min(SEED_CUTOFFS.values())

    def test_numbers_too_large_to_store_are_refused_before_anything_is_written(self):
        for mount in MOUNTS:
            for name, (body, field) in TOO_LARGE_BODIES.items():
                with self.subTest(mount=mount, body=name):
                    course = seed_course(self.staff)
                    with capture_grading_effects() as effects:
                        response = _patch(self.client, grading_url(mount, course.id), body)
                    envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
                    assert list(envelope["errors"]) == [field]
                    assert effects == {"signals": 0, "events": 0, "credit_tasks": []}
                    assert stored_policy(course.id) == stored_policy(seed_course(self.staff).id)

    def test_a_form_encoded_body_is_refused(self):
        _, course = self._twin_courses()
        response = self.client.patch(
            grading_url(CONFORMING, course.id),
            data="graders[0]type=Homework&grade_cutoffs=x",
            content_type="application/x-www-form-urlencoded",
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert list(envelope["errors"]) == ["non_field_errors"]
        _, fresh = self._twin_courses()
        assert stored_policy(course.id) == stored_policy(fresh.id)


class WholePolicyBodyCasesTest(TestCase):
    """Every whole-policy body is replayed on at least one mount."""

    def test_hand_picked_bodies_run_on_both_mounts_and_generated_bodies_on_one(self):
        mounts_by_body = {}
        for mount, name, _ in FULL_BODY_CASES:
            mounts_by_body.setdefault(name, []).append(mount)
        assert len(FULL_BODIES) == len(HAND_PICKED_FULL_BODIES) + len(GENERATED_FULL_BODIES)
        assert set(mounts_by_body) == set(FULL_BODIES)
        for name in HAND_PICKED_FULL_BODIES:
            assert sorted(mounts_by_body[name]) == sorted(MOUNTS), name
        for name in GENERATED_FULL_BODIES:
            assert len(mounts_by_body[name]) == 1, name
        per_mount = [sum(mounts_by_body[name] == [mount] for name in GENERATED_FULL_BODIES) for mount in MOUNTS]
        assert max(per_mount) - min(per_mount) <= 1, per_mount


class GradingUpdateTest(_UpdateBehaviourMixin, ModuleStoreTestCase):
    """Updates when every grading change is announced."""

    recompute_only = False


class GradingUpdateRecomputeOnlyTest(_UpdateBehaviourMixin, ModuleStoreTestCase):
    """Updates when only changes to weights or counts are announced."""

    recompute_only = True


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

COURSE_KEY = "course-v1:edX+ToyX+Toy_Course"


@ddt.ddt
class GradingRoutingTest(SharedModuleStoreTestCase):
    """Both addresses serve one view with the same actions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_detail_url_resolves(self):
        assert grading_url(LEGACY, COURSE_KEY) == f"/api/contentstore/v3/authoring_grading/{COURSE_KEY}/"

    def test_conforming_url_reverses_to_expected_path(self):
        assert grading_url(CONFORMING, COURSE_KEY) == f"/api/authoring/v3/courses/{COURSE_KEY}/grading/"

    def test_conforming_and_legacy_routes_share_view_and_actions(self):
        legacy = resolve(grading_url(LEGACY, COURSE_KEY)).func
        conforming = resolve(grading_url(CONFORMING, COURSE_KEY)).func
        assert conforming.cls is legacy.cls
        assert routed_actions(conforming) == routed_actions(legacy) == {
            "get": "retrieve",
            "patch": "partial_update",
        }

    def test_legacy_format_suffix_reads_and_updates_like_the_plain_address(self):
        """The legacy router also routes ``<course key>.json`` to the same actions."""
        client = _client(UserFactory(is_staff=True))
        suffixed = reverse(
            "cms.djangoapps.contentstore:v3:authoring_grading-detail",
            kwargs={"course_key": str(self.course.id), "format": "json"},
        )
        assert suffixed == f"/api/contentstore/v3/authoring_grading/{self.course.id}.json"
        assert resolve(suffixed).func.cls is resolve(grading_url(LEGACY, self.course.id)).func.cls

        read = client.get(suffixed)
        assert read.status_code == 200, read.content
        assert read.json() == client.get(grading_url(LEGACY, self.course.id)).json()

        with patch(CREDIT_TASK):
            updated = _patch(client, suffixed, {"grace_period": {"hours": 7}})
        assert updated.status_code == 200, updated.content
        assert updated.json()["grace_period"] == {"hours": 7, "minutes": 0, "seconds": 0}
        assert updated.json() == client.get(grading_url(LEGACY, self.course.id)).json()

    # The error library does not catalogue MethodNotAllowed, ParseError,
    # UnsupportedMediaType or NotAcceptable, so these four answer with the
    # envelope typed internal. Change the expected type here once it does.

    @ddt.data(*[(mount, method) for mount in MOUNTS for method in ("post", "put", "delete")])
    @ddt.unpack
    def test_other_methods_are_not_allowed(self, mount, method):
        client = _client(UserFactory(is_staff=True))
        response = getattr(client, method)(grading_url(mount, self.course.id), data={}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    @ddt.data(*MOUNTS)
    def test_malformed_json_is_refused_with_400(self, mount):
        client = _client(UserFactory(is_staff=True))
        response = client.patch(
            grading_url(mount, self.course.id), data='{"graders": [', content_type="application/json",
        )
        assert_error_envelope(response, expected_status=400, expected_type_slug="internal")

    @ddt.data(*MOUNTS)
    def test_unsupported_body_type_is_refused_with_415(self, mount):
        client = _client(UserFactory(is_staff=True))
        response = client.patch(grading_url(mount, self.course.id), data="graders", content_type="text/plain")
        assert_error_envelope(response, expected_status=415, expected_type_slug="internal")

    @ddt.data(*MOUNTS)
    def test_unsupported_accept_is_refused_with_406(self, mount):
        client = _client(UserFactory(is_staff=True))
        response = client.get(grading_url(mount, self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # These addresses match no route, so the service's HTML 404 page answers them.
    # Change these to the error envelope once a catch-all route under each mount
    # answers unknown addresses.

    @ddt.data(*[(key, method) for key in ("not-a-course-key", "edX/ToyX/Toy_Course") for method in ("get", "patch")])
    @ddt.unpack
    def test_unparsable_or_slash_form_key_is_404_on_conforming_route(self, key, method):
        client = _client(UserFactory(is_staff=True))
        response = getattr(client, method)(f"/api/authoring/v3/courses/{key}/grading/", data={}, format="json")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    @ddt.data("get", "patch")
    def test_key_without_separators_is_404_on_legacy_route(self, method):
        client = _client(UserFactory(is_staff=True))
        response = getattr(client, method)("/api/contentstore/v3/authoring_grading/not-a-key/", data={}, format="json")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    @ddt.data("get", "patch")
    def test_version_only_course_key_is_404_on_conforming_route(self, method):
        # The converter accepts a key that names only a version. No course run
        # is behind it, so the view answers 404 rather than 500.
        client = _client(UserFactory())
        response = getattr(client, method)(
            "/api/authoring/v3/courses/course-v1:version@0123456789abcdef01234567/grading/", data={}, format="json",
        )
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_v0_endpoint_unaffected_by_v3_envelope(self):
        """The frozen v0 update keeps its own error body."""
        response = _client().post(v0_url(COURSE_KEY), data={}, format="json")
        assert response.status_code == 401
        assert "type" not in response.data
        assert "instance" not in response.data
        assert response.data["developer_message"] == "Authentication credentials were not provided."


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------

class _QueryCountMixin:
    """
    SQL and Mongo calls per request, measured on the second request so request
    caches are warm. A read costs no more than the v1 read, and the full view
    costs the same.
    """

    reader = None

    def setUp(self):
        super().setUp()
        self.client = _client(self.reader)
        self.course = CourseFactory.create()
        for index in range(2):
            section = BlockFactory.create(parent=self.course, category="chapter", display_name=f"S{index}")
            BlockFactory.create(parent=section, category="sequential", format="Homework", graded=True)

    def _measure(self, request):
        """SQL queries, Mongo reads and Mongo writes of the second of two identical requests."""
        request()
        with CaptureQueriesContext(connection) as queries, count_mongo_calls() as mongo:
            response = request()
        assert response.status_code == 200, response.content
        return len(queries.captured_queries), mongo["reads"], mongo["writes"]

    def _v1_read(self):
        return self._measure(lambda: self.client.get(v1_url(self.course.id)))

    def _read(self, mount, **params):
        return self._measure(lambda: self.client.get(grading_url(mount, self.course.id), params))

    def test_default_read_costs_no_more_than_the_v1_read(self):
        v1_sql, v1_mongo_reads, v1_mongo_writes = self._v1_read()
        for mount in MOUNTS:
            sql, mongo_reads, mongo_writes = self._read(mount)
            assert sql <= v1_sql
            assert mongo_reads <= v1_mongo_reads
            assert mongo_writes == v1_mongo_writes == 0

    def test_full_read_costs_the_same_as_the_v1_read(self):
        v1 = self._v1_read()
        for mount in MOUNTS:
            assert self._read(mount, view="full") == v1


@ddt.ddt
class GradingQueryCountTest(_QueryCountMixin, ModuleStoreTestCase):
    """
    Pinned counts with policy-based authoring permissions off: SQL queries,
    Mongo reads and Mongo writes per request.
    """

    def setUp(self):
        self.reader = UserFactory(is_staff=True)
        super().setUp()

    @ddt.data(*MOUNTS)
    def test_default_read(self, mount):
        assert self._read(mount) == (12, 4, 0)
        assert self._v1_read() == (14, 4, 0)

    @ddt.data(*MOUNTS)
    def test_full_read(self, mount):
        assert self._read(mount, view="full") == (14, 4, 0)

    @ddt.data(*MOUNTS)
    def test_whole_policy_update(self, mount):
        body = {
            "graders": [
                {"type": "Homework", "min_count": 2, "drop_count": 0, "short_label": "HW", "weight": 60, "id": 0},
                {"type": "Exam", "min_count": 1, "drop_count": 0, "short_label": "Ex", "weight": 40, "id": 1},
            ],
            "grade_cutoffs": {"Pass": 0.5},
            "grace_period": {"hours": 1, "minutes": 0},
            "minimum_grade_credit": 0.8,
        }
        counts = self._measure(lambda: _patch(self.client, grading_url(mount, self.course.id), body))
        assert counts == (33, 42, 6)


class GradingQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
    """Read costs against the v1 read with policy-based authoring permissions on."""

    def setUp(self):
        super().setUp()
        self.client = _client(self.super_user)
