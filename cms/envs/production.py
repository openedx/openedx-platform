# ruff: noqa: I001 - settings file: star-import order is semantically significant
"""
Override common.py with key-value pairs from YAML (plus some extra defaults & post-processing).

This file is in the process of being restructured. Please see:
https://github.com/openedx/edx-platform/blob/master/docs/decisions/0022-settings-simplification.rst
"""

# We intentionally define lots of variables that aren't used, and
# want to import all variables from base settings files
# pylint: disable=wildcard-import, unused-wildcard-import


import codecs
import os
import warnings

import yaml
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy
from edx_django_utils.plugins import add_plugins
from openedx_events.event_bus import merge_producer_configs
from path import Path as path

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType
from openedx.core.lib.derived import derive_settings  # pylint: disable=wrong-import-order
from openedx.core.lib.features_setting_proxy import FeaturesProxy
from openedx.core.lib.logsettings import get_logger_config  # pylint: disable=wrong-import-order
from xmodule.modulestore.modulestore_settings import \
    convert_module_store_setting_if_needed  # pylint: disable=wrong-import-order

from .common import *  # noqa: F403

# A proxy for feature flags stored in the settings namespace
FEATURES = FeaturesProxy(globals())


def get_env_setting(setting):
    """ Get the environment setting or return exception """
    try:
        return os.environ[setting]
    except KeyError:
        error_msg = "Set the %s env variable" % setting  # noqa: UP031
        raise ImproperlyConfigured(error_msg)  # pylint: disable=raise-missing-from  # noqa: B904

#######################################################################################################################
#### YAML LOADING
####

# A file path to a YAML file from which to load configuration overrides for LMS.
try:
    CONFIG_FILE = get_env_setting('CMS_CFG')
except ImproperlyConfigured:
    CONFIG_FILE = get_env_setting('STUDIO_CFG')
    warnings.warn(
        "STUDIO_CFG environment variable is deprecated. Use CMS_CFG instead.",
        DeprecationWarning,
        stacklevel=2,
    )

with codecs.open(CONFIG_FILE, encoding='utf-8') as f:

    # _YAML_TOKENS starts out with the exact contents of the CMS_CFG YAML file.
    # Please avoid adding new references to _YAML_TOKENS. Such references make our settings logic more complex.
    # Instead, just reference the Django settings, which we define in the next step...
    _YAML_TOKENS = yaml.safe_load(f)

    # Update the global namespace of this module with the key-value pairs from _YAML_TOKENS.
    # In other words: For (almost) every YAML key-value pair, define/update a Django setting with that name and value.
    vars().update({

        #  Note: If `value` is a mutable object (e.g., a dict), then it will be aliased between the global namespace and
        #  _YAML_TOKENS. In other words, updates to `value` will manifest in _YAML_TOKENS as well. This is intentional,
        #  in order to maintain backwards compatibility with old Django plugins which use _YAML_TOKENS.
        key: value
        for key, value in _YAML_TOKENS.items()

        # Do NOT define/update Django settings for these particular special keys.
        # We handle each of these with its special logic (below, in this same module).
        # For example, we need to *update* the default FEATURES dict rather than wholesale *override* it.
        if key not in [
            'FEATURES',
            'TRACKING_BACKENDS',
            'EVENT_TRACKING_BACKENDS',
            'JWT_AUTH',
            'CELERY_QUEUES',
            'MKTG_URL_LINK_MAP',
            'REST_FRAMEWORK',
            'EVENT_BUS_PRODUCER_CONFIG',
            'DEFAULT_FILE_STORAGE',
            'STATICFILES_STORAGE',
        ]
    })

#######################################################################################################################
#### LOAD THE EDX-PLATFORM GIT REVISION
####

try:
    # A file path to a YAML file from which to load all the code revisions currently deployed
    REVISION_CONFIG_FILE = get_env_setting('REVISION_CFG')

    with codecs.open(REVISION_CONFIG_FILE, encoding='utf-8') as f:
        REVISION_CONFIG = yaml.safe_load(f)
except Exception:  # pylint: disable=broad-except
    REVISION_CONFIG = {}

# Do NOT calculate this dynamically at startup with git because it's *slow*.
EDX_PLATFORM_REVISION = REVISION_CONFIG.get('EDX_PLATFORM_REVISION', EDX_PLATFORM_REVISION)  # noqa: F405


#######################################################################################################################
#### POST-PROCESSING OF YAML
####
#### This is where we do a bunch of logic to post-process the results of the YAML, including: conditionally setting
#### updates, merging dicts+lists which we did not override, and in some cases simply ignoring the YAML value in favor
#### of a specific production value.
####

# Don't use a connection pool, since connections are dropped by ELB.
BROKER_POOL_LIMIT = 0
BROKER_CONNECTION_TIMEOUT = 1

# Each worker should only fetch one message at a time
CELERYD_PREFETCH_MULTIPLIER = 1

CELERY_ROUTES = "openedx.core.lib.celery.routers.route_task"

if STATIC_URL_BASE:  # noqa: F405
    STATIC_URL = STATIC_URL_BASE  # noqa: F405
    if not STATIC_URL.endswith("/"):
        STATIC_URL += "/"
    STATIC_URL += 'studio/'

if STATIC_ROOT_BASE:  # noqa: F405
    STATIC_ROOT = path(STATIC_ROOT_BASE) / 'studio'  # noqa: F405

DATA_DIR = path(DATA_DIR)  # noqa: F405

# Cache used for location mapping -- called many times with the same key/value
# in a given request.
if 'loc_cache' not in CACHES:  # noqa: F405
    CACHES['loc_cache'] = {  # noqa: F405
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'edx_location_mem_cache',
    }

if 'staticfiles' in CACHES:  # noqa: F405
    CACHES['staticfiles']['KEY_PREFIX'] = EDX_PLATFORM_REVISION  # noqa: F405


MKTG_URL_LINK_MAP.update(_YAML_TOKENS.get('MKTG_URL_LINK_MAP', {}))  # noqa: F405

#Timezone overrides
TIME_ZONE = CELERY_TIMEZONE  # noqa: F405

for feature, value in _YAML_TOKENS.get('FEATURES', {}).items():
    FEATURES[feature] = value

# Additional installed apps
for app in _YAML_TOKENS.get('ADDL_INSTALLED_APPS', []):
    INSTALLED_APPS.append(app)  # noqa: F405

LOGGING = get_logger_config(
    LOG_DIR,  # noqa: F405
    logging_env=LOGGING_ENV,  # noqa: F405
    service_variant=SERVICE_VARIANT,  # noqa: F405
)

LOGIN_REDIRECT_WHITELIST.extend([reverse_lazy('home')])  # noqa: F405

############### XBlock filesystem field config ##########
if 'url_root' in DJFS:  # noqa: F405
    DJFS['url_root'] = DJFS['url_root'].format(platform_revision=EDX_PLATFORM_REVISION)  # noqa: F405

# Note that this is the Studio key for Segment. There is a separate key for the LMS.
CMS_SEGMENT_KEY = _YAML_TOKENS.get('SEGMENT_KEY')

if AWS_ACCESS_KEY_ID == "":  # noqa: F405
    AWS_ACCESS_KEY_ID = None

if AWS_SECRET_ACCESS_KEY == "":  # noqa: F405
    AWS_SECRET_ACCESS_KEY = None

AWS_DEFAULT_ACL = 'private'
AWS_BUCKET_ACL = AWS_DEFAULT_ACL
# The number of seconds that a generated URL is valid for.
AWS_QUERYSTRING_EXPIRE = 7 * 24 * 60 * 60  # 7 days

_yaml_storages = _YAML_TOKENS.get('STORAGES', {})

_storages_default_backend_is_missing = not _yaml_storages.get('default', {}).get('BACKEND')

# For backward compatibility, if YAML provides legacy keys (DEFAULT_FILE_STORAGE, STATICFILES_STORAGE)
# and STORAGES doesn’t explicitly define the corresponding backend, migrate the legacy value into STORAGES.
# If YAML doesn't provide lagacy keys, no backend is defined in STORAGES['default'] and AWS creds are present,
# fall back to S3Boto3Storage.
#
# This ensures YAML-provided values take precedence over defaults from common.py,
# without overwriting user-defined STORAGES and AWS creds are treated only as a fallback.
if _storages_default_backend_is_missing:
    if 'DEFAULT_FILE_STORAGE' in _YAML_TOKENS:
        STORAGES['default']['BACKEND'] = _YAML_TOKENS['DEFAULT_FILE_STORAGE']  # noqa: F405
    elif AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
        STORAGES['default']['BACKEND'] = 'storages.backends.s3boto3.S3Boto3Storage'  # noqa: F405

# Apply legacy STATICFILES_STORAGE if no backend is defined for "staticfiles"
if 'STATICFILES_STORAGE' in _YAML_TOKENS and not _yaml_storages.get('staticfiles', {}).get('BACKEND'):
    STORAGES['staticfiles']['BACKEND'] = _YAML_TOKENS['STATICFILES_STORAGE']  # noqa: F405

if COURSE_IMPORT_EXPORT_BUCKET:  # noqa: F405
    COURSE_IMPORT_EXPORT_STORAGE = 'cms.djangoapps.contentstore.storage.ImportExportS3Storage'
else:
    COURSE_IMPORT_EXPORT_STORAGE = STORAGES['default']['BACKEND']  # noqa: F405

USER_TASKS_ARTIFACT_STORAGE = COURSE_IMPORT_EXPORT_STORAGE

if COURSE_METADATA_EXPORT_BUCKET:  # noqa: F405
    COURSE_METADATA_EXPORT_STORAGE = 'cms.djangoapps.export_course_metadata.storage.CourseMetadataExportS3Storage'
else:
    COURSE_METADATA_EXPORT_STORAGE = STORAGES['default']['BACKEND']  # noqa: F405

# The normal database user does not have enough permissions to run migrations.
# Migrations are run with separate credentials, given as DB_MIGRATION_*
# environment variables
for name, database in DATABASES.items():  # noqa: F405
    if name != 'read_replica':
        database.update({
            'ENGINE': os.environ.get('DB_MIGRATION_ENGINE', database['ENGINE']),
            'USER': os.environ.get('DB_MIGRATION_USER', database['USER']),
            'PASSWORD': os.environ.get('DB_MIGRATION_PASS', database['PASSWORD']),
            'NAME': os.environ.get('DB_MIGRATION_NAME', database['NAME']),
            'HOST': os.environ.get('DB_MIGRATION_HOST', database['HOST']),
            'PORT': os.environ.get('DB_MIGRATION_PORT', database['PORT']),
        })

MODULESTORE = convert_module_store_setting_if_needed(MODULESTORE)  # noqa: F405

# Celery Broker

BROKER_URL = "{}://{}:{}@{}/{}".format(CELERY_BROKER_TRANSPORT,  # noqa: F405, UP032
                                       CELERY_BROKER_USER,  # noqa: F405
                                       CELERY_BROKER_PASSWORD,  # noqa: F405
                                       CELERY_BROKER_HOSTNAME,  # noqa: F405
                                       CELERY_BROKER_VHOST)  # noqa: F405

try:
    BROKER_TRANSPORT_OPTIONS = {
        'fanout_patterns': True,
        'fanout_prefix': True,
        **_YAML_TOKENS.get('CELERY_BROKER_TRANSPORT_OPTIONS', {})
    }
except TypeError as exc:
    raise ImproperlyConfigured('CELERY_BROKER_TRANSPORT_OPTIONS must be a dict') from exc

# Build a CELERY_QUEUES dict the way that celery expects, based on a couple lists of queue names from the YAML.
_YAML_CELERY_QUEUES = _YAML_TOKENS.get('CELERY_QUEUES', None)
if _YAML_CELERY_QUEUES:
    CELERY_QUEUES = {queue: {} for queue in _YAML_CELERY_QUEUES}

# Then add alternate environment queues
_YAML_ALTERNATE_WORKER_QUEUES = _YAML_TOKENS.get('ALTERNATE_WORKER_QUEUES', '').split()
ALTERNATE_QUEUES = [
    DEFAULT_PRIORITY_QUEUE.replace(SERVICE_VARIANT, alternate)  # noqa: F405
    for alternate in _YAML_ALTERNATE_WORKER_QUEUES
]

CELERY_QUEUES.update(
    {
        alternate: {}
        for alternate in ALTERNATE_QUEUES
        if alternate not in list(CELERY_QUEUES.keys())
    }
)

# Event tracking
TRACKING_BACKENDS.update(_YAML_TOKENS.get("TRACKING_BACKENDS", {}))  # noqa: F405
EVENT_TRACKING_BACKENDS['tracking_logs']['OPTIONS']['backends'].update(  # noqa: F405
    _YAML_TOKENS.get("EVENT_TRACKING_BACKENDS", {})
)
EVENT_TRACKING_BACKENDS['segmentio']['OPTIONS']['processors'][0]['OPTIONS']['whitelist'].extend(  # noqa: F405
    EVENT_TRACKING_SEGMENTIO_EMIT_WHITELIST  # noqa: F405
)


if ENABLE_COURSEWARE_INDEX or ENABLE_LIBRARY_INDEX:  # noqa: F405
    # Use ElasticSearch for the search engine
    SEARCH_ENGINE = "search.elastic.ElasticSearchEngine"

# TODO: Once we have successfully upgraded to ES7, switch this back to ELASTIC_SEARCH_CONFIG.
ELASTIC_SEARCH_CONFIG = _YAML_TOKENS.get('ELASTIC_SEARCH_CONFIG_ES7', [{}])

XBLOCK_SETTINGS.setdefault("VideoBlock", {})["licensing_enabled"] = FEATURES["LICENSING"]  # noqa: F405
XBLOCK_SETTINGS.setdefault("VideoBlock", {})['YOUTUBE_API_KEY'] = YOUTUBE_API_KEY  # noqa: F405

############################ OAUTH2 Provider ###################################

#### JWT configuration ####
JWT_AUTH.update(_YAML_TOKENS.get('JWT_AUTH', {}))  # noqa: F405

######################## CUSTOM COURSES for EDX CONNECTOR ######################
if CUSTOM_COURSES_EDX:  # noqa: F405
    INSTALLED_APPS.append('openedx.core.djangoapps.ccxcon.apps.CCXConnectorConfig')  # noqa: F405

########################## Extra middleware classes  #######################

# Allow extra middleware classes to be added to the app through configuration.
MIDDLEWARE.extend(EXTRA_MIDDLEWARE_CLASSES)  # noqa: F405


#######################################################################################################################
#### DERIVE ANY DERIVED SETTINGS
####

derive_settings(__name__)


#######################################################################################################################
#### LOAD SETTINGS FROM DJANGO PLUGINS
####
#### This is at the bottom because it is going to load more settings after base settings are loaded
####

# These dicts are defined solely for BACKWARDS COMPATIBILITY with existing plugins which may theoretically
# rely upon them. Please do not add new references to these dicts!
# - If you need to access the YAML values in this module, use _YAML_TOKENS.
# - If you need to access to these values elsewhere, use the corresponding rendered `settings.*`
#   value rathering than diving into these dicts.
ENV_TOKENS = _YAML_TOKENS
AUTH_TOKENS = _YAML_TOKENS
ENV_FEATURES = _YAML_TOKENS.get("FEATURES", {})
ENV_CELERY_QUEUES = _YAML_CELERY_QUEUES
ALTERNATE_QUEUE_ENVS = _YAML_ALTERNATE_WORKER_QUEUES

# Load production.py in plugins
add_plugins(__name__, ProjectType.CMS, SettingsType.PRODUCTION)


#######################################################################################################################
#### MORE YAML POST-PROCESSING
####
#### More post-processing, but these will not be available Django plugins.
#### Unclear whether or not these are down here intentionally.
####

############# CORS headers for cross-domain requests #################
if ENABLE_CORS_HEADERS:  # noqa: F405
    CORS_ALLOW_CREDENTIALS = True
    CORS_ORIGIN_WHITELIST = _YAML_TOKENS.get('CORS_ORIGIN_WHITELIST', ())
    CORS_ORIGIN_ALLOW_ALL = _YAML_TOKENS.get('CORS_ORIGIN_ALLOW_ALL', False)
    CORS_ALLOW_INSECURE = _YAML_TOKENS.get('CORS_ALLOW_INSECURE', False)

######################## CELERY ROTUING ########################

# Defines alternate environment tasks, as a dict of form { task_name: alternate_queue }
ALTERNATE_ENV_TASKS = {
    'completion_aggregator.tasks.update_aggregators': 'lms',
    'openedx.core.djangoapps.content.block_structure.tasks.update_course_in_cache': 'lms',
    'openedx.core.djangoapps.content.block_structure.tasks.update_course_in_cache_v2': 'lms',
}

# Defines the task -> alternate worker queue to be used when routing.
EXPLICIT_QUEUES = {
    'lms.djangoapps.grades.tasks.compute_all_grades_for_course': {
        'queue': POLICY_CHANGE_GRADES_ROUTING_KEY},  # noqa: F405
    'lms.djangoapps.grades.tasks.recalculate_course_and_subsection_grades_for_user': {
        'queue': SINGLE_LEARNER_COURSE_REGRADE_ROUTING_KEY},  # noqa: F405
    'cms.djangoapps.contentstore.tasks.update_search_index': {
        'queue': UPDATE_SEARCH_INDEX_JOB_QUEUE},  # noqa: F405
}

############## XBlock extra mixins ############################
XBLOCK_MIXINS += tuple(XBLOCK_EXTRA_MIXINS)  # noqa: F405

# Translation overrides
LANGUAGE_COOKIE_NAME = _YAML_TOKENS.get('LANGUAGE_COOKIE') or LANGUAGE_COOKIE_NAME  # noqa: F405

############## DRF overrides ##############
REST_FRAMEWORK.update(_YAML_TOKENS.get('REST_FRAMEWORK', {}))  # noqa: F405

# keys for  big blue button live provider
# TODO: This should not be in the core platform. If it has to stay for now, though, then we should move these
#       defaults into common.py
COURSE_LIVE_GLOBAL_CREDENTIALS["BIG_BLUE_BUTTON"] = {  # noqa: F405
    "KEY": _YAML_TOKENS.get('BIG_BLUE_BUTTON_GLOBAL_KEY'),
    "SECRET": _YAML_TOKENS.get('BIG_BLUE_BUTTON_GLOBAL_SECRET'),
    "URL": _YAML_TOKENS.get('BIG_BLUE_BUTTON_GLOBAL_URL'),
}

# TODO: We believe that this could be more simply defined as CMS_ROOT_URL. We are not making the change now,
#       but please don't follow this pattern in other defaults...
INACTIVE_USER_URL = f'http{"s" if HTTPS == "on" else ""}://{CMS_BASE}'  # noqa: F405

############## Event bus producer ##############
EVENT_BUS_PRODUCER_CONFIG = merge_producer_configs(
    EVENT_BUS_PRODUCER_CONFIG,  # noqa: F405
    _YAML_TOKENS.get('EVENT_BUS_PRODUCER_CONFIG', {})
)

############## Authoring API drf-spectacular openapi settings ##############
# These fields override the spectacular settings default values.
# Any fields not included here will use the default values.
SPECTACULAR_SETTINGS = {
    'TITLE': 'Authoring API',
    'DESCRIPTION': f'''Experimental API to edit xblocks and course content.
    \n\nDanger: Do not use on running courses!
    \n\n - How to gain access: Please email the owners of this openedx service.
    \n - How to use: This API uses oauth2 authentication with the
    access token endpoint: `{LMS_ROOT_URL}/oauth2/access_token`.
    Please see separately provided documentation.
    \n - How to test: You must be logged in as course author for whatever course you want to test with.
    You can use the [Swagger UI](https://{CMS_BASE}/authoring-api/ui/) to "Try out" the API
    with your test course. To do this, you must select the "Local" server.
    \n - Public vs. Local servers: The "Public" server is where you can reach the API externally. The "Local" server is
    for development with a local edx-platform version,  and for use via the
    [Swagger UI](https://{CMS_BASE}/authoring-api/ui/).
    \n - Swaggerfile: [Download link](https://{CMS_BASE}/authoring-api/schema/)''',  # noqa: F405
    'VERSION': '0.1.0',
    'SERVE_INCLUDE_SCHEMA': False,
    # restrict spectacular to CMS API endpoints (cms/lib/spectacular.py):
    'PREPROCESSING_HOOKS': ['cms.lib.spectacular.cms_api_filter'],
    # remove the default schema path prefix to replace it with server-specific base paths:
    'SCHEMA_PATH_PREFIX': '/api/contentstore',
    'SCHEMA_PATH_PREFIX_TRIM': '/api/contentstore',
    'SERVERS': [
        {'url': AUTHORING_API_URL, 'description': 'Public'},  # noqa: F405
        {'url': f'https://{CMS_BASE}', 'description': 'Local'},  # noqa: F405
        {'url': f'https://{CMS_BASE}/api/contentstore', 'description': 'CMS-contentstore'}  # noqa: F405
    ],
}


#######################################################################################################################
# HEY! Don't add anything to the end of this file.
# Add your defaults to common.py instead!
# If you really need to add post-YAML logic, add it above the "DERIVE ANY DERIVED SETTINGS" section.
#######################################################################################################################
