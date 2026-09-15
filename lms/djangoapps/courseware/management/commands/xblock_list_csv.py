"""
Script for generating CSV data on all components,
to audit the XBlocks used.
"""

import csv
import sys
from typing import TextIO

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.django import modulestore

CORE_XBLOCKS = ["html", "problem", "video"]


def iter_descendants(block, ancestor_names=None):
    """
    Recursively yield (ancestor_names, descendant) for every descendant of
    `block`, regardless of nesting depth. `ancestor_names` is the list of
    display names of container blocks strictly between `block` and
    `descendant`.
    """
    ancestor_names = ancestor_names or []
    for child in block.get_children():
        yield ancestor_names, child
        yield from iter_descendants(child, ancestor_names + [child.display_name])


def _iter_course_rows(course, exclude_core_xblocks):
    """
    Yield CSV rows for `course`.
    """
    for section in course.get_children():
        for subsection in section.get_children():
            for unit in subsection.get_children():
                for ancestors, component in iter_descendants(unit):
                    if exclude_core_xblocks and component.location.block_type in CORE_XBLOCKS:
                        continue
                    yield (
                        course.id,
                        course.display_name,
                        section.display_name,
                        subsection.display_name,
                        unit.display_name,
                        component.display_name,
                        component.location.block_type,
                        " > ".join(
                            str(name or "")
                            for name in [section.display_name, subsection.display_name, unit.display_name]
                            + ancestors
                            + [component.display_name]
                        ),
                    )


def _resolve_courses(courses, error_file):
    """
    Resolve the requested course ID strings directly against the
    modulestore. Returns (list of course objects sorted by course id,
    number of course IDs that could not be resolved).
    """
    resolved = []
    failures = 0
    for course_id in dict.fromkeys(courses):
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            print(f"Invalid course ID: {course_id}", file=error_file)
            failures += 1
            continue
        course = modulestore().get_course(course_key)
        if course is None:
            print(f"Course not found: {course_key}", file=error_file)
            failures += 1
        else:
            resolved.append(course)
    resolved.sort(key=lambda course: str(course.id))
    return resolved, failures


class Command(BaseCommand):
    """
    Generate a CSV file with all published components used in courses with their xblock type.
    """

    help = "Generate a CSV file with all published components used in courses with their xblock type."

    def add_arguments(self, parser):
        parser.add_argument(
            "filename", help="Path to the CSV output file. Use '-' to print to stdout."
        )
        parser.add_argument(
            "--exclude-core-xblocks",
            action="store_true",
            help=f"Exclude components using core XBlocks: ({', '.join(CORE_XBLOCKS)}). Default: false",
        )
        parser.add_argument(
            "--courses",
            nargs="+",
            metavar="COURSE_ID",
            help="Filter the report by one or more course IDs",
        )


    def handle(self, *args, **options):
        filename = options["filename"]
        exclude_core_xblocks = options["exclude_core_xblocks"]
        courses = options["courses"]

        if filename == "-":
            failures = generate_xblocks_csv(self.stdout, exclude_core_xblocks, courses, self.stderr)
        else:
            with open(filename, "w", encoding="utf-8", newline="") as file_handle:
                failures = generate_xblocks_csv(file_handle, exclude_core_xblocks, courses, self.stderr)

        if failures:
            raise CommandError(f"{failures} course(s) could not be processed. See stderr for details.")


def generate_xblocks_csv(
    file_handle: TextIO,
    exclude_core_xblocks: bool,
    courses: list[str] | None,
    error_file: TextIO = sys.stderr,
):
    """
    Generate the CSV and write it to `file_handle`. Returns the number of
    courses that could not be resolved or processed.
    """
    if courses:
        course_list, failures = _resolve_courses(courses, error_file)
    else:
        course_list = sorted(modulestore().get_courses(), key=lambda course: str(course.id))
        failures = 0

    writer = csv.writer(file_handle)
    writer.writerow(
        (
            "Course ID",
            "Course Name",
            "Section Name",
            "Subsection Name",
            "Unit Name",
            "Component Name",
            "Xblock Type",
            "Full Hierarchy",
        )
    )

    for course in course_list:
        row_iter = _iter_course_rows(course, exclude_core_xblocks)
        while True:
            try:
                row = next(row_iter)
            except StopIteration:
                break
            except Exception:  # pylint: disable=broad-except
                failures += 1
                print(
                    f"Failed processing course {course.id}",
                    file=error_file
                )
                break
            writer.writerow(row)

    return failures
