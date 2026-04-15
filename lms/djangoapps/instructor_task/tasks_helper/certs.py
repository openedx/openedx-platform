"""
Instructor tasks related to certificates.
"""


import logging
from time import time

from django.contrib.auth import get_user_model
from django.db.models import Q

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.certificates.api import (
    generate_certificate_task,
    get_enrolled_allowlisted_not_passing_users,
    get_enrolled_allowlisted_users,
)
from lms.djangoapps.certificates.data import CertificateStatuses

from .runner import TaskProgress

User = get_user_model()

log = logging.getLogger(__name__)


def generate_students_certificates(
        _xblock_instance_args, _entry_id, course_id, task_input, action_name):
    """
    For a given `course_id`, generate certificates for only students present in 'students' key in task_input
    json column, otherwise generate certificates for all enrolled students.
    """
    start_time = time()
    students_to_generate_certs_for = CourseEnrollment.objects.users_enrolled_in(course_id)

    student_set = task_input.get('student_set')
    if student_set == 'all_allowlisted':
        # Generate Certificates for all allowlisted students.
        students_to_generate_certs_for = get_enrolled_allowlisted_users(course_id)

    elif student_set == 'allowlisted_not_generated':
        # Allowlisted students who did not yet receive certificates
        students_to_generate_certs_for = get_enrolled_allowlisted_not_passing_users(course_id)

    elif student_set == "specific_student":
        specific_student_id = task_input.get('specific_student_id')
        students_to_generate_certs_for = students_to_generate_certs_for.filter(id=specific_student_id)

    task_progress = TaskProgress(action_name, students_to_generate_certs_for.count(), start_time)

    current_step = {'step': 'Calculating students already have certificates'}
    task_progress.update_task_state(extra_meta=current_step)

    statuses_to_regenerate = task_input.get('statuses_to_regenerate', [])
    if student_set is not None and not statuses_to_regenerate:
        # We want to skip 'filtering students' only when students are given and statuses to regenerate are not
        students_require_certs = students_to_generate_certs_for
    else:
        students_require_certs = students_require_certificate(
            course_id, students_to_generate_certs_for, statuses_to_regenerate
        )

    log.info(f'About to attempt certificate generation for {len(students_require_certs)} users in course {course_id}. '
             f'The student_set is {student_set} and statuses_to_regenerate is {statuses_to_regenerate}')

    task_progress.skipped = task_progress.total - len(students_require_certs)

    current_step = {'step': 'Generating Certificates'}
    task_progress.update_task_state(extra_meta=current_step)

    # Generate certificate for each student
    for student in students_require_certs:
        task_progress.attempted += 1
        log.info(f'Attempt will be made to generate a course certificate for {student.id} : {course_id}.')
        generate_certificate_task(student, course_id)
    return task_progress.update_task_state(extra_meta=current_step)


def students_require_certificate(course_id, enrolled_students, statuses_to_regenerate=None):
    """
    Returns list of students where certificates needs to be generated.
    if 'statuses_to_regenerate' is given then return students that have Generated Certificates
    and the generated certificate status lies in 'statuses_to_regenerate'

    if 'statuses_to_regenerate' is not given then return all the enrolled student skipping the ones
    whose certificates have already been generated.

    :param course_id:
    :param enrolled_students:
    :param statuses_to_regenerate:
    """
    if statuses_to_regenerate:
        # Return Students that have Generated Certificates and the generated certificate status
        # lies in 'statuses_to_regenerate'
        students_require_certificates = enrolled_students.filter(
            generatedcertificate__course_id=course_id,
            generatedcertificate__status__in=statuses_to_regenerate
        )
        # Fetch results otherwise subsequent operations on table cause wrong data fetch
        return list(students_require_certificates)
    else:
        # compute those students whose certificates are already generated
        students_already_have_certs = User.objects.filter(
            ~Q(generatedcertificate__status=CertificateStatuses.unavailable),
            generatedcertificate__course_id=course_id)

        # Return all the enrolled student skipping the ones whose certificates have already been generated
        return list(set(enrolled_students) - set(students_already_have_certs))
