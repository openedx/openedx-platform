# pylint: disable=missing-module-docstring

import json
import logging

from celery.states import FAILURE, READY_STATES, REVOKED
from django.http import HttpResponse
from django.utils.translation import gettext as _

from lms.djangoapps.instructor_task.api_helper import get_status_from_instructor_task, get_updated_instructor_task
from lms.djangoapps.instructor_task.models import PROGRESS

log = logging.getLogger(__name__)

# return status for completed tasks and tasks in progress
STATES_WITH_STATUS = list(READY_STATES) + [PROGRESS]


def _get_instructor_task_status(task_id):
    """
    Returns status for a specific task.

    Written as an internal method here (rather than as a helper)
    so that get_task_completion_info() can be called without
    causing a circular dependency (since it's also called directly).
    """
    instructor_task = get_updated_instructor_task(task_id)
    status = get_status_from_instructor_task(instructor_task)
    if instructor_task is not None and instructor_task.task_state in STATES_WITH_STATUS:
        succeeded, message = get_task_completion_info(instructor_task)
        status['message'] = message
        status['succeeded'] = succeeded
    return status


def instructor_task_status(request):
    """
    View method that returns the status of a course-related task or tasks.

    Status is returned as a JSON-serialized dict, wrapped as the content of a HTTPResponse.

    The task_id can be specified to this view in one of two ways:

    * by making a request containing 'task_id' as a parameter with a single value
      Returns a dict containing status information for the specified task_id

    * by making a request containing 'task_ids' as a parameter,
      with a list of task_id values.
      Returns a dict of dicts, with the task_id as key, and the corresponding
      dict containing status information for the specified task_id

      Task_id values that are unrecognized are skipped.

    The dict with status information for a task contains the following keys:
      'message': on complete tasks, status message reporting on final progress,
          or providing exception message if failed.  For tasks in progress,
          indicates the current progress.
      'succeeded': on complete tasks or tasks in progress, boolean value indicates if the
          task outcome was successful:  did it achieve what it set out to do.
          This is in contrast with a successful task_state, which indicates that the
          task merely completed.
      'task_id': id assigned by LMS and used by celery.
      'task_state': state of task as stored in celery's result store.
      'in_progress': boolean indicating if task is still running.
      'task_progress': dict containing progress information.  This includes:
          'attempted': number of attempts made
          'succeeded': number of attempts that "succeeded"
          'total': number of possible subtasks to attempt
          'action_name': user-visible verb to use in status messages.  Should be past-tense.
          'duration_ms': how long the task has (or had) been running.
          'exception': name of exception class raised in failed tasks.
          'message': returned for failed and revoked tasks.
          'traceback': optional, returned if task failed and produced a traceback.

    """
    output = {}
    task_id = request.GET.get('task_id') or request.POST.get('task_id')
    tasks = request.GET.get('task_ids[]') or request.POST.get('task_ids[]')
    if task_id:
        output = _get_instructor_task_status(task_id)
    elif tasks:
        for task_id in tasks:
            task_output = _get_instructor_task_status(task_id)
            if task_output is not None:
                output[task_id] = task_output

    return HttpResponse(json.dumps(output, indent=4))  # pylint: disable=http-response-with-json-dumps


def get_task_completion_info(instructor_task):  # pylint: disable=too-many-statements
    """
    Construct progress message from progress information in InstructorTask entry.

    Returns (boolean, message string) duple, where the boolean indicates
    whether the task completed without incident.  (It is possible for a
    task to attempt many sub-tasks, such as rescoring many students' problem
    responses, and while the task runs to completion, some of the students'
    responses could not be rescored.)

    Used for providing messages to instructor_task_status(), as well as
    external calls for providing course task submission history information.
    """
    succeeded = False

    if instructor_task.task_state not in STATES_WITH_STATUS:
        return (succeeded, _("No status information available"))

    # we're more surprised if there is no output for a completed task, but just warn:
    if instructor_task.task_output is None:
        log.warning(_("No task_output information found for instructor_task {0}").format(instructor_task.task_id))
        return (succeeded, _("No status information available"))

    try:
        task_output = json.loads(instructor_task.task_output)
    except ValueError:
        fmt = _("No parsable task_output information found for instructor_task {0}: {1}")
        log.warning(fmt.format(instructor_task.task_id, instructor_task.task_output))
        return (succeeded, _("No parsable status information available"))

    if instructor_task.task_state in [FAILURE, REVOKED]:
        return (succeeded, task_output.get('message', _('No message provided')))

    if any(key not in task_output for key in ['action_name', 'attempted', 'total']):
        fmt = _("Invalid task_output information found for instructor_task {0}: {1}")
        log.warning(fmt.format(instructor_task.task_id, instructor_task.task_output))
        return (succeeded, _("No progress status information available"))

    action_name = _(task_output['action_name'])  # pylint: disable=translation-of-non-string
    num_attempted = task_output['attempted']
    num_total = task_output['total']

    # In earlier versions of this code, the key 'updated' was used instead of
    # (the more general) 'succeeded'.  In order to support history that may contain
    # output with the old key, we check for values with both the old and the current
    # key, and simply sum them.
    num_succeeded = task_output.get('updated', 0) + task_output.get('succeeded', 0)
    num_skipped = task_output.get('skipped', 0)

    student = None
    problem_url = None
    email_id = None
    try:
        task_input = json.loads(instructor_task.task_input)
    except ValueError:
        fmt = _("No parsable task_input information found for instructor_task {0}: {1}")
        log.warning(fmt.format(instructor_task.task_id, instructor_task.task_input))
    else:
        student = task_input.get('student')
        problem_url = task_input.get('problem_url')
        entrance_exam_url = task_input.get('entrance_exam_url')
        email_id = task_input.get('email_id')

    if instructor_task.task_state == PROGRESS:
        # special message for providing progress updates:
        # Translators: {action} is a past-tense verb that is localized separately. {attempted} and {succeeded} are counts.  # pylint: disable=line-too-long
        msg_format = _("Progress: {action} {succeeded} of {attempted} so far")
    elif student is not None and problem_url is not None:
        # this reports on actions on problems for a particular student:
        if num_attempted == 0:
            # Translators: {action} is a past-tense verb that is localized separately. {student} is a student identifier.  # pylint: disable=line-too-long
            msg_format = _("Unable to find submission to be {action} for student '{student}'")
        elif num_succeeded == 0:
            # Translators: {action} is a past-tense verb that is localized separately. {student} is a student identifier.  # pylint: disable=line-too-long
            msg_format = _("Problem failed to be {action} for student '{student}'")
        else:
            succeeded = True
            # Translators: {action} is a past-tense verb that is localized separately. {student} is a student identifier.  # pylint: disable=line-too-long
            msg_format = _("Problem successfully {action} for student '{student}'")
    elif student is not None and entrance_exam_url is not None:
        # this reports on actions on entrance exam for a particular student:
        if num_attempted == 0:
            # Translators: {action} is a past-tense verb that is localized separately.
            # {student} is a student identifier.
            msg_format = _("Unable to find entrance exam submission to be {action} for student '{student}'")
        else:
            succeeded = True
            # Translators: {action} is a past-tense verb that is localized separately.
            # {student} is a student identifier.
            msg_format = _("Entrance exam successfully {action} for student '{student}'")
    elif student is None and problem_url is not None:
        # this reports on actions on problems for all students:
        if num_attempted == 0:
            # Translators: {action} is a past-tense verb that is localized separately.
            msg_format = _("Unable to find any students with submissions to be {action}")
        elif num_succeeded == 0:
            # Translators: {action} is a past-tense verb that is localized separately. {attempted} is a count.
            msg_format = _("Problem failed to be {action} for any of {attempted} students")
        elif num_succeeded == num_attempted:
            succeeded = True
            # Translators: {action} is a past-tense verb that is localized separately. {attempted} is a count.
            msg_format = _("Problem successfully {action} for {attempted} students")
        else:  # num_succeeded < num_attempted
            # Translators: {action} is a past-tense verb that is localized separately. {succeeded} and {attempted} are counts.  # pylint: disable=line-too-long
            msg_format = _("Problem {action} for {succeeded} of {attempted} students")
    elif email_id is not None:
        # this reports on actions on bulk emails
        if num_attempted == 0:
            # Translators: {action} is a past-tense verb that is localized separately.
            msg_format = _("Unable to find any recipients to be {action}")
        elif num_succeeded == 0:
            # Translators: {action} is a past-tense verb that is localized separately. {attempted} is a count.
            msg_format = _("Message failed to be {action} for any of {attempted} recipients ")
        elif num_succeeded == num_attempted:
            succeeded = True
            # Translators: {action} is a past-tense verb that is localized separately. {attempted} is a count.
            msg_format = _("Message successfully {action} for {attempted} recipients")
        else:  # num_succeeded < num_attempted
            # Translators: {action} is a past-tense verb that is localized separately. {succeeded} and {attempted} are counts.  # pylint: disable=line-too-long
            msg_format = _("Message {action} for {succeeded} of {attempted} recipients")
    else:
        # provide a default:
        # Translators: {action} is a past-tense verb that is localized separately. {succeeded} and {attempted} are counts.  # pylint: disable=line-too-long
        msg_format = _("Status: {action} {succeeded} of {attempted}")

    if num_skipped > 0:
        # Translators: {skipped} is a count.  This message is appended to task progress status messages.
        msg_format += _(" (skipping {skipped})")

    if student is None and num_attempted != num_total:
        # Translators: {total} is a count.  This message is appended to task progress status messages.
        msg_format += _(" (out of {total})")

    # Update status in task result object itself:
    message = msg_format.format(
        action=action_name,
        succeeded=num_succeeded,
        attempted=num_attempted,
        total=num_total,
        skipped=num_skipped,
        student=student
    )
    return (succeeded, message)
