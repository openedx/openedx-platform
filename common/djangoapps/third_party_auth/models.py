"""
Models used to implement SAML SSO support in third_party_auth
(inlcuding Shibboleth support)
"""


import json
import logging
import re

from config_models.models import ConfigurationModel, cache
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from organizations.models import Organization
from social_core.backends.base import BaseAuth
from social_core.backends.oauth import OAuthAuth
from social_core.backends.saml import SAMLAuth
from social_core.exceptions import SocialAuthBaseException
from social_core.utils import module_member

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_current_request
from openedx.core.djangoapps.user_api.accounts import USERNAME_MAX_LENGTH
from openedx.core.lib.hash_utils import create_hash256

from .lti import LTI_PARAMS_KEY, LTIAuthBackend
from .saml import STANDARD_SAML_PROVIDER_KEY, get_saml_idp_choices, get_saml_idp_class

log = logging.getLogger(__name__)

REGISTRATION_FORM_FIELD_BLACKLIST = [
    'name',
    'username'
]


# A dictionary of {name: class} entries for each python-social-auth backend available.
# Because this setting can specify arbitrary code to load and execute, it is set via
# normal Django settings only and cannot be changed at runtime:
def _load_backend_classes(base_class=BaseAuth):
    """ Load the list of python-social-auth backend classes from Django settings """
    for class_path in settings.AUTHENTICATION_BACKENDS:
        auth_class = module_member(class_path)
        if issubclass(auth_class, base_class):
            yield auth_class
_PSA_BACKENDS = {backend_class.name: backend_class for backend_class in _load_backend_classes()}
_PSA_OAUTH2_BACKENDS = [backend_class.name for backend_class in _load_backend_classes(OAuthAuth)]
_PSA_SAML_BACKENDS = [backend_class.name for backend_class in _load_backend_classes(SAMLAuth)]
_LTI_BACKENDS = [backend_class.name for backend_class in _load_backend_classes(LTIAuthBackend)]


def clean_json(value, of_type):
    """ Simple helper method to parse and clean JSON """
    if not value.strip():
        return json.dumps(of_type())
    try:
        value_python = json.loads(value)
    except ValueError as err:
        raise ValidationError(f"Invalid JSON: {err}")  # pylint: disable=raise-missing-from  # noqa: B904
    if not isinstance(value_python, of_type):
        raise ValidationError(f"Expected a JSON {of_type}")
    return json.dumps(value_python, indent=4)


def clean_username(username=''):
    """
    Simple helper method to ensure a username is compatible with our system requirements.
    """
    if settings.FEATURES.get("ENABLE_UNICODE_USERNAME"):
        return ('_').join(re.findall(settings.USERNAME_REGEX_PARTIAL, username))[:USERNAME_MAX_LENGTH]
    else:
        return ('_').join(re.findall(r'[a-zA-Z0-9\-]+', username))[:USERNAME_MAX_LENGTH]


class AuthNotConfigured(SocialAuthBaseException):
    """ Exception when SAMLProviderData or other required info is missing """
    def __init__(self, provider_name):
        super().__init__()
        self.provider_name = provider_name

    def __str__(self):
        return _('Authentication with {} is currently unavailable.').format(
            self.provider_name
        )


def _get_site_id_from_settings() -> int:
    """
    Simply return SITE_ID from settings.

    We define this function so the current SITE_ID can be used as the default value on a
    couple fields in this module. We can't use `settings.SITE_ID` directly in the model class,
    because then the migration file will contain whatever the value of SITE_ID was when the
    migration was created. This value is usually 1, but in the event that a developer is
    running a non-default site, they would get a value like 2 or 3, which will result in
    a dirty migration state. By defining this wrapper function, the name
    `_get_site_id_from_settings` will be serialized to the migration file as the default,
    regardless of what any developer's active SITE_ID is.

    Reference: https://docs.djangoproject.com/en/dev/ref/models/fields/#default
    """
    return settings.SITE_ID


class ProviderConfig(ConfigurationModel):
    """
    Abstract Base Class for configuring a third_party_auth provider

    .. no_pii:
    """
    KEY_FIELDS = ('slug',)

    icon_class = models.CharField(
        max_length=50,
        blank=True,
        default='fa-sign-in',
        help_text=(
            'The Font Awesome (or custom) icon class to use on the login button for this provider. '
            'Examples: fa-google-plus, fa-facebook, fa-linkedin, fa-sign-in, fa-university'
        ),
    )
    # We use a FileField instead of an ImageField here because ImageField
    # doesn't support SVG. This means we don't get any image validation, but
    # that should be fine because only trusted users should be uploading these
    # anyway.
    icon_image = models.FileField(
        blank=True,
        help_text=(
            'If there is no Font Awesome icon available for this provider, upload a custom image. '
            'SVG images are recommended as they can scale to any size.'
        ),
    )
    name = models.CharField(
        max_length=50, blank=True, help_text="Name of this provider (shown to users)")
    slug = models.SlugField(
        max_length=30, db_index=True, default='default',
        help_text=(
            'A short string uniquely identifying this provider. '
            'Cannot contain spaces and should be a usable as a CSS class. Examples: "ubc", "mit-staging"'
        ))
    secondary = models.BooleanField(
        default=False,
        help_text=_(
            'Secondary providers are displayed less prominently, '
            'in a separate list of "Institution" login providers.'
        ),
    )
    organization = models.ForeignKey(
        Organization,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        help_text=_(
            'optional. If this provider is an Organization, this attribute '
            'can be used reference users in that Organization'
        )
    )
    site = models.ForeignKey(
        Site,
        default=_get_site_id_from_settings,
        related_name='%(class)ss',
        help_text=_(
            'The Site that this provider configuration belongs to.'
        ),
        on_delete=models.CASCADE,
    )
    skip_hinted_login_dialog = models.BooleanField(
        default=False,
        help_text=_(
            "If this option is enabled, users that visit a \"TPA hinted\" URL for this provider "
            "(e.g. a URL ending with `?tpa_hint=[provider_name]`) will be forwarded directly to "
            "the login URL of the provider instead of being first prompted with a login dialog."
        ),
    )
    skip_registration_form = models.BooleanField(
        default=False,
        help_text=_(
            "If this option is enabled, users will not be asked to confirm their details "
            "(name, email, etc.) during the registration process. Only select this option "
            "for trusted providers that are known to provide accurate user information."
        ),
    )
    skip_email_verification = models.BooleanField(
        default=False,
        help_text=_(
            "If this option is selected, users will not be required to confirm their "
            "email, and their account will be activated immediately upon registration."
        ),
    )
    send_welcome_email = models.BooleanField(
        default=False,
        help_text=_(
            "If this option is selected, users will be sent a welcome email upon registration."
        ),
    )
    visible = models.BooleanField(
        default=False,
        help_text=_(
            "If this option is not selected, users will not be presented with the provider "
            "as an option to authenticate with on the login screen, but manual "
            "authentication using the correct link is still possible."
        ),
    )
    max_session_length = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=None,
        verbose_name='Max session length (seconds)',
        help_text=_(
            "If this option is set, then users logging in using this SSO provider will have "
            "their session length limited to no longer than this value. If set to 0 (zero), "
            "the session will expire upon the user closing their browser. If left blank, the "
            "Django platform session default length will be used."
        )
    )
    send_to_registration_first = models.BooleanField(
        default=False,
        help_text=_(
            "If this option is selected, users will be directed to the registration page "
            "immediately after authenticating with the third party instead of the login page."
        ),
    )
    sync_learner_profile_data = models.BooleanField(
        default=False,
        help_text=_(
            "Synchronize user profile data received from the identity provider with the edX user "
            "account on each SSO login. The user will be notified if the email address associated "
            "with their account is changed as a part of this synchronization."
        )
    )
    enable_sso_id_verification = models.BooleanField(
        default=False,
        help_text="Use the presence of a profile from a trusted third party as proof of identity verification.",
    )

    # Enterprise-only field: excludes this provider from the EnterpriseCustomer Django admin IDP
    # dropdown. Added in ENT-1366 after social auth providers (Facebook, Google, etc.) were linked
    # as enterprise IDPs, incorrectly associating all their users with an enterprise. Should ideally
    # be migrated into the enterprise plugin.
    disable_for_enterprise_sso = models.BooleanField(
        default=False,
        verbose_name='Disabled for Enterprise TPA',
        help_text=_(
            "IDPs with this set to True will be excluded from the dropdown IDP selection "
            "in the EnterpriseCustomer Django Admin form."
        )
    )

    was_valid_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text=(
            "Timestamped field that indicates a user has successfully logged in using this configuration at least once."
        )
    )

    prefix = None  # used for provider_id. Set to a string value in subclass
    backend_name = None  # Set to a field or fixed value in subclass
    accepts_logins = True  # Whether to display a sign-in button when the provider is enabled

    # "enabled" field is inherited from ConfigurationModel

    class Meta:
        app_label = "third_party_auth"
        abstract = True

    def clean(self):
        """ Ensure that at most `icon_class` or `icon_image` is set """
        super().clean()
        if bool(self.icon_class) and bool(self.icon_image):
            raise ValidationError('Either an icon class or an icon image must be given (but not both)')

    @property
    def provider_id(self):
        """ Unique string key identifying this provider. Must be URL and css class friendly. """
        assert self.prefix is not None
        return "-".join((self.prefix, ) + tuple(getattr(self, field) for field in self.KEY_FIELDS))

    @property
    def backend_class(self):
        """ Get the python-social-auth backend class used for this provider """
        return _PSA_BACKENDS[self.backend_name]

    @property
    def full_class_name(self):
        """ Get the fully qualified class name of this provider. """
        return f'{self.__module__}.{self.__class__.__name__}'

    def get_url_params(self):
        """ Get a dict of GET parameters to append to login links for this provider """
        return {}

    def is_active_for_pipeline(self, pipeline):
        """ Is this provider being used for the specified pipeline? """
        return self.backend_name == pipeline['backend']

    def match_social_auth(self, social_auth):
        """ Is this provider being used for this UserSocialAuth entry? """
        return self.backend_name == social_auth.provider

    def get_remote_id_from_social_auth(self, social_auth):
        """ Given a UserSocialAuth object, return the remote ID used by this provider. """
        # This is generally the same thing as the UID, expect when one backend is used for multiple providers
        assert self.match_social_auth(social_auth)
        return social_auth.uid

    def get_social_auth_uid(self, remote_id):
        """
        Return the uid in social auth.

        This is default implementation. Subclass may override with a different one.
        """
        return remote_id

    @classmethod
    def get_register_form_data(cls, pipeline_kwargs):
        """Gets dict of data to display on the register form.

        register_user uses this to populate
        the new account creation form with values supplied by the user's chosen
        provider, preventing duplicate data entry.

        Args:
            pipeline_kwargs: dict of string -> object. Keyword arguments
                accumulated by the pipeline thus far.

        Returns:
            Dict of string -> string. Keys are names of form fields; values are
            values for that field. Where there is no value, the empty string
            must be used.
        """
        registration_form_data = {}

        # Details about the user sent back from the provider.
        details = pipeline_kwargs.get('details').copy()

        # Set the registration form to use the `fullname` detail for the `name` field.
        registration_form_data['name'] = details.get('fullname', '')

        # Get the username separately to take advantage of the de-duping logic
        # built into the pipeline. The provider cannot de-dupe because it can't
        # check the state of taken usernames in our system. Note that there is
        # technically a data race between the creation of this value and the
        # creation of the user object, so it is still possible for users to get
        # an error on submit.
        registration_form_data['username'] = clean_username(pipeline_kwargs.get('username') or '')

        # Any other values that are present in the details dict should be copied
        # into the registration form details. This may include details that do
        # not map to a value that exists in the registration form. However,
        # because the fields that are actually rendered are not based on this
        # list, only those values that map to a valid registration form field
        # will actually be sent to the form as default values.
        for blacklisted_field in REGISTRATION_FORM_FIELD_BLACKLIST:
            details.pop(blacklisted_field, None)
        registration_form_data.update(details)

        return registration_form_data

    def get_authentication_backend(self):
        """Gets associated Django settings.AUTHENTICATION_BACKEND string."""
        return f'{self.backend_class.__module__}.{self.backend_class.__name__}'

    @property
    def display_for_login(self):
        """
        Determines whether the provider ought to be shown as an option with
        which to authenticate on the login screen, registration screen, and elsewhere.
        """
        return bool(self.enabled_for_current_site and self.accepts_logins and self.visible)

    @property
    def enabled_for_current_site(self):
        """
        Determines if the provider is able to be used with the current site.
        """
        return self.enabled and self.site_id == Site.objects.get_current(get_current_request()).id


class OAuth2ProviderConfig(ProviderConfig):
    """
    Configuration Entry for an OAuth2 based provider.
    Also works for OAuth1 providers.

    .. no_pii:
    """
    # We are keying the provider config by backend_name and site_id to support configuration per site.
    # In order to reuse a backend for a second provider, a subclass can be created with seperate name.
    # example:
    # class SecondOpenIDProvider(OpenIDAuth):
    #   name = "second-openId-provider"
    KEY_FIELDS = ('site_id', 'backend_name')
    prefix = 'oa2'
    backend_name = models.CharField(
        max_length=50, blank=False, db_index=True,
        help_text=(
            "Which python-social-auth OAuth2 provider backend to use. "
            "The list of backend choices is determined by the THIRD_PARTY_AUTH_BACKENDS setting."
            # To be precise, it's set by AUTHENTICATION_BACKENDS
            # which production.py sets from THIRD_PARTY_AUTH_BACKENDS
        )
    )
    key = models.TextField(blank=True, verbose_name="Client ID")
    secret = models.TextField(
        blank=True,
        verbose_name="Client Secret",
        help_text=(
            'For increased security, you can avoid storing this in your database by leaving '
            ' this field blank and setting '
            'SOCIAL_AUTH_OAUTH_SECRETS = {"(backend name)": "secret", ...} '
            'in your instance\'s Django settings (or lms.yml)'
        )
    )
    other_settings = models.TextField(blank=True, help_text="Optional JSON object with advanced settings, if any.")

    class Meta:
        app_label = "third_party_auth"
        verbose_name = "Provider Configuration (OAuth)"
        verbose_name_plural = verbose_name

    @classmethod
    def current(cls, *args):
        """
        Get the current config model for the provider according to the given backend and the current
        site.
        """
        site_id = Site.objects.get_current(get_current_request()).id
        return super(OAuth2ProviderConfig, cls).current(site_id, *args)  # noqa: UP008

    @property
    def provider_id(self):
        """
        Unique string key identifying this provider. Must be URL and css class friendly.
        Ignoring site_id as the config is filtered using current method which fetches the configuration for the current
        site_id.
        """
        assert self.prefix is not None
        return "-".join((self.prefix, ) + tuple(
            str(getattr(self, field))
            for field in self.KEY_FIELDS
            if field != 'site_id'
        ))

    def clean(self):
        """ Standardize and validate fields """
        super().clean()
        self.other_settings = clean_json(self.other_settings, dict)

    def get_setting(self, name):
        """ Get the value of a setting, or raise KeyError """
        if name == "KEY":
            return self.key
        if name == "SECRET":
            if self.secret:
                return self.secret
            # To allow instances to avoid storing secrets in the DB, the secret can also be set via Django:
            return getattr(settings, 'SOCIAL_AUTH_OAUTH_SECRETS', {}).get(self.backend_name, '')
        if self.other_settings:
            other_settings = json.loads(self.other_settings)
            assert isinstance(other_settings, dict), "other_settings should be a JSON object (dictionary)"
            return other_settings[name]
        raise KeyError


class SAMLConfiguration(ConfigurationModel):
    """
    General configuration required for this edX instance to act as a SAML
    Service Provider and allow users to authenticate via third party SAML
    Identity Providers (IdPs)

    .. no_pii:
    """
    KEY_FIELDS = ('site_id', 'slug')
    site = models.ForeignKey(
        Site,
        default=_get_site_id_from_settings,
        related_name='%(class)ss',
        help_text=_(
            'The Site that this SAML configuration belongs to.'
        ),
        on_delete=models.CASCADE,
    )
    slug = models.SlugField(
        max_length=30,
        default='default',
        blank=True,
        help_text=(
            'A short string uniquely identifying this configuration. '
            'Cannot contain spaces. Examples: "ubc", "mit-staging"'
        ),
    )
    private_key = models.TextField(
        help_text=(
            'To generate a key pair as two files, run '
            '"openssl req -new -x509 -days 3652 -nodes -out saml.crt -keyout saml.key". '
            'Paste the contents of saml.key here. '
            'For increased security, you can avoid storing this in your database by leaving '
            'this field blank and setting it via the SOCIAL_AUTH_SAML_SP_PRIVATE_KEY setting '
            'in your instance\'s Django settings (or lms.yml).'
        ),
        blank=True,
    )
    public_key = models.TextField(
        help_text=(
            'Public key certificate. '
            'For increased security, you can avoid storing this in your database by leaving '
            'this field blank and setting it via the SOCIAL_AUTH_SAML_SP_PUBLIC_CERT setting '
            'in your instance\'s Django settings (or lms.yml).'
        ),
        blank=True,
    )
    entity_id = models.CharField(max_length=255, default="http://saml.example.com", verbose_name="Entity ID")
    org_info_str = models.TextField(
        verbose_name="Organization Info",
        default='{"en-US": {"url": "http://www.example.com", "displayname": "Example Inc.", "name": "example"}}',
        help_text="JSON dictionary of 'url', 'displayname', and 'name' for each language",
    )
    other_config_str = models.TextField(
        default='{\n"SECURITY_CONFIG": {"metadataCacheDuration": 604800, "signMetadata": false}\n}',
        help_text=(
            "JSON object defining advanced settings that are passed on to python-saml. "
            "Valid keys that can be set here include: SECURITY_CONFIG and SP_EXTRA"
        ),
    )
    is_public = models.BooleanField(
        default=False,
        verbose_name="Allow customers to see and use this SAML configuration",
        help_text=(
            "When checked, customers will be able to choose this SAML Configuration "
            "in the admin portal."
        ),
    )

    class Meta:
        app_label = "third_party_auth"
        verbose_name = "SAML Configuration"
        verbose_name_plural = verbose_name

    def __str__(self):
        """
        Return human-readable string representation.
        """
        return "SAMLConfiguration {site}: {slug} on {date:%Y-%m-%d %H:%M:%S}".format(  # noqa: UP032
            site=self.site.name,
            slug=self.slug,
            date=self.change_date,
        )

    def clean(self):
        """ Standardize and validate fields """
        super().clean()
        self.org_info_str = clean_json(self.org_info_str, dict)
        self.other_config_str = clean_json(self.other_config_str, dict)

        self.private_key = (
            self.private_key
            .replace("-----BEGIN RSA PRIVATE KEY-----", "")
            .replace("-----BEGIN PRIVATE KEY-----", "")
            .replace("-----END RSA PRIVATE KEY-----", "")
            .replace("-----END PRIVATE KEY-----", "")
            .strip()
        )
        self.public_key = (
            self.public_key
            .replace("-----BEGIN CERTIFICATE-----", "")
            .replace("-----END CERTIFICATE-----", "")
            .strip()
        )

    def get_setting(self, name):
        """ Get the value of a setting, or raise KeyError """
        default_saml_contact = {
            # Default contact information to put into the SAML metadata that gets generated by python-saml.
            "givenName": _("{platform_name} Support").format(
                platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
            ),
            "emailAddress": configuration_helpers.get_value('TECH_SUPPORT_EMAIL', settings.TECH_SUPPORT_EMAIL),
        }

        if name == "ORG_INFO":
            return json.loads(self.org_info_str)
        if name == "SP_ENTITY_ID":
            return self.entity_id
        if name == "SP_PUBLIC_CERT":
            if self.public_key:
                return self.public_key
            # To allow instances to avoid storing keys in the DB, the key pair can also be set via Django:
            if self.slug == 'default':
                return getattr(settings, 'SOCIAL_AUTH_SAML_SP_PUBLIC_CERT', '')
            else:
                public_certs = getattr(settings, 'SOCIAL_AUTH_SAML_SP_PUBLIC_CERT_DICT', {})
                return public_certs.get(self.slug, '')
        if name == "SP_PRIVATE_KEY":
            if self.private_key:
                return self.private_key
            # To allow instances to avoid storing keys in the DB, the private key can also be set via Django:
            if self.slug == 'default':
                return getattr(settings, 'SOCIAL_AUTH_SAML_SP_PRIVATE_KEY', '')
            else:
                private_keys = getattr(settings, 'SOCIAL_AUTH_SAML_SP_PRIVATE_KEY_DICT', {})
                return private_keys.get(self.slug, '')
        other_config = {
            # These defaults can be overriden by self.other_config_str
            "GET_ALL_EXTRA_DATA": True,  # Save all attribute values the IdP sends into the UserSocialAuth table
            "TECHNICAL_CONTACT": default_saml_contact,
            "SUPPORT_CONTACT": default_saml_contact,
        }
        other_config.update(json.loads(self.other_config_str))
        return other_config[name]  # SECURITY_CONFIG, SP_EXTRA, or similar extra settings


def active_saml_configurations_filter():
    """
    Returns a mapping to be used for the SAMLProviderConfig to limit the SAMLConfiguration choices to the current set.
    """
    query_set = SAMLConfiguration.objects.current_set()
    return {'id__in': query_set.values_list('id', flat=True)}


class SAMLProviderConfig(ProviderConfig):
    """
    Configuration Entry for a SAML/Shibboleth provider.

    .. no_pii:
    """
    prefix = 'saml'
    display_name = models.CharField(
        max_length=35, blank=True,
        help_text=_("A configuration nickname.")
    )
    backend_name = models.CharField(
        max_length=50, default='tpa-saml', blank=True,
        help_text="Which python-social-auth provider backend to use. 'tpa-saml' is the standard edX SAML backend."
    )
    entity_id = models.CharField(
        max_length=255, verbose_name="Entity ID", blank=True,
        help_text="Example: https://idp.testshib.org/idp/shibboleth"
    )
    metadata_source = models.CharField(
        max_length=255, blank=True,
        help_text=(
            "URL to this provider's XML metadata. Should be an HTTPS URL. "
            "Example: https://www.testshib.org/metadata/testshib-providers.xml"
        )
    )
    attr_user_permanent_id = models.CharField(
        max_length=128, blank=True, verbose_name="User ID Attribute",
        help_text=(
            "URN of the SAML attribute that we can use as a unique, "
            "persistent user ID. Leave blank for default."
        )
    )
    attr_full_name = models.CharField(
        max_length=128, blank=True, verbose_name="Full Name Attribute",
        help_text="URN of SAML attribute containing the user's full name. Leave blank for default."
    )
    default_full_name = models.CharField(
        max_length=255, blank=True, verbose_name="Default Value for Full Name",
        help_text="Default value for full name to be used if not present in SAML response."
    )
    attr_first_name = models.CharField(
        max_length=128, blank=True, verbose_name="First Name Attribute",
        help_text="URN of SAML attribute containing the user's first name. Leave blank for default."
    )
    default_first_name = models.CharField(
        max_length=255, blank=True, verbose_name="Default Value for First Name",
        help_text="Default value for first name to be used if not present in SAML response."
    )
    attr_last_name = models.CharField(
        max_length=128, blank=True, verbose_name="Last Name Attribute",
        help_text="URN of SAML attribute containing the user's last name. Leave blank for default."
    )
    default_last_name = models.CharField(
        max_length=255, blank=True, verbose_name="Default Value for Last Name",
        help_text="Default value for last name to be used if not present in SAML response.")
    attr_username = models.CharField(
        max_length=128, blank=True, verbose_name="Username Hint Attribute",
        help_text="URN of SAML attribute to use as a suggested username for this user. Leave blank for default."
    )
    default_username = models.CharField(
        max_length=255, blank=True, verbose_name="Default Value for Username",
        help_text="Default value for username to be used if not present in SAML response."
    )
    attr_email = models.CharField(
        max_length=128, blank=True, verbose_name="Email Attribute",
        help_text="URN of SAML attribute containing the user's email address[es]. Leave blank for default.")
    default_email = models.CharField(
        max_length=255, blank=True, verbose_name="Default Value for Email",
        help_text="Default value for email to be used if not present in SAML response."
    )
    automatic_refresh_enabled = models.BooleanField(
        default=True, verbose_name="Enable automatic metadata refresh",
        help_text="When checked, the SAML provider's metadata will be included "
                  "in the automatic refresh job, if configured."
    )
    identity_provider_type = models.CharField(
        max_length=128, blank=True, verbose_name="Identity Provider Type", default=STANDARD_SAML_PROVIDER_KEY,
        choices=get_saml_idp_choices(), help_text=(
            "Some SAML providers require special behavior. For example, SAP SuccessFactors SAML providers require an "
            "additional API call to retrieve user metadata not provided in the SAML response. Select the provider type "
            "which best matches your use case. If in doubt, choose the Standard SAML Provider type."
        )
    )
    debug_mode = models.BooleanField(
        default=False, verbose_name="Debug Mode",
        help_text=(
            "In debug mode, all SAML XML requests and responses will be logged. "
            "This is helpful for testing/setup but should always be disabled before users start using this provider."
        ),
    )
    country = models.CharField(
        max_length=128,
        help_text=(
            'URN of SAML attribute containing the user`s country.',
        ),
        blank=True,
    )
    skip_hinted_login_dialog = models.BooleanField(
        default=True,
        help_text=_(
            "If this option is enabled, users that visit a \"TPA hinted\" URL for this provider "
            "(e.g. a URL ending with `?tpa_hint=[provider_name]`) will be forwarded directly to "
            "the login URL of the provider instead of being first prompted with a login dialog."
        ),
    )
    skip_registration_form = models.BooleanField(
        default=True,
        help_text=_(
            "If this option is enabled, users will not be asked to confirm their details "
            "(name, email, etc.) during the registration process. Only select this option "
            "for trusted providers that are known to provide accurate user information."
        ),
    )
    skip_email_verification = models.BooleanField(
        default=True,
        help_text=_(
            "If this option is selected, users will not be required to confirm their "
            "email, and their account will be activated immediately upon registration."
        ),
    )
    send_to_registration_first = models.BooleanField(
        default=True,
        help_text=_(
            "If this option is selected, users will be directed to the registration page "
            "immediately after authenticating with the third party instead of the login page."
        ),
    )
    skip_registration_optional_checkboxes = models.BooleanField(
        default=False,
        help_text=_(
            "If enabled, optional checkboxes (marketing emails opt-in, etc.) will not be rendered "
            "on the registration form for users registering via this provider. When these checkboxes "
            "are skipped, their values are inferred as False (opted out)."
        ),
    )
    other_settings = models.TextField(
        verbose_name="Advanced settings", blank=True,
        help_text=(
            'For advanced use cases, enter a JSON object with addtional configuration. '
            'The tpa-saml backend supports {"requiredEntitlements": ["urn:..."]}, '
            'which can be used to require the presence of a specific eduPersonEntitlement, '
            'and {"extra_field_definitions": [{"name": "...", "urn": "..."},...]}, which can be '
            'used to define registration form fields and the URNs that can be used to retrieve '
            'the relevant values from the SAML response. Custom provider types, as selected '
            'in the "Identity Provider Type" field, may make use of the information stored '
            'in this field for additional configuration.'
        )
    )
    archived = models.BooleanField(default=False)
    saml_configuration = models.ForeignKey(
        SAMLConfiguration,
        on_delete=models.SET_NULL,
        limit_choices_to=active_saml_configurations_filter,
        null=True,
        blank=True,
    )

    def clean(self):
        """ Standardize and validate fields """
        super().clean()
        self.other_settings = clean_json(self.other_settings, dict)

    class Meta:
        app_label = "third_party_auth"
        verbose_name = "Provider Configuration (SAML IdP)"
        verbose_name_plural = "Provider Configuration (SAML IdPs)"

    def save(self, *args, **kwargs):
        # Disallowing any new entries that have the same entity ID as an existing provider config unless the slug
        # matches.
        # This both allows for the old architecture to create new rows on save but also prevents enterprise users from
        # creating configs that share entity ID's with other enterprises
        # One consequence of this is that once a provider configuration is created, the slug is essentially locked in
        # and unchangeable. But I blame that on bad old architecture.
        existing_provider_configs = SAMLProviderConfig.objects.current_set().filter(
            entity_id=self.entity_id,
            archived=False,
        ).exclude(slug=self.slug)
        # If any exist, raise an integrity error
        if existing_provider_configs:
            exc_str = f'Entity ID: {self.entity_id} already in use'
            # There are cases of preexisting configurations that share entity id's so we can't blow up if we
            # encounter this issue. Instead just log for clarity.
            # raise IntegrityError(exc_str)
            log.warning(exc_str)
        super().save(*args, **kwargs)

    def get_url_params(self):
        """ Get a dict of GET parameters to append to login links for this provider """
        return {'idp': self.slug}

    def is_active_for_pipeline(self, pipeline):
        """ Is this provider being used for the specified pipeline? """
        return self.backend_name == pipeline['backend'] and self.slug == pipeline['kwargs']['response']['idp_name']

    def match_social_auth(self, social_auth):
        """ Is this provider being used for this UserSocialAuth entry? """
        prefix = self.slug + ":"
        return self.backend_name == social_auth.provider and social_auth.uid.startswith(prefix)

    def get_remote_id_from_field_name(self, social_auth, field_name):
        """ Given a UserSocialAuth object, return the user remote ID against the field name provided. """
        if not self.match_social_auth(social_auth):
            raise ValueError(
                f"UserSocialAuth record does not match given provider {self.provider_id}"
            )
        field_value = social_auth.extra_data.get(field_name, None)
        if field_value and isinstance(field_value, list):
            return field_value[0]
        return field_value

    def get_remote_id_from_social_auth(self, social_auth):
        """ Given a UserSocialAuth object, return the remote ID used by this provider. """
        assert self.match_social_auth(social_auth)
        # Remove the prefix from the UID
        return social_auth.uid[len(self.slug) + 1:]

    def get_social_auth_uid(self, remote_id):
        """ Get social auth uid from remote id by prepending idp_slug to the remote id """
        return f'{self.slug}:{remote_id}'

    def get_setting(self, name):
        """ Get the value of a setting, or raise KeyError """
        if self.other_settings:
            other_settings = json.loads(self.other_settings)
            return other_settings[name]
        raise KeyError

    def get_config(self, backend):
        """
        Return a SAMLIdentityProvider instance for use by SAMLAuthBackend.

        Essentially this just returns the values of this object and its
        associated 'SAMLProviderData' entry.
        """
        if self.other_settings:
            conf = json.loads(self.other_settings)
        else:
            conf = {}
        attrs = (
            'attr_user_permanent_id', 'attr_full_name', 'attr_first_name',
            'attr_last_name', 'attr_username', 'attr_email', 'entity_id', 'country')
        attr_defaults = {
            'attr_full_name': 'default_full_name',
            'attr_first_name': 'default_first_name',
            'attr_last_name': 'default_last_name',
            'attr_username': 'default_username',
            'attr_email': 'default_email',
        }

        # Defaults for missing attributes in SAML Response
        conf['attr_defaults'] = {}

        for field in attrs:
            field_name = attr_defaults.get(field)
            val = getattr(self, field)
            if val:
                conf[field] = val

            # Default values for SAML attributes
            default = getattr(self, field_name) if field_name else None
            conf['attr_defaults'][field] = default

        # Now get the data fetched automatically from the metadata.xml:
        data_records = SAMLProviderData.objects.filter(entity_id=self.entity_id)
        public_keys = []
        for record in data_records:
            if record.is_valid():
                public_keys.append(record.public_key)
                sso_url = record.sso_url
        if not public_keys:
            log.error(
                'No SAMLProviderData found for provider "%s" with entity id "%s" and IdP slug "%s". '
                'Run "manage.py saml pull" to fix or debug.',
                self.name, self.entity_id, self.slug
            )
            raise AuthNotConfigured(provider_name=self.name)

        conf['x509certMulti'] = {'signing': public_keys}
        conf['x509cert'] = ''
        conf['url'] = sso_url

        # Add SAMLConfiguration appropriate for this IdP
        conf['saml_sp_configuration'] = (
            self.saml_configuration or
            SAMLConfiguration.current(self.site.id, 'default')
        )
        idp_class = get_saml_idp_class(self.identity_provider_type)
        return idp_class(backend, self.slug, **conf)


class SAMLProviderData(models.Model):  # noqa: DJ008
    """
    Data about a SAML IdP that is fetched automatically by 'manage.py saml pull'

    This data is only required during the actual authentication process.

    .. no_pii:
    """
    cache_timeout = 600
    fetched_at = models.DateTimeField(db_index=True, null=False)
    expires_at = models.DateTimeField(db_index=True, null=True)

    entity_id = models.CharField(max_length=255, db_index=True)  # This is the key for lookups in this table
    sso_url = models.URLField(verbose_name="SSO URL")
    public_key = models.TextField()

    class Meta:
        app_label = "third_party_auth"
        verbose_name = "SAML Provider Data"
        verbose_name_plural = verbose_name
        ordering = ('-fetched_at', )

    def is_valid(self):
        """ Is this data valid? """
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return bool(self.entity_id and self.sso_url and self.public_key)
    is_valid.boolean = True

    @classmethod
    def cache_key_name(cls, entity_id):
        """ Return the name of the key to use to cache the current data """
        return f'configuration/{cls.__name__}/current/{entity_id}'

    @classmethod
    def current(cls, entity_id):
        """
        Return the active data entry, if any, otherwise None
        """
        cached = cache.get(cls.cache_key_name(entity_id))
        if cached is not None:
            return cached

        try:
            current = cls.objects.filter(entity_id=entity_id).order_by('-fetched_at')[0]
        except IndexError:
            current = None

        cache.set(cls.cache_key_name(entity_id), current, cls.cache_timeout)
        return current


class LTIProviderConfig(ProviderConfig):
    """
    Configuration required for this edX instance to act as a LTI
    Tool Provider and allow users to authenticate and be enrolled in a
    course via third party LTI Tool Consumers.

    .. no_pii:
    """
    prefix = 'lti'
    backend_name = 'lti'

    # This provider is not visible to users
    icon_class = None
    icon_image = None
    secondary = False

    # LTI login cannot be initiated by the tool provider
    accepts_logins = False

    KEY_FIELDS = ('lti_consumer_key', )

    lti_consumer_key = models.CharField(
        max_length=255,
        help_text=(
            'The name that the LTI Tool Consumer will use to identify itself'
        )
    )

    lti_hostname = models.CharField(
        default='localhost',
        max_length=255,
        help_text=(
            'The domain that  will be acting as the LTI consumer.'
        ),
        db_index=True
    )

    lti_consumer_secret = models.CharField(
        default=create_hash256,
        max_length=255,
        help_text=(
            'The shared secret that the LTI Tool Consumer will use to '
            'authenticate requests. Only this edX instance and this '
            'tool consumer instance should know this value. '
            'For increased security, you can avoid storing this in '
            'your database by leaving this field blank and setting '
            'SOCIAL_AUTH_LTI_CONSUMER_SECRETS = {"consumer key": "secret", ...} '
            'in your instance\'s Django setttigs (or lms.yml)'
        ),
        blank=True,
    )

    lti_max_timestamp_age = models.IntegerField(
        default=10,
        help_text=(
            'The maximum age of oauth_timestamp values, in seconds.'
        )
    )

    def match_social_auth(self, social_auth):
        """ Is this provider being used for this UserSocialAuth entry? """
        prefix = self.lti_consumer_key + ":"
        return self.backend_name == social_auth.provider and social_auth.uid.startswith(prefix)

    def get_remote_id_from_social_auth(self, social_auth):
        """ Given a UserSocialAuth object, return the remote ID used by this provider. """
        assert self.match_social_auth(social_auth)
        # Remove the prefix from the UID
        return social_auth.uid[len(self.lti_consumer_key) + 1:]

    def is_active_for_pipeline(self, pipeline):
        """ Is this provider being used for the specified pipeline? """
        try:
            return (
                self.backend_name == pipeline['backend'] and
                self.lti_consumer_key == pipeline['kwargs']['response'][LTI_PARAMS_KEY]['oauth_consumer_key']
            )
        except KeyError:
            return False

    def get_lti_consumer_secret(self):
        """ If the LTI consumer secret is not stored in the database, check Django settings instead """
        if self.lti_consumer_secret:
            return self.lti_consumer_secret
        return getattr(settings, 'SOCIAL_AUTH_LTI_CONSUMER_SECRETS', {}).get(self.lti_consumer_key, '')

    class Meta:
        app_label = "third_party_auth"
        verbose_name = "Provider Configuration (LTI)"
        verbose_name_plural = verbose_name


class AppleMigrationUserIdInfo(models.Model):
    """
    Model to store users' Apple Unique Identifier during migration
    process of Apple team from edx Inc. to edx LLC.
    """
    old_apple_id = models.CharField(max_length=255)
    transfer_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001
    new_apple_id = models.CharField(max_length=255, null=True, blank=True)  # noqa: DJ001

    def __str__(self):
        return self.old_apple_id

    class Meta:  # noqa: DJ012
        app_label = "third_party_auth"
        verbose_name = "Apple User Id Migration Info"
        verbose_name_plural = verbose_name
