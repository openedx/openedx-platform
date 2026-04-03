"""
A Command which, given a mapping of V1 to V2 Libraries,
edits all xblocks in courses which refer to the v1 library to point to the v2 library.
"""

import csv
import logging

from celery import group
from django.core.management import BaseCommand, CommandError

from cms.djangoapps.contentstore.tasks import (
    replace_all_library_source_blocks_ids_for_course,
    undo_all_library_source_blocks_ids_for_course,
    validate_all_library_source_blocks_ids_for_course,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Example usage:
        $ ./manage.py cms replace_v1_lib_refs_with_v2_in_courses '/path/to/library_mappings.csv'
        $ ./manage.py cms replace_v1_lib_refs_with_v2_in_courses '/path/to/library_mappings.csv' --validate
        $ ./manage.py cms replace_v1_lib_refs_with_v2_in_courses '/path/to/library_mappings.csv' --undo
    """
    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help='Path to the CSV file.')
        parser.add_argument('--validate', action='store_true', help='Validate previous runs of the command')
        parser.add_argument('--undo', action='store_true', help='Validate previous runs of the command')

    def replace_all_library_source_blocks_ids(self, v1_to_v2_lib_map):
        """A method to replace 'source_library_id' in all relevant blocks."""

        course_id_strings = list(CourseOverview.get_all_course_keys())

        # Following is used for debugging and should be removed
        for course_id_string in course_id_strings:
            log.info(course_id_string)

        # Use Celery to distribute the workload
        tasks = group(
            replace_all_library_source_blocks_ids_for_course.s(
                course_id_string,
                v1_to_v2_lib_map
            )
            for course_id_string in course_id_strings
        )
        results = tasks.apply_async()

        for result in results.get():
            if isinstance(result, Exception):
                # Handle the task failure here
                log.error("Task failed with error: %s", str(result))
                continue
        log.info(
            "Completed replacing all v1 library source ids with v2 library source ids"
        )

    def validate(self, v1_to_v2_lib_map):
        """ Validate that replace_all_library_source_blocks_ids was successful"""
        course_id_strings = list(CourseOverview.get_all_course_keys())
        tasks = group(validate_all_library_source_blocks_ids_for_course.s(course_id, v1_to_v2_lib_map) for course_id in course_id_strings)  # lint-amnesty, pylint: disable=line-too-long
        results = tasks.apply_async()

        validation = set()
        for result in results.get():
            if isinstance(result, Exception):
                # Handle the task failure here
                log.error("Task failed with error: %s", str(result))
                continue
            else:
                validation.update(result)

        if validation.issubset(v1_to_v2_lib_map.values()):
            log.info("Validation: All values in the input map are present in courses.")
        else:
            log.info(
                "Validation Failed: There are unmapped v1 libraries."
            )

    def undo(self, v1_to_v2_lib_map):
        """ undo the changes made by replace_all_library_source_blocks_ids"""
        course_id_strings = list(CourseOverview.get_all_course_keys())

        # Use Celery to distribute the workload
        tasks = group(
            undo_all_library_source_blocks_ids_for_course.s(
                course_id,
                v1_to_v2_lib_map
            )
            for course_id in course_id_strings
        )
        results = tasks.apply_async()

        for result in results.get():
            if isinstance(result, Exception):
                # Handle the task failure here
                log.error("Task failed with error: %s", str(result))
                continue
        log.info("Completed replacing all v2 library source ids with v1 library source ids. Undo Complete")

    def handle(self, *args, **kwargs):
        """ Parse arguments and begin command"""
        file_path = kwargs['file_path']
        v1_to_v2_lib_map = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as csvfile:

                if not file_path.endswith('.csv'):
                    raise CommandError('Invalid file format. Only CSV files are supported.')

                csv_reader = csv.reader(csvfile)

                for row in csv_reader:
                    if len(row) >= 2:
                        key = row[0].strip()
                        value = row[1].strip()
                        v1_to_v2_lib_map[key] = value

                print("Data successfully imported as dictionary:")

        except FileNotFoundError:
            log.error("File not found at '%s'.", {file_path})
        except Exception as e:  # lint-amnesty, pylint: disable=broad-except
            log.error("An error occurred: %s", {str(e)})

        if kwargs['validate']:
            self.validate(v1_to_v2_lib_map)
        if kwargs['undo']:
            self.undo(v1_to_v2_lib_map)
        else:
            self.replace_all_library_source_blocks_ids(v1_to_v2_lib_map)
