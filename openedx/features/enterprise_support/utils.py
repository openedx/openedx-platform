"""
Utility methods for Enterprise
"""


import json

from completion.exceptions import UnavailableCompletionData
from completion.utilities import get_key_to_last_completed_block
from crum import get_current_request
from django.conf import settings
from django.core.cache import cache
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _
from edx_django_utils.cache import TieredCache, get_cache_key
from edx_toggles.toggles import WaffleFlag
from enterprise.api.v1.serializers import EnterpriseCustomerBrandingConfigurationSerializer
from enterprise.models import EnterpriseCustomer, EnterpriseCustomerUser
from social_django.models import UserSocialAuth

from common.djangoapps import third_party_auth
from common.djangoapps.student.helpers import get_next_url_for_login_page
from lms.djangoapps.branding.api import get_privacy_url
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_authn.cookies import standard_cookie_settings
from openedx.core.djangolib.markup import HTML, Text

ENTERPRISE_HEADER_LINKS = WaffleFlag('enterprise.enterprise_header_links', __name__)  # pylint: disable=toggle-missing-annotation


def get_data_consent_share_cache_key(user_id, course_id, enterprise_customer_uuid=None):
    """
        Returns cache key for data sharing consent needed against user_id, course_id and enterprise_customer_uuid
    """
    cache_key_params = dict(
        type='data_sharing_consent_needed',
        user_id=user_id,
        course_id=course_id,
    )

    if enterprise_customer_uuid:
        cache_key_params['enterprise_customer_uuid'] = enterprise_customer_uuid

    return get_cache_key(**cache_key_params)


def get_is_enterprise_cache_key(user_id):
    """
        Returns cache key for the enterprise learner validation method needed against user_id.
    """
    return get_cache_key(type='is_enterprise_learner', user_id=user_id)


def clear_data_consent_share_cache(user_id, course_id, enterprise_customer_uuid):
    """
        clears data_sharing_consent_needed cache
    """
    consent_cache_key = get_data_consent_share_cache_key(user_id, course_id, enterprise_customer_uuid)
    TieredCache.delete_all_tiers(consent_cache_key)


def update_logistration_context_for_enterprise(request, context, enterprise_customer):
    """
    Take the processed context produced by the view, determine if it's relevant
    to a particular Enterprise Customer, and update it to include that customer's
    enterprise metadata.

     Arguments:
         request (HttpRequest): The request for the logistration page.
         context (dict): Context for logistration page.
         enterprise_customer (dict): data for enterprise customer

    """
    sidebar_context = {}
    if enterprise_customer:
        is_proxy_login = request.GET.get('proxy_login')
        sidebar_context = get_enterprise_sidebar_context(enterprise_customer, is_proxy_login)

    if sidebar_context:
        context['data']['registration_form_desc']['fields'] = enterprise_fields_only(
            context['data']['registration_form_desc']
        )
        context.update(sidebar_context)
        context['enable_enterprise_sidebar'] = True
        context['data']['hide_auth_warnings'] = True
        context['data']['enterprise_name'] = enterprise_customer['name']
    else:
        context['enable_enterprise_sidebar'] = False

    update_third_party_auth_context_for_enterprise(request, context, enterprise_customer)


def get_enterprise_sidebar_context(enterprise_customer, is_proxy_login):
    """
    Get context information for enterprise sidebar for the given enterprise customer.

    Args:
        enterprise_customer (dict): customer data from enterprise-customer endpoint, cached
        is_proxy_login (bool): If True, use proxy login welcome template

    Returns: Enterprise Sidebar Context with the following key-value pairs.
    {
        'enterprise_name': 'Enterprise Name',
        'enterprise_logo_url': 'URL of the enterprise logo image',
        'enterprise_branded_welcome_string': 'Human readable welcome message customized for the enterprise',
        'platform_welcome_string': 'Human readable welcome message for an enterprise learner',
    }
    """
    platform_name = configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)

    branding_configuration = enterprise_customer.get('branding_configuration', {})
    logo_url = branding_configuration.get('logo', '') if isinstance(branding_configuration, dict) else ''

    if is_proxy_login:
        branded_welcome_template = configuration_helpers.get_value(
            'ENTERPRISE_PROXY_LOGIN_WELCOME_TEMPLATE',
            settings.ENTERPRISE_PROXY_LOGIN_WELCOME_TEMPLATE
        )
    else:
        branded_welcome_template = configuration_helpers.get_value(
            'ENTERPRISE_SPECIFIC_BRANDED_WELCOME_TEMPLATE',
            settings.ENTERPRISE_SPECIFIC_BRANDED_WELCOME_TEMPLATE
        )

    branded_welcome_string = Text(branded_welcome_template).format(
        start_bold=HTML('<b>'),
        end_bold=HTML('</b>'),
        line_break=HTML('<br/>'),
        enterprise_name=enterprise_customer['name'],
        platform_name=platform_name,
        privacy_policy_link_start=HTML("<a href='{pp_url}' rel='noopener' target='_blank'>").format(
            pp_url=get_privacy_url()
        ),
        privacy_policy_link_end=HTML("</a>"),
    )

    platform_welcome_template = configuration_helpers.get_value(
        'ENTERPRISE_PLATFORM_WELCOME_TEMPLATE',
        settings.ENTERPRISE_PLATFORM_WELCOME_TEMPLATE
    )
    platform_welcome_string = platform_welcome_template.format(platform_name=platform_name)

    return {
        'enterprise_name': enterprise_customer['name'],
        'enterprise_logo_url': logo_url,
        'enterprise_branded_welcome_string': branded_welcome_string,
        'platform_welcome_string': platform_welcome_string,
    }


def enterprise_fields_only(fields):
    """
    Take the received field definition, and exclude those fields that we don't want
    to require if the user is going to be a member of an Enterprise Customer.
    """
    enterprise_exclusions = configuration_helpers.get_value(
        'ENTERPRISE_EXCLUDED_REGISTRATION_FIELDS',
        settings.ENTERPRISE_EXCLUDED_REGISTRATION_FIELDS
    )
    return [field for field in fields['fields'] if field['name'] not in enterprise_exclusions]


def update_third_party_auth_context_for_enterprise(request, context, enterprise_customer=None):
    """
    Return updated context of third party auth with modified data for the given enterprise customer.

    Arguments:
        request (HttpRequest): The request for the logistration page.
        context (dict): Context for third party auth providers and auth pipeline.
        enterprise_customer (dict): data for enterprise customer

    Returns:
         context (dict): Updated context of third party auth with modified
         `errorMessage`.
    """
    if context['data']['third_party_auth']['errorMessage']:
        context['data']['third_party_auth']['errorMessage'] = Text(_(
            'We are sorry, you are not authorized to access {platform_name} via this channel. '
            'Please contact your learning administrator or manager in order to access {platform_name}.'
            '{line_break}{line_break}'
            'Error Details:{line_break}{error_message}')
        ).format(
            platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
            error_message=context['data']['third_party_auth']['errorMessage'],
            line_break=HTML('<br/>')
        )

    if enterprise_customer:
        context['data']['third_party_auth']['providers'] = []
        context['data']['third_party_auth']['secondaryProviders'] = []

    running_pipeline = third_party_auth.pipeline.get(request)
    if running_pipeline is not None:
        current_provider = third_party_auth.provider.Registry.get_from_pipeline(running_pipeline)
        if current_provider is not None and current_provider.skip_registration_form and enterprise_customer:
            # For enterprise (and later for everyone), we need to get explicit consent to the
            # Terms of service instead of auto submitting the registration form outright.
            context['data']['third_party_auth']['autoSubmitRegForm'] = False
            context['data']['third_party_auth']['autoRegisterWelcomeMessage'] = Text(_(
                'Thank you for joining {platform_name}. '
                'Just a couple steps before you start learning!')
            ).format(
                platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
            )
            context['data']['third_party_auth']['registerFormSubmitButtonText'] = _('Continue')

    return context


def handle_enterprise_cookies_for_logistration(request, response, context):
    """
    Helper method for setting or deleting enterprise cookies on logistration response.

    Arguments:
        request (HttpRequest): The request for the logistration page.
        response (HttpResponse): The response for the logistration page.
        context (dict): Context for logistration page.

    """
    # This cookie can be used for tests or minor features,
    # but should not be used for payment related or other critical work
    # since users can edit their cookies
    _set_experiments_is_enterprise_cookie(request, response, context['enable_enterprise_sidebar'])

    # Remove enterprise cookie so that subsequent requests show default login page.
    response.delete_cookie(
        configuration_helpers.get_value('ENTERPRISE_CUSTOMER_COOKIE_NAME', settings.ENTERPRISE_CUSTOMER_COOKIE_NAME),
        domain=configuration_helpers.get_value('BASE_COOKIE_DOMAIN', settings.BASE_COOKIE_DOMAIN),
    )


def _set_experiments_is_enterprise_cookie(request, response, experiments_is_enterprise):
    """ Sets the experiments_is_enterprise cookie on the response.
    This cookie can be used for tests or minor features,
    but should not be used for payment related or other critical work
    since users can edit their cookies
    """
    cookie_settings = standard_cookie_settings(request)

    response.set_cookie(
        'experiments_is_enterprise',
        json.dumps(experiments_is_enterprise),
        **cookie_settings
    )


def update_account_settings_context_for_enterprise(context, enterprise_customer, user):
    """
    Take processed context for account settings page and update it taking enterprise customer into account.

     Arguments:
        context (dict): Context for account settings page.
        enterprise_customer (dict): data for enterprise customer
        user (User): request user
    """
    enterprise_context = {
        'enterprise_name': enterprise_customer['name'] if enterprise_customer else None,
        'sync_learner_profile_data': _get_sync_learner_profile_data(enterprise_customer),
        'edx_support_url': configuration_helpers.get_value('SUPPORT_SITE_LINK', settings.SUPPORT_SITE_LINK),
        'enterprise_readonly_account_fields': {
            'fields': list(get_enterprise_readonly_account_fields(user))
        }
    }
    context.update(enterprise_context)


def get_enterprise_readonly_account_fields(user):
    """
    Returns a set of account fields that are read-only for enterprise users.
    """
    # TODO circular dependency between enterprise_support.api and enterprise_support.utils
    from openedx.features.enterprise_support.api import enterprise_customer_for_request
    enterprise_customer = enterprise_customer_for_request(get_current_request())

    enterprise_readonly_account_fields = list(settings.ENTERPRISE_READONLY_ACCOUNT_FIELDS)

    # if user has no `UserSocialAuth` record then allow to edit `fullname`
    # whether the `sync_learner_profile_data` is enabled or disabled
    user_social_auth_record = _user_has_social_auth_record(user, enterprise_customer)
    if not user_social_auth_record and 'name' in enterprise_readonly_account_fields:
        enterprise_readonly_account_fields.remove('name')

    sync_learner_profile_data = _get_sync_learner_profile_data(enterprise_customer)
    return set(enterprise_readonly_account_fields) if sync_learner_profile_data else set()


def _user_has_social_auth_record(user, enterprise_customer):
    """
    Return True if a `UserSocialAuth` record exists for `user` False otherwise.
    """
    provider_backend_names = []
    if enterprise_customer and enterprise_customer['identity_providers']:
        for idp in enterprise_customer['identity_providers']:
            identity_provider = third_party_auth.provider.Registry.get(
                provider_id=idp['provider_id']
            )
            if identity_provider and hasattr(identity_provider, 'backend_name'):
                provider_backend_names.append(identity_provider.backend_name)

        if provider_backend_names:
            return UserSocialAuth.objects.select_related('user').\
                filter(provider__in=provider_backend_names, user=user).exists()
    return False


def _get_sync_learner_profile_data(enterprise_customer):
    """
    Returns whether the configuration of the given enterprise customer supports
    synching learner profile data.
    """
    if enterprise_customer:
        identity_provider = third_party_auth.provider.Registry.get(
            provider_id=enterprise_customer['identity_provider'],
        )
        if identity_provider:
            return identity_provider.sync_learner_profile_data

    return False


def get_enterprise_learner_portal(request):
    """
    Gets the formatted portal name and slug that can be used
    to generate a link for an enabled enterprise Learner Portal.

    Caches and returns result in/from the user's request session if provided.
    """
    # Prevent a circular import.
    from openedx.features.enterprise_support.api import enterprise_customer_uuid_for_request, enterprise_enabled

    user = request.user
    # Only cache this if a learner is authenticated (AnonymousUser exists and should not be tracked)

    learner_portal_session_key = 'enterprise_learner_portal'

    if enterprise_enabled() and ENTERPRISE_HEADER_LINKS.is_enabled() and user and user.id:
        # If the key exists return that value
        if learner_portal_session_key in request.session:
            return json.loads(request.session[learner_portal_session_key])

        kwargs = {
            'user_id': user.id,
            'enterprise_customer__enable_learner_portal': True,
        }
        enterprise_customer_uuid = enterprise_customer_uuid_for_request(request)
        if enterprise_customer_uuid:
            kwargs['enterprise_customer__uuid'] = enterprise_customer_uuid

        queryset = EnterpriseCustomerUser.objects.filter(**kwargs).prefetch_related(
            'enterprise_customer',
            'enterprise_customer__branding_configuration',
        )

        if not enterprise_customer_uuid:
            # If the request doesn't help us know which Enterprise Customer UUID to select with,
            # order by the most recently activated/modified customers,
            # so that when we select the first result of the query as the preferred
            # customer, it's the most recently active one.
            queryset = queryset.order_by('-enterprise_customer__active', '-modified')

        preferred_enterprise_customer_user = queryset.first()
        if not preferred_enterprise_customer_user:
            return None

        enterprise_customer = preferred_enterprise_customer_user.enterprise_customer
        learner_portal_data = {
            'name': enterprise_customer.name,
            'slug': enterprise_customer.slug,
            'logo': enterprise_branding_configuration(enterprise_customer).get('logo'),
        }

        # Cache the result in the user's request session
        request.session[learner_portal_session_key] = json.dumps(learner_portal_data)
        return learner_portal_data
    return None


def enterprise_branding_configuration(enterprise_customer_obj):
    """
    Given an instance of ``EnterpriseCustomer``, returns a related
    branding_configuration serialized dictionary if it exists, otherwise
    the serialized default EnterpriseCustomerBrandingConfiguration object.

    EnterpriseCustomerBrandingConfigurationSerializer will use default values
    for any empty branding config fields.
    """
    branding_config = enterprise_customer_obj.safe_branding_configuration
    return EnterpriseCustomerBrandingConfigurationSerializer(branding_config).data


def get_enterprise_learner_generic_name(request):
    """
    Get a generic name concatenating the Enterprise Customer name and 'Learner'.

    ENT-924: Temporary solution for hiding potentially sensitive SSO names.
    When a more complete solution is put in place, delete this function and all of its uses.
    """
    # Prevent a circular import. This function makes sense to be in this module though. And see function description.
    from openedx.features.enterprise_support.api import enterprise_customer_for_request

    # ENT-2626: For 404 pages we don't need to perform these actions.
    if getattr(request, 'view_name', None) == '404':
        return

    enterprise_customer = enterprise_customer_for_request(request)

    return (
        enterprise_customer['name'] + 'Learner'
        if enterprise_customer and enterprise_customer['replace_sensitive_sso_username']
        else ''
    )


def is_enterprise_learner(user):
    """
    Check if the given user belongs to an enterprise. Cache the value if an enterprise learner is found.

    Arguments:
        user (User): Django User object or Django User object id.

    Returns:
        (bool): True if given user is an enterprise learner.
    """
    # Prevent a circular import.
    from openedx.features.enterprise_support.api import enterprise_enabled

    if not enterprise_enabled():
        return False

    try:
        user_id = int(user)
    except TypeError:
        user_id = user.id
    cached_is_enterprise_key = get_is_enterprise_cache_key(user_id)
    if cache.get(cached_is_enterprise_key):
        return True

    if EnterpriseCustomerUser.objects.filter(user_id=user_id).exists():
        # Cache the enterprise user for one hour.
        cache.set(cached_is_enterprise_key, True, 3600)
        return True

    return False


def get_enterprise_slug_login_url():
    """
    Return the enterprise slug login's URL (enterprise/login) if it exists otherwise None
    """
    try:
        return reverse('enterprise_slug_login')
    except NoReverseMatch:
        return None


def get_provider_login_url(request, provider_id, redirect_url=None):
    """
    Return the given provider's login URL.

    This method is here to avoid the importing of pipeline and student app in enterprise.
    """

    provider_login_url = third_party_auth.pipeline.get_login_url(
        provider_id,
        third_party_auth.pipeline.AUTH_ENTRY_LOGIN,
        redirect_url=redirect_url if redirect_url else get_next_url_for_login_page(request)
    )
    return provider_login_url


def fetch_enterprise_customer_by_id(enterprise_uuid):
    return EnterpriseCustomer.objects.get(uuid=enterprise_uuid)


def is_course_accessed(user, course_id):
    """
    Check if the learner accessed the course.

    Arguments:
        user (User): Django User object.
        course_id (String): The course identifier

    Returns:
        (bool): True if course has been accessed by the enterprise learner.
    """
    try:
        get_key_to_last_completed_block(user, course_id)
        return True
    except UnavailableCompletionData:
        return False
