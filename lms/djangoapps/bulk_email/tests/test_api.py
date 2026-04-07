"""
Tests for the public Python API functions of the Bulk Email app.
"""
from testfixtures import LogCapture

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import InstructorFactory
from lms.djangoapps.bulk_email.api import (
    create_course_email,
    determine_targets_for_course_email,
    get_course_email,
    update_course_email,
)
from lms.djangoapps.bulk_email.data import BulkEmailTargetChoices
from lms.djangoapps.bulk_email.models import CourseEmail
from openedx.core.djangoapps.course_groups.models import CourseUserGroup
from openedx.core.lib.html_to_text import html_to_text
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

LOG_PATH = "lms.djangoapps.bulk_email.api"


class BulkEmailApiTests(ModuleStoreTestCase):
    """
    Tests for Python functions in the bulk_email app's public `api.py` that interact with CourseEmail instances.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.target = [BulkEmailTargetChoices.SEND_TO_MYSELF]
        self.subject = "email subject"
        self.html_message = "<p>test message</p>"

    def test_create_course_email(self):
        """
        Happy path test for the `create_course_email` function. Verifies the creation of a CourseEmail instance with
        the bare minimum information required for the function call.
        """
        course_email = create_course_email(
            self.course.id,
            self.instructor,
            self.target,
            self.subject,
            self.html_message,
        )

        assert course_email.sender.id == self.instructor.id
        assert course_email.subject == self.subject
        assert course_email.html_message == self.html_message
        assert course_email.course_id == self.course.id
        assert course_email.text_message == html_to_text(self.html_message)

    def test_create_course_email_with_optional_args(self):
        """
        Additional testing to verify that optional data is used as expected when passed into the `create_course_email`
        function.
        """
        text_message = "everything is awesome!"
        template_name = "gnarly_template"
        from_addr = "blub@noreply.fish.com"

        course_email = create_course_email(
            self.course.id,
            self.instructor,
            self.target,
            self.subject,
            self.html_message,
            text_message=text_message,
            template_name=template_name,
            from_addr=from_addr
        )

        assert course_email.sender.id == self.instructor.id
        assert course_email.subject == self.subject
        assert course_email.html_message == self.html_message
        assert course_email.course_id == self.course.id
        assert course_email.text_message == text_message
        assert course_email.template_name == template_name
        assert course_email.from_addr == from_addr

    def test_create_course_email_expect_exception(self):
        """
        Test to verify behavior when an exception occurs when calling teh `create_course_email` function.
        """
        targets = ["humpty dumpty"]

        expected_message = (
            f"Cannot create course email for {self.course.id} requested by user {self.instructor} for targets "
            f"{targets}"
        )

        with self.assertRaises(ValueError):
            with LogCapture() as log:
                create_course_email(
                    self.course.id,
                    self.instructor,
                    targets,
                    self.subject,
                    self.html_message
                )

        log.check_present(
            (LOG_PATH, "ERROR", expected_message),
        )

    def test_get_course_email(self):
        """
        A test to verify the happy path behavior of the `get_course_email` utility function and the presence of an
        expected log message when an email instance can't be found for a given id.
        """
        course_email = create_course_email(
            self.course.id,
            self.instructor,
            self.target,
            self.subject,
            self.html_message,
        )

        email_instance = get_course_email(course_email.id)
        assert email_instance.id == course_email.id

        # Next, try and retrieve a CourseEmail instance that does not exist.
        email_id_dne = 3463435
        expected_message = (
            f"CourseEmail instance with id '{email_id_dne}' could not be found"
        )
        with LogCapture() as log:
            get_course_email(email_id_dne)

        log.check_present(
            (LOG_PATH, "ERROR", expected_message),
        )

    def test_update_course_email(self):
        """
        A test that verifies the ability to update a CourseEmail instance when using the public `update_course_email`
        function.
        """
        course_email = create_course_email(
            self.course.id,
            self.instructor,
            self.target,
            self.subject,
            self.html_message,
        )

        updated_targets = [BulkEmailTargetChoices.SEND_TO_MYSELF, BulkEmailTargetChoices.SEND_TO_STAFF]
        updated_subject = "New Email Subject"
        updated_html_message = "<p>New HTML content!</p>"
        expected_plaintext_message = html_to_text(updated_html_message)
        update_course_email(
            self.course.id,
            course_email.id,
            updated_targets,
            updated_subject,
            updated_html_message
        )

        updated_course_email = CourseEmail.objects.get(id=course_email.id)
        assert updated_course_email.subject == updated_subject
        assert updated_course_email.html_message == updated_html_message
        assert updated_course_email.text_message == expected_plaintext_message
        email_targets = updated_course_email.targets.values_list('target_type', flat=True)
        for target in updated_targets:
            assert target in email_targets
        assert True

        # update course email but provide the plaintext content this time
        plaintext_message = "I am a plaintext message"
        update_course_email(
            self.course.id,
            course_email.id,
            updated_targets,
            updated_subject,
            updated_html_message,
            plaintext_message=plaintext_message
        )
        updated_course_email = CourseEmail.objects.get(id=course_email.id)
        assert updated_course_email.text_message == plaintext_message

    def test_update_course_email_no_targets_expect_error(self):
        """
        A test that verifies an expected error occurs when bad data is passed to the `update_course_email` function.
        """
        course_email = create_course_email(
            self.course.id,
            self.instructor,
            self.target,
            self.subject,
            self.html_message,
        )

        with self.assertRaises(ValueError):
            update_course_email(
                self.course.id,
                course_email.id,
                [],
                self.subject,
                self.html_message,
            )

    def test_determine_targets_for_course_email(self):
        """
        A test to verify the basic functionality of the `determine_targets_for_course_email` function.
        """
        # create cohort in our test course-run
        cohort_name = "TestCohort"
        cohort = CourseUserGroup.objects.create(
            name=cohort_name,
            course_id=self.course.id,
            group_type=CourseUserGroup.COHORT
        )

        # create a track called 'test' in our test course-run
        slug = "test-track"
        CourseMode.objects.create(mode_slug=slug, course_id=self.course.id)

        targets = [
            BulkEmailTargetChoices.SEND_TO_MYSELF,
            BulkEmailTargetChoices.SEND_TO_STAFF,
            BulkEmailTargetChoices.SEND_TO_LEARNERS,
            f"{BulkEmailTargetChoices.SEND_TO_COHORT}:{cohort.name}",
            f"{BulkEmailTargetChoices.SEND_TO_TRACK}:{slug}"
        ]
        returned_targets = determine_targets_for_course_email(
            self.course.id,
            self.subject,
            targets
        )

        # verify all the targets we expect are there by building a list of the expected (short) display names of each
        # target
        expected_targets = [
            BulkEmailTargetChoices.SEND_TO_MYSELF,
            BulkEmailTargetChoices.SEND_TO_STAFF,
            BulkEmailTargetChoices.SEND_TO_LEARNERS,
            f"cohort-{cohort_name}",
            f"track-{slug}"
        ]
        for target in returned_targets:
            assert target.short_display() in expected_targets

    def test_determine_targets_for_course_email_invalid_target_expect_error(self):
        """
        A test to verify an expected error is thrown when invalid target data is sent to the
        `determine_targets_for_course` function.
        """
        targets = ["OtherGroup"]
        expected_error_msg = (
            f"Course email being sent to an unrecognized target: '{targets[0]}' for '{self.course.id}', "
            f"subject '{self.subject}'"
        )

        with self.assertRaises(ValueError) as value_err:
            determine_targets_for_course_email(
                self.course.id,
                self.subject,
                targets
            )

        assert str(value_err.exception) == expected_error_msg
