"""
Emits course grade events.
"""
from logging import getLogger

from crum import get_current_user
from django.conf import settings
from eventtracking import tracker
from openedx_events.learning.data import (
    CcxCourseData,
    CcxCoursePassingStatusData,
    CourseData,
    CoursePassingStatusData,
    UserData,
    UserPersonalData,
)
from openedx_events.learning.signals import CCX_COURSE_PASSING_STATUS_UPDATED, COURSE_PASSING_STATUS_UPDATED

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.track import contexts, segment
from common.djangoapps.track.event_transaction_utils import (
    create_new_event_transaction_id,
    get_event_transaction_id,
    get_event_transaction_type,
    set_event_transaction_type,
)
from lms.djangoapps.grades.signals.signals import SCHEDULE_FOLLOW_UP_SEGMENT_EVENT_FOR_COURSE_PASSED_FIRST_TIME
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.enterprise_support.context import get_enterprise_event_context

log = getLogger(__name__)

COURSE_GRADE_CALCULATED = 'edx.grades.course.grade_calculated'
GRADES_OVERRIDE_EVENT_TYPE = 'edx.grades.problem.score_overridden'
GRADES_RESCORE_EVENT_TYPE = 'edx.grades.problem.rescored'
PROBLEM_SUBMITTED_EVENT_TYPE = 'edx.grades.problem.submitted'
STATE_DELETED_EVENT_TYPE = 'edx.grades.problem.state_deleted'
SUBSECTION_OVERRIDE_EVENT_TYPE = 'edx.grades.subsection.score_overridden'
SUBSECTION_GRADE_CALCULATED = 'edx.grades.subsection.grade_calculated'
COURSE_GRADE_PASSED_FIRST_TIME_EVENT_TYPE = 'edx.course.grade.passed.first_time'
COURSE_GRADE_NOW_PASSED_EVENT_TYPE = 'edx.course.grade.now_passed'
COURSE_GRADE_NOW_FAILED_EVENT_TYPE = 'edx.course.grade.now_failed'


def grade_updated(**kwargs):
    """
    Emits the appropriate grade-related event after checking for which
    event-transaction is active.

    Emits a problem.submitted event only if there is no current event
    transaction type, i.e. we have not reached this point in the code via
    an outer event type (such as problem.rescored or score_overridden).
    """
    root_type = get_event_transaction_type()

    if not root_type:
        root_id = get_event_transaction_id()
        if not root_id:
            root_id = create_new_event_transaction_id()
        set_event_transaction_type(PROBLEM_SUBMITTED_EVENT_TYPE)
        tracker.emit(
            str(PROBLEM_SUBMITTED_EVENT_TYPE),
            {
                'user_id': str(kwargs['user_id']),
                'course_id': str(kwargs['course_id']),
                'problem_id': str(kwargs['usage_id']),
                'event_transaction_id': str(root_id),
                'event_transaction_type': str(PROBLEM_SUBMITTED_EVENT_TYPE),
                'weighted_earned': kwargs.get('weighted_earned'),
                'weighted_possible': kwargs.get('weighted_possible'),
            }
        )

    elif root_type in [GRADES_RESCORE_EVENT_TYPE, GRADES_OVERRIDE_EVENT_TYPE]:
        current_user = get_current_user()
        instructor_id = getattr(current_user, 'id', None)
        tracker.emit(
            str(root_type),
            {
                'course_id': str(kwargs['course_id']),
                'user_id': str(kwargs['user_id']),
                'problem_id': str(kwargs['usage_id']),
                'new_weighted_earned': kwargs.get('weighted_earned'),
                'new_weighted_possible': kwargs.get('weighted_possible'),
                'only_if_higher': kwargs.get('only_if_higher'),
                'instructor_id': str(instructor_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(root_type),
            }
        )

    elif root_type in [SUBSECTION_OVERRIDE_EVENT_TYPE]:
        tracker.emit(
            str(root_type),
            {
                'course_id': str(kwargs['course_id']),
                'user_id': str(kwargs['user_id']),
                'problem_id': str(kwargs['usage_id']),
                'only_if_higher': kwargs.get('only_if_higher'),
                'override_deleted': kwargs.get('score_deleted', False),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(root_type),
            }
        )


def subsection_grade_calculated(subsection_grade):
    """
    Emits an edx.grades.subsection.grade_calculated event
    with data from the passed subsection_grade.
    """
    event_name = SUBSECTION_GRADE_CALCULATED
    context = contexts.course_context_from_course_id(subsection_grade.course_id)
    # TODO (AN-6134): remove this context manager
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(
            event_name,
            {
                'user_id': str(subsection_grade.user_id),
                'course_id': str(subsection_grade.course_id),
                'block_id': str(subsection_grade.usage_key),
                'course_version': str(subsection_grade.course_version),
                'weighted_total_earned': subsection_grade.earned_all,
                'weighted_total_possible': subsection_grade.possible_all,
                'weighted_graded_earned': subsection_grade.earned_graded,
                'weighted_graded_possible': subsection_grade.possible_graded,
                'first_attempted': str(subsection_grade.first_attempted),
                'subtree_edited_timestamp': str(subsection_grade.subtree_edited_timestamp),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
                'visible_blocks_hash': str(subsection_grade.visible_blocks_id),
            }
        )


def course_grade_calculated(course_grade):
    """
    Emits an edx.grades.course.grade_calculated event
    with data from the passed course_grade.
    """
    event_name = COURSE_GRADE_CALCULATED
    context = contexts.course_context_from_course_id(course_grade.course_id)
    # TODO (AN-6134): remove this context manager
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(
            event_name,
            {
                'user_id': str(course_grade.user_id),
                'course_id': str(course_grade.course_id),
                'course_version': str(course_grade.course_version),
                'percent_grade': course_grade.percent_grade,
                'letter_grade': str(course_grade.letter_grade),
                'course_edited_timestamp': str(course_grade.course_edited_timestamp),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
                'grading_policy_hash': str(course_grade.grading_policy_hash),
            }
        )


def course_grade_passed_first_time(user_id, course_id):
    """
    Emits an event edx.course.grade.passed.first_time
    with data from the passed course_grade.
    """
    event_name = COURSE_GRADE_PASSED_FIRST_TIME_EVENT_TYPE
    context = contexts.course_context_from_course_id(course_id)
    context_enterprise = get_enterprise_event_context(user_id, course_id)
    context.update(context_enterprise)
    # TODO (AN-6134): remove this context manager
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(
            event_name,
            {
                'user_id': str(user_id),
                'course_id': str(course_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type())
            }
        )


def course_grade_now_passed(user, course_id):
    """
    Emits an edx.course.grade.now_passed and passing status updated events
    with data from the course and user passed now.
    """
    event_name = COURSE_GRADE_NOW_PASSED_EVENT_TYPE
    context = contexts.course_context_from_course_id(course_id)
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(
            event_name,
            {
                'user_id': str(user.id),
                'course_id': str(course_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type())
            }
        )

    _emit_course_passing_status_update(user, course_id, is_passing=True)


def course_grade_now_failed(user, course_id):
    """
    Emits an edx.course.grade.now_failed and passing status updated events
    with data from the course and user failed now.
    """
    event_name = COURSE_GRADE_NOW_FAILED_EVENT_TYPE
    context = contexts.course_context_from_course_id(course_id)
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(
            event_name,
            {
                'user_id': str(user.id),
                'course_id': str(course_id),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type())
            }
        )

    _emit_course_passing_status_update(user, course_id, is_passing=False)


def fire_segment_event_on_course_grade_passed_first_time(user_id, course_locator):
    """
    Fire a segment event `edx.course.grade.passed.first_time` with the desired data.

    * Event should be only fired for learners enrolled in paid enrollment modes.
    """
    event_name = 'edx.course.learner.passed.first_time'
    courserun_key = str(course_locator)
    courserun_org = course_locator.org
    paid_enrollment_modes = (
        CourseMode.MASTERS,
        CourseMode.VERIFIED,
        CourseMode.CREDIT_MODE,
        CourseMode.PROFESSIONAL,
        CourseMode.NO_ID_PROFESSIONAL_MODE,
    )

    try:
        enrollment = CourseEnrollment.objects.get(
            user_id=user_id,
            course_id=courserun_key,
            mode__in=paid_enrollment_modes
        )
    except CourseEnrollment.DoesNotExist:
        return

    try:
        courserun_display_name = CourseOverview.objects.values_list('display_name', flat=True).get(id=courserun_key)
    except CourseOverview.DoesNotExist:
        return

    event_properties = {
        'LMS_ENROLLMENT_ID': enrollment.id,
        'COURSE_TITLE': courserun_display_name,
        'COURSE_ORG_NAME': courserun_org,
    }
    if getattr(settings, 'OUTCOME_SURVEYS_EVENTS_ENABLED', False):
        segment.track(user_id, event_name, event_properties)

        # fire signal so that a follow up event can be scheduled in outcome_surveys app
        SCHEDULE_FOLLOW_UP_SEGMENT_EVENT_FOR_COURSE_PASSED_FIRST_TIME.send(
            sender=None,
            user_id=user_id,
            course_id=course_locator,
            event_properties=event_properties
        )

    log.info("Segment event fired for passed learners. Event: [{}], Data: [{}]".format(event_name, event_properties))


def _emit_course_passing_status_update(user, course_id, is_passing):
    """
    Emit course passing status event according to the course type.
    The status of event is determined by is_passing parameter.
    """
    if hasattr(course_id, 'ccx'):
        # .. event_implemented_name: CCX_COURSE_PASSING_STATUS_UPDATED
        # .. event_type: org.openedx.learning.ccx.course.passing.status.updated.v1
        CCX_COURSE_PASSING_STATUS_UPDATED.send_event(
            course_passing_status=CcxCoursePassingStatusData(
                is_passing=is_passing,
                user=UserData(
                    pii=UserPersonalData(
                        username=user.username,
                        email=user.email,
                        name=user.get_full_name() or user.profile.name,
                    ),
                    id=user.id,
                    is_active=user.is_active,
                ),
                course=CcxCourseData(
                    ccx_course_key=course_id,
                    master_course_key=course_id.to_course_locator(),
                ),
            )
        )
    else:
        # .. event_implemented_name: COURSE_PASSING_STATUS_UPDATED
        # .. event_type: org.openedx.learning.course.passing.status.updated.v1
        COURSE_PASSING_STATUS_UPDATED.send_event(
            course_passing_status=CoursePassingStatusData(
                is_passing=is_passing,
                user=UserData(
                    pii=UserPersonalData(
                        username=user.username,
                        email=user.email,
                        name=user.get_full_name() or user.profile.name,
                    ),
                    id=user.id,
                    is_active=user.is_active,
                ),
                course=CourseData(
                    course_key=course_id,
                ),
            )
        )
