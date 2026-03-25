"""
A management command that can be used to remove a stale `certificate_available_date` from a course-run.
"""
import logging  # noqa: I001
import shlex

from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.models import CleanStaleCertificateAvailabilityDatesConfig
from openedx.core.lib.courses import get_course_by_id
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Example usage:

    To see info regarding the course-run's certificate display behavior and availability date data:
        $ ./manage.py cms clean_stale_certificate_available_dates --course-runs <course_run_key>
    To remove a stale certificate availability date:
        $ ./manage.py cms clean_stale_certificate_available_dates --course-runs <course_run_key> --delete
    To run this command using arguments stored in the database (as part of the
    `CleanStaleCertificateAvailabilityDatesConfig` configuration model):
        $ ./manage.py cms clean_stale_certificate_available_dates --args-from-database
    """
    help = (
        "Removes the certificate_available_date data from the specified self-paced course-runs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--course-runs",
            nargs="+",
            help=(
                "A space seperated list of course-runs where we want to remove the `certificate_available_date` from"
            )
        )
        parser.add_argument(
            "--delete",
            action='store_true',
            help=(
                "Optional. If included, this flag tells the management command to remove the "
                "`certificiate_available_date` data for the given courses"
            )
        )
        parser.add_argument(
            "--args-from-database",
            action='store_true',
            help=(
                "Use arguments from the CleanStaleCertificateAvailabilityDatesConfig configuration model instead of "
                "the command line",
            )
        )

    def get_args_from_database(self):
        """
        Returns an options dictionary from the current CleanStaleCertificateAvailabilityDatesConfig model.
        """
        config = CleanStaleCertificateAvailabilityDatesConfig.current()
        if not config.enabled:
            raise CommandError(
                "CleanStaleCertificateAvailabilityDatesConfig is disabled, but --args-from-database was requested."
            )

        args = shlex.split(config.arguments)
        parser = self.create_parser("manage.py", "clean_stale_certificate_available_dates")
        return vars(parser.parse_args(args))

    def log_certificate_info_for_course(self, course, course_run):
        """
        A utility function that prints information regarding the current course's pacing, certificate display behavior,
        and certificate availability date.
        """
        logging.info(f"Is course '{course_run}' self-paced? {course.self_paced}")
        logging.info(
            f"The certificate display behavior for course '{course_run}' is {course.certificates_display_behavior}"
        )
        logging.info(
            f"The certificate_available_date for course '{course_run}' is '{course.certificate_available_date}'"
        )

    def update_course_settings(self, course):
        """
        A utility function that updates-and-commits the certificate display behavior and certificate_available_date
        for the course to fix the stale certificate availability date.
        """
        del course.certificate_available_date
        course.certificates_display_behavior = CertificatesDisplayBehaviors.EARLY_NO_INFO
        # commit the changes
        modulestore().update_item(course, ModuleStoreEnum.UserID.mgmt_command)

    def handle(self, *args, **options):
        if options["args_from_database"]:
            options = self.get_args_from_database()

        log.info("The `clean_stale_certificate_available_dates` command is starting.")

        course_runs = options.get("course_runs")
        for run in course_runs:
            # verify that the course-key specified is valid
            logging.info(f"Checking that '{run}' is a parsable CourseKey")
            try:
                course_key = CourseKey.from_string(run)
            except InvalidKeyError as key_error:
                raise CommandError(f"{run} is not a parsable CourseKey") from key_error

            course = get_course_by_id(course_key)
            self.log_certificate_info_for_course(course, run)

            if options['delete']:
                if not course.self_paced:
                    raise CommandError(
                        f"The course '{run}' is instructor-paced and the certificate availability date can be adjusted "
                        "via Studio in the UI. Aborting operation."
                    )

                logging.info(f"Attempting to remove the certificate available date in course '{run}'")
                self.update_course_settings(course)
