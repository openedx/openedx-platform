"""Tests for the shared v2 test helpers."""

from django.db import connection
from django.db.models import signals as model_signals
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import PASSWORD, SignalRecorder, writes
from common.djangoapps.student.tests.factories import UserFactory


class WriteCaptureTest(TestCase):
    """The capture the idempotence tests rely on sees a write when one happens."""

    def test_the_capture_sees_a_write_when_one_happens(self):
        with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
            UserFactory(password=PASSWORD)
        assert writes(queries.captured_queries) != []
        assert any(signal is model_signals.post_save for _, signal in signals.unexpected())
