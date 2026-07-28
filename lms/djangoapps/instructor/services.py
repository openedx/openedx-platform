"""
Implementation of "Instructor" service
"""


import logging

from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey

import lms.djangoapps.instructor.enrollment as enrollment
from common.djangoapps.student import auth
from common.djangoapps.student.models import get_user_by_username_or_email
from common.djangoapps.student.roles import CourseStaffRole
from lms.djangoapps.commerce.utils import create_zendesk_ticket
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor.tasks import update_exam_completion_task
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


class InstructorService:
    """
    Instructor service for deleting the students attempt(s) of an exam. This service has been created
    for the edx_proctoring's dependency injection to cater for a requirement where edx_proctoring
    needs to call into edx-platform's functions to delete the students' existing answers, grades
    and attempt counts if there had been an earlier attempt.

    This service also contains utility functions to check if a user is course staff, send notifications
    related to proctored exam attempts, and retrieve a course team's proctoring escalation email.
    """

    def delete_student_attempt(self, student_identifier, course_id, content_id, requesting_user):
        """
        Deletes student state for a problem. requesting_user may be kept as an audit trail.

        Takes some of the following query parameters
            - student_identifier is an email or username
            - content_id is a url-name of a problem
            - course_id is the id for the course
        """
        course_id = CourseKey.from_string(course_id)

        try:
            student = get_user_by_username_or_email(student_identifier)
        except ObjectDoesNotExist:
            err_msg = (
                'Error occurred while attempting to reset student attempts for user '
                f'{student_identifier} for content_id {content_id}. '
                'User does not exist!'
            )
            log.error(err_msg)
            return

        try:
            module_state_key = UsageKey.from_string(content_id)
        except InvalidKeyError:
            err_msg = (
                f'Invalid content_id {content_id}!'
            )
            log.error(err_msg)
            return

        if student:
            try:
                enrollment.reset_student_attempts(
                    course_id,
                    student,
                    module_state_key,
                    requesting_user=requesting_user,
                    delete_module=True,
                )
            except (StudentModule.DoesNotExist, enrollment.sub_api.SubmissionError):
                err_msg = (
                    'Error occurred while attempting to reset student attempts for user '
                    f'{student_identifier} for content_id {content_id}.'
                )
                log.error(err_msg)

            # In some cases, reset_student_attempts does not clear the entire exam's completion state.
            # One example of this is an exam with multiple units (verticals) within it and the learner
            # never viewing one of the units. All of the content in that unit will still be marked complete,
            # but the reset code is unable to handle clearing the completion in that scenario.
            update_exam_completion_task.apply_async((student_identifier, content_id, 0.0))

    def complete_student_attempt(self, user_identifier: str, content_id: str) -> None:
        """
        Calls the update_exam_completion_task, marking the exam as complete.

        The task submits all completable xblocks inside of the content_id block to the
        Completion Service to mark them as complete. One use case of this function is
        for special exams (timed/proctored) where regardless of submission status on
        individual problems, we want to mark the entire exam as complete when the exam
        is finished.

        params:
            user_identifier (str): username or email of a user
            content_id (str): the block key for a piece of content
        """
        update_exam_completion_task.apply_async((user_identifier, content_id, 1.0))

    def is_course_staff(self, user, course_id):
        """
        Returns True if the user is the course staff
        else Returns False
        """
        return auth.user_has_role(user, CourseStaffRole(CourseKey.from_string(course_id)))

    def send_support_notification(self, course_id, exam_name, student_username, review_status, review_url=None):
        """
        Creates a Zendesk ticket for an exam attempt review from the proctoring system.
        Currently, it sends notifications for 'Suspicious" status, but additional statuses can be supported
        by adding to the notify_support_for_status list in edx_proctoring/backends/software_secure.py
        The notifications can be disabled by disabling the
        "Create Zendesk Tickets For Suspicious Proctored Exam Attempts" setting in the course's Advanced settings.
        """

        course_key = CourseKey.from_string(course_id)
        course = modulestore().get_course(course_key)

        if course.create_zendesk_tickets:
            requester_name = "edx-proctoring"
            email = "edx-proctoring@edx.org"
            subject = _("Proctored Exam Review: {review_status}").format(review_status=review_status)
            body = _(
                "A proctored exam attempt for {exam_name} in {course_name} by username: {student_username} "
                "was reviewed as {review_status} by the proctored exam review provider.\n"
                "Review link: {review_url}"
            ).format(
                exam_name=exam_name,
                course_name=course.display_name,
                student_username=student_username,
                review_status=review_status,
                review_url=review_url or 'not available',
            )
            tags = ["proctoring"]
            create_zendesk_ticket(requester_name, email, subject, body, tags)

    def get_proctoring_escalation_email(self, course_id):
        """
        Returns the proctoring escalation email for a course, or None if not given.

        Example arguments:
        * course_id (String): 'block-v1:edX+DemoX+Demo_Course'
        """
        try:
            # Convert course id into course key
            course_key = CourseKey.from_string(course_id)
        except AttributeError:
            # If a course key object is given instead of a string, ensure that it is used
            course_key = course_id
        course = modulestore().get_course(course_key)
        if course is None:
            raise ObjectDoesNotExist(
                'Could not find proctoring escalation email for course_id={course_id}.'  # noqa: UP032
                ' This course does not exist.'.format(course_id=course_id)
            )

        return course.proctoring_escalation_email
