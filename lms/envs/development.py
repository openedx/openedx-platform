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

# Point STATIC_ROOT at test_root/staticfiles so that WEBPACK_LOADER's STATS_FILE resolves here.
# STATS_FILE is derived from STATIC_ROOT (see openedx/envs/common.py), and this is the directory
# that `npm run build-dev` / `npm run watch` write webpack-stats.json into by default (see
# webpack.common.config.js, which falls back to ./test_root/staticfiles when STATIC_ROOT_LMS is
# unset). The base default of ENV_ROOT/staticfiles points *outside* the repo and does not match
# where webpack writes, so the loader can't find the stats file.
#
# NOTE: This is purely so the webpack stats manifest can be located. You are NOT expected to run
# collectstatic in development -- with DEBUG=True the staticfiles finders serve assets directly
# from their source dirs (e.g. the bundles in common/static/bundles). This mirrors what the test
# settings already do (openedx/envs/test.py).
STATIC_ROOT = REPO_ROOT / 'test_root' / 'staticfiles'  # noqa: F405

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = DEBUG

# Run Celery tasks synchronously in-process so local development needs no message broker or worker.
# The base default (CELERY_ALWAYS_EAGER = False, openedx/envs/common.py) makes task-enqueuing code
# paths try to reach a broker and 500 with "Connection refused". This matches the old devstack
# behavior.
CELERY_ALWAYS_EAGER = True

LMS_BASE = 'local.openedx.io:8000'
LMS_ROOT_URL = f'http://{LMS_BASE}'
ALLOWED_HOSTS = ['local.openedx.io']

# Add JWTs so we can get reliable session keys
# TODO: It would be nice to link to how we generate these secrets.
JWT_AUTH.update({  # noqa: F405
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

# Dealing with CORS
CORS_ALLOW_CREDENTIALS = True
# Each development MFE is served under apps.local.openedx.io on its own port. Every MFE fetches its
# config from the LMS MFE Config API, so each origin must be allowed here for that cross-origin
# request to succeed.
CORS_ORIGIN_WHITELIST = (
    "http://apps.local.openedx.io:1984",  # communications
    "http://apps.local.openedx.io:1993",  # ora-grading
    "http://apps.local.openedx.io:1994",  # gradebook
    "http://apps.local.openedx.io:1995",  # profile
    "http://apps.local.openedx.io:1996",  # learner-dashboard
    "http://apps.local.openedx.io:1997",  # account
    "http://apps.local.openedx.io:1998",  # catalog
    "http://apps.local.openedx.io:1999",  # authn
    "http://apps.local.openedx.io:2000",  # learning
    "http://apps.local.openedx.io:2001",  # authoring (Studio)
    "http://apps.local.openedx.io:2002",  # discussions
    "http://apps.local.openedx.io:2025",  # admin-console
)

# Post-login/logout redirects back to an MFE are only honored when the target host is whitelisted
# here (entries are host:port, no scheme). Mirrors the MFE origins allowed for CORS above.
LOGIN_REDIRECT_WHITELIST = [
    "apps.local.openedx.io:1984",  # communications
    "apps.local.openedx.io:1993",  # ora-grading
    "apps.local.openedx.io:1994",  # gradebook
    "apps.local.openedx.io:1995",  # profile
    "apps.local.openedx.io:1996",  # learner-dashboard
    "apps.local.openedx.io:1997",  # account
    "apps.local.openedx.io:1998",  # catalog
    "apps.local.openedx.io:1999",  # authn
    "apps.local.openedx.io:2000",  # learning
    "apps.local.openedx.io:2001",  # authoring (Studio)
    "apps.local.openedx.io:2002",  # discussions
    "apps.local.openedx.io:2025",  # admin-console
]

# Cookie Related Settings
SESSION_COOKIE_DOMAIN = '.local.openedx.io'

# MFE Development URLs
# One URL per development MFE, ordered by port, each served under apps.local.openedx.io.
COMMUNICATIONS_MICROFRONTEND_URL = "http://apps.local.openedx.io:1984/communications"
ORA_GRADING_MICROFRONTEND_URL = "http://apps.local.openedx.io:1993/ora-grading"
WRITABLE_GRADEBOOK_URL = "http://apps.local.openedx.io:1994/gradebook"
PROFILE_MICROFRONTEND_URL = "http://apps.local.openedx.io:1995/profile/u/"
# This one needs a trailing slash to load correctly right now.
LEARNER_HOME_MICROFRONTEND_URL = "http://apps.local.openedx.io:1996/learner-dashboard/"
ACCOUNT_MICROFRONTEND_URL = "http://apps.local.openedx.io:1997/account/"
CATALOG_MICROFRONTEND_URL = "http://apps.local.openedx.io:1998/catalog"
AUTHN_MICROFRONTEND_URL = "http://apps.local.openedx.io:1999/authn"
# Host (no scheme) used to build password-reset / account-recovery email links when the authn MFE
# is enabled (ENABLE_AUTHN_MICROFRONTEND). Paired with AUTHN_MICROFRONTEND_URL above.
AUTHN_MICROFRONTEND_DOMAIN = "apps.local.openedx.io:1999/authn"
# This one explicitly needs to not have a trailing slash because of how it's used to make other
# urls.
LEARNING_MICROFRONTEND_URL = "http://apps.local.openedx.io:2000/learning"
COURSE_AUTHORING_MICROFRONTEND_URL = "http://apps.local.openedx.io:2001/authoring"
DISCUSSIONS_MICROFRONTEND_URL = "http://apps.local.openedx.io:2002/discussions"
ADMIN_CONSOLE_MICROFRONTEND_URL = "http://apps.local.openedx.io:2025/admin-console"

# Temporarily enable the MFE Config API so the dev MFEs can fetch their runtime config from the
# LMS. It is off by default in common.py (ENABLE_MFE_CONFIG_API), which makes /api/mfe_config/v1
# return a 404. The flag itself is being deprecated -- see
# https://github.com/openedx/openedx-platform/issues/38959. Remove this override once that DEPR
# lands, or fold its removal into the DEPR if this development.py work merges first.
ENABLE_MFE_CONFIG_API = True

# Shared backend URLs served to all MFEs via the MFE Config API (GET /api/mfe_config/v1). An MFE
# that sets MFE_CONFIG_API_URL fetches this at startup and merges it *over* its build-time .env
# defaults (which point at localhost), so we don't have to hand-edit each MFE's env to use the
# local.openedx.io DNS. Keys use the SCREAMING_SNAKE names that @edx/frontend-platform reads
# straight into getConfig(). Values common to every MFE go here; per-MFE values go in
# MFE_CONFIG_OVERRIDES keyed by MFE name (e.g. 'learning').
MFE_CONFIG = {
    "LMS_BASE_URL": LMS_ROOT_URL,  # noqa: F405
    "LOGIN_URL": f"{LMS_ROOT_URL}/login",  # noqa: F405
    "LOGOUT_URL": f"{LMS_ROOT_URL}/logout",  # noqa: F405
    "REFRESH_ACCESS_TOKEN_ENDPOINT": f"{LMS_ROOT_URL}/login_refresh",  # noqa: F405
    # Studio runs on its own subdomain and port (8001) in this setup. CMS_BASE in the LMS common
    # settings still points at the production default, so set the dev Studio URL explicitly here.
    "STUDIO_BASE_URL": "http://studio.local.openedx.io:8001",
}

#######################################################################################################################
#### DERIVE ANY DERIVED SETTINGS
####

derive_settings(__name__)
add_plugins(__name__, ProjectType.LMS, SettingsType.DEVSTACK)
