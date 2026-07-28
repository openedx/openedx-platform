"""
A Django command that exports a course to a tar.gz file.

If <filename> is '-', it pipes the file to stdout.

This is used by Analytics research exports to provide researchers
with course content.

At present, it differs from Studio exports in several ways:

* It does not include static content.
* The top-level directory in the resulting tarball is a "safe"
  (i.e. ascii) version of the course_key, rather than the word "course".
* It only supports the export of courses.  It does not export libraries.
"""


import os
import re
import shutil
import tarfile
from tempfile import mkdtemp, mkstemp
from textwrap import dedent

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from path import Path as path

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.xml_exporter import export_course_to_xml


class Command(BaseCommand):
    """
    Export a course to XML. The output is compressed as a tar.gz file.
    """
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument('course_id')
        parser.add_argument('--output')

    def handle(self, *args, **options):
        course_id = options['course_id']

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            raise CommandError("Unparsable course_id")  # pylint: disable=raise-missing-from  # noqa: B904
        except IndexError:
            raise CommandError("Insufficient arguments")  # pylint: disable=raise-missing-from  # noqa: B904

        filename = options['output']
        pipe_results = False

        if filename is None:
            fd, filename = mkstemp()
            os.close(fd)
            os.unlink(filename)
            pipe_results = True

        export_course_to_tarfile(course_key, filename)

        results = self._get_results(filename) if pipe_results else b''

        # results is of type bytes, so we must write the underlying buffer directly.
        self.stdout.buffer.write(results)

    def _get_results(self, filename):
        """
        Load results from file.

        Returns:
            bytes: bytestring of file contents.
        """
        with open(filename, 'rb') as f:
            results = f.read()
            os.remove(filename)
        return results


def export_course_to_tarfile(course_key, filename):
    """Exports a course into a tar.gz file"""
    tmp_dir = mkdtemp()
    try:
        course_dir = export_course_to_directory(course_key, tmp_dir)
        compress_directory(course_dir, filename)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def export_course_to_directory(course_key, root_dir):
    """Export course into a directory"""
    store = modulestore()
    course = store.get_course(course_key)
    if course is None:
        raise CommandError("Invalid course_id")

    # The safest characters are A-Z, a-z, 0-9, <underscore>, <period> and <hyphen>.
    # We represent the first four with \w.
    # TODO: Once we support courses with unicode characters, we will need to revisit this.
    replacement_char = '-'
    course_dir = replacement_char.join([course.id.org, course.id.course, course.id.run])
    course_dir = re.sub(r'[^\w\.\-]', replacement_char, course_dir)

    export_course_to_xml(store, None, course.id, root_dir, course_dir)

    export_dir = path(root_dir) / course_dir
    return export_dir


def compress_directory(directory, filename):
    """Compress a directory into a tar.gz file"""
    mode = 'w:gz'
    name = path(directory).name
    with tarfile.open(filename, mode) as tar_file:
        tar_file.add(directory, arcname=name)
