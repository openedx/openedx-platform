"""
This file contains tasks that are designed to perform background operations on the
running state of a course.

At present, these tasks all operate on StudentModule objects in one way or another,
so they share a visitor architecture.  Each task defines an "update function" that
takes a block, a particular StudentModule object, and xblock_instance_args.

A task may optionally specify a "filter function" that takes a query for StudentModule
objects, and adds additional filter clauses.

A task also passes through "xblock_instance_args", that are used to provide
information to our code that instantiates xmodule instances.

The task definition then calls the traversal function, passing in the three arguments
above, along with the id value for an InstructorTask object.  The InstructorTask
object contains a 'task_input' row which is a JSON-encoded dict containing
a problem URL and optionally a student.  These are used to set up the initial value
of the query for traversing StudentModule objects.

"""

import logging
from functools import partial

from celery import shared_task
from django.utils.translation import gettext_noop
from edx_django_utils.monitoring import set_code_owner_attribute

from lms.djangoapps.bulk_email.tasks import perform_delegate_email_batches
from lms.djangoapps.instructor_task.tasks_base import BaseInstructorTask
from lms.djangoapps.instructor_task.tasks_helper.certs import generate_students_certificates
from lms.djangoapps.instructor_task.tasks_helper.enrollments import (
    process_student_enrollment_batch,
    upload_inactive_enrolled_students_info_csv,
    upload_may_enroll_csv,
    upload_students_csv,
)
from lms.djangoapps.instructor_task.tasks_helper.grades import CourseGradeReport, ProblemGradeReport, ProblemResponses
from lms.djangoapps.instructor_task.tasks_helper.misc import (
    cohort_students_and_upload,
    upload_course_survey_report,
    upload_ora2_data,
    upload_ora2_submission_files,
    upload_ora2_summary,
    upload_proctored_exam_results_report,
    generate_anonymous_ids
)

from lms.djangoapps.instructor_task.tasks_helper.module_state import (
    delete_problem_module_state,
    override_score_module_state,
    perform_module_state_update,
    rescore_problem_module_state,
    reset_attempts_module_state
)
from lms.djangoapps.instructor_task.tasks_helper.runner import run_main_task

TASK_LOG = logging.getLogger('edx.celery.task')


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def rescore_problem(entry_id, xblock_instance_args):
    """Rescores a problem in a course, for all students or one specific student.

    `entry_id` is the id value of the InstructorTask entry that corresponds to this task.
    The entry contains the `course_id` that identifies the course, as well as the
    `task_input`, which contains task-specific input.

    The task_input should be a dict with the following entries:

      'problem_url': the full URL to the problem to be rescored.  (required)

      'student': the identifier (username or email) of a particular user whose
          problem submission should be rescored.  If not specified, all problem
          submissions for the problem will be rescored.

    `xblock_instance_args` provides information needed by _get_module_instance_for_task()
    to instantiate an xblock instance.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('rescored')
    update_fcn = partial(rescore_problem_module_state, xblock_instance_args)

    visit_fcn = partial(perform_module_state_update, update_fcn, None)
    return run_main_task(entry_id, visit_fcn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def override_problem_score(entry_id, xblock_instance_args):
    """
    Overrides a specific learner's score on a problem.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('overridden')
    update_fcn = partial(override_score_module_state, xblock_instance_args)

    visit_fcn = partial(perform_module_state_update, update_fcn, None)
    return run_main_task(entry_id, visit_fcn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def reset_problem_attempts(entry_id, xblock_instance_args):
    """Resets problem attempts to zero for a particular problem for all students in a course.

    `entry_id` is the id value of the InstructorTask entry that corresponds to this task.
    The entry contains the `course_id` that identifies the course, as well as the
    `task_input`, which contains task-specific input.

    The task_input should be a dict with the following entries:

      'problem_url': the full URL to the problem to be rescored.  (required)

    `xblock_instance_args` provides information needed by _get_module_instance_for_task()
    to instantiate an xblock instance.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('reset')
    update_fcn = partial(reset_attempts_module_state, xblock_instance_args)
    visit_fcn = partial(perform_module_state_update, update_fcn, None)
    return run_main_task(entry_id, visit_fcn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def delete_problem_state(entry_id, xblock_instance_args):
    """Deletes problem state entirely for all students on a particular problem in a course.

    `entry_id` is the id value of the InstructorTask entry that corresponds to this task.
    The entry contains the `course_id` that identifies the course, as well as the
    `task_input`, which contains task-specific input.

    The task_input should be a dict with the following entries:

      'problem_url': the full URL to the problem to be rescored.  (required)

    `xblock_instance_args` provides information needed by _get_module_instance_for_task()
    to instantiate an xblock instance.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('deleted')
    update_fcn = partial(delete_problem_module_state, xblock_instance_args)
    visit_fcn = partial(perform_module_state_update, update_fcn, None)
    return run_main_task(entry_id, visit_fcn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def send_bulk_course_email(entry_id, _xblock_instance_args):
    """Sends emails to recipients enrolled in a course.

    `entry_id` is the id value of the InstructorTask entry that corresponds to this task.
    The entry contains the `course_id` that identifies the course, as well as the
    `task_input`, which contains task-specific input.

    The task_input should be a dict with the following entries:

      'email_id': the full URL to the problem to be rescored.  (required)

    `_xblock_instance_args` provides information needed by _get_module_instance_for_task()
    to instantiate an xblock instance.  This is unused here.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('emailed')
    visit_fcn = perform_delegate_email_batches
    return run_main_task(entry_id, visit_fcn, action_name)


@shared_task(
    name='lms.djangoapps.instructor_task.tasks.calculate_problem_responses_csv.v2',
    base=BaseInstructorTask,
)
@set_code_owner_attribute
def calculate_problem_responses_csv(entry_id, xblock_instance_args):
    """
    Compute student answers to a given problem and upload the CSV to
    an S3 bucket for download.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('generated')
    task_fn = partial(ProblemResponses.generate, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def calculate_grades_csv(entry_id, xblock_instance_args):
    """
    Grade a course and push the results to an S3 bucket for download.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('graded')
    TASK_LOG.info(
        "Task: %s, InstructorTask ID: %s, Task type: %s, Preparing for task execution",
        xblock_instance_args.get('task_id'), entry_id, action_name
    )

    task_fn = partial(CourseGradeReport.generate, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def calculate_problem_grade_report(entry_id, xblock_instance_args):
    """
    Generate a CSV for a course containing all students' problem
    grades and push the results to an S3 bucket for download.
    """
    # Translators: This is a past-tense phrase that is inserted into task progress messages as {action}.
    action_name = gettext_noop('problem distribution graded')
    TASK_LOG.info(
        "Task: %s, InstructorTask ID: %s, Task type: %s, Preparing for task execution",
        xblock_instance_args.get('task_id'), entry_id, action_name
    )

    task_fn = partial(ProblemGradeReport.generate, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def calculate_students_features_csv(entry_id, xblock_instance_args):
    """
    Compute student profile information for a course and upload the
    CSV to an S3 bucket for download.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('generated')
    task_fn = partial(upload_students_csv, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def course_survey_report_csv(entry_id, xblock_instance_args):
    """
    Compute the survey report for a course and upload the
    generated report to an S3 bucket for download.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('generated')
    task_fn = partial(upload_course_survey_report, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def proctored_exam_results_csv(entry_id, xblock_instance_args):
    """
    Compute proctored exam results report for a course and upload the
    CSV for download.
    """
    action_name = 'generating_proctored_exam_results_report'
    task_fn = partial(upload_proctored_exam_results_report, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def calculate_may_enroll_csv(entry_id, xblock_instance_args):
    """
    Compute information about invited students who have not enrolled
    in a given course yet and upload the CSV to an S3 bucket for
    download.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('generated')
    task_fn = partial(upload_may_enroll_csv, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def calculate_inactive_enrolled_students_info_csv(entry_id, xblock_instance_args):
    """
    Compute information about invited students who have not enrolled
    in a given course yet and upload the CSV to an S3 bucket for
    download.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('generated')
    task_fn = partial(upload_inactive_enrolled_students_info_csv, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def generate_certificates(entry_id, xblock_instance_args):
    """
    Grade students and generate certificates.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    action_name = gettext_noop('certificates generated')
    TASK_LOG.info(
        "Task: %s, InstructorTask ID: %s, Task type: %s, Preparing for task execution",
        xblock_instance_args.get('task_id'), entry_id, action_name
    )

    task_fn = partial(generate_students_certificates, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def cohort_students(entry_id, xblock_instance_args):
    """
    Cohort students in bulk, and upload the results.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    # An example of such a message is: "Progress: {action} {succeeded} of {attempted} so far"
    action_name = gettext_noop('cohorted')
    task_fn = partial(cohort_students_and_upload, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def generate_anonymous_ids_for_course(entry_id, xblock_instance_args):
    """
    Generate a CSV of anonymize IDs for enrolled learner for course.
    """
    # Translators: This is a past-tense verb that is inserted into task progress messages as {action}.
    # An example of such a message is: "Progress: {action} {succeeded} of {attempted} so far"
    action_name = gettext_noop('generate_anonymized_id')
    task_fn = partial(generate_anonymous_ids, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def export_ora2_data(entry_id, xblock_instance_args):
    """
    Generate a CSV of ora2 responses and push it to S3.
    """
    action_name = gettext_noop('generated')
    task_fn = partial(upload_ora2_data, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def export_ora2_submission_files(entry_id, xblock_instance_args):
    """
    Download all submission files, generate csv downloads list,
    put all this into zip archive and push it to S3.
    """
    action_name = gettext_noop('compressed')
    task_fn = partial(upload_ora2_submission_files, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def export_ora2_summary(entry_id, xblock_instance_args):
    """
    Generate a CSV of ora2/student summaries and push it to S3.
    """
    action_name = gettext_noop('generated')
    task_fn = partial(upload_ora2_summary, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)


@shared_task(base=BaseInstructorTask)
@set_code_owner_attribute
def student_enrollment_batch(entry_id, xblock_instance_args):
    """
    Process student enrollment/unenrollment operations in batch asynchronously.
    """
    action_name = gettext_noop('processed')
    task_fn = partial(process_student_enrollment_batch, xblock_instance_args)
    return run_main_task(entry_id, task_fn, action_name)
