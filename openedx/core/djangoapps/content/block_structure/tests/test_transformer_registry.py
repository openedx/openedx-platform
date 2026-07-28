"""
Tests for transformer_registry.py
"""


from unittest import TestCase

import ddt

from ..transformer_registry import TransformerRegistry
from .helpers import MockTransformer, clear_registered_transformers_cache, mock_registered_transformers


class TestTransformer1(MockTransformer):
    """
    1st test instance of the MockTransformer that is registered.
    """
    pass  # pylint: disable=unnecessary-pass


class TestTransformer2(MockTransformer):
    """
    2nd test instance of the MockTransformer that is registered.
    """
    pass  # pylint: disable=unnecessary-pass


class UnregisteredTestTransformer3(MockTransformer):
    """
    3rd test instance of the MockTransformer that is not registered.
    """
    pass  # pylint: disable=unnecessary-pass


@ddt.ddt
class TransformerRegistryTestCase(TestCase):
    """
    Test cases for TransformerRegistry.
    """

    def tearDown(self):
        super().tearDown()
        clear_registered_transformers_cache()

    @ddt.data(
        # None case
        ([], []),

        # 1 registered
        ([TestTransformer1()], []),

        # 2 registered
        ([TestTransformer1(), TestTransformer2()], []),

        # 1 unregistered
        ([UnregisteredTestTransformer3()], [UnregisteredTestTransformer3.name()]),

        # 1 registered and 1 unregistered
        ([TestTransformer1(), UnregisteredTestTransformer3()], [UnregisteredTestTransformer3.name()]),
    )
    @ddt.unpack
    def test_find_unregistered(self, transformers, expected_unregistered):

        with mock_registered_transformers([TestTransformer1, TestTransformer2]):
            self.assertSetEqual(  # noqa: PT009
                TransformerRegistry.find_unregistered(transformers),
                set(expected_unregistered),
            )

    def test_write_version_hash(self):
        # hash with TestTransformer1
        with mock_registered_transformers([TestTransformer1]):
            version_hash_1 = TransformerRegistry.get_write_version_hash()
            assert version_hash_1 == '+2nc5o2YRerVfAtItQBQ/6jVkkw='

            # should return the same value again
            assert version_hash_1 == TransformerRegistry.get_write_version_hash()

        # hash with TestTransformer1 and TestTransformer2
        with mock_registered_transformers([TestTransformer1, TestTransformer2]):
            version_hash_2 = TransformerRegistry.get_write_version_hash()
            assert version_hash_2 == '5GwhvmSM9hknjUslzPnKDA5QaCo='
            assert version_hash_1 != version_hash_2
