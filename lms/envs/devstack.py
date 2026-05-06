# ruff: noqa: I001 - settings file: star-import order is semantically significant
"""
Specific overrides to the base prod settings to make development easier.
"""


# Silence noisy logs
import logging
from os.path import abspath, dirname, join
from urllib.parse import urlparse

# pylint: enable=unicode-format-string  # pylint: disable=bad-option-value
#####################################################################
from edx_django_utils.plugins import add_plugins

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType
from openedx.core.lib.features_setting_proxy import FeaturesProxy

from .production import *  # pylint: disable=wildcard-import, unused-wildcard-import  # noqa: F403

FEATURES = FeaturesProxy(globals())
# Don't use S3 in devstack, fall back to filesystem
STORAGES['default']['BACKEND'] = 'django.core.files.storage.FileSystemStorage'  # noqa: F405
ORA2_FILEUPLOAD_BACKEND = 'django'


DEBUG = True
INTERNAL_IPS = ('127.0.0.1',)

USE_I18N = True
DEFAULT_TEMPLATE_ENGINE['OPTIONS']['debug'] = True  # noqa: F405
LMS_BASE = 'localhost:18000'
CMS_BASE = 'localhost:18010'
SITE_NAME = LMS_BASE

SESSION_COOKIE_NAME = 'lms_sessionid'

# By default don't use a worker, execute tasks as if they were local functions
CELERY_ALWAYS_EAGER = True
# When the celery task is run eagerly, it is executed locally while sharing the
# thread and its request cache with the active Django Request. In that case,
# do not clear the cache.
CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION = False
HTTPS = 'off'

LMS_ROOT_URL = f'http://{LMS_BASE}'
LMS_INTERNAL_ROOT_URL = LMS_ROOT_URL
ENTERPRISE_API_URL = f'{LMS_INTERNAL_ROOT_URL}/enterprise/api/v1/'
IDA_LOGOUT_URI_LIST = [
    'http://localhost:18130/logout/',  # ecommerce
    'http://localhost:18150/logout/',  # credentials
    'http://localhost:18381/logout/',  # discovery
    'http://localhost:18010/logout/',  # studio
]

################################ LOGGERS ######################################

LOG_OVERRIDES = [
    ('common.djangoapps.track.contexts', logging.CRITICAL),
    ('common.djangoapps.track.middleware', logging.CRITICAL),
    ('lms.djangoapps.discussion.django_comment_client.utils', logging.CRITICAL),
]
for log_name, log_level in LOG_OVERRIDES:
    logging.getLogger(log_name).setLevel(log_level)

# Docker does not support the syslog socket at /dev/log. Rely on the console.
LOGGING['handlers']['local'] = LOGGING['handlers']['tracking'] = {  # noqa: F405
    'class': 'logging.NullHandler',
}

LOGGING['loggers']['tracking']['handlers'] = ['console']  # noqa: F405

################################ EMAIL ########################################

EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
EMAIL_FILE_PATH = '/edx/src/ace_messages/'

############################ PYFS XBLOCKS SERVICE #############################
# Set configuration for Django pyfilesystem

DJFS = {
    'type': 'osfs',
    'directory_root': 'lms/static/djpyfs',
    'url_root': '/static/djpyfs',
}

################################ DEBUG TOOLBAR ################################

# The Django Debug Toolbar is disabled by default, if you need to enable it for
# debugging.  Simply uncomment the INSTALLED_APPS and MIDDLEWARE section below.
#
# INSTALLED_APPS += ['debug_toolbar']
# MIDDLEWARE += [
#     'lms.djangoapps.discussion.django_comment_client.utils.QueryCountDebugMiddleware',
#     'debug_toolbar.middleware.DebugToolbarMiddleware',
# ]

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
    'SHOW_TOOLBAR_CALLBACK': 'lms.envs.devstack.should_show_debug_toolbar',
}


def should_show_debug_toolbar(request):  # pylint: disable=missing-function-docstring
    # We always want the toolbar on devstack unless running tests from another Docker container
    hostname = request.get_host()
    if hostname.startswith('edx.devstack.lms:') or hostname.startswith('lms.devstack.edx:'):
        return False
    return True

########################### PIPELINE #################################

PIPELINE['PIPELINE_ENABLED'] = False  # noqa: F405
STORAGES['staticfiles']['BACKEND'] = 'openedx.core.storage.DevelopmentStorage'  # noqa: F405

# Revert to the default set of finders as we don't want the production pipeline
STATICFILES_FINDERS = [
    'openedx.core.djangoapps.theming.finders.ThemeFilesFinder',
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

# Disable JavaScript compression in development
PIPELINE['JS_COMPRESSOR'] = None  # noqa: F405

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = DEBUG

PIPELINE['SASS_ARGUMENTS'] = '--debug-info'  # noqa: F405

########################### VERIFIED CERTIFICATES #################################

AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING = True

########################### External REST APIs #################################
ENABLE_MOBILE_REST_API = True
ENABLE_VIDEO_ABSTRACTION_LAYER_API = True

########################## SECURITY #######################
ENABLE_MAX_FAILED_LOGIN_ATTEMPTS = False
SQUELCH_PII_IN_LOGS = False
PREVENT_CONCURRENT_LOGINS = False

########################### Milestones #################################
MILESTONES_APP = True

########################### Entrance Exams #################################
ENTRANCE_EXAMS = True

################################ COURSE LICENSES ################################
LICENSING = True


########################## Courseware Search #######################
ENABLE_COURSEWARE_SEARCH = True
ENABLE_COURSEWARE_SEARCH_FOR_COURSE_STAFF = True
SEARCH_ENGINE = 'search.elastic.ElasticSearchEngine'
SEARCH_COURSEWARE_CONTENT_LOG_PARAMS = True

ELASTIC_SEARCH_CONFIG = [
    {
        'use_ssl': False,
        'host': 'edx.devstack.elasticsearch710',
        'port': 9200
    }
]

########################## Dashboard Search #######################
ENABLE_DASHBOARD_SEARCH = False


########################## Certificates Web/HTML View #######################
CERTIFICATES_HTML_VIEW = True


########################## Course Discovery #######################
LANGUAGE_MAP = {
    'terms': dict(ALL_LANGUAGES),  # noqa: F405
    'name': 'Language',
}
COURSE_DISCOVERY_MEANINGS = {
    'org': {
        'name': 'Organization',
    },
    'modes': {
        'name': 'Course Type',
        'terms': {
            'honor': 'Honor',
            'verified': 'Verified',
        },
    },
    'language': LANGUAGE_MAP,
}

ENABLE_COURSE_DISCOVERY = False
# Setting for overriding default filtering facets for Course discovery
# COURSE_DISCOVERY_FILTERS = ["org", "language", "modes"]
COURSES_ARE_BROWSEABLE = True
HOMEPAGE_COURSE_MAX = 9

# Software secure fake page feature flag
ENABLE_SOFTWARE_SECURE_FAKE = True

# Setting for the testing of Software Secure Result Callback
VERIFY_STUDENT["SOFTWARE_SECURE"] = {  # noqa: F405
    "API_ACCESS_KEY": "BBBBBBBBBBBBBBBBBBBB",
    "API_SECRET_KEY": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
}
DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH = "verify_student_disable_account_activation_requirement"

# Skip enrollment start date filtering
SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING = True


########################## Shopping cart ##########################
ENABLE_COSMETIC_DISPLAY_PRICE = True

######################### Program Enrollments #####################
ENABLE_ENROLLMENT_RESET = True

########################## Third Party Auth #######################

if ENABLE_THIRD_PARTY_AUTH and (  # noqa: F405
        'common.djangoapps.third_party_auth.dummy.DummyBackend' not in AUTHENTICATION_BACKENDS  # noqa: F405
):
    AUTHENTICATION_BACKENDS = ['common.djangoapps.third_party_auth.dummy.DummyBackend'] + list(AUTHENTICATION_BACKENDS)  # noqa: F405  # pylint: disable=line-too-long

########################## Authn MFE Context API #######################
ENABLE_DYNAMIC_REGISTRATION_FIELDS = True

############## ECOMMERCE API CONFIGURATION SETTINGS ###############
ECOMMERCE_PUBLIC_URL_ROOT = 'http://localhost:18130'
ECOMMERCE_API_URL = 'http://edx.devstack.ecommerce:18130/api/v2'

############## Comments CONFIGURATION SETTINGS ###############
COMMENTS_SERVICE_URL = 'http://edx.devstack.forum:4567'

############## Credentials CONFIGURATION SETTINGS ###############
CREDENTIALS_INTERNAL_SERVICE_URL = 'http://edx.devstack.credentials:18150'
CREDENTIALS_PUBLIC_SERVICE_URL = 'http://localhost:18150'

############## Exams CONFIGURATION SETTINGS ####################
EXAMS_SERVICE_URL = 'http://localhost:8740/api/v1'

TOKEN_SIGNING.update({  # noqa: F405
    'JWT_PUBLIC_SIGNING_JWK_SET': (
        '{"keys": [{"kid": "localdev_exam_token_key", "e": "AQAB", "kty": "RSA", "n": "o5cn3ljSRi6FaDEKTn0PS-oL9EFyv1pI'
        '7dRgffQLD1qf5D6sprmYfWWokSsrWig8u2y0HChSygR6Jn5KXBqQn6FpM0dDJLnWQDRXHLl3Ey1iPYgDSmOIsIGrV9ZyNCQwk03wAgWbfdBTig'
        '3QSDYD-sTNOs3pc4UD_PqAvU2nz_1SS2ZiOwOn5F6gulE1L0iE3KEUEvOIagfHNVhz0oxa_VRZILkzV-zr6R_TW1m97h4H8jXl_VJyQGyhMGGy'
        'puDrQ9_vaY_RLEulLCyY0INglHWQ7pckxBtI5q55-Vio2wgewe2_qYcGsnBGaDNbySAsvYcWRrqDiFyzrJYivodqTQ"}]}'
    )
})

########################## PROGRAMS LEARNER PORTAL ##############################
LEARNER_PORTAL_URL_ROOT = 'http://localhost:8734'

########################## ENTERPRISE LEARNER PORTAL ##############################
ENTERPRISE_LEARNER_PORTAL_NETLOC = 'localhost:8734'
ENTERPRISE_LEARNER_PORTAL_BASE_URL = 'http://' + ENTERPRISE_LEARNER_PORTAL_NETLOC

########################## ENTERPRISE ADMIN PORTAL ##############################
ENTERPRISE_ADMIN_PORTAL_NETLOC = 'localhost:1991'
ENTERPRISE_ADMIN_PORTAL_BASE_URL = 'http://' + ENTERPRISE_ADMIN_PORTAL_NETLOC

########################## GRADEBOOK APP ##############################
WRITABLE_GRADEBOOK_URL = 'http://localhost:1994/gradebook'

########################## ORA STAFF GRADING APP ##############################
ORA_GRADING_MICROFRONTEND_URL = 'http://localhost:1993'

########################## ORA MFE APP ##############################
ORA_MICROFRONTEND_URL = 'http://localhost:1992'

########################## LEARNER HOME APP ##############################
LEARNER_HOME_MICROFRONTEND_URL = 'http://localhost:1996'

########################## LEARNING MFE ##############################
# pylint: disable=line-too-long
LEARNING_MICROFRONTEND_URL = os.environ.get("LEARNING_MICROFRONTEND_URL", "http://localhost:2000")  # noqa: F405
LEARNING_MICROFRONTEND_NETLOC = os.environ.get("LEARNING_MICROFRONTEND_NETLOC", urlparse(LEARNING_MICROFRONTEND_URL).netloc)  # noqa: F405

###################### Cross-domain requests ######################
ENABLE_CORS_HEADERS = True
CORS_ALLOW_CREDENTIALS = True
CORS_ORIGIN_WHITELIST = ()
CORS_ORIGIN_ALLOW_ALL = True

LOGIN_REDIRECT_WHITELIST.extend([  # noqa: F405
    CMS_BASE,
    # Allow redirection to all micro-frontends.
    # Please add your MFE if is not already listed here.
    # Note: For this to work, the MFE must set BASE_URL in its .env.development to:
    #   BASE_URL=http://localhost:$PORT
    # as opposed to:
    #   BASE_URL=localhost:$PORT
    'localhost:18000',  # lms
    'localhost:18130',  # ecommerce
    'localhost:1997',  # frontend-app-account
    'localhost:1976',  # frontend-app-program-console
    'localhost:1994',  # frontend-app-gradebook
    LEARNING_MICROFRONTEND_NETLOC,  # frontend-app-learning
    'localhost:2001',  # frontend-app-course-authoring
    'localhost:3001',  # frontend-app-library-authoring
    'localhost:18400',  # frontend-app-publisher
    'localhost:1993',  # frontend-app-ora-grading
    'localhost:1996',  # frontend-app-learner-dashboard
    ENTERPRISE_LEARNER_PORTAL_NETLOC,  # frontend-app-learner-portal-enterprise
    ENTERPRISE_ADMIN_PORTAL_NETLOC,  # frontend-app-admin-portal
])

###################### JWTs ######################
JWT_AUTH.update({  # noqa: F405
    'JWT_AUDIENCE': 'lms-key',
    'JWT_ISSUER': f'{LMS_ROOT_URL}/oauth2',
    'JWT_ISSUERS': [{
        'AUDIENCE': 'lms-key',
        'ISSUER': f'{LMS_ROOT_URL}/oauth2',
        'SECRET_KEY': 'lms-secret',
    }],
    'JWT_SECRET_KEY': 'lms-secret',
    'JWT_SIGNING_ALGORITHM': 'RS512',
    'JWT_PRIVATE_SIGNING_JWK': """
        {
            "kid": "devstack_key",
            "kty": "RSA",
            "key_ops": [
                "sign"
            ],
            "n": "smKFSYowG6nNUAdeqH1jQQnH1PmIHphzBmwJ5vRf1vu48BUI5VcVtUWIPqzRK_LDSlZYh9D0YFL0ZTxIrlb6Tn3Xz7pYvpIAeYuQv3_H5p8tbz7Fb8r63c1828wXPITVTv8f7oxx5W3lFFgpFAyYMmROC4Ee9qG5T38LFe8_oAuFCEntimWxN9F3P-FJQy43TL7wG54WodgiM0EgzkeLr5K6cDnyckWjTuZbWI-4ffcTgTZsL_Kq1owa_J2ngEfxMCObnzGy5ZLcTUomo4rZLjghVpq6KZxfS6I1Vz79ZsMVUWEdXOYePCKKsrQG20ogQEkmTf9FT_SouC6jPcHLXw",
            "e": "AQAB",
            "d": "RQ6k4NpRU3RB2lhwCbQ452W86bMMQiPsa7EJiFJUg-qBJthN0FMNQVbArtrCQ0xA1BdnQHThFiUnHcXfsTZUwmwvTuiqEGR_MI6aI7h5D8vRj_5x-pxOz-0MCB8TY8dcuK9FkljmgtYvV9flVzCk_uUb3ZJIBVyIW8En7n7nV7JXpS9zey1yVLld2AbRG6W5--Pgqr9JCI5-bLdc2otCLuen2sKyuUDHO5NIj30qGTaKUL-OW_PgVmxrwKwccF3w5uGNEvMQ-IcicosCOvzBwdIm1uhdm9rnHU1-fXz8VLRHNhGVv7z6moghjNI0_u4smhUkEsYeshPv7RQEWTdkOQ",
            "p": "7KWj7l-ZkfCElyfvwsl7kiosvi-ppOO7Imsv90cribf88DexcO67xdMPesjM9Nh5X209IT-TzbsOtVTXSQyEsy42NY72WETnd1_nAGLAmfxGdo8VV4ZDnRsA8N8POnWjRDwYlVBUEEeuT_MtMWzwIKU94bzkWVnHCY5vbhBYLeM",
            "q": "wPkfnjavNV1Hqb5Qqj2crBS9HQS6GDQIZ7WF9hlBb2ofDNe2K2dunddFqCOdvLXr7ydRcK51ZwSeHjcjgD1aJkHA9i1zqyboxgd0uAbxVDo6ohnlVqYLtap2tXXcavKm4C9MTpob_rk6FBfEuq4uSsuxFvCER4yG3CYBBa4gZVU",
            "dp": "MO9Ppss-Bl-mC1vGyJDBbMgr2GgivGYbHFLt6ERfTGsvcr0RhDjZu16ZpNpBB6B7-K-uJGHxPmmf8P9KRWDBUAwOSaT2a-pTsuux6PKCwVTZfUq5LxAkiyg6WZTGoWASEtoae0XRHEy2TvIKNl5AiX-h_DwDPDbEYcWCZVAb6-E",
            "dq": "m03j7GkGSWRxMGNCeEBtvvBR4vDS9Her7AtjbNSWnRxDMQrKSdRMaiu-m7tOT3n6D9cM7Cr7wZUtzBOENskprHBu47FgzfXakMWfYhv0TV0voxZERKAN_H7cWt4oLsprEzH9r6THsxFPdKxMYBGeoAOe2l9nlk26m6LaX7_rwqE",
            "qi": "jnJ0nfARyAcHsezENNrXKnDM-LrMJWMHPh_70ZM_pF5iRMOLojHkTVsUIzYi6Uj2ohX9Jz1zsV207kCuPqQXURbhlt1xEaktwCmySeWU4qkMTptWp4ya2jEwGn8EKJ1iEc0GhDkRyLrgm4ol-sq9DMaKEkhTGy4Y3-8mMCBVqeQ"
        }
""",
    'JWT_PUBLIC_SIGNING_JWK_SET': (
        '{"keys": [{"kid": "devstack_key", "e": "AQAB", "kty": "RSA", "n": "smKFSYowG6nNUAdeqH1jQQnH1PmIHphzBmwJ5vRf1vu'
        '48BUI5VcVtUWIPqzRK_LDSlZYh9D0YFL0ZTxIrlb6Tn3Xz7pYvpIAeYuQv3_H5p8tbz7Fb8r63c1828wXPITVTv8f7oxx5W3lFFgpFAyYMmROC'
        '4Ee9qG5T38LFe8_oAuFCEntimWxN9F3P-FJQy43TL7wG54WodgiM0EgzkeLr5K6cDnyckWjTuZbWI-4ffcTgTZsL_Kq1owa_J2ngEfxMCObnzG'
        'y5ZLcTUomo4rZLjghVpq6KZxfS6I1Vz79ZsMVUWEdXOYePCKKsrQG20ogQEkmTf9FT_SouC6jPcHLXw"}]}'
    ),
})
add_plugins(__name__, ProjectType.LMS, SettingsType.DEVSTACK)


######################### Django Rest Framework ########################

REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] += (  # noqa: F405
    'rest_framework.renderers.BrowsableAPIRenderer',
)

OPENAPI_CACHE_TIMEOUT = 0

#####################################################################
# set replica set of contentstore to none as we haven't setup any for lms in devstack
CONTENTSTORE['DOC_STORE_CONFIG']['replicaSet'] = None  # noqa: F405

#####################################################################
# set replica sets of moduelstore to none as we haven't setup any for lms in devstack
for store in MODULESTORE['default']['OPTIONS']['stores']:  # noqa: F405
    if 'DOC_STORE_CONFIG' in store and 'replicaSet' in store['DOC_STORE_CONFIG']:
        store['DOC_STORE_CONFIG']['replicaSet'] = None


#####################################################################
# Lastly, run any migrations, if needed.
MODULESTORE = convert_module_store_setting_if_needed(MODULESTORE)  # noqa: F405


SECRET_KEY = '85920908f28904ed733fe576320db18cabd7b6cd'

EDXNOTES_INTERNAL_API = 'http://edx.devstack.edxnotesapi:18120/api/v1'
EDXNOTES_CLIENT_NAME = 'edx_notes_api-backend-service'

############## Settings for Microfrontends  #########################
LEARNING_MICROFRONTEND_URL = 'http://localhost:2000'
ACCOUNT_MICROFRONTEND_URL = 'http://localhost:1997'
PROFILE_MICROFRONTEND_URL = 'http://localhost:1995'
COMMUNICATIONS_MICROFRONTEND_URL = 'http://localhost:1984'
AUTHN_MICROFRONTEND_URL = 'http://localhost:1999'
AUTHN_MICROFRONTEND_DOMAIN = 'localhost:1999'
EXAMS_DASHBOARD_MICROFRONTEND_URL = 'http://localhost:2020'
INSTRUCTOR_MICROFRONTEND_URL = 'http://localhost:2003/instructor-dashboard'
CATALOG_MICROFRONTEND_URL = 'http://localhost:1998/catalog'
COURSE_AUTHORING_MICROFRONTEND_URL = 'http://localhost:2001/authoring'
ADMIN_CONSOLE_MICROFRONTEND_URL = 'http://localhost:2025/admin-console'

################### FRONTEND APPLICATION DISCUSSIONS ###################
DISCUSSIONS_MICROFRONTEND_URL = 'http://localhost:2002'

################### FRONTEND APPLICATION DISCUSSIONS FEEDBACK URL###################
DISCUSSIONS_MFE_FEEDBACK_URL = None

DISCUSSION_SPAM_URLS = []

############## Docker based devstack settings #######################

AUTOMATIC_AUTH_FOR_TESTING = True
ENABLE_DISCUSSION_SERVICE = True
SHOW_HEADER_LANGUAGE_SELECTOR = True

# Enable enterprise integration by default.
# See https://github.com/openedx/edx-enterprise/blob/master/docs/development.rst for
# more background on edx-enterprise.
# Toggle this off if you don't want anything to do with enterprise in devstack.
ENABLE_ENTERPRISE_INTEGRATION = True

ENABLE_MKTG_SITE = os.environ.get('ENABLE_MARKETING_SITE', False)  # noqa: F405
MARKETING_SITE_ROOT = os.environ.get('MARKETING_SITE_ROOT', 'http://localhost:8080')  # noqa: F405

MKTG_URLS = {
    'ABOUT': '/about',
    'ACCESSIBILITY': '/accessibility',
    'AFFILIATES': '/affiliate-program',
    'BLOG': '/blog',
    'CAREERS': '/careers',
    'CONTACT': '/support/contact_us',
    'COURSES': '/course',
    'DONATE': '/donate',
    'ENTERPRISE': '/enterprise',
    'FAQ': '/student-faq',
    'HONOR': '/edx-terms-service',
    'HOW_IT_WORKS': '/how-it-works',
    'MEDIA_KIT': '/media-kit',
    'NEWS': '/news-announcements',
    'PRESS': '/press',
    'PRIVACY': '/edx-privacy-policy',
    'ROOT': MARKETING_SITE_ROOT,
    'SCHOOLS': '/schools-partners',
    'SITE_MAP': '/sitemap',
    'TRADEMARKS': '/trademarks',
    'TOS': '/edx-terms-service',
    'TOS_AND_HONOR': '/edx-terms-service',
    'WHAT_IS_VERIFIED_CERT': '/verified-certificate',
    'PROGRAM_SUBSCRIPTIONS': '/program-subscriptions',
}

ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS = {}

ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL = "http://edx.devstack.lms:18000/oauth2"

CREDENTIALS_SERVICE_USERNAME = 'credentials_worker'

COURSE_CATALOG_URL_ROOT = 'http://edx.devstack.discovery:18381'
COURSE_CATALOG_API_URL = f'{COURSE_CATALOG_URL_ROOT}/api/v1'

SYSTEM_WIDE_ROLE_CLASSES = os.environ.get("SYSTEM_WIDE_ROLE_CLASSES", SYSTEM_WIDE_ROLE_CLASSES)  # noqa: F405
SYSTEM_WIDE_ROLE_CLASSES.append(
    'system_wide_roles.SystemWideRoleAssignment',
)

if ENABLE_ENTERPRISE_INTEGRATION:
    SYSTEM_WIDE_ROLE_CLASSES.append(
        'enterprise.SystemWideEnterpriseUserRoleAssignment',
    )

#####################################################################

# django-session-cookie middleware
DCS_SESSION_COOKIE_SAMESITE = 'Lax'
DCS_SESSION_COOKIE_SAMESITE_FORCE_ALL = True

########################## THEMING  #######################
# If you want to enable theming in devstack, uncomment this section and add any relevant
# theme directories to COMPREHENSIVE_THEME_DIRS

# We have to import the make_mako_template_dirs method here because production.py calls
# derive_settings('lms.envs.production') which runs make_mako_template_dirs with
# the settings from production, which doesn't include these theming settings. Thus,
# the templating engine is unable to find the themed templates because they don't exist
# in it's path. Re-calling derive_settings doesn't work because the settings was already
# changed from a function to a list, and it can't be derived again.

# from openedx.envs.common import make_mako_template_dirs
# ENABLE_COMPREHENSIVE_THEMING = True
# COMPREHENSIVE_THEME_DIRS = [
#     "/edx/app/edxapp/edx-platform/themes/"
# ]
# TEMPLATES[1]["DIRS"] = make_mako_template_dirs
# derive_settings(__name__)

# Uncomment the lines below if you'd like to see SQL statements in your devstack LMS log.
# LOGGING['handlers']['console']['level'] = 'DEBUG'
# LOGGING['loggers']['django.db.backends'] = {'handlers': ['console'], 'level': 'DEBUG', 'propagate': False}

################### Special Exams (Proctoring) and Prereqs ###################
ENABLE_SPECIAL_EXAMS = True
ENABLE_PREREQUISITE_COURSES = True

# Used in edx-proctoring for ID generation in lieu of SECRET_KEY - dummy value
# (ref MST-637)
PROCTORING_USER_OBFUSCATION_KEY = '85920908f28904ed733fe576320db18cabd7b6cd'

#################### Webpack Configuration Settings ##############################
WEBPACK_LOADER['DEFAULT']['TIMEOUT'] = 5  # noqa: F405

#################### Network configuration ####################
# Devstack is directly exposed to the caller
CLOSEST_CLIENT_IP_FROM_HEADERS = []

#################### Event bus backend ########################
EVENT_BUS_PRODUCER = 'edx_event_bus_redis.create_producer'
EVENT_BUS_REDIS_CONNECTION_URL = 'redis://:password@edx.devstack.redis:6379/'
EVENT_BUS_TOPIC_PREFIX = 'dev'
EVENT_BUS_CONSUMER = 'edx_event_bus_redis.RedisEventConsumer'

certificate_revoked_event_config = EVENT_BUS_PRODUCER_CONFIG['org.openedx.learning.certificate.revoked.v1']  # noqa: F405
certificate_revoked_event_config['learning-certificate-lifecycle']['enabled'] = True
certificate_created_event_config = EVENT_BUS_PRODUCER_CONFIG['org.openedx.learning.certificate.created.v1']  # noqa: F405
certificate_created_event_config['learning-certificate-lifecycle']['enabled'] = True

course_access_role_added_event_setting = EVENT_BUS_PRODUCER_CONFIG[  # noqa: F405
    'org.openedx.learning.user.course_access_role.added.v1'
]
course_access_role_added_event_setting['learning-course-access-role-lifecycle']['enabled'] = True
course_access_role_removed_event_setting = EVENT_BUS_PRODUCER_CONFIG[  # noqa: F405
    'org.openedx.learning.user.course_access_role.removed.v1'
]
course_access_role_removed_event_setting['learning-course-access-role-lifecycle']['enabled'] = True

lc_enrollment_revoked_setting = \
    EVENT_BUS_PRODUCER_CONFIG['org.openedx.enterprise.learner_credit_course_enrollment.revoked.v1']  # noqa: F405
lc_enrollment_revoked_setting['learner-credit-course-enrollment-lifecycle']['enabled'] = True

# API access management
API_ACCESS_MANAGER_EMAIL = 'api-access@example.com'
API_ACCESS_FROM_EMAIL = 'api-requests@example.com'
API_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/'
AUTH_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/authentication/index.html'

############################ AI_TRANSLATIONS ##################################
AI_TRANSLATIONS_API_URL = 'http://localhost:18760/api/v1'

############################ CSRF ##################################

# MFEs that will call this service in devstack
CSRF_TRUSTED_ORIGINS = [
    LEARNING_MICROFRONTEND_URL,  # frontend-app-learning
    'http://localhost:2001',  # frontend-app-course-authoring
    'http://localhost:1997',  # frontend-app-account
    'http://localhost:1995',  # frontend-app-profile
    'http://localhost:1992',  # frontend-app-ora
    'http://localhost:2002',  # frontend-app-discussions
    'http://localhost:1991',  # frontend-app-admin-portal
    'http://localhost:8734',  # frontend-app-learner-portal-enterprise
    'http://localhost:1999',  # frontend-app-authn
    'http://localhost:18450',  # frontend-app-support-tools
    'http://localhost:1994',  # frontend-app-gradebook
    'http://localhost:1996',  # frontend-app-learner-dashboard
]

RETIREMENT_STATES = [
    'PENDING',
    'LOCKING_ACCOUNT',
    'LOCKING_COMPLETE',
    'RETIRING_ENROLLMENTS',
    'ENROLLMENTS_COMPLETE',
    'RETIRING_LMS_MISC',
    'LMS_MISC_COMPLETE',
    'RETIRING_LMS',
    'LMS_COMPLETE',
    'ERRORED',
    'ABORTED',
    'COMPLETE',
]

################# New settings must go ABOVE this line #################
########################################################################
# See if the developer has any local overrides.
if os.path.isfile(join(dirname(abspath(__file__)), 'private.py')):  # noqa: F405
    from .private import *  # pylint: disable=import-error,wildcard-import  # noqa: F403
