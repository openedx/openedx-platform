"""Shared helpers for the authoring v1 endpoint tests."""

from contextlib import contextmanager
from functools import cache
from unittest.mock import patch

import jsonschema
import pymongo
from crum import signals as crum_signals
from django.core import signals as core_signals
from django.db import connection
from django.db.models import signals as model_signals
from django.dispatch import Signal
from django.test.utils import CaptureQueriesContext
from edx_rest_framework_extensions.testing import assert_error_envelope
from openedx_authz.models.engine import PolicyCacheControl
from openedx_events.tooling import OpenEdxPublicSignal
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import generate_cms_schema
from xmodule.modulestore.mixed import MixedModuleStore

COURSE_NOT_FOUND = "The requested course does not exist."
UNKNOWN_COURSE_KEY = "course-v1:NoSuchOrg+NoSuchCourse+NoSuchRun"
VERSION_ONLY_KEY = "course-v1:version@0123456789abcdef01234567"


# ---------------------------------------------------------------------------
# Clients and refusals
# ---------------------------------------------------------------------------

def api_client(user=None):
    """An API client that returns server errors as responses, authenticated as ``user`` if given."""
    client = APIClient()
    client.raise_request_exception = False
    if user is not None:
        client.force_authenticate(user=user)
    return client


def session_client(user):
    """A client signed in to a browser session as ``user``."""
    user.set_password("password")
    user.save()
    client = APIClient()
    assert client.login(username=user.username, password="password")
    return client


def assert_refused(response, status):
    """Assert ``response`` is the error envelope for ``status``; a 404 names the missing course."""
    slug = {401: "authn", 403: "authz", 404: "not-found"}[status]
    envelope = assert_error_envelope(response, expected_status=status, expected_type_slug=slug)
    if status == 404:
        assert envelope["detail"] == COURSE_NOT_FOUND
    return envelope


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

@cache
def published_schema():
    """The CMS authoring schema, generated once per test process."""
    return generate_cms_schema()


def as_json_schema(node):
    """An OpenAPI 3.0 schema as JSON Schema: ``nullable`` becomes a null alternative."""
    if isinstance(node, list):
        return [as_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    converted = {key: as_json_schema(value) for key, value in node.items() if key != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


def assert_matches_published_schema(body, component):
    """Assert ``body`` is valid against the published schema component ``component``."""
    components = as_json_schema(published_schema()["components"]["schemas"])
    jsonschema.validate(body, {"$ref": f"#/components/schemas/{component}", "components": {"schemas": components}})


# ---------------------------------------------------------------------------
# Comparing two response bodies
# ---------------------------------------------------------------------------

VOLATILE_FIELDS = {"instance"}
_ABSENT = object()


def flatten_json(obj, prefix=""):
    """
    A ``{json_path: value}`` map of every leaf of ``obj``, without volatile
    members. An empty object or array is a leaf, so a container that empties
    or changes kind is seen.
    """
    flat = {}
    if isinstance(obj, dict) and obj:
        for key in sorted(obj):
            if key in VOLATILE_FIELDS:
                continue
            flat.update(flatten_json(obj[key], f"{prefix}.{key}" if prefix else key))
    elif isinstance(obj, list) and obj:
        for index, item in enumerate(obj):
            flat.update(flatten_json(item, f"{prefix}[{index}]"))
    else:
        flat[prefix] = obj
    return flat


def same_json(a, b):
    """Whether two leaves are equal and of the same JSON type: true is not 1, 1.0 is not 1, {} is not []."""
    return type(a) is type(b) and a == b


def changed_paths(legacy, new):
    """The json paths whose leaf differs between the two bodies."""
    a, b = flatten_json(legacy), flatten_json(new)
    return {key for key in set(a) | set(b) if not same_json(a.get(key, _ABSENT), b.get(key, _ABSENT))}


def undeclared_paths(changed, intentional_differences):
    """The ``changed`` paths that no ``(path, reason)`` entry of ``intentional_differences`` covers."""
    declared = [path for path, _ in intentional_differences]
    return {
        key for key in changed
        if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for path in declared)
    }


# ---------------------------------------------------------------------------
# Side effects and costs
# ---------------------------------------------------------------------------

# The one write the permission library makes on a database's first role or
# permission lookup, and the model signals its save sends.
FIRST_LOOKUP_WRITES = [("INSERT", PolicyCacheControl._meta.db_table)]  # pylint: disable=protected-access


FIRST_LOOKUP_SIGNALS = [("pre_save", "PolicyCacheControl"), ("post_save", "PolicyCacheControl")]


# Signals every request sends without writing anything.
NEUTRAL_SIGNALS = (
    core_signals.request_started,
    core_signals.request_finished,
    model_signals.pre_init,
    model_signals.post_init,
    crum_signals.current_user_getter,
    crum_signals.current_user_setter,
)


MODEL_SIGNAL_NAMES = {
    model_signals.pre_save: "pre_save",
    model_signals.post_save: "post_save",
    model_signals.pre_delete: "pre_delete",
    model_signals.post_delete: "post_delete",
    model_signals.m2m_changed: "m2m_changed",
}


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
    found = []
    for query in queries.captured_queries:
        words = query["sql"].split()
        statement = words[0].upper()
        if statement in ("INSERT", "DELETE"):
            found.append((statement, words[2].strip('"`')))
        elif statement == "UPDATE":
            found.append((statement, words[1].strip('"`')))
    return found


@contextmanager
def record_effects(test_case):
    """
    Record, inside the block, SQL writes, Mongo writes, modulestore updates,
    every Django signal other than the neutral ones (as ``(name, sender)``) and
    every openedx-event. Commit callbacks registered inside the block are run,
    so anything sent on commit is seen.
    """
    effects = {"signals": [], "events": [], "update_item": []}
    send, send_robust, send_event = Signal.send, Signal.send_robust, OpenEdxPublicSignal.send_event
    update_item = MixedModuleStore.update_item

    def record(signal, sender):
        if any(signal is neutral for neutral in NEUTRAL_SIGNALS):
            return
        name = MODEL_SIGNAL_NAMES.get(signal, repr(signal))
        effects["signals"].append((name, getattr(sender, "__name__", repr(sender))))

    def spy_send(signal, sender, **named):
        record(signal, sender)
        return send(signal, sender, **named)

    def spy_send_robust(signal, sender, **named):
        record(signal, sender)
        return send_robust(signal, sender, **named)

    def spy_send_event(signal, *args, **kwargs):
        effects["events"].append(signal.event_type)
        return send_event(signal, *args, **kwargs)

    def spy_update_item(store, xblock, *args, **kwargs):
        effects["update_item"].append(str(xblock.location))
        return update_item(store, xblock, *args, **kwargs)

    with CaptureQueriesContext(connection) as queries, \
            count_mongo_calls() as mongo, \
            patch.object(Signal, "send", spy_send), \
            patch.object(Signal, "send_robust", spy_send_robust), \
            patch.object(OpenEdxPublicSignal, "send_event", spy_send_event), \
            patch.object(MixedModuleStore, "update_item", spy_update_item):
        # Innermost, so the callbacks run on its exit while every spy above is installed.
        with test_case.captureOnCommitCallbacks(execute=True):
            yield effects
    effects["sql_writes"] = sql_writes(queries)
    effects["mongo_writes"] = mongo["writes"]


def measure(request):
    """SQL queries, Mongo reads and Mongo writes of the second of two identical requests."""
    request()
    with CaptureQueriesContext(connection) as queries, count_mongo_calls() as mongo:
        response = request()
    assert response.status_code == 200, response.content
    return len(queries.captured_queries), mongo["reads"], mongo["writes"]
