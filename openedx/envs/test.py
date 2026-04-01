"""
Common test related Django settings for Open edX services.

Shared test values between the LMS and CMS should be declared here when possible
rather than duplicated or imported accross services.
"""
import os
from path import Path as path
from uuid import uuid4

from django.utils.translation import gettext_lazy

from openedx.core.lib.derived import Derived

from common.djangoapps.util.testing import patch_sessions, patch_testcase


# This patch disables the commit_on_success decorator during tests
# in TestCase subclasses.
patch_testcase()
patch_sessions()

# Allow all hosts during tests, we use a lot of different ones all over the codebase.
ALLOWED_HOSTS = ["*"]

SITE_NAME = "edx.org"

# mongo connection settings
MONGO_PORT_NUM = int(os.environ.get("EDXAPP_TEST_MONGO_PORT", "27017"))
MONGO_HOST = os.environ.get("EDXAPP_TEST_MONGO_HOST", "localhost")

THIS_UUID = uuid4().hex[:5]

DISABLE_SET_JWT_COOKIES_FOR_TESTS = True

# Most tests don't use the discussion service, so we turn it off to speed them up.
# Tests that do can enable this flag, but must use the UrlResetMixin class to force urls.py
# to reload.
ENABLE_DISCUSSION_SERVICE = False

ENABLE_SERVICE_STATUS = True

# Toggles embargo on for testing
EMBARGO = True

# Enable the milestones app in tests to be consistent with it being enabled in production
MILESTONES_APP = True

ENABLE_ENROLLMENT_TRACK_USER_PARTITION = True

# Need wiki for courseware views to work. TODO (vshnayder): shouldn't need it.
WIKI_ENABLED = True

# Directory settings
TEST_ROOT = path("test_root")
STATIC_ROOT = TEST_ROOT / "staticfiles"
DATA_DIR = TEST_ROOT / "data"


def make_staticfile_dirs(settings):
    """
    Derives the final list of static files directories based on the provided settings.

    Args:
        settings: A Django settings module object.

    Returns:
        list: A list of static files directories (path.Path objects)
    """
    staticfiles_dirs = [
        settings.COMMON_ROOT / "static",
        settings.PROJECT_ROOT / "static",
    ]
    staticfiles_dirs += [
        (course_dir, settings.COMMON_TEST_DATA_ROOT / course_dir)
        for course_dir in os.listdir(settings.COMMON_TEST_DATA_ROOT)
        if os.path.isdir(settings.COMMON_TEST_DATA_ROOT / course_dir)
    ]
    return staticfiles_dirs

STATICFILES_DIRS = Derived(make_staticfile_dirs)

# Platform names with unicode for testing
PLATFORM_NAME = gettext_lazy("édX")
PLATFORM_DESCRIPTION = gettext_lazy("Open édX Platform")

# Enable a parental consent age limit for testing
PARENTAL_CONSENT_AGE_LIMIT = 13

# Test theme
TEST_THEME = Derived(lambda settings: settings.COMMON_ROOT / "test" / "test-theme")
ENABLE_COMPREHENSIVE_THEMING = True

# Enable EdxNotes for tests
ENABLE_EDXNOTES = True

# Use MockSearchEngine as the search engine for test scenario
SEARCH_ENGINE = "search.tests.mock_search_engine.MockSearchEngine"

# Custom courses
CUSTOM_COURSES_EDX = True

# These ports are carefully chosen so that if the browser needs to
# access them, they will be available through the SauceLabs SSH tunnel
XQUEUE_PORT = 8040
YOUTUBE_PORT = 8031
LTI_PORT = 8765
VIDEO_SOURCE_PORT = 8777

# Fast password hashing for tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Dummy secret key for tests
SECRET_KEY = "85920908f28904ed733fe576320db18cabd7b6cd"

jwt_jwks_values = {
    'JWT_PUBLIC_SIGNING_JWK_SET': """
       {
            "keys":[
                {
                    "kid":"BTZ9HA6K",
                    "e":"AQAB",
                    "kty":"RSA",
                    "n":"o5cn3ljSRi6FaDEKTn0PS-oL9EFyv1pI7dRgffQLD1qf5D6sprmYfWWokSsrWig8u2y0HChSygR6Jn5KXBqQn6FpM0dDJLnWQDRXHLl3Ey1iPYgDSmOIsIGrV9ZyNCQwk03wAgWbfdBTig3QSDYD-sTNOs3pc4UD_PqAvU2nz_1SS2ZiOwOn5F6gulE1L0iE3KEUEvOIagfHNVhz0oxa_VRZILkzV-zr6R_TW1m97h4H8jXl_VJyQGyhMGGypuDrQ9_vaY_RLEulLCyY0INglHWQ7pckxBtI5q55-Vio2wgewe2_qYcGsnBGaDNbySAsvYcWRrqDiFyzrJYivodqTQ"
                }
            ]
        }
    """,
    'JWT_PRIVATE_SIGNING_JWK': """
        {
            "kid": "BTZ9HA6K",
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
    """,
}

# Celery settings
CELERY_ALWAYS_EAGER = True
CELERY_RESULT_BACKEND = "django-cache"
CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION = False

# Static files
MEDIA_ROOT = TEST_ROOT / "uploads"
MEDIA_URL = "/uploads/"

# Video CDN
VIDEO_CDN_URL = {"CN": "http://api.xuetangx.com/edx/video?s3_url="}

# Video transcripts storage
VIDEO_TRANSCRIPTS_SETTINGS = dict(
    VIDEO_TRANSCRIPTS_MAX_BYTES=3 * 1024 * 1024,  # 3 MB
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
        base_url=MEDIA_URL,
    ),
    DIRECTORY_PREFIX="video-transcripts/",
)

# Microfrontend URLs
ACCOUNT_MICROFRONTEND_URL = "http://account-mfe"
LEARNING_MICROFRONTEND_URL = "http://learning-mfe"
DISCUSSIONS_MICROFRONTEND_URL = "http://discussions-mfe"
ORDER_HISTORY_MICROFRONTEND_URL = "http://order-history-mfe/"
PROFILE_MICROFRONTEND_URL = "http://profile-mfe"
CATALOG_MICROFRONTEND_URL = "http://catalog-mfe"

# API URLs
ECOMMERCE_API_URL = "https://ecommerce.example.com/api/v2/"
LOGIN_ISSUE_SUPPORT_LINK = "https://support.example.com/login-issue-help.html"

# Rate Limits
LOGISTRATION_RATELIMIT_RATE = "5/5m"
LOGISTRATION_PER_EMAIL_RATELIMIT_RATE = "6/5m"
LOGISTRATION_API_RATELIMIT = "5/m"

REGISTRATION_VALIDATION_RATELIMIT = "5/minute"
REGISTRATION_RATELIMIT = "5/minute"
OPTIONAL_FIELD_API_RATELIMIT = "5/m"

RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT = "2/m"
RESET_PASSWORD_API_RATELIMIT = "2/m"

# These keys are used for all of our asynchronous downloadable files, including
# the ones that contain information other than grades.
GRADES_DOWNLOAD = {
    "STORAGE_TYPE": "localfs",
    "BUCKET": "edx-grades",
    "ROOT_PATH": "/tmp/edx-s3/grades",
}

# edx-rbac
SYSTEM_WIDE_ROLE_CLASSES = os.environ.get("SYSTEM_WIDE_ROLE_CLASSES", [])

# Used in edx-proctoring for ID generation in lieu of SECRET_KEY - dummy value
# (ref MST-637)
PROCTORING_USER_OBFUSCATION_KEY = "85920908f28904ed733fe576320db18cabd7b6cd"

# Network configuration
CLOSEST_CLIENT_IP_FROM_HEADERS = []

# For tests, both the CMS and LMS add these credentials to the COURSE_LIVE_GLOBAL_CREDENTIALS
# setting. We cannot update this setting here because we don't have access to it in this
# module. If we inherit variables from openedx/envs/common.py in the future, we could then
# set the value directly here.
big_blue_button_credentials = {
    "KEY": "***",
    "SECRET": "***",
    "URL": "***",
}

DEFAULT_MOBILE_AVAILABLE = True

# Override production settings for testing
AWS_QUERYSTRING_AUTH = False
AWS_S3_CUSTOM_DOMAIN = "SET-ME-PLEASE (ex. bucket-name.s3.amazonaws.com)"
AWS_STORAGE_BUCKET_NAME = "SET-ME-PLEASE (ex. bucket-name)"
CELERY_BROKER_HOSTNAME = "localhost"
CELERY_BROKER_PASSWORD = "celery"
CELERY_BROKER_TRANSPORT = "amqp"
CELERY_BROKER_USER = "celery"
CHAT_COMPLETION_API = "https://example.com/chat/completion"
CHAT_COMPLETION_API_KEY = "i am a key"
ENTERPRISE_ENROLLMENT_API_URL = "https://localhost:18000/api/enrollment/v1/"
LMS_INTERNAL_ROOT_URL = "https://localhost:18000"
OPENAPI_CACHE_TIMEOUT = 0
SECURE_PROXY_SSL_HEADER = None
SESSION_COOKIE_DOMAIN = ""
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SHARED_COOKIE_DOMAIN = ""
SOFTWARE_SECURE_VERIFICATION_ROUTING_KEY = "edx.lms.core.default"
STATIC_ROOT_BASE = "/edx/var/edxapp/staticfiles"
STATIC_URL_BASE = "/static/"
