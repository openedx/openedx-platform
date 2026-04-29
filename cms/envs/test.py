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



import os  # noqa: I001 - suppresses linting for this whole block, sort imports manually as needed
import tempfile

from django.utils.translation import gettext_lazy
from edx_django_utils.plugins import add_plugins

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType
from openedx.core.lib.derived import derive_settings
from openedx.core.lib.features_setting_proxy import FeaturesProxy
from xmodule.modulestore.modulestore_settings import update_module_store_settings  # pylint: disable=wrong-import-order

from .common import *  # noqa: F403
from openedx.envs.test import *  # must come after .common to override Derived values with literals  # noqa: F403

# A proxy for feature flags stored in the settings namespace
FEATURES = FeaturesProxy(globals())

# Include a non-ascii character in STUDIO_NAME and STUDIO_SHORT_NAME to uncover possible
# UnicodeEncodeErrors in tests. Also use lazy text to reveal possible json dumps errors
STUDIO_NAME = gettext_lazy("Your Platform 𝓢𝓽𝓾𝓭𝓲𝓸")
STUDIO_SHORT_NAME = gettext_lazy("𝓢𝓽𝓾𝓭𝓲𝓸")

COMMON_TEST_DATA_ROOT = COMMON_ROOT / "test" / "data"  # noqa: F405

COMPREHENSIVE_THEME_DIRS = [REPO_ROOT / "themes", REPO_ROOT / "common/test"]  # noqa: F405

WEBPACK_LOADER['DEFAULT']['LOADER_CLASS'] = 'webpack_loader.loader.FakeWebpackLoader'  # noqa: F405

GITHUB_REPO_ROOT = TEST_ROOT / "data"  # noqa: F405

# For testing "push to lms"
ENABLE_EXPORT_GIT = True
GIT_REPO_EXPORT_DIR = TEST_ROOT / "export_course_repos"  # noqa: F405

# Avoid having to run collectstatic before the unit test suite
# If we don't add these settings, then Django templates that can't
# find pipelined assets will raise a ValueError.
# http://stackoverflow.com/questions/12816941/unit-testing-with-django-pipeline
STORAGES['staticfiles']['BACKEND'] = "pipeline.storage.NonPackagingPipelineStorage"  # noqa: F405
STATIC_URL = "/static/"

# Update module store settings per defaults for tests
update_module_store_settings(
    MODULESTORE,  # noqa: F405
    module_store_options={
        "default_class": "xmodule.hidden_block.HiddenBlock",
        "fs_root": TEST_ROOT / "data",  # noqa: F405
    },
    doc_store_settings={
        "db": f"test_xmodule_{THIS_UUID}",  # noqa: F405
        "host": MONGO_HOST,  # noqa: F405
        "port": MONGO_PORT_NUM,  # noqa: F405
        "collection": "test_modulestore",
    },
)

CONTENTSTORE = {
    "ENGINE": "xmodule.contentstore.mongo.MongoContentStore",
    "DOC_STORE_CONFIG": {
        "host": MONGO_HOST,  # noqa: F405
        "db": f"test_xcontent_{THIS_UUID}",  # noqa: F405
        "port": MONGO_PORT_NUM,  # noqa: F405
        "collection": "dont_trip",
    },
    # allow for additional options that can be keyed on a name, e.g. 'trashcan'
    "ADDITIONAL_OPTIONS": {"trashcan": {"bucket": "trash_fs"}},
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": TEST_ROOT / "db" / "cms.db",  # noqa: F405
        "ATOMIC_REQUESTS": True,
    },
}

LMS_BASE = "localhost:8000"
LMS_ROOT_URL = f"http://{LMS_BASE}"

CMS_BASE = "localhost:8001"
CMS_ROOT_URL = f"http://{CMS_BASE}"

COURSE_AUTHORING_MICROFRONTEND_URL = "http://course-authoring-mfe"

CACHES = {
    # This is the cache used for most things.
    # In staging/prod envs, the sessions also live here.
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "edx_loc_mem_cache",
        "KEY_FUNCTION": "common.djangoapps.util.memcache.safe_key",
    },
    # The general cache is what you get if you use our util.cache. It's used for
    # things like caching the course.xml file for different A/B test groups.
    # We set it to be a DummyCache to force reloading of course.xml in dev.
    # In staging environments, we would grab VERSION from data uploaded by the
    # push process.
    "general": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        "KEY_PREFIX": "general",
        "VERSION": 4,
        "KEY_FUNCTION": "common.djangoapps.util.memcache.safe_key",
    },
    "mongo_metadata_inheritance": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": os.path.join(tempfile.gettempdir(), "mongo_metadata_inheritance"),
        "TIMEOUT": 300,
        "KEY_FUNCTION": "common.djangoapps.util.memcache.safe_key",
    },
    "loc_cache": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "edx_location_mem_cache",
    },
    "course_structure_cache": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    },
}

################################# CELERY ######################################

# test_status_cancel in cms/cms_user_tasks/test.py is failing without this
# @override_setting for BROKER_URL is not working in testcase, so updating here
BROKER_URL = "memory://localhost/"

# No segment key
CMS_SEGMENT_KEY = None

# Enable certificates for the tests
CERTIFICATES_HTML_VIEW = True

# Enable content libraries code for the tests
ENABLE_CONTENT_LIBRARIES = True

# ENTRANCE EXAMS
ENTRANCE_EXAMS = True

# Courseware Search Index
ENABLE_COURSEWARE_INDEX = True
ENABLE_LIBRARY_INDEX = True

########################## AUTHOR PERMISSION #######################
ENABLE_CREATOR_GROUP = False

# teams feature
ENABLE_TEAMS = True

######### custom courses #########
INSTALLED_APPS += [  # noqa: F405
    "openedx.core.djangoapps.ccxcon.apps.CCXConnectorConfig",
    "common.djangoapps.third_party_auth.apps.ThirdPartyAuthConfig",
]

########################## VIDEO IMAGE STORAGE ############################
VIDEO_IMAGE_SETTINGS = dict(
    VIDEO_IMAGE_MAX_BYTES=2 * 1024 * 1024,  # 2 MB
    VIDEO_IMAGE_MIN_BYTES=2 * 1024,  # 2 KB
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,  # noqa: F405
    ),
    DIRECTORY_PREFIX="video-images/",
    BASE_URL=MEDIA_URL,  # noqa: F405
)
VIDEO_IMAGE_DEFAULT_FILENAME = "default_video_image.png"

############################## Authentication ##############################

# Most of the JWT_AUTH settings come from cms/envs/common.py (from openedx/envs/common.py),
# but here we update to use JWKS values from openedx/envs/test.py for testing.
JWT_AUTH.update(jwt_jwks_values)  # noqa: F405

####################### Plugin Settings ##########################

add_plugins(__name__, ProjectType.CMS, SettingsType.TEST)

########################## Derive Any Derived Settings  #######################

derive_settings(__name__)

################################ Templates #################################

# For the "preview" template engine, the CMS uses the same dirs as the LMS. Here we mutate
# the DIRS list to match the MAKO_TEMPLATE_DIRS_BASE list produced by lms.envs.test.
preview_template = next(d for d in TEMPLATES if d["NAME"] == "preview")  # noqa: F405
preview_template['DIRS'].extend([
    COMMON_ROOT / 'test' / 'templates',  # noqa: F405
    COMMON_ROOT / 'test' / 'test_sites',  # noqa: F405
    REPO_ROOT / 'openedx' / 'core' / 'djangolib' / 'tests' / 'templates',  # noqa: F405
])
for theme_dir in COMPREHENSIVE_THEME_DIRS:  # pylint: disable=not-an-iterable
    preview_template['DIRS'].insert(0, theme_dir)

############### Settings for Django Rate limit #####################

RATELIMIT_RATE = '2/m'

############## openedx_content config ##############
OPENEDX_LEARNING = {
    "MEDIA": {"BACKEND": "django.core.files.storage.InMemoryStorage", "OPTIONS": {"location": MEDIA_ROOT + "_private"}}  # noqa: F405
}


# This value has traditionally been imported from the LMS. Now we modify it to match to avoid dependency
# on the LMS settings. The default in cms/envs/common.py includes the `marketing_emails_opt_in` field which is not
# in the dict that was previously imported from the LMS for testing so we remove it here
REGISTRATION_EXTRA_FIELDS.pop("marketing_emails_opt_in", None)  # noqa: F405

# Course Live
COURSE_LIVE_GLOBAL_CREDENTIALS["BIG_BLUE_BUTTON"] = big_blue_button_credentials  # noqa: F405

# Proctoring
PROCTORING_SETTINGS = {}

#### Override default production settings for testing purposes

del AUTHORING_API_URL  # noqa: F821
del BROKER_HEARTBEAT  # noqa: F821
del BROKER_HEARTBEAT_CHECKRATE  # noqa: F821
del BROKER_USE_SSL  # noqa: F821
del EMAIL_FILE_PATH  # noqa: F821
del PARSE_KEYS  # noqa: F821
del SESSION_INACTIVITY_TIMEOUT_IN_SECONDS  # noqa: F821
ENTERPRISE_API_URL = "https://localhost:18000/enterprise/api/v1/"
ENTERPRISE_CONSENT_API_URL = "https://localhost:18000/consent/api/v1/"
INACTIVE_USER_URL = "http://localhost:18010"
POLICY_CHANGE_GRADES_ROUTING_KEY = "edx.lms.core.default"
SINGLE_LEARNER_COURSE_REGRADE_ROUTING_KEY = "edx.lms.core.default"
