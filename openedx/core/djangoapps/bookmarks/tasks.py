"""
Tasks for bookmarks.
"""


import logging

from celery import shared_task
from django.db import transaction
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.django import modulestore

from . import PathItem

log = logging.getLogger('edx.celery.task')


def _calculate_course_xblocks_data(course_key):
    """
    Fetch data for all the blocks in the course.

    This data consists of the display_name and path of the block.
    """
    with modulestore().bulk_operations(course_key):

        course = modulestore().get_course(course_key, depth=None)
        blocks_info_dict = {}

        # Collect display_name and children usage keys.
        blocks_stack = [course]
        while blocks_stack:
            current_block = blocks_stack.pop()
            children = current_block.get_children() if current_block.has_children else []
            usage_id = str(current_block.scope_ids.usage_id)
            block_info = {
                'usage_key': current_block.scope_ids.usage_id,
                'display_name': current_block.display_name_with_default,
                'children_ids': [str(child.scope_ids.usage_id) for child in children]
            }
            blocks_info_dict[usage_id] = block_info

            # Add this blocks children to the stack so that we can traverse them as well.
            blocks_stack.extend(children)

    # Set children
    for block in blocks_info_dict.values():
        block.setdefault('children', [])
        for child_id in block['children_ids']:
            block['children'].append(blocks_info_dict[child_id])
        block.pop('children_ids', None)

    # Calculate paths
    def add_path_info(block_info, current_path):
        """Do a DFS and add paths info to each block_info."""

        block_info.setdefault('paths', [])
        block_info['paths'].append(current_path)

        for child_block_info in block_info['children']:
            add_path_info(child_block_info, current_path + [block_info])

    add_path_info(blocks_info_dict[str(course.scope_ids.usage_id)], [])

    return blocks_info_dict


def _paths_from_data(paths_data):
    """
    Construct a list of paths from path data.
    """
    paths = []
    for path_data in paths_data:
        paths.append([
            PathItem(item['usage_key'], item['display_name']) for item in path_data
            if item['usage_key'].block_type != 'course'
        ])

    return [path for path in paths if path]


def paths_equal(paths_1, paths_2):
    """
    Check if two paths are equivalent.
    """
    if len(paths_1) != len(paths_2):
        return False

    for path_1, path_2 in zip(paths_1, paths_2):  # noqa: B905
        if len(path_1) != len(path_2):
            return False

        for path_item_1, path_item_2 in zip(path_1, path_2):  # noqa: B905
            if path_item_1.display_name != path_item_2.display_name:
                return False

            usage_key_1 = path_item_1.usage_key.replace(
                course_key=modulestore().fill_in_run(path_item_1.usage_key.course_key)
            )
            usage_key_2 = path_item_1.usage_key.replace(
                course_key=modulestore().fill_in_run(path_item_2.usage_key.course_key)
            )
            if usage_key_1 != usage_key_2:
                return False

    return True


def _update_xblocks_cache(course_key):
    """
    Calculate the XBlock cache data for a course and update the XBlockCache table.
    """
    from .models import XBlockCache
    blocks_data = _calculate_course_xblocks_data(course_key)

    def update_block_cache_if_needed(block_cache, block_data):
        """ Compare block_cache object with data and update if there are differences. """
        paths = _paths_from_data(block_data['paths'])
        if block_cache.display_name != block_data['display_name'] or not paths_equal(block_cache.paths, paths):
            log.info('Updating XBlockCache with usage_key: %s', str(block_cache.usage_key))
            block_cache.display_name = block_data['display_name']
            block_cache.paths = paths
            block_cache.save()

    with transaction.atomic():
        block_caches = XBlockCache.objects.filter(course_key=course_key)
        for block_cache in block_caches:
            block_data = blocks_data.pop(str(block_cache.usage_key), None)
            if block_data:
                update_block_cache_if_needed(block_cache, block_data)

    for block_data in blocks_data.values():
        with transaction.atomic():
            paths = _paths_from_data(block_data['paths'])
            log.info('Creating XBlockCache with usage_key: %s', str(block_data['usage_key']))
            block_cache, created = XBlockCache.objects.get_or_create(usage_key=block_data['usage_key'], defaults={
                'course_key': course_key,
                'display_name': block_data['display_name'],
                'paths': paths,
            })

            if not created:
                update_block_cache_if_needed(block_cache, block_data)


@shared_task(name='openedx.core.djangoapps.bookmarks.tasks.update_xblocks_cache')
@set_code_owner_attribute
def update_xblocks_cache(course_id):
    """
    Update the XBlocks cache for a course.

    Arguments:
        course_id (String): The course_id of a course.
    """
    # Ideally we'd like to accept a CourseLocator; however, CourseLocator is not JSON-serializable (by default) so
    # Celery's delayed tasks fail to start. For this reason, callers should pass the course key as a Unicode string.
    if not isinstance(course_id, str):
        raise ValueError(f'course_id must be a string. {type(course_id)} is not acceptable.')

    course_key = CourseKey.from_string(course_id)
    log.info('Starting XBlockCaches update for course_key: %s', course_id)
    _update_xblocks_cache(course_key)
    log.info('Ending XBlockCaches update for course_key: %s', course_id)
