"""
Script for generating CSV data on all components,
to audit the XBlocks used.
"""

import csv
import sys
from typing import TextIO

from django.core.management.base import BaseCommand

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import modulestore

CORE_XBLOCKS = ["html", "problem", "video"]


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
            generate_xblocks_csv(sys.stdout, exclude_core_xblocks, courses)
        else:
            with open(filename, "w") as file_handle:
                generate_xblocks_csv(file_handle, exclude_core_xblocks, courses)


def generate_xblocks_csv(
    file_handle: TextIO,
    exclude_core_xblocks: bool,
    courses: list[str] | None,
):
    """
    Generate the CSV and write it to `file_handle`.
    """
    if courses:
        overviews = CourseOverview.objects.filter(id__in=courses).order_by("id")
    else:
        overviews = CourseOverview.objects.all().order_by("id")

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
        )
    )

    for overview in overviews:
        try:
            course = modulestore().get_course(overview.id)
            writer.writerows(
                (
                    course.id,
                    course.display_name,
                    section.display_name,
                    subsection.display_name,
                    unit.display_name,
                    component.display_name,
                    component.location.block_type,
                )
                for section in course.get_children()
                for subsection in section.get_children()
                for unit in subsection.get_children()
                for component in unit.get_children()
                if not exclude_core_xblocks
                or component.location.block_type not in CORE_XBLOCKS
            )
        except:  # pylint: disable=bare-except
            print(
                f"Failed retrieving course {overview.id} from modulestore",
                file=sys.stderr
            )
