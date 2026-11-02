"""
Receivers of signals sent from django-user-tasks
"""
import logging
import re
from urllib.parse import urljoin

from django.dispatch import receiver
from django.urls import reverse
from user_tasks.models import UserTaskArtifact
from user_tasks.signals import user_task_stopped

from cms.djangoapps.contentstore.toggles import bypass_olx_failure_enabled
from cms.djangoapps.contentstore.utils import course_import_olx_validation_is_enabled
from openedx.core.djangoapps.content_libraries.api import is_library_backup_task, is_library_restore_task

from .tasks import send_task_complete_email

LOGGER = logging.getLogger(__name__)
LIBRARY_CONTENT_TASK_NAME_TEMPLATE = 'updating .*type@library_content.* from library'


@receiver(user_task_stopped, dispatch_uid="cms_user_task_stopped")
def user_task_stopped_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Handles sending notifications when a django-user-tasks completes.
    This is a signal receiver for user_task_stopped. Currently it only sends
    a generic "task completed" email, and only when a top-level task
    completes. Eventually it might make more sense to create specific per-task
    handlers.
    Arguments:
        sender (obj): Currently the UserTaskStatus object class
        **kwargs: See below
    Keywork Arguments:
        status (obj): UserTaskStatus of the completed task
    Returns:
        None
    """

    def is_library_content_update(task_name: str) -> bool:
        """
        Decides whether to suppress an end-of-task email on the basis that the just-ended task was a library content
        XBlock update operation, and that emails following such operations amount to spam
        Arguments:
            task_name: The name of the just-ended task. By convention, if this was a library content XBlock update
            task, then the task name follows the pattern prescribed in LibrarySyncChildrenTask
            (content_libraries under openedx) 'Updating {key} from library'. Moreover, the block type
            in the task name is always of type 'library_content' for such operations
        Returns:
            True if the end-of-task email should be suppressed
        """
        p = re.compile(LIBRARY_CONTENT_TASK_NAME_TEMPLATE)
        return p.match(task_name)

    def get_olx_validation_from_artifact():
        """
        Get olx validation error if available for current task.
        Returns:
            olx validation string or None.
        """
        if not course_import_olx_validation_is_enabled():
            return None

        olx_artifact = UserTaskArtifact.objects.filter(status=status, name="OLX_VALIDATION_ERROR").first()
        if olx_artifact and not bypass_olx_failure_enabled():
            return olx_artifact.text

    def should_skip_end_of_task_email(task_name) -> bool:
        """
        Studio tasks generally send an email when finished, but not always.

        Some tasks can last many minutes, e.g. course import/export. For these
        tasks, there is a high chance that the user has navigated away and will
        want to check back in later. Yet email notification is unnecessary and
        distracting for things like the Library restore task, which is
        relatively quick and cannot be resumed (i.e. if you navigate away, you
        have to upload again).

        The task_name passed in will be lowercase.
        """
        # We currently have to pattern match on the name to differentiate
        # between tasks. A better long term solution would be to add a separate
        # task type identifier field to Django User Tasks.
        return (
            is_library_content_update(task_name) or
            is_library_backup_task(task_name) or
            is_library_restore_task(task_name)
        )

    status = kwargs['status']

    # Only send email when the entire task is complete, should only send when
    # a chain / chord / etc completes, not on sub-tasks.
    if status.parent is None:
        task_name = status.name.lower()

        # Also suppress emails on library content XBlock updates (too much like spam)
        if should_skip_end_of_task_email(task_name):
            LOGGER.info(f"Suppressing end-of-task email on task {task_name}")
            return

        # `name` and `status` are not unique, first is our best guess
        artifact = UserTaskArtifact.objects.filter(status=status, name="BASE_URL").first()

        detail_url = None
        if artifact and artifact.url.startswith(('http://', 'https://')):
            detail_url = urljoin(
                artifact.url,
                reverse('usertaskstatus-detail', args=[status.uuid])
            )

        # check if this is a course optimizer task
        is_course_optimizer_task = False
        course_optimizer_artifact = UserTaskArtifact.objects.filter(status=status, name="BrokenLinks").first()
        if course_optimizer_artifact:
            is_course_optimizer_task = True

        user_email = status.user.email
        olx_validation_text = get_olx_validation_from_artifact()
        task_args = [task_name, str(status.state_text), user_email, detail_url,
                     olx_validation_text, is_course_optimizer_task]
        try:
            send_task_complete_email.delay(*task_args)
        except Exception:  # pylint: disable=broad-except
            LOGGER.exception("Unable to queue send_task_complete_email")
