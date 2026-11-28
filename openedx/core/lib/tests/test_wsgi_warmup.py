"""
Tests for wsgi_warmup.py
"""
import gc
import sys
from unittest.mock import call, patch

from django.conf import settings
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import get_resolver

from openedx.core.lib.wsgi_warmup import GC_THRESHOLDS, _import_stores, _load_urls, warm_up_service_and_freeze_gc

MODULESTORE = {
    "default": {
        "ENGINE": "xmodule.modulestore.mixed.MixedModuleStore",
        "OPTIONS": {
            "stores": [
                {"NAME": "split", "ENGINE": "xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore"},
            ],
        },
    },
}
CONTENTSTORE = {"ENGINE": "xmodule.contentstore.mongo.MongoContentStore"}


class RestoreGCMixin:
    """
    Start each test with GC off, as it is when a WSGI module calls warm_up_service_and_freeze_gc(),
    and put the GC state back afterwards.
    """

    def setUp(self):
        super().setUp()
        self.original_thresholds = gc.get_threshold()
        self.was_enabled = gc.isenabled()
        gc.disable()  # as the WSGI modules do before they import anything

    def tearDown(self):
        gc.unfreeze()
        gc.set_threshold(*self.original_thresholds)
        if self.was_enabled:
            gc.enable()
        else:
            gc.disable()
        super().tearDown()

    def assert_gc_frozen_and_enabled(self, thresholds=GC_THRESHOLDS):
        assert gc.isenabled()
        assert gc.get_threshold() == thresholds
        assert gc.get_freeze_count() > 0


@override_settings(MODULESTORE=MODULESTORE, CONTENTSTORE=CONTENTSTORE)
@patch("openedx.core.lib.wsgi_warmup.import_module")
@patch("django.urls.reverse")
class TestWarmUpAndFreezeGC(RestoreGCMixin, SimpleTestCase):
    """
    Test warm_up_service_and_freeze_gc() with the warm-up steps' imports mocked out.
    """

    def test_warms_up_then_freezes(self, mock_reverse, mock_import_module):
        warm_up_service_and_freeze_gc()

        mock_reverse.assert_called_once_with("heartbeat")
        assert mock_import_module.call_args_list == [
            call("xmodule.modulestore.mixed"),
            call("xmodule.contentstore.mongo"),
            call("xmodule.modulestore.split_mongo.split_draft"),
        ]
        self.assert_gc_frozen_and_enabled()

    def test_custom_thresholds(self, mock_reverse, mock_import_module):  # pylint: disable=unused-argument
        warm_up_service_and_freeze_gc(thresholds=(5000, 10, 10))

        self.assert_gc_frozen_and_enabled(thresholds=(5000, 10, 10))

    def test_failed_step_does_not_stop_the_others(self, mock_reverse, mock_import_module):
        mock_reverse.side_effect = RuntimeError("no URLconf")

        with self.assertLogs("openedx.core.lib.wsgi_warmup", level="ERROR") as logs:
            warm_up_service_and_freeze_gc()

        assert "_load_urls failed" in logs.output[0]
        assert mock_import_module.call_count == 3
        self.assert_gc_frozen_and_enabled()

    def test_failure_still_enables_gc(self, mock_reverse, mock_import_module):  # pylint: disable=unused-argument
        mock_import_module.side_effect = ImportError("no such store")

        with self.assertLogs("openedx.core.lib.wsgi_warmup", level="ERROR") as logs:
            warm_up_service_and_freeze_gc()

        assert "_import_stores failed" in logs.output[0]
        self.assert_gc_frozen_and_enabled()

    @override_settings(WSGI_WARM_UP_SERVICE_AND_FREEZE_GC=False)
    def test_setting_turns_it_all_off(self, mock_reverse, mock_import_module):
        frozen_before = gc.get_freeze_count()

        warm_up_service_and_freeze_gc()

        mock_reverse.assert_not_called()
        mock_import_module.assert_not_called()
        assert gc.isenabled()
        assert gc.get_threshold() == self.original_thresholds
        assert gc.get_freeze_count() == frozen_before


class TestWarmUpForReal(RestoreGCMixin, SimpleTestCase):
    """
    Run the warm-up with nothing mocked, against the URLconf and the stores of whichever
    service's test settings are in use. CI runs this file under both the LMS and the
    Studio settings.

    These tests catch a warm-up that breaks, such as a renamed URL or a change in the
    shape of the store settings. They can't show that the warm-up is what loads the code,
    because most of it is already imported by the time a test runs.

    SimpleTestCase refuses database queries, so these also check that the warm-up
    makes none.
    """

    def test_load_urls(self):
        _load_urls()

        assert settings.ROOT_URLCONF in sys.modules
        assert get_resolver().reverse("heartbeat") == "heartbeat"

    def test_import_stores(self):
        modulestore_settings = settings.MODULESTORE["default"]
        stores = modulestore_settings["OPTIONS"]["stores"]
        assert stores, "The test settings should name at least one store for this test to mean anything."
        engines = [modulestore_settings["ENGINE"], settings.CONTENTSTORE["ENGINE"]]
        engines += [store["ENGINE"] for store in stores]

        _import_stores()

        for engine in engines:
            module_name, class_name = engine.rsplit(".", 1)
            assert hasattr(sys.modules[module_name], class_name)

    def test_whole_warm_up_logs_no_errors(self):
        # warm_up_service_and_freeze_gc() logs a failed step instead of raising, so a quiet log
        # is the only sign that every step worked.
        with self.assertNoLogs("openedx.core.lib.wsgi_warmup", level="ERROR"):
            warm_up_service_and_freeze_gc()

        self.assert_gc_frozen_and_enabled()
