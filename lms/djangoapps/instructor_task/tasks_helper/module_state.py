"""
Instructor Tasks related to module state.
"""


import json
import logging
from time import time

from django.utils.translation import gettext_noop
from opaque_keys.edx.keys import UsageKey
from xblock.scorable import Score
from xblocks_contrib.problem.capa.responsetypes import LoncapaProblemError, ResponseError, StudentInputError

from common.djangoapps.student.models import get_user_by_username_or_email
from common.djangoapps.track.event_transaction_utils import create_new_event_transaction_id, set_event_transaction_type
from common.djangoapps.track.views import task_track
from common.djangoapps.util.db import outer_atomic
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from lms.djangoapps.courseware.courses import get_problems_in_section
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.grades.api import events as grades_events
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from ..exceptions import UpdateProblemModuleStateError
from .runner import TaskProgress
from .utils import UNKNOWN_TASK_ID, UPDATE_STATUS_FAILED, UPDATE_STATUS_SKIPPED, UPDATE_STATUS_SUCCEEDED

TASK_LOG = logging.getLogger('edx.celery.task')


def perform_module_state_update(update_fcn, filter_fcn, _entry_id, course_id, task_input, action_name):
    """
    Performs generic update by visiting StudentModule instances with the update_fcn provided.

    The student modules are fetched for update the `update_fcn` is called on each StudentModule
    that passes the resulting filtering. It is passed four arguments:  the block for
    the module pointed to by the module_state_key, the particular StudentModule to update, the
    xblock_instance_args, and the task_input being passed through.  If the value returned by the
    update function evaluates to a boolean True, the update is successful; False indicates the update
    on the particular student module failed.
    A raised exception indicates a fatal condition -- that no other student modules should be considered.

    The return value is a dict containing the task's results, with the following keys:

          'attempted': number of attempts made
          'succeeded': number of attempts that "succeeded"
          'skipped': number of attempts that "skipped"
          'failed': number of attempts that "failed"
          'total': number of possible updates to attempt
          'action_name': user-visible verb to use in status messages.  Should be past-tense.
              Pass-through of input `action_name`.
          'duration_ms': how long the task has (or had) been running.

    Because this is run internal to a task, it does not catch exceptions.  These are allowed to pass up to the
    next level, so that it can set the failure modes and capture the error trace in the InstructorTask and the
    result object.

    """
    start_time = time()
    usage_keys = []
    problem_url = task_input.get('problem_url')
    entrance_exam_url = task_input.get('entrance_exam_url')
    student_identifier = task_input.get('student')
    override_score_task = action_name == gettext_noop('overridden')
    problems = {}

    # if problem_url is present make a usage key from it
    if problem_url:
        usage_key = UsageKey.from_string(problem_url).map_into_course(course_id)
        usage_keys.append(usage_key)

        # find the problem block:
        problem_block = modulestore().get_item(usage_key)
        problems[str(usage_key)] = problem_block

    # if entrance_exam is present grab all problems in it
    if entrance_exam_url:
        problems = get_problems_in_section(entrance_exam_url)
        usage_keys = [UsageKey.from_string(location) for location in problems.keys()]

    modules_to_update = _get_modules_to_update(
        course_id, usage_keys, student_identifier, filter_fcn, override_score_task
    )

    task_progress = TaskProgress(action_name, len(modules_to_update), start_time)
    task_progress.update_task_state()

    for module_to_update in modules_to_update:
        task_progress.attempted += 1
        block = problems[str(module_to_update.module_state_key)]
        # There is no try here:  if there's an error, we let it throw, and the task will
        # be marked as FAILED, with a stack trace.
        update_status = update_fcn(block, module_to_update, task_input)
        if update_status == UPDATE_STATUS_SUCCEEDED:
            # If the update_fcn returns true, then it performed some kind of work.
            # Logging of failures is left to the update_fcn itself.
            task_progress.succeeded += 1
        elif update_status == UPDATE_STATUS_FAILED:
            task_progress.failed += 1
        elif update_status == UPDATE_STATUS_SKIPPED:
            task_progress.skipped += 1
        else:
            raise UpdateProblemModuleStateError(f"Unexpected update_status returned: {update_status}")

    return task_progress.update_task_state()


@outer_atomic
def rescore_problem_module_state(xblock_instance_args, block, student_module, task_input):
    '''
    Takes an XBlock and a corresponding StudentModule object, and
    performs rescoring on the student's problem submission.

    Throws exceptions if the rescoring is fatal and should be aborted if in a loop.
    In particular, raises UpdateProblemModuleStateError if module fails to instantiate,
    or if the module doesn't support rescoring.

    Returns True if problem was successfully rescored for the given student, and False
    if problem encountered some kind of error in rescoring.
    '''
    # unpack the StudentModule:
    course_id = student_module.course_id
    student = student_module.student
    usage_key = student_module.module_state_key

    with modulestore().bulk_operations(course_id):
        course = get_course_by_id(course_id)
        # TODO: Here is a call site where we could pass in a loaded course.  I
        # think we certainly need it since grading is happening here, and field
        # overrides would be important in handling that correctly
        instance = _get_module_instance_for_task(
            course_id,
            student,
            block,
            xblock_instance_args,
            grade_bucket_type='rescore',
            course=course
        )

        if instance is None:
            # Either permissions just changed, or someone is trying to be clever
            # and load something they shouldn't have access to.
            msg = "No module {location} for student {student}--access denied?".format(  # noqa: UP032
                location=usage_key,
                student=student
            )
            TASK_LOG.warning(msg)
            return UPDATE_STATUS_FAILED

        if not hasattr(instance, 'rescore'):
            # This should not happen, since it should be already checked in the
            # caller, but check here to be sure.
            msg = f"Specified module {usage_key} of type {instance.__class__} does not support rescoring."
            raise UpdateProblemModuleStateError(msg)

        # We check here to see if the problem has any submissions. If it does not, we don't want to rescore it
        if not instance.has_submitted_answer():
            return UPDATE_STATUS_SKIPPED

        # Set the tracking info before this call, because it makes downstream
        # calls that create events.  We retrieve and store the id here because
        # the request cache will be erased during downstream calls.
        create_new_event_transaction_id()
        set_event_transaction_type(grades_events.GRADES_RESCORE_EVENT_TYPE)

        # specific events from CAPA are not propagated up the stack. Do we want this?
        try:
            instance.rescore(only_if_higher=task_input['only_if_higher'])
        except (LoncapaProblemError, ResponseError):
            # Capture a backtrace for these errors, but only a warning below for student input errors.
            TASK_LOG.exception(
                "error processing rescore call for course %(course)s, problem %(loc)s "
                "and student %(student)s",
                dict(
                    course=course_id,
                    loc=usage_key,
                    student=student
                )
            )
        except StudentInputError:
            TASK_LOG.warning(
                "error processing rescore call for course %(course)s, problem %(loc)s "
                "and student %(student)s",
                dict(
                    course=course_id,
                    loc=usage_key,
                    student=student
                )
            )
            return UPDATE_STATUS_FAILED

        instance.save()
        TASK_LOG.debug(
            "successfully processed rescore call for course %(course)s, problem %(loc)s "
            "and student %(student)s",
            dict(
                course=course_id,
                loc=usage_key,
                student=student
            )
        )

        return UPDATE_STATUS_SUCCEEDED


@outer_atomic
def override_score_module_state(xblock_instance_args, block, student_module, task_input):
    '''
    Takes an XBlock and a corresponding StudentModule object, and
    performs an override on the student's problem score.

    Throws exceptions if the override is fatal and should be aborted if in a loop.
    In particular, raises UpdateProblemModuleStateError if module fails to instantiate,
    or if the module doesn't support overriding, or if the score used for override
    is outside the acceptable range of scores (between 0 and the max score for the
    problem).

    Returns True if problem was successfully overriden for the given student, and False
    if problem encountered some kind of error in overriding.
    '''
    # unpack the StudentModule:
    course_id = student_module.course_id
    student = student_module.student
    usage_key = student_module.module_state_key

    with modulestore().bulk_operations(course_id):
        course = get_course_by_id(course_id)
        instance = _get_module_instance_for_task(
            course_id,
            student,
            block,
            xblock_instance_args,
            course=course
        )

        if instance is None:
            # Either permissions just changed, or someone is trying to be clever
            # and load something they shouldn't have access to.
            msg = "No module {location} for student {student}--access denied?".format(  # noqa: UP032
                location=usage_key,
                student=student
            )
            TASK_LOG.warning(msg)
            return UPDATE_STATUS_FAILED

        if not hasattr(instance, 'set_score'):
            msg = "Scores cannot be overridden for this problem type."
            raise UpdateProblemModuleStateError(msg)

        weighted_override_score = float(task_input['score'])
        if not (0 <= weighted_override_score <= instance.max_score()):  # pylint: disable=superfluous-parens
            msg = "Score must be between 0 and the maximum points available for the problem."
            raise UpdateProblemModuleStateError(msg)

        # Set the tracking info before this call, because it makes downstream
        # calls that create events.  We retrieve and store the id here because
        # the request cache will be erased during downstream calls.
        create_new_event_transaction_id()
        set_event_transaction_type(grades_events.GRADES_OVERRIDE_EVENT_TYPE)

        problem_weight = instance.weight if instance.weight is not None else 1
        if problem_weight == 0:  # pylint: disable=no-else-raise
            msg = "Scores cannot be overridden for a problem that has a weight of zero."
            raise UpdateProblemModuleStateError(msg)
        else:
            instance.set_score(Score(
                raw_earned=weighted_override_score / problem_weight,
                raw_possible=instance.max_score() / problem_weight
            ))

        instance.publish_grade()
        instance.save()
        TASK_LOG.debug(
            "successfully processed score override for course %(course)s, problem %(loc)s "
            "and student %(student)s",
            dict(
                course=course_id,
                loc=usage_key,
                student=student
            )
        )

        return UPDATE_STATUS_SUCCEEDED


@outer_atomic
def reset_attempts_module_state(xblock_instance_args, _block, student_module, _task_input):
    """
    Resets problem attempts to zero for specified `student_module`.

    Returns a status of UPDATE_STATUS_SUCCEEDED if a problem has non-zero attempts
    that are being reset, and UPDATE_STATUS_SKIPPED otherwise.
    """
    update_status = UPDATE_STATUS_SKIPPED
    problem_state = json.loads(student_module.state) if student_module.state else {}
    if 'attempts' in problem_state:
        old_number_of_attempts = problem_state["attempts"]
        if old_number_of_attempts > 0:
            problem_state["attempts"] = 0
            # convert back to json and save
            student_module.state = json.dumps(problem_state)
            student_module.save()
            # get request-related tracking information from args passthrough,
            # and supplement with task-specific information:
            track_function = _get_track_function_for_task(student_module.student, xblock_instance_args)
            event_info = {"old_attempts": old_number_of_attempts, "new_attempts": 0}
            track_function('problem_reset_attempts', event_info)
            update_status = UPDATE_STATUS_SUCCEEDED

    return update_status


@outer_atomic
def delete_problem_module_state(xblock_instance_args, _block, student_module, _task_input):
    """
    Delete the StudentModule entry.

    Always returns UPDATE_STATUS_SUCCEEDED, indicating success, if it doesn't raise an exception due to database error.
    """
    student_module.delete()
    # get request-related tracking information from args passthrough,
    # and supplement with task-specific information:
    track_function = _get_track_function_for_task(student_module.student, xblock_instance_args)
    track_function('problem_delete_state', {})
    return UPDATE_STATUS_SUCCEEDED


def _get_module_instance_for_task(course_id, student, block, xblock_instance_args=None,
                                  grade_bucket_type=None, course=None):
    """
    Fetches a StudentModule instance for a given `course_id`, `student` object, and `block`.

    `xblock_instance_args` is used to provide information for creating a track function.
    It is passed, along with `grade_bucket_type`, to get_block_for_descriptor.
    """
    # get request-related tracking information from args passthrough, and supplement with task-specific information:
    request_info = xblock_instance_args.get('request_info', {}) if xblock_instance_args is not None else {}
    task_info = {"student": student.username, "task_id": _get_task_id_from_xblock_args(xblock_instance_args)}

    def make_track_function():
        '''
        Make a tracking function that logs what happened.

        For insertion into runtime, and used by CapaModule, which will
        provide the event_type (as string) and event (as dict) as arguments.
        The request_info and task_info (and page) are provided here.
        '''
        return lambda event_type, event: task_track(request_info, task_info, event_type, event, page='x_module_task')

    return get_block_for_descriptor(
        user=student,
        request=None,
        block=block,
        field_data_cache=FieldDataCache.cache_for_block_descendents(course_id, student, block),
        course_key=course_id,
        track_function=make_track_function(),
        grade_bucket_type=grade_bucket_type,
        # This module isn't being used for front-end rendering
        request_token=None,
        # pass in a loaded course for override enabling
        course=course
    )


def _get_track_function_for_task(student, xblock_instance_args=None, source_page='x_module_task'):
    """
    Make a tracking function that logs what happened.

    For insertion into runtime, and used by CapaModule, which will
    provide the event_type (as string) and event (as dict) as arguments.
    The request_info and task_info (and page) are provided here.
    """
    # get request-related tracking information from args passthrough, and supplement with task-specific
    # information:
    request_info = xblock_instance_args.get('request_info', {}) if xblock_instance_args is not None else {}
    task_info = {'student': student.username, 'task_id': _get_task_id_from_xblock_args(xblock_instance_args)}

    return lambda event_type, event: task_track(request_info, task_info, event_type, event, page=source_page)


def _get_task_id_from_xblock_args(xblock_instance_args):
    """Gets task_id from `xblock_instance_args` dict, or returns default value if missing."""
    if xblock_instance_args is None:
        return UNKNOWN_TASK_ID
    else:
        return xblock_instance_args.get('task_id', UNKNOWN_TASK_ID)


def _get_modules_to_update(course_id, usage_keys, student_identifier, filter_fcn, override_score_task=False):
    """
    Fetches a StudentModule instances for a given `course_id`, `student` object, and `usage_keys`.

    StudentModule instances are those that match the specified `course_id` and `module_state_key`.
    If `student_identifier` is not None, it is used as an additional filter to limit the modules to those belonging
    to that student. If `student_identifier` is None, performs update on modules for all students on the specified
    problem.
    The matched instances are then applied `filter_fcn` if not None. It filters out the matched instances.
    It takes one argument, which is the query being filtered, and returns the filtered version of the query.
    If `override_score_task` is True and we there were not matching instances of StudentModule, try to create
    those instances. This is only for override scores and the use case is for learners that have missed the deadline.

    Arguments:
        course_id(str): The unique identifier for the course.
        usage_keys(list): List of UsageKey objects
        student_identifier(str): Identifier for a student or None. The identifier can be either username or email
        filter_fcn: If it is not None, it is applied to the query that has been constructed.
        override_score_task (bool): Optional argument which indicates if it is an override score or not.
    """
    def get_student():
        """ Fetches student instance if an identifier is provided, else return None """
        return None if not student_identifier else get_user_by_username_or_email(student_identifier)

    module_query_params = {'course_id': course_id, 'module_state_keys': usage_keys}

    # give the option of updating an individual student. If not specified,
    # then updates all students who have responded to a problem so far
    student = get_student()
    if student:
        module_query_params['student_id'] = student.id

    student_modules = StudentModule.get_state_by_params(**module_query_params)
    if filter_fcn is not None:
        student_modules = filter_fcn(student_modules)

    can_create_student_modules = (override_score_task and (student_modules.count() == 0) and student is not None)
    if can_create_student_modules:
        student_modules = [
            StudentModule.objects.get_or_create(course_id=course_id, student=student, module_state_key=key)[0]
            for key in usage_keys
        ]
    return student_modules
