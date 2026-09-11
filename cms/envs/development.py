"""
This settings file is optimized for local development.  It should work equally well for bare-metal development and for
running inside of development environments such as tutor.

This file is currently in development itself and so may not work for everyone out of the box.  More updates, including
updated documentation will be added as we get closer to removing devstack.py
"""

import hashlib
import hmac

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

#################### Studio Search (Meilisearch) ####################
# Enable Studio/library content search (the content.search app behind /api/content_search),
# pointing at a local Meilisearch on :7700. MEILISEARCH_URL is used by the Python backend;
# MEILISEARCH_PUBLIC_URL is the URL the browser uses to query Meilisearch directly.
MEILISEARCH_ENABLED = True
MEILISEARCH_URL = "http://localhost:7700"
MEILISEARCH_PUBLIC_URL = "http://localhost:7700"

# Namespace Meilisearch indexes so a dev instance doesn't collide with other indexes on a shared
# Meilisearch. "openedx_" is a sensible default; the base default in cms/envs/common.py is "".
# TODO: long term this default should move up to cms/envs/common.py, but changing it there is a
# breaking change for existing deployments (their unprefixed indexes would need a reindex), so we
# set it here for development only until that migration is handled separately.
MEILISEARCH_INDEX_PREFIX = "openedx_"

# Meilisearch derives every API key's value as HMAC-SHA256(master_key, key_uid): you choose the
# UID, and Meilisearch determines the key value. By fixing both the master key and the UID as shared
# dev constants, the derived key is the same for everyone, so we compute it here -- rather than each
# developer having to fetch a randomly-generated key from their own Meilisearch and paste a
# per-person value into this shared file. MEILISEARCH_MASTER_KEY must match the MEILI_MASTER_KEY of
# whatever Meilisearch instance you run locally.
#
# Meilisearch cannot declare a key at boot, so a key with this UID must also be created once in your
# Meilisearch (this is idempotent -- Meilisearch returns an error if the UID already exists, which
# you can ignore):
#
#   curl -X POST "http://localhost:7700/keys" \
#     -H "Authorization: Bearer openedx-insecure-meilisearch-master-key" \
#     -H "Content-Type: application/json" \
#     --data-binary '{"uid": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "name": "Open edX backend",
#                     "actions": ["*"], "indexes": ["openedx_*"], "expiresAt": null}'
#
# These are insecure, dev-only values.
MEILISEARCH_MASTER_KEY = "openedx-insecure-meilisearch-master-key"
MEILISEARCH_API_KEY_UID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
MEILISEARCH_API_KEY = hmac.new(
    MEILISEARCH_MASTER_KEY.encode(), MEILISEARCH_API_KEY_UID.encode(), hashlib.sha256
).hexdigest()

#######################################################################################################################
#### DERIVE ANY DERIVED SETTINGS
####

derive_settings(__name__)
add_plugins(__name__, ProjectType.CMS, SettingsType.DEVSTACK)
