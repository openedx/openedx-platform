"""
This module handles loading xmodule templates
These templates are used by the CMS to provide content that overrides xmodule defaults for
samples.

``Template``s are defined in x_module. They contain 2 attributes:
    :metadata: A dictionary with the template metadata
    :data: A JSON value that defines the template content
"""

# should this move to cms since it's really only for module crud?


import logging  # noqa: I001
from collections import defaultdict

from xblock.core import XBlock

from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

log = logging.getLogger(__name__)


def all_templates():
    """
    Returns all templates for enabled modules, grouped by block type
    """
    # TODO use memcache to memoize w/ expiration
    templates = defaultdict(list)
    course = CourseFactory.create()

    for category, _ in XBlock.load_classes():
        loaded_block = BlockFactory.create(category=category, parent_location=course.location)
        if not hasattr(loaded_block, 'templates'):
            continue
        templates[category] = loaded_block.templates()

    return templates
