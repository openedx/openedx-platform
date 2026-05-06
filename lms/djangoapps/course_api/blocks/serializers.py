"""
Serializers for Course Blocks related return objects.
"""

from django.conf import settings
from rest_framework import serializers
from rest_framework.reverse import reverse

from lms.djangoapps.course_blocks.transformers.hidden_content import HiddenContentTransformer
from lms.djangoapps.course_blocks.transformers.visibility import VisibilityTransformer
from openedx.core.djangoapps.discussions.transformers import DiscussionsTopicLinkTransformer

from .transformers.block_completion import BlockCompletionTransformer
from .transformers.block_counts import BlockCountsTransformer
from .transformers.extra_fields import ExtraFieldsTransformer
from .transformers.milestones import MilestonesAndSpecialExamsTransformer
from .transformers.navigation import BlockNavigationTransformer
from .transformers.student_view import StudentViewTransformer


class SupportedFieldType:
    """
    Metadata about fields supported by different transformers
    """

    def __init__(
        self,
        block_field_name,
        transformer=None,
        requested_field_name=None,
        serializer_field_name=None,
        default_value=None
    ):
        self.transformer = transformer
        self.block_field_name = block_field_name
        self.requested_field_name = requested_field_name or block_field_name
        self.serializer_field_name = serializer_field_name or self.requested_field_name
        self.default_value = default_value


# A list of metadata for additional requested fields to be used by the
# BlockSerializer` class.  Each entry provides information on how that field can
# be requested (`requested_field_name`), can be found (`transformer` and
# `block_field_name`), and should be serialized (`serializer_field_name` and
# `default_value`).

SUPPORTED_FIELDS = [
    SupportedFieldType('category', requested_field_name='type'),
    SupportedFieldType('display_name', default_value=''),
    SupportedFieldType('effort_activities'),
    SupportedFieldType('effort_time'),
    SupportedFieldType('graded'),
    SupportedFieldType('format'),
    SupportedFieldType('start'),
    SupportedFieldType('due'),
    SupportedFieldType('contains_gated_content'),
    SupportedFieldType('has_score'),
    SupportedFieldType('has_scheduled_content'),
    SupportedFieldType('weight'),
    SupportedFieldType('show_correctness'),
    SupportedFieldType('hide_from_toc'),
    SupportedFieldType('icon_class'),
    # 'student_view_data'
    SupportedFieldType(StudentViewTransformer.STUDENT_VIEW_DATA, StudentViewTransformer),
    # 'student_view_multi_device'
    SupportedFieldType(StudentViewTransformer.STUDENT_VIEW_MULTI_DEVICE, StudentViewTransformer),

    SupportedFieldType('special_exam_info', MilestonesAndSpecialExamsTransformer),

    # set the block_field_name to None so the entire data for the transformer is serialized
    SupportedFieldType(None, BlockCountsTransformer, BlockCountsTransformer.BLOCK_COUNTS),

    SupportedFieldType(
        BlockNavigationTransformer.BLOCK_NAVIGATION,
        BlockNavigationTransformer,
        requested_field_name='nav_depth',
        serializer_field_name='descendants',
    ),

    # Provide the staff visibility info stored when VisibilityTransformer ran previously
    SupportedFieldType(
        'merged_visible_to_staff_only',
        VisibilityTransformer,
        requested_field_name='visible_to_staff_only',
    ),

    SupportedFieldType(
        'merged_hide_after_due',
        HiddenContentTransformer,
        requested_field_name='hide_after_due'
    ),

    SupportedFieldType(BlockCompletionTransformer.COMPLETION, BlockCompletionTransformer),
    SupportedFieldType(BlockCompletionTransformer.COMPLETE),
    SupportedFieldType(BlockCompletionTransformer.RESUME_BLOCK),
    SupportedFieldType(DiscussionsTopicLinkTransformer.EXTERNAL_ID),
    SupportedFieldType(DiscussionsTopicLinkTransformer.EMBED_URL),

    *[SupportedFieldType(field_name) for field_name in ExtraFieldsTransformer.get_requested_extra_fields()],
]

# This lists the names of all fields that are allowed
# to be show to users who do not have access to a particular piece
# of content
FIELDS_ALLOWED_IN_AUTH_DENIED_CONTENT = [
    "display_name",
    "block_id",
    "student_view_url",
    "student_view_multi_device",
    "lms_web_url",
    "legacy_web_url",
    "type",
    "id",
    "block_counts",
    "graded",
    "descendants",
    "authorization_denial_reason",
    "authorization_denial_message",
    'contains_gated_content',
]


class BlockSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer for single course block
    """

    def _get_field(self, block_key, transformer, field_name, default):
        """
        Get the field value requested.  The field may be an XBlock field, a
        transformer block field, or an entire tranformer block data dict.
        """
        value = None
        if transformer is None:
            value = self.context['block_structure'].get_xblock_field(block_key, field_name)
        elif field_name is None:
            try:
                value = self.context['block_structure'].get_transformer_block_data(block_key, transformer).fields
            except KeyError:
                pass
        else:
            value = self.context['block_structure'].get_transformer_block_field(block_key, transformer, field_name)

        return value if (value is not None) else default

    def to_representation(self, block_key):  # pylint: disable=arguments-differ
        """
        Return a serializable representation of the requested block
        """
        # create response data dict for basic fields

        block_structure = self.context['block_structure']
        authorization_denial_reason = block_structure.get_xblock_field(block_key, 'authorization_denial_reason')
        authorization_denial_message = block_structure.get_xblock_field(block_key, 'authorization_denial_message')

        jump_to_courseware_url = reverse(
            'jump_to',
            kwargs={
                'course_id': str(block_key.course_key),
                'location': str(block_key),
            },
            request=self.context['request'],
        )

        data = {
            'id': str(block_key),
            'block_id': str(block_key.block_id),
            'lms_web_url': jump_to_courseware_url,
            'legacy_web_url': jump_to_courseware_url + '?experience=legacy',
            'student_view_url': reverse(
                'render_xblock',
                kwargs={'usage_key_string': str(block_key)},
                request=self.context['request'],
            ),
        }

        if settings.FEATURES.get("ENABLE_LTI_PROVIDER") and 'lti_url' in self.context['requested_fields']:
            data['lti_url'] = reverse(
                'lti_provider_launch',
                kwargs={'course_id': str(block_key.course_key), 'usage_id': str(block_key)},
                request=self.context['request'],
            )

        # add additional requested fields that are supported by the various transformers
        for supported_field in SUPPORTED_FIELDS:
            if supported_field.requested_field_name in self.context['requested_fields']:
                field_value = self._get_field(
                    block_key,
                    supported_field.transformer,
                    supported_field.block_field_name,
                    supported_field.default_value,
                )
                if field_value is not None:
                    # only return fields that have data
                    data[supported_field.serializer_field_name] = field_value

        if 'children' in self.context['requested_fields']:
            children = block_structure.get_children(block_key)
            if children:
                data['children'] = [str(child) for child in children]

        if authorization_denial_reason and authorization_denial_message:
            data['authorization_denial_reason'] = authorization_denial_reason
            data['authorization_denial_message'] = authorization_denial_message
            cleaned_data = data.copy()
            for field in data.keys():  # pylint: disable=consider-iterating-dictionary
                if field not in FIELDS_ALLOWED_IN_AUTH_DENIED_CONTENT:
                    del cleaned_data[field]
            data = cleaned_data

        return data


class BlockDictSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializer that formats a BlockStructure object to a dictionary, rather
    than a list, of blocks
    """
    root = serializers.CharField(source='root_block_usage_key')
    blocks = serializers.SerializerMethodField()

    def get_blocks(self, structure):
        """
        Serialize to a dictionary of blocks keyed by the block's usage_key.
        """
        return {
            str(block_key): BlockSerializer(block_key, context=self.context).data
            for block_key in structure
        }
