"""
Signal handler for setting default course mode expiration dates
"""


import logging  # noqa: I001

from crum import get_current_user
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.signals import post_save
from django.dispatch.dispatcher import receiver

from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.partitions.partitions import ENROLLMENT_TRACK_PARTITION_ID

from .models import CourseMode, CourseModeExpirationConfig

log = logging.getLogger(__name__)


@receiver(SignalHandler.course_published)
def _listen_for_course_publish(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Catches the signal that a course has been published in Studio and
    sets the verified mode dates to defaults.
    """
    try:
        verified_mode = CourseMode.objects.get(course_id=course_key, mode_slug=CourseMode.VERIFIED)
        if _should_update_date(verified_mode):
            course = modulestore().get_course(course_key)
            if not course or not course.end:
                return None
            verification_window = CourseModeExpirationConfig.current().verification_window
            new_expiration_datetime = course.end - verification_window

            if verified_mode.expiration_datetime != new_expiration_datetime:
                # Set the expiration_datetime without triggering the explicit flag
                verified_mode._expiration_datetime = new_expiration_datetime  # pylint: disable=protected-access
                verified_mode.save()
    except ObjectDoesNotExist:
        pass


def _should_update_date(verified_mode):
    """ Returns whether or not the verified mode should be updated. """
    return not(verified_mode is None or verified_mode.expiration_datetime_is_explicit)


@receiver(post_save, sender=CourseMode)
def update_masters_access_course(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """
    Update all blocks in the verified content group to include the master's content group
    """
    if instance.mode_slug != CourseMode.MASTERS:
        return
    masters_id = getattr(settings, 'COURSE_ENROLLMENT_MODES', {}).get('masters', {}).get('id', None)
    verified_id = getattr(settings, 'COURSE_ENROLLMENT_MODES', {}).get('verified', {}).get('id', None)
    if not (masters_id and verified_id):
        log.error("Missing settings.COURSE_ENROLLMENT_MODES -> verified:%s masters:%s", verified_id, masters_id)
        return

    course_id = instance.course_id
    user = get_current_user()
    user_id = user.id if user else None
    store = modulestore()

    with store.bulk_operations(course_id):
        try:
            items = store.get_items(course_id, settings={'group_access': {'$exists': True}}, include_orphans=False)
        except ItemNotFoundError:
            return
        for item in items:
            group_access = item.group_access
            enrollment_groups = group_access.get(ENROLLMENT_TRACK_PARTITION_ID, None)
            if enrollment_groups is not None:
                if verified_id in enrollment_groups and masters_id not in enrollment_groups:
                    enrollment_groups.append(masters_id)
                    item.group_access = group_access
                    log.info("Publishing %s with Master's group access", item.location)
                    store.update_item(item, user_id)
                    store.publish(item.location, user_id)
