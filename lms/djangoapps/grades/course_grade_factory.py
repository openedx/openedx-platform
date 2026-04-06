"""
Course Grade Factory Class
"""
from collections import namedtuple
from logging import getLogger

from openedx.core.djangoapps.signals.signals import (
    COURSE_GRADE_CHANGED,
    COURSE_GRADE_NOW_FAILED,
    COURSE_GRADE_NOW_PASSED,
)

from .course_data import CourseData
from .course_grade import CourseGrade, ZeroCourseGrade
from .models import PersistentCourseGrade
from .models_api import prefetch_grade_overrides_and_visible_blocks

log = getLogger(__name__)


class CourseGradeFactory:
    """
    Factory class to create Course Grade objects.
    """
    GradeResult = namedtuple('GradeResult', ['student', 'course_grade', 'error'])

    def read(
            self,
            user,
            course=None,
            collected_block_structure=None,
            course_structure=None,
            course_key=None,
            create_if_needed=True,
    ):
        """
        Returns the CourseGrade for the given user in the course.
        Reads the value from storage or creates an initial value of zero.

        At least one of course, collected_block_structure, course_structure,
        or course_key should be provided.
        """
        course_data = CourseData(user, course, collected_block_structure, course_structure, course_key)
        try:
            return self._read(user, course_data)
        except PersistentCourseGrade.DoesNotExist:
            return self._create_zero(user, course_data)

    def update(
            self,
            user,
            course=None,
            collected_block_structure=None,
            course_structure=None,
            course_key=None,
            force_update_subsections=False,
    ):
        """
        Computes, updates, and returns the CourseGrade for the given
        user in the course.

        At least one of course, collected_block_structure, course_structure,
        or course_key should be provided.
        """
        course_data = CourseData(user, course, collected_block_structure, course_structure, course_key)
        return self._update(
            user,
            course_data,
            force_update_subsections=force_update_subsections
        )

    def iter(
            self,
            users,
            course=None,
            collected_block_structure=None,
            course_key=None,
            force_update=False,
    ):
        """
        Given a course and an iterable of students (User), yield a GradeResult
        for every student enrolled in the course.  GradeResult is a named tuple of:

            (student, course_grade, err_msg)

        If an error occurred, course_grade will be None and err_msg will be an
        exception message. If there was no error, err_msg is an empty string.
        """
        # Pre-fetch the collected course_structure (in _iter_grade_result) so:
        # 1. Correctness: the same version of the course is used to
        #    compute the grade for all students.
        # 2. Optimization: the collected course_structure is not
        #    retrieved from the data store multiple times.
        course_data = CourseData(
            user=None, course=course, collected_block_structure=collected_block_structure, course_key=course_key,
        )
        for user in users:
            yield self._iter_grade_result(user, course_data, force_update)

    def _iter_grade_result(self, user, course_data, force_update):  # lint-amnesty, pylint: disable=missing-function-docstring
        try:
            kwargs = {
                'user': user,
                'course': course_data.course,
                'collected_block_structure': course_data.collected_structure,
                'course_key': course_data.course_key,
            }
            if force_update:
                kwargs['force_update_subsections'] = True

            method = CourseGradeFactory().update if force_update else CourseGradeFactory().read
            course_grade = method(**kwargs)
            return self.GradeResult(user, course_grade, None)
        except Exception as exc:  # pylint: disable=broad-except
            # Keep marching on even if this student couldn't be graded for
            # some reason, but log it for future reference.
            log.exception(
                'Cannot grade student %s in course %s because of exception: %s',
                user.id,
                course_data.course_key,
                str(exc)
            )
            return self.GradeResult(user, None, exc)

    @staticmethod
    def _create_zero(user, course_data):
        """
        Returns a ZeroCourseGrade object for the given user and course.
        """
        log.debug('Grades: CreateZero, %s, User: %s', str(course_data), user.id)
        return ZeroCourseGrade(user, course_data)

    @staticmethod
    def _read(user, course_data):
        """
        Returns a CourseGrade object based on stored grade information
        for the given user and course.
        """
        persistent_grade = PersistentCourseGrade.read(user.id, course_data.course_key)
        log.debug('Grades: Read, %s, User: %s, %s', str(course_data), user.id, persistent_grade)

        return CourseGrade(
            user,
            course_data,
            persistent_grade.percent_grade,
            persistent_grade.letter_grade,
            persistent_grade.letter_grade != '',
            last_updated=persistent_grade.modified
        )

    @staticmethod
    def _update(user, course_data, force_update_subsections=False):
        """
        Computes, saves, and returns a CourseGrade object for the
        given user and course.
        Sends a COURSE_GRADE_CHANGED signal to listeners and
        COURSE_GRADE_NOW_PASSED if learner has passed course or
        COURSE_GRADE_NOW_FAILED if learner is now failing course
        """
        if force_update_subsections:
            prefetch_grade_overrides_and_visible_blocks(user, course_data.course_key)

        course_grade = CourseGrade(
            user,
            course_data,
            force_update_subsections=force_update_subsections
        )
        course_grade = course_grade.update()

        should_persist = course_grade.attempted
        if should_persist:
            course_grade._subsection_grade_factory.bulk_create_unsaved()  # lint-amnesty, pylint: disable=protected-access
            PersistentCourseGrade.update_or_create(
                user_id=user.id,
                course_id=course_data.course_key,
                course_version=course_data.version,
                course_edited_timestamp=course_data.edited_on,
                grading_policy_hash=course_data.grading_policy_hash,
                percent_grade=course_grade.percent,
                letter_grade=course_grade.letter_grade or "",
                passed=course_grade.passed,
            )

        COURSE_GRADE_CHANGED.send_robust(
            sender=None,
            user=user,
            course_grade=course_grade,
            course_key=course_data.course_key,
            deadline=course_data.course.end,
        )
        if course_grade.passed:
            COURSE_GRADE_NOW_PASSED.send(
                sender=CourseGradeFactory,
                user=user,
                course_id=course_data.course_key,
            )
        else:
            COURSE_GRADE_NOW_FAILED.send(
                sender=CourseGradeFactory,
                user=user,
                course_id=course_data.course_key,
                grade=course_grade,
            )

        log.info(
            'Grades: Update, %s, User: %s, %s, persisted: %s',
            course_data.full_string(), user.id, course_grade, should_persist,
        )

        return course_grade
