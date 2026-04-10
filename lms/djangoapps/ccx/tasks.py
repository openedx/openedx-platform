"""
Asynchronous tasks for the CCX app.
"""


import logging

from ccx_keys.locator import CCXLocator
from django.dispatch import receiver
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import CourseLocator

from lms import CELERY_APP
from lms.djangoapps.ccx.models import CustomCourseForEdX
from xmodule.modulestore.django import SignalHandler  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger("edx.ccx")


@receiver(SignalHandler.course_published)
def course_published_handler(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Consume signals that indicate course published.

    For a master course publish, re-emit the signal for each derived CCX so that
    per-CCX receivers (CourseOverview, grades, schedules, ...) run.

    For a CCX publish, regenerate the CCX's outline in ``learning_sequences``
    from the parent course's outline. The Studio-side handler that writes
    ``CourseOutlineData`` runs only in the CMS process and never sees CCX
    signals dispatched from LMS, so without this path CCX courses never
    acquire an outline and the LMS Outline view fails. See issue #37365 and
    ADR 0011 (LMS must not touch the modulestore).
    """
    if isinstance(course_key, CCXLocator):
        update_ccx_course_outline.delay(str(course_key))
    else:
        send_ccx_course_published.delay(str(course_key))


@CELERY_APP.task
@set_code_owner_attribute
def update_ccx_course_outline(ccx_course_key_str):
    """
    Refresh the Learning Sequences outline for a single CCX course.

    Runs in the LMS Celery worker. Uses only the public
    ``learning_sequences`` API and does not touch the modulestore, per
    ADR 0011. See issue #37365.
    """
    from openedx.core.djangoapps.content.learning_sequences.api import (
        key_supports_outlines,
        replace_course_outline_for_ccx,
    )
    from openedx.core.djangoapps.content.learning_sequences.data import CourseOutlineData

    try:
        ccx_course_key = CCXLocator.from_string(ccx_course_key_str)
    except InvalidKeyError:
        log.exception("update_ccx_course_outline: invalid key %s", ccx_course_key_str)
        return

    if not key_supports_outlines(ccx_course_key):
        return

    try:
        replace_course_outline_for_ccx(ccx_course_key)
    except CourseOutlineData.DoesNotExist:
        # Parent course has not been published through Studio yet. Log and
        # bail; the next parent publish will cascade down here via
        # send_ccx_course_published and retry.
        log.warning(
            "update_ccx_course_outline: no parent outline for %s yet; "
            "will retry on next parent publish",
            ccx_course_key,
        )


@CELERY_APP.task
@set_code_owner_attribute
def send_ccx_course_published(course_key):
    """
    Find all CCX derived from this course, and send course published event for them.
    """
    course_key = CourseLocator.from_string(course_key)
    for ccx in CustomCourseForEdX.objects.filter(course_id=course_key):
        try:
            ccx_key = CCXLocator.from_course_locator(course_key, str(ccx.id))
        except InvalidKeyError:
            log.info('Attempt to publish course with deprecated id. Course: %s. CCX: %s', course_key, ccx.id)
            continue
        responses = SignalHandler.course_published.send(
            sender=ccx,
            course_key=ccx_key
        )
        for rec, response in responses:
            log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)
