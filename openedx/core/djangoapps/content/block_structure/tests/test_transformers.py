"""
Tests for transformers.py
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest

from ..block_structure import BlockStructureModulestoreData
from ..exceptions import TransformerDataIncompatible, TransformerException
from ..transformers import BlockStructureTransformers
from .helpers import ChildrenMapTestMixin, MockFilteringTransformer, MockTransformer, mock_registered_transformers


class TestBlockStructureTransformers(ChildrenMapTestMixin, TestCase):
    """
    Test class for testing BlockStructureTransformers
    """

    class UnregisteredTransformer(MockTransformer):
        """
        Mock transformer that is not registered.
        """
        pass  # pylint: disable=unnecessary-pass

    def setUp(self):
        super().setUp()
        self.transformers = BlockStructureTransformers(usage_info=MagicMock())
        self.registered_transformers = [MockTransformer(), MockFilteringTransformer()]

    def add_mock_transformer(self):
        """
        Adds the registered transformers to the self.transformers collection.
        """
        with mock_registered_transformers(self.registered_transformers):
            self.transformers += self.registered_transformers

    def test_add_registered(self):
        self.add_mock_transformer()
        assert self.registered_transformers[0] in self.transformers._transformers['no_filter']  # pylint: disable=protected-access, line-too-long
        assert self.registered_transformers[1] in self.transformers._transformers['supports_filter']  # pylint: disable=protected-access, line-too-long

    def test_add_unregistered(self):
        with pytest.raises(TransformerException):
            self.transformers += [self.UnregisteredTransformer()]

        assert self.transformers._transformers['no_filter'] == []  # pylint: disable=protected-access
        assert self.transformers._transformers['supports_filter'] == []  # pylint: disable=protected-access

    def test_collect(self):
        with mock_registered_transformers(self.registered_transformers):
            with patch(
                'openedx.core.djangoapps.content.block_structure.tests.helpers.MockTransformer.collect'
            ) as mock_collect_call:
                BlockStructureTransformers.collect(block_structure=MagicMock())
                assert mock_collect_call.called

    def test_transform(self):
        self.add_mock_transformer()

        with patch(
            'openedx.core.djangoapps.content.block_structure.tests.helpers.MockTransformer.transform'
        ) as mock_transform_call:
            self.transformers.transform(block_structure=MagicMock())
            assert mock_transform_call.called

    def test_verify_versions(self):
        block_structure = self.create_block_structure(
            self.SIMPLE_CHILDREN_MAP,
            BlockStructureModulestoreData
        )

        with mock_registered_transformers(self.registered_transformers):
            with pytest.raises(TransformerDataIncompatible):
                self.transformers.verify_versions(block_structure)
            self.transformers.collect(block_structure)
            assert self.transformers.verify_versions(block_structure)
