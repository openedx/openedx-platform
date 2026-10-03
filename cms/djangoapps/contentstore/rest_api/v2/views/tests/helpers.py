"""Shared helpers for the v2 authoring endpoint tests."""

from unittest.mock import patch

from crum import signals as crum_signals
from django.core import signals as core_signals
from django.db.models import signals as model_signals
from django.dispatch import Signal
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from openedx_events.tooling import OpenEdxPublicSignal
from rest_framework.test import APIClient

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
