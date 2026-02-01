"""
Code related to the handling of Proctored Exams in Studio
using the edx-proctoring plugin.

Courses using the exam IDA are handled by ./exams.py
"""


import logging

from django.conf import settings
from edx_proctoring.api import (
    create_exam,
    create_exam_review_policy,
    get_all_exams_for_course,
    get_exam_by_content_id,
    remove_review_policy,
    update_exam,
    update_review_policy
)
from edx_proctoring.exceptions import ProctoredExamNotFoundException, ProctoredExamReviewPolicyNotFoundException

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from .helpers import is_item_in_course_tree

log = logging.getLogger(__name__)


def register_special_exams(course_key):
    """
    This is typically called on a course published signal. The course is examined for sequences
    that are marked as timed exams. Then these are registered with the edx-proctoring
    subsystem. Likewise, if formerly registered exams are unmarked, then those
    registered exams are marked as inactive
    """
    if not settings.FEATURES.get('ENABLE_SPECIAL_EXAMS'):
        # if feature is not enabled then do a quick exit
        return

    store = modulestore()
    course = store.get_course(course_key)
    if course is None:
        raise ItemNotFoundError("Course {} does not exist", str(course_key))  # lint-amnesty, pylint: disable=raising-format-tuple

    if not course.enable_proctored_exams and not course.enable_timed_exams:
        # likewise if course does not have these features turned on
        # then quickly exit
        return

    # get all sequences, since they can be marked as timed/proctored exams
    _timed_exams = store.get_items(
        course_key,
        qualifiers={
            'category': 'sequential',
        },
        settings={
            'is_time_limited': True,
        }
    )

    # filter out any potential dangling sequences
    timed_exams = [
        timed_exam
        for timed_exam in _timed_exams
        if is_item_in_course_tree(timed_exam)
    ]

    # enumerate over list of sequences which are time-limited and
    # add/update any exam entries in edx-proctoring
    for timed_exam in timed_exams:
        msg = (
            'Found {location} as a timed-exam in course structure. Inspecting...'.format(
                location=str(timed_exam.location)
            )
        )
        log.info(msg)

        exam_metadata = {
            'exam_name': timed_exam.display_name,
            'time_limit_mins': timed_exam.default_time_limit_minutes,
            'due_date': timed_exam.due if not course.self_paced else None,
            'is_proctored': timed_exam.is_proctored_exam,
            # backends that support onboarding exams will treat onboarding exams as practice
            'is_practice_exam': timed_exam.is_practice_exam or timed_exam.is_onboarding_exam,
            'is_active': True,
            'hide_after_due': timed_exam.hide_after_due,
            'backend': course.proctoring_provider,
        }

        try:
            exam = get_exam_by_content_id(str(course_key), str(timed_exam.location))
            # update case, make sure everything is synced
            exam_metadata['exam_id'] = exam['id']

            exam_id = update_exam(**exam_metadata)
            msg = 'Updated timed exam {exam_id}'.format(exam_id=exam['id'])
            log.info(msg)

        except ProctoredExamNotFoundException:
            exam_metadata['course_id'] = str(course_key)
            exam_metadata['content_id'] = str(timed_exam.location)
            exam_id = create_exam(**exam_metadata)
            msg = f'Created new timed exam {exam_id}'
            log.info(msg)

        exam_review_policy_metadata = {
            'exam_id': exam_id,
            'set_by_user_id': timed_exam.edited_by,
            'review_policy': timed_exam.exam_review_rules,
        }

        # only create/update exam policy for the proctored exams
        if timed_exam.is_proctored_exam and not timed_exam.is_practice_exam and not timed_exam.is_onboarding_exam:
            try:
                update_review_policy(**exam_review_policy_metadata)
            except ProctoredExamReviewPolicyNotFoundException:
                if timed_exam.exam_review_rules:  # won't save an empty rule.
                    create_exam_review_policy(**exam_review_policy_metadata)
                    msg = f'Created new exam review policy with exam_id {exam_id}'
                    log.info(msg)
        else:
            try:
                # remove any associated review policy
                remove_review_policy(exam_id=exam_id)
            except ProctoredExamReviewPolicyNotFoundException:
                pass

    # then see which exams we have in edx-proctoring that are not in
    # our current list. That means the the user has disabled it
    exams = get_all_exams_for_course(course_key)

    for exam in exams:
        if exam['is_active']:
            # try to look up the content_id in the sequences location

            search = [
                timed_exam for timed_exam in timed_exams if
                str(timed_exam.location) == exam['content_id']
            ]
            if not search:
                # This means it was turned off in Studio, we need to mark
                # the exam as inactive (we don't delete!)
                msg = 'Disabling timed exam {exam_id}'.format(exam_id=exam['id'])
                log.info(msg)
                update_exam(
                    exam_id=exam['id'],
                    is_proctored=False,
                    is_active=False,
                )
