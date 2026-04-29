"""
Script for exporting courseware from Mongo to a tar.gz file
"""


import os

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.xml_exporter import export_course_to_xml


class Command(BaseCommand):
    """
    Export the specified course into a directory.  Output will need to be tar zxcf'ed.
    """
    help = 'Export the specified course into a directory'

    def add_arguments(self, parser):
        parser.add_argument('course_id')
        parser.add_argument('output_path')

    def handle(self, *args, **options):
        """
        Given a course id(old or new style), and an output_path folder.  Export the
        corresponding course from mongo and put it directly in the folder.
        """
        try:
            course_key = CourseKey.from_string(options['course_id'])
        except InvalidKeyError:
            raise CommandError("Invalid course_key: '%s'." % options['course_id'])  # pylint: disable=raise-missing-from  # noqa: B904, UP031

        if not modulestore().get_course(course_key):
            raise CommandError("Course with %s key not found." % options['course_id'])  # noqa: UP031

        output_path = options['output_path']

        print(f"Exporting course id = {course_key} to {output_path}")

        if not output_path.endswith('/'):
            output_path += '/'

        root_dir = os.path.dirname(output_path)
        course_dir = os.path.splitext(os.path.basename(output_path))[0]

        export_course_to_xml(modulestore(), contentstore(), course_key, root_dir, course_dir)
