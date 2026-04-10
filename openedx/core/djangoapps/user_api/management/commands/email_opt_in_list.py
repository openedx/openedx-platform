"""Generate a list indicating whether users have opted in or out of receiving email from an org.

Email opt-in is stored as an org-level preference.
When reports are generated, we need to handle:

1) Org aliases: some organizations might have multiple course key "org" values.
    We choose the most recently set preference among all org aliases.
    Since this information isn't stored anywhere in edx-platform,
    the caller needs to pass in the list of orgs and aliases.

2) No preference set: Some users may not have an opt-in preference set
    if they enrolled before the preference was introduced.
    These users are opted in by default.

3) Restricting to a subset of courses in an org: Some orgs have courses
    that we don't want to include in the results (e.g. EdX-created test courses).
    Allow the caller to explicitly specify the list of courses in the org.

The command will always use the read replica database if one is configured.

"""


import contextlib
import csv
import logging
import os.path
import time
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from xmodule.modulestore.django import modulestore

DEFAULT_CHUNK_SIZE = 10

LOGGER = logging.getLogger(__name__)


def chunks(sequence, chunk_size):
    return (sequence[index: index + chunk_size] for index in range(0, len(sequence), chunk_size))


class Command(BaseCommand):
    """
    Generate a list of email opt-in values for user enrollments.
    """
    help = "Generate a list of email opt-in values for user enrollments."

    def add_arguments(self, parser):
        parser.add_argument('file_path',
                            metavar='OUTPUT_FILENAME',
                            help='Path where to output the email opt-in list.')
        parser.add_argument('org_list',
                            nargs='+',
                            metavar='ORG_ALIASES',
                            help='List of orgs for which to retrieve email opt-in info.')
        parser.add_argument('--courses',
                            metavar='COURSE_ID_LIST',
                            help='List of course IDs for which to retrieve email opt-in info.')
        parser.add_argument('--email-optin-chunk-size',
                            type=int,
                            default=DEFAULT_CHUNK_SIZE,
                            dest='email_optin_chunk_size',
                            metavar='CHUNK_SIZE',
                            help='Number of courses for which to get opt-in information in a single query.')

    # Fields output in the CSV
    OUTPUT_FIELD_NAMES = [
        "user_id",
        "username",
        "email",
        "full_name",
        "course_id",
        "is_opted_in_for_email",
        "preference_set_datetime"
    ]

    # Number of records to read at a time when making
    # multiple queries over a potentially large dataset.
    QUERY_INTERVAL = 1000

    # Default datetime if the user has not set a preference
    DEFAULT_DATETIME_STR = datetime(year=2014, month=12, day=1).isoformat(' ')

    def handle(self, *args, **options):
        """
        Execute the command.

        Arguments:
            file_path (str): Path to the output file.
            *org_list (unicode): List of organization aliases.

        Keyword Arguments:
            courses (unicode): Comma-separated list of course keys.  If provided,
                include only these courses in the results.

        Raises:
            CommandError
        """
        file_path = options['file_path']
        org_list = options['org_list']

        if os.path.exists(file_path):
            raise CommandError(f"File already exists at '{file_path}'")

        only_courses = options.get("courses")

        if only_courses is None:
            # Retrieve all the courses for the org.
            # If we were given a specific list of courses to include,
            # filter out anything not in that list.
            courses = self._get_courses_for_org(org_list)

            # Add in organizations from the course keys, to ensure we're including orgs with different capitalizations
            org_list = list(set(org_list) | {course.org for course in courses})
        else:
            courses = list(set(only_courses.split(",")))

        # If no courses are found, abort
        if not courses:
            raise CommandError(
                "No courses found for orgs: {orgs}".format(
                    orgs=", ".join(org_list)
                )
            )

        # Let the user know what's about to happen
        LOGGER.info(
            "Retrieving data for courses: {courses}".format(
                courses=", ".join([str(course) for course in courses])
            )
        )

        email_optin_chunk_size = options.get('email_optin_chunk_size', DEFAULT_CHUNK_SIZE)

        # Open the output file and generate the report.
        with open(file_path, "w") as file_handle:
            with self._log_execution_time():
                for course_group in chunks(courses, email_optin_chunk_size):
                    self._write_email_opt_in_prefs(file_handle, org_list, course_group)

        # Remind the user where the output file is
        LOGGER.info(f"Output file: {file_path}")

    def _get_courses_for_org(self, org_aliases):
        """
        Retrieve all course keys for a particular org.

        Arguments:
            org_aliases (list): List of aliases for the org.

        Returns:
            List of `CourseKey`s
        """
        all_courses = modulestore().get_courses()
        orgs_lowercase = [org.lower() for org in org_aliases]
        return [
            course.id
            for course in all_courses
            if course.id.org.lower() in orgs_lowercase
        ]

    @contextlib.contextmanager
    def _log_execution_time(self):
        """
        Context manager for measuring execution time.
        """
        start_time = time.time()
        yield
        execution_time = time.time() - start_time
        LOGGER.info(f"Execution time: {execution_time} seconds")

    def _write_email_opt_in_prefs(self, file_handle, org_aliases, courses):
        """
        Write email opt-in preferences to the output file.

        This will generate a CSV with one row for each enrollment.
        This means that the user's "opt in" preference will be specified
        multiple times if the user has enrolled in multiple courses
        within the org.  However, the values should always be the same:
        if the user is listed as "opted out" for course A, she will
        also be listed as "opted out" for courses B, C, and D.

        Arguments:
            file_handle (file): Handle to the output file.
            org_aliases (list): List of aliases for the org.
            courses (list): List of course keys in the org.

        Returns:
            None
        """
        writer = csv.DictWriter(file_handle, fieldnames=self.OUTPUT_FIELD_NAMES)
        writer.writeheader()

        cursor = self._db_cursor()
        query = (
            """
            SELECT
                user.`id` AS `user_id`,
                user.`username` AS username,
                user.`email` AS `email`,
                profile.`name` AS `full_name`,
                enrollment.`course_id` AS `course_id`,
                (
                    SELECT value
                    FROM user_api_userorgtag
                    WHERE org IN ( {org_list} )
                    AND `key`=\"email-optin\"
                    AND `user_id`=user.`id`
                    ORDER BY modified DESC
                    LIMIT 1
                ) AS `is_opted_in_for_email`,
                (
                    SELECT modified
                    FROM user_api_userorgtag
                    WHERE org IN ( {org_list} )
                    AND `key`=\"email-optin\"
                    AND `user_id`=user.`id`
                    ORDER BY modified DESC
                    LIMIT 1
                ) AS `preference_set_datetime`
            FROM
                student_courseenrollment AS enrollment
                LEFT JOIN auth_user AS user ON user.id=enrollment.user_id
                LEFT JOIN auth_userprofile AS profile ON profile.user_id=user.id
            WHERE enrollment.course_id IN ( {course_id_list} )
            """
        ).format(
            course_id_list=self._sql_list(courses),
            org_list=self._sql_list(org_aliases)
        )

        cursor.execute(query)
        row_count = 0
        for row in self._iterate_results(cursor):
            user_id, username, email, full_name, course_id, is_opted_in, pref_set_datetime = row

            if pref_set_datetime:
                pref_set_datetime = timezone.make_aware(pref_set_datetime, dt_timezone.utc)  # noqa: UP017
            else:
                pref_set_datetime = self.DEFAULT_DATETIME_STR

            if not full_name:
                full_name = ""

            # Only encode to utf-8 in python2 because python3's csv writer can handle unicode.
            writer.writerow({
                "user_id": user_id,
                "username": username,
                "email": email,
                # There should not be a case where users are without full_names. We only need this safe check because
                # of ECOM-1995.
                "full_name": full_name,
                "course_id": course_id,
                "is_opted_in_for_email": is_opted_in if is_opted_in else "True",
                "preference_set_datetime": pref_set_datetime,
            })
            row_count += 1

        # Log the number of rows we processed
        LOGGER.info(f"Retrieved {row_count} records for orgs {org_aliases}.")

    def _iterate_results(self, cursor):
        """
        Iterate through the results of a database query, fetching in chunks.

        Arguments:
            cursor: The database cursor

        Yields:
            tuple of row values from the query
        """
        while True:
            rows = cursor.fetchmany(self.QUERY_INTERVAL)
            if not rows:
                break
            yield from rows

    def _sql_list(self, values):
        """
        Serialize a list of values for including in a SQL "IN" statement.
        """
        return ",".join([f'"{val}"' for val in values])

    def _db_cursor(self):
        """
        Return a database cursor to the read replica if one is available.
        """
        # Use the read replica if one has been configured
        db_alias = (
            'read_replica'
            if 'read_replica' in settings.DATABASES
            else 'default'
        )
        return connections[db_alias].cursor()
