# pylint: disable=unused-import
"""
Python APIs exposed by the bulk_email app to other in-process apps.
"""
import logging

from django.conf import settings
from django.urls import reverse

from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.bulk_email.data import BulkEmailTargetChoices
from lms.djangoapps.bulk_email.models import CohortTarget, CourseEmail, CourseModeTarget, Target
from lms.djangoapps.bulk_email.models_api import (
    is_bulk_email_disabled_for_course,
    is_bulk_email_feature_enabled,
    is_user_opted_out_for_course,
)
from lms.djangoapps.discussion.notification_prefs.views import UsernameCipher
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.html_to_text import html_to_text

log = logging.getLogger(__name__)


def get_emails_enabled(user, course_id):
    """
    Get whether or not emails are enabled in the context of a course.

    Arguments:
        user: the user object for which we want to check whether emails are enabled
        course_id (string): the course id of the course

    Returns:
        (bool): True if emails are enabled for the course associated with course_id for the user;
        False otherwise
    """
    if is_bulk_email_feature_enabled(course_id=course_id) and not is_bulk_email_disabled_for_course(course_id):
        return not is_user_opted_out_for_course(user=user, course_id=course_id)
    return None


def get_unsubscribed_link(username, course_id):
    """
    :param username: username
    :param course_id:
    :return: AES encrypted token based on the user email
    """
    lms_root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    token = UsernameCipher.encrypt(username)
    optout_url = reverse('bulk_email_opt_out', kwargs={'token': token, 'course_id': course_id})
    url = f'{lms_root_url}{optout_url}'
    return url


def create_course_email(course_id, sender, targets, subject, html_message, text_message=None, template_name=None,
                        from_addr=None):
    """
    Python API for creating a new CourseEmail instance.

    Args:
        course_id (CourseKey): The CourseKey of the course.
        sender (String): Email author.
        targets (List[String]): Recipient groups the message should be sent to.
        subject (String)): Email subject.
        html_message (String): Email body. Includes HTML markup.
        text_message (String, optional): Plaintext version of email body. Defaults to None.
        template_name (String, optional): Name of custom email template to use. Defaults to None.
        from_addr (String, optional): Custom sending address, if desired. Defaults to None.

    Returns:
        CourseEmail: Returns the created CourseEmail instance.
    """
    try:
        course_email = CourseEmail.create(
            course_id,
            sender,
            targets,
            subject,
            html_message,
            text_message=text_message,
            template_name=template_name,
            from_addr=from_addr
        )

        return course_email
    except ValueError as err:
        log.exception(f"Cannot create course email for {course_id} requested by user {sender} for targets {targets}")
        raise ValueError from err


def update_course_email(course_id, email_id, targets, subject, html_message, plaintext_message=None):
    """
    Utility function that allows a course_email instance to be updated after it has been created.

    course_id (CourseKey): The CourseKey of the course.
    email_id (Int): The PK `id` value of the course_email instance that is to be updated.
    targets (List[String]): Recipient groups the message should be sent to.
    subject (String)): Email subject.
    html_message (String): Email body. Includes HTML markup.
    text_message (String, optional): Plaintext version of email body. Defaults to None.
    """
    log.info(f"Updating course email with id '{email_id}' in course '{course_id}'")
    # generate a new stripped version of the plaintext content from the HTML markup
    if plaintext_message is None:
        plaintext_message = html_to_text(html_message)

    # update the targets for the message
    new_targets = determine_targets_for_course_email(course_id, subject, targets)
    if not new_targets:
        raise ValueError("Must specify at least one target (recipient group) for a course email")

    # get the course email and load into memory, update the fields individually since we have to update/set a M2M
    # relationship on the instance
    course_email = CourseEmail.objects.get(course_id=course_id, id=email_id)
    course_email.subject = subject
    course_email.html_message = html_message
    course_email.text_message = plaintext_message
    course_email.save()
    # update the targets M2M relationship
    course_email.targets.clear()
    course_email.targets.add(*new_targets)
    course_email.save()


def get_course_email(email_id):
    """
    Utility function for retrieving a CourseEmail instance from a given CourseEmail id.

    Args:
        email_id (int): The ID of the CourseEmail instance you want to retrieve.

    Returns:
        CourseEmail: The CourseEmail instance, if it exists.
    """
    try:
        return CourseEmail.objects.get(id=email_id)
    except CourseEmail.DoesNotExist:
        log.exception(f"CourseEmail instance with id '{email_id}' could not be found")

    return None


def determine_targets_for_course_email(course_id, subject, targets):
    """
    Utility function to determine the targets (recipient groups) selected by an author of a course email.

    Historically, this used to be a piece of logic in the CourseEmail model's `create` function but has been extracted
    here so it can be used by a REST API of the `instructor_task` app.
    """
    new_targets = []
    for target in targets:
        # split target, to handle cohort:cohort_name and track:mode_slug
        target_split = target.split(':', 1)
        # Ensure our desired target exists
        if not BulkEmailTargetChoices.is_valid_target(target_split[0]):  # pylint: disable=no-else-raise
            raise ValueError(
                f"Course email being sent to an unrecognized target: '{target}' for '{course_id}', subject '{subject}'"
            )
        elif target_split[0] == BulkEmailTargetChoices.SEND_TO_COHORT:
            # target_split[1] will contain the cohort name
            cohort = CohortTarget.ensure_valid_cohort(target_split[1], course_id)
            new_target, _ = CohortTarget.objects.get_or_create(target_type=target_split[0], cohort=cohort)
        elif target_split[0] == BulkEmailTargetChoices.SEND_TO_TRACK:
            # target_split[1] contains the desired mode slug
            CourseModeTarget.ensure_valid_mode(target_split[1], course_id)
            # There could exist multiple CourseModes that match this query, due to differing currency types.
            # The currencies do not affect user lookup though, so we can just use the first result.
            mode = CourseMode.objects.filter(course_id=course_id, mode_slug=target_split[1])[0]
            new_target, _ = CourseModeTarget.objects.get_or_create(target_type=target_split[0], track=mode)
        else:
            new_target, _ = Target.objects.get_or_create(target_type=target_split[0])
        new_targets.append(new_target)

    return new_targets
