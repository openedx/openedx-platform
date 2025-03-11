"""
This settings file is optimized for local development.  It should work equally well for bare-metal development and for
running inside of development environments such as tutor.

This file is currently in development itself and so may not work for everyone out of the box.  More updates, including
updated documentation will be added as we get closer to removing devstack.py
"""

#Helpers for loading plugins and their settings.
from edx_django_utils.plugins import add_plugins

from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType
from openedx.core.lib.derived import derive_settings

# Use the common file as the starting point.
# pylint: disable=wildcard-import
from .common import *  # noqa: F403

DEBUG = True

STORAGES['default']['BACKEND'] = 'django.core.files.storage.FileSystemStorage'  # noqa: F405
STORAGES['staticfiles']['BACKEND'] = 'openedx.core.storage.DevelopmentStorage'  # noqa: F405

# Disable pipeline compression in development
PIPELINE['PIPELINE_ENABLED'] = False  # noqa: F405

# Revert to the default set of finders as we don't want the production pipeline
STATICFILES_FINDERS = [
    'openedx.core.djangoapps.theming.finders.ThemeFilesFinder',
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'pipeline.finders.PipelineFinder',
]

# Point STATIC_ROOT at test_root/staticfiles/studio so that WEBPACK_LOADER's STATS_FILE resolves
# here. STATS_FILE is derived from STATIC_ROOT (see openedx/envs/common.py), and this is the
# directory that `npm run build-dev` / `npm run watch` write the Studio webpack-stats.json into by
# default (see webpack.common.config.js, whose staticRootCms falls back to
# ./test_root/staticfiles/studio when STATIC_ROOT_CMS is unset). The base default of
# ENV_ROOT/staticfiles/studio points *outside* the repo and does not match where webpack writes,
# so the loader can't find the stats file.
#
# NOTE: This is purely so the webpack stats manifest can be located. You are NOT expected to run
# collectstatic in development -- with DEBUG=True the staticfiles finders serve assets directly
# from their source dirs (e.g. the bundles in common/static/bundles). The '/studio' suffix mirrors
# the production convention (cms/envs/production.py).
STATIC_ROOT = REPO_ROOT / 'test_root' / 'staticfiles' / 'studio'  # noqa: F405

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = DEBUG

# Run Celery tasks synchronously in-process so local development needs no message broker or worker.
# The base default (CELERY_ALWAYS_EAGER = False, openedx/envs/common.py) makes task-enqueuing code
# paths -- e.g. the event fired on xblock creation -- try to reach a broker and 500 with
# "Connection refused". This matches the old devstack behavior.
CELERY_ALWAYS_EAGER = True

LMS_BASE = 'local.openedx.io:8000'
LMS_ROOT_URL = f'http://{LMS_BASE}'

CMS_BASE = 'studio.local.openedx.io:8001'
CMS_ROOT_URL = f'http://{CMS_BASE}'
ALLOWED_HOSTS = ['studio.local.openedx.io']

# Dealing with CORS
CORS_ALLOW_CREDENTIALS = True
# Each development MFE is served under apps.local.openedx.io on its own port. In practice the CMS
# only needs to accept cross-origin requests from the authoring MFE (Studio's frontend); the other
# MFEs talk to the LMS, not Studio. The rest are listed but commented out -- uncomment an origin if
# that MFE turns out to need to call Studio APIs directly.
CORS_ORIGIN_WHITELIST = (
    "http://apps.local.openedx.io:2001",  # authoring (Studio)
    # "http://apps.local.openedx.io:1984",  # communications
    # "http://apps.local.openedx.io:1993",  # ora-grading
    # "http://apps.local.openedx.io:1994",  # gradebook
    # "http://apps.local.openedx.io:1995",  # profile
    # "http://apps.local.openedx.io:1996",  # learner-dashboard
    # "http://apps.local.openedx.io:1997",  # account
    # "http://apps.local.openedx.io:1998",  # catalog
    # "http://apps.local.openedx.io:1999",  # authn
    # "http://apps.local.openedx.io:2000",  # learning
    # "http://apps.local.openedx.io:2002",  # discussions
    # "http://apps.local.openedx.io:2025",  # admin-console
)

# Unsafe (POST/PUT/DELETE) requests from the authoring MFE undergo Django's CSRF origin check, so
# the MFE origin must be trusted here or Studio rejects writes with a 403 ("Origin checking
# failed"). Scoped to the authoring MFE for the same reason as CORS_ORIGIN_WHITELIST above;
# uncomment another origin if that MFE needs to make write requests to Studio.
CSRF_TRUSTED_ORIGINS = [
    "http://apps.local.openedx.io:2001",  # authoring (Studio)
    # "http://apps.local.openedx.io:1984",  # communications
    # "http://apps.local.openedx.io:1993",  # ora-grading
    # "http://apps.local.openedx.io:1994",  # gradebook
    # "http://apps.local.openedx.io:1995",  # profile
    # "http://apps.local.openedx.io:1996",  # learner-dashboard
    # "http://apps.local.openedx.io:1997",  # account
    # "http://apps.local.openedx.io:1998",  # catalog
    # "http://apps.local.openedx.io:1999",  # authn
    # "http://apps.local.openedx.io:2000",  # learning
    # "http://apps.local.openedx.io:2002",  # discussions
    # "http://apps.local.openedx.io:2025",  # admin-console
]

# Cookie Related Settings
SESSION_COOKIE_DOMAIN = '.local.openedx.io'

# MFE Development URLs
# This one needs a trailing slash to load correctly right now.
LEARNER_HOME_MICROFRONTEND_URL = 'http://apps.local.openedx.io:1996/learner-dashboard/'
# This one explicitly needs to not have a trailing slash because of how it's used to make other
# urls.
LEARNING_MICROFRONTEND_URL = "http://apps.local.openedx.io:2000/learning"

# The course-authoring MFE (frontend-app-authoring) now serves Studio's course outline, pages &
# resources, etc. The base default is None (openedx/envs/common.py), so Studio's course_index view
# builds a redirect to None and 500s (`get_course_outline_url` -> `redirect(None)`). Point it at
# the authoring MFE, which runs on port 2001 under /authoring.
COURSE_AUTHORING_MICROFRONTEND_URL = "http://apps.local.openedx.io:2001/authoring"
CATALOG_MICROFRONTEND_URL = "http://apps.local.openedx.io:1998/catalog"

#######################################################################################################################
#### DERIVE ANY DERIVED SETTINGS
####

derive_settings(__name__)
add_plugins(__name__, ProjectType.LMS, SettingsType.DEVSTACK)
