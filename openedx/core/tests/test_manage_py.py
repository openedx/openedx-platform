"""
Tests for the management command context manager filter hook in the top-level ``manage.py``.
"""
import os
import runpy
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from openedx_filters.management.filters import ManagementCommandContextmanagerRequested

MANAGE_PY = str(Path(__file__).resolve().parents[3] / "manage.py")


class ManagePyFilterTest(TestCase):
    """
    Verify that manage.py runs the command inside the context manager returned by the filter pipeline.
    """

    def _run_manage_py(self, argv, filter_side_effect=None):
        """
        Execute manage.py as ``__main__`` with the given argv.

        Returns the mocked ``run_filter`` and ``execute_from_command_line``.
        """
        events = []

        @contextmanager
        def tracking_contextmanager():
            events.append("enter")
            yield
            events.append("exit")

        def fake_run_filter(**kwargs):
            return (
                kwargs["command_contextmanager"] if filter_side_effect is None else tracking_contextmanager(),
                kwargs["command_name"],
                kwargs["service_variant"],
            )

        mock_execute = MagicMock(side_effect=lambda *_a, **_k: events.append("execute"))
        filter_patch = patch.object(
            ManagementCommandContextmanagerRequested, "run_filter", side_effect=fake_run_filter
        )
        with patch.object(sys, "argv", ["manage.py", *argv]), \
                patch.dict(os.environ), \
                filter_patch as mock_filter, \
                patch("django.core.management.execute_from_command_line", mock_execute):
            runpy.run_path(MANAGE_PY, run_name="__main__")
        return mock_filter, mock_execute, events

    def test_filter_receives_command_name_and_service_variant(self):
        mock_filter, mock_execute, _ = self._run_manage_py(["lms", "migrate", "--noinput"])

        kwargs = mock_filter.call_args.kwargs
        assert kwargs["command_name"] == "migrate"
        assert kwargs["service_variant"] == "lms"
        # runpy sets argv[0] to the absolute path of manage.py, so only compare the arguments after it.
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args[0][1:] == ["migrate", "--noinput"]

    def test_cms_service_variant(self):
        mock_filter, _, _ = self._run_manage_py(["cms", "check"])

        assert mock_filter.call_args.kwargs["service_variant"] == "cms"

    def test_leading_options_are_skipped_to_find_command_name(self):
        mock_filter, mock_execute, _ = self._run_manage_py(["lms", "--verbosity", "2", "--traceback", "migrate"])

        assert mock_filter.call_args.kwargs["command_name"] == "migrate"
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args[0][1:] == ["--verbosity", "2", "--traceback", "migrate"]

    def test_options_only_uses_fallback_name(self):
        mock_filter, _, _ = self._run_manage_py(["lms", "--traceback"])

        assert mock_filter.call_args.kwargs["command_name"] == "no-argument"

    def test_no_command_uses_fallback_name(self):
        mock_filter, _, _ = self._run_manage_py(["lms"])

        assert mock_filter.call_args.kwargs["command_name"] == "no-argument"

    def test_command_runs_inside_returned_contextmanager(self):
        _, _, events = self._run_manage_py(["lms", "check"], filter_side_effect=True)

        assert events == ["enter", "execute", "exit"]
