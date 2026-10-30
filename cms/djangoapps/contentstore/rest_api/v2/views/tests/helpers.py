"""Shared helpers for the v2 authoring endpoint tests."""

from contextlib import contextmanager
from unittest.mock import patch

import pymongo
from crum import signals as crum_signals
from django.core import signals as core_signals
from django.db import connection
from django.db.models import signals as model_signals
from django.db.models.signals import post_save
from django.dispatch import Signal
from django.test.utils import CaptureQueriesContext
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from openedx_authz.models.engine import PolicyCacheControl
from openedx_events.content_authoring.signals import XBLOCK_UPDATED
from openedx_events.tooling import OpenEdxPublicSignal
from rest_framework.test import APIClient

from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

PASSWORD = "test"

# The schema settings the CMS publishes its authoring schema with. The test
# settings module configures none.
SCHEMA_SETTINGS = {
    "PREPROCESSING_HOOKS": ["cms.lib.spectacular.cms_api_filter"],
    "SCHEMA_PATH_PREFIX": r"/api/(contentstore|authoring)",
}


def logged_in_client(user):
    """Return a client holding a real session for ``user``, created with ``PASSWORD``."""
    client = APIClient()
    assert client.login(username=user.username, password=PASSWORD)
    return client


def writes(queries):
    """Return the SQL statements in ``queries`` that insert, update or delete rows."""
    return [q["sql"] for q in queries if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


class SignalRecorder:
    """
    Record every Django signal and openedx-event sent while active.

    ``unexpected()`` leaves out only the signals any request sends without
    writing anything: the request boundaries, model instance construction on
    every row read, and django-crum's current-user bookkeeping.
    """

    NEUTRAL = (
        core_signals.request_started,
        core_signals.request_finished,
        model_signals.pre_init,
        model_signals.post_init,
        crum_signals.current_user_getter,
        crum_signals.current_user_setter,
    )

    def __init__(self):
        self.sent = []
        self._patches = []

    def _wrap(self, owner, name):
        """Prepare a patch of ``owner.name`` that records each send before performing it."""
        original = getattr(owner, name)
        recorder = self

        def wrapper(signal, *args, **kwargs):
            recorder.sent.append((name, signal))
            return original(signal, *args, **kwargs)

        self._patches.append(patch.object(owner, name, wrapper))

    def __enter__(self):
        self._wrap(Signal, "send")
        self._wrap(Signal, "send_robust")
        self._wrap(OpenEdxPublicSignal, "send_event")
        for active in self._patches:
            active.start()
        return self

    def __exit__(self, *exc):
        for active in self._patches:
            active.stop()

    def unexpected(self):
        """Return the recorded ``(method, signal)`` pairs other than the neutral signals."""
        return [(name, signal) for name, signal in self.sent if not any(signal is n for n in self.NEUTRAL)]


def generate_cms_schema():
    """Generate the CMS authoring schema with the settings the service publishes it with."""
    with patched_settings(SCHEMA_SETTINGS):
        return SchemaGenerator().get_schema(request=None, public=True)


def resolve_ref(schema, node):
    """Return the component ``node`` points at, or ``node`` itself when it is inline."""
    ref = node.get("$ref")
    if ref is None:
        return node
    return schema["components"]["schemas"][ref.rsplit("/", 1)[1]]


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


# The one write the permission library makes on a database's first role or
# permission lookup, whichever API makes it.
FIRST_LOOKUP_WRITES = [("INSERT", PolicyCacheControl._meta.db_table)]  # pylint: disable=protected-access


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
