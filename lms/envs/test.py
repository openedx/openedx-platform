"""
This config file runs the simplest dev environment using sqlite, and db-based
sessions. Assumes structure:

/envroot/
        /db   # This is where it'll write the database file
        /edx-platform  # The location of this repo
        /log  # Where we're going to write log files
"""

# We intentionally define lots of variables that aren't used, and
# want to import all variables from base settings files
# pylint: disable=wildcard-import, unused-wildcard-import


import logging
from collections import OrderedDict

from edx_django_utils.plugins import add_plugins

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType
from openedx.core.lib.derived import derive_settings
from openedx.core.lib.tempdir import mkdtemp_clean
from xmodule.modulestore.modulestore_settings import update_module_store_settings  # lint-amnesty, pylint: disable=wrong-import-order

from openedx.core.lib.features_setting_proxy import FeaturesProxy

from .common import *

from openedx.envs.test import *


# A proxy for feature flags stored in the settings namespace
FEATURES = FeaturesProxy(globals())

# Silence noisy logs to make troubleshooting easier when tests fail.
LOG_OVERRIDES = [
    ('factory.generate', logging.ERROR),
    ('factory.containers', logging.ERROR),
]
for log_name, log_level in LOG_OVERRIDES:
    logging.getLogger(log_name).setLevel(log_level)

# can't test start dates with this True, but on the other hand,
# can test everything else :)
DISABLE_START_DATES = True

ENABLE_VERIFIED_CERTIFICATES = True

ENABLE_BULK_ENROLLMENT_VIEW = True

ENABLE_BULK_USER_RETIREMENT = True

COMMON_TEST_DATA_ROOT = COMMON_ROOT / "test" / "data"

COMPREHENSIVE_THEME_DIRS = [REPO_ROOT / "themes", REPO_ROOT / "common/test"]

WEBPACK_LOADER['DEFAULT']['LOADER_CLASS'] = 'webpack_loader.loader.FakeWebpackLoader'

STATUS_MESSAGE_PATH = TEST_ROOT / "status_message.json"

COURSES_ROOT = TEST_ROOT / "data"

# Where the content data is checked out.
GITHUB_REPO_ROOT = ENV_ROOT / "data"

USE_I18N = True
LANGUAGE_CODE = 'en'  # tests assume they will get English.

XQUEUE_INTERFACE = {
    "url": "http://sandbox-xqueue.edx.org",
    "django_auth": {
        "username": "lms",
        "password": "***REMOVED***"
    },
    "basic_auth": ('anant', 'agarwal'),
}
XQUEUE_WAITTIME_BETWEEN_REQUESTS = 5  # seconds

# Don't rely on a real staff grading backend
MOCK_STAFF_GRADING = True
MOCK_PEER_GRADING = True

COMMENTS_SERVICE_URL = 'http://localhost:4567'

DJFS = {
    'type': 'osfs',
    'directory_root': f'{DATA_DIR}/django-pyfs/static/django-pyfs',
    'url_root': '/static/django-pyfs',
}

API_ACCESS_MANAGER_EMAIL = 'api-access@example.com'

############################ STATIC FILES #############################

# Avoid having to run collectstatic before the unit test suite
# If we don't add these settings, then Django templates that can't
# find pipelined assets will raise a ValueError.
# http://stackoverflow.com/questions/12816941/unit-testing-with-django-pipeline
STORAGES['staticfiles']['BACKEND'] = 'pipeline.storage.NonPackagingPipelineStorage'

# Don't use compression during tests
PIPELINE['JS_COMPRESSOR'] = None

update_module_store_settings(
    MODULESTORE,
    module_store_options={
        'fs_root': TEST_ROOT / "data",
    },
    xml_store_options={
        'data_dir': mkdtemp_clean(dir=TEST_ROOT),  # never inadvertently load all the XML courses
    },
    doc_store_settings={
        'host': MONGO_HOST,
        'port': MONGO_PORT_NUM,
        'db': f'test_xmodule_{THIS_UUID}',
        'collection': 'test_modulestore',
    },
)

CONTENTSTORE = {
    'ENGINE': 'xmodule.contentstore.mongo.MongoContentStore',
    'DOC_STORE_CONFIG': {
        'host': MONGO_HOST,
        'db': f'test_xcontent_{THIS_UUID}',
        'port': MONGO_PORT_NUM,
    }
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'ATOMIC_REQUESTS': True,
    },
    'student_module_history': {
        'ENGINE': 'django.db.backends.sqlite3',
    },
}

CACHES = {
    # This is the cache used for most things.
    # In staging/prod envs, the sessions also live here.
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    },

    # The general cache is what you get if you use our util.cache. It's used for
    # things like caching the course.xml file for different A/B test groups.
    # We set it to be a DummyCache to force reloading of course.xml in dev.
    # In staging environments, we would grab VERSION from data uploaded by the
    # push process.
    'general': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    },

    'mongo_metadata_inheritance': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    },
    'loc_cache': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    },
    'course_structure_cache': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    },
}

############################# SECURITY SETTINGS ################################
# Default to advanced security in common.py, so tests can reset here to use
# a simpler security model
ENFORCE_PASSWORD_POLICY = False
ENABLE_MAX_FAILED_LOGIN_ATTEMPTS = False
SQUELCH_PII_IN_LOGS = False
PREVENT_CONCURRENT_LOGINS = False

######### Third-party auth ##########
ENABLE_THIRD_PARTY_AUTH = True

AUTHENTICATION_BACKENDS = [
    'social_core.backends.google.GoogleOAuth2',
    'social_core.backends.linkedin.LinkedinOAuth2',
    'social_core.backends.facebook.FacebookOAuth2',
    'social_core.backends.azuread.AzureADOAuth2',
    'social_core.backends.twitter.TwitterOAuth',
    'common.djangoapps.third_party_auth.identityserver3.IdentityServer3',
    'common.djangoapps.third_party_auth.dummy.DummyBackend',
    'common.djangoapps.third_party_auth.saml.SAMLAuthBackend',
    'common.djangoapps.third_party_auth.lti.LTIAuthBackend',
] + AUTHENTICATION_BACKENDS

THIRD_PARTY_AUTH_CUSTOM_AUTH_FORMS = {
    'custom1': {
        'secret_key': 'opensesame',
        'url': '/misc/my-custom-registration-form',
        'error_url': '/misc/my-custom-sso-error-page'
    },
}

############################## OAUTH2 Provider ################################
OAUTH_ENFORCE_SECURE = False

########################### External REST APIs #################################
ENABLE_MOBILE_REST_API = True
ENABLE_VIDEO_ABSTRACTION_LAYER_API = True

######################### MARKETING SITE ###############################

MKTG_URL_LINK_MAP = {
    'ABOUT': 'about',
    'CONTACT': 'contact',
    'HELP_CENTER': 'help-center',
    'COURSES': 'courses',
    'ROOT': 'root',
    'TOS': 'tos',
    'HONOR': 'honor',
    'PRIVACY': 'privacy',
    'CAREERS': 'careers',
    'NEWS': 'news',
    'PRESS': 'press',
    'BLOG': 'blog',
    'DONATE': 'donate',
    'SITEMAP.XML': 'sitemap_xml',

    # Verified Certificates
    'WHAT_IS_VERIFIED_CERT': 'verified-certificate',
}

SUPPORT_SITE_LINK = 'https://example.support.edx.org'
PASSWORD_RESET_SUPPORT_LINK = 'https://support.example.com/password-reset-help.html'
ACTIVATION_EMAIL_SUPPORT_LINK = 'https://support.example.com/activation-email-help.html'
SEND_ACTIVATION_EMAIL_URL = 'https://courses.example.edx.org/api/send_account_activation_email'
ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS = OrderedDict([
    ("utm_campaign", "edX.org Referral"),
    ("utm_source", "edX.org"),
    ("utm_medium", "Footer"),
])

############### Module Store Items ##########
HOSTNAME_MODULESTORE_DEFAULT_MAPPINGS = {}

### This enables the Metrics tab for the Instructor dashboard ###########
CLASS_DASHBOARD = True

# add extra template directory for test-only templates
MAKO_TEMPLATE_DIRS_BASE.extend([
    COMMON_ROOT / 'test' / 'templates',
    COMMON_ROOT / 'test' / 'test_sites',
    REPO_ROOT / 'openedx' / 'core' / 'djangolib' / 'tests' / 'templates',
])

# Setting for the testing of Software Secure Result Callback
VERIFY_STUDENT["SOFTWARE_SECURE"] = {
    "API_ACCESS_KEY": "BBBBBBBBBBBBBBBBBBBB",
    "API_SECRET_KEY": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
}

######### dashboard git log settings #########
MONGODB_LOG = {
    'host': MONGO_HOST,
    'port': MONGO_PORT_NUM,
    'user': '',
    'password': '',
    'db': 'xlog',
}

NOTES_DISABLED_TABS = []

# Enable courseware search for tests
ENABLE_COURSEWARE_SEARCH = True

# Enable dashboard search for tests
ENABLE_DASHBOARD_SEARCH = True

FACEBOOK_APP_SECRET = "Test"
FACEBOOK_APP_ID = "Test"
FACEBOOK_API_VERSION = "v2.8"

######### custom courses #########
INSTALLED_APPS += ['lms.djangoapps.ccx', 'openedx.core.djangoapps.ccxcon.apps.CCXConnectorConfig']

# Set dummy values for profile image settings.
PROFILE_IMAGE_BACKEND = {
    'class': 'openedx.core.storage.OverwriteStorage',
    'options': {
        'location': MEDIA_ROOT,
        'base_url': 'http://example-storage.com/profile-images/',
    },
}
PROFILE_IMAGE_DEFAULT_FILENAME = 'default'
PROFILE_IMAGE_DEFAULT_FILE_EXTENSION = 'png'
PROFILE_IMAGE_HASH_SEED = 'secret'
PROFILE_IMAGE_MAX_BYTES = 1024 * 1024
PROFILE_IMAGE_MIN_BYTES = 100

# Enable the LTI provider feature for testing
ENABLE_LTI_PROVIDER = True
INSTALLED_APPS.append('lms.djangoapps.lti_provider.apps.LtiProviderConfig')
AUTHENTICATION_BACKENDS.append('lms.djangoapps.lti_provider.users.LtiBackend')

# Financial assistance page
ENABLE_FINANCIAL_ASSISTANCE_FORM = True

COURSE_BLOCKS_API_EXTRA_FIELDS = [
    ('course', 'course_visibility'),
    ('course', 'other_course_settings'),
]

COURSE_CATALOG_URL_ROOT = 'https://catalog.example.com'
COURSE_CATALOG_API_URL = f'{COURSE_CATALOG_URL_ROOT}/api/v1'

COMPREHENSIVE_THEME_LOCALE_PATHS = [REPO_ROOT / "themes/conf/locale", ]

PREPEND_LOCALE_PATHS = []

LMS_ROOT_URL = "http://localhost:8000"

# Needed for derived settings used by cms only.
FRONTEND_LOGIN_URL = '/login'
FRONTEND_LOGOUT_URL = '/logout'
FRONTEND_REGISTER_URL = '/register'

# Programs Learner Portal URL
LEARNER_PORTAL_URL_ROOT = 'http://localhost:8734'

ECOMMERCE_PUBLIC_URL_ROOT = None
ENTERPRISE_API_URL = 'http://enterprise.example.com/enterprise/api/v1/'
ENTERPRISE_CONSENT_API_URL = 'http://enterprise.example.com/consent/api/v1/'

########################## ENTERPRISE LEARNER PORTAL ##############################
ENTERPRISE_LEARNER_PORTAL_NETLOC = 'example.com:8734'
ENTERPRISE_LEARNER_PORTAL_BASE_URL = 'http://' + ENTERPRISE_LEARNER_PORTAL_NETLOC

ACTIVATION_EMAIL_FROM_ADDRESS = 'test_activate@edx.org'

TEMPLATES[0]['OPTIONS']['debug'] = True
TEMPLATES.append(
    {
        # This separate copy of the Mako backend is used to test rendering previews in the 'lms.main' namespace
        'NAME': 'preview',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        'APP_DIRS': False,
        'DIRS': MAKO_TEMPLATE_DIRS_BASE,
        'OPTIONS': {
            'context_processors': CONTEXT_PROCESSORS,
            'debug': False,
            'namespace': 'lms.main',
        }
    }
)

############################## Authentication ##############################

# Most of the JWT_AUTH settings come from lms/envs/common.py (from openedx/envs/common.py),
# but here we update to use JWKS values from openedx/envs/test.py for testing.
JWT_AUTH.update(jwt_jwks_values)

####################### Plugin Settings ##########################

add_plugins(__name__, ProjectType.LMS, SettingsType.TEST)

########################## Derive Any Derived Settings  #######################

derive_settings(__name__)

############################ STATIC FILES #############################
STATICFILES_DIRS.append(("uploads", MEDIA_ROOT))

_NEW_STATICFILES_DIRS = []
# Strip out any static files that aren't in the repository root
# so that the tests can run with only the edx-platform directory checked out
for static_dir in STATICFILES_DIRS:  # pylint: disable=not-an-iterable
    # Handle both tuples and non-tuple directory definitions
    try:
        _, data_dir = static_dir
    except ValueError:
        data_dir = static_dir

    if data_dir.startswith(REPO_ROOT):
        _NEW_STATICFILES_DIRS.append(static_dir)
STATICFILES_DIRS = _NEW_STATICFILES_DIRS

FILE_UPLOAD_TEMP_DIR = TEST_ROOT / "uploads"
FILE_UPLOAD_HANDLERS = [
    'django.core.files.uploadhandler.MemoryFileUploadHandler',
    'django.core.files.uploadhandler.TemporaryFileUploadHandler',
]

# Configuration used for generating PDF Receipts/Invoices

PDF_RECEIPT_TAX_ID = 'add here'
PDF_RECEIPT_FOOTER_TEXT = 'add your own specific footer text here'
PDF_RECEIPT_DISCLAIMER_TEXT = 'add your own specific disclaimer text here'
PDF_RECEIPT_BILLING_ADDRESS = 'add your own billing address here with appropriate line feed characters'
PDF_RECEIPT_TERMS_AND_CONDITIONS = 'add your own terms and conditions'
PDF_RECEIPT_TAX_ID_LABEL = 'Tax ID'

AUTHN_MICROFRONTEND_URL = "http://authn-mfe"
AUTHN_MICROFRONTEND_DOMAIN = "authn-mfe"
LEARNER_HOME_MICROFRONTEND_URL = "http://learner-home-mfe"
ORA_GRADING_MICROFRONTEND_URL = "http://ora-grading-mfe"
ORA_MICROFRONTEND_URL = "http://ora-mfe"

########################## limiting dashboard courses ######################

DASHBOARD_COURSE_LIMIT = 250

########################## Settings for proctoring ######################
PROCTORING_SETTINGS = {
    'LINK_URLS': {
        'faq': 'https://support.example.com/proctoring-faq.html'
    }
}

############## Exams CONFIGURATION SETTINGS ####################
EXAMS_SERVICE_URL = 'http://exams.example.com/api/v1'

############### Settings for Django Rate limit #####################

RATELIMIT_RATE = '2/m'

##### LOGISTRATION RATE LIMIT SETTINGS #####
LOGIN_AND_REGISTER_FORM_RATELIMIT = '5/5m'

CORS_ORIGIN_WHITELIST = ['https://sandbox.edx.org']

################## MFE API ####################
ENABLE_MFE_CONFIG_API = True
MFE_CONFIG = {
    "BASE_URL": "https://name_of_mfe.example.com",
    "LANGUAGE_PREFERENCE_COOKIE_NAME": "example-language-preference",
    "LOGO_URL": "https://courses.example.com/logo.png"
}

MFE_CONFIG_OVERRIDES = {
    "mymfe": {
        "LANGUAGE_PREFERENCE_COOKIE_NAME": "mymfe-language-preference",
        "LOGO_URL": "https://courses.example.com/mymfe-logo.png",
    },
    "yourmfe": {
        "LANGUAGE_PREFERENCE_COOKIE_NAME": "yourmfe-language-preference",
        "LOGO_URL": "https://courses.example.com/yourmfe-logo.png",
    },
}

############## Settings for survey report ##############
SURVEY_REPORT_EXTRA_DATA = {}
SURVEY_REPORT_ENDPOINT = "https://example.com/survey_report"
SURVEY_REPORT_CHECK_THRESHOLD = 6
SURVEY_REPORT_ENABLE = True
ANONYMOUS_SURVEY_REPORT = False

CSRF_TRUSTED_ORIGINS = ['https://*.example.com']

############## Settings for JWT token handling ##############
TOKEN_SIGNING = {
    'JWT_ISSUER': 'token-test-issuer',
    'JWT_SIGNING_ALGORITHM': 'RS512',
    'JWT_SUPPORTED_VERSION': '1.2.0',
    'JWT_PRIVATE_SIGNING_JWK': """
        {
            "kid": "token-test-sign",
            "kty": "RSA",
            "key_ops": [
                "sign"
            ],
            "n": "o5cn3ljSRi6FaDEKTn0PS-oL9EFyv1pI7dRgffQLD1qf5D6sprmYfWWokSsrWig8u2y0HChSygR6Jn5KXBqQn6FpM0dDJLnWQDRXHLl3Ey1iPYgDSmOIsIGrV9ZyNCQwk03wAgWbfdBTig3QSDYD-sTNOs3pc4UD_PqAvU2nz_1SS2ZiOwOn5F6gulE1L0iE3KEUEvOIagfHNVhz0oxa_VRZILkzV-zr6R_TW1m97h4H8jXl_VJyQGyhMGGypuDrQ9_vaY_RLEulLCyY0INglHWQ7pckxBtI5q55-Vio2wgewe2_qYcGsnBGaDNbySAsvYcWRrqDiFyzrJYivodqTQ",
            "e": "AQAB",
            "d": "HIiV7KNjcdhVbpn3KT-I9n3JPf5YbGXsCIedmPqDH1d4QhBofuAqZ9zebQuxkRUpmqtYMv0Zi6ECSUqH387GYQF_XvFUFcjQRPycISd8TH0DAKaDpGr-AYNshnKiEtQpINhcP44I1AYNPCwyoxXA1fGTtmkKChsuWea7o8kytwU5xSejvh5-jiqu2SF4GEl0BEXIAPZsgbzoPIWNxgO4_RzNnWs6nJZeszcaDD0CyezVSuH9QcI6g5QFzAC_YuykSsaaFJhZ05DocBsLczShJ9Omf6PnK9xlm26I84xrEh_7x4fVmNBg3xWTLh8qOnHqGko93A1diLRCrKHOvnpvgQ",
            "p": "3T3DEtBUka7hLGdIsDlC96Uadx_q_E4Vb1cxx_4Ss_wGp1Loz3N3ZngGyInsKlmbBgLo1Ykd6T9TRvRNEWEtFSOcm2INIBoVoXk7W5RuPa8Cgq2tjQj9ziGQ08JMejrPlj3Q1wmALJr5VTfvSYBu0WkljhKNCy1KB6fCby0C9WE",
            "q": "vUqzWPZnDG4IXyo-k5F0bHV0BNL_pVhQoLW7eyFHnw74IOEfSbdsMspNcPSFIrtgPsn7981qv3lN_staZ6JflKfHayjB_lvltHyZxfl0dvruShZOx1N6ykEo7YrAskC_qxUyrIvqmJ64zPW3jkuOYrFs7Ykj3zFx3Zq1H5568G0",
            "dp": "Azh08H8r2_sJuBXAzx_mQ6iZnAZQ619PnJFOXjTqnMgcaK8iSHLL2CgDIUQwteUcBphgP0uBrfWIBs5jmM8rUtVz4CcrPb5jdjhHjuu4NxmnFbPlhNoOp8OBUjPP3S-h-fPoaFjxDrUqz_zCdPVzp4S6UTkf6Hu-SiI9CFVFZ8E",
            "dq": "WQ44_KTIbIej9qnYUPMA1DoaAF8ImVDIdiOp9c79dC7FvCpN3w-lnuugrYDM1j9Tk5bRrY7-JuE6OaKQgOtajoS1BIxjYHj5xAVPD15CVevOihqeq5Zx0ZAAYmmCKRrfUe0iLx2QnIcoKH1-Azs23OXeeo6nysznZjvv9NVJv60",
            "qi": "KSWGH607H1kNG2okjYdmVdNgLxTUB-Wye9a9FNFE49UmQIOJeZYXtDzcjk8IiK3g-EU3CqBeDKVUgHvHFu4_Wj3IrIhKYizS4BeFmOcPDvylDQCmJcC9tXLQgHkxM_MEJ7iLn9FOLRshh7GPgZphXxMhezM26Cz-8r3_mACHu84"
        }
    """,  # noqa: E501,

    'JWT_PUBLIC_SIGNING_JWK_SET': """
        {
          "keys": [
            {
              "kid": "token-test-sign",
              "kty": "RSA",
              "n": "o5cn3ljSRi6FaDEKTn0PS-oL9EFyv1pI7dRgffQLD1qf5D6sprmYfWWokSsrWig8u2y0HChSygR6Jn5KXBqQn6FpM0dDJLnWQDRXHLl3Ey1iPYgDSmOIsIGrV9ZyNCQwk03wAgWbfdBTig3QSDYD-sTNOs3pc4UD_PqAvU2nz_1SS2ZiOwOn5F6gulE1L0iE3KEUEvOIagfHNVhz0oxa_VRZILkzV-zr6R_TW1m97h4H8jXl_VJyQGyhMGGypuDrQ9_vaY_RLEulLCyY0INglHWQ7pckxBtI5q55-Vio2wgewe2_qYcGsnBGaDNbySAsvYcWRrqDiFyzrJYivodqTQ",
              "e": "AQAB"
            },
            {
              "kid": "token-test-wrong-key",
              "kty": "RSA",
              "n": "o5cn3ljSRi6FaDEKTn0PS-oL9EFyv1pI7dRgffQLD1qf5D6sprmYfWWokSsrWig8u2y0HChSygR6Jn5KXBqQn6FpM0dDJLnWQDRXHLl3Ey1iPYgDSmOIsIGrV9ZyNCQwk03wAgWbfdBTig3QSDYD-sTNOs3pc4UD_PqAvU2nz_1SS2ZiOwOn5F6gulE1L0iE3KEUEvOIagfHNVhz0oxa_VRZILkzV-zr6R_TW1m97h4H8jXl_VJyQGyhMGGypuDrQ9_vaY_RLEulLCyY0INglHWQ7pckxBtI5q55-Vio2wgewe2_qYcGsnBGaDNbySAsvYcWRrqDiFyzrJYivodqTQ",
              "e": "AQAB"
            }
          ]
        }
    """,  # noqa: E501
}

### Course Live
COURSE_LIVE_GLOBAL_CREDENTIALS["BIG_BLUE_BUTTON"] = big_blue_button_credentials

### Override default production settings for testing purposes

API_ACCESS_FROM_EMAIL = "api-requests@example.com"
BRANCH_IO_KEY = ""
CC_MERCHANT_NAME = "Your Platform Name Here"
CERT_QUEUE = "certificates"
CMS_BASE = "localhost:18010"
COMMENTS_SERVICE_KEY = "password"
del BROKER_HEARTBEAT
del BROKER_HEARTBEAT_CHECKRATE
del BROKER_USE_SSL
del DEFAULT_ENTERPRISE_API_URL
del DEFAULT_ENTERPRISE_CONSENT_API_URL
del EMAIL_FILE_PATH
del ENABLE_REQUIRE_THIRD_PARTY_AUTH
del ENTITLEMENTS_EXPIRATION_ROUTING_KEY
del PYTHON_LIB_FILENAME
del REGISTRATION_CODE_LENGTH
del SESSION_INACTIVITY_TIMEOUT_IN_SECONDS
del SSL_AUTH_DN_FORMAT_STRING
del SSL_AUTH_EMAIL_DOMAIN
EDX_API_KEY = "PUT_YOUR_API_KEY_HERE"
ENTERPRISE_PUBLIC_ENROLLMENT_API_URL = "https://localhost:18000/api/enrollment/v1/"
GOOGLE_ANALYTICS_LINKEDIN = "GOOGLE_ANALYTICS_LINKEDIN_DUMMY"
GOOGLE_SITE_VERIFICATION_ID = ""
ID_VERIFICATION_SUPPORT_LINK = ""
MAINTENANCE_BANNER_TEXT = "Sample banner message"
ZENDESK_API_KEY = ""
ZENDESK_USER = ""
