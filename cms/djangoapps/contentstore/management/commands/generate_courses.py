"""
Django management command to generate a test course from a course config json
"""


import json
import logging

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError
from xblock.fields import Date

from cms.djangoapps.contentstore.management.commands.utils import user_from_str
from cms.djangoapps.contentstore.views.course import create_new_course_in_store
from openedx.core.djangoapps.credit.models import CreditProvider
from xmodule.course_block import CourseFields  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import DuplicateCourseError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tabs import CourseTabList  # lint-amnesty, pylint: disable=wrong-import-order

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """ Generate a basic course """
    help = 'Generate courses on studio from a json list of courses'

    def add_arguments(self, parser):
        parser.add_argument(
            'courses_json',
        )

    def handle(self, *args, **options):
        try:
            courses = json.loads(options["courses_json"])["courses"]
        except ValueError:
            raise CommandError("Invalid JSON object")  # lint-amnesty, pylint: disable=raise-missing-from
        except KeyError:
            raise CommandError("JSON object is missing courses list")  # lint-amnesty, pylint: disable=raise-missing-from

        for course_settings in courses:
            # Validate course
            if not self._course_is_valid(course_settings):
                logger.warning("Can't create course, proceeding to next course")
                continue

            # Retrieve settings
            org = course_settings["organization"]
            num = course_settings["number"]
            run = course_settings["run"]
            user_email = course_settings["user"]
            try:
                user = user_from_str(user_email)
            except User.DoesNotExist:
                logger.warning(user_email + " user does not exist")  # lint-amnesty, pylint: disable=logging-not-lazy
                logger.warning("Can't create course, proceeding to next course")
                continue
            fields = self._process_course_fields(course_settings["fields"])

            # Create the course
            try:
                new_course = create_new_course_in_store("split", user, org, num, run, fields)
                logger.info(f"Created {str(new_course.id)}")
            except DuplicateCourseError:
                logger.warning("Course already exists for %s, %s, %s", org, num, run)

            # Configure credit provider
            if ("enrollment" in course_settings) and ("credit_provider" in course_settings["enrollment"]):
                credit_provider = course_settings["enrollment"]["credit_provider"]
                if credit_provider is not None:
                    CreditProvider.objects.get_or_create(
                        provider_id=credit_provider,
                        display_name=credit_provider
                    )

    def _process_course_fields(self, fields):
        """ Returns a validated list of course fields """
        all_fields = list(CourseFields.__dict__.keys())
        non_course_fields = [
            "__doc__",
            "__module__",
            "__weakref__",
            "__dict__"
        ]
        for field in non_course_fields:
            all_fields.remove(field)

        # Non-primitive course fields
        date_fields = [
            "certificate_available_date",
            "announcement",
            "enrollment_start",
            "enrollment_end",
            "start",
            "end"
        ]
        course_tab_list_fields = [
            "tabs"
        ]

        for field in dict(fields):
            if field not in all_fields:
                # field does not exist as a CourseField
                del fields[field]
                logger.info(field + "is not a valid CourseField")  # lint-amnesty, pylint: disable=logging-not-lazy
            elif fields[field] is None:
                # field is unset
                del fields[field]
            elif field in date_fields:
                # Generate Date object from the json value
                try:
                    date_json = fields[field]
                    fields[field] = Date().from_json(date_json)
                    logger.info(field + " has been set to " + date_json)
                except Exception:  # pylint: disable=broad-except
                    logger.info("The date string could not be parsed for " + field)  # lint-amnesty, pylint: disable=logging-not-lazy
                    del fields[field]
            elif field in course_tab_list_fields:
                # Generate CourseTabList object from the json value
                try:
                    course_tab_list_json = fields[field]
                    fields[field] = CourseTabList().from_json(course_tab_list_json)
                    logger.info(field + " has been set to " + course_tab_list_json)
                except Exception:  # pylint: disable=broad-except
                    logger.info("The course tab list string could not be parsed for " + field)  # lint-amnesty, pylint: disable=logging-not-lazy
                    del fields[field]
            else:
                # CourseField is valid and has been set
                logger.info(field + " has been set to " + str(fields[field]))  # lint-amnesty, pylint: disable=logging-not-lazy

        for field in all_fields:
            if field not in fields:
                logger.info(field + " has not been set")  # lint-amnesty, pylint: disable=logging-not-lazy
        return fields

    def _course_is_valid(self, course):
        """ Returns true if the course contains required settings """
        is_valid = True

        # Check course settings
        required_course_settings = [
            "organization",
            "number",
            "run",
            "fields",
            "user"
        ]
        for setting in required_course_settings:
            if setting not in course:
                logger.warning("Course json is missing " + setting)  # lint-amnesty, pylint: disable=logging-not-lazy
                is_valid = False

        # Check fields settings
        required_field_settings = [
            "display_name"
        ]
        if "fields" in course:
            for setting in required_field_settings:
                if setting not in course["fields"]:
                    logger.warning("Fields json is missing " + setting)  # lint-amnesty, pylint: disable=logging-not-lazy
                    is_valid = False

        return is_valid
