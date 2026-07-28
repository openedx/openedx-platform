"""Utilities to assist with commerce tasks."""


import json
import logging
from urllib.parse import urlencode, urljoin

import requests
import waffle  # pylint: disable=invalid-django-waffle-import
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.translation import gettext as _
from edx_django_utils.plugins import pluggable_override

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.commerce.utils import (
    get_ecommerce_api_base_url,
    get_ecommerce_api_client,
    is_commerce_service_configured,
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming import helpers as theming_helpers

from .models import CommerceConfiguration

log = logging.getLogger(__name__)


def is_account_activation_requirement_disabled():
    """
    Checks to see if the django-waffle switch for disabling the account activation requirement is active

    Returns:
        Boolean value representing switch status
    """
    switch_name = configuration_helpers.get_value(
        'DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH',
        settings.DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH
    )
    return waffle.switch_is_active(switch_name)


class EcommerceService:
    """ Helper class for ecommerce service integration. """

    def __init__(self):
        self.config = CommerceConfiguration.current()

    @property
    def ecommerce_url_root(self):
        """ Retrieve Ecommerce service public url root. """
        return configuration_helpers.get_value('ECOMMERCE_PUBLIC_URL_ROOT', settings.ECOMMERCE_PUBLIC_URL_ROOT)

    @pluggable_override('OVERRIDE_GET_ABSOLUTE_ECOMMERCE_URL')
    def get_absolute_ecommerce_url(self, ecommerce_page_url):
        """ Return the absolute URL to the ecommerce page.

        Args:
            ecommerce_page_url (str): Relative path to the ecommerce page.

        Returns:
            Absolute path to the ecommerce page.
        """
        return urljoin(self.ecommerce_url_root, ecommerce_page_url)

    def get_order_dashboard_url(self):
        """ Return the URL to the ecommerce dashboard orders page.

        Returns:
            String: order dashboard url.
        """
        return self.get_absolute_ecommerce_url(CommerceConfiguration.DEFAULT_ORDER_DASHBOARD_URL)

    def get_receipt_page_url(self, order_number):
        """
        Gets the URL for the Order Receipt page hosted by the ecommerce service.

        Args:
            order_number (str): Order number.

        Returns:
            Receipt page for the specified Order.
        """

        return self.get_absolute_ecommerce_url(CommerceConfiguration.DEFAULT_RECEIPT_PAGE_URL + order_number)

    def is_enabled(self, user):
        """
        Determines the availability of the EcommerceService based on user activation and service configuration.
        Note: If the user is anonymous we bypass the user activation gate and only look at the service config.

        Returns:
            Boolean
        """
        user_is_active = user.is_active or is_account_activation_requirement_disabled()
        allow_user = user_is_active or user.is_anonymous
        return allow_user and self.config.checkout_on_ecommerce_service

    def payment_page_url(self):
        """ Return the URL for the checkout page.

        Example:
            http://localhost:8002/basket/add/
        """
        return self.get_absolute_ecommerce_url(self.config.basket_checkout_page)

    @pluggable_override('OVERRIDE_GET_CHECKOUT_PAGE_URL')
    def get_checkout_page_url(self, *skus, **kwargs):
        """ Construct the URL to the ecommerce checkout page and include products.

        Args:
            skus (list): List of SKUs associated with products to be added to basket
            program_uuid (string): The UUID of the program, if applicable
            course_run_keys (list): The course run keys of the products to be added to basket.

        Returns:
            Absolute path to the ecommerce checkout page showing basket that contains specified products.

        Example:
            http://localhost:8002/basket/add/?sku=5H3HG5&sku=57FHHD
            http://localhost:8002/basket/add/?sku=5H3HG5&sku=57FHHD&bundle=3bdf1dd1-49be-4a15-9145-38901f578c5a
        """
        program_uuid = kwargs.get('program_uuid')
        enterprise_catalog_uuid = kwargs.get('catalog')
        query_params = {'sku': skus}
        if enterprise_catalog_uuid:
            query_params.update({'catalog': enterprise_catalog_uuid})

        url = '{checkout_page_path}?{query_params}'.format(  # noqa: UP032
            checkout_page_path=self.get_absolute_ecommerce_url(self.config.basket_checkout_page),
            query_params=urlencode(query_params, doseq=True),
        )
        if program_uuid:
            url = '{url}&bundle={program_uuid}'.format(  # noqa: UP032
                url=url,
                program_uuid=program_uuid
            )
        return url

    def upgrade_url(self, user, course_key):
        """
        Returns the URL for the user to upgrade, or None if not applicable.
        """
        course_run_key = str(course_key)

        verified_mode = CourseMode.verified_mode_for_course(course_key)
        if verified_mode:
            if self.is_enabled(user):
                return self.get_checkout_page_url(verified_mode.sku, course_run_keys=[course_run_key])
            else:
                return reverse('dashboard')
        return None


@pluggable_override('OVERRIDE_REFUND_ENTITLEMENT')
def refund_entitlement(course_entitlement):
    """
    Attempt a refund of a course entitlement. Verify the User before calling this refund method

    Returns:
        bool: True if the Refund is successfully processed.
    """
    user_model = get_user_model()
    enrollee = course_entitlement.user
    entitlement_uuid = str(course_entitlement.uuid)

    if not is_commerce_service_configured():
        log.error(
            'Ecommerce service is not configured, cannot refund for user [%s], course entitlement [%s].',
            enrollee.id,
            entitlement_uuid
        )
        return False

    service_user = user_model.objects.get(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME)
    api_client = get_ecommerce_api_client(service_user)

    log.info(
        'Attempting to create a refund for user [%s], course entitlement [%s]...',
        enrollee.id,
        entitlement_uuid
    )

    try:
        refunds_url = urljoin(f"{get_ecommerce_api_base_url()}/", "refunds/")
        refunds_response = api_client.post(
            refunds_url,
            data={
                'order_number': course_entitlement.order_number,
                'username': enrollee.username,
                'entitlement_uuid': entitlement_uuid,
            }
        )
        refunds_response.raise_for_status()
        refund_ids = refunds_response.json()
    except Exception as exc:  # pylint: disable=broad-except
        # Catch any possible exceptions from the Ecommerce service to ensure we fail gracefully
        log.exception(
            "Unexpected exception while attempting to initiate refund for user [%s], "
            "course entitlement [%s] message: [%s]",
            enrollee.id,
            course_entitlement.uuid,
            str(exc)
        )
        return False

    if refund_ids:
        log.info(
            'Refund successfully opened for user [%s], course entitlement [%s]: %r',
            enrollee.id,
            entitlement_uuid,
            refund_ids,
        )

        return _process_refund(
            refund_ids=refund_ids,
            api_client=api_client,
            mode=course_entitlement.mode,
            user=enrollee,
            always_notify=True,
        )
    else:
        log.warning('No refund opened for user [%s], course entitlement [%s]', enrollee.id, entitlement_uuid)
        return False


@pluggable_override('OVERRIDE_REFUND_SEAT')
def refund_seat(course_enrollment, change_mode=False):
    """
    Attempt to initiate a refund for any orders associated with the seat being unenrolled,
    using the commerce service.

    Arguments:
        course_enrollment (CourseEnrollment): a student enrollment
        change_mode (Boolean): change the course mode to free mode or not

    Returns:
        A list of the external service's IDs for any refunds that were initiated
            (may be empty).

    Raises:
        requests.exceptions.RequestException: for any unhandled HTTP error during communication with
            the E-Commerce Service.
        requests.exceptions.Timeout: if the attempt to reach the commerce service timed out.
    """
    User = get_user_model()  # pylint:disable=invalid-name
    course_key_str = str(course_enrollment.course_id)
    enrollee = course_enrollment.user

    service_user = User.objects.get(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME)
    api_client = get_ecommerce_api_client(service_user)

    log.info('Attempting to create a refund for user [%s], course [%s]...', enrollee.id, course_key_str)

    refunds_url = urljoin(f"{get_ecommerce_api_base_url()}/", "refunds/")
    refunds_response = api_client.post(
        refunds_url,
        data={'course_id': course_key_str, 'username': enrollee.username}
    )
    refunds_response.raise_for_status()
    refund_ids = refunds_response.json() if refunds_response.content else None

    if refund_ids:
        log.info('Refund successfully opened for user [%s], course [%s]: %r', enrollee.id, course_key_str, refund_ids)

        _process_refund(
            refund_ids=refund_ids,
            api_client=api_client,
            mode=course_enrollment.mode,
            user=enrollee,
        )
        if change_mode:
            auto_enroll(course_enrollment)
    else:
        log.info('No refund opened for user [%s], course [%s]', enrollee.id, course_key_str)

    return refund_ids


@pluggable_override('OVERRIDE_GET_PROGRAM_PRICE_INFO')
def get_program_price_info(api_user, params):
    """
    Get the program price info from the ecommerce service.

    Args:
        api_user: The user to use to make the request.
        params: The params to use to make the request.

    Returns:
       JSON: {
                'total_incl_tax_excl_discounts': basket.total_incl_tax_excl_discounts,
                'total_incl_tax': basket.total_incl_tax,
                'currency': basket.currency
            }
    """
    if not api_user.is_authenticated:
        api_user = get_user_model().objects.get(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME)

    api_client = get_ecommerce_api_client(api_user)
    api_url = urljoin(f"{get_ecommerce_api_base_url()}/", "baskets/calculate/")

    response = api_client.get(api_url, params=params)
    return response


def auto_enroll(course_enrollment):
    """
    Helper method to update an enrollment to a default course mode.

    Arguments:
        course_enrollment (CourseEnrollment): The course_enrollment to update.

    Returns:
        bool: True if auto-enroll is successful. False if auto-enroll is not applicable.
    """
    enrollment_course_id = course_enrollment.course_id

    if CourseMode.can_auto_enroll(course_id=enrollment_course_id):
        auto_enroll_mode = CourseMode.auto_enroll_mode(course_id=enrollment_course_id)
        course_enrollment.update_enrollment(mode=auto_enroll_mode, is_active=False, skip_refund=True)
        course_enrollment.save()
        log.info(
            'Auto-enrolled user [%s], course [%s] in mode [%s].',
            course_enrollment.user_id,
            enrollment_course_id,
            auto_enroll_mode
        )
        return True
    else:
        return False


def _process_refund(refund_ids, api_client, mode, user, always_notify=False):
    """
    Helper method to process a refund for a given course_product. This method assumes that the User has already
    been unenrolled.

    Arguments:
        refund_ids: List of refund ids to be processed
        api_client: The API Client used in the processing of refunds
        mode: The mode that the refund should be processed for
        user: The user that the refund is being processed for
        always_notify (bool): This will enable always notifying support with Zendesk tickets when
            an approval is required

    Returns:
        bool: True if the refund process was successful, False if there are any Errors that are not handled
    """
    config = CommerceConfiguration.current()

    if config.enable_automatic_refund_approval:
        refunds_requiring_approval = []

        for refund_id in refund_ids:
            try:
                # NOTE: The following assumes that the user has already been unenrolled.
                # We are then able to approve payment. Additionally, this ensures we don't tie up an
                # additional web worker when the E-Commerce Service tries to unenroll the learner.
                base_url = get_ecommerce_api_base_url()
                api_url = urljoin(f"{base_url}/", f"refunds/{refund_id}/process/")
                response = api_client.put(api_url, json={'action': 'approve_payment_only'})
                response.raise_for_status()
                log.info('Refund [%d] successfully approved.', refund_id)
            except:  # pylint: disable=bare-except
                # Push the refund to Support to process
                log.exception('Failed to automatically approve refund [%d]!', refund_id)
                refunds_requiring_approval.append(refund_id)
    else:
        refunds_requiring_approval = refund_ids

    if refunds_requiring_approval:
        # XCOM-371: this is a temporary measure to suppress refund-related email
        # notifications to students and support for free enrollments.  This
        # condition should be removed when the CourseEnrollment.refundable() logic
        # is updated to be more correct, or when we implement better handling (and
        # notifications) in Otto for handling reversal of $0 transactions.
        if mode != 'verified' and not always_notify:
            # 'verified' is the only enrollment mode that should presently
            # result in opening a refund request.
            log.info(
                'Skipping refund support notification for non-verified mode for user [%s], mode: [%s]',
                user.id,
                mode,
            )
        else:
            try:
                return _send_refund_notification(user, refunds_requiring_approval)
            except:  # pylint: disable=bare-except
                # Unable to send notification to Support, do not break as this method is used by Signals
                log.warning('Could not send support notification for refund.', exc_info=True)
                return False
    return True


def _send_refund_notification(user, refund_ids):
    """
    Notify the support team of the refund request.

    Returns:
        bool: True if we are able to send the notification.  In this case that means we were able to create
              a ZenDesk ticket
    """

    tags = ['auto_refund']

    if theming_helpers.is_request_in_themed_site():
        # this is not presently supported with the external service.
        raise NotImplementedError("Unable to send refund processing emails to support teams.")

    # Build the information for the ZenDesk ticket
    student = user
    subject = _("[Refund] User-Requested Refund")
    body = _generate_refund_notification_body(student, refund_ids)
    requester_name = student.profile.name or student.username

    return create_zendesk_ticket(requester_name, student.email, subject, body, tags)


def _generate_refund_notification_body(student, refund_ids):
    """ Returns a refund notification message body. """
    msg = _(
        'A refund request has been initiated for {username} ({email}). '
        'To process this request, please visit the link(s) below.'
    ).format(username=student.username, email=student.email)

    ecommerce_url_root = configuration_helpers.get_value(
        'ECOMMERCE_PUBLIC_URL_ROOT', settings.ECOMMERCE_PUBLIC_URL_ROOT,
    )
    refund_urls = [urljoin(ecommerce_url_root, f'/dashboard/refunds/{refund_id}/')
                   for refund_id in refund_ids]

    # emails contained in this message could contain unicode characters so encode as such
    return '{msg}\n\n{urls}'.format(msg=msg, urls='\n'.join(refund_urls))


def create_zendesk_ticket(requester_name, requester_email, subject, body, tags=None):
    """
    Create a Zendesk ticket via API.

    Returns:
        bool: False if we are unable to create the ticket for any reason
    """
    if not (settings.ZENDESK_URL and settings.ZENDESK_USER and settings.ZENDESK_API_KEY):
        log.error('Zendesk is not configured. Cannot create a ticket.')
        return False

    # Copy the tags to avoid modifying the original list.
    tags = set(tags or [])
    tags.add('LMS')
    tags = list(tags)

    data = {
        'ticket': {
            'requester': {
                'name': requester_name,
                'email': str(requester_email)
            },
            'subject': subject,
            'comment': {'body': body},
            'tags': tags
        }
    }

    # Encode the data to create a JSON payload
    payload = json.dumps(data)

    # Set the request parameters
    url = urljoin(settings.ZENDESK_URL, '/api/v2/tickets.json')
    user = f'{settings.ZENDESK_USER}/token'
    pwd = settings.ZENDESK_API_KEY
    headers = {'content-type': 'application/json'}

    try:
        response = requests.post(url, data=payload, auth=(user, pwd), headers=headers)

        # Check for HTTP codes other than 201 (Created)
        if response.status_code != 201:
            log.error('Failed to create ticket. Status: [%d], Body: [%s]', response.status_code, response.content)
            return False
        else:
            log.debug('Successfully created ticket.')
    except Exception:  # pylint: disable=broad-except
        log.exception('Failed to create ticket.')
        return False
    return True
