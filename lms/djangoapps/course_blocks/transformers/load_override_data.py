"""
Load Override Data Transformer
"""


import json

from lms.djangoapps.courseware.models import StudentFieldOverride
from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer

# The list of fields are in support of Individual due dates and could be expanded for other use cases.
REQUESTED_FIELDS = [
    'start',
    'display_name',
    'due'
]


def _get_override_query(course_key, location_list, user_id):
    """
    returns queryset containing override data.

    Args:
        course_key (CourseLocator): Course locator object
        location_list (List<UsageKey>): List of usage key of all blocks
        user_id (int): User id
    """
    return StudentFieldOverride.objects.filter(
        course_id=course_key,
        location__in=location_list,
        field__in=REQUESTED_FIELDS,
        student__id=user_id
    )


def override_xblock_fields(course_key, location_list, block_structure, user_id):
    """
    loads override data of block

    Args:
        course_key (CourseLocator): course locator object
        location_list (List<UsageKey>): list of usage key of all blocks
        block_structure (BlockStructure): block structure class
        user_id (int): User id
    """
    query = _get_override_query(course_key, location_list, user_id)
    for student_field_override in query:
        value = json.loads(student_field_override.value)
        field = student_field_override.field
        block_structure.override_xblock_field(
            student_field_override.location,
            field,
            value
        )


class OverrideDataTransformer(BlockStructureTransformer):
    """
    A transformer that load override data in xblock.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    def __init__(self, user):
        self.user = user

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "load_override_data"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this transformer's transform method.
        """
        # collect basic xblock fields
        block_structure.request_xblock_fields(*REQUESTED_FIELDS)

    def transform(self, usage_info, block_structure):
        """
        loads override data into blocks
        """
        override_xblock_fields(
            usage_info.course_key,
            block_structure.topological_traversal(),
            block_structure,
            self.user.id
        )
