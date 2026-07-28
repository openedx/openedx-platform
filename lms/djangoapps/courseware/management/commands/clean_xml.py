"""
Contains functions that handle XML course data
"""


import os
import sys
import traceback

import lxml.etree
from django.core.management.base import BaseCommand
from fs.osfs import OSFS
from path import Path as path

from xmodule.modulestore.xml import XMLModuleStore


def traverse_tree(course):
    """
    Load every block in course.  Return bool success value.
    """
    queue = [course]
    while len(queue) > 0:
        node = queue.pop()
        queue.extend(node.get_children())

    return True


def export(course, export_dir):
    """
    Export the specified course to course_dir.  Creates dir if it doesn't
    exist.  Overwrites files, does not clean out dir beforehand.
    """
    fs = OSFS(export_dir, create=True)
    if not fs.isdirempty('.'):  # pylint: disable=no-member
        print(f'WARNING: Directory {export_dir} not-empty.  May clobber/confuse things')

    try:
        course.runtime.export_fs = fs
        root = lxml.etree.Element('root')
        course.add_xml_to_node(root)
        with fs.open('course.xml', mode='w') as f:
            root.write(f)

        return True
    except:  # pylint: disable=bare-except
        print('Export failed!')
        traceback.print_exc()

    return False


def import_with_checks(course_dir):  # pylint: disable=missing-function-docstring
    all_ok = True

    print(f'Attempting to load "{course_dir}"')

    course_dir = path(course_dir)
    data_dir = course_dir.dirname()
    source_dirs = [course_dir.basename()]

    # No default class--want to complain if it doesn't find plugins for any
    # module.
    modulestore = XMLModuleStore(
        data_dir,
        default_class=None,
        source_dirs=source_dirs
    )

    def str_of_err(tpl):
        (msg, exc_str) = tpl
        return f'{msg}\n{exc_str}'

    courses = modulestore.get_courses()

    n = len(courses)
    if n != 1:
        print(f'ERROR: Expect exactly 1 course.  Loaded {n}: {courses}')
        return (False, None)

    course = courses[0]
    errors = modulestore.get_course_errors(course.id)
    if len(errors) != 0:
        all_ok = False
        print(
            '\n' +
            '========================================' +
            'ERRORs during import:' +
            '\n'.join(map(str_of_err, errors)) +
            '========================================' +
            '\n'
        )

    # print course
    validators = (
        traverse_tree,
    )

    print('========================================')
    print('Running validators...')

    for validate in validators:
        print(f'Running {validate.__name__}')
        all_ok = validate(course) and all_ok

    if all_ok:
        print('Course passes all checks!')
    else:
        print('Course fails some checks.  See above for errors.')
    return all_ok, course


def check_roundtrip(course_dir):
    """
    Check that import->export leaves the course the same
    """

    print('====== Roundtrip import =======')
    (ok, course) = import_with_checks(course_dir)
    if not ok:
        raise Exception('Roundtrip import failed!')

    print('====== Roundtrip export =======')
    export_dir = course_dir + '.rt'
    export(course, export_dir)

    # dircmp doesn't do recursive diffs.
    # diff = dircmp(course_dir, export_dir, ignore=[], hide=[])
    print('======== Roundtrip diff: =========')
    sys.stdout.flush()  # needed to make diff appear in the right place
    os.system(f'diff -r {course_dir} {export_dir}')
    print('======== ideally there is no diff above this =======')


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = 'Imports specified course, validates it, then exports it in a canonical format.'

    def add_arguments(self, parser):
        parser.add_argument('course_dir',
                            help='path to the input course directory')
        parser.add_argument('output_dir',
                            help='path to the output course directory')
        parser.add_argument('--force',
                            action='store_true',
                            help='export course even if there were import errors')

    def handle(self, *args, **options):
        course_dir = options['course_dir']
        output_dir = options['output_dir']
        force = options['force']

        (ok, course) = import_with_checks(course_dir)
        if ok or force:
            if not ok:
                print('WARNING: Exporting despite errors')
            export(course, output_dir)
            check_roundtrip(output_dir)
        else:
            print('Did NOT export')
