"""
Django settings for use when generating API documentation.
Basically the LMS devstack settings plus a few items needed to successfully
import all the Studio code.
"""

from textwrap import dedent
import os

from openedx.core.lib.derived import derive_settings

from lms.envs.common import *  # lint-amnesty, pylint: disable=wildcard-import
from cms.envs.common import (  # lint-amnesty, pylint: disable=unused-import
    ADVANCED_PROBLEM_TYPES,
    COURSE_IMPORT_EXPORT_STORAGE,
    GIT_EXPORT_DEFAULT_IDENT,
    SCRAPE_YOUTUBE_THUMBNAILS_JOB_QUEUE,
    VIDEO_TRANSCRIPT_MIGRATIONS_JOB_QUEUE,
    UPDATE_SEARCH_INDEX_JOB_QUEUE,
    FRONTEND_REGISTER_URL,
    ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS,
)

# Turn on all the boolean feature flags, so that conditionally included
# API endpoints will be found.
for key, value in FEATURES.items():
    if value is False:
        FEATURES[key] = True

# Settings that will fail if we enable them, and we don't need them for docs anyway.
FEATURES["RUN_AS_ANALYTICS_SERVER_ENABLED"] = False
FEATURES["ENABLE_SOFTWARE_SECURE_FAKE"] = False
FEATURES["ENABLE_MKTG_SITE"] = False

INSTALLED_APPS.extend(
    [
        "cms.djangoapps.contentstore.apps.ContentstoreConfig",
        'cms.djangoapps.modulestore_migrator',
        "cms.djangoapps.course_creators",
        "cms.djangoapps.xblock_config.apps.XBlockConfig",
        "lms.djangoapps.lti_provider",
        "openedx.core.djangoapps.content.search",
        "openedx.core.djangoapps.content_staging",
    ]
)

# Swagger generation details
openapi_security_info_basic = (
    "Obtain with a `POST` request to `/user/v1/account/login_session/`.  "
    "If needed, copy the cookies from the response to your new call."
)
openapi_security_info_jwt = dedent(
    """
    Obtain by making a `POST` request to `/oauth2/v1/access_token`.

    You will need to be logged in and have a client ID and secret already created.

    Your request should have the headers

    ```
    'Content-Type': 'application/x-www-form-urlencoded'
    ```

    Your request should have the data payload

    ```
    'grant_type': 'client_credentials'
    'client_id': [your client ID]
    'client_secret':  [your client secret]
    'token_type': 'jwt'
    ```

    Your JWT will be returned in the response as `access_token`. Prefix with `JWT ` in your header.
    """
)
openapi_security_info_csrf = (
    "Obtain by making a `GET` request to `/csrf/api/v1/token`. The token will be in the response cookie `csrftoken`."
)
SWAGGER_SETTINGS["SECURITY_DEFINITIONS"] = {
    "Basic": {
        "type": "basic",
        "description": openapi_security_info_basic,
    },
    "jwt": {
        "type": "apiKey",
        "name": "Authorization",
        "in": "header",
        "description": openapi_security_info_jwt,
    },
    "csrf": {
        "type": "apiKey",
        "name": "X-CSRFToken",
        "in": "header",
        "description": openapi_security_info_csrf,
    },
}


COMMON_TEST_DATA_ROOT = ""

# Needed because other settings derive from it and expect it
# to be a string but it is not relevant for generating docs.
LMS_ROOT_URL = "https://example.com"

derive_settings(__name__)
