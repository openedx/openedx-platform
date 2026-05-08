# ruff: noqa: I001 - settings file: star-import order is semantically significant
"""
Specific overrides to the base prod settings to make development easier.
"""


import logging
from os.path import abspath, dirname, join

from openedx.core.lib.features_setting_proxy import FeaturesProxy

from .production import *  # pylint: disable=wildcard-import, unused-wildcard-import  # noqa: F403

FEATURES = FeaturesProxy(globals())

# Don't use S3 in devstack, fall back to filesystem
STORAGES['default']['BACKEND'] = 'django.core.files.storage.FileSystemStorage'  # noqa: F405
COURSE_IMPORT_EXPORT_STORAGE = 'django.core.files.storage.FileSystemStorage'
USER_TASKS_ARTIFACT_STORAGE = COURSE_IMPORT_EXPORT_STORAGE

DEBUG = True
USE_I18N = True
DEFAULT_TEMPLATE_ENGINE['OPTIONS']['debug'] = DEBUG  # noqa: F405
SITE_NAME = 'localhost:8001'
HTTPS = 'off'

CMS_BASE = 'localhost:18010'
SESSION_COOKIE_NAME = 'studio_sessionid'

################################ LOGGERS ######################################


# Disable noisy loggers
for pkg_name in ['common.djangoapps.track.contexts', 'common.djangoapps.track.middleware']:
    logging.getLogger(pkg_name).setLevel(logging.CRITICAL)

# Docker does not support the syslog socket at /dev/log. Rely on the console.
LOGGING['handlers']['local'] = LOGGING['handlers']['tracking'] = {  # noqa: F405
    'class': 'logging.NullHandler',
}

LOGGING['loggers']['tracking']['handlers'] = ['console']  # noqa: F405

################################ EMAIL ########################################

EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
EMAIL_FILE_PATH = '/edx/src/ace_messages/'

################################# LMS INTEGRATION #############################

LMS_BASE = 'localhost:18000'
LMS_ROOT_URL = f'http://{LMS_BASE}'

FRONTEND_REGISTER_URL = LMS_ROOT_URL + '/register'

################################## Video Pipeline Settings #########################

ENABLE_VIDEO_UPLOAD_PIPELINE = True

########################### PIPELINE #################################

# Skip packaging and optimization in development
PIPELINE['PIPELINE_ENABLED'] = False  # noqa: F405
STORAGES['staticfiles']['BACKEND'] = 'openedx.core.storage.DevelopmentStorage'  # noqa: F405

# Revert to the default set of finders as we don't want the production pipeline
STATICFILES_FINDERS = [
    'openedx.core.djangoapps.theming.finders.ThemeFilesFinder',
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

############################ PYFS XBLOCKS SERVICE #############################
# Set configuration for Django pyfilesystem

DJFS = {
    'type': 'osfs',
    'directory_root': 'cms/static/djpyfs',
    'url_root': '/static/djpyfs',
}

################################# CELERY ######################################

# By default don't use a worker, execute tasks as if they were local functions
CELERY_ALWAYS_EAGER = True

# When the celery task is eagerly, it is executed locally while sharing the
# thread and its request cache with the active Django Request. In that case,
# do not clear the cache.
CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION = False

################################ DEBUG TOOLBAR ################################

INSTALLED_APPS += ['debug_toolbar']  # noqa: F405

MIDDLEWARE.append('debug_toolbar.middleware.DebugToolbarMiddleware')  # noqa: F405
INTERNAL_IPS = ('127.0.0.1',)

DEBUG_TOOLBAR_PANELS = (
    'debug_toolbar.panels.versions.VersionsPanel',
    'debug_toolbar.panels.timer.TimerPanel',
    'debug_toolbar.panels.settings.SettingsPanel',
    'debug_toolbar.panels.headers.HeadersPanel',
    'debug_toolbar.panels.request.RequestPanel',
    'debug_toolbar.panels.sql.SQLPanel',
    'debug_toolbar.panels.signals.SignalsPanel',
    'debug_toolbar.panels.cache.CachePanel',
    'debug_toolbar.panels.history.HistoryPanel',

    # ProfilingPanel has been intentionally removed for default devstack.py
    # runtimes for performance reasons.
    # 'debug_toolbar.panels.profiling.ProfilingPanel',
)

DEBUG_TOOLBAR_CONFIG = {
    # Profile panel is incompatible with wrapped views
    # See https://github.com/jazzband/django-debug-toolbar/issues/792
    'DISABLE_PANELS': (
        'debug_toolbar.panels.profiling.ProfilingPanel',
    ),
    'SHOW_TOOLBAR_CALLBACK': 'cms.envs.devstack.should_show_debug_toolbar',
}


def should_show_debug_toolbar(request):  # pylint: disable=missing-function-docstring
    # We always want the toolbar on devstack unless running tests from another Docker container
    hostname = request.get_host()
    if hostname.startswith('edx.devstack.studio:') or hostname.startswith('studio.devstack.edx:'):
        return False
    return True


################################ MILESTONES ################################
MILESTONES_APP = True

########################### ORGANIZATIONS #################################
# Although production studio.edx.org disables `ORGANIZATIONS_AUTOCREATE`,
# we purposefully leave auto-creation enabled in Devstack Studio for developer
# convenience, allowing devs to create test courses for any organization
# without having to first manually create said organizations in the admin panel.
ORGANIZATIONS_AUTOCREATE = True

################################ ENTRANCE EXAMS ################################
ENTRANCE_EXAMS = True

################################ COURSE LICENSES ################################
LICENSING = True
# Needed to enable licensing on video blocks
XBLOCK_SETTINGS.update({'VideoBlock': {'licensing_enabled': True}})  # noqa: F405

################################ SEARCH INDEX ################################
ENABLE_COURSEWARE_INDEX = True
ENABLE_LIBRARY_INDEX = False
SEARCH_ENGINE = "search.elastic.ElasticSearchEngine"

ELASTIC_SEARCH_CONFIG = [
    {
        'use_ssl': False,
        'host': 'edx.devstack.elasticsearch710',
        'port': 9200
    }
]

################################ COURSE DISCUSSIONS ###########################
ENABLE_DISCUSSION_SERVICE = True

################################ CREDENTIALS ###########################
CREDENTIALS_SERVICE_USERNAME = 'credentials_worker'

########################## Certificates Web/HTML View #######################
CERTIFICATES_HTML_VIEW = True

########################## AUTHOR PERMISSION #######################
ENABLE_CREATOR_GROUP = True

########################## Library creation organizations restriction #######################
ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES = True

################### FRONTEND APPLICATION PUBLISHER URL ###################
FRONTEND_APP_PUBLISHER_URL = 'http://localhost:18400'

################### FRONTEND APPLICATION COURSE AUTHORING ###################
COURSE_AUTHORING_MICROFRONTEND_URL = 'http://localhost:2001'

################### FRONTEND APPLICATION DISCUSSIONS ###################
DISCUSSIONS_MICROFRONTEND_URL = 'http://localhost:2002'

################### FRONTEND APPLICATION ACCOUNT ###################
ACCOUNT_MICROFRONTEND_URL = 'http://localhost:1997'

################### FRONTEND APPLICATION LEARNING ###################
LEARNING_MICROFRONTEND_URL = 'http://localhost:2000'

################### FRONTEND APPLICATION DISCUSSIONS FEEDBACK URL###################
DISCUSSIONS_MFE_FEEDBACK_URL = None

################################# DJANGO-REQUIRE ###############################

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = DEBUG

########################### OAUTH2 #################################
JWT_AUTH.update({  # noqa: F405
    'JWT_ISSUER': f'{LMS_ROOT_URL}/oauth2',
    'JWT_ISSUERS': [{
        'AUDIENCE': 'lms-key',
        'ISSUER': f'{LMS_ROOT_URL}/oauth2',
        'SECRET_KEY': 'lms-secret',
    }],
    'JWT_SECRET_KEY': 'lms-secret',
    'JWT_AUDIENCE': 'lms-key',
    'JWT_PUBLIC_SIGNING_JWK_SET': (
        '{"keys": [{"kid": "devstack_key", "e": "AQAB", "kty": "RSA", "n": "smKFSYowG6nNUAdeqH1jQQnH1PmIHphzBmwJ5vRf1vu'
        '48BUI5VcVtUWIPqzRK_LDSlZYh9D0YFL0ZTxIrlb6Tn3Xz7pYvpIAeYuQv3_H5p8tbz7Fb8r63c1828wXPITVTv8f7oxx5W3lFFgpFAyYMmROC'
        '4Ee9qG5T38LFe8_oAuFCEntimWxN9F3P-FJQy43TL7wG54WodgiM0EgzkeLr5K6cDnyckWjTuZbWI-4ffcTgTZsL_Kq1owa_J2ngEfxMCObnzG'
        'y5ZLcTUomo4rZLjghVpq6KZxfS6I1Vz79ZsMVUWEdXOYePCKKsrQG20ogQEkmTf9FT_SouC6jPcHLXw"}]}'
    ),
})

# pylint: enable=unicode-format-string  # pylint: disable=bad-option-value

IDA_LOGOUT_URI_LIST = [
    'http://localhost:18130/logout/',  # ecommerce
    'http://localhost:18150/logout/',  # credentials
]

ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL = "http://edx.devstack.lms/oauth2"

#####################################################################

# pylint: disable=wrong-import-order, wrong-import-position
from edx_django_utils.plugins import add_plugins

# pylint: disable=wrong-import-order, wrong-import-position
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType

add_plugins(__name__, ProjectType.CMS, SettingsType.DEVSTACK)


OPENAPI_CACHE_TIMEOUT = 0

#####################################################################
# set replica set of contentstore to none as we haven't setup any for cms in devstack
CONTENTSTORE['DOC_STORE_CONFIG']['replicaSet'] = None  # noqa: F405

#####################################################################
# set replica sets of moduelstore to none as we haven't setup any for cms in devstack
for store in MODULESTORE['default']['OPTIONS']['stores']:  # noqa: F405
    if 'DOC_STORE_CONFIG' in store and 'replicaSet' in store['DOC_STORE_CONFIG']:
        store['DOC_STORE_CONFIG']['replicaSet'] = None


#####################################################################
# Lastly, run any migrations, if needed.
MODULESTORE = convert_module_store_setting_if_needed(MODULESTORE)  # noqa: F405

# Dummy secret key for dev
SECRET_KEY = '85920908f28904ed733fe576320db18cabd7b6cd'

############# CORS headers for cross-domain requests #################
ENABLE_CORS_HEADERS = True
CORS_ALLOW_CREDENTIALS = True
CORS_ORIGIN_ALLOW_ALL = True

################### Special Exams (Proctoring) and Prereqs ###################
ENABLE_SPECIAL_EXAMS = True
ENABLE_PREREQUISITE_COURSES = True

# Used in edx-proctoring for ID generation in lieu of SECRET_KEY - dummy value
# (ref MST-637)
PROCTORING_USER_OBFUSCATION_KEY = '85920908f28904ed733fe576320db18cabd7b6cd'

#################### Webpack Configuration Settings ##############################
WEBPACK_LOADER['DEFAULT']['TIMEOUT'] = 5  # noqa: F405

################ Using LMS SSO for login to Studio ################
SOCIAL_AUTH_EDX_OAUTH2_KEY = 'studio-sso-key'
SOCIAL_AUTH_EDX_OAUTH2_SECRET = 'studio-sso-secret'  # in stage, prod would be high-entropy secret
# routed internally server-to-server
SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT = ENV_TOKENS.get('SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT', 'http://edx.devstack.lms:18000')  # noqa: F405  # pylint: disable=line-too-long
SOCIAL_AUTH_EDX_OAUTH2_PUBLIC_URL_ROOT = 'http://localhost:18000'  # used in browser redirect

# Don't form the return redirect URL with HTTPS on devstack
SOCIAL_AUTH_REDIRECT_IS_HTTPS = False

#################### Network configuration ####################
# Devstack is directly exposed to the caller
CLOSEST_CLIENT_IP_FROM_HEADERS = []

#################### Credentials Settings ####################
CREDENTIALS_INTERNAL_SERVICE_URL = 'http://edx.devstack.credentials:18150'
CREDENTIALS_PUBLIC_SERVICE_URL = 'http://localhost:18150'

########################## ORA MFE APP ##############################
ORA_MICROFRONTEND_URL = 'http://localhost:1992'

########################## LEARNER HOME APP ##############################
LEARNER_HOME_MICROFRONTEND_URL = 'http://localhost:1996'

############################ AI_TRANSLATIONS ##################################
AI_TRANSLATIONS_API_URL = 'http://localhost:18760/api/v1'

############################ CSRF ##################################

# MFEs that will call this service in devstack
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:3001',  # frontend-app-library-authoring
    'http://localhost:2001',  # frontend-app-course-authoring
    'http://localhost:1992',  # frontend-app-ora
    'http://localhost:1999',  # frontend-app-authn
]

#################### Event bus backend ########################

EVENT_BUS_PRODUCER = 'edx_event_bus_redis.create_producer'
EVENT_BUS_REDIS_CONNECTION_URL = 'redis://:password@edx.devstack.redis:6379/'
EVENT_BUS_TOPIC_PREFIX = 'dev'
EVENT_BUS_CONSUMER = 'edx_event_bus_redis.RedisEventConsumer'

course_catalog_event_setting = EVENT_BUS_PRODUCER_CONFIG['org.openedx.content_authoring.course.catalog_info.changed.v1']  # noqa: F405  # pylint: disable=line-too-long
course_catalog_event_setting['course-catalog-info-changed']['enabled'] = True

xblock_published_event_setting = EVENT_BUS_PRODUCER_CONFIG['org.openedx.content_authoring.xblock.published.v1']  # noqa: F405  # pylint: disable=line-too-long
xblock_published_event_setting['course-authoring-xblock-lifecycle']['enabled'] = True
xblock_deleted_event_setting = EVENT_BUS_PRODUCER_CONFIG['org.openedx.content_authoring.xblock.deleted.v1']  # noqa: F405  # pylint: disable=line-too-long
xblock_deleted_event_setting['course-authoring-xblock-lifecycle']['enabled'] = True
xblock_duplicated_event_setting = EVENT_BUS_PRODUCER_CONFIG['org.openedx.content_authoring.xblock.duplicated.v1']  # noqa: F405  # pylint: disable=line-too-long
xblock_duplicated_event_setting['course-authoring-xblock-lifecycle']['enabled'] = True


################# New settings must go ABOVE this line #################
########################################################################
# See if the developer has any local overrides.
if os.path.isfile(join(dirname(abspath(__file__)), 'private.py')):  # noqa: F405
    from .private import *  # pylint: disable=import-error,wildcard-import  # noqa: F403

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
    \n - Swaggerfile: [Download link](https://{CMS_BASE}/authoring-api/schema/)''',
    'VERSION': '0.1.0',
    'SERVE_INCLUDE_SCHEMA': False,
    # restrict spectacular to CMS API endpoints (cms/lib/spectacular.py):
    'PREPROCESSING_HOOKS': ['cms.lib.spectacular.cms_api_filter'],
    # remove the default schema path prefix to replace it with server-specific base paths:
    'SCHEMA_PATH_PREFIX': '/api/contentstore',
    'SCHEMA_PATH_PREFIX_TRIM': '/api/contentstore',
    'SERVERS': [
        {'url': AUTHORING_API_URL, 'description': 'Public'},  # noqa: F405
        {'url': f'http://{CMS_BASE}', 'description': 'Local'},
        {'url': f'http://{CMS_BASE}/api/contentstore', 'description': 'CMS-contentstore'}
    ],
}
