"""
Signal receivers for the "student" application.
"""

# pylint: disable=unused-argument
import logging
from asyncio.log import logger

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from common.djangoapps.student.helpers import EMAIL_EXISTS_MSG_FMT, USERNAME_EXISTS_MSG_FMT, AccountValidationError
from common.djangoapps.student.models import (
    CourseAccessRole,
    CourseEnrollment,
    CourseEnrollmentCelebration,
    PendingNameChange,
    is_email_retired,
    is_username_retired
)
from common.djangoapps.student.models_api import confirm_name_change
from common.djangoapps.student.signals import (
    USER_EMAIL_CHANGED,
    emit_course_access_role_added,
    emit_course_access_role_removed
)
from lms.djangoapps.courseware.toggles import courseware_mfe_progress_milestones_are_active
from lms.djangoapps.utils import get_email_client
from openedx.core.djangoapps.safe_sessions.middleware import EmailChangeMiddleware
from openedx.features.name_affirmation_api.utils import is_name_affirmation_installed

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=get_user_model())
def on_user_updated(sender, instance, **kwargs):
    """
    Check for retired usernames.
    """
    # Check only at User creation time and when not raw.
    if not instance.id and not kwargs['raw']:
        prefix_to_check = getattr(settings, 'RETIRED_USERNAME_PREFIX', None)
        if prefix_to_check:
            # Check for username that's too close to retired username format.
            if instance.username.startswith(prefix_to_check):
                raise AccountValidationError(
                    USERNAME_EXISTS_MSG_FMT.format(username=instance.username),
                    field="username"
                )

        # Check for a retired username.
        if is_username_retired(instance.username):
            raise AccountValidationError(
                USERNAME_EXISTS_MSG_FMT.format(username=instance.username),
                field="username"
            )

        # Check for a retired email.
        if is_email_retired(instance.email):
            raise AccountValidationError(
                EMAIL_EXISTS_MSG_FMT.format(email=instance.email),
                field="email"
            )


@receiver(post_save, sender=CourseEnrollment)
def create_course_enrollment_celebration(sender, instance, created, **kwargs):
    """
    Creates celebration rows when enrollments are created

    This is how we distinguish between new enrollments that we want to celebrate and old ones
    that existed before we introduced a given celebration.
    """
    if not created:
        return

    # The UI for celebrations is only supported on the MFE right now, so don't turn on
    # celebrations unless this enrollment's course is MFE-enabled and has milestones enabled.
    if not courseware_mfe_progress_milestones_are_active(instance.course_id):
        return

    try:
        CourseEnrollmentCelebration.objects.create(
            enrollment=instance,
            celebrate_first_section=True,
            celebrate_weekly_goal=True,
        )
    except IntegrityError:
        # A celebration object was already created. Shouldn't happen, but ignore it if it does.
        pass


@receiver(post_save, sender=CourseAccessRole)
def on_course_access_role_created(sender, instance, created, **kwargs):
    """
    Emit an event to the event-bus when a CourseAccessRole is created
    """
    # Updating a role instance to a different role is unhandled behavior at the moment
    # this event assumes roles are only created or deleted
    if not created:
        return

    user = instance.user
    emit_course_access_role_added(user, instance.course_id, instance.org, instance.role)


@receiver(post_delete, sender=CourseAccessRole)
def listen_for_course_access_role_removed(sender, instance, **kwargs):
    """
    Emit an event to the event-bus when a CourseAccessRole is deleted
    """
    user = instance.user
    emit_course_access_role_removed(user, instance.course_id, instance.org, instance.role)


def listen_for_verified_name_approved(sender, user_id, profile_name, **kwargs):
    """
    If the user has a pending name change that corresponds to an approved verified name, confirm it.
    """
    user = get_user_model().objects.get(id=user_id)
    try:
        pending_name_change = PendingNameChange.objects.get(user=user, new_name=profile_name)
        confirm_name_change(user, pending_name_change)
    except PendingNameChange.DoesNotExist:
        pass


if is_name_affirmation_installed():
    # pylint: disable=import-error
    from edx_name_affirmation.signals import VERIFIED_NAME_APPROVED
    VERIFIED_NAME_APPROVED.connect(listen_for_verified_name_approved)


@receiver(USER_EMAIL_CHANGED)
def _listen_for_user_email_changed(sender, user, request, **kwargs):
    """ If user has changed their email, update that in session and Braze profile. """

    # Store the user's email for session consistency (used by EmailChangeMiddleware)
    EmailChangeMiddleware.register_email_change(request, user.email)

    email = user.email
    user_id = user.id
    attributes = [{'email': email, 'external_id': user_id}]

    try:
        email_client = get_email_client()
        if email_client:
            email_client.track_user(attributes=attributes)
    except Exception as exc:   # pylint: disable=broad-except
        logger.exception(f'Unable to sync new email [{email}] with Braze for user [{user_id}]')
