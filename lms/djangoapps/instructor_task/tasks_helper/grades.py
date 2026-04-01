"""
Functionality for generating grade reports.
"""

import csv
import logging
import re
from collections import OrderedDict, defaultdict
from datetime import datetime
from itertools import chain
from tempfile import TemporaryFile

from time import time

from django.conf import settings
from django.contrib.auth import get_user_model
from lazy import lazy
from opaque_keys.edx.keys import UsageKey
from pytz import UTC
from six.moves import zip_longest

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import BulkRoleCache
from lms.djangoapps.certificates import api as certs_api
from lms.djangoapps.certificates.api import get_certificates_for_course_and_users
from lms.djangoapps.course_blocks.api import get_course_block_access_transformers, get_course_blocks
from lms.djangoapps.course_blocks.transformers import library_content
from lms.djangoapps.courseware.user_state_client import DjangoXBlockUserStateClient
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.grades.api import context as grades_context
from lms.djangoapps.grades.api import prefetch_course_and_subsection_grades
from lms.djangoapps.instructor_analytics.basic import list_problem_responses
from lms.djangoapps.instructor_analytics.csvs import format_dictlist
from lms.djangoapps.instructor_task.config.waffle import (
    course_grade_report_verified_only,
    problem_grade_report_verified_only,
    use_on_disk_grade_reporting,
)
from lms.djangoapps.teams.models import CourseTeamMembership
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.content.block_structure.api import get_course_in_cache
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.core.djangoapps.course_groups.cohorts import bulk_cache_cohorts, get_cohort, is_course_cohorted
from openedx.core.djangoapps.user_api.course_tag.api import BulkCourseTags
from openedx.core.lib.cache_utils import get_cache
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.partitions.partitions_service import PartitionService  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.split_test_block import get_split_user_partitions  # lint-amnesty, pylint: disable=wrong-import-order

from .runner import TaskProgress
from .utils import upload_csv_to_report_store, upload_csv_file_to_report_store

TASK_LOG = logging.getLogger('edx.celery.task')

ENROLLED_IN_COURSE = 'enrolled'

NOT_ENROLLED_IN_COURSE = 'unenrolled'


def _user_enrollment_status(user, course_id):
    """
    Returns the enrollment activation status in the given course
    for the given user.
    """
    enrollment_is_active = CourseEnrollment.enrollment_mode_for_user(user, course_id)[1]
    if enrollment_is_active:
        return ENROLLED_IN_COURSE
    return NOT_ENROLLED_IN_COURSE


def _flatten(iterable):
    return list(chain.from_iterable(iterable))


class _CourseGradeReportContext:
    """
    Internal class that provides a common context to use for a single grade
    report.  When a report is parallelized across multiple processes,
    elements of this context are serialized and parsed across process
    boundaries.
    """

    def __init__(self, _xblock_instance_args, _entry_id, course_id, _task_input, action_name):
        self.task_info_string = (
            'Task: {task_id}, '
            'InstructorTask ID: {entry_id}, '
            'Course: {course_id}, '
            'Input: {task_input}'
        ).format(
            task_id=_xblock_instance_args.get('task_id') if _xblock_instance_args is not None else None,
            entry_id=_entry_id,
            course_id=course_id,
            task_input=_task_input,
        )
        self.action_name = action_name
        self.course_id = course_id
        self.task_progress = TaskProgress(self.action_name, total=None, start_time=time())
        self.report_for_verified_only = course_grade_report_verified_only(self.course_id)
        self.upload_parent_dir = _task_input.get('upload_parent_dir', '')
        self.upload_filename = _task_input.get('filename', 'grade_report')

    @lazy
    def course(self):
        return get_course_by_id(self.course_id)

    @lazy
    def course_structure(self):
        return get_course_in_cache(self.course_id)

    @lazy
    def course_experiments(self):
        return get_split_user_partitions(self.course.user_partitions)

    @lazy
    def teams_enabled(self):
        return self.course.teams_enabled

    @lazy
    def cohorts_enabled(self):
        return is_course_cohorted(self.course_id)

    @lazy
    def graded_assignments(self):
        """
        Returns an OrderedDict that maps an assignment type to a dict of
        subsection-headers and average-header.
        """
        grading_cxt = grades_context.grading_context(self.course, self.course_structure)
        graded_assignments_map = OrderedDict()
        for assignment_type_name, subsection_infos in grading_cxt['all_graded_subsections_by_type'].items():
            graded_subsections_map = OrderedDict()
            for subsection_index, subsection_info in enumerate(subsection_infos, start=1):
                subsection = subsection_info['subsection_block']
                header_name = "{assignment_type} {subsection_index}: {subsection_name}".format(
                    assignment_type=assignment_type_name,
                    subsection_index=subsection_index,
                    subsection_name=subsection.display_name,
                )
                graded_subsections_map[subsection.location] = header_name

            average_header = f"{assignment_type_name}"

            # Use separate subsection and average columns only if
            # there's more than one subsection.
            separate_subsection_avg_headers = len(subsection_infos) > 1
            if separate_subsection_avg_headers:
                average_header += " (Avg)"

            graded_assignments_map[assignment_type_name] = {
                'subsection_headers': graded_subsections_map,
                'average_header': average_header,
                'separate_subsection_avg_headers': separate_subsection_avg_headers,
                'grader': grading_cxt['subsection_type_graders'].get(assignment_type_name),
            }
        return graded_assignments_map

    def update_status(self, message):
        """
        Updates the status on the celery task to the given message.
        Also logs the update.
        """
        TASK_LOG.info('%s, Task type: %s, %s', self.task_info_string, self.action_name, message)
        return self.task_progress.update_task_state(extra_meta={'step': message})


class _ProblemGradeReportContext:
    """
    Internal class that provides a common context to use for a single problem
    grade report.  When a report is parallelized across multiple processes,
    elements of this context are serialized and parsed across process
    boundaries.
    """

    def __init__(self, _xblock_instance_args, _entry_id, course_id, _task_input, action_name):
        task_id = _xblock_instance_args.get('task_id') if _xblock_instance_args is not None else None
        self.task_info_string = (
            'Task: {task_id}, '
            'InstructorTask ID: {entry_id}, '
            'Course: {course_id}, '
            'Input: {task_input}'
        ).format(
            task_id=task_id,
            entry_id=_entry_id,
            course_id=course_id,
            task_input=_task_input,
        )
        self.task_id = task_id
        self.entry_id = _entry_id
        self.task_input = _task_input
        self.action_name = action_name
        self.course_id = course_id
        self.report_for_verified_only = problem_grade_report_verified_only(self.course_id)
        self.task_progress = TaskProgress(self.action_name, total=None, start_time=time())
        self.upload_filename = _task_input.get('filename', 'problem_grade_report')
        self.upload_parent_dir = _task_input.get('upload_parent_dir', '')

    @lazy
    def course(self):
        return get_course_by_id(self.course_id)

    @lazy
    def graded_scorable_blocks_header(self):
        """
        Returns an OrderedDict that maps a scorable block's id to its
        headers in the final report.
        """
        scorable_blocks_map = OrderedDict()
        grading_context = grades_context.grading_context_for_course(self.course)
        for assignment_type_name, subsection_infos in grading_context['all_graded_subsections_by_type'].items():
            for subsection_index, subsection_info in enumerate(subsection_infos, start=1):
                for scorable_block in subsection_info['scored_descendants']:
                    header_name = (
                        "{assignment_type} {subsection_index}: "
                        "{subsection_name} - {scorable_block_name}"
                    ).format(
                        scorable_block_name=scorable_block.display_name,
                        assignment_type=assignment_type_name,
                        subsection_index=subsection_index,
                        subsection_name=subsection_info['subsection_block'].display_name,
                    )
                    scorable_blocks_map[scorable_block.location] = [header_name + " (Earned)",
                                                                    header_name + " (Possible)"]
        return scorable_blocks_map

    @lazy
    def course_structure(self):
        return get_course_in_cache(self.course_id)

    def update_status(self, message):
        """
        Updates the status on the celery task to the given message.
        Also logs the update.
        """
        TASK_LOG.info('%s, Task type: %s, %s', self.task_info_string, self.action_name, message)
        return self.task_progress.update_task_state(extra_meta={'step': message})


class _CertificateBulkContext:
    def __init__(self, context, users):
        certificate_allowlist = certs_api.get_allowlist(context.course_id)
        self.allowlisted_user_ids = [entry['user_id'] for entry in certificate_allowlist]
        self.certificates_by_user = {
            certificate.user.id: certificate
            for certificate in
            get_certificates_for_course_and_users(course_id=context.course_id, users=users)
        }


class _TeamBulkContext:  # lint-amnesty, pylint: disable=missing-class-docstring
    def __init__(self, context, users):
        self.enabled = context.teams_enabled
        if self.enabled:
            self.teams_by_user = {
                membership.user.id: membership.team.name
                for membership in
                CourseTeamMembership.objects.filter(team__course_id=context.course_id, user__in=users)
            }
        else:
            self.teams_by_user = {}


class _EnrollmentBulkContext:
    def __init__(self, context, users):
        CourseEnrollment.bulk_fetch_enrollment_states(users, context.course_id)
        self.verified_users = set(IDVerificationService.get_verified_user_ids(users))


class _CourseGradeBulkContext:  # lint-amnesty, pylint: disable=missing-class-docstring
    def __init__(self, context, users):
        self.certs = _CertificateBulkContext(context, users)
        self.teams = _TeamBulkContext(context, users)
        self.enrollments = _EnrollmentBulkContext(context, users)
        bulk_cache_cohorts(context.course_id, users)
        BulkRoleCache.prefetch(users)
        prefetch_course_and_subsection_grades(context.course_id, users)
        BulkCourseTags.prefetch(context.course_id, users)


class InMemoryReportMixin:
    """
    Mixin for a file report that will generate file in memory and then upload to report store
    """

    def _generate(self):
        """
        Internal method for generating a grade report for the given context.
        """
        self.context.update_status('InMemoryReportMixin - 1: Starting grade report')
        success_headers = self._success_headers()
        error_headers = self._error_headers()
        batched_rows = self._batched_rows()

        self.context.update_status('InMemoryReportMixin - 2: Compiling grades')
        success_rows, error_rows = self._compile(batched_rows)

        self.context.update_status('InMemoryReportMixin - 3: Uploading grades')
        self._upload(success_headers, success_rows, error_headers, error_rows)

        return self.context.update_status('InMemoryReportMixin - 4: Completed grades')

    def _upload(self, success_headers, success_rows, error_headers, error_rows):
        """
        Creates and uploads a CSV for the given headers and rows.
        """
        date = datetime.now(UTC)
        upload_csv_to_report_store(
            [success_headers] + success_rows,
            self.context.upload_filename,
            self.context.course_id,
            date,
            parent_dir=self.context.upload_parent_dir
        )

        if len(error_rows) > 0:
            upload_csv_to_report_store(
                [error_headers] + error_rows,
                self.context.upload_filename + '_err',
                self.context.course_id,
                date,
                parent_dir=self.context.upload_parent_dir
            )

    def _compile(self, batched_rows):
        """
        Compiles and returns the complete list of (success_rows, error_rows) for
        the given batched_rows.
        """
        # partition and chain successes and errors
        success_rows, error_rows = zip(*batched_rows)
        success_rows = list(chain(*success_rows))
        error_rows = list(chain(*error_rows))

        # update metrics on task status
        self.context.task_progress.succeeded = len(success_rows)
        self.context.task_progress.failed = len(error_rows)
        self.context.task_progress.attempted = self.context.task_progress.succeeded + self.context.task_progress.failed
        self.context.task_progress.total = self.context.task_progress.attempted
        return success_rows, error_rows


class TemporaryFileReportMixin:
    """
    Mixin for a file report that will write rows iteratively to a TempFile
    """

    def _generate(self):
        """
        Generate a CSV containing all students' problem grades within a given `course_id`.
        """
        self.context.update_status('TemporaryFileReportMixin - 1: Starting grade report')
        batched_rows = self._batched_rows()

        with TemporaryFile('r+') as success_file, TemporaryFile('r+') as error_file:
            self.context.update_status('TemporaryFileReportMixin - 2: Compiling grades into temp files')
            has_errors = self.iter_and_write_batched_rows(batched_rows, success_file, error_file)

            self.context.update_status('TemporaryFileReportMixin - 3: Uploading files')
            self.upload_temp_files(success_file, error_file, has_errors)

        return self.context.update_status('TemporaryFileReportMixin - 4: Completed grades')

    def iter_and_write_batched_rows(self, batched_rows, success_file, error_file):
        """
        Iterate through batched rows, writing returned chunks to disk as we go.
        This should hopefully help us avoid out of memory errors.
        """
        success_writer = csv.writer(success_file)
        error_writer = csv.writer(error_file)

        # Write headers
        success_writer.writerow(self._success_headers())
        error_writer.writerow(self._error_headers())

        succeeded, failed = 0, 0
        # Iterate through batched rows, writing to temp file
        for success_rows, error_rows in batched_rows:
            success_writer.writerows(success_rows)
            if len(error_rows) > 0:
                error_writer.writerows(error_rows)
            succeeded += len(success_rows)
            failed += len(error_rows)

        self.context.task_progress.succeeded = succeeded
        self.context.task_progress.failed = failed
        self.context.task_progress.attempted = succeeded + failed
        self.context.task_progress.total = self.context.task_progress.attempted

        return self.context.task_progress.failed > 0

    def upload_temp_files(self, success_file, error_file, has_errors):
        """
        Uploads success and error csv files to report store
        """
        date = datetime.now(UTC)

        success_file.seek(0)
        upload_csv_file_to_report_store(
            success_file,
            self.context.upload_filename,
            self.context.course_id,
            date,
            parent_dir=self.context.upload_parent_dir
        )

        if has_errors:
            error_file.seek(0)
            upload_csv_file_to_report_store(
                error_file,
                self.context.upload_filename + '_err',
                self.context.course_id,
                date,
                parent_dir=self.context.upload_parent_dir
            )


class GradeReportBase:
    """
    Base class for grade reports (ProblemGradeReport and CourseGradeReport).
    """

    def __init__(self, context):
        self.context = context

    def _get_enrolled_learner_count(self):
        """
        Returns count of number of learner enrolled in course.
        """
        return CourseEnrollment.objects.users_enrolled_in(
            course_id=self.context.course_id,
            include_inactive=True,
            verified_only=self.context.report_for_verified_only,
        ).count()

    def log_task_info(self, message):
        """
        Updates the status on the celery task to the given message.
        Also logs the update.
        """
        fmt = 'Task: {task_id}, InstructorTask ID: {entry_id}, Course: {course_id}, Input: {task_input}'
        task_info_string = fmt.format(
            task_id=self.context.task_id,
            entry_id=self.context.entry_id,
            course_id=self.context.course_id,
            task_input=self.context.task_input
        )
        TASK_LOG.info('%s, Task type: %s, %s, %s', task_info_string, self.context.action_name,
                      message, self.context.task_progress.state)

    def _batch_users(self):
        """
        Returns a generator of batches of users.
        """
        def grouper(iterable, chunk_size=100, fillvalue=None):
            args = [iter(iterable)] * chunk_size
            return zip_longest(*args, fillvalue=fillvalue)

        def get_enrolled_learners_for_course(course_id, verified_only=False):
            """
            Get all the enrolled users in a course chunk by chunk.
            This generator method fetches & loads the enrolled user objects on demand which in chunk
            size defined. This method is a workaround to avoid out-of-memory errors.
            """
            filter_kwargs = {
                'courseenrollment__course_id': course_id,
            }
            if verified_only:
                filter_kwargs['courseenrollment__mode'] = CourseMode.VERIFIED

            user_ids_list = get_user_model().objects.filter(**filter_kwargs).values_list('id', flat=True).order_by('id')
            user_chunks = grouper(user_ids_list)
            for user_ids in user_chunks:
                user_ids = [user_id for user_id in user_ids if user_id is not None]
                min_id = min(user_ids)
                max_id = max(user_ids)
                users = get_user_model().objects.filter(
                    id__gte=min_id,
                    id__lte=max_id,
                    **filter_kwargs
                ).select_related('profile')

                yield users

        return get_enrolled_learners_for_course(
            course_id=self.context.course_id,
            verified_only=self.context.report_for_verified_only
        )

    def log_additional_info_for_testing(self, message):
        """
        Investigation logs for test problem grade report.

        TODO -- Remove as a part of PROD-1287
        """
        self.context.update_status(message)

    def _clear_caches(self):
        """
        Override if a report type wants to clear caches after a batch of learners has
        been processed
        """

    def _batched_rows(self):
        """
        A generator of batches of (success_rows, error_rows) for this report.
        """
        for users in self._batch_users():
            yield self._rows_for_users(users)
            self._clear_caches()


class CourseGradeReport(GradeReportBase):
    """
    Class to encapsulate functionality related to generating user/row had header data for Corse Grade Reports.
    """
    # Batch size for chunking the list of enrollees in the course.
    USER_BATCH_SIZE = 100

    @classmethod
    def generate(cls, _xblock_instance_args, _entry_id, course_id, _task_input, action_name):
        """
        Public method to generate a grade report.
        """
        with modulestore().bulk_operations(course_id):
            context = _CourseGradeReportContext(_xblock_instance_args, _entry_id, course_id, _task_input, action_name)
            if use_on_disk_grade_reporting(course_id):  # AU-926
                return TempFileCourseGradeReport(context)._generate()  # pylint: disable=protected-access
            else:
                return InMemoryCourseGradeReport(context)._generate()  # pylint: disable=protected-access

    def _success_headers(self):
        """
        Returns a list of all applicable column headers for this grade report.
        """
        return (
            ["Student ID", "Email", "Username"] +
            self._grades_header() +
            (['Cohort Name'] if self.context.cohorts_enabled else []) +
            [f'Experiment Group ({partition.name})' for partition in self.context.course_experiments] +
            (['Team Name'] if self.context.teams_enabled else []) +
            ['Enrollment Track', 'Verification Status'] +
            ['Certificate Eligible', 'Certificate Delivered', 'Certificate Type'] +
            ['Enrollment Status']
        )

    def _error_headers(self):
        """
        Returns a list of error headers for this grade report.
        """
        return ["Student ID", "Username", "Error"]

    def _grades_header(self):
        """
        Returns the applicable grades-related headers for this report.
        """
        graded_assignments = self.context.graded_assignments
        grades_header = ["Grade"]
        for assignment_info in graded_assignments.values():
            if assignment_info['separate_subsection_avg_headers']:
                grades_header.extend(assignment_info['subsection_headers'].values())
            grades_header.append(assignment_info['average_header'])
        return grades_header

    def _rows_for_users(self, users):
        """
        Returns a list of rows for the given users for this report.
        """
        with modulestore().bulk_operations(self.context.course_id):
            bulk_context = _CourseGradeBulkContext(self.context, users)

            success_rows, error_rows = [], []
            for user, course_grade, error in CourseGradeFactory().iter(
                users,
                course=self.context.course,
                collected_block_structure=self.context.course_structure,
                course_key=self.context.course_id,
            ):
                if not course_grade:
                    # An empty gradeset means we failed to grade a student.
                    error_rows.append([user.id, user.username, str(error)])
                else:
                    success_rows.append(
                        [user.id, user.email, user.username] +
                        self._user_grades(course_grade) +
                        self._user_cohort_group_names(user) +
                        self._user_experiment_group_names(user) +
                        self._user_team_names(user, bulk_context.teams) +
                        self._user_verification_mode(user, bulk_context.enrollments) +
                        self._user_certificate_info(user, course_grade, bulk_context.certs) +
                        [_user_enrollment_status(user, self.context.course_id)]
                    )
            return success_rows, error_rows

    def _user_grades(self, course_grade):
        """
        Returns a list of grade results for the given course_grade corresponding
        to the headers for this report.
        """
        grade_results = []
        for _, assignment_info in self.context.graded_assignments.items():
            subsection_grades, subsection_grades_results = self._user_subsection_grades(
                course_grade,
                assignment_info['subsection_headers'],
            )
            grade_results.extend(subsection_grades_results)

            assignment_average = self._user_assignment_average(course_grade, subsection_grades, assignment_info)
            if assignment_average is not None:
                grade_results.append([assignment_average])

        return [course_grade.percent] + _flatten(grade_results)

    def _user_subsection_grades(self, course_grade, subsection_headers):
        """
        Returns a list of grade results for the given course_grade corresponding
        to the headers for this report.
        """
        subsection_grades = []
        grade_results = []
        for subsection_location in subsection_headers:
            subsection_grade = course_grade.subsection_grade(subsection_location)
            if subsection_grade.attempted_graded or subsection_grade.override:
                grade_result = subsection_grade.percent_graded
            else:
                grade_result = 'Not Attempted'
            grade_results.append([grade_result])
            subsection_grades.append(subsection_grade)
        return subsection_grades, grade_results

    def _user_assignment_average(self, course_grade, subsection_grades, assignment_info):
        """
        Returns grade averages for assignment types
        """
        if assignment_info['separate_subsection_avg_headers']:
            if assignment_info['grader']:
                if course_grade.attempted:
                    subsection_breakdown = [
                        {'percent': subsection_grade.percent_graded}
                        for subsection_grade in subsection_grades
                    ]
                    assignment_average, _ = assignment_info['grader'].total_with_drops(subsection_breakdown)
                else:
                    assignment_average = 0.0
                return assignment_average

    def _user_cohort_group_names(self, user):
        """
        Returns a list of names of cohort groups in which the given user
        belongs.
        """
        cohort_group_names = []
        if self.context.cohorts_enabled:
            group = get_cohort(user, self.context.course_id, assign=False, use_cached=True)
            cohort_group_names.append(group.name if group else '')
        return cohort_group_names

    def _user_experiment_group_names(self, user):
        """
        Returns a list of names of course experiments in which the given user
        belongs.
        """
        experiment_group_names = []
        for partition in self.context.course_experiments:
            group = PartitionService(self.context.course_id).get_group(user, partition, assign=False)
            experiment_group_names.append(group.name if group else '')
        return experiment_group_names

    def _user_team_names(self, user, bulk_teams):
        """
        Returns a list of names of teams in which the given user belongs.
        """
        team_names = []
        if bulk_teams.enabled:
            team_names = [bulk_teams.teams_by_user.get(user.id, '')]
        return team_names

    def _user_verification_mode(self, user, bulk_enrollments):
        """
        Returns a list of enrollment-mode and verification-status for the
        given user.
        """
        enrollment_mode = CourseEnrollment.enrollment_mode_for_user(user, self.context.course_id)[0]
        verification_status = IDVerificationService.verification_status_for_user(
            user,
            enrollment_mode,
            user_is_verified=user.id in bulk_enrollments.verified_users,
        )
        return [enrollment_mode, verification_status]

    def _user_certificate_info(self, user, course_grade, bulk_certs):
        """
        Returns the course certification information for the given user.
        """
        is_allowlisted = user.id in bulk_certs.allowlisted_user_ids
        certificate_info = certs_api.certificate_info_for_user(
            user,
            self.context.course_id,
            course_grade.letter_grade,
            is_allowlisted,
            bulk_certs.certificates_by_user.get(user.id),
        )
        return certificate_info


class InMemoryCourseGradeReport(CourseGradeReport, InMemoryReportMixin):
    """ Course Grade Report that compiles and then uploads all rows at once """


class TempFileCourseGradeReport(CourseGradeReport, TemporaryFileReportMixin):
    """ Course Grade Report that writes file iteratively to a TempFile to then be uploaded """


class ProblemGradeReport(GradeReportBase):
    """
    Class to encapsulate functionality related to generating user/row had header data for Problem Grade Reports.
    """

    @classmethod
    def generate(cls, _xblock_instance_args, _entry_id, course_id, _task_input, action_name):
        """
        Public method to generate a grade report.
        """
        with modulestore().bulk_operations(course_id):
            context = _ProblemGradeReportContext(_xblock_instance_args, _entry_id, course_id, _task_input, action_name)
            if use_on_disk_grade_reporting(course_id):  # AU-926
                return TempFileProblemGradeReport(context)._generate()  # pylint: disable=protected-access
            else:
                return InMemoryProblemGradeReport(context)._generate()  # pylint: disable=protected-access

    def _success_headers(self):
        """
        Returns headers for all gradable blocks including fixed headers
        for report.
        Returns:
            list: combined header and scorable blocks
        """
        header_row = list(self._problem_grades_header().values()) + ['Enrollment Status', 'Grade']
        return header_row + _flatten(list(self.context.graded_scorable_blocks_header.values()))

    def _error_headers(self):
        """
        Returns error headers for error report.
        Returns:
            list: error headers
        """
        return list(self._problem_grades_header().values()) + ['error_msg']

    def _problem_grades_header(self):
        """Problem Grade report header."""
        return OrderedDict([('id', 'Student ID'), ('email', 'Email'), ('username', 'Username')])

    def _rows_for_users(self, users):
        """
        Returns a list of rows for the given users for this report.
        """
        success_rows, error_rows = [], []
        for student, course_grade, error in CourseGradeFactory().iter(
            users,
            course=self.context.course,
            collected_block_structure=self.context.course_structure,
            course_key=self.context.course_id,
        ):
            if not course_grade:
                err_msg = str(error)
                # There was an error grading this student.
                if not err_msg:
                    err_msg = 'Unknown error'
                error_rows.append(
                    [student.id, student.email, student.username] +
                    [err_msg]
                )
                continue

            earned_possible_values = []
            for block_location in self.context.graded_scorable_blocks_header:
                try:
                    problem_score = course_grade.problem_scores[block_location]
                except KeyError:
                    earned_possible_values.append(['Not Available', 'Not Available'])
                else:
                    if problem_score.first_attempted:
                        earned_possible_values.append([problem_score.earned, problem_score.possible])
                    else:
                        earned_possible_values.append(['Not Attempted', problem_score.possible])

            enrollment_status = _user_enrollment_status(student, self.context.course_id)
            success_rows.append(
                [student.id, student.email, student.username] +
                [enrollment_status, course_grade.percent] +
                _flatten(earned_possible_values)
            )

        return success_rows, error_rows

    def _clear_caches(self):
        get_cache('get_enrollment').clear()
        get_cache(CourseEnrollment.MODE_CACHE_NAMESPACE).clear()


class InMemoryProblemGradeReport(ProblemGradeReport, InMemoryReportMixin):
    """ Program Grade Report that compiles and then uploads all rows at once """


class TempFileProblemGradeReport(ProblemGradeReport, TemporaryFileReportMixin):
    """ Program Grade Report that writes file iteratively to a TempFile to then be uploaded """


class ProblemResponses:
    """
    Class to encapsulate functionality related to generating Problem Responses Reports.
    """

    @staticmethod
    def _build_block_base_path(block):
        """
        Return the display names of the blocks that lie above the supplied block in hierarchy.

        Arguments:
            block: a single block

        Returns:
            List[str]: a list of display names of blocks starting from the root block (Course)
        """
        path = []
        while block.parent:
            block = block.get_parent()
            path.append(block.display_name)
        return list(reversed(path))

    @classmethod
    def _build_problem_list(cls, course_blocks, root, path=None):
        """
        Generate a tuple of display names, block location paths and block keys
        for all problem blocks under the ``root`` block.
        Arguments:
            course_blocks (BlockStructureBlockData): Block structure for a course.
            root (UsageKey): This block and its children will be used to generate
                the problem list.
            path (List[str]): The list of display names for the parent of root block
        Yields:
            Tuple[str, List[str], UsageKey]: tuple of a block's display name, path, and
                usage key.
        """
        name = course_blocks.get_xblock_field(root, 'display_name')
        if not name or name == 'problem':
            # Fallback: CourseBlocks may not have display_name cached for all blocks,
            # especially for dynamically generated content or library_content blocks.
            # Loading the full block is necessary to get meaningful names for CSV reports
            TASK_LOG.debug(
                "ProblemResponses: display_name missing in course_blocks for %s, falling back to modulestore. "
                "Occasional occurrences of this message are expected (e.g., library_content children); "
                "frequent occurrences may indicate a cache or transformer issue.",
                root,
            )
            try:
                block = modulestore().get_item(root)
                name = getattr(block, 'display_name', None) or root.block_type
            except ItemNotFoundError:
                name = root.block_type
        if path is None:
            path = [name]

        yield name, path, root

        for block in course_blocks.get_children(root):
            name = course_blocks.get_xblock_field(block, 'display_name') or block.block_type
            yield from cls._build_problem_list(course_blocks, block, path + [name])

    @classmethod
    def _build_student_data(
        cls, user_id, course_key, usage_key_str_list, filter_types=None,
    ):
        """
        Generate a list of problem responses for all problem under the
        ``problem_location`` root.
        Arguments:
            user_id (int): The user id for the user generating the report
            course_key (CourseKey): The ``CourseKey`` for the course whose report
                is being generated
            usage_key_str_list (List[str]): The generated report will include these
                blocks and their child blocks.
            filter_types (List[str]): The report generator will only include data for
                block types in this list.
        Returns:
              Tuple[List[Dict], List[str]]: Returns a list of dictionaries
                containing the student data which will be included in the
                final csv, and the features/keys to include in that CSV.
        """
        usage_keys = [
            UsageKey.from_string(usage_key_str).map_into_course(course_key)
            for usage_key_str in usage_key_str_list
        ]

        user = get_user_model().objects.get(pk=user_id)

        # For reporting, we want the full set of descendant blocks including all children
        # of library_content blocks (randomized content). The default transformer list includes
        # ContentLibraryTransformer which filters children based on per-user selections.
        # For staff-generated reports, we bypass those library transformers to see all problems.
        report_transformers = BlockStructureTransformers([
            transformer for transformer in get_course_block_access_transformers(user)
            if not isinstance(
                transformer,
                (
                    library_content.ContentLibraryTransformer,
                    library_content.ContentLibraryOrderTransformer,
                )
            )
        ])

        student_data = []
        max_count = settings.FEATURES.get('MAX_PROBLEM_RESPONSES_COUNT')

        store = modulestore()
        user_state_client = DjangoXBlockUserStateClient()

        # Each user's generated report data may contain different fields, so we use an OrderedDict to prevent
        # duplication of keys while preserving the order the XBlock provides the keys in.
        student_data_keys = OrderedDict()

        with store.bulk_operations(course_key):
            for usage_key in usage_keys:  # lint-amnesty, pylint: disable=too-many-nested-blocks
                if max_count is not None and max_count <= 0:
                    break
                course_blocks = get_course_blocks(user, usage_key, transformers=report_transformers)
                base_path = cls._build_block_base_path(store.get_item(usage_key))
                for title, path, block_key in cls._build_problem_list(course_blocks, usage_key):
                    # Chapter, sequential, library_content, and itembank blocks are filtered out
                    # since they include state which isn't useful for this report.
                    # library_content (V1) and itembank (V2) state contains internal selection
                    # metadata (which problems were randomly assigned to each user), not actual
                    # student responses.
                    if block_key.block_type in ('sequential', 'chapter', 'library_content', 'itembank'):
                        continue

                    if filter_types is not None and block_key.block_type not in filter_types:
                        continue

                    block = store.get_item(block_key)
                    generated_report_data = defaultdict(list)

                    # Blocks can implement the generate_report_data method to provide their own
                    # human-readable formatting for user state.
                    if hasattr(block, 'generate_report_data'):
                        try:
                            user_state_iterator = user_state_client.iter_all_for_block(block_key)
                            for username, state in block.generate_report_data(user_state_iterator, max_count):
                                generated_report_data[username].append(state)
                        except NotImplementedError:
                            pass

                    responses = []

                    for response in list_problem_responses(course_key, block_key, max_count):
                        response['title'] = title
                        # A human-readable location for the current block
                        response['location'] = ' > '.join(base_path + path)
                        # A machine-friendly location for the current block
                        response['block_key'] = str(block_key)
                        # A block that has a single state per user can contain multiple responses
                        # within the same state.
                        user_states = generated_report_data.get(response['username'])
                        if user_states:
                            # For each response in the block, copy over the basic data like the
                            # title, location, block_key and state, and add in the responses
                            for user_state in user_states:
                                user_response = response.copy()
                                user_response.update(user_state)

                                # Respect the column order as returned by the xblock, if any.
                                if isinstance(user_state, OrderedDict):
                                    user_state_keys = user_state.keys()
                                else:
                                    user_state_keys = sorted(user_state.keys())
                                for key in user_state_keys:
                                    student_data_keys[key] = 1

                                responses.append(user_response)
                        else:
                            responses.append(response)

                    student_data += responses

                    if max_count is not None:
                        max_count -= len(responses)
                        if max_count <= 0:
                            break

        # Keep the keys in a useful order, starting with username, title and location,
        # then the columns returned by the xblock report generator in sorted order and
        # finally end with the more machine friendly block_key and state.
        student_data_keys_list = (
            ['username', 'title', 'location'] +
            list(student_data_keys.keys()) +
            ['block_key', 'state']
        )

        return student_data, student_data_keys_list

    @classmethod
    def generate(cls, _xblock_instance_args, _entry_id, course_id, task_input, action_name):
        """
        For a given `course_id`, generate a CSV file containing
        all student answers to a given problem, and store using a `ReportStore`.
        """
        start_time = time()
        start_date = datetime.now(UTC)
        num_reports = 1
        task_progress = TaskProgress(action_name, num_reports, start_time)
        current_step = {'step': 'Calculating students answers to problem'}
        task_progress.update_task_state(extra_meta=current_step)
        problem_locations = task_input.get('problem_locations').split(',')
        problem_types_filter = task_input.get('problem_types_filter')

        filter_types = None
        if problem_types_filter:
            filter_types = problem_types_filter.split(',')

        # Compute result table and format it
        student_data, student_data_keys = cls._build_student_data(
            user_id=task_input.get('user_id'),
            course_key=course_id,
            usage_key_str_list=problem_locations,
            filter_types=filter_types,
        )

        for data in student_data:
            for key in student_data_keys:
                data.setdefault(key, '')

        header, rows = format_dictlist(student_data, student_data_keys)

        task_progress.attempted = task_progress.succeeded = len(rows)
        task_progress.skipped = task_progress.total - task_progress.attempted

        rows.insert(0, header)

        current_step = {'step': 'Uploading CSV'}
        task_progress.update_task_state(extra_meta=current_step)

        # Perform the upload
        csv_name = cls._generate_upload_file_name(problem_locations, filter_types)
        report_name = upload_csv_to_report_store(rows, csv_name, course_id, start_date)
        current_step = {
            'step': 'CSV uploaded',
            'report_name': report_name,
        }

        return task_progress.update_task_state(extra_meta=current_step)

    @staticmethod
    def _generate_upload_file_name(problem_locations, filters):
        """Generate a concise file name based on the report generation parameters."""
        multiple_problems = len(problem_locations) > 1
        csv_name = 'student_state'
        if multiple_problems:
            csv_name += '_from_multiple_blocks'
        else:
            problem_location = re.sub(r'[:/]', '_', problem_locations[0])
            csv_name += '_from_' + problem_location
        if filters:
            csv_name += '_for_' + ','.join(filters)
        return csv_name
