"""
Tests for model_mixins.py.
"""

from unittest import TestCase, mock

import ddt

from openedx.core.djangolib.model_mixins import DeletableByUserValue


@ddt.ddt
class TestDeletableByUserValue(TestCase):
    """
    Unit tests for DeletableByUserValue.
    """

    class NonRedactingModel(DeletableByUserValue):
        """
        Dummy model that uses default redaction behavior.
        """

    class RedactingModel(DeletableByUserValue):
        """
        Dummy model that overrides redaction fields.
        """

        @classmethod
        def redact_before_delete_fields(cls):
            return {'email': 'redacted@retired.invalid'}

    def _make_queryset(self, exists):
        """
        Return a mock queryset with ``exists`` and ``values_list`` pre-configured.
        """
        queryset = mock.Mock()
        queryset.exists.return_value = exists
        queryset.values_list.return_value = [11, 12] if exists else []
        return queryset

    def test_redact_before_delete_fields_defaults_to_empty_dict(self):
        """
        Verify the default redaction hook does not request any field updates.
        """
        assert not self.NonRedactingModel.redact_before_delete_fields()

    def test_delete_by_user_value_returns_false_when_no_matches(self):
        """
        Verify no updates or deletes occur when no rows match the filter.
        """
        queryset = self._make_queryset(exists=False)
        with mock.patch.object(self.NonRedactingModel, 'objects', create=True) as mock_objects:
            mock_objects.filter.return_value = queryset

            was_deleted = self.NonRedactingModel.delete_by_user_value(value='missing@example.com', field='email')

        assert not was_deleted
        mock_objects.filter.assert_called_once_with(email='missing@example.com')
        queryset.update.assert_not_called()
        queryset.delete.assert_not_called()

    @ddt.data(
        ('NonRedactingModel', None),
        ('RedactingModel', {'email': 'redacted@retired.invalid'}),
    )
    @ddt.unpack
    def test_delete_by_user_value(self, model_name, expected_redact_fields):
        """
        Verify delete behavior with and without redaction configured.

        When no redaction hook is set, rows are deleted directly.
        When a redaction hook is set, fields are updated before deletion.
        """
        model_cls = getattr(self, model_name)
        queryset = self._make_queryset(exists=True)
        with mock.patch.object(model_cls, 'objects', create=True) as mock_objects:
            mock_objects.filter.return_value = queryset

            was_deleted = model_cls.delete_by_user_value(value='learner@example.com', field='email')

        assert was_deleted
        assert mock_objects.filter.call_args_list == [
            mock.call(email='learner@example.com'),
            mock.call(id__in=[11, 12]),
        ]
        if expected_redact_fields:
            queryset.update.assert_called_once_with(**expected_redact_fields)
        else:
            queryset.update.assert_not_called()
        queryset.delete.assert_called_once_with()
