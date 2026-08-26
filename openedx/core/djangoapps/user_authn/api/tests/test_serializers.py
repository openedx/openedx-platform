"""
Tests for serializers for the MFE Context
"""

from django.test import TestCase

from openedx.core.djangoapps.user_authn.api.tests.data_mock import (
    MFE_CONTEXT_WITH_EXTRA_CONTEXT_DATA,
    MFE_CONTEXT_WITH_TPA_DATA,
    MFE_CONTEXT_WITHOUT_TPA_DATA,
    SERIALIZED_MFE_CONTEXT_WITH_EXTRA_CONTEXT_DATA,
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

        self.assertDictEqual(  # noqa: PT009
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

        self.assertDictEqual(  # noqa: PT009
            serialized_data,
            SERIALIZED_MFE_CONTEXT_WITHOUT_TPA_DATA
        )

    def test_mfe_context_serializer_with_extra_context(self):
        """
        Test that entries the serializer declares no fields for are merged into contextData
        from extra_context, preserving their nesting.
        """
        output_data = MFEContextSerializer(
            MFE_CONTEXT_WITH_EXTRA_CONTEXT_DATA
        ).data

        assert output_data == SERIALIZED_MFE_CONTEXT_WITH_EXTRA_CONTEXT_DATA

    def test_mfe_context_serializer_ignores_undeclared_context_keys(self):
        """
        Test that context entries outside extra_context are still dropped, so the response
        shape stays under the serializer's control.
        """
        context = {
            'context_data': {
                **MFE_CONTEXT_WITHOUT_TPA_DATA['context_data'],
                'skipRegistrationOptionalCheckboxes': True,
            },
        }

        serialized_data = MFEContextSerializer(context).data

        assert 'skipRegistrationOptionalCheckboxes' not in serialized_data['contextData']
