"""
Tests for the Meilisearch index reconciliation logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from django.test import TestCase, override_settings
from meilisearch.errors import MeilisearchError

from openedx.core.djangolib.testing.utils import skip_unless_cms

try:
    from .. import api
    from ..api import (
        IndexDrift,
        _apply_index_settings,
        _detect_index_drift,
        reconcile_index,
    )
    from ..apps import ContentSearchConfig
    from ..handlers import handle_post_migrate
    from ..index_config import (
        INDEX_DISTINCT_ATTRIBUTE,
        INDEX_FILTERABLE_ATTRIBUTES,
        INDEX_PRIMARY_KEY,
        INDEX_RANKING_RULES,
        INDEX_SEARCHABLE_ATTRIBUTES,
        INDEX_SORTABLE_ATTRIBUTES,
    )
except RuntimeError:
    pass


@skip_unless_cms
class TestIndexDrift(TestCase):
    """Tests for the IndexDrift dataclass."""

    def test_all_fields_match(self):
        drift = IndexDrift(
            exists=True,
            is_empty=False,
            primary_key_correct=True,
            distinct_attribute_match=True,
            filterable_attributes_match=True,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        assert drift.exists is True
        assert drift.is_empty is False
        assert drift.primary_key_correct is True
        assert drift.distinct_attribute_match is True
        assert drift.filterable_attributes_match is True
        assert drift.searchable_attributes_match is True
        assert drift.sortable_attributes_match is True
        assert drift.ranking_rules_match is True
        assert not drift.is_settings_drifted

    def test_settings_drifted_when_one_setting_false(self):
        drift = IndexDrift(
            exists=True,
            is_empty=False,
            primary_key_correct=True,
            distinct_attribute_match=True,
            filterable_attributes_match=False,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        assert drift.primary_key_correct is True
        assert drift.filterable_attributes_match is False
        assert drift.is_settings_drifted

    def test_primary_key_wrong_without_settings_drift(self):
        drift = IndexDrift(
            exists=True,
            is_empty=False,
            primary_key_correct=False,
            distinct_attribute_match=True,
            filterable_attributes_match=True,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        assert drift.primary_key_correct is False
        assert drift.distinct_attribute_match is True
        assert drift.filterable_attributes_match is True
        assert drift.searchable_attributes_match is True
        assert drift.sortable_attributes_match is True
        assert drift.ranking_rules_match is True
        assert not drift.is_settings_drifted

    def test_missing_index_leaves_optional_fields_unset(self):
        drift = IndexDrift(exists=False)
        assert drift.exists is False
        assert drift.is_empty is None
        assert drift.primary_key_correct is None
        assert drift.distinct_attribute_match is None
        assert drift.filterable_attributes_match is None
        assert drift.searchable_attributes_match is None
        assert drift.sortable_attributes_match is None
        assert drift.ranking_rules_match is None
        assert not drift.is_settings_drifted

    def test_multiple_settings_drifted(self):
        drift = IndexDrift(
            exists=True,
            is_empty=True,
            primary_key_correct=True,
            distinct_attribute_match=False,
            filterable_attributes_match=False,
            searchable_attributes_match=True,
            sortable_attributes_match=False,
            ranking_rules_match=True,
        )
        assert drift.distinct_attribute_match is False
        assert drift.filterable_attributes_match is False
        assert drift.searchable_attributes_match is True
        assert drift.sortable_attributes_match is False
        assert drift.ranking_rules_match is True
        assert drift.is_settings_drifted


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestDetectIndexDrift(TestCase):
    """Tests for _detect_index_drift()."""

    def setUp(self):
        super().setUp()
        api.clear_meilisearch_client()

    def test_index_missing(self, mock_meilisearch):
        """When the index doesn't exist, returns exists=False with all other fields None."""
        from meilisearch.errors import MeilisearchApiError

        mock_meilisearch.return_value.get_index.side_effect = MeilisearchApiError(
            "Not found", Mock(text='{"code":"index_not_found"}')
        )

        drift = _detect_index_drift("test_index")

        assert not drift.exists
        assert drift.is_empty is None
        assert drift.primary_key_correct is None
        assert drift.distinct_attribute_match is None

    def test_all_settings_match(self, mock_meilisearch):
        """When all settings match, returns non-drifted state."""
        mock_index = Mock()
        mock_index.primary_key = INDEX_PRIMARY_KEY
        mock_index.get_stats.return_value = Mock(number_of_documents=100)
        mock_index.get_settings.return_value = {
            "distinctAttribute": INDEX_DISTINCT_ATTRIBUTE,
            "filterableAttributes": list(INDEX_FILTERABLE_ATTRIBUTES),
            "searchableAttributes": list(INDEX_SEARCHABLE_ATTRIBUTES),
            "sortableAttributes": list(INDEX_SORTABLE_ATTRIBUTES),
            "rankingRules": list(INDEX_RANKING_RULES),
        }
        mock_meilisearch.return_value.get_index.return_value = mock_index

        drift = _detect_index_drift("test_index")

        assert drift.exists
        assert drift.is_empty is False
        assert drift.primary_key_correct is True
        assert drift.distinct_attribute_match is True
        assert drift.filterable_attributes_match is True
        assert drift.searchable_attributes_match is True
        assert drift.sortable_attributes_match is True
        assert drift.ranking_rules_match is True
        assert not drift.is_settings_drifted

    def test_filterable_attributes_mismatch(self, mock_meilisearch):
        """Detects when filterable attributes differ."""
        mock_index = Mock()
        mock_index.primary_key = INDEX_PRIMARY_KEY
        mock_index.get_stats.return_value = Mock(number_of_documents=0)
        mock_index.get_settings.return_value = {
            "distinctAttribute": INDEX_DISTINCT_ATTRIBUTE,
            "filterableAttributes": ["some_other_field"],
            "searchableAttributes": list(INDEX_SEARCHABLE_ATTRIBUTES),
            "sortableAttributes": list(INDEX_SORTABLE_ATTRIBUTES),
            "rankingRules": list(INDEX_RANKING_RULES),
        }
        mock_meilisearch.return_value.get_index.return_value = mock_index

        drift = _detect_index_drift("test_index")

        assert drift.exists
        assert drift.is_empty
        assert drift.filterable_attributes_match is False
        assert drift.distinct_attribute_match is True
        assert drift.is_settings_drifted

    def test_ranking_rules_order_matters(self, mock_meilisearch):
        """Ranking rules comparison is order-sensitive."""
        mock_index = Mock()
        mock_index.primary_key = INDEX_PRIMARY_KEY
        mock_index.get_stats.return_value = Mock(number_of_documents=50)
        # Reverse the ranking rules
        mock_index.get_settings.return_value = {
            "distinctAttribute": INDEX_DISTINCT_ATTRIBUTE,
            "filterableAttributes": list(INDEX_FILTERABLE_ATTRIBUTES),
            "searchableAttributes": list(INDEX_SEARCHABLE_ATTRIBUTES),
            "sortableAttributes": list(INDEX_SORTABLE_ATTRIBUTES),
            "rankingRules": list(reversed(INDEX_RANKING_RULES)),
        }
        mock_meilisearch.return_value.get_index.return_value = mock_index

        drift = _detect_index_drift("test_index")

        assert drift.ranking_rules_match is False
        assert drift.is_settings_drifted

    def test_filterable_attributes_order_independent(self, mock_meilisearch):
        """Filterable/searchable/sortable attributes comparison is order-independent."""
        mock_index = Mock()
        mock_index.primary_key = INDEX_PRIMARY_KEY
        mock_index.get_stats.return_value = Mock(number_of_documents=10)
        mock_index.get_settings.return_value = {
            "distinctAttribute": INDEX_DISTINCT_ATTRIBUTE,
            "filterableAttributes": list(reversed(INDEX_FILTERABLE_ATTRIBUTES)),
            "searchableAttributes": list(reversed(INDEX_SEARCHABLE_ATTRIBUTES)),
            "sortableAttributes": list(reversed(INDEX_SORTABLE_ATTRIBUTES)),
            "rankingRules": list(INDEX_RANKING_RULES),
        }
        mock_meilisearch.return_value.get_index.return_value = mock_index

        drift = _detect_index_drift("test_index")

        assert drift.filterable_attributes_match is True
        assert drift.searchable_attributes_match is True
        assert drift.sortable_attributes_match is True
        assert drift.primary_key_correct is True
        assert not drift.is_settings_drifted

    def test_primary_key_mismatch(self, mock_meilisearch):
        """Detects primary key mismatch."""
        mock_index = Mock()
        mock_index.primary_key = "wrong_key"
        mock_index.get_stats.return_value = Mock(number_of_documents=100)
        mock_index.get_settings.return_value = {
            "distinctAttribute": INDEX_DISTINCT_ATTRIBUTE,
            "filterableAttributes": list(INDEX_FILTERABLE_ATTRIBUTES),
            "searchableAttributes": list(INDEX_SEARCHABLE_ATTRIBUTES),
            "sortableAttributes": list(INDEX_SORTABLE_ATTRIBUTES),
            "rankingRules": list(INDEX_RANKING_RULES),
        }
        mock_meilisearch.return_value.get_index.return_value = mock_index

        drift = _detect_index_drift("test_index")

        assert drift.exists is True
        assert drift.is_empty is False
        assert drift.primary_key_correct is False
        # Settings themselves are fine
        assert not drift.is_settings_drifted

    def test_multiple_mismatches(self, mock_meilisearch):
        """Reports all drifted fields when multiple settings differ."""
        mock_index = Mock()
        mock_index.primary_key = INDEX_PRIMARY_KEY
        mock_index.get_stats.return_value = Mock(number_of_documents=0)
        mock_index.get_settings.return_value = {
            "distinctAttribute": "wrong_attr",
            "filterableAttributes": ["wrong"],
            "searchableAttributes": list(INDEX_SEARCHABLE_ATTRIBUTES),
            "sortableAttributes": list(INDEX_SORTABLE_ATTRIBUTES),
            "rankingRules": ["wrong"],
        }
        mock_meilisearch.return_value.get_index.return_value = mock_index

        drift = _detect_index_drift("test_index")

        assert drift.distinct_attribute_match is False
        assert drift.filterable_attributes_match is False
        assert drift.searchable_attributes_match is True
        assert drift.sortable_attributes_match is True
        assert drift.ranking_rules_match is False
        assert drift.is_settings_drifted


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestApplyIndexSettings(TestCase):
    """Tests for _apply_index_settings()."""

    def setUp(self):
        super().setUp()
        api.clear_meilisearch_client()

    @patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
    def test_applies_all_settings(self, mock_meilisearch):
        """All 5 settings are applied in wait mode."""
        mock_index = mock_meilisearch.return_value.index.return_value
        status_cb = Mock()

        _apply_index_settings("test_index", wait=True, status_cb=status_cb)

        mock_index.update_distinct_attribute.assert_called_once_with(INDEX_DISTINCT_ATTRIBUTE)
        mock_index.update_filterable_attributes.assert_called_once_with(INDEX_FILTERABLE_ATTRIBUTES)
        mock_index.update_searchable_attributes.assert_called_once_with(INDEX_SEARCHABLE_ATTRIBUTES)
        mock_index.update_sortable_attributes.assert_called_once_with(INDEX_SORTABLE_ATTRIBUTES)
        mock_index.update_ranking_rules.assert_called_once_with(INDEX_RANKING_RULES)

    @patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task")
    def test_waits_for_each_task(self, mock_wait, mock_meilisearch):
        """Each settings update is waited on when wait=True."""
        mock_index = mock_meilisearch.return_value.index.return_value
        task_info = Mock()
        mock_index.update_distinct_attribute.return_value = task_info
        mock_index.update_filterable_attributes.return_value = task_info
        mock_index.update_searchable_attributes.return_value = task_info
        mock_index.update_sortable_attributes.return_value = task_info
        mock_index.update_ranking_rules.return_value = task_info

        _apply_index_settings("test_index", wait=True)

        assert mock_wait.call_count == 5

    @patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task")
    def test_does_not_wait_when_wait_false(self, mock_wait, mock_meilisearch):
        """Settings are fire-and-forget when wait=False."""
        status_cb = Mock()

        _apply_index_settings("test_index", wait=False, status_cb=status_cb)

        mock_wait.assert_not_called()
        status_cb.assert_called()

    @patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task")
    def test_raises_on_task_failure(self, mock_wait, mock_meilisearch):
        """MeilisearchError is raised if a waited-on task fails."""
        mock_wait.side_effect = MeilisearchError("Task failed")

        with pytest.raises(MeilisearchError):
            _apply_index_settings("test_index", wait=True)


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestReconcileIndex(TestCase):
    """Tests for reconcile_index()."""

    def setUp(self):
        super().setUp()
        api.clear_meilisearch_client()

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    @patch("openedx.core.djangoapps.content.search.api.reset_index")
    def test_index_missing(self, mock_reset, mock_drift, mock_meilisearch):
        """When index doesn't exist, reset_index is called to create it."""
        mock_drift.return_value = IndexDrift(exists=False)
        status_cb = Mock()

        reconcile_index(status_cb=status_cb)

        mock_reset.assert_called_once()
        status_cb.assert_any_call("Studio search index not found. Creating and configuring...")

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    def test_index_empty_configured(self, mock_drift, mock_meilisearch):
        """When index is empty and configured, no action taken."""
        mock_drift.return_value = IndexDrift(
            exists=True,
            is_empty=True,
            primary_key_correct=True,
            distinct_attribute_match=True,
            filterable_attributes_match=True,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        status_cb = Mock()

        reconcile_index(status_cb=status_cb)

        status_cb.assert_any_call(
            "Index exists and is correctly configured but empty. Run './manage.py cms reindex_studio' to populate."
        )

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    @patch("openedx.core.djangoapps.content.search.api._apply_index_settings")
    def test_index_empty_drifted_settings(self, mock_apply, mock_drift, mock_meilisearch):
        """When index is empty and settings drifted, settings are applied."""
        mock_drift.return_value = IndexDrift(
            exists=True,
            is_empty=True,
            primary_key_correct=True,
            distinct_attribute_match=True,
            filterable_attributes_match=False,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        status_cb = Mock()

        reconcile_index(status_cb=status_cb)

        mock_apply.assert_called_once_with(api.STUDIO_INDEX_NAME, wait=True, status_cb=status_cb)
        status_cb.assert_any_call("Empty index has drifted settings. Reconfiguring...")

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    @patch("openedx.core.djangoapps.content.search.api.reset_index")
    def test_index_empty_wrong_pk(self, mock_reset, mock_drift, mock_meilisearch):
        """When index is empty with wrong PK, reset_index is called."""
        mock_drift.return_value = IndexDrift(
            exists=True,
            is_empty=True,
            primary_key_correct=False,
            distinct_attribute_match=True,
            filterable_attributes_match=True,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        warn_cb = Mock()

        reconcile_index(warn_cb=warn_cb)

        mock_reset.assert_called_once()
        warn_cb.assert_any_call("Primary key mismatch on empty index. Recreating...")

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    def test_index_populated_configured(self, mock_drift, mock_meilisearch):
        """When index is populated and configured, no action taken."""
        mock_drift.return_value = IndexDrift(
            exists=True,
            is_empty=False,
            primary_key_correct=True,
            distinct_attribute_match=True,
            filterable_attributes_match=True,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        status_cb = Mock()

        reconcile_index(status_cb=status_cb)

        status_cb.assert_any_call("Index is populated and correctly configured. No action needed.")

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    @patch("openedx.core.djangoapps.content.search.api._apply_index_settings")
    def test_index_populated_drifted_settings(self, mock_apply, mock_drift, mock_meilisearch):
        """When index is populated and drifted, settings are applied with warnings."""
        mock_drift.return_value = IndexDrift(
            exists=True,
            is_empty=False,
            primary_key_correct=True,
            distinct_attribute_match=True,
            filterable_attributes_match=False,
            searchable_attributes_match=False,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        status_cb = Mock()
        warn_cb = Mock()

        reconcile_index(status_cb=status_cb, warn_cb=warn_cb)

        mock_apply.assert_called_once_with(api.STUDIO_INDEX_NAME, wait=True, status_cb=status_cb)
        # Check that drifted fields are logged
        warn_cb.assert_any_call("  - filterableAttributes: DRIFTED")
        warn_cb.assert_any_call("  - searchableAttributes: DRIFTED")
        # Check that non-drifted fields are NOT logged as drifted
        drifted_calls = [c for c in warn_cb.call_args_list if "DRIFTED" in str(c)]
        assert len(drifted_calls) == 2

    @patch("openedx.core.djangoapps.content.search.api._detect_index_drift")
    @patch("openedx.core.djangoapps.content.search.api.reset_index")
    def test_index_populated_wrong_pk(self, mock_reset, mock_drift, mock_meilisearch):
        """When index is populated with wrong PK, reset_index is called (destructive)."""
        mock_drift.return_value = IndexDrift(
            exists=True,
            is_empty=False,
            primary_key_correct=False,
            distinct_attribute_match=True,
            filterable_attributes_match=True,
            searchable_attributes_match=True,
            sortable_attributes_match=True,
            ranking_rules_match=True,
        )
        warn_cb = Mock()

        reconcile_index(warn_cb=warn_cb)

        mock_reset.assert_called_once()
        # Should warn about data loss
        warn_cb.assert_any_call("Index recreated empty. Run './manage.py cms reindex_studio' to repopulate.")

    @override_settings(MEILISEARCH_ENABLED=False)
    def test_meilisearch_disabled(self, mock_meilisearch):
        """When Meilisearch is disabled, reconcile_index raises RuntimeError (from client)."""
        api.clear_meilisearch_client()
        with pytest.raises(RuntimeError):
            reconcile_index()


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestHandlePostMigrate(TestCase):
    """Tests for the handle_post_migrate signal handler."""

    def setUp(self):
        super().setUp()
        api.clear_meilisearch_client()

    @patch("openedx.core.djangoapps.content.search.handlers.reconcile_index")
    def test_calls_reconcile_for_search_app(self, mock_reconcile, mock_meilisearch):
        """Handler calls reconcile_index when sender is the search app."""
        sender = Mock()
        sender.label = ContentSearchConfig.label

        handle_post_migrate(sender=sender)

        mock_reconcile.assert_called_once()

    @patch("openedx.core.djangoapps.content.search.handlers.reconcile_index")
    def test_skips_wrong_sender(self, mock_reconcile, mock_meilisearch):
        """Handler does nothing when sender is a different app."""
        sender = Mock()
        sender.label = "some_other_app"

        handle_post_migrate(sender=sender)

        mock_reconcile.assert_not_called()

    @override_settings(MEILISEARCH_ENABLED=False)
    @patch("openedx.core.djangoapps.content.search.handlers.reconcile_index")
    def test_skips_when_disabled(self, mock_reconcile, mock_meilisearch):
        """Handler does nothing when Meilisearch is disabled."""
        sender = Mock()
        sender.label = ContentSearchConfig.label

        handle_post_migrate(sender=sender)

        mock_reconcile.assert_not_called()

    @patch("openedx.core.djangoapps.content.search.handlers.reconcile_index")
    def test_catches_connection_error(self, mock_reconcile, mock_meilisearch):
        """Handler catches ConnectionError and logs warning."""
        sender = Mock()
        sender.label = ContentSearchConfig.label
        mock_reconcile.side_effect = ConnectionError("Cannot connect")

        # Should not raise
        handle_post_migrate(sender=sender)

    @patch("openedx.core.djangoapps.content.search.handlers.reconcile_index")
    def test_catches_meilisearch_error(self, mock_reconcile, mock_meilisearch):
        """Handler catches MeilisearchError and logs warning."""
        sender = Mock()
        sender.label = ContentSearchConfig.label
        mock_reconcile.side_effect = MeilisearchError("Something went wrong")

        # Should not raise
        handle_post_migrate(sender=sender)

    @patch("openedx.core.djangoapps.content.search.handlers.reconcile_index")
    def test_catches_generic_exception(self, mock_reconcile, mock_meilisearch):
        """Handler catches unexpected exceptions and logs warning."""
        sender = Mock()
        sender.label = ContentSearchConfig.label
        mock_reconcile.side_effect = RuntimeError("Unexpected")

        # Should not raise
        handle_post_migrate(sender=sender)

    def test_signal_connected(self, mock_meilisearch):
        """Verify post_migrate signal is connected to handle_post_migrate."""
        from django.db.models.signals import post_migrate

        from ..handlers import handle_post_migrate as handler_fn

        # Check that the handler is in the receivers
        receiver_funcs = [r[1]() for r in post_migrate.receivers if r[1]() is not None]
        assert handler_fn in receiver_funcs, "handle_post_migrate should be connected to post_migrate signal"


@skip_unless_cms
@override_settings(MEILISEARCH_ENABLED=True)
@patch("openedx.core.djangoapps.content.search.api._wait_for_meili_task", new=MagicMock(return_value=None))
@patch("openedx.core.djangoapps.content.search.api.MeilisearchClient")
class TestInitIndexBackwardCompat(TestCase):
    """Tests that init_index() still works as a compatibility wrapper."""

    def setUp(self):
        super().setUp()
        api.clear_meilisearch_client()

    @patch("openedx.core.djangoapps.content.search.api.reconcile_index")
    def test_init_index_delegates_to_reconcile(self, mock_reconcile, mock_meilisearch):
        """init_index() should delegate to reconcile_index()."""
        status_cb = Mock()
        warn_cb = Mock()

        api.init_index(status_cb=status_cb, warn_cb=warn_cb)

        mock_reconcile.assert_called_once_with(status_cb=status_cb, warn_cb=warn_cb)
