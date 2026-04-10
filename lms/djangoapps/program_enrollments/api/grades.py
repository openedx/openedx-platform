"""
Python API functions related to reading program-course grades.

Outside of this subpackage, import these functions
from `lms.djangoapps.program_enrollments.api`.
"""


import logging

from common.djangoapps.util.query import read_replica_or_default
from lms.djangoapps.grades.api import CourseGradeFactory, clear_prefetched_course_grades, prefetch_course_grades

from .reading import fetch_program_course_enrollments

logger = logging.getLogger(__name__)


def iter_program_course_grades(program_uuid, course_key, paginate_queryset_fn=None):
    """
    Load grades (or grading errors) for a given program-course.

    Arguments:
        program_uuid (str)
        course_key (CourseKey)
        paginate_queryset_fn (QuerySet -> QuerySet):
            Optional function to paginate the results,
            generally passed in from `self.request.paginate_queryset`
            on a paginated DRF `APIView`.
            If `None`, all results will be loaded and returned.

    Returns: generator[BaseProgramCourseGrade]
    """
    enrollments_qs = fetch_program_course_enrollments(
        program_uuid=program_uuid,
        course_key=course_key,
        realized_only=True,
    ).select_related(
        'program_enrollment',
        'program_enrollment__user',
    ).using(read_replica_or_default())
    enrollments = (
        paginate_queryset_fn(enrollments_qs) if paginate_queryset_fn
        else enrollments_qs
    )
    if not enrollments:
        return []
    return _generate_grades(course_key, list(enrollments))


def _generate_grades(course_key, enrollments):
    """
    Load enrolled user grades for a program-course,
    using bulk fetching for efficiency.

    Arguments:
        course_key (CourseKey)
        enrollments (list[ProgramCourseEnrollment])

    Yields: BaseProgramCourseGrade
    """
    users = [enrollment.program_enrollment.user for enrollment in enrollments]
    prefetch_course_grades(course_key, users)
    try:
        grades_iter = CourseGradeFactory().iter(users, course_key=course_key)
        for enrollment, grade_tuple in zip(enrollments, grades_iter):  # noqa: B905
            user, course_grade, exception = grade_tuple
            if course_grade:
                yield ProgramCourseGradeOk(enrollment, course_grade)
            else:
                error_template = 'Failed to load course grade for user ID {} in {}: {}'
                error_string = error_template.format(
                    user.id,
                    course_key,
                    str(exception) if exception else 'Unknown error'
                )
                logger.error(error_string)
                yield ProgramCourseGradeError(enrollment, exception)
    finally:
        clear_prefetched_course_grades(course_key)


class BaseProgramCourseGrade:
    """
    Base for either a courserun grade or grade-loading failure.

    Can be passed to ProgramCourseGradeResultSerializer.
    """
    is_error = None  # Override in subclass

    def __init__(self, program_course_enrollment):
        """
        Given a ProgramCourseEnrollment,
        create a BaseProgramCourseGrade instance.
        """
        self.program_course_enrollment = program_course_enrollment


class ProgramCourseGradeOk(BaseProgramCourseGrade):
    """
    Represents a courserun grade for a user enrolled through a program.
    """
    is_error = False

    def __init__(self, program_course_enrollment, course_grade):
        """
        Given a ProgramCourseEnrollment and course grade object,
        create a ProgramCourseGradeOk.
        """
        super().__init__(
            program_course_enrollment
        )
        self.passed = course_grade.passed
        self.percent = course_grade.percent
        self.letter_grade = course_grade.letter_grade


class ProgramCourseGradeError(BaseProgramCourseGrade):
    """
    Represents a failure to load a courserun grade for a user enrolled through
    a program.
    """
    is_error = True

    def __init__(self, program_course_enrollment, exception=None):
        """
        Given a ProgramCourseEnrollment and an Exception,
        create a ProgramCourseGradeError.
        """
        super().__init__(
            program_course_enrollment
        )
        self.error = str(exception) if exception else "Unknown error"
