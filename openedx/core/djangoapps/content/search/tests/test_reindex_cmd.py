"""
Tests for the reindex_studio management command and the rebuild_index_incremental Celery task.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from openedx.core.djangolib.testing.utils import skip_unless_cms

try:
    from .. import api
    from ..tasks import rebuild_index_incremental
except RuntimeError:
    pass


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
class TestReindexStudioCommand(TestCase):
    """Tests for the reindex_studio management command."""

    @patch("openedx.core.djangoapps.content.search.tasks.rebuild_index_incremental.delay")
    def test_enqueues_task(self, mock_delay):
        """Command enqueues the incremental rebuild task."""
        mock_delay.return_value = Mock(id="fake-task-id")

        call_command("reindex_studio")

        mock_delay.assert_called_once_with()

    @override_settings(MEILISEARCH_ENABLED=False)
    def test_disabled(self):
        """Command raises error when Meilisearch is disabled."""
        with pytest.raises(CommandError, match="not enabled"):
            call_command("reindex_studio")

    def test_reset_flag_removed(self):
        """Passing --reset raises a clear error."""
        with pytest.raises(CommandError, match="--reset flag has been removed"):
            call_command("reindex_studio", "--reset")

    def test_init_flag_removed(self):
        """Passing --init raises a clear error."""
        with pytest.raises(CommandError, match="--init flag has been removed"):
            call_command("reindex_studio", "--init")

    @patch("openedx.core.djangoapps.content.search.tasks.rebuild_index_incremental.delay")
    @patch("openedx.core.djangoapps.content.search.management.commands.reindex_studio.log")
    def test_incremental_flag_accepted_with_warning(self, mock_log, mock_delay):
        """Passing --incremental logs a warning but still enqueues the task."""
        mock_delay.return_value = Mock(id="fake-task-id")

        call_command("reindex_studio", "--incremental")

        mock_log.warning.assert_called_once()
        mock_delay.assert_called_once_with()


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestRebuildIndexIncrementalTask(TestCase):
    """Tests for the rebuild_index_incremental Celery task."""

    def setUp(self):
        super().setUp()
        api.clear_meilisearch_client()

    @patch("openedx.core.djangoapps.content.search.api.rebuild_index")
    def test_calls_rebuild_incremental(self, mock_rebuild, mock_meilisearch):
        """Task calls api.rebuild_index with incremental=True."""
        rebuild_index_incremental()

        mock_rebuild.assert_called_once()
        _, kwargs = mock_rebuild.call_args
        assert kwargs["incremental"] is True

    @patch("openedx.core.djangoapps.content.search.api.rebuild_index")
    def test_rebuild_already_in_progress(self, mock_rebuild, mock_meilisearch):
        """Task exits gracefully if rebuild lock is already held."""
        mock_rebuild.side_effect = RuntimeError("Rebuild already in progress")

        # Should not raise
        rebuild_index_incremental()

    @patch("openedx.core.djangoapps.content.search.api.rebuild_index")
    def test_other_runtime_error_raised(self, mock_rebuild, mock_meilisearch):
        """Task re-raises RuntimeError if it's not about lock contention."""
        mock_rebuild.side_effect = RuntimeError("Something else went wrong")

        with pytest.raises(RuntimeError, match="Something else went wrong"):
            rebuild_index_incremental()

    @patch("openedx.core.djangoapps.content.search.api.rebuild_index")
    def test_idempotent(self, mock_rebuild, mock_meilisearch):
        """Task can be called multiple times safely."""
        rebuild_index_incremental()
        rebuild_index_incremental()

        assert mock_rebuild.call_count == 2
