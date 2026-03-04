"""Settings for the third-party auth module.

The flow for settings registration is:

The base settings file contains a boolean, ENABLE_THIRD_PARTY_AUTH, indicating
whether this module is enabled. startup.py probes the ENABLE_THIRD_PARTY_AUTH.
If true, it:

    a) loads this module.
    b) calls apply_settings(), passing in the Django settings
"""


from django.conf import settings
from openedx.features.enterprise_support.api import insert_enterprise_pipeline_elements


def apply_settings(django_settings):
    """Set provider-independent settings."""

    # Whitelisted URL query parameters retrained in the pipeline session.
    # Params not in this whitelist will be silently dropped.
    django_settings.FIELDS_STORED_IN_SESSION = ['auth_entry', 'next']

    # Inject exception middleware to make redirects fire.
    django_settings.MIDDLEWARE.extend(
        ['common.djangoapps.third_party_auth.middleware.ExceptionMiddleware']
    )

    # Where to send the user if there's an error during social authentication
    # and we cannot send them to a more specific URL
    # (see middleware.ExceptionMiddleware).
    django_settings.SOCIAL_AUTH_LOGIN_ERROR_URL = '/'

    # Where to send the user once social authentication is successful.
    django_settings.SOCIAL_AUTH_LOGIN_REDIRECT_URL = '/dashboard'

    # Disable sanitizing of redirect urls in social-auth since the platform
    # already does its own sanitization via the LOGIN_REDIRECT_WHITELIST setting.
    django_settings.SOCIAL_AUTH_SANITIZE_REDIRECTS = False

    # Adding extra key value pair in the url query string for microsoft as per request
    django_settings.SOCIAL_AUTH_AZUREAD_OAUTH2_AUTH_EXTRA_ARGUMENTS = {'msafed': 0}

    # Avoid default username check to allow non-ascii characters
    django_settings.SOCIAL_AUTH_CLEAN_USERNAMES = not settings.ENABLE_UNICODE_USERNAME

    # Inject our customized auth pipeline. All auth backends must work with
    # this pipeline.
    django_settings.SOCIAL_AUTH_PIPELINE = [
        'common.djangoapps.third_party_auth.pipeline.parse_query_params',
        'social_core.pipeline.social_auth.social_details',
        'social_core.pipeline.social_auth.social_uid',
        'social_core.pipeline.social_auth.auth_allowed',
        'social_core.pipeline.social_auth.social_user',
        'common.djangoapps.third_party_auth.pipeline.associate_by_email_if_login_api',
        'common.djangoapps.third_party_auth.pipeline.associate_by_email_if_saml',
        'common.djangoapps.third_party_auth.pipeline.associate_by_email_if_oauth',
        'common.djangoapps.third_party_auth.pipeline.get_username',
        'common.djangoapps.third_party_auth.pipeline.set_pipeline_timeout',
        'common.djangoapps.third_party_auth.pipeline.ensure_user_information',
        'social_core.pipeline.user.create_user',
        'social_core.pipeline.social_auth.associate_user',
        'social_core.pipeline.social_auth.load_extra_data',
        'social_core.pipeline.user.user_details',
        'common.djangoapps.third_party_auth.pipeline.user_details_force_sync',
        'common.djangoapps.third_party_auth.pipeline.set_id_verification_status',
        'common.djangoapps.third_party_auth.pipeline.set_logged_in_cookies',
        'common.djangoapps.third_party_auth.pipeline.login_analytics',
        'common.djangoapps.third_party_auth.pipeline.ensure_redirect_url_is_safe',
    ]

    # Add enterprise pipeline elements if the enterprise app is installed
    insert_enterprise_pipeline_elements(django_settings.SOCIAL_AUTH_PIPELINE)

    # Required so that we can use unmodified PSA OAuth2 backends:
    django_settings.SOCIAL_AUTH_STRATEGY = 'common.djangoapps.third_party_auth.strategy.ConfigurationModelStrategy'

    # We let the user specify their email address during signup.
    django_settings.SOCIAL_AUTH_PROTECTED_USER_FIELDS = ['email']

    # Disable exceptions by default for prod so you get redirect behavior
    # instead of a Django error page. During development you may want to
    # enable this when you want to get stack traces rather than redirections.
    django_settings.SOCIAL_AUTH_RAISE_EXCEPTIONS = False

    # Clean username to make sure username is compatible with our system requirements
    django_settings.SOCIAL_AUTH_CLEAN_USERNAME_FUNCTION = 'common.djangoapps.third_party_auth.models.clean_username'

    # Allow users to login using social auth even if their account is not verified yet
    # This is required since we [ab]use django's 'is_active' flag to indicate verified
    # accounts; without this set to True, python-social-auth won't allow us to link the
    # user's account to the third party account during registration (since the user is
    # not verified at that point).
    # We also generally allow unverified third party auth users to login (see the logic
    # in ensure_user_information in pipeline.py) because otherwise users who use social
    # auth to register with an invalid email address can become "stuck".
    # TODO: Remove the following if/when email validation is separated from the is_active flag.
    django_settings.SOCIAL_AUTH_INACTIVE_USER_LOGIN = True
    django_settings.SOCIAL_AUTH_INACTIVE_USER_URL = '/auth/inactive'

    # Context processors required under Django.
    django_settings.SOCIAL_AUTH_UUID_LENGTH = 10
    django_settings.DEFAULT_TEMPLATE_ENGINE['OPTIONS']['context_processors'] += (
        'social_django.context_processors.backends',
        'social_django.context_processors.login_redirect',
    )
