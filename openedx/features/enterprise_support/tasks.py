"""
Tasks for Enterprise.
"""


import logging

from celery import shared_task
from edx_django_utils.monitoring import set_code_owner_attribute
from enterprise.models import EnterpriseCourseEnrollment

from openedx.features.enterprise_support.utils import clear_data_consent_share_cache

log = logging.getLogger('edx.celery.task')


@shared_task(name='openedx.features.enterprise_support.tasks.clear_enterprise_customer_data_consent_share_cache')
@set_code_owner_attribute
def clear_enterprise_customer_data_consent_share_cache(enterprise_customer_uuid):
    """
        clears data_sharing_consent_needed cache for whole enterprise
    """
    enterprise_course_enrollments = EnterpriseCourseEnrollment.objects.filter(
        enterprise_customer_user__enterprise_customer__uuid=enterprise_customer_uuid
    )
    count = enterprise_course_enrollments.count()
    log.info(
        'Stated Clearing {count} data_sharing_consent_needed cache for enterprise customer {uuid}'.format(
            count=count,
            uuid=enterprise_customer_uuid,
        )
    )
    for enrollment in enterprise_course_enrollments:
        clear_data_consent_share_cache(
            enrollment.enterprise_customer_user.user_id,
            enrollment.course_id,
            enterprise_customer_uuid,
        )
    log.info('Ended Clearing data_sharing_consent_needed cache for enterprise customer {uuid}'.format(
        uuid=enterprise_customer_uuid,
    ))
