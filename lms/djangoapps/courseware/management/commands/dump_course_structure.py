"""
Dump the structure of a course as a JSON object.

The resulting JSON object has one entry for each block in the course:

{
  "$block_url": {
    "category": "$block_category",
    "children": [$block_children_urls... ],
    "metadata": {$block_metadata}
  },

  "$block_url": ....
  ...
}

"""


import json
from textwrap import dedent

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from xblock.fields import Scope

from xmodule.discussion_block import DiscussionXBlock
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.inheritance import compute_inherited_metadata, own_metadata

FILTER_LIST = ['xml_attributes']
INHERITED_FILTER_LIST = ['children', 'xml_attributes']


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument('course_id',
                            help='specifies the course to dump')
        parser.add_argument('--modulestore',
                            default='default',
                            help='name of the modulestore')
        parser.add_argument('--inherited',
                            action='store_true',
                            help='include inherited metadata')
        parser.add_argument('--inherited_defaults',
                            action='store_true',
                            help='include default values of inherited metadata')

    def handle(self, *args, **options):

        # Get the modulestore

        store = modulestore()

        # Get the course data

        try:
            course_key = CourseKey.from_string(options['course_id'])
        except InvalidKeyError:
            raise CommandError("Invalid course_id")  # pylint: disable=raise-missing-from  # noqa: B904

        course = store.get_course(course_key)
        if course is None:
            raise CommandError("Invalid course_id")

        # Precompute inherited metadata at the course level, if needed:

        if options['inherited']:
            compute_inherited_metadata(course)

        # Convert course data to dictionary and dump it as JSON to stdout

        info = dump_block(course, inherited=options['inherited'], defaults=options['inherited_defaults'])

        return json.dumps(info, indent=2, sort_keys=True, default=str)


def dump_block(block, destination=None, inherited=False, defaults=False):
    """
    Add the block and all its children to the destination dictionary in
    as a flat structure.
    """

    destination = destination if destination else {}

    items = own_metadata(block)

    # HACK: add discussion ids to list of items to export (AN-6696)
    if isinstance(block, DiscussionXBlock) and 'discussion_id' not in items:
        items['discussion_id'] = block.discussion_id

    filtered_metadata = {k: v for k, v in items.items() if k not in FILTER_LIST}

    destination[str(block.location)] = {
        'category': block.location.block_type,
        'children': [str(child) for child in getattr(block, 'children', [])],
        'metadata': filtered_metadata,
    }

    if inherited:
        # When calculating inherited metadata, don't include existing
        # locally-defined metadata
        inherited_metadata_filter_list = list(filtered_metadata.keys())
        inherited_metadata_filter_list.extend(INHERITED_FILTER_LIST)

        def is_inherited(field):
            if field.name in inherited_metadata_filter_list:
                return False
            elif field.scope != Scope.settings:
                return False
            elif defaults:
                return True
            else:
                return field.values != field.default

        inherited_metadata = {field.name: field.read_json(block) for field in block.fields.values() if is_inherited(field)}  # pylint: disable=line-too-long
        destination[str(block.location)]['inherited_metadata'] = inherited_metadata

    for child in block.get_children():
        dump_block(child, destination, inherited, defaults)

    return destination
