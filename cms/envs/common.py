"""
This is the common settings file, intended to set sane defaults. If you have a
piece of configuration that's dependent on a set of feature flags being set,
then create a function that returns the calculated value based on the value of
FEATURES[...]. Modules that extend this one can change the feature
configuration in an environment specific config file and re-calculate those
values.

We should make a method that calls all these config methods so that you just
make one call at the end of your site-specific dev file to reset all the
dependent variables (like INSTALLED_APPS) for you.

Longer TODO:
1. Right now our treatment of static content in general and in particular
   course-specific static content is haphazard.
2. We should have a more disciplined approach to feature flagging, even if it
   just means that we stick them in a dict called FEATURES.
3. We need to handle configuration for multiple courses. This could be as
   multiple sites, but we do need a way to map their data assets.

When refering to XBlocks, we use the entry-point name. For example,
|   setup(
|       name='xblock-foobar',
|       version='0.1',
|       packages=[
|           'foobar_xblock',
|       ],
|       entry_points={
|           'xblock.v1': [
|               'foobar-block = foobar_xblock:FoobarBlock',
|           #    ^^^^^^^^^^^^ This is the one you want.
|           ]
|       },
|   )
"""

# We intentionally define lots of variables that aren't used, and
# want to import all variables from base settings files
# pylint: disable=unused-import, useless-suppression, wrong-import-order, wrong-import-position

import importlib.util
import os

from corsheaders.defaults import default_headers as corsheaders_default_headers
from datetime import timedelta

from django.utils.translation import gettext_lazy as _

import lms.envs.common

from openedx.envs.common import *  # pylint: disable=wildcard-import

from path import Path as path

from lms.djangoapps.lms_xblock.mixin import LmsBlockMixin
from cms.lib.xblock.authoring_mixin import AuthoringMixin
from cms.lib.xblock.upstream_sync import UpstreamSyncMixin
from xmodule.modulestore.edit_info import EditInfoMixin
from openedx.core.lib.derived import Derived
from openedx.core.lib.features_setting_proxy import FeaturesProxy

# A proxy for feature flags stored in the settings namespace
FEATURES = FeaturesProxy(globals())

# pylint: enable=useless-suppression

############################ FEATURE CONFIGURATION #############################

CONTACT_MAILING_ADDRESS = _('Your Contact Mailing Address Here')

# Dummy secret key for dev/test
SECRET_KEY = 'dev key'

# .. setting_name: STUDIO_NAME
# .. setting_default: Your Platform Studio
# .. setting_description: The name that will appear on the landing page of Studio, as well as in various emails and
#   templates.
STUDIO_NAME = _("Your Platform Studio")
STUDIO_SHORT_NAME = _("Studio")

# FEATURES

GITHUB_PUSH = False

# email address for studio staff (eg to request course creation)
STUDIO_REQUEST_EMAIL = ''

# Segment - must explicitly turn it on for production
CMS_SEGMENT_KEY = None

# If set to True, new Studio users won't be able to author courses unless
# an Open edX admin has added them to the course creator group.
ENABLE_CREATOR_GROUP = True

# If set to True, organization staff members can create libraries for their specific
# organization and no other organizations. They do not need to be course creators,
# even when ENABLE_CREATOR_GROUP is True.
ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES = True

# Turn off account locking if failed login attempts exceeds a limit
ENABLE_MAX_FAILED_LOGIN_ATTEMPTS = False

# .. toggle_name: settings.EDITABLE_SHORT_DESCRIPTION
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: This feature flag allows editing of short descriptions on the Schedule & Details page in
#   Open edX Studio. Set to False if you want to disable the editing of the course short description.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-02-13
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2334
EDITABLE_SHORT_DESCRIPTION = True

# Hide any Personally Identifiable Information from application logs
SQUELCH_PII_IN_LOGS = False

# Allow creating courses with non-ascii characters in the course id
ALLOW_UNICODE_COURSE_ID = False

# Prevent concurrent logins per user
PREVENT_CONCURRENT_LOGINS = False

# Turn off Video Upload Pipeline through Studio, by default
ENABLE_VIDEO_UPLOAD_PIPELINE = False

# Show a new field in "Advanced settings" that can store custom data about a
# course and that can be read from themes
ENABLE_OTHER_COURSE_SETTINGS = False

# Enable support for content libraries. Note that content libraries are
# only supported in courses using split mongo.
ENABLE_CONTENT_LIBRARIES = True

# .. toggle_name: settings.ENABLE_CONTENT_LIBRARIES_LTI_TOOL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When set to True, Content Libraries in
#    Studio can be used as an LTI 1.3 tool by external LTI platforms.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-08-17
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/27411
ENABLE_CONTENT_LIBRARIES_LTI_TOOL = False

# Toggle course entrance exams feature
ENTRANCE_EXAMS = False

# Enable the courseware search functionality
ENABLE_COURSEWARE_INDEX = False

# Enable content libraries (modulestore) search functionality
ENABLE_LIBRARY_INDEX = False

# .. toggle_name: settings.ALLOW_COURSE_RERUNS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: This will allow staff member to re-run the course from the studio home page and will
#   always use the split modulestore. When this is set to False, the Re-run Course link will not be available on
#   the studio home page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-02-13
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6965
ALLOW_COURSE_RERUNS = True

# Whether archived courses (courses with end dates in the past) should be
# shown in Studio in a separate list.
ENABLE_SEPARATE_ARCHIVED_COURSES = True

ENABLE_GRADE_DOWNLOADS = True

ENABLE_DISCUSSION_HOME_PANEL = True

ENABLE_COUNTRY_ACCESS = False
ENABLE_CREDIT_API = False

### ORA Feature Flags ###

# .. toggle_name: settings.DEPRECATE_OLD_COURSE_KEYS_IN_STUDIO
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Warn about removing support for deprecated course keys.
#      To enable, set to True.
#      To disable, set to False.
#      To enable with a custom support deadline, set to an ISO-8601 date string:
#        eg: '2020-09-01'
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-06-12
# .. toggle_target_removal_date: 2021-04-01
# .. toggle_warning: This can be removed once support is removed for deprecated
#   course keys.
# .. toggle_tickets: https://openedx.atlassian.net/browse/DEPR-58
DEPRECATE_OLD_COURSE_KEYS_IN_STUDIO = True

# .. toggle_name: settings.DISABLE_COURSE_CREATION
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If set to True, it disables the course creation functionality and hides the "New Course"
#   button in studio.
#   It is important to note that the value of this flag only affects if the user doesn't have a staff role,
#   otherwise the course creation functionality will work as it should.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-12-02
# .. toggle_warning: Another toggle DISABLE_LIBRARY_CREATION overrides DISABLE_COURSE_CREATION, if present.
DISABLE_COURSE_CREATION = False

# .. toggle_name: settings.ENABLE_LTI_PII_ACKNOWLEDGEMENT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enables the lti pii acknowledgement feature for a course
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-10
# .. toggle_target_removal_date: None
# .. toggle_tickets: 'https://2u-internal.atlassian.net/browse/MST-2055'
ENABLE_LTI_PII_ACKNOWLEDGEMENT = False

# .. toggle_name: settings.DISABLE_ADVANCED_SETTINGS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to `True` to disable the advanced settings page in Studio for all users except those
#   having `is_superuser` or `is_staff` set to `True`.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-03-31
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/32015
DISABLE_ADVANCED_SETTINGS = False

# .. toggle_name: settings.ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enables sending xblock lifecycle events over the event bus. Used to create the
#   EVENT_BUS_PRODUCER_CONFIG setting
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2023-10-10
# .. toggle_target_removal_date: 2023-10-12
# .. toggle_warning: The default may be changed in a later release. See
#   https://github.com/openedx/openedx-events/issues/265
# .. toggle_tickets: https://github.com/edx/edx-arch-experiments/issues/381
ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS = False

# .. toggle_name: settings.ENABLE_HIDE_FROM_TOC_UI
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, exposes hide_from_toc xblock attribute so course authors can configure it as
#  a section visibility option in Studio.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-02-29
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/33952
ENABLE_HIDE_FROM_TOC_UI = False

# .. toggle_name: settings.IN_CONTEXT_DISCUSSION_ENABLED_DEFAULT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Set to False to disable in-context discussion for units by default.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-09-02
IN_CONTEXT_DISCUSSION_ENABLED_DEFAULT = True

# .. toggle_name: ENABLE_COPPA_COMPLIANCE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When True, inforces COPPA compliance and removes YOB field from registration form and accounnt
# .. settings page. Also hide YOB banner from profile page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-10-27
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/VAN-622'
ENABLE_COPPA_COMPLIANCE = False

ENABLE_JASMINE = False

MARKETING_EMAILS_OPT_IN = False

############################# MICROFRONTENDS ###################################
COURSE_AUTHORING_MICROFRONTEND_URL = None

############################# SOCIAL MEDIA SHARING #############################
SOCIAL_SHARING_SETTINGS = {
    # Note: Ensure 'CUSTOM_COURSE_URLS' has a matching value in lms/envs/common.py
    'CUSTOM_COURSE_URLS': False,
    'DASHBOARD_FACEBOOK': False,
    'CERTIFICATE_FACEBOOK': False,
    'CERTIFICATE_TWITTER': False,
    'DASHBOARD_TWITTER': False
}

############################# SET PATH INFORMATION #############################
PROJECT_ROOT = path(__file__).abspath().dirname().dirname()  # /edx-platform/cms
REPO_ROOT = PROJECT_ROOT.dirname()
COMMON_ROOT = REPO_ROOT / "common"
OPENEDX_ROOT = REPO_ROOT / "openedx"
CMS_ROOT = REPO_ROOT / "cms"
LMS_ROOT = REPO_ROOT / "lms"
ENV_ROOT = REPO_ROOT.dirname()  # virtualenv dir /edx-platform is in
COURSES_ROOT = ENV_ROOT / "data"
XMODULE_ROOT = REPO_ROOT / "xmodule"

GITHUB_REPO_ROOT = ENV_ROOT / "data"

# For geolocation ip database
GEOIP_PATH = REPO_ROOT / "common/static/data/geoip/GeoLite2-Country.mmdb"

DATA_DIR = COURSES_ROOT

######################## BRANCH.IO ###########################
BRANCH_IO_KEY = ''

######################## GOOGLE ANALYTICS ###########################
GOOGLE_ANALYTICS_ACCOUNT = None

######################## HOTJAR ###########################
HOTJAR_ID = 00000

############################# TEMPLATE CONFIGURATION #############################
# Mako templating
import tempfile
MAKO_MODULE_DIR = os.path.join(tempfile.gettempdir(), 'mako_cms')
MAKO_TEMPLATE_DIRS_BASE = [
    PROJECT_ROOT / 'templates',
    COMMON_ROOT / 'templates',
    COMMON_ROOT / 'djangoapps' / 'pipeline_mako' / 'templates',
    COMMON_ROOT / 'static',  # required to statically include common Underscore templates
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'cors_csrf' / 'templates',
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'dark_lang' / 'templates',
    OPENEDX_ROOT / 'core' / 'lib' / 'license' / 'templates',
    CMS_ROOT / 'djangoapps' / 'pipeline_js' / 'templates',
]

CONTEXT_PROCESSORS = (
    'django.template.context_processors.request',
    'django.template.context_processors.static',
    'django.contrib.messages.context_processors.messages',
    'django.template.context_processors.i18n',
    'django.contrib.auth.context_processors.auth',  # this is required for admin
    'django.template.context_processors.csrf',
    'help_tokens.context_processor',
    'openedx.core.djangoapps.site_configuration.context_processors.configuration_context',
)

# Django templating
TEMPLATES = [
    {
        'NAME': 'django',
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Don't look for template source files inside installed applications.
        'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        'DIRS': Derived(make_mako_template_dirs),
        # Options specific to this backend.
        'OPTIONS': {
            'loaders': (
                # We have to use mako-aware template loaders to be able to include
                # mako templates inside django templates (such as main_django.html).
                'openedx.core.djangoapps.theming.template_loaders.ThemeTemplateLoader',
                'common.djangoapps.edxmako.makoloader.MakoFilesystemLoader',
                'common.djangoapps.edxmako.makoloader.MakoAppDirectoriesLoader',
            ),
            'context_processors': CONTEXT_PROCESSORS,
            # Change 'debug' in your environment settings files - not here.
            'debug': False
        }
    },
    {
        'NAME': 'mako',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        'APP_DIRS': False,
        'DIRS': Derived(make_mako_template_dirs),
        'OPTIONS': {
            'context_processors': CONTEXT_PROCESSORS,
            'debug': False,
        }
    },
    {
        # This separate copy of the Mako backend is used to render previews using the LMS templates
        'NAME': 'preview',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        'APP_DIRS': False,
        'DIRS': lms.envs.common.MAKO_TEMPLATE_DIRS_BASE,
        'OPTIONS': {
            'context_processors': CONTEXT_PROCESSORS,
            'debug': False,
            'namespace': 'lms.main',
        }
    },
]
DEFAULT_TEMPLATE_ENGINE = TEMPLATES[0]

#################################### AWS #######################################
AWS_SECURITY_TOKEN = None

##############################################################################

# use the ratelimit backend to prevent brute force attacks
AUTHENTICATION_BACKENDS = [
    'auth_backends.backends.EdXOAuth2',
    'rules.permissions.ObjectPermissionBackend',
    'openedx.core.djangoapps.content_libraries.auth.LtiAuthenticationBackend',
    'django.contrib.auth.backends.AllowAllUsersModelBackend',
    'bridgekeeper.backends.RulePermissionBackend',
]

LMS_BASE = None

# Use LMS SSO for login, once enabled by setting LOGIN_URL (see docs/guides/studio_oauth.rst)
SOCIAL_AUTH_STRATEGY = 'auth_backends.strategies.EdxDjangoStrategy'
LOGIN_REDIRECT_URL = EDX_ROOT_URL + '/home/'
LOGIN_URL = '/login/'
FRONTEND_LOGIN_URL = LOGIN_URL
# Warning: Must have trailing slash to activate correct logout view
# (auth_backends, not LMS user_authn)
FRONTEND_LOGOUT_URL = '/logout/'
FRONTEND_REGISTER_URL = Derived(lambda settings: settings.LMS_ROOT_URL + '/register')

ENTERPRISE_API_URL = Derived(lambda settings: settings.LMS_INTERNAL_ROOT_URL + '/enterprise/api/v1/')
ENTERPRISE_CONSENT_API_URL = Derived(lambda settings: settings.LMS_INTERNAL_ROOT_URL + '/consent/api/v1/')

# Public domain name of Studio (should be resolvable from the end-user's browser)
CMS_BASE = None
CMS_ROOT_URL = None

MAINTENANCE_BANNER_TEXT = 'Sample banner message'

WIKI_ENABLED = True

CERT_QUEUE = 'certificates'

################################# Middleware ###################################

MIDDLEWARE = [
    'openedx.core.lib.x_forwarded_for.middleware.XForwardedForMiddleware',
    'edx_django_utils.security.csp.middleware.content_security_policy_middleware',

    'crum.CurrentRequestUserMiddleware',

    # Resets the request cache.
    'edx_django_utils.cache.middleware.RequestCacheMiddleware',

    # Various monitoring middleware
    'edx_django_utils.monitoring.CookieMonitoringMiddleware',
    'edx_django_utils.monitoring.DeploymentMonitoringMiddleware',
    'edx_django_utils.monitoring.FrontendMonitoringMiddleware',
    'edx_django_utils.monitoring.MonitoringMemoryMiddleware',

    'openedx.core.djangoapps.header_control.middleware.HeaderControlMiddleware',
    'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sites.middleware.CurrentSiteMiddleware',

    # CORS and CSRF
    'django.middleware.csrf.CsrfViewMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'openedx.core.djangoapps.cors_csrf.middleware.CorsCSRFMiddleware',
    'openedx.core.djangoapps.cors_csrf.middleware.CsrfCrossDomainCookieMiddleware',

    # JWT auth
    'edx_rest_framework_extensions.auth.jwt.middleware.JwtAuthCookieMiddleware',

    # Allows us to define redirects via Django admin
    'django_sites_extensions.middleware.RedirectMiddleware',

    # Instead of SessionMiddleware, we use a more secure version
    # 'django.contrib.sessions.middleware.SessionMiddleware',
    'openedx.core.djangoapps.safe_sessions.middleware.SafeSessionMiddleware',

    'method_override.middleware.MethodOverrideMiddleware',

    # Instead of AuthenticationMiddleware, we use a cache-backed version
    'openedx.core.djangoapps.cache_toolbox.middleware.CacheBackedAuthenticationMiddleware',

    'common.djangoapps.student.middleware.UserStandingMiddleware',

    'django.contrib.messages.middleware.MessageMiddleware',
    'common.djangoapps.track.middleware.TrackMiddleware',

    # This is used to set or update the user language preferences.
    'openedx.core.djangoapps.lang_pref.middleware.LanguagePreferenceMiddleware',

    # Allows us to dark-launch particular languages
    'openedx.core.djangoapps.dark_lang.middleware.DarkLangMiddleware',

    'openedx.core.djangoapps.embargo.middleware.EmbargoMiddleware',

    # Detects user-requested locale from 'accept-language' header in http request
    'django.middleware.locale.LocaleMiddleware',

    'codejail.django_integration.ConfigureCodeJailMiddleware',

    # for expiring inactive sessions
    'openedx.core.djangoapps.session_inactivity_timeout.middleware.SessionInactivityTimeout',

    'openedx.core.djangoapps.theming.middleware.CurrentSiteThemeMiddleware',

    # use Django built in clickjacking protection
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    'waffle.middleware.WaffleMiddleware',

    # Enables force_django_cache_miss functionality for TieredCache.
    'edx_django_utils.cache.middleware.TieredCacheMiddleware',

    # Adds monitoring attributes to requests.
    'edx_rest_framework_extensions.middleware.RequestCustomAttributesMiddleware',

    'edx_rest_framework_extensions.auth.jwt.middleware.EnsureJWTAuthSettingsMiddleware',

    # Handles automatically storing user ids in django-simple-history tables when possible.
    'simple_history.middleware.HistoryRequestMiddleware',

    # This must be last so that it runs first in the process_response chain
    'openedx.core.djangoapps.site_configuration.middleware.SessionCookieDomainOverrideMiddleware',
]

EXTRA_MIDDLEWARE_CLASSES = []

############# XBlock Configuration ##########

# Import after sys.path fixup
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.x_module import XModuleMixin, ResourceTemplates

# These are the Mixins that will be added to every Blocklike upon instantiation.
# DO NOT EXPAND THIS LIST!! We want it eventually to be EMPTY. Why? Because dynamically adding functions/behaviors to
# objects at runtime is confusing for both developers and static tooling (pylint/mypy). Instead...
#  - to add special Blocklike behaviors just for your site: override `XBLOCK_EXTRA_MIXINS` with your own XBlockMixins.
#  - to add new functionality to all Blocklikes: add it to the base Blocklike class in the core openedx/XBlock repo.
XBLOCK_MIXINS = (
    # TODO: For each of these, either
    #  (a) merge their functionality into the base Blocklike class, or
    #  (b) refactor their functionality out of the Blocklike objects and into the edx-platform block runtimes.
    LmsBlockMixin,
    InheritanceMixin,
    ResourceTemplates,
    XModuleMixin,
    EditInfoMixin,
    UpstreamSyncMixin,  # Should be above AuthoringMixin for UpstreamSyncMixin.editor_saved to take effect
    AuthoringMixin,
)

############################ ORA 2 ############################################

# By default, don't use a file prefix
ORA2_FILE_PREFIX = 'default_env-default_deployment/ora2'

############################ Modulestore Configuration ################################

DOC_STORE_CONFIG = {
    'db': 'edxapp',
    'host': 'localhost',
    'replicaSet': '',
    'user': 'edxapp',
    'port': 27017,
    'collection': 'modulestore',
    'ssl': False,
    # https://api.mongodb.com/python/2.9.1/api/pymongo/mongo_client.html#module-pymongo.mongo_client
    # default is never timeout while the connection is open,
    #this means it needs to explicitly close raising pymongo.errors.NetworkTimeout
    'socketTimeoutMS': 6000,
    'connectTimeoutMS': 2000,  # default is 20000, I believe raises pymongo.errors.ConnectionFailure
    # Not setting waitQueueTimeoutMS and waitQueueMultiple since pymongo defaults to nobody being allowed to wait
    'auth_source': None,
    'read_preference': 'PRIMARY'
    # If 'asset_collection' defined, it'll be used
    # as the collection name for asset metadata.
    # Otherwise, a default collection name will be used.
}

CONTENTSTORE = {
    'ENGINE': 'xmodule.contentstore.mongo.MongoContentStore',
    # connection strings are duplicated temporarily for
    # backward compatibility
    'OPTIONS': {
        'db': 'edxapp',
        'host': 'localhost',
        'password': 'password',
        'port': 27017,
        'user': 'edxapp',
        'ssl': False,
        'auth_source': None
    },
    'ADDITIONAL_OPTIONS': {},
    'DOC_STORE_CONFIG': DOC_STORE_CONFIG
}

MODULESTORE_BRANCH = 'draft-preferred'

MODULESTORE = {
    'default': {
        'ENGINE': 'xmodule.modulestore.mixed.MixedModuleStore',
        'OPTIONS': {
            'mappings': {},
            'stores': [
                {
                    'NAME': 'split',
                    'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': DATA_DIR,
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                },
                {
                    'NAME': 'draft',
                    'ENGINE': 'xmodule.modulestore.mongo.DraftMongoModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': DATA_DIR,
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                }
            ]
        }
    }
}

# Modulestore-level field override providers. These field override providers don't
# require student context.
MODULESTORE_FIELD_OVERRIDE_PROVIDERS = ()

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
DEFAULT_HASHING_ALGORITHM = 'sha256'

#################### Python sandbox ############################################

CODE_JAIL = {
    # from https://github.com/openedx/codejail/blob/master/codejail/django_integration.py#L24, '' should be same as None
    'python_bin': '/edx/app/edxapp/venvs/edxapp-sandbox/bin/python',
    # User to run as in the sandbox.
    'user': 'sandbox',

    # Configurable limits.
    'limits': {
        # How many CPU seconds can jailed code use?
        'CPU': 1,
        # Limit the memory of the jailed process to something high but not
        # infinite (512MiB in bytes)
        'VMEM': 536870912,
        # Time in seconds that the jailed process has to run.
        'REALTIME': 3,
        'PROXY': 0,
        # Needs to be non-zero so that jailed code can use it as their temp directory.(1MiB in bytes)
        'FSIZE': 1048576,
    },

    # Overrides to default configurable 'limits' (above).
    # Keys should be course run ids.
    # Values should be dictionaries that look like 'limits'.
    "limit_overrides": {},
}

############################ DJANGO_BUILTINS ################################

ROOT_URLCONF = 'cms.urls'

COURSE_IMPORT_EXPORT_BUCKET = ''
COURSE_METADATA_EXPORT_BUCKET = ''

ALTERNATE_WORKER_QUEUES = 'lms'

# .. setting_name: GIT_REPO_EXPORT_DIR
# .. setting_default: '/edx/var/edxapp/export_course_repos'
# .. setting_description: When courses are exported to git, either with the export_git management command or the git
#   export view from the studio (when settings.ENABLE_EXPORT_GIT is True), they are stored in this directory, which
#   must exist at the time of the export.
GIT_REPO_EXPORT_DIR = '/edx/var/edxapp/export_course_repos'
# .. setting_name: GIT_EXPORT_DEFAULT_IDENT
# .. setting_default: {'name': 'STUDIO_EXPORT_TO_GIT', 'email': 'STUDIO_EXPORT_TO_GIT@example.com'}
# .. setting_description: When courses are exported to git, commits are signed with this name/email git identity.
GIT_EXPORT_DEFAULT_IDENT = {
    'name': 'STUDIO_EXPORT_TO_GIT',
    'email': 'STUDIO_EXPORT_TO_GIT@example.com'
}

# Email
TECH_SUPPORT_EMAIL = 'technical@example.com'
EMAIL_FILE_PATH = Derived(lambda settings: path(settings.DATA_DIR) / "emails" / "studio")
DEFAULT_FROM_EMAIL = 'registration@example.com'
DEFAULT_FEEDBACK_EMAIL = 'feedback@example.com'
TECH_SUPPORT_EMAIL = 'technical@example.com'
CONTACT_EMAIL = 'info@example.com'
BUGS_EMAIL = 'bugs@example.com'
SERVER_EMAIL = 'devops@example.com'
UNIVERSITY_EMAIL = 'university@example.com'
PRESS_EMAIL = 'press@example.com'

# Static content
STATIC_URL = '/static/studio/'
STATIC_ROOT = os.environ.get('STATIC_ROOT_CMS', ENV_ROOT / 'staticfiles' / 'studio')

STATICFILES_DIRS = [
    COMMON_ROOT / "static",
    PROJECT_ROOT / "static",
    # Temporarily adding the following static path as we are migrating the built-in blocks' Sass to vanilla CSS.
    # Once all of the built-in blocks are extracted from edx-platform, we can remove this static path.
    # Relevant ticket: https://github.com/openedx/edx-platform/issues/35300
    XMODULE_ROOT / "static",
]

COURSE_IMPORT_EXPORT_STORAGE = 'django.core.files.storage.FileSystemStorage'
COURSE_METADATA_EXPORT_STORAGE = 'django.core.files.storage.FileSystemStorage'

STATICI18N_ROOT = PROJECT_ROOT / "static"

##### custom vendor plugin variables #####

############################### PIPELINE #######################################

PIPELINE = {
    'PIPELINE_ENABLED': True,
    # Don't use compression by default
    'CSS_COMPRESSOR': None,
    'JS_COMPRESSOR': None,
    # Don't wrap JavaScript as there is code that depends upon updating the global namespace
    'DISABLE_WRAPPER': True,
    # Specify the UglifyJS binary to use
    'UGLIFYJS_BINARY': 'node_modules/.bin/uglifyjs',
    'COMPILERS': (),
    'YUI_BINARY': 'yui-compressor',
}

STATICFILES_STORAGE_KWARGS = {}

# List of finder classes that know how to find static files in various locations.
# Note: the pipeline finder is included to be able to discover optimized files
STATICFILES_FINDERS = [
    'openedx.core.djangoapps.theming.finders.ThemeFilesFinder',
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'openedx.core.lib.xblock_pipeline.finder.XBlockPipelineFinder',
    'pipeline.finders.PipelineFinder',
]

PIPELINE['STYLESHEETS'] = {
    'style-vendor': {
        'source_filenames': [
            'css/vendor/normalize.css',
            'css/vendor/font-awesome.css',
            'css/vendor/html5-input-polyfills/number-polyfill.css',
            'js/vendor/CodeMirror/codemirror.css',
            'css/vendor/ui-lightness/jquery-ui-1.8.22.custom.css',
            'css/vendor/jquery.qtip.min.css',
            'js/vendor/markitup/skins/simple/style.css',
            'js/vendor/markitup/sets/wiki/style.css',
        ],
        'output_filename': 'css/cms-style-vendor.css',
    },
    'style-vendor-tinymce-content': {
        'source_filenames': [
            'css/tinymce-studio-content-fonts.css',
            'js/vendor/tinymce/js/tinymce/skins/ui/studio-tmce5/content.min.css',
            'css/tinymce-studio-content.css'
        ],
        'output_filename': 'css/cms-style-vendor-tinymce-content.css',
    },
    'style-vendor-tinymce-skin': {
        'source_filenames': [
            'js/vendor/tinymce/js/tinymce/skins/ui/studio-tmce5/skin.min.css'
        ],
        'output_filename': 'css/cms-style-vendor-tinymce-skin.css',
    },
    'style-main-v1': {
        'source_filenames': [
            'css/studio-main-v1.css',
        ],
        'output_filename': 'css/studio-main-v1.css',
    },
    'style-main-v1-rtl': {
        'source_filenames': [
            'css/studio-main-v1-rtl.css',
        ],
        'output_filename': 'css/studio-main-v1-rtl.css',
    },
    'style-xmodule-annotations': {
        'source_filenames': [
            'css/vendor/ova/annotator.css',
            'css/vendor/ova/edx-annotator.css',
            'css/vendor/ova/video-js.min.css',
            'css/vendor/ova/rangeslider.css',
            'css/vendor/ova/share-annotator.css',
            'css/vendor/ova/richText-annotator.css',
            'css/vendor/ova/tags-annotator.css',
            'css/vendor/ova/flagging-annotator.css',
            'css/vendor/ova/diacritic-annotator.css',
            'css/vendor/ova/grouping-annotator.css',
            'css/vendor/ova/ova.css',
            'js/vendor/ova/catch/css/main.css'
        ],
        'output_filename': 'css/cms-style-xmodule-annotations.css',
    },
    'course-unit-mfe-iframe-bundle': {
        'source_filenames': [
            'css/course-unit-mfe-iframe-bundle.css',
        ],
        'output_filename': 'css/course-unit-mfe-iframe-bundle.css',
    },
}

base_vendor_js = [
    'js/src/utility.js',
    'js/src/logger.js',
    'common/js/vendor/jquery.js',
    'common/js/vendor/jquery-migrate.js',
    'js/vendor/jquery.cookie.js',
    'js/vendor/url.min.js',
    'common/js/vendor/underscore.js',
    'common/js/vendor/underscore.string.js',
    'common/js/vendor/backbone.js',
    'js/vendor/URI.min.js',

    # Make some edX UI Toolkit utilities available in the global "edx" namespace
    'edx-ui-toolkit/js/utils/global-loader.js',
    'edx-ui-toolkit/js/utils/string-utils.js',
    'edx-ui-toolkit/js/utils/html-utils.js',

    # Here we were loading Bootstrap and supporting libraries, but it no longer seems to be needed for any Studio UI.
    # 'common/js/vendor/bootstrap.bundle.js',

    # Finally load RequireJS
    'common/js/vendor/require.js'
]

# test_order: Determines the position of this chunk of javascript on
# the jasmine test page
PIPELINE['JAVASCRIPT'] = {
    'base_vendor': {
        'source_filenames': base_vendor_js,
        'output_filename': 'js/cms-base-vendor.js',
    },
}

STATICFILES_IGNORE_PATTERNS = (
    "*.py",
    "*.pyc",

    # It would be nice if we could do, for example, "**/*.scss",
    # but these strings get passed down to the `fnmatch` module,
    # which doesn't support that. :(
    # http://docs.python.org/2/library/fnmatch.html
    "sass/*.scss",
    "sass/*/*.scss",
    "sass/*/*/*.scss",
    "sass/*/*/*/*.scss",

    # Ignore tests
    "spec",
    "spec_helpers",

    # Symlinks used by js-test-tool
    "xmodule_js",
    "common_static",
)

################################# DJANGO-REQUIRE ###############################

# The name of a build profile to use for your project, relative to REQUIRE_BASE_URL.
# A sensible value would be 'app.build.js'. Leave blank to use the built-in default build profile.
# Set to False to disable running the default profile (e.g. if only using it to build Standalone
# Modules)
REQUIRE_BUILD_PROFILE = "cms/js/build.js"

# The name of the require.js script used by your project, relative to REQUIRE_BASE_URL.
REQUIRE_JS = "js/vendor/requiresjs/require.js"

########################## DJANGO WEBPACK LOADER ##############################

WEBPACK_LOADER = {
    'DEFAULT': {
        'BUNDLE_DIR_NAME': 'bundles/',
        'STATS_FILE': os.path.join(STATIC_ROOT, 'webpack-stats.json'),
    },
    'WORKERS': {
        'BUNDLE_DIR_NAME': 'bundles/',
        'STATS_FILE': os.path.join(STATIC_ROOT, 'webpack-worker-stats.json')
    }
}

############################ SERVICE_VARIANT ##################################

# SERVICE_VARIANT specifies name of the variant used, which decides what JSON
# configuration files are read during startup.
SERVICE_VARIANT = os.environ.get('SERVICE_VARIANT', 'cms')

# CONFIG_PREFIX specifies the prefix of the JSON configuration files,
# based on the service variant. If no variant is use, don't use a
# prefix.
CONFIG_PREFIX = SERVICE_VARIANT + "." if SERVICE_VARIANT else ""


################################# CELERY ######################################

# Name the exchange and queues for each variant

QUEUE_VARIANT = CONFIG_PREFIX.lower()

CELERY_DEFAULT_EXCHANGE = f'edx.{QUEUE_VARIANT}core'

HIGH_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.high'
DEFAULT_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.default'
LOW_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.low'

CELERY_DEFAULT_QUEUE = DEFAULT_PRIORITY_QUEUE
CELERY_DEFAULT_ROUTING_KEY = DEFAULT_PRIORITY_QUEUE

CELERY_QUEUES = {
    HIGH_PRIORITY_QUEUE: {},
    DEFAULT_PRIORITY_QUEUE: {},
    LOW_PRIORITY_QUEUE: {},
}

# Queues configuration

CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION = True

BROKER_USE_SSL = Derived(lambda settings: settings.CELERY_BROKER_USE_SSL)

CELERY_ALWAYS_EAGER = False

############################## HEARTBEAT ######################################

HEARTBEAT_CELERY_ROUTING_KEY = HIGH_PRIORITY_QUEUE

############################## Video ##########################################

# Additional languages that should be supported for video transcripts, not included in ALL_LANGUAGES
EXTENDED_VIDEO_TRANSCRIPT_LANGUAGES = []

############################# SETTINGS FOR VIDEO UPLOAD PIPELINE #############################

VIDEO_UPLOAD_PIPELINE = {
    'VEM_S3_BUCKET': '',
    'BUCKET': '',
    'ROOT_PATH': '',
    'CONCURRENT_UPLOAD_LIMIT': 4,
}

############################# SETTINGS FOR VIDEO AUDIO DESCRIPTION #############################

VIDEO_AUDIO_DESCRIPTION_SETTINGS = dict(
    VIDEO_AUDIO_DESCRIPTION_MAX_BYTES=200 * 1024 * 1024,  # 200 MB
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
    ),
    DIRECTORY_PREFIX='audio-descriptions/',
    BASE_URL=MEDIA_URL,
)

############################ APPS #####################################

# The order of INSTALLED_APPS is important, when adding new apps here
# remember to check that you are not creating new
# RemovedInDjango19Warnings in the test logs.
INSTALLED_APPS = [
    # Standard apps
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.humanize',
    'django.contrib.redirects',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',

    # Tweaked version of django.contrib.staticfiles
    'openedx.core.djangoapps.staticfiles.apps.EdxPlatformStaticFilesConfig',

    'django_celery_results',

    'method_override',

    # Common Initialization
    'openedx.core.djangoapps.common_initialization.apps.CommonInitializationConfig',

    # Common views
    'openedx.core.djangoapps.common_views',

    # API access administration
    'openedx.core.djangoapps.api_admin',

    # CORS and cross-domain CSRF
    'corsheaders',
    'openedx.core.djangoapps.cors_csrf',

    # Provides the 'django_markup' template library so we can use 'interpolate_html' in django templates
    'xss_utils',

    # History tables
    'simple_history',

    # Database-backed configuration
    'config_models',
    'openedx.core.djangoapps.config_model_utils',
    'waffle',

    # Monitor the status of services
    'openedx.core.djangoapps.service_status',

    # Video block configs (This will be moved to Video once it becomes an XBlock)
    'openedx.core.djangoapps.video_config',

    # edX Video Pipeline integration
    'openedx.core.djangoapps.video_pipeline',

    # For CMS
    'cms.djangoapps.contentstore.apps.ContentstoreConfig',
    'common.djangoapps.split_modulestore_django.apps.SplitModulestoreDjangoBackendAppConfig',

    'openedx.core.djangoapps.contentserver',
    'cms.djangoapps.course_creators',
    'common.djangoapps.student.apps.StudentConfig',  # misleading name due to sharing with lms
    'openedx.core.djangoapps.course_groups',  # not used in cms (yet), but tests run
    'cms.djangoapps.xblock_config.apps.XBlockConfig',
    'cms.djangoapps.export_course_metadata.apps.ExportCourseMetadataConfig',
    'cms.djangoapps.modulestore_migrator',

    # New (Learning-Core-based) XBlock runtime
    'openedx.core.djangoapps.xblock.apps.StudioXBlockAppConfig',

    'openedx.core.djangoapps.util.apps.UtilConfig',

    # Tracking
    'common.djangoapps.track',
    'eventtracking.django.apps.EventTrackingConfig',

    # For asset pipelining
    'common.djangoapps.edxmako.apps.EdxMakoConfig',
    'pipeline',
    'common.djangoapps.static_replace',
    'require',
    'webpack_loader',

    # Site configuration for theming and behavioral modification
    'openedx.core.djangoapps.site_configuration',

    # Ability to detect and special-case crawler behavior
    'openedx.core.djangoapps.crawlers',

    # Discussion
    'openedx.core.djangoapps.django_comment_common',

    # Notifications
    'openedx.core.djangoapps.notifications',

    # for course creator table
    'django.contrib.admin',

    # for managing course modes
    'common.djangoapps.course_modes.apps.CourseModesConfig',

    # Verified Track Content Cohorting (Beta feature that will hopefully be removed)
    'openedx.core.djangoapps.verified_track_content',

    # Dark-launching languages
    'openedx.core.djangoapps.dark_lang',

    #
    # User preferences
    'wiki',
    'django_notify',
    'lms.djangoapps.course_wiki',  # Our customizations
    'mptt',
    'sekizai',
    'openedx.core.djangoapps.user_api',

    # Country embargo support
    'openedx.core.djangoapps.embargo',

    # Course action state
    'common.djangoapps.course_action_state',

    'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig',
    'openedx.core.djangoapps.content.block_structure.apps.BlockStructureConfig',

    # edx-milestones service
    'milestones',

    # Credit courses
    'openedx.core.djangoapps.credit.apps.CreditConfig',

    'common.djangoapps.xblock_django',

    # Catalog integration
    'openedx.core.djangoapps.catalog',

    # Programs support
    'openedx.core.djangoapps.programs.apps.ProgramsConfig',

    # django-oauth-toolkit
    'oauth2_provider',

    # These are apps that aren't strictly needed by Studio, but are imported by
    # other apps that are.  Django 1.8 wants to have imported models supported
    # by installed apps.
    'openedx.core.djangoapps.oauth_dispatch.apps.OAuthDispatchAppConfig',
    'lms.djangoapps.courseware',
    'lms.djangoapps.coursewarehistoryextended',
    'lms.djangoapps.survey.apps.SurveyConfig',
    'lms.djangoapps.verify_student.apps.VerifyStudentConfig',
    'completion',

    # System Wide Roles
    'openedx.core.djangoapps.system_wide_roles',

    # Static i18n support
    'statici18n',

    # Tagging
    'cms.lib.xblock.tagging',

    # Enables default site and redirects
    'django_sites_extensions',

    # additional release utilities to ease automation
    'release_util',

    # rule-based authorization
    'rules.apps.AutodiscoverRulesConfig',
    'bridgekeeper',

    # management of user-triggered async tasks (course import/export, etc.)
    'user_tasks',

    # CMS specific user task handling
    'cms.djangoapps.cms_user_tasks.apps.CmsUserTasksConfig',

    # Unusual migrations
    'common.djangoapps.database_fixups',

    # Customized celery tasks, including persisting failed tasks so they can
    # be retried
    'celery_utils',

    # Waffle related utilities
    'openedx.core.djangoapps.waffle_utils',

    # DRF filters
    'django_filters',
    'cms.djangoapps.api',

    # edx-drf-extensions
    'csrf.apps.CsrfAppConfig',  # Enables frontend apps to retrieve CSRF tokens.

    # Entitlements, used in openedx tests
    'common.djangoapps.entitlements',

    # Asset management for mako templates
    'common.djangoapps.pipeline_mako',

    # API Documentation
    'drf_yasg',

    # Tagging
    'openedx_tagging.core.tagging.apps.TaggingConfig',
    'openedx.core.djangoapps.content_tagging',

    # Search
    'openedx.core.djangoapps.content.search',

    # For Programs API
    'lms.djangoapps.program_enrollments',

    'openedx.features.course_duration_limits',
    'openedx.features.content_type_gating',
    'openedx.features.discounts',
    'openedx.features.effort_estimation',
    'lms.djangoapps.experiments',

    'openedx.core.djangoapps.external_user_ids',
    # so sample_task is available to celery workers
    'openedx.core.djangoapps.heartbeat',

    # signal handlers to capture course dates into edx-when
    'openedx.core.djangoapps.course_date_signals',

    # Management of per-user schedules
    'openedx.core.djangoapps.schedules',
    'rest_framework_jwt',

    # Learning Sequence Navigation
    'openedx.core.djangoapps.content.learning_sequences.apps.LearningSequencesConfig',

    # Database-backed Organizations App (http://github.com/openedx/edx-organizations)
    'organizations',

    # User and group management via edx-django-utils
    'edx_django_utils.user',

    # Allow Studio to use LMS for SSO
    'social_django',

    # Content Library LTI 1.3 Support.
    'pylti1p3.contrib.django.lti1p3_tool_config',

    # For edx ace template tags
    'edx_ace',

    # alternative swagger generator for CMS API
    'drf_spectacular',

    'openedx_events',

    # Learning Core Apps, used by v2 content libraries (content_libraries app)
    "openedx_learning.apps.authoring.collections",
    "openedx_learning.apps.authoring.components",
    "openedx_learning.apps.authoring.contents",
    "openedx_learning.apps.authoring.publishing",
    "openedx_learning.apps.authoring.units",
    "openedx_learning.apps.authoring.subsections",
    "openedx_learning.apps.authoring.sections",
]


################# EDX MARKETING SITE ##################################

MKTG_URL_LINK_MAP = {}

ID_VERIFICATION_SUPPORT_LINK = ''
PASSWORD_RESET_SUPPORT_LINK = ''
ACTIVATION_EMAIL_SUPPORT_LINK = ''
LOGIN_ISSUE_SUPPORT_LINK = ''

############################## EVENT TRACKING #################################

TRACK_MAX_EVENT = 50000

TRACKING_BACKENDS = {
    'logger': {
        'ENGINE': 'common.djangoapps.track.backends.logger.LoggerBackend',
        'OPTIONS': {
            'name': 'tracking'
        }
    }
}

# We're already logging events, and we don't want to capture user
# names/passwords.  Heartbeat events are likely not interesting.
TRACKING_IGNORE_URL_PATTERNS = [r'^/event', r'^/login', r'^/heartbeat']

EVENT_TRACKING_ENABLED = True
EVENT_TRACKING_BACKENDS = {
    'tracking_logs': {
        'ENGINE': 'eventtracking.backends.routing.RoutingBackend',
        'OPTIONS': {
            'backends': {
                'logger': {
                    'ENGINE': 'eventtracking.backends.logger.LoggerBackend',
                    'OPTIONS': {
                        'name': 'tracking',
                        'max_event_size': TRACK_MAX_EVENT,
                    }
                }
            },
            'processors': [
                {'ENGINE': 'common.djangoapps.track.shim.LegacyFieldMappingProcessor'},
                {'ENGINE': 'common.djangoapps.track.shim.PrefixedEventProcessor'}
            ]
        }
    },
    'segmentio': {
        'ENGINE': 'eventtracking.backends.routing.RoutingBackend',
        'OPTIONS': {
            'backends': {
                'segment': {'ENGINE': 'eventtracking.backends.segment.SegmentBackend'}
            },
            'processors': [
                {
                    'ENGINE': 'eventtracking.processors.whitelist.NameWhitelistProcessor',
                    'OPTIONS': {
                        'whitelist': []
                    }
                },
                {
                    'ENGINE': 'common.djangoapps.track.shim.GoogleAnalyticsProcessor'
                }
            ]
        }
    }
}
EVENT_TRACKING_PROCESSORS = []

EVENT_TRACKING_SEGMENTIO_EMIT_WHITELIST = []

##### ACCOUNT LOCKOUT DEFAULT PARAMETERS #####
MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED = 6
MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS = 30 * 60


### Apps only installed in some instances
# The order of INSTALLED_APPS matters, so this tuple is the app name and the item in INSTALLED_APPS
# that this app should be inserted *before*. A None here means it should be appended to the list.
OPTIONAL_APPS = (
    ('problem_builder', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('edx_sga', None),

    # edx-ora2
    ('submissions', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.assessment', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.fileupload', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.staffgrader', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.workflow', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.xblock', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),

    # edxval
    ('edxval', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),

    # Deprecated apps from the edx-enterprise package. We're working on removing these as part of
    # pluginifying edx-enterprise (<https://discuss.openedx.org/t/18316>)
    ('integrated_channels.integrated_channel', None),
    ('integrated_channels.degreed', None),
    ('integrated_channels.degreed2', None),
    ('integrated_channels.sap_success_factors', None),
    ('integrated_channels.xapi', None),
    ('integrated_channels.cornerstone', None),
    ('integrated_channels.blackboard', None),
    ('integrated_channels.canvas', None),
    ('integrated_channels.moodle', None),
)


for app_name, insert_before in OPTIONAL_APPS:
    # First attempt to only find the module rather than actually importing it,
    # to avoid circular references - only try to import if it can't be found
    # by find_spec, which doesn't work with import hooks
    if importlib.util.find_spec(app_name) is None:
        try:
            __import__(app_name)
        except ImportError:
            continue

    try:
        INSTALLED_APPS.insert(INSTALLED_APPS.index(insert_before), app_name)
    except (IndexError, ValueError):
        INSTALLED_APPS.append(app_name)

### Size of chunks into which asset uploads will be divided
UPLOAD_CHUNK_SIZE_IN_MB = 10

### Max size of asset uploads to GridFS
MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB = 20

# FAQ url to direct users to if they upload
# a file that exceeds the above size
MAX_ASSET_UPLOAD_FILE_SIZE_URL = ""

### Default language for a new course
DEFAULT_COURSE_LANGUAGE = "en"

# Specify XBlocks that should be treated as advanced problems. Each entry is a
# dict:
#       'component': the entry-point name of the XBlock.
#       'boilerplate_name': an optional YAML template to be used.  Specify as
#               None to omit.
#
ADVANCED_PROBLEM_TYPES = [
    {
        'component': 'drag-and-drop-v2',
        'boilerplate_name': None
    },
    {
        'component': 'staffgradedxblock',
        'boilerplate_name': None
    }
]

LIBRARY_BLOCK_TYPES = [
    {
        'component': 'library_content',
        'boilerplate_name': None
    }
]

############### Settings for Retirement #####################
# See annotations in lms/envs/common.py for details.
RETIRED_USERNAME_FMT = Derived(lambda settings: settings.RETIRED_USERNAME_PREFIX + '{}')
# See annotations in lms/envs/common.py for details.
RETIRED_EMAIL_FMT = Derived(lambda settings: settings.RETIRED_EMAIL_PREFIX + '{}@' + settings.RETIRED_EMAIL_DOMAIN)
# See annotations in lms/envs/common.py for details.
RETIRED_USER_SALTS = ['abc', '123']
# See annotations in lms/envs/common.py for details.
RETIREMENT_SERVICE_WORKER_USERNAME = 'RETIREMENT_SERVICE_USER'

# Files and Uploads type filter values

FILES_AND_UPLOAD_TYPE_FILTERS = {
    "Images": ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/tiff', 'image/tif', 'image/x-icon',
               'image/svg+xml', 'image/bmp', 'image/x-ms-bmp', ],
    "Documents": [
        'application/pdf',
        'text/plain',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.openxmlformats-officedocument.presentationml.slideshow',
        'application/vnd.openxmlformats-officedocument.presentationml.template',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
        'application/msword',
        'application/vnd.ms-excel',
        'application/vnd.ms-powerpoint',
        'application/csv',
        'application/vnd.ms-excel.sheet.macroEnabled.12',
        'text/x-tex',
        'application/x-pdf',
        'application/vnd.ms-excel.sheet.macroenabled.12',
        'file/pdf',
        'image/pdf',
        'text/csv',
        'text/pdf',
        'text/x-sh',
        '\"application/pdf\"',
    ],
    "Audio": ['audio/mpeg', 'audio/mp3', 'audio/x-wav', 'audio/ogg', 'audio/wav', 'audio/aac', 'audio/x-m4a',
              'audio/mp4', 'audio/x-ms-wma', ],
    "Code": ['application/json', 'text/html', 'text/javascript', 'application/javascript', 'text/css', 'text/x-python',
             'application/x-java-jnlp-file', 'application/xml', 'application/postscript', 'application/x-javascript',
             'application/java-vm', 'text/x-c++src', 'text/xml', 'text/x-scss', 'application/x-python-code',
             'application/java-archive', 'text/x-python-script', 'application/x-ruby', 'application/mathematica',
             'text/coffeescript', 'text/x-matlab', 'application/sql', 'text/php', ]
}

ELASTIC_FIELD_MAPPINGS = {
    "start_date": {
        "type": "date"
    }
}

XBLOCK_FS_STORAGE_BUCKET = None
XBLOCK_FS_STORAGE_PREFIX = None

############################ Global Database Configuration #####################

DATABASE_ROUTERS = [
    'openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter',
]

############################ OAUTH2 Provider ###################################

# 5 minute expiration time for JWT id tokens issued for external API requests.
OAUTH_ID_TOKEN_EXPIRATION = 5 * 60

EDX_DRF_EXTENSIONS = {
    # Set this value to an empty dict in order to prevent automatically updating
    # user data from values in (possibly stale) JWTs.
    'JWT_PAYLOAD_USER_ATTRIBUTE_MAPPING': {},
}

############## Settings for Studio Context Sensitive Help ##############

HELP_TOKENS_INI_FILE = REPO_ROOT / "cms" / "envs" / "help_tokens.ini"

############## DJANGO-USER-TASKS ##############

# How long until database records about the outcome of a task and its artifacts get deleted?
USER_TASKS_MAX_AGE = timedelta(days=7)

############################# Persistent Grades ####################################

# Queue to use for updating persistent grades
RECALCULATE_GRADES_ROUTING_KEY = DEFAULT_PRIORITY_QUEUE

# .. setting_name: DEFAULT_GRADE_DESIGNATIONS
# .. setting_default: ['A', 'B', 'C', 'D']
# .. setting_description: The default 'pass' grade cutoff designations to be used. The failure grade
#     is always 'F' and should not be included in this list.
# .. setting_warning: The DEFAULT_GRADE_DESIGNATIONS list must have more than one designation,
#     or else ['A', 'B', 'C', 'D'] will be used as the default grade designations. Also, only the first
#     11 grade designations are used by the UI, so it's advisable to restrict the list to 11 items.
DEFAULT_GRADE_DESIGNATIONS = ['A', 'B', 'C', 'D']

########## Settings for video transcript migration tasks ############
VIDEO_TRANSCRIPT_MIGRATIONS_JOB_QUEUE = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

########## Settings youtube thumbnails scraper tasks ############
SCRAPE_YOUTUBE_THUMBNAILS_JOB_QUEUE = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

########## Settings update search index task ############
UPDATE_SEARCH_INDEX_JOB_QUEUE = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

###################### VIDEO IMAGE STORAGE ######################

VIDEO_IMAGE_DEFAULT_FILENAME = 'images/video-images/default_video_image.png'
VIDEO_IMAGE_SUPPORTED_FILE_FORMATS = {
    '.bmp': 'image/bmp',
    '.bmp2': 'image/x-ms-bmp',   # PIL gives x-ms-bmp format
    '.gif': 'image/gif',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png'
}
VIDEO_IMAGE_MAX_FILE_SIZE_MB = '2 MB'
VIDEO_IMAGE_MIN_FILE_SIZE_KB = '2 KB'
VIDEO_IMAGE_MAX_WIDTH = 1280
VIDEO_IMAGE_MAX_HEIGHT = 720
VIDEO_IMAGE_MIN_WIDTH = 640
VIDEO_IMAGE_MIN_HEIGHT = 360
VIDEO_IMAGE_ASPECT_RATIO = 16 / 9.0
VIDEO_IMAGE_ASPECT_RATIO_TEXT = '16:9'
VIDEO_IMAGE_ASPECT_RATIO_ERROR_MARGIN = 0.1

###################### ZENDESK ######################
ZENDESK_USER = ''
ZENDESK_API_KEY = ''

############## Installed Django Apps #########################

from edx_django_utils.plugins import get_plugin_apps, add_plugins
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType

INSTALLED_APPS.extend(get_plugin_apps(ProjectType.CMS))
add_plugins(__name__, ProjectType.CMS, SettingsType.COMMON)

# Course exports streamed in blocks of this size. 8192 or 8kb is the default
# setting for the FileWrapper class used to iterate over the export file data.
# See: https://docs.python.org/2/library/wsgiref.html#wsgiref.util.FileWrapper
COURSE_EXPORT_DOWNLOAD_CHUNK_SIZE = 8192

COMMENTS_SERVICE_URL = 'http://localhost:18080'
COMMENTS_SERVICE_KEY = 'password'

EXAMS_SERVICE_URL = 'http://localhost:18740/api/v1'

############# CORS headers for cross-domain requests #################

# Set CORS_ALLOW_HEADERS regardless of whether we've enabled ENABLE_CORS_HEADERS
# because that decision might happen in a later config file. (The headers to
# allow is an application logic, and not site policy.)
CORS_ALLOW_HEADERS = corsheaders_default_headers + (
    'use-jwt-cookie',
    'content-range',
    'content-disposition',
)

########################## VIDEO IMAGE STORAGE ############################

VIDEO_IMAGE_SETTINGS = dict(
    VIDEO_IMAGE_MAX_BYTES=2 * 1024 * 1024,    # 2 MB
    VIDEO_IMAGE_MIN_BYTES=2 * 1024,       # 2 KB
    # Backend storage
    # STORAGE_CLASS='storages.backends.s3boto3.S3Boto3Storage',
    # STORAGE_KWARGS=dict(bucket='video-image-bucket'),
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
    ),
    DIRECTORY_PREFIX='video-images/',
    BASE_URL=MEDIA_URL,
)

VIDEO_IMAGE_MAX_AGE = 31536000

########################## VIDEO TRANSCRIPTS STORAGE ############################
TRANSCRIPT_LANG_CACHE_TIMEOUT = 60 * 60 * 24


##### shoppingcart Payment #####
PAYMENT_SUPPORT_EMAIL = 'billing@example.com'

################################ Bulk Email ###################################
# Parameters for breaking down course enrollment into subtasks.
BULK_EMAIL_EMAILS_PER_TASK = 500

# Suffix used to construct 'from' email address for bulk emails.
# A course-specific identifier is prepended.
BULK_EMAIL_DEFAULT_FROM_EMAIL = 'no-reply@example.com'

# Flag to indicate if individual email addresses should be logged as they are sent
# a bulk email message.
BULK_EMAIL_LOG_SENT_EMAILS = False

###################### Grade Downloads ######################
# These keys are used for all of our asynchronous downloadable files, including
# the ones that contain information other than grades.
GRADES_DOWNLOAD = {
    'STORAGE_CLASS': 'django.core.files.storage.FileSystemStorage',
    'STORAGE_KWARGS': {
        'location': '/tmp/edx-s3/grades',
    },
    'STORAGE_TYPE': None,
    'BUCKET': None,
    'ROOT_PATH': None,
}

############### Settings swift #####################################
SWIFT_USE_TEMP_URLS = False

############### Settings for facebook ##############################
FACEBOOK_APP_ID = 'FACEBOOK_APP_ID'
FACEBOOK_APP_SECRET = 'FACEBOOK_APP_SECRET'
FACEBOOK_API_VERSION = 'v2.1'

###################### PROCTORING SETTINGS ##########################
PROCTORING_SETTINGS = {}

###################### LEARNER PORTAL ################################
LEARNER_PORTAL_URL_ROOT = 'https://learner-portal-localhost:18000'

############################ JWT #################################

REGISTRATION_EXTRA_FIELDS = {
    'confirm_email': 'hidden',
    'level_of_education': 'optional',
    'gender': 'optional',
    'year_of_birth': 'optional',
    'mailing_address': 'optional',
    'goals': 'optional',
    'honor_code': 'required',
    'terms_of_service': 'hidden',
    'city': 'hidden',
    'country': 'hidden',
    'marketing_emails_opt_in': 'hidden',
}
EDXAPP_PARSE_KEYS = {}
PARSE_KEYS = {}

###################### DEPRECATED URLS ##########################

# .. toggle_name: DISABLE_DEPRECATED_SIGNIN_URL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Toggle for removing the deprecated /signin url.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2019-12-02
# .. toggle_target_removal_date: 2020-06-01
# .. toggle_warning: This url can be removed once it no longer has any real traffic.
# .. toggle_tickets: ARCH-1253
DISABLE_DEPRECATED_SIGNIN_URL = False

# .. toggle_name: DISABLE_DEPRECATED_SIGNUP_URL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Toggle for removing the deprecated /signup url.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2019-12-02
# .. toggle_target_removal_date: 2020-06-01
# .. toggle_warning: This url can be removed once it no longer has any real traffic.
# .. toggle_tickets: ARCH-1253
DISABLE_DEPRECATED_SIGNUP_URL = False

##### REGISTRATION RATE LIMIT SETTINGS #####
OPTIONAL_FIELD_API_RATELIMIT = '10/h'

######################## Setting for content libraries ########################
MAX_BLOCKS_PER_CONTENT_LIBRARY = 100_000

######################## Organizations ########################

# .. toggle_name: ORGANIZATIONS_AUTOCREATE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, creating a course run or content library with
#   an "org slug" that does not map to an Organization in the database will trigger the
#   creation of a new Organization, with its name and short_name set to said org slug.
#   When disabled, creation of such content with an unknown org slug will instead
#   result in a validation error.
#   If you want the Organization table to be an authoritative information source in
#   Studio, then disable this; however, if you want the table to just be a reflection of
#   the orgs referenced in Studio content, then leave it enabled.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-11-02
# .. toggle_tickets: https://github.com/openedx/edx-organizations/blob/master/docs/decisions/0001-phase-in-db-backed-organizations-to-all.rst
ORGANIZATIONS_AUTOCREATE = True

################# Documentation links for course apps #################

COURSE_LIVE_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/add_course_live.html"

######################## Registration ########################

# Social-core setting that allows inactive users to be able to
# log in. The only case it's used is when user registering a new account through the LMS.
INACTIVE_USER_LOGIN = True

# Redirect URL for inactive user. If not set, user will be redirected to /login after the login itself (loop)
INACTIVE_USER_URL = f'http://{CMS_BASE}'

######################## Discussion Forum settings ########################

# Feedback link in upgraded discussion notification alert
DISCUSSIONS_INCONTEXT_FEEDBACK_URL = ''

# Learn More link in upgraded discussion notification alert
# pylint: disable=line-too-long
DISCUSSIONS_INCONTEXT_LEARNMORE_URL = "https://docs.openedx.org/en/latest/educators/concepts/communication/about_course_discussions.html"
# pylint: enable=line-too-long

#### Event bus producing ####


def _should_send_xblock_events(settings):
    return settings.ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS

# .. setting_name: EVENT_BUS_PRODUCER_CONFIG
# .. setting_default: all events disabled
# .. setting_description: Dictionary of event_types mapped to dictionaries of topic to topic-related configuration.
#    Each topic configuration dictionary contains
#    * `enabled`: a toggle denoting whether the event will be published to the topic. These should be annotated
#       according to
#       https://docs.openedx.org/projects/edx-toggles/en/latest/how_to/documenting_new_feature_toggles.html
#    * `event_key_field` which is a period-delimited string path to event data field to use as event key.
#    Note: The topic names should not include environment prefix as it will be dynamically added based on
#    EVENT_BUS_TOPIC_PREFIX setting.

EVENT_BUS_PRODUCER_CONFIG = {
    'org.openedx.content_authoring.course.catalog_info.changed.v1': {
        'course-catalog-info-changed':
            {'event_key_field': 'catalog_info.course_key',
             # .. toggle_name: EVENT_BUS_PRODUCER_CONFIG['org.openedx.content_authoring.course.catalog_info.changed.v1']
             #    ['course-catalog-info-changed']['enabled']
             # .. toggle_implementation: DjangoSetting
             # .. toggle_default: False
             # .. toggle_description: if enabled, will publish COURSE_CATALOG_INFO_CHANGED events to the event bus on
             #    the course-catalog-info-changed topics
             # .. toggle_warning: The default may be changed in a later release. See
             #    https://github.com/openedx/openedx-events/issues/265
             # .. toggle_use_cases: opt_in
             # .. toggle_creation_date: 2023-10-10
             'enabled': False},
    },
    'org.openedx.content_authoring.xblock.published.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': Derived(_should_send_xblock_events)},
    },
    'org.openedx.content_authoring.xblock.deleted.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': Derived(_should_send_xblock_events)},
    },
    'org.openedx.content_authoring.xblock.duplicated.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': Derived(_should_send_xblock_events)},
    },
    # LMS events. These have to be copied over here because lms.common adds some derived entries as well,
    # and the derivation fails if the keys are missing. If we ever remove the import of lms.common, we can remove these.
    'org.openedx.learning.certificate.created.v1': {
        'learning-certificate-lifecycle':
            {'event_key_field': 'certificate.course.course_key', 'enabled': False},
    },
    'org.openedx.learning.certificate.revoked.v1': {
        'learning-certificate-lifecycle':
            {'event_key_field': 'certificate.course.course_key', 'enabled': False},
    },
    "org.openedx.learning.course.passing.status.updated.v1": {
        "learning-badges-lifecycle": {
            "event_key_field": "course_passing_status.course.course_key",
            "enabled": Derived(should_send_learning_badge_events),
        },
    },
    "org.openedx.learning.ccx.course.passing.status.updated.v1": {
        "learning-badges-lifecycle": {
            "event_key_field": "course_passing_status.course.ccx_course_key",
            "enabled": Derived(should_send_learning_badge_events),
        },
    },
}

################### Authoring API ######################

# This affects the Authoring API swagger docs but not the legacy swagger docs under /api-docs/.
REST_FRAMEWORK['DEFAULT_SCHEMA_CLASS'] = 'drf_spectacular.openapi.AutoSchema'

################### Studio Search (beta), using Meilisearch ###################

# Enable Studio search features (powered by Meilisearch) (beta, off by default)
MEILISEARCH_ENABLED = False
# Meilisearch URL that the python backend can use. Often points to another docker container or k8s service.
MEILISEARCH_URL = "http://meilisearch"
# URL that browsers (end users) can use to reach Meilisearch. Should be HTTPS in production.
MEILISEARCH_PUBLIC_URL = "http://meilisearch.example.com"
# To support multi-tenancy, you can prefix all indexes with a common key like "sandbox7-"
# and use a restricted tenant token in place of an API key, so that this Open edX instance
# can only use the index(es) that start with this prefix.
# See https://www.meilisearch.com/docs/learn/security/tenant_tokens
MEILISEARCH_INDEX_PREFIX = ""
MEILISEARCH_API_KEY = "devkey"

# .. setting_name: COURSE_ANALYSIS_WORKFLOW_URL
# .. setting_default: ''
# .. setting_description: Base URL of the Course Optimizer extended-report
# .. backend (xpert-ai-workflows). Studio calls this server-side and
# .. proxies the response to the browser; the browser never calls it
# .. directly.
COURSE_ANALYSIS_WORKFLOW_URL = ''
# .. setting_name: COURSE_ANALYSIS_WORKFLOW_API_KEY
# .. setting_default: ''
# .. setting_description: Shared secret sent as the X-Api-Key header on
# .. every request to COURSE_ANALYSIS_WORKFLOW_URL.
COURSE_ANALYSIS_WORKFLOW_API_KEY = ''
# .. setting_name: COURSE_ANALYSIS_WORKFLOW_REQUEST_TIMEOUT_SECONDS
# .. setting_default: 5
# .. setting_description: How long to wait on a request to
# .. COURSE_ANALYSIS_WORKFLOW_URL before giving up. A slow/unreachable
# .. backend shouldn't tie up a Studio request thread waiting on it.
COURSE_ANALYSIS_WORKFLOW_REQUEST_TIMEOUT_SECONDS = 5
# .. setting_name: COURSE_ANALYSIS_EXPORT_TASK_SOFT_TIME_LIMIT
# .. setting_default: 600
# .. setting_description: Soft Celery time limit, in seconds, for the
# .. background task that exports and uploads a course to the Course
# .. Optimizer extended-report backend. When exceeded, the task gets a
# .. chance to record the run as failed before it's terminated, so a
# .. pathological or very large course can't tie up a worker indefinitely.
COURSE_ANALYSIS_EXPORT_TASK_SOFT_TIME_LIMIT = 600
# .. setting_name: COURSE_ANALYSIS_EXPORT_TASK_TIME_LIMIT
# .. setting_default: 660
# .. setting_description: Hard Celery time limit, in seconds, for the
# .. background task that exports and uploads a course to the Course
# .. Optimizer extended-report backend. Should be a little higher than
# .. COURSE_ANALYSIS_EXPORT_TASK_SOFT_TIME_LIMIT to give the soft limit a
# .. chance to run first.
COURSE_ANALYSIS_EXPORT_TASK_TIME_LIMIT = 660
# .. setting_name: COURSE_ANALYSIS_REPORT_CACHE_TIMEOUT_SECONDS
# .. setting_default: 900
# .. setting_description: How long Studio remembers that a Course Optimizer
# .. extended-analysis run is pending or failed, keyed by course. This
# .. covers the window between queuing the export/upload task and
# .. xpert-ai-workflows actually creating the run, during which the status
# .. endpoint would otherwise have nothing new to report and could
# .. mistake a stale prior run for the one just requested. Should be a
# .. little higher than COURSE_ANALYSIS_EXPORT_TASK_TIME_LIMIT.
COURSE_ANALYSIS_REPORT_CACHE_TIMEOUT_SECONDS = 900

# .. setting_name: LIBRARY_ENABLED_BLOCKS
# .. setting_default: ['problem', 'video', 'html', 'drag-and-drop-v2']
# .. setting_description: List of block types that are ready/enabled to be created/used
# .. in libraries. Both basic blocks and advanced blocks can be included.
# .. In the future, we will support individual configuration per library - see
# .. openedx/core/djangoapps/content_libraries/api.py::get_allowed_block_types()
LIBRARY_ENABLED_BLOCKS = [
    'problem',
    'video',
    'html',
    'drag-and-drop-v2',
    'openassessment',
    'conditional',
    'done',
    'edx_sga',
    'freetextresponse',
    'google-calendar',
    'google-document',
    'invideoquiz',
    'lti',
    'lti_consumer',
    'pdf',
    'poll',
    'survey',
    'word_cloud',
]

# .. setting_name: DEFAULT_ORG_LOGO_URL
# .. setting_default: Derived(lambda settings: settings.STATIC_URL + 'images/logo.png')
# .. setting_description: The default logo url for organizations that do not have a logo set.
# .. setting_warning: This url is used as a placeholder for organizations that do not have a logo set.
DEFAULT_ORG_LOGO_URL = Derived(lambda settings: settings.STATIC_URL + 'images/logo.png')

# Misc
AUTHORING_API_URL = ''
