"""
Tests for serializers for the MFE Context
"""

from django.test import TestCase

from openedx.core.djangoapps.user_authn.api.tests.data_mock import (
    MFE_CONTEXT_WITH_TPA_DATA,
    MFE_CONTEXT_WITHOUT_TPA_DATA,
    SERIALIZED_MFE_CONTEXT_WITH_TPA_DATA,
    SERIALIZED_MFE_CONTEXT_WITHOUT_TPA_DATA,
)
from openedx.core.djangoapps.user_authn.serializers import MFEContextSerializer


class TestMFEContextSerializer(TestCase):
    """
    High-level unit tests for MFEContextSerializer
    """

    def test_mfe_context_serializer(self):
        """
        Test MFEContextSerializer with mock data that serializes data correctly
        """

        output_data = MFEContextSerializer(
            MFE_CONTEXT_WITH_TPA_DATA
        ).data

        self.assertDictEqual(
            output_data,
            SERIALIZED_MFE_CONTEXT_WITH_TPA_DATA
        )

    def test_mfe_context_serializer_default_response(self):
        """
        Test MFEContextSerializer with default data
        """
        serialized_data = MFEContextSerializer(
            MFE_CONTEXT_WITHOUT_TPA_DATA
        ).data

        self.assertDictEqual(
            serialized_data,
            SERIALIZED_MFE_CONTEXT_WITHOUT_TPA_DATA
        )
