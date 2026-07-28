"""
APIs providing support for enterprise functionality.
"""

import logging
import traceback
from urllib.parse import urljoin

import requests
from crum import get_current_request
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from edx_django_utils.cache import get_cache_key
from edx_rest_api_client.auth import SuppliedJwtAuth
from requests.exceptions import HTTPError

from common.djangoapps.third_party_auth.pipeline import get as get_partial_pipeline
from common.djangoapps.third_party_auth.provider import Registry
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangolib.markup import HTML, Text

try:
    from consent.models import DataSharingConsent, DataSharingConsentTextOverrides
    from enterprise.api.v1.serializers import (
        EnterpriseCustomerUserReadOnlySerializer,
        EnterpriseCustomerUserWriteSerializer,
    )
    from enterprise.models import (
        EnterpriseCourseEnrollment,
        EnterpriseCustomer,
        EnterpriseCustomerUser,
    )
except ImportError:  # pragma: no cover
    pass


CONSENT_FAILED_PARAMETER = 'consent_failed'
LOGGER = logging.getLogger("edx.enterprise_helpers")
ENTERPRISE_CUSTOMER_KEY_NAME = 'enterprise_customer'

# See https://open-edx-proposals.readthedocs.io/en/latest/oep-0022-bp-django-caches.html#common-caching-defect-and-fix
_CACHE_MISS = '__CACHE_MISS__'


class EnterpriseApiException(Exception):
    """
    Exception for errors while communicating with the Enterprise service API.
    """


class ConsentApiClient:
    """
    Class for producing an Enterprise Consent service API client
    """

    def __init__(self, user):
        """
        Initialize an authenticated Consent service API client by using the
        provided user.
        """
        jwt = create_jwt_for_user(user)
        base_api_url = configuration_helpers.get_value(
            'ENTERPRISE_CONSENT_API_URL', settings.ENTERPRISE_CONSENT_API_URL
        )
        self.client = requests.Session()
        self.client.auth = SuppliedJwtAuth(jwt)
        self.consent_endpoint = urljoin(f"{base_api_url}/", "data_sharing_consent")

    def revoke_consent(self, **kwargs):
        """
        Revoke consent from any existing records that have it at the given scope.

        This endpoint takes any given kwargs, which are understood as filtering the
        conceptual scope of the consent involved in the request.
        """
        response = self.client.delete(self.consent_endpoint, json=kwargs)
        response.raise_for_status()
        return response.json()

    def provide_consent(self, **kwargs):
        """
        Provide consent at the given scope.

        This endpoint takes any given kwargs, which are understood as filtering the
        conceptual scope of the consent involved in the request.
        """
        response = self.client.post(self.consent_endpoint, json=kwargs)
        response.raise_for_status()
        return response.json()

    def consent_required(self, enrollment_exists=False, **kwargs):
        """
        Determine if consent is required at the given scope.

        This endpoint takes any given kwargs, which are understood as filtering the
        conceptual scope of the consent involved in the request.
        """

        # Call the endpoint with the given kwargs, and check the value that it provides.
        response = self.client.get(self.consent_endpoint, params=kwargs)
        response.raise_for_status()
        response = response.json()

        LOGGER.info(
            '[ENTERPRISE DSC] Consent Requirement Info. APIParams: [%s], APIResponse: [%s], EnrollmentExists: [%s]',
            kwargs,
            response,
            enrollment_exists,
        )

        # No Enterprise record exists, but we're already enrolled in a course. So, go ahead and proceed.
        if enrollment_exists and not response.get('exists', False):
            return False

        # In all other cases, just trust the Consent API.
        return response['consent_required']


class EnterpriseServiceClientMixin:
    """
    Class for initializing an Enterprise API clients with service user.
    """

    def __init__(self):
        """
        Initialize an authenticated Enterprise API client by using the
        Enterprise worker user by default.
        """
        user = User.objects.get(username=settings.ENTERPRISE_SERVICE_WORKER_USERNAME)
        super().__init__(user)


class ConsentApiServiceClient(EnterpriseServiceClientMixin, ConsentApiClient):
    """
    Class for producing an Enterprise Consent API client with service user.
    """


class EnterpriseApiClient:
    """
    Class for producing an Enterprise service API client.
    """

    def __init__(self, user):
        """
        Initialize an authenticated Enterprise service API client.

        Authentificate by jwt token using the provided user.
        """
        self.user = user
        jwt = create_jwt_for_user(user)
        self.base_api_url = configuration_helpers.get_value('ENTERPRISE_API_URL', settings.ENTERPRISE_API_URL)
        self.client = requests.Session()
        self.client.auth = SuppliedJwtAuth(jwt)

    def get_enterprise_customer(self, uuid):
        api_url = urljoin(f"{self.base_api_url}/", f"enterprise-customer/{uuid}/")
        response = self.client.get(api_url)
        response.raise_for_status()
        return response.json()

    def post_enterprise_course_enrollment(self, username, course_id):
        """
        Create an EnterpriseCourseEnrollment by using the corresponding serializer (for validation).
        """
        data = {
            'username': username,
            'course_id': course_id,
        }
        api_url = urljoin(f"{self.base_api_url}/", "enterprise-course-enrollment/")
        try:
            response = self.client.post(api_url, data=data)
            response.raise_for_status()
        except HTTPError:
            message = (  # noqa: UP032
                "An error occured while posting EnterpriseCourseEnrollment for user {username} and "
                "course run {course_id}."
            ).format(
                username=username,
                course_id=course_id,
            )
            LOGGER.exception(message)
            raise EnterpriseApiException(message)  # pylint: disable=raise-missing-from  # noqa: B904

    def fetch_enterprise_learner_data(self, user):
        """
        Fetch information related to enterprise from the Enterprise Service.

        Example:
            fetch_enterprise_learner_data(user)

        Argument:
            user: (User) django auth user

        Returns:
            dict:
            {
                "count": 1,
                "num_pages": 1,
                "current_page": 1,
                "next": null,
                "start": 0,
                "previous": null
                "results": [
                    {
                        "enterprise_customer": {
                            "uuid": "cf246b88-d5f6-4908-a522-fc307e0b0c59",
                            "name": "TestShib",
                            "active": true,
                            "site": {
                                "domain": "example.com",
                                "name": "example.com"
                            },
                            "enable_data_sharing_consent": true,
                            "enforce_data_sharing_consent": "at_login",
                            "branding_configuration": {
                                "enterprise_customer": "cf246b88-d5f6-4908-a522-fc307e0b0c59",
                                "logo": "https://open.edx.org/sites/all/themes/edx_open/logo.png"
                            },
                            "enterprise_customer_entitlements": [
                                {
                                    "enterprise_customer": "cf246b88-d5f6-4908-a522-fc307e0b0c59",
                                    "entitlement_id": 69
                                }
                            ],
                            "replace_sensitive_sso_username": False,
                        },
                        "user_id": 5,
                        "user": {
                            "username": "staff",
                            "first_name": "",
                            "last_name": "",
                            "email": "staff@example.com",
                            "is_staff": true,
                            "is_active": true,
                            "date_joined": "2016-09-01T19:18:26.026495Z"
                        },
                        "data_sharing_consent_records": [
                            {
                                "username": "staff",
                                "enterprise_customer_uuid": "cf246b88-d5f6-4908-a522-fc307e0b0c59",
                                "exists": true,
                                "course_id": "course-v1:edX DemoX Demo_Course",
                                "consent_provided": true,
                                "consent_required": false
                            }
                        ]
                    }
                ],
            }
        """
        if not user.is_authenticated:
            return None

        api_url = urljoin(f"{self.base_api_url}/", "enterprise-learner/")

        try:
            querystring = {'username': user.username}
            response = self.client.get(api_url, params=querystring)
            response.raise_for_status()
        except HTTPError:
            LOGGER.exception(
                'Failed to get enterprise-learner for user [%s] with client user [%s]. Caller: %s, Request PATH: %s',
                user.username,
                self.user.username,
                "".join(traceback.format_stack()),
                get_current_request().META['PATH_INFO'],
            )
            return None

        return response.json()


class EnterpriseApiServiceClient(EnterpriseServiceClientMixin, EnterpriseApiClient):
    """
    Class for producing an Enterprise service API client with service user.
    """

    def get_enterprise_customer(self, uuid):
        """
        Fetch enterprise customer with enterprise service user and cache the
        API response`.
        """
        enterprise_customer = enterprise_customer_from_cache(uuid=uuid)
        if enterprise_customer is _CACHE_MISS:
            api_url = urljoin(f"{self.base_api_url}/", f"enterprise-customer/{uuid}/")
            response = self.client.get(api_url)
            response.raise_for_status()
            enterprise_customer = response.json() if response.content else None
            if enterprise_customer:
                cache_enterprise(enterprise_customer)

        return enterprise_customer


def activate_learner_enterprise(request, user, enterprise_customer):
    """
    Allow an enterprise learner to activate one of learner's linked enterprises.
    """
    serializer = EnterpriseCustomerUserWriteSerializer(data={
        'enterprise_customer': enterprise_customer,
        'username': user.username,
        'active': True
    })
    if serializer.is_valid():
        serializer.save()
        enterprise_customer_user = EnterpriseCustomerUser.objects.get(
            user_id=user.id,
            enterprise_customer=enterprise_customer
        )
        enterprise_customer_user.update_session(request)
        LOGGER.info(
            '[Enterprise Selection Page] Learner activated an enterprise. User: %s, EnterpriseCustomer: %s',
            user.username,
            enterprise_customer,
        )
        return True

    return False


def enterprise_enabled():
    """
    Determines whether the Enterprise app is installed
    """
    return django_apps.is_installed('enterprise') and settings.FEATURES.get('ENABLE_ENTERPRISE_INTEGRATION', False)


def enterprise_is_enabled(otherwise=None):
    """Decorator which requires that the Enterprise feature be enabled before the function can run."""
    def decorator(func):
        """Decorator for ensuring the Enterprise feature is enabled."""
        def wrapper(*args, **kwargs):
            if enterprise_enabled():
                return func(*args, **kwargs)
            return otherwise
        return wrapper
    return decorator


def get_enterprise_customer_cache_key(uuid, username=settings.ENTERPRISE_SERVICE_WORKER_USERNAME):
    """The cache key used to get cached Enterprise Customer data."""
    return get_cache_key(
        resource='enterprise-customer',
        resource_id=uuid,
        username=username,
    )


def cache_enterprise(enterprise_customer):
    """Add this customer's data to the Django cache."""
    cache_key = get_enterprise_customer_cache_key(enterprise_customer['uuid'])
    cache.set(cache_key, enterprise_customer, settings.ENTERPRISE_API_CACHE_TIMEOUT)


def enterprise_customer_from_cache(uuid):
    """
    Retrieve enterprise customer data associated with the given ``uuid`` from the Django cache,
    returning a ``__CACHE_MISS__`` if absent.
    """
    cache_key = get_enterprise_customer_cache_key(uuid)
    return cache.get(cache_key, _CACHE_MISS)


def add_enterprise_customer_to_session(request, enterprise_customer):
    """ Add the given enterprise_customer data to the request's session if user is authenticated. """
    if request.user.is_authenticated:
        request.session[ENTERPRISE_CUSTOMER_KEY_NAME] = enterprise_customer


def enterprise_customer_from_session(request):
    """
    Retrieve enterprise_customer data from the request's session,
    returning a ``__CACHE_MISS__`` if absent.

    Now checks for session existence before attempting to access it.
    """
    if not request or not hasattr(request, 'session'):
        return _CACHE_MISS
    else:
        return request.session.get(ENTERPRISE_CUSTOMER_KEY_NAME, _CACHE_MISS)


def enterprise_customer_uuid_from_session(request):
    """
    Retrieve an enterprise customer UUID from the request's session,
    returning a ``__CACHE_MISS__`` if absent.  Note that this may
    return ``None``, which indicates that we've previously looked
    for an associated customer for this request's user, and
    none was present.
    """
    customer_data = enterprise_customer_from_session(request)
    if customer_data is not _CACHE_MISS:
        customer_data = customer_data or {}
        return customer_data.get('uuid')
    return _CACHE_MISS


def enterprise_customer_uuid_from_query_param(request):
    """
    Returns an enterprise customer UUID from the given request's GET data,
    or ``__CACHE_MISS__`` if not present.
    """
    return request.GET.get(ENTERPRISE_CUSTOMER_KEY_NAME, _CACHE_MISS)


def enterprise_customer_uuid_from_cookie(request):
    """
    Returns an enterprise customer UUID from the given request's cookies,
    or ``__CACHE_MISS__`` if not present.
    """
    return request.COOKIES.get(settings.ENTERPRISE_CUSTOMER_COOKIE_NAME, _CACHE_MISS)


@enterprise_is_enabled()
def enterprise_customer_from_api(request):
    """Use an API to get Enterprise Customer data from request context clues."""
    enterprise_customer = None
    enterprise_customer_uuid = enterprise_customer_uuid_for_request(request)
    if enterprise_customer_uuid is _CACHE_MISS:
        # enterprise_customer_uuid_for_request() `shouldn't` return a __CACHE_MISS__,
        # but just in case it does, we check for it and return early if found.
        return enterprise_customer

    if enterprise_customer_uuid:
        # If we were able to obtain an EnterpriseCustomer UUID, go ahead
        # and use it to attempt to retrieve EnterpriseCustomer details
        # from the EnterpriseCustomer API.
        enterprise_api_client = (
            EnterpriseApiClient(user=request.user)
            if request.user.is_authenticated
            else EnterpriseApiServiceClient()
        )

        try:
            enterprise_customer = enterprise_api_client.get_enterprise_customer(enterprise_customer_uuid)
        except HTTPError as err:
            if err.response.status_code == 404:
                enterprise_customer = None
            else:
                raise
    return enterprise_customer


@enterprise_is_enabled()
def enterprise_customer_uuid_for_request(request):
    """
    Check all the context clues of the request to gather a particular EnterpriseCustomer's UUID.
    """
    sso_provider_id = request.GET.get('tpa_hint')
    running_pipeline = get_partial_pipeline(request)
    if running_pipeline:
        # Determine if the user is in the middle of a third-party auth pipeline,
        # and set the sso_provider_id parameter to match if so.
        sso_provider_id = Registry.get_from_pipeline(running_pipeline).provider_id

    if sso_provider_id:
        # If we have a third-party auth provider, get the linked enterprise customer.
        try:
            # FIXME: Implement an Enterprise API endpoint where we can get the EC
            # directly via the linked SSO provider
            # Check if there's an Enterprise Customer such that the linked SSO provider
            # has an ID equal to the ID we got from the running pipeline or from the
            # request tpa_hint URL parameter.
            enterprise_customer_uuid = EnterpriseCustomer.objects.get(
                enterprise_customer_identity_providers__provider_id=sso_provider_id
            ).uuid
        except EnterpriseCustomer.DoesNotExist:
            LOGGER.info(
                '[ENTERPRISE DSC] Customer not found using SSO Provider ID. User: [%s], SSOProviderID: [%s]',
                request.user.username,
                sso_provider_id
            )
            enterprise_customer_uuid = None
    else:
        enterprise_customer_uuid = _customer_uuid_from_query_param_cookies_or_session(request)

    if enterprise_customer_uuid is _CACHE_MISS or enterprise_customer_uuid is None:
        if not request.user.is_authenticated:
            return None

        # If there's no way to get an Enterprise UUID for the request, check to see
        # if there's already an Enterprise attached to the requesting user on the backend.
        enterprise_customer = None
        learner_data = get_enterprise_learner_data_from_db(request.user)
        if learner_data:
            enterprise_customer = learner_data[0]['enterprise_customer']
            enterprise_customer_uuid = enterprise_customer['uuid']
            cache_enterprise(enterprise_customer)
        else:
            enterprise_customer_uuid = None

        # Now that we've asked the database for this users's enterprise customer data,
        # add it to their session (even if it's null/empty, which indicates the user
        # has no associated enterprise customer).
        LOGGER.info(
            '[ENTERPRISE DSC] Updating Session. User: [%s], UserAuthenticated: [%s], EnterpriseCustomer: [%s]',
            request.user.username,
            request.user.is_authenticated,
            enterprise_customer
        )
        add_enterprise_customer_to_session(request, enterprise_customer)

    return enterprise_customer_uuid


def _customer_uuid_from_query_param_cookies_or_session(request):
    """
    Helper function that plucks a customer UUID out of the given requests's
    query params, cookie, or session data.
    Returns ``__CACHE_MISS__`` if none of those keys are present in the request.
    """
    for function in (
        enterprise_customer_uuid_from_query_param,
        enterprise_customer_uuid_from_cookie,
        enterprise_customer_uuid_from_session,
    ):
        enterprise_customer_uuid = function(request)
        if enterprise_customer_uuid is not _CACHE_MISS:
            LOGGER.info(
                '[ENTERPRISE DSC] Customer Info. User: [%s], Function: [%s], UUID: [%s]',
                request.user.username,
                function,
                enterprise_customer_uuid
            )
            return enterprise_customer_uuid

    return _CACHE_MISS


@enterprise_is_enabled()
def enterprise_customer_for_request(request):
    """
    Check all the context clues of the request to determine if
    the request being made is tied to a particular EnterpriseCustomer.
    """
    enterprise_customer = enterprise_customer_from_session(request)
    if enterprise_customer is _CACHE_MISS:
        enterprise_customer = enterprise_customer_from_api(request)
        LOGGER.info(
            '[ENTERPRISE DSC] Updating Session. User: [%s], UserAuthenticated: [%s], EnterpriseCustomer: [%s]',
            request.user.username,
            request.user.is_authenticated,
            enterprise_customer
        )
        add_enterprise_customer_to_session(request, enterprise_customer)
    return enterprise_customer


@enterprise_is_enabled()
def get_enterprise_learner_data_from_api(user):
    """
    Client API operation adapter/wrapper
    """
    if user.is_authenticated:
        enterprise_learner_data = EnterpriseApiClient(user=user).fetch_enterprise_learner_data(user)
        if enterprise_learner_data:
            return enterprise_learner_data['results']


@enterprise_is_enabled()
def get_enterprise_learner_data_from_db(user):
    """
    Query the database directly and use the same serializer that the api call would use to return the same results.
    """
    if user.is_authenticated:
        queryset = EnterpriseCustomerUser.objects.filter(user_id=user.id)
        serializer = EnterpriseCustomerUserReadOnlySerializer(queryset, many=True)
        return serializer.data


@enterprise_is_enabled(otherwise=[])
def get_data_sharing_consents(user):
    """
    Returns a list of data sharing consent records for the given user.
    """

    return DataSharingConsent.objects.filter(
        username=user.username
    )


@enterprise_is_enabled(otherwise=[])
def get_enterprise_course_enrollments(user):
    """
    Returns a list of enterprise course enrollments for the given user.
    """

    return EnterpriseCourseEnrollment.objects.select_related(
        'licensedenterprisecourseenrollment_enrollment_fulfillment',
        'enterprise_customer_user'
    ).prefetch_related(
        'enterprise_customer_user__enterprise_customer'
    ).filter(
        enterprise_customer_user__user_id=user.id
    )


@enterprise_is_enabled()
def enterprise_customer_from_session_or_learner_data(request):
    """
    Returns an Enterprise Customer for the authenticated user.

    Retrieves customer from session by default. If _CACHE_MISS, retrieve customer using
    learner data from the DB and add customer data to the session.

    Args:
        request: request made to the LMS dashboard
    """
    enterprise_customer = enterprise_customer_from_session(request)
    if enterprise_customer is _CACHE_MISS:
        learner_data = get_enterprise_learner_data_from_db(request.user)
        enterprise_customer = learner_data[0]['enterprise_customer'] if learner_data else None
        # Add to session cache regardless of whether it is null
        LOGGER.info(
            '[ENTERPRISE DSC] Updating Session. User: [%s], UserAuthenticated: [%s], EnterpriseCustomer: [%s]',
            request.user.username,
            request.user.is_authenticated,
            enterprise_customer
        )
        add_enterprise_customer_to_session(request, enterprise_customer)
        if enterprise_customer:
            cache_enterprise(enterprise_customer)
    return enterprise_customer


@enterprise_is_enabled()
def get_enterprise_learner_portal_enabled_message(enterprise_customer):
    """
    Returns message to be displayed in dashboard if the user is linked to an Enterprise with the Learner Portal enabled.
    Note: request.session[ENTERPRISE_CUSTOMER_KEY_NAME] will be used in case the user is linked to
        multiple Enterprises. Otherwise, it won't exist and the Enterprise Learner data
        will be used. If that doesn't exist return None.
    Args:
        enterprise_customer: EnterpriseCustomer object
    """
    if not enterprise_customer:
        return None

    if not enterprise_customer.get('enable_learner_portal', False):
        return None

    learner_portal_url = "{base_url}/{slug}?utm_source=lms_dashboard_banner".format(
        base_url=settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL,
        slug=enterprise_customer['slug']
    )

    return Text(_(
        "You have access to the {bold_start}{enterprise_name}{bold_end} dashboard. "
        "To access the courses available to you through {enterprise_name}, "
        "{link_start}visit the {enterprise_name} dashboard{link_end}."
    )).format(
        enterprise_name=enterprise_customer['name'],
        bold_start=HTML("<b>"),
        bold_end=HTML("</b>"),
        link_start=HTML(f"<a href='{learner_portal_url}'>"),
        link_end=HTML("</a>"),
    )


@enterprise_is_enabled(otherwise={})
def get_enterprise_learner_portal_context(request):
    """
    Determines a selected enterprise customer from session or learner data from the DB.

    Arguments:
        request: A request object.

    Returns:
        dict: A dictionary representing the necessary metadata and messaging about an Enterprise Learner Portal,
            used in the dashboard.html template.
    """
    context = {}
    enterprise_customer = enterprise_customer_from_session_or_learner_data(request)
    if not enterprise_customer:
        return context

    enterprise_learner_portal_enabled_message = get_enterprise_learner_portal_enabled_message(enterprise_customer)
    context.update({
        'enterprise_customer_name': enterprise_customer.get('name'),
        'enterprise_customer_slug': enterprise_customer.get('slug'),
        'enterprise_customer_learner_portal_enabled': enterprise_customer.get('enable_learner_portal', False),
        'enterprise_customer_uuid': enterprise_customer.get('uuid'),
        'enterprise_learner_portal_base_url': settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL,
        'enterprise_learner_portal_enabled_message': enterprise_learner_portal_enabled_message,
    })
    return context


@enterprise_is_enabled()
def get_consent_notification_data(enterprise_customer):
    """
    Returns the consent notification data from DataSharingConsentPage modal
    """
    title_template = None
    message_template = None
    try:
        consent_page = DataSharingConsentTextOverrides.objects.get(enterprise_customer_id=enterprise_customer['uuid'])
        title_template = consent_page.declined_notification_title
        message_template = consent_page.declined_notification_message
    except DataSharingConsentTextOverrides.DoesNotExist:
        LOGGER.info(
            "DataSharingConsentPage object doesn't exit for {enterprise_customer_name}".format(
                enterprise_customer_name=enterprise_customer['name']
            )
        )
    return title_template, message_template


@enterprise_is_enabled(otherwise='')
def get_dashboard_consent_notification(request, user, course_enrollments):
    """
    If relevant to the request at hand, create a banner on the dashboard indicating consent failed.

    Args:
        request: The WSGIRequest object produced by the user browsing to the Dashboard page.
        user: The logged-in user
        course_enrollments: A list of the courses to be rendered on the Dashboard page.

    Returns:
        str: Either an empty string, or a string containing the HTML code for the notification banner.
    """
    enrollment = None
    consent_needed = False
    course_id = request.GET.get(CONSENT_FAILED_PARAMETER)

    if course_id:

        enterprise_customer = enterprise_customer_for_request(request)
        if not enterprise_customer:
            return ''

        for course_enrollment in course_enrollments:
            if str(course_enrollment.course_id) == course_id:
                enrollment = course_enrollment
                break

        client = ConsentApiClient(user=request.user)
        consent_needed = client.consent_required(
            enterprise_customer_uuid=enterprise_customer['uuid'],
            username=user.username,
            course_id=course_id,
        )

    if consent_needed and enrollment:

        title_template, message_template = get_consent_notification_data(enterprise_customer)
        if not title_template:
            title_template = _(
                'Enrollment in {course_title} was not complete.'
            )
        if not message_template:
            message_template = _(
                'If you have concerns about sharing your data, please contact your administrator '
                'at {enterprise_customer_name}.'
            )

        title = title_template.format(
            course_title=enrollment.course_overview.display_name,
        )
        message = message_template.format(
            enterprise_customer_name=enterprise_customer['name'],
        )

        return render_to_string(
            'enterprise_support/enterprise_consent_declined_notification.html',
            {
                'title': title,
                'message': message,
                'course_name': enrollment.course_overview.display_name,
            }
        )
    return ''
