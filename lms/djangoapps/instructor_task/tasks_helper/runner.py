"""
Instrutor Task runner
"""


import json  # noqa: I001
import logging
from time import time

from celery import current_task
from django.db import reset_queries

from common.djangoapps.util.db import outer_atomic
from lms.djangoapps.instructor_task.models import PROGRESS, InstructorTask

TASK_LOG = logging.getLogger('edx.celery.task')


class TaskProgress:
    """
    Encapsulates the current task's progress by keeping track of
    'attempted', 'succeeded', 'skipped', 'failed', 'total',
    'action_name', and 'duration_ms' values.
    """
    def __init__(self, action_name, total, start_time):
        self.action_name = action_name
        self.total = total
        self.start_time = start_time
        self.attempted = 0
        self.succeeded = 0
        self.skipped = 0
        self.failed = 0
        self.preassigned = 0

    @property
    def state(self):
        return {
            'action_name': self.action_name,
            'attempted': self.attempted,
            'succeeded': self.succeeded,
            'skipped': self.skipped,
            'failed': self.failed,
            'total': self.total,
            'preassigned': self.preassigned,
            'duration_ms': int((time() - self.start_time) * 1000),
        }

    def update_task_state(self, extra_meta=None):
        """
        Update the current celery task's state to the progress state
        specified by the current object.  Returns the progress
        dictionary for use by `run_main_task` and
        `BaseInstructorTask.on_success`.

        Arguments:
            extra_meta (dict): Extra metadata to pass to `update_state`

        Returns:
            dict: The current task's progress dict
        """
        progress_dict = self.state
        if extra_meta is not None:
            progress_dict.update(extra_meta)
        _get_current_task().update_state(state=PROGRESS, meta=progress_dict)
        return progress_dict


def run_main_task(entry_id, task_fcn, action_name):
    """
    Applies the `task_fcn` to the arguments defined in `entry_id` InstructorTask.

    Arguments passed to `task_fcn` are:

     `entry_id` : the primary key for the InstructorTask entry representing the task.
     `course_id` : the id for the course.
     `task_input` : dict containing task-specific arguments, JSON-decoded from InstructorTask's task_input.
     `action_name` : past-tense verb to use for constructing status messages.

    If no exceptions are raised, the `task_fcn` should return a dict containing
    the task's result with the following keys:

          'attempted': number of attempts made
          'succeeded': number of attempts that "succeeded"
          'skipped': number of attempts that "skipped"
          'failed': number of attempts that "failed"
          'total': number of possible subtasks to attempt
          'action_name': user-visible verb to use in status messages.
              Should be past-tense.  Pass-through of input `action_name`.
          'duration_ms': how long the task has (or had) been running.

    """

    # Get the InstructorTask to be updated. If this fails then let the exception return to Celery.
    # There's no point in catching it here.
    with outer_atomic():
        entry = InstructorTask.objects.get(pk=entry_id)
        entry.task_state = PROGRESS
        entry.save_now()

    # Get inputs to use in this task from the entry
    task_id = entry.task_id
    course_id = entry.course_id
    task_input = json.loads(entry.task_input)

    # Construct log message
    fmt = 'Task: {task_id}, InstructorTask ID: {entry_id}, Course: {course_id}, Input: {task_input}'
    task_info_string = fmt.format(task_id=task_id, entry_id=entry_id, course_id=course_id, task_input=task_input)
    TASK_LOG.info('%s, Starting update (nothing %s yet)', task_info_string, action_name)

    # Check that the task_id submitted in the InstructorTask matches the current task
    # that is running.
    request_task_id = _get_current_task().request.id
    if task_id != request_task_id:
        fmt = '{task_info}, Requested task did not match actual task "{actual_id}"'
        message = fmt.format(task_info=task_info_string, actual_id=request_task_id)
        TASK_LOG.error(message)
        raise ValueError(message)

    # Now do the work
    task_progress = task_fcn(entry_id, course_id, task_input, action_name)

    # Release any queries that the connection has been hanging onto
    reset_queries()

    # Log and exit, returning task_progress info as task result
    TASK_LOG.info('%s, Task type: %s, Finishing task: %s', task_info_string, action_name, task_progress)
    return task_progress


def _get_current_task():
    """
    Stub to make it easier to test without actually running Celery.

    This is a wrapper around celery.current_task, which provides access
    to the top of the stack of Celery's tasks.  When running tests, however,
    it doesn't seem to work to mock current_task directly, so this wrapper
    is used to provide a hook to mock in tests, while providing the real
    `current_task` in production.
    """
    return current_task
