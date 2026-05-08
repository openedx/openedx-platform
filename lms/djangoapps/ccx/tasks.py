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
from xmodule.modulestore.django import SignalHandler  # pylint: disable=wrong-import-order

log = logging.getLogger("edx.ccx")


@receiver(SignalHandler.course_published)
def course_published_handler(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Consume signals that indicate course published. If course already a CCX, do nothing.
    """
    if not isinstance(course_key, CCXLocator):
        send_ccx_course_published.delay(str(course_key))


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
