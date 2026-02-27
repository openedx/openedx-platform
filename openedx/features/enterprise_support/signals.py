"""
This module contains signals related to enterprise.
"""


import logging

from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from enterprise.models import EnterpriseCourseEnrollment, EnterpriseCustomer
from slumber.exceptions import HttpClientError

from common.djangoapps.student.signals import UNENROLL_DONE
from openedx.core.djangoapps.commerce.utils import ecommerce_api_client
from openedx.core.djangoapps.signals.signals import COURSE_ASSESSMENT_GRADE_CHANGED, COURSE_GRADE_NOW_PASSED
from openedx.features.enterprise_support.tasks import clear_enterprise_customer_data_consent_share_cache
from openedx.features.enterprise_support.utils import clear_data_consent_share_cache, is_enterprise_learner

# This is a temporary import path while we transition from integrated_channels to channel_integrations
if getattr(settings, 'ENABLE_LEGACY_INTEGRATED_CHANNELS', True):
    from integrated_channels.integrated_channel.tasks import (
        transmit_single_learner_data,
        transmit_single_subsection_learner_data
    )
else:
    from channel_integrations.integrated_channel.tasks import (
        transmit_single_learner_data,
        transmit_single_subsection_learner_data
    )

log = logging.getLogger(__name__)


@receiver(post_save, sender=EnterpriseCourseEnrollment)
def update_dsc_cache_on_course_enrollment(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """
        clears data_sharing_consent_needed cache after Enterprise Course Enrollment
    """
    clear_data_consent_share_cache(
        instance.enterprise_customer_user.user_id,
        instance.course_id,
        str(instance.enterprise_customer_user.enterprise_customer.uuid)
    )


@receiver(pre_save, sender=EnterpriseCustomer)
def update_dsc_cache_on_enterprise_customer_update(sender, instance, **kwargs):
    """
        clears data_sharing_consent_needed cache after enable_data_sharing_consent flag is changed.
    """
    old_instance = sender.objects.filter(pk=instance.uuid).first()
    if old_instance:   # instance already exists, so it's updating.
        new_value = instance.enable_data_sharing_consent
        old_value = old_instance.enable_data_sharing_consent
        if new_value != old_value:
            kwargs = {'enterprise_customer_uuid': str(instance.uuid)}
            result = clear_enterprise_customer_data_consent_share_cache.apply_async(kwargs=kwargs)
            log.info("DSC: Created {task_name}[{task_id}] with arguments {kwargs}".format(
                task_name=clear_enterprise_customer_data_consent_share_cache.name,
                task_id=result.task_id,
                kwargs=kwargs,
            ))


@receiver(COURSE_GRADE_NOW_PASSED, dispatch_uid="new_passing_enterprise_learner")
def handle_enterprise_learner_passing_grade(sender, user, course_id, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for a learner passing a course, transmit data to relevant integrated channel
    """
    if is_enterprise_learner(user):
        kwargs = {
            'username': str(user.username),
            'course_run_id': str(course_id)
        }

        transmit_single_learner_data.apply_async(kwargs=kwargs)


@receiver(COURSE_ASSESSMENT_GRADE_CHANGED)
def handle_enterprise_learner_subsection(sender, user, course_id, subsection_id, subsection_grade, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for an enterprise learner completing a subsection, transmit data to relevant integrated channel.
    """
    if is_enterprise_learner(user):
        kwargs = {
            'username': str(user.username),
            'course_run_id': str(course_id),
            'subsection_id': str(subsection_id),
            'grade': str(subsection_grade),
        }

        transmit_single_subsection_learner_data.apply_async(kwargs=kwargs)


@receiver(UNENROLL_DONE)
def refund_order_voucher(sender, course_enrollment, skip_refund=False, **kwargs):  # pylint: disable=unused-argument
    """
        Call the /api/v2/enterprise/coupons/create_refunded_voucher/ API to create new voucher and assign it to user.
    """

    if skip_refund:
        return
    if not course_enrollment.refundable():
        return
    if not course_enrollment.is_order_voucher_refundable():
        return
    if not EnterpriseCourseEnrollment.objects.filter(
        enterprise_customer_user__user_id=course_enrollment.user_id,
        course_id=str(course_enrollment.course.id)
    ).exists():
        return

    service_user = User.objects.get(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME)

    # TODO: Replace ecommerce_api_client with get_ecommerce_api_client after completing ENT-6112
    # https://2u-internal.atlassian.net/browse/ENT-6112
    client = ecommerce_api_client(service_user)
    order_number = course_enrollment.get_order_attribute_value('order_number')
    if order_number:
        error_message = "Encountered {} from ecommerce while creating refund voucher. Order={}, enrollment={}, user={}"
        try:
            client.enterprise.coupons.create_refunded_voucher.post({"order": order_number})
        except HttpClientError as ex:
            log.info(
                error_message.format(type(ex).__name__, order_number, course_enrollment, course_enrollment.user)
            )
        except Exception as ex:  # pylint: disable=broad-except
            log.exception(
                error_message.format(type(ex).__name__, order_number, course_enrollment, course_enrollment.user)
            )
