"""
A few places in the LMS want to notify the Credentials service when certain events happen (like certificates being
awarded or grades changing). To do this, they listen for a signal. Sometimes we want to rebuild the data on these apps
regardless of an actual change in the database, either to recover from a bug or to bootstrap a new feature we're
rolling out for the first time.

This management command will manually trigger the receivers we care about. (We don't want to trigger all receivers
for these signals, since these are busy signals.)
"""

import logging
import shlex

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateutil.parser
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.credentials.models import NotifyCredentialsConfig
from openedx.core.djangoapps.credentials.tasks.v1.tasks import handle_notify_credentials
from openedx.core.djangoapps.catalog.api import (
    get_programs_from_cache_by_uuid,
    get_course_run_key_for_program_from_cache,
)

log = logging.getLogger(__name__)


def parsetime(timestr):
    dt = dateutil.parser.parse(timestr)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


class Command(BaseCommand):
    """
    Example usage:

    # Process all certs/grades changes for a given course:
    $ ./manage.py lms --settings=devstack notify_credentials \
    --courses course-v1:edX+DemoX+Demo_Course

    # Process all certs/grades changes in a given time range:
    $ ./manage.py lms --settings=devstack notify_credentials \
    --start-date 2018-06-01 --end-date 2018-07-31

    A Dry Run will produce output that looks like:

        DRY-RUN: This command would have handled changes for...
        3 Certificates:
            course-v1:edX+RecordsSelfPaced+1 for user 14
            course-v1:edX+RecordsSelfPaced+1 for user 17
            course-v1:edX+RecordsSelfPaced+1 for user 18
        3 Grades:
            course-v1:edX+RecordsSelfPaced+1 for user 14
            course-v1:edX+RecordsSelfPaced+1 for user 17
            course-v1:edX+RecordsSelfPaced+1 for user 18
    """

    help = (
        "Simulate certificate/grade changes without actually modifying database "
        "content. Specifically, trigger the handlers that send data to Credentials."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Just show a preview of what would happen.",
        )
        parser.add_argument(
            "--site",
            default=None,
            help="Site domain to notify for (if not specified, all sites are notified). Uses course_org_filter.",
        )
        parser.add_argument(
            "--courses",
            nargs="+",
            help="Send information only for specific course runs.",
        )
        parser.add_argument(
            "--program_uuids",
            nargs="+",
            help="Send user data for course runs for courses within a program based on program uuids provided.",
        )
        parser.add_argument(
            "--start-date",
            type=parsetime,
            help="Send information only for certificates or grades that have changed since this date.",
        )
        parser.add_argument(
            "--end-date",
            type=parsetime,
            help="Send information only for certificates or grades that have changed before this date.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0,
            help="Number of seconds to sleep between processing queries, so that we don't flood our queues.",
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=100,
            help="Number of items to query at once.",
        )
        parser.add_argument(
            "--auto",
            action="store_true",
            help="Use to run the management command periodically",
        )
        parser.add_argument(
            "--args-from-database",
            action="store_true",
            help="Use arguments from the NotifyCredentialsConfig model instead of the command line.",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Run grade/cert change signal in verbose mode",
        )
        parser.add_argument(
            "--notify_programs",
            action="store_true",
            help="Send program award notifications with course notification tasks",
        )
        parser.add_argument(
            "--user_ids",
            default=None,
            nargs="+",
            help="Run the command for the given user or list of users",
        )
        parser.add_argument(
            "--revoke_program_certs",
            action="store_true",
            help="If true, system will check if any program certificates need to be revoked from learners",
        )

    def get_args_from_database(self):
        """Returns an options dictionary from the current NotifyCredentialsConfig model."""
        config = NotifyCredentialsConfig.current()
        if not config.enabled:
            raise CommandError("NotifyCredentialsConfig is disabled, but --args-from-database was requested.")

        # This split will allow for quotes to wrap datetimes, like "2020-10-20 04:00:00" and other
        # arguments as if it were the command line
        argv = shlex.split(config.arguments)
        parser = self.create_parser("manage.py", "notify_credentials")
        return parser.parse_args(argv).__dict__  # we want a dictionary, not a non-iterable Namespace object

    def handle(self, *args, **options):
        if options["args_from_database"]:
            options = self.get_args_from_database()

        if options["auto"]:
            run_frequency = settings.NOTIFY_CREDENTIALS_FREQUENCY
            options["end_date"] = datetime.now().replace(minute=0, second=0, microsecond=0)
            options["start_date"] = options["end_date"] - timedelta(seconds=run_frequency)

        log.info(
            f"notify_credentials starting, dry-run={options['dry_run']}, site={options['site']}, "
            f"delay={options['delay']} seconds, page_size={options['page_size']}, "
            f"from={options['start_date'] if options['start_date'] else 'NA'}, "
            f"to={options['end_date'] if options['end_date'] else 'NA'}, notify_programs={options['notify_programs']}, "
            f"user_ids={options['user_ids']}, execution={'auto' if options['auto'] else 'manual'}, "
            f"revoke_program_certs={options['revoke_program_certs']}"
        )

        program_course_run_keys = self._get_course_run_keys_for_programs(options["program_uuids"])

        course_runs = options["courses"]
        if not course_runs:
            course_runs = []
        if program_course_run_keys:
            course_runs.extend(program_course_run_keys)

        course_run_keys = self._get_validated_course_run_keys(course_runs)
        if not (course_run_keys or options["start_date"] or options["end_date"] or options["user_ids"]):
            raise CommandError(
                "You must specify a filter (e.g. --courses, --program_uuids, --start-date, or --user_ids)"
            )

        handle_notify_credentials.delay(options, course_run_keys)

    def _get_course_run_keys_for_programs(self, uuids):
        """
        Retrieve all course runs for all of the given program UUIDs.

        Params:
            uuids (list): List of programs UUIDs.

        Returns:
            (list): List of Course Run Keys as Strings.

        """
        program_course_run_keys = []
        if uuids:
            programs = get_programs_from_cache_by_uuid(uuids=uuids)
            for program in programs:
                program_course_run_keys.extend(get_course_run_key_for_program_from_cache(program))
        return program_course_run_keys

    def _get_validated_course_run_keys(self, course_run_keys):
        """
        Validates a list of course run keys and returns the validated keys.

        Params:
            courses (list):  list of strings that can be parsed by CourseKey to verify the keys.
        Returns:
            (list): Containing a series of validated course keys as strings.
        """
        if not course_run_keys:
            course_run_keys = []
        validated_course_run_keys = []

        log.info("%d courses specified: %s", len(course_run_keys), ", ".join(course_run_keys))
        for course_run_key in course_run_keys:
            try:
                # Use CourseKey to check if the course_id is parsable, but just
                # keep the string; the celery task needs JSON serializable data.
                validated_course_run_keys.append(str(CourseKey.from_string(course_run_key)))
            except InvalidKeyError as exc:
                raise CommandError("{} is not a parsable CourseKey".format(course_run_key)) from exc
        return validated_course_run_keys
