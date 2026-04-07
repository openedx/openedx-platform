"""
   Utils for Blocks
"""
from rest_framework.utils.serializer_helpers import ReturnList

from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider


def filter_discussion_xblocks_from_response(response, course_key):
    """
    Removes discussion xblocks if discussion provider is openedx.
    """
    configuration = DiscussionsConfiguration.get(context_key=course_key)
    provider = configuration.provider_type

    if provider != Provider.OPEN_EDX:
        return response

    is_list_response = isinstance(response.data, ReturnList)

    # Find discussion xblock IDs
    if is_list_response:
        discussion_xblocks = [
            block.get('id') for block in response.data
            if block.get('type') == 'discussion'
        ]
    else:
        discussion_xblocks = [
            key for key, value in response.data.get('blocks', {}).items()
            if value.get('type') == 'discussion'
        ]

    # Filter out discussion blocks
    if is_list_response:
        filtered_blocks = [
            block for block in response.data
            if block.get('type') != 'discussion'
        ]
    else:
        filtered_blocks = {
            key: value for key, value in response.data.get('blocks', {}).items()
            if value.get('type') != 'discussion'
        }

    # Remove references to discussion xblocks
    # These references needs to be removed because they no longer exist
    blocks_iterable = filtered_blocks if is_list_response else filtered_blocks.values()
    for block_data in blocks_iterable:
        for key in ['descendants', 'children']:
            if key in block_data:
                block_data[key] = [
                    descendant for descendant in block_data[key]
                    if descendant not in discussion_xblocks
                ]

    # Update response
    if is_list_response:
        response.data = ReturnList(filtered_blocks, serializer=None)
    else:
        response.data['blocks'] = filtered_blocks

    return response
