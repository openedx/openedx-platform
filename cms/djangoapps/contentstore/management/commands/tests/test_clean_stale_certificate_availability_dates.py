"""
Tests for the `clean_stale_certificate_available_dates` management command.
"""
from datetime import datetime, timedelta

import pytz
from django.core.management import CommandError, call_command

from cms.djangoapps.contentstore.models import CleanStaleCertificateAvailabilityDatesConfig
from openedx.core.lib.courses import get_course_by_id
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class CleanStaleCertAvailableDateTests(ModuleStoreTestCase):
    """
    Tests for the `clean_stale_certificate_available_dates` management command.
    """
    def setUp(self):
        super().setUp()

        self.self_paced_course1 = CourseFactory()
        self.self_paced_course2 = CourseFactory()
        self.instructor_paced_course1 = CourseFactory()

        # set the self-paced courses to self-paced, create and insert an invalid certificate available date for them
        self.certificate_available_date = datetime.now(pytz.UTC) + timedelta(days=30)
        self.self_paced_course1.self_paced = True
        self.self_paced_course1.certificate_available_date = self.certificate_available_date
        self.self_paced_course2.self_paced = True
        self.self_paced_course2.certificate_available_date = self.certificate_available_date
        self.instructor_paced_course1.self_paced = False
        self.instructor_paced_course1.certificate_available_date = self.certificate_available_date

        self.update_course(self.self_paced_course1, ModuleStoreEnum.UserID.test)
        self.update_course(self.self_paced_course2, ModuleStoreEnum.UserID.test)
        self.update_course(self.instructor_paced_course1, ModuleStoreEnum.UserID.test)

    def test_remove_certificate_available_date_from_self_paced_courses(self):
        """
        Happy path test that attempts to remove a certificate available date from a course that shouldn't have one.
        """
        call_command(
            "clean_stale_certificate_available_dates",
            "--course-runs",
            self.self_paced_course1.id,
            self.self_paced_course2.id,
            "--delete"
        )

        course = get_course_by_id(self.self_paced_course1.id)
        assert course.certificate_available_date is None
        assert course.certificates_display_behavior == CertificatesDisplayBehaviors.EARLY_NO_INFO
        course = get_course_by_id(self.self_paced_course2.id)
        assert course.certificate_available_date is None
        assert course.certificates_display_behavior == CertificatesDisplayBehaviors.EARLY_NO_INFO

    def test_remove_certificate_available_date_no_delete_flag(self):
        """
        Verifies the behavior of the management command when the delete flag is _not_ passed in. When the management
        command is run without this flag, we should just log information about the course and no action should be taken
        to remove the data from the course.
        """
        call_command(
            "clean_stale_certificate_available_dates",
            "--course-runs",
            self.self_paced_course1.id
        )

        course = get_course_by_id(self.self_paced_course1.id)
        assert course.certificate_available_date is not None
        # I can't compare the two datetime's directly as the datetime coming from the course/modulestore is slightly
        # different (the TZ info is saved as a FixedOffset instead of the specific time zone, so I compare the two
        # datetimes as an ISO8601 string)
        assert (
            course.certificate_available_date.isoformat(timespec='minutes') ==
            self.certificate_available_date.isoformat(timespec='minutes')
        )
        assert course.certificates_display_behavior == CertificatesDisplayBehaviors.END

    def test_remove_certificate_available_date_from_instructor_paced_course_expect_error(self):
        """
        Verifies behavior of the management command when it is run with an instructor-paced course.
        """
        expected_exception_message = (
            f"The course '{self.instructor_paced_course1.id}' is instructor-paced and the certificate availability "
            "date can be adjusted via Studio in the UI. Aborting operation."
        )

        with self.assertRaises(CommandError) as error:
            call_command(
                "clean_stale_certificate_available_dates",
                "--course-runs",
                self.instructor_paced_course1.id,
                "--delete"
            )

        assert str(error.exception) == expected_exception_message

    def test_remove_certificate_available_date_with_args_from_database(self):
        """
        Verifies the behavior of the management command when it is run via using arguments stored in our database as
        part of the `CleanStaleCertificateAvailabilityDatesConfig` configuration model.
        """
        # try running the command but expect it to fail since there are no options in the database and the configuration
        # model is disabled
        expected_error_message = (
            "CleanStaleCertificateAvailabilityDatesConfig is disabled, but --args-from-database was requested."
        )
        with self.assertRaises(CommandError) as error:
            call_command("clean_stale_certificate_available_dates", "--args-from-database")

        assert str(error.exception) == expected_error_message

        # add a configuration and enable it
        config = CleanStaleCertificateAvailabilityDatesConfig.current()
        config.arguments = f"--course-runs {self.self_paced_course1.id} {self.self_paced_course2.id} --delete"
        config.enabled = True
        config.save()

        call_command("clean_stale_certificate_available_dates", "--args-from-database")

        course = get_course_by_id(self.self_paced_course1.id)
        assert course.certificate_available_date is None
        assert course.certificates_display_behavior == CertificatesDisplayBehaviors.EARLY_NO_INFO
        course = get_course_by_id(self.self_paced_course2.id)
        assert course.certificate_available_date is None
        assert course.certificates_display_behavior == CertificatesDisplayBehaviors.EARLY_NO_INFO

        # explicitly disable the configuration and try running one more time
        config.enabled = False
        config.save()

        with self.assertRaises(CommandError) as disabled_error:
            call_command("clean_stale_certificate_available_dates", "--args-from-database")

        assert str(error.exception) == expected_error_message
