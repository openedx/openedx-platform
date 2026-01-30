"""
Grades related signals.
"""
import json
from contextlib import contextmanager
from logging import getLogger

from django.dispatch import receiver
from opaque_keys.edx.keys import CourseKey, LearningContextKey, UsageKey
from opaque_keys import InvalidKeyError
from openedx_events.learning.signals import EXTERNAL_GRADER_SCORE_SUBMITTED
from openedx_events.learning.signals import EXAM_ATTEMPT_REJECTED, EXAM_ATTEMPT_VERIFIED
from submissions.models import score_reset, score_set
from xblock.scorable import ScorableXBlockMixin, Score

from common.djangoapps.student.models import user_by_anonymous_id
from common.djangoapps.student.signals import ENROLLMENT_TRACK_UPDATED
from common.djangoapps.track.event_transaction_utils import get_event_transaction_id, get_event_transaction_type
from common.djangoapps.util.date_utils import to_timestamp
from lms.djangoapps.courseware.model_data import get_score, set_score
from lms.djangoapps.grades.tasks import (
    RECALCULATE_GRADE_DELAY_SECONDS,
    recalculate_course_and_subsection_grades_for_user,
    recalculate_subsection_grade_v3
)
from openedx.core.djangoapps.course_groups.signals.signals import COHORT_MEMBERSHIP_UPDATED
from openedx.core.djangoapps.signals.signals import (  # lint-amnesty, pylint: disable=wrong-import-order
    COURSE_GRADE_NOW_FAILED,
    COURSE_GRADE_NOW_PASSED
)
from openedx.core.lib.grade_utils import is_score_higher_or_equal
from xmodule.modulestore.django import modulestore

from .. import events
from ..constants import GradeOverrideFeatureEnum, ScoreDatabaseTableEnum
from ..course_grade_factory import CourseGradeFactory
from ..scores import weighted_score
from .signals import (
    COURSE_GRADE_PASSED_FIRST_TIME,
    PROBLEM_RAW_SCORE_CHANGED,
    PROBLEM_WEIGHTED_SCORE_CHANGED,
    SCORE_PUBLISHED,
    SUBSECTION_OVERRIDE_CHANGED,
    SUBSECTION_SCORE_CHANGED
)

log = getLogger(__name__)


@receiver(score_set, dispatch_uid='submissions_score_set_handler')
def submissions_score_set_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Consume the score_set signal defined in the Submissions API, and convert it
    to a PROBLEM_WEIGHTED_SCORE_CHANGED signal defined in this module. Converts the
    unicode keys for user, course and item into the standard representation for the
    PROBLEM_WEIGHTED_SCORE_CHANGED signal.

    This method expects that the kwargs dictionary will contain the following
    entries (See the definition of score_set):
      - 'points_possible': integer,
      - 'points_earned': integer,
      - 'anonymous_user_id': unicode,
      - 'course_id': unicode,
      - 'item_id': unicode
    """
    points_possible = kwargs['points_possible']
    points_earned = kwargs['points_earned']
    course_id = kwargs['course_id']
    usage_id = kwargs['item_id']
    user = user_by_anonymous_id(kwargs['anonymous_user_id'])
    if user is None:
        return
    if points_possible == 0:
        # This scenario is known to not succeed, see TNL-6559 for details.
        return

    PROBLEM_WEIGHTED_SCORE_CHANGED.send(
        sender=None,
        weighted_earned=points_earned,
        weighted_possible=points_possible,
        user_id=user.id,
        anonymous_user_id=kwargs['anonymous_user_id'],
        course_id=course_id,
        usage_id=usage_id,
        modified=kwargs['created_at'],
        score_db_table=ScoreDatabaseTableEnum.submissions,
    )


@receiver(score_reset, dispatch_uid='submissions_score_reset_handler')
def submissions_score_reset_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Consume the score_reset signal defined in the Submissions API, and convert
    it to a PROBLEM_WEIGHTED_SCORE_CHANGED signal indicating that the score
    has been set to 0/0. Converts the unicode keys for user, course and item
    into the standard representation for the PROBLEM_WEIGHTED_SCORE_CHANGED signal.

    This method expects that the kwargs dictionary will contain the following
    entries (See the definition of score_reset):
      - 'anonymous_user_id': unicode,
      - 'course_id': unicode,
      - 'item_id': unicode
    """
    course_id = kwargs['course_id']
    usage_id = kwargs['item_id']
    user = user_by_anonymous_id(kwargs['anonymous_user_id'])
    if user is None:
        return

    PROBLEM_WEIGHTED_SCORE_CHANGED.send(
        sender=None,
        weighted_earned=0,
        weighted_possible=0,
        user_id=user.id,
        anonymous_user_id=kwargs['anonymous_user_id'],
        course_id=course_id,
        usage_id=usage_id,
        modified=kwargs['created_at'],
        score_deleted=True,
        score_db_table=ScoreDatabaseTableEnum.submissions,
    )


@contextmanager
def disconnect_submissions_signal_receiver(signal):
    """
    Context manager to be used for temporarily disconnecting edx-submission's set or reset signal.

    Clear Student State on ORA problems currently results in a set->reset signal pair getting fired
    from submissions which leads to tasks being enqueued, one of which can never succeed. This context manager
    fixes the issue by disconnecting the "set" handler during the clear_state operation.
    """
    if signal == score_set:
        handler = submissions_score_set_handler
        dispatch_uid = 'submissions_score_set_handler'
    else:
        if signal != score_reset:
            raise ValueError("This context manager only handles score_set and score_reset signals.")
        handler = submissions_score_reset_handler
        dispatch_uid = 'submissions_score_reset_handler'

    signal.disconnect(dispatch_uid=dispatch_uid)
    try:
        yield
    finally:
        signal.connect(handler, dispatch_uid=dispatch_uid)


@receiver(SCORE_PUBLISHED)
def score_published_handler(sender, block, user, raw_earned, raw_possible, only_if_higher, **kwargs):  # pylint: disable=unused-argument
    """
    Handles whenever a block's score is published.
    Returns whether the score was actually updated.
    """
    update_score = True
    if only_if_higher:
        previous_score = get_score(user.id, block.location)

        if previous_score is not None:
            prev_raw_earned, prev_raw_possible = (previous_score.grade, previous_score.max_grade)

            if not is_score_higher_or_equal(prev_raw_earned, prev_raw_possible, raw_earned, raw_possible):
                update_score = False
                log.warning(
                    "Grades: Rescore is not higher than previous: "
                    "user: {}, block: {}, previous: {}/{}, new: {}/{} ".format(
                        user, block.location, prev_raw_earned, prev_raw_possible, raw_earned, raw_possible,
                    )
                )

    if update_score:
        # Set the problem score in CSM.
        score_modified_time = set_score(user.id, block.location, raw_earned, raw_possible)

        # Set the problem score on the xblock.
        if isinstance(block, ScorableXBlockMixin):
            block.set_score(Score(raw_earned=raw_earned, raw_possible=raw_possible))

        # Fire a signal (consumed by enqueue_subsection_update, below)
        PROBLEM_RAW_SCORE_CHANGED.send(
            sender=None,
            raw_earned=raw_earned,
            raw_possible=raw_possible,
            weight=getattr(block, 'weight', None),
            user_id=user.id,
            course_id=str(block.location.course_key),
            usage_id=str(block.location),
            only_if_higher=only_if_higher,
            modified=score_modified_time,
            score_db_table=ScoreDatabaseTableEnum.courseware_student_module,
            score_deleted=kwargs.get('score_deleted', False),
            grader_response=kwargs.get('grader_response', False)
        )
    return update_score


@receiver(PROBLEM_RAW_SCORE_CHANGED)
def problem_raw_score_changed_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Handles the raw score changed signal, converting the score to a
    weighted score and firing the PROBLEM_WEIGHTED_SCORE_CHANGED signal.
    """
    if kwargs['raw_possible'] is not None:
        weighted_earned, weighted_possible = weighted_score(
            kwargs['raw_earned'],
            kwargs['raw_possible'],
            kwargs['weight'],
        )
    else:  # TODO: remove as part of TNL-5982
        weighted_earned, weighted_possible = kwargs['raw_earned'], kwargs['raw_possible']

    PROBLEM_WEIGHTED_SCORE_CHANGED.send(
        sender=None,
        weighted_earned=weighted_earned,
        weighted_possible=weighted_possible,
        user_id=kwargs['user_id'],
        course_id=kwargs['course_id'],
        usage_id=kwargs['usage_id'],
        only_if_higher=kwargs['only_if_higher'],
        score_deleted=kwargs.get('score_deleted', False),
        modified=kwargs['modified'],
        score_db_table=kwargs['score_db_table'],
        grader_response=kwargs.get('grader_response', False)
    )


@receiver(PROBLEM_WEIGHTED_SCORE_CHANGED)
@receiver(SUBSECTION_OVERRIDE_CHANGED)
def enqueue_subsection_update(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Handles the PROBLEM_WEIGHTED_SCORE_CHANGED or SUBSECTION_OVERRIDE_CHANGED signals by
    enqueueing a subsection update operation to occur asynchronously.
    """
    events.grade_updated(**kwargs)
    context_key = LearningContextKey.from_string(kwargs['course_id'])
    if not context_key.is_course:
        return  # If it's not a course, it has no subsections, so skip the subsection grading update
    recalculate_subsection_grade_v3.apply_async(
        kwargs=dict(
            user_id=kwargs['user_id'],
            anonymous_user_id=kwargs.get('anonymous_user_id'),
            course_id=kwargs['course_id'],
            usage_id=kwargs['usage_id'],
            only_if_higher=kwargs.get('only_if_higher'),
            expected_modified_time=to_timestamp(kwargs['modified']),
            score_deleted=kwargs.get('score_deleted', False),
            event_transaction_id=str(get_event_transaction_id()),
            event_transaction_type=str(get_event_transaction_type()),
            score_db_table=kwargs['score_db_table'],
            force_update_subsections=kwargs.get('force_update_subsections', False),
        ),
        countdown=RECALCULATE_GRADE_DELAY_SECONDS,
    )


@receiver(SUBSECTION_SCORE_CHANGED)
def recalculate_course_grade_only(sender, course, course_structure, user, **kwargs):  # pylint: disable=unused-argument
    """
    Updates a saved course grade, but does not update the subsection
    grades the user has in this course.
    """
    CourseGradeFactory().update(user, course=course, course_structure=course_structure)


@receiver(ENROLLMENT_TRACK_UPDATED)
@receiver(COHORT_MEMBERSHIP_UPDATED)
def recalculate_course_and_subsection_grades(sender, user, course_key, countdown=None, **kwargs):  # pylint: disable=unused-argument
    """
    Updates a saved course grade, forcing the subsection grades
    from which it is calculated to update along the way.
    """
    recalculate_course_and_subsection_grades_for_user.apply_async(
        countdown=countdown,
        kwargs=dict(
            user_id=user.id,
            course_key=str(course_key)
        )
    )


@receiver(COURSE_GRADE_NOW_PASSED)
def listen_for_passing_grade(sender, user, course_id, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for a signal indicating that the user has passed a course run.

    Emits an edx.course.grade.now_passed event
    """
    events.course_grade_now_passed(user, course_id)


@receiver(COURSE_GRADE_NOW_FAILED)
def listen_for_failing_grade(sender, user, course_id, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for a signal indicating that the user has failed a course run.

    Emits an edx.course.grade.now_failed event
    """
    events.course_grade_now_failed(user, course_id)


@receiver(COURSE_GRADE_PASSED_FIRST_TIME)
def listen_for_course_grade_passed_first_time(sender, user_id, course_id, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for a signal indicating that the user has passed course grade first time.

    Emits an event edx.course.grade.passed.first_time
    """
    events.course_grade_passed_first_time(user_id, course_id)
    events.fire_segment_event_on_course_grade_passed_first_time(user_id, course_id)


@receiver(EXAM_ATTEMPT_VERIFIED)
def exam_attempt_verified_event_handler(sender, signal, **kwargs):  # pylint: disable=unused-argument
    """
    Consume `EXAM_ATTEMPT_VERIFIED` events from the event bus. This will trigger
    an undo section override, if one exists.
    """
    from ..api import should_override_grade_on_rejected_exam, undo_override_subsection_grade

    event_data = kwargs.get('exam_attempt')
    user_data = event_data.student_user
    course_key = event_data.course_key
    usage_key = event_data.usage_key

    if should_override_grade_on_rejected_exam(course_key):
        undo_override_subsection_grade(user_data.id, course_key, usage_key, GradeOverrideFeatureEnum.proctoring)


@receiver(EXAM_ATTEMPT_REJECTED)
def exam_attempt_rejected_event_handler(sender, signal, **kwargs):  # pylint: disable=unused-argument
    """
    Consume `EXAM_ATTEMPT_REJECTED` events from the event bus. This will trigger a subsection override.
    """
    from ..api import override_subsection_grade

    event_data = kwargs.get('exam_attempt')
    override_grade_value = 0.0
    user_data = event_data.student_user
    course_key = event_data.course_key
    usage_key = event_data.usage_key

    override_subsection_grade(
        user_data.id,
        course_key,
        usage_key,
        earned_all=override_grade_value,
        earned_graded=override_grade_value,
        feature=GradeOverrideFeatureEnum.proctoring,
        overrider=None,
        comment=None,
    )


@receiver(EXTERNAL_GRADER_SCORE_SUBMITTED)
def handle_external_grader_score(signal, sender, score, **kwargs):
    """
    Event handler for external grader score submissions.

    This function is triggered when an external grader submits a score through the
    EXTERNAL_GRADER_SCORE_SUBMITTED signal. It processes the score and updates
    the corresponding XBlock instance with the grading results.

    Args:
       signal: The signal that triggered this handler
       sender: The object that sent the signal
       score: An object containing the score data with attributes:
           - score_msg: The actual score message/response from the grader
           - course_id: String ID of the course
           - user_id: ID of the user who submitted the problem
           - module_id: ID of the module/problem
           - submission_id: ID of the submission
           - queue_key: Key identifying the submission in the queue
           - queue_name: Name of the queue used for grading
       **kwargs: Additional keyword arguments passed with the signal

    The function logs details about the score event, formats the grader message
    appropriately, and then calls the module's score_update handler to record
    the grade in the learning management system.
    """

    log.info(f"Received external grader score event: {signal}, {sender}, {score}, {kwargs}")

    grader_msg = score.score_msg
    log.info(
        "External grader event score payload received: user_id=%s, module_id=%s, submission_id=%s, course_id=%s",
        score.user_id,
        score.module_id,
        score.submission_id,
        score.course_id,
    )

    # Since we already confirm this in edx-submissions, it is safe to parse this
    grader_msg = json.loads(grader_msg)
    log.info(f"External grader score: {grader_msg['score']}")

    data = {
        'xqueue_header': json.dumps({
            'lms_key': str(score.submission_id),
            'queue_name': score.queue_name
        }),
        'xqueue_body': json.dumps(grader_msg),
        'queuekey': score.queue_key
    }

    try:
        course_key = CourseKey.from_string(score.course_id)
        course = modulestore().get_course(course_key, depth=0)
    except InvalidKeyError:
        log.error("Invalid course_id received from external grader: %s", score.course_id)
        return

    try:
        usage_key = UsageKey.from_string(score.module_id)
    except InvalidKeyError:
        log.error("Invalid usage key received from external grader: %s", score.module_id)
        return

    # pylint: disable=broad-exception-caught
    try:
        # Use our new function instead of load_single_xblock
        # NOTE: Importing this at module level causes a circular import because
        # score_render → block_render → grades signals → back into this module.
        # Keeping it inside the handler avoids that by loading it only when needed.
        from lms.djangoapps.grades.score_render import load_xblock_for_external_grader
        instance = load_xblock_for_external_grader(score.user_id,
                                                   course_key,
                                                   usage_key,
                                                   course=course)

        # Call the handler method (mirroring the original xqueue_callback)
        instance.handle_ajax('score_update', data)

        # Save any state changes
        instance.save()

        log.info(f"Successfully processed external grade for module {score.module_id}, user {score.user_id}")

    except Exception as e:
        log.exception(
            "Error processing external grade for user_id=%s, module_id=%s, submission_id=%s: %s",
            score.user_id,
            score.module_id,
            score.submission_id,
            e,
        )
        raise
