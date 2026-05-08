"""
This file contains celery tasks for entitlements-related functionality.
"""
import logging

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings  # pylint: disable=unused-import  # noqa: F401
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute

from common.djangoapps.entitlements.models import CourseEntitlement, CourseEntitlementSupportDetail

LOGGER = get_task_logger(__name__)
log = logging.getLogger(__name__)

# Maximum number of retries before giving up on awarding credentials.
# For reference, 11 retries with exponential backoff yields a maximum waiting
# time of 2047 seconds (about 30 minutes). Setting this to None could yield
# unwanted behavior: infinite retries.
MAX_RETRIES = 11

User = get_user_model()


@shared_task(bind=True, ignore_result=True)
@set_code_owner_attribute
def expire_old_entitlements(self, start, end, logid='...'):
    """
    This task is designed to be called to process a bundle of entitlements
    that might be expired and confirm if we can do so. This is useful when
    checking if an entitlement has just been abandoned by the learner and needs
    to be expired. (In the normal course of a learner using the platform, the
    entitlement will expire itself. But if a learner doesn't log in... So we
    run this task every now and then to clear the backlog.)

    Args:
        start (int): The beginning id in the database to examine
        end (int): The id in the database to stop examining at (i.e. range is exclusive)
        logid (str): A string to identify this task in the logs

    Returns:
        None

    """
    LOGGER.info('Running task expire_old_entitlements %d:%d [%s]', start, end, logid)

    # This query could be optimized to return a more narrow set, but at a
    # complexity cost. See bug LEARNER-3451 about improving it.
    entitlements = CourseEntitlement.objects.filter(expired_at__isnull=True, id__gte=start, id__lte=end)

    countdown = 2 ** self.request.retries

    try:
        for entitlement in entitlements:
            # This property request will update the expiration if necessary as
            # a side effect. We could manually call update_expired_at(), but
            # let's use the same API the rest of the LMS does, to mimic normal
            # usage and allow the update call to be an internal detail.
            if entitlement.expired_at_datetime:
                LOGGER.info('Expired entitlement with id %d [%s]', entitlement.id, logid)

    except Exception as exc:
        LOGGER.exception('Failed to expire entitlements [%s]', logid)
        # The call above is idempotent, so retry at will
        raise self.retry(exc=exc, countdown=countdown, max_retries=MAX_RETRIES)  # noqa: B904

    LOGGER.info('Successfully completed the task expire_old_entitlements after examining %d entries [%s]', entitlements.count(), logid)  # pylint: disable=line-too-long


@shared_task(bind=True)
@set_code_owner_attribute
def expire_and_create_entitlements(self, entitlement_ids, support_username):
    """
    Expire entitlements older than one year.

    Exception: if the entitlement is for a course in a list of exceptional courses,
    expire those entitlements if they're older than 18 months instead.

    Then create a copy of the expired entitlement to renew it for another year
    / 18 months.

    Args:
        entitlement_ids (List<int>): A list of entitlement ids to expire.
        support_username (str): The username to attribute the entitlement
            expiration and recreation to.

    Returns:
        None

    """
    support_user = User.objects.get(username=support_username)

    first_entitlement_id = entitlement_ids[0]
    last_entitlement_id = entitlement_ids[-1]

    log.info(
        'Running task expire_and_create_entitlements over %d entitlements from id %d to id %d, task id :%s',
        len(entitlement_ids),
        first_entitlement_id,
        last_entitlement_id,
        self.request.id
    )

    try:
        for entitlement_id in entitlement_ids:
            entitlement = CourseEntitlement.objects.get(id=entitlement_id)
            log.info('Started expiring entitlement with id %d, task id :%s',
                     entitlement.id,
                     self.request.id)
            entitlement.expire_entitlement()
            log.info('Expired entitlement with id %d as expiration period has reached, task id :%s',
                     entitlement.id,
                     self.request.id)
            support_detail = {
                'action': 'EXPIRE',
                'comments': 'REV-3574',
                'entitlement': entitlement,
                'support_user': support_user,
            }
            CourseEntitlementSupportDetail.objects.create(**support_detail)

            # Creating new entitlement and support details
            new_entitlement_detail = {
                'course_uuid': entitlement.course_uuid,
                'user': entitlement.user,
                'mode': entitlement.mode,
                'refund_locked': True,
            }
            new_entitlement = CourseEntitlement.objects.create(**new_entitlement_detail)
            support_detail = {
                'action': 'CREATE',
                'comments': 'REV-3574',
                'entitlement': new_entitlement,
                'support_user': support_user,
            }
            CourseEntitlementSupportDetail.objects.create(**support_detail)
            log.info(
                'created new entitlement with id %d corresponding to above expired entitlement'
                'with id %d, task id :%s ',
                new_entitlement.id,
                entitlement.id,
                self.request.id
            )

    except Exception as exc:  # pylint: disable=broad-except  # noqa: F841
        log.exception('Failed to expire entitlements that reached their expiration period, task id :%s',
                      self.request.id)

    log.info('Successfully completed the task expire_and_create_entitlements after examining'
             '%d entries, task id :%s',
             len(entitlement_ids),
             self.request.id)
