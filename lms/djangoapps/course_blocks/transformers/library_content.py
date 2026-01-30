"""
Item Bank Transformer.

Transformers for handling item bank blocks (library_content, itembank, etc.)
that use ItemBankMixin for randomized content selection.
"""


import json
import logging

from eventtracking import tracker
from xblock.core import XBlock

from common.djangoapps.track import contexts
from lms.djangoapps.courseware.models import StudentModule
from openedx.core.djangoapps.content.block_structure.transformer import (
    BlockStructureTransformer,
    FilteringTransformerMixin
)
from xmodule.item_bank_block import ItemBankMixin  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

from ..utils import get_student_module_as_dict

logger = logging.getLogger(__name__)

# Module-level cache to avoid repeated XBlock.load_class() calls
_BLOCK_CLASS_CACHE = {}


def _get_block_class(block_type: str):
    """
    Get the XBlock class from a block type.
    """
    if block_type not in _BLOCK_CLASS_CACHE:
        try:
            _BLOCK_CLASS_CACHE[block_type] = XBlock.load_class(block_type)
        except Exception:  # lint-amnesty, pylint: disable=broad-except
            logger.exception('Failed to load block class for type %s', block_type)
            _BLOCK_CLASS_CACHE[block_type] = None
    return _BLOCK_CLASS_CACHE[block_type]


class ContentLibraryTransformer(FilteringTransformerMixin, BlockStructureTransformer):
    """
    A transformer that manipulates the block structure by removing all
    blocks within item bank blocks (library_content, itembank, etc.)
    to which a user should not have access.

    This transformer works with any XBlock that inherits from ItemBankMixin,
    filtering children based on the selection logic defined by each block type.

    Staff users are not to be exempted from item bank pathways.
    """
    WRITE_VERSION = 2
    READ_VERSION = 1

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class;
        same identifier used in setup.py.
        """
        return "library_content"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        block_structure.request_xblock_fields('max_count')
        block_structure.request_xblock_fields('category')
        store = modulestore()

        # needed for analytics purposes
        def summarize_block(usage_key):
            """ Basic information about the given block """
            orig_key, orig_version = store.get_block_original_usage(usage_key)
            return {
                "usage_key": str(usage_key),
                "original_usage_key": str(orig_key) if orig_key else None,
                "original_usage_version": str(orig_version) if orig_version else None,
            }

        # For each block check if block uses ItemBankMixin (e.g., library_content, itembank).
        # Mark these blocks and collect analytics data for their children.
        for block_key in block_structure.topological_traversal(
                yield_descendants_of_unyielded=True,
        ):
            block_class = _get_block_class(block_key.block_type)
            is_item_bank = block_class and issubclass(block_class, ItemBankMixin)

            if is_item_bank:
                block_structure.set_transformer_block_field(block_key, cls, 'is_item_bank_block', True)
                xblock = block_structure.get_xblock(block_key)
                for child_key in xblock.children:
                    summary = summarize_block(child_key)
                    block_structure.set_transformer_block_field(child_key, cls, 'block_analytics_summary', summary)

    def transform_block_filters(self, usage_info, block_structure):
        all_library_children = set()
        all_selected_children = set()
        for block_key in block_structure:
            # Check if this block was marked as an ItemBankMixin block during collect
            is_item_bank = block_structure.get_transformer_block_field(block_key, self, 'is_item_bank_block')
            # Fallback for old cache that doesn't have the 'is_item_bank_block' field
            if is_item_bank is None:
                block_class = _get_block_class(block_key.block_type)
                is_item_bank = block_class and issubclass(block_class, ItemBankMixin)

            if not is_item_bank:
                continue
            library_children = block_structure.get_children(block_key)
            if library_children:
                all_library_children.update(library_children)
                selected = []
                max_count = block_structure.get_xblock_field(block_key, 'max_count')
                if max_count < 0:
                    max_count = len(library_children)

                # Retrieve "selected" json from LMS MySQL database.
                state_dict = get_student_module_as_dict(usage_info.user, usage_info.course_key, block_key)
                for selected_block in state_dict.get('selected', []):
                    # Add all selected entries for this user for this
                    # library block to the selected list.
                    block_type, block_id = selected_block
                    usage_key = usage_info.course_key.make_usage_key(block_type, block_id)
                    if usage_key in library_children:
                        selected.append(selected_block)

                # Update selected
                previous_count = len(selected)
                # Get the cached block class to call make_selection
                block_class = _get_block_class(block_key.block_type)
                if not block_class:
                    logger.error('Failed to load block class for %s', block_key)
                    continue
                block_keys = block_class.make_selection(selected, library_children, max_count)
                selected = block_keys['selected']

                # Save back any changes
                if any(block_keys[changed] for changed in ('invalid', 'overlimit', 'added')):
                    state_dict['selected'] = selected
                    StudentModule.save_state(
                        student=usage_info.user,
                        course_id=usage_info.course_key,
                        module_state_key=block_key,
                        defaults={
                            'state': json.dumps(state_dict),
                        },
                    )

                # publish events for analytics
                self._publish_events(
                    block_structure,
                    block_key,
                    previous_count,
                    max_count,
                    block_keys,
                    usage_info.user.id,
                )
                all_selected_children.update(usage_info.course_key.make_usage_key(s[0], s[1]) for s in selected)

        def check_child_removal(block_key):
            """
            Return True if selected block should be removed.

            Block is removed if it is a child of an item bank block, but has
            not been selected for current user.
            """
            if block_key not in all_library_children:
                return False
            if block_key in all_selected_children:
                return False
            return True

        return [block_structure.create_removal_filter(check_child_removal)]

    def _publish_events(self, block_structure, location, previous_count, max_count, block_keys, user_id):
        """
        Helper method to publish events for analytics purposes
        """

        def format_block_keys(keys):
            """
            Helper function to format block keys
            """
            json_result = []
            for key in keys:
                info = block_structure.get_transformer_block_field(
                    key, ContentLibraryTransformer, 'block_analytics_summary'
                )
                json_result.append(info)
            return json_result

        # Get the cached block class to call publish_selected_children_events
        block_class = _get_block_class(location.block_type)
        if not block_class:
            logger.error('Failed to load block class for publishing events: %s', location)
            return

        def publish_event(event_name, result, **kwargs):
            """
            Helper function to publish an event for analytics purposes
            """
            event_data = {
                "location": str(location),
                "previous_count": previous_count,
                "result": result,
                "max_count": max_count,
            }
            event_data.update(kwargs)
            context = contexts.course_context_from_course_id(location.course_key)
            if user_id:
                context['user_id'] = user_id
            event_prefix = block_class.get_selected_event_prefix()
            full_event_name = f"{event_prefix}.{event_name}"
            with tracker.get_tracker().context(full_event_name, context):
                tracker.emit(full_event_name, event_data)

        block_class.publish_selected_children_events(
            block_keys,
            format_block_keys,
            publish_event,
        )


class ContentLibraryOrderTransformer(BlockStructureTransformer):
    """
    A transformer that manipulates the block structure by modifying the order of the
    selected blocks within item bank blocks (library_content, itembank, etc.)
    to match the order of the selections made by the ContentLibraryTransformer or the
    corresponding XBlock. This transformer requires the selections for the item bank block
    to be already made either by the ContentLibraryTransformer or the XBlock.

    This transformer works with any XBlock that inherits from ItemBankMixin.

    Staff users are *not* exempted from item bank pathways.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class;
        same identifier used in setup.py
        """
        return "library_content_randomize"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        # There is nothing to collect
        pass  # pylint:disable=unnecessary-pass

    def transform(self, usage_info, block_structure):
        """
        Transforms the order of the children of the randomized content block
        to match the order of the selections made and stored in the XBlock 'selected' field.
        """
        for block_key in block_structure:
            # Try to read from cache (collected by ContentLibraryTransformer)
            is_item_bank = block_structure.get_transformer_block_field(
                block_key,
                ContentLibraryTransformer,
                'is_item_bank_block'
            )

            # Fallback for old cache that doesn't have the 'is_item_bank_block' field
            if is_item_bank is None:
                block_class = _get_block_class(block_key.block_type)
                is_item_bank = block_class and issubclass(block_class, ItemBankMixin)

            if not is_item_bank:
                continue

            library_children = block_structure.get_children(block_key)

            if library_children:
                state_dict = get_student_module_as_dict(usage_info.user, usage_info.course_key, block_key)
                current_children_blocks = {block.block_id for block in library_children}
                current_selected_blocks = {item[1] for item in state_dict.get('selected', [])}

                # As the selections should have already been made by the ContentLibraryTransformer,
                # the current children of the item bank block should be the same as the stored
                # selections. If they aren't, some other transformer that ran before this transformer
                # has modified those blocks (for example, content gating may have affected this). So do not
                # transform the order in that case.
                if current_children_blocks != current_selected_blocks:
                    logger.debug(
                        'Mismatch between the children of %s in the stored state and the actual children for user %s. '
                        'Continuing without order transformation.',
                        str(block_key),
                        usage_info.user.username
                    )
                else:
                    ordering_data = {block[1]: position for position, block in enumerate(state_dict['selected'])}
                    library_children.sort(key=lambda block, data=ordering_data: data[block.block_id])
