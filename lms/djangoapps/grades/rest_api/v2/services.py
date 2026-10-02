"""Grade reads shared by the Grades API v2 views."""

import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import NotFound

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import BulkRoleCache
from common.djangoapps.track.event_transaction_utils import (
    create_new_event_transaction_id,
    get_event_transaction_id,
    get_event_transaction_type,
    set_event_transaction_type,
)
from common.djangoapps.util.date_utils import to_timestamp
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.courseware.courses import get_course
from lms.djangoapps.courseware.exceptions import CourseRunNotFound
from lms.djangoapps.courseware.models import BaseStudentModuleHistory, StudentModule
from lms.djangoapps.grades.api import (
    CourseGradeFactory,
    clear_prefetched_course_and_subsection_grades,
    clear_prefetched_course_grades,
    gradebook_bulk_management_enabled,
    prefetch_course_and_subsection_grades,
    prefetch_course_grades,
)
from lms.djangoapps.grades.api import constants as grades_constants
from lms.djangoapps.grades.api import context as grades_context
from lms.djangoapps.grades.api import events as grades_events
from lms.djangoapps.grades.config.waffle import ENFORCE_FREEZE_GRADE_AFTER_COURSE_END
from lms.djangoapps.grades.course_data import CourseData
from lms.djangoapps.grades.models import PersistentSubsectionGrade, PersistentSubsectionGradeOverride
from lms.djangoapps.grades.rest_api.v1.views import SubmissionHistoryView
from lms.djangoapps.grades.rest_api.v2.errors import SubsectionUnavailable
from lms.djangoapps.grades.subsection_grade import CreateSubsectionGrade
from lms.djangoapps.grades.subsection_grade_factory import SubsectionGradeFactory
from lms.djangoapps.grades.tasks import recalculate_subsection_grade_v3
from lms.djangoapps.program_enrollments.api import fetch_program_course_enrollments_by_students
from openedx.core.djangoapps.content.block_structure.exceptions import UsageKeyNotInBlockStructure
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order
from xmodule.util.misc import get_default_short_labeler  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


#: The grade values an override may replace.
OVERRIDE_FIELDS = (
    'earned_all_override',
    'possible_all_override',
    'earned_graded_override',
    'possible_graded_override',
)


def course_content(course_key, depth=0):
    """
    Return the course block of ``course_key``, loaded ``depth`` levels deep.

    Raises NotFound when the course has no content, which happens when its
    overview outlives it.
    """
    try:
        return get_course_by_id(course_key, depth=depth)
    except Http404 as exc:
        log.info('Course %s has an overview but no content.', course_key)
        raise NotFound() from exc


def course_grade_rows(enrollments, with_section_breakdown=False):
    """
    Return one course-grade row per enrollment, in the order the enrollments were given.

    Enrollments whose grade cannot be computed are logged and left out, so a
    page may hold fewer rows than it has enrollments.

    Arguments:
        enrollments: CourseEnrollment objects, which may span several courses.
        with_section_breakdown (bool): also include the course grader's
            breakdown of each grade.
    """
    enrollments = list(enrollments)
    rows_by_enrollment = {}
    for course_key, course_enrollments in _group_by_course(enrollments).items():
        users = [enrollment.user for enrollment in course_enrollments]
        grades = {}
        with _prefetched_course_grades(course_key, users):
            for user, course_grade, error in CourseGradeFactory().iter(users, course_key=course_key):
                if error:
                    log.warning(
                        'Leaving user %s out of the grades of course %s: grade unavailable.', user.id, course_key,
                    )
                    continue
                grades[user.id] = course_grade
        for enrollment in course_enrollments:
            course_grade = grades.get(enrollment.user_id)
            if course_grade is not None:
                rows_by_enrollment[enrollment.id] = _course_grade_row(enrollment, course_grade, with_section_breakdown)
    return [rows_by_enrollment[enrollment.id] for enrollment in enrollments if enrollment.id in rows_by_enrollment]


def course_grade_row(enrollment, with_section_breakdown=False):
    """
    Return the course-grade row of one enrollment.

    Unlike ``course_grade_rows``, a grade that cannot be computed raises, since
    the caller asked for this grade and nothing else.
    """
    course_grade = CourseGradeFactory().read(enrollment.user, course_key=enrollment.course_id)
    return _course_grade_row(enrollment, course_grade, with_section_breakdown)


def _course_grade_row(enrollment, course_grade, with_section_breakdown):
    """Return the row for one enrollment and its computed grade."""
    row = {
        'username': enrollment.user.username,
        'course_key': str(enrollment.course_id),
        'email': visible_email(enrollment),
        'passed': course_grade.passed,
        'percent': course_grade.percent,
        'letter_grade': course_grade.letter_grade,
    }
    if with_section_breakdown:
        row['section_breakdown'] = course_grade.summary['section_breakdown']
    return row


def gradebook_entry_rows(enrollments, course_key):
    """
    Return one gradebook row per enrollment in ``course_key``, in the order given.

    Enrollments whose grade cannot be computed are logged and left out, so a
    page may hold fewer rows than it has enrollments.
    """
    enrollments = list(enrollments)
    if not enrollments:
        return []
    structure = _GradebookStructure(course_key)
    users = [enrollment.user for enrollment in enrollments]
    external_keys = _external_user_keys(users, course_key)
    grades = {}
    with _prefetched_gradebook(course_key, users):
        for user, course_grade, error in CourseGradeFactory().iter(
            users, course_key=course_key, collected_block_structure=structure.collected,
        ):
            if error:
                log.warning(
                    'Leaving user %s out of the gradebook of course %s: grade unavailable.', user.id, course_key,
                )
                continue
            grades[user.id] = course_grade
    return [
        _gradebook_entry(enrollment, structure, grades[enrollment.user_id], external_keys.get(enrollment.user_id))
        for enrollment in enrollments if enrollment.user_id in grades
    ]


def gradebook_entry_row(enrollment, course_key):
    """
    Return the gradebook row of one enrollment in ``course_key``.

    A grade that cannot be computed raises, since the caller asked for this row
    and nothing else.
    """
    structure = _GradebookStructure(course_key)
    course_grade = CourseGradeFactory().read(
        enrollment.user, structure.course, collected_block_structure=structure.collected,
    )
    external_key = _external_user_keys([enrollment.user], course_key).get(enrollment.user_id)
    return _gradebook_entry(enrollment, structure, course_grade, external_key)


class _GradebookStructure:
    """
    The parts of a course's content every gradebook row is built from.

    The structure is read once for the course, not once per learner: reading a
    learner's own view of the course is far more expensive, and the gradebook
    shows the same subsections for everyone.
    """

    def __init__(self, course_key):
        self.course = course_content(course_key, depth=None)
        self.collected = CourseData(user=None, course=self.course).collected_structure
        self.graded_subsections = list(grades_context.graded_subsections_for_course(self.collected))


def _gradebook_entry(enrollment, structure, course_grade, external_user_key):
    """Return the gradebook row for one enrollment and its computed grade."""
    user = enrollment.user
    entry = {
        'username': user.username,
        'email': visible_email(enrollment),
        'percent': course_grade.percent,
        'section_breakdown': _gradebook_sections(structure, course_grade),
    }
    if enrollment.mode == CourseMode.MASTERS:
        entry['full_name'] = user.profile.name
    if external_user_key:
        entry['external_user_key'] = external_user_key
    return entry


def _gradebook_sections(structure, course_grade):
    """Return the learner's score on each graded subsection of the course."""
    # The labeler numbers the subsections of each type as it is called, so
    # every row needs a fresh one.
    labeler = get_default_short_labeler(structure.course)
    sections = []
    for subsection in structure.graded_subsections:
        subsection_grade = course_grade.subsection_grade(subsection.location)
        # Reading the scores of a subsection the learner has not attempted
        # would mean reading the learner's own view of the course, so an
        # unattempted subsection reports zero scores instead.
        attempted = bool(subsection_grade.attempted_graded or subsection_grade.override)
        sections.append({
            'attempted': attempted,
            'category': subsection_grade.format,
            'label': labeler(subsection_grade.format),
            'usage_key': str(subsection_grade.location),
            'percent': subsection_grade.percent_graded,
            'score_earned': subsection_grade.graded_total.earned if attempted else 0,
            'score_possible': subsection_grade.graded_total.possible if attempted else 0,
            'subsection_name': subsection_grade.display_name,
        })
    return sections


def _external_user_keys(users, course_key):
    """
    Return ``{user id: program key}`` for the learners enrolled in ``course_key`` through a program.

    Where a learner has several program enrollments in the course, the one
    listed first by status and then most recently modified wins.
    """
    program_course_enrollments = fetch_program_course_enrollments_by_students(
        users=users, course_keys=[course_key],
    ).select_related('program_enrollment').order_by('status', '-modified')
    keys = {}
    for program_course_enrollment in program_course_enrollments:
        program_enrollment = program_course_enrollment.program_enrollment
        keys.setdefault(program_enrollment.user_id, program_enrollment.external_user_key)
    return keys


def visible_email(enrollment):
    """Return the learner's email address, which only learners on the master's track expose."""
    return enrollment.user.email if enrollment.mode == CourseMode.MASTERS else ''


def _group_by_course(enrollments):
    """Return ``{course key: [enrollment, ...]}``, keeping the given order within each course."""
    grouped = defaultdict(list)
    for enrollment in enrollments:
        grouped[enrollment.course_id].append(enrollment)
    return grouped


@contextmanager
def _prefetched_course_grades(course_key, users):
    """Prefetch the course grades of ``users`` in ``course_key`` for the duration of the block."""
    prefetch_course_grades(course_key, users)
    try:
        yield
    finally:
        clear_prefetched_course_grades(course_key)


@contextmanager
def _prefetched_gradebook(course_key, users):
    """Prefetch the grades, enrollment states, cohorts and roles of ``users`` for the duration of the block."""
    prefetch_course_and_subsection_grades(course_key, users)
    CourseEnrollment.bulk_fetch_enrollment_states(users, course_key)
    cohorts.bulk_cache_cohorts(course_key, users)
    BulkRoleCache.prefetch(users)
    try:
        yield
    finally:
        clear_prefetched_course_and_subsection_grades(course_key)


def record_subsection_grade_overrides(requesting_user, course, items):
    """
    Record the subsection grade overrides in ``items``, then recompute the grades they change.

    The overrides are stored in one transaction, so either every one of them is
    recorded or none is. The grades they change are recomputed once all of them
    are stored. Returns one row per override, in the order given.

    Arguments:
        requesting_user: the user recording the overrides.
        course: the course block, loaded to full depth.
        items: validated override items, whose ``username`` holds the learner's
            enrollment and whose ``usage_key`` holds the parsed subsection key.
    """
    recorded = []
    with transaction.atomic():
        for item in items:
            learner = item['username'].user
            grade = _stored_subsection_grade(learner, course, item['usage_key'])
            override = PersistentSubsectionGradeOverride.update_or_create_override(
                requesting_user=requesting_user,
                subsection_grade_model=grade,
                feature=grades_constants.GradeOverrideFeatureEnum.gradebook,
                system=grades_constants.GradeOverrideFeatureEnum.gradebook,
                **{name: item[name] for name in (*OVERRIDE_FIELDS, 'comment') if name in item},
            )
            recorded.append((learner, item['usage_key'], grade, override))
    for learner, _, grade, override in recorded:
        _recalculate_overridden_grade(grade, override)
        log.info(
            'Grades: Bulk_Update, UpdatedByUser: %s, User: %s, Usage: %s, Grade: %s, GradeOverride: %s, Success: %s',
            requesting_user.id, learner.id, grade.usage_key, grade, override, True,
        )
    return [
        {
            'username': learner.username,
            'usage_key': str(usage_key),
            **{name: getattr(override, name) for name in OVERRIDE_FIELDS},
            'comment': override.override_reason,
        }
        for learner, usage_key, _, override in recorded
    ]


def _stored_subsection_grade(learner, course, usage_key):
    """Return the stored grade of ``learner`` on the subsection, storing a computed one when there is none."""
    try:
        return PersistentSubsectionGrade.objects.get(
            user_id=learner.id, course_id=usage_key.course_key, usage_key=usage_key,
        )
    except PersistentSubsectionGrade.DoesNotExist:
        subsection = course.get_child(usage_key)
        structure = CourseData(learner, course=course).structure
        return CreateSubsectionGrade(subsection, structure, {}, {}).update_or_create_model(
            learner, force_update_subsections=True,
        )


def _recalculate_overridden_grade(grade, override):
    """Recompute the subsection and course grades an override changed, and announce the change."""
    set_event_transaction_type(grades_events.SUBSECTION_GRADE_CALCULATED)
    create_new_event_transaction_id()
    recalculate_subsection_grade_v3.apply(
        kwargs={
            'user_id': grade.user_id,
            'anonymous_user_id': None,
            'course_id': str(grade.course_id),
            'usage_id': str(grade.usage_key),
            'only_if_higher': False,
            'expected_modified_time': to_timestamp(override.modified),
            'score_deleted': False,
            'event_transaction_id': str(get_event_transaction_id()),
            'event_transaction_type': str(get_event_transaction_type()),
            'score_db_table': grades_constants.ScoreDatabaseTableEnum.overrides,
            'force_update_subsections': True,
        },
    )
    grades_events.subsection_grade_calculated(grade)


def enrolled_learner(username, course_key):
    """
    Return the learner named ``username`` if they hold an enrollment in ``course_key``, active or not.

    Raises NotFound otherwise, so an address naming a learner outside the course
    does not reveal whether the account exists.
    """
    enrollment = CourseEnrollment.objects.select_related('user').filter(
        user__username=username, course_id=course_key,
    ).first()
    if enrollment is None:
        raise NotFound()
    return enrollment.user


def read_subsection_grade(learner, usage_key):
    """
    Return ``learner``'s grade on the subsection ``usage_key``, and the override recorded on it.

    A stored grade is returned as stored. For a learner with no stored grade,
    the grade is computed from their view of the course without being stored.
    Raises SubsectionUnavailable when that view does not include the
    subsection, and NotFound when the course has no such block.
    """
    try:
        stored = PersistentSubsectionGrade.read_grade(learner.id, usage_key)
    except PersistentSubsectionGrade.DoesNotExist:
        scores, override = _computed_subsection_scores(learner, usage_key), None
    else:
        scores = {name: getattr(stored, name) for name in SUBSECTION_SCORE_FIELDS}
        try:
            override = stored.override
        except ObjectDoesNotExist:
            override = None
    return {
        'username': learner.username,
        'usage_key': str(usage_key),
        'course_key': str(usage_key.course_key),
        'original_grade': scores,
        'override': override,
    }


#: The points a subsection grade records.
SUBSECTION_SCORE_FIELDS = ('earned_all', 'possible_all', 'earned_graded', 'possible_graded')


def _computed_subsection_scores(learner, usage_key):
    """Return the points ``learner`` has earned on the subsection, computed without storing them."""
    try:
        structure = get_course_blocks(learner, usage_key)
    except (ItemNotFoundError, UsageKeyNotInBlockStructure) as exc:
        log.info('No block %s to grade for user %s.', usage_key, learner.id)
        raise NotFound() from exc
    if usage_key not in structure:
        raise SubsectionUnavailable()
    grade = SubsectionGradeFactory(learner, course_structure=structure).create(
        structure[usage_key], read_only=True, force_calculate=True,
    )
    return {
        'earned_all': grade.all_total.earned,
        'possible_all': grade.all_total.possible,
        'earned_graded': grade.graded_total.earned,
        'possible_graded': grade.graded_total.possible,
    }


def course_grading_policy(course_key, with_subsections=True, graded_only=False):
    """
    Return how ``course_key`` is graded.

    Arguments:
        with_subsections (bool): also describe the subsections learners can see,
            whether grades are frozen, and whether bulk grade management is
            offered. Without them the course content is read one level deep only.
        graded_only (bool): leave the subsections that carry no grade out.
    """
    course = course_content(course_key, depth=2 if with_subsections else 0)
    policy = {
        'grade_cutoffs': course.grade_cutoffs,
        'assignment_types': [
            {
                'type': assignment_type,
                'short_label': grader.short_label,
                'min_count': grader.min_count,
                'drop_count': grader.drop_count,
                'weight': weight,
            }
            for grader, assignment_type, weight in course.grader.subgraders
        ],
    }
    if with_subsections:
        policy['subsections'] = _grading_subsections(course, graded_only)
        policy['grades_frozen'] = grades_frozen(course_key, course.end)
        policy['bulk_management_enabled'] = _bulk_management_enabled(course_key)
    return policy


def grades_frozen(course_key, course_end):
    """
    Return whether the grades of ``course_key`` are frozen, given the course's end date.

    Grades freeze a configured number of days after the course ends, where
    freezing is switched on for the course. The end date is passed in rather
    than looked up because the shared lookup creates a missing course overview,
    and a read must not.
    """
    if not ENFORCE_FREEZE_GRADE_AFTER_COURSE_END.is_enabled(course_key) or not course_end:
        return False
    return timezone.now() > course_end + timedelta(settings.GRADEBOOK_FREEZE_DAYS)


def _grading_subsections(course, graded_only):
    """Return one entry per subsection learners can see, in course order."""
    labeler = get_default_short_labeler(course)
    subsections = []
    for section in _visible(course.get_children()):
        for subsection in _visible(section.get_children()):
            if graded_only and not subsection.graded:
                continue
            subsections.append({
                'usage_key': str(subsection.location),
                'display_name': subsection.display_name,
                'graded': subsection.graded,
                'assignment_type': subsection.format,
                'short_label': labeler(subsection.format) if subsection.graded else None,
            })
    return subsections


def _visible(blocks):
    """Return the blocks that are neither staff-only nor hidden from the course outline."""
    return [block for block in blocks if not block.visible_to_staff_only and not block.hide_from_toc]


def _bulk_management_enabled(course_key):
    """Return whether bulk grade management is offered: where it is switched on, or the course has a master's track."""
    if gradebook_bulk_management_enabled(course_key):
        return True
    return CourseMode.objects.filter(course_id=course_key, mode_slug=CourseMode.MASTERS).exists()


def submission_history_rows(enrollments, course_key, with_data=False):
    """
    Return one submission-history row per enrollment in ``course_key``, in the order given.

    Each row lists the scored problems of the course the learner has a recorded
    submission to. The submissions of the whole page are read together, so the
    number of queries does not grow with the page.

    Arguments:
        with_data (bool): also include the definition of each problem.

    Raises NotFound when the course has no content.
    """
    enrollments = list(enrollments)
    try:
        course = get_course(course_key, depth=4)
    except CourseRunNotFound as exc:
        raise NotFound() from exc
    problems = SubmissionHistoryView.get_problem_blocks(course)
    submissions = _submissions_by_learner_and_problem(enrollments, course_key, problems)
    rows = []
    for enrollment in enrollments:
        row = {'username': enrollment.user.username, 'problems': []}
        for problem in problems:
            history = submissions.get((enrollment.user_id, problem.location))
            if not history:
                continue
            entry = {
                'usage_key': str(problem.scope_ids.usage_id),
                'display_name': problem.display_name,
                'submissions': history,
            }
            if with_data:
                entry['data'] = problem.data
            row['problems'].append(entry)
        rows.append(row)
    return rows


def _submissions_by_learner_and_problem(enrollments, course_key, problems):
    """Return ``{(user id, problem location): [submission, ...]}``, newest first within each history table."""
    if not enrollments or not problems:
        return {}
    modules = list(StudentModule.objects.filter(
        course_id=course_key,
        student_id__in=[enrollment.user_id for enrollment in enrollments],
        module_state_key__in=[problem.location for problem in problems],
    ))
    if not modules:
        return {}
    module_keys = {module.id: (module.student_id, module.module_state_key) for module in modules}
    submissions = defaultdict(list)
    for entry in BaseStudentModuleHistory.get_history(modules):
        submissions[module_keys[entry.student_module_id]].append({
            'state': json.loads(entry.state) if entry.state is not None else None,
            'grade': entry.grade,
            'max_grade': entry.max_grade,
        })
    return submissions
