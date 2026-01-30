"""
Grades Transformer
"""


import json
from base64 import b64encode
from functools import reduce as functools_reduce
from hashlib import sha1
from logging import getLogger

from lms.djangoapps.course_blocks.transformers.utils import collect_unioned_set_field, get_field_on_block
from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer

log = getLogger(__name__)


class GradesTransformer(BlockStructureTransformer):
    """
    The GradesTransformer collects grading information and stores it on
    the block structure.

    No runtime transformations are performed.

    The following values are stored as xblock_fields on their respective blocks
    in the block structure:

        due: (datetime) when the problem is due.
        format: (string) what type of problem it is
        graded: (boolean)
        has_score: (boolean)
        weight: (numeric)
        show_correctness: (string) when to show grades (one of 'always', 'past_due', 'never')

    Additionally, the following value is calculated and stored as a
    transformer_block_field for each block:

        max_score: (numeric)
    """
    WRITE_VERSION = 4
    READ_VERSION = 4
    FIELDS_TO_COLLECT = [
        'due',
        'format',
        'graded',
        'has_score',
        'weight',
        'course_version',
        'subtree_edited_on',
        'show_correctness',
    ]

    EXPLICIT_GRADED_FIELD_NAME = 'explicit_graded'

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return 'grades'

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        block_structure.request_xblock_fields(*cls.FIELDS_TO_COLLECT)
        cls._collect_max_scores(block_structure)
        collect_unioned_set_field(
            block_structure=block_structure,
            transformer=cls,
            merged_field_name='subsections',
            filter_by=lambda block_key: block_key.block_type == 'sequential',
        )
        cls._collect_explicit_graded(block_structure)
        cls._collect_grading_policy_hash(block_structure)

    def transform(self, block_structure, usage_context):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Perform no transformations.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @classmethod
    def grading_policy_hash(cls, course):
        """
        Returns the grading policy hash for the given course.
        """
        ordered_policy = json.dumps(
            course.grading_policy,
            separators=(',', ':'),  # Remove spaces from separators for more compact representation
            sort_keys=True,
        )
        return b64encode(sha1(ordered_policy.encode('utf-8')).digest()).decode('utf-8')

    @classmethod
    def _collect_explicit_graded(cls, block_structure):
        """
        Collect the 'explicit_graded' field for every block.
        """
        def _set_field(block_key, field_value):
            """
            Sets the explicit graded field to the given value for the
            given block.
            """
            block_structure.set_transformer_block_field(block_key, cls, cls.EXPLICIT_GRADED_FIELD_NAME, field_value)

        def _get_field(block_key):
            """
            Gets the explicit graded field to the given value for the
            given block.
            """
            return block_structure.get_transformer_block_field(block_key, cls, cls.EXPLICIT_GRADED_FIELD_NAME)

        block_types_to_ignore = {'course', 'chapter', 'sequential'}

        for block_key in block_structure.topological_traversal():
            if block_key.block_type in block_types_to_ignore:
                _set_field(block_key, None)
            else:
                explicit_field_on_block = get_field_on_block(block_structure.get_xblock(block_key), 'graded')
                if explicit_field_on_block is not None:
                    _set_field(block_key, explicit_field_on_block)
                else:
                    values_from_parents = [
                        _get_field(parent)
                        for parent in block_structure.get_parents(block_key)
                        if parent.block_type not in block_types_to_ignore
                    ]
                    non_null_values_from_parents = [value for value in values_from_parents if not None]
                    explicit_from_parents = functools_reduce(lambda x, y: x or y, non_null_values_from_parents, None)
                    _set_field(block_key, explicit_from_parents)

    @classmethod
    def _collect_max_scores(cls, block_structure):
        """
        Collect the `max_score` for every block in the provided `block_structure`.
        """
        for block_locator in block_structure.post_order_traversal():
            block = block_structure.get_xblock(block_locator)
            if getattr(block, 'has_score', False):
                cls._collect_max_score(block_structure, block)

    @classmethod
    def _collect_max_score(cls, block_structure, block):
        """
        Collect the `max_score` from the given block, storing it as a
        `transformer_block_field` associated with the `GradesTransformer`.
        """
        max_score = block.max_score()
        block_structure.set_transformer_block_field(block.location, cls, 'max_score', max_score)
        if max_score is None:
            log.warning(f"GradesTransformer: max_score is None for {block.location}")

    @classmethod
    def _collect_grading_policy_hash(cls, block_structure):
        """
        Collect a hash of the course's grading policy, storing it as a
        `transformer_block_field` associated with the `GradesTransformer`.
        """
        course_location = block_structure.root_block_usage_key
        course_block = block_structure.get_xblock(course_location)
        block_structure.set_transformer_block_field(
            course_block.location,
            cls,
            "grading_policy_hash",
            cls.grading_policy_hash(course_block),
        )
