"""
MFE API Views for useful information related to mfes.
"""

from configparser import Error as ConfigParserError

import edx_api_doc_tools as apidocs
from django.conf import settings
from django.http import HttpResponseNotFound, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from help_tokens.core import HelpUrlExpert
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

# Translation map from legacy SCREAMING_SNAKE_CASE MFE_CONFIG keys to
# camelCase field names matching frontend-base's RequiredSiteConfig and
# OptionalSiteConfig interfaces.
# See https://github.com/openedx/frontend-base/blob/main/types.ts
SITE_CONFIG_TRANSLATION_MAP: dict[str, str] = {
    # RequiredSiteConfig
    "SITE_NAME": "siteName",
    "BASE_URL": "baseUrl",
    "LMS_BASE_URL": "lmsBaseUrl",
    "LOGIN_URL": "loginUrl",
    "LOGOUT_URL": "logoutUrl",
    # OptionalSiteConfig
    "STUDIO_BASE_URL": "cmsBaseUrl",
    "LOGO_URL": "headerLogoImageUrl",
    "ACCESS_TOKEN_COOKIE_NAME": "accessTokenCookieName",
    "LANGUAGE_PREFERENCE_COOKIE_NAME": "languagePreferenceCookieName",
    "USER_INFO_COOKIE_NAME": "userInfoCookieName",
    "CSRF_TOKEN_API_PATH": "csrfTokenApiPath",
    "REFRESH_ACCESS_TOKEN_API_PATH": "refreshAccessTokenApiPath",
    "SEGMENT_KEY": "segmentKey",
}


# Translation map from known MFE names to reverse-domain appIds.
MFE_NAME_TO_APP_ID: dict[str, str] = {
    "account": "org.openedx.frontend.app.account",
    "admin-console": "org.openedx.frontend.app.adminConsole",
    "authn": "org.openedx.frontend.app.authn",
    "authoring": "org.openedx.frontend.app.authoring",
    "catalog": "org.openedx.frontend.app.catalog",
    "communications": "org.openedx.frontend.app.communications",
    "course-authoring": "org.openedx.frontend.app.authoring",
    "discussions": "org.openedx.frontend.app.discussions",
    "gradebook": "org.openedx.frontend.app.gradebook",
    "instructor-dashboard": "org.openedx.frontend.app.instructorDashboard",
    "learner-dashboard": "org.openedx.frontend.app.learnerDashboard",
    "learner-record": "org.openedx.frontend.app.learnerRecord",
    "learning": "org.openedx.frontend.app.learning",
    "ora-grading": "org.openedx.frontend.app.oraGrading",
    "profile": "org.openedx.frontend.app.profile",
}


def get_legacy_config() -> dict:
    """
    Return legacy configuration values available in either site configuration or django settings.
    """
    return {
        "ENABLE_COURSE_SORTING_BY_START_DATE": configuration_helpers.get_value(
            "ENABLE_COURSE_SORTING_BY_START_DATE",
            settings.FEATURES["ENABLE_COURSE_SORTING_BY_START_DATE"],
        ),
        "HOMEPAGE_PROMO_VIDEO_YOUTUBE_ID": configuration_helpers.get_value(
            "homepage_promo_video_youtube_id", None
        ),
        "HOMEPAGE_COURSE_MAX": configuration_helpers.get_value(
            "HOMEPAGE_COURSE_MAX", settings.HOMEPAGE_COURSE_MAX
        ),
        "COURSE_ABOUT_TWITTER_ACCOUNT": configuration_helpers.get_value(
            "course_about_twitter_account", settings.PLATFORM_TWITTER_ACCOUNT
        ),
        "NON_BROWSABLE_COURSES": not settings.FEATURES.get("COURSES_ARE_BROWSABLE"),
        "ENABLE_COURSE_DISCOVERY": settings.FEATURES["ENABLE_COURSE_DISCOVERY"],
    }


def get_mfe_config() -> dict:
    """Return common MFE configuration from settings or site configuration.

    Returns:
        A dictionary of configuration values shared across all MFEs.
    """
    mfe_config = (
        configuration_helpers.get_value("MFE_CONFIG", settings.MFE_CONFIG) or {}
    )
    if not isinstance(mfe_config, dict):
        return {}
    return mfe_config


def resolve_help_token(token: str) -> str | None:
    """Resolve a help-tokens token to a URL, returning None if the token cannot be resolved."""
    try:
        return HelpUrlExpert.the_one().url_for_token(token)
    except (KeyError, ConfigParserError):
        return None


def get_legacy_config_overrides() -> dict:
    """Return per-app legacy configuration overrides.

    Same shape as get_explicit_mfe_config_overrides(): a dict keyed by MFE name,
    where each value is a dict of config values.

    This is a compatibility layer for per-app values that historically
    came from legacy systems (e.g., help-tokens).
    """
    overrides: dict[str, dict] = {}

    instructor_help_url = resolve_help_token("instructor")
    if instructor_help_url:
        overrides["instructor-dashboard"] = {"SUPPORT_URL": instructor_help_url}

    return overrides


def get_explicit_mfe_config_overrides() -> dict:
    """Return MFE-specific overrides from settings or site configuration.

    Returns:
        A dictionary keyed by MFE name, where each value is a dict of
        per-MFE overrides.  Non-dict entries are filtered out.
    """
    raw_overrides = (
        configuration_helpers.get_value(
            "MFE_CONFIG_OVERRIDES",
            settings.MFE_CONFIG_OVERRIDES,
        )
        or {}
    )
    if not isinstance(raw_overrides, dict):
        return {}

    return {
        mfe_name: overrides
        for mfe_name, overrides in raw_overrides.items()
        if isinstance(overrides, dict)
    }


def get_mfe_config_overrides() -> dict:
    """Return all MFE-specific overrides, merging legacy fallbacks with explicit settings.

    Legacy per-app fallbacks (e.g., from help-tokens) are included at the lowest
    precedence; explicit MFE_CONFIG_OVERRIDES from settings or site configuration
    take priority.

    Returns:
        A dictionary keyed by MFE name, where each value is a dict of
        per-MFE overrides.
    """
    legacy_overrides = get_legacy_config_overrides()
    explicit_overrides = get_explicit_mfe_config_overrides()
    all_mfe_names = set(legacy_overrides) | set(explicit_overrides)
    return {
        mfe_name: legacy_overrides.get(mfe_name, {}) | explicit_overrides.get(mfe_name, {})
        for mfe_name in all_mfe_names
    }


def get_frontend_site_config() -> dict:
    """Return frontend site configuration from settings or site configuration.

    Unlike MFE_CONFIG, this setting is already in frontend-base's expected
    camelCase format and requires no translation.
    """
    frontend_site_config = (
        configuration_helpers.get_value(
            "FRONTEND_SITE_CONFIG", settings.FRONTEND_SITE_CONFIG
        )
        or {}
    )
    if not isinstance(frontend_site_config, dict):
        return {}
    return frontend_site_config


class MFEConfigView(APIView):
    """
    Provides an API endpoint to get the MFE configuration from settings (or site configuration).
    """

    @method_decorator(cache_page(settings.MFE_CONFIG_API_CACHE_TIMEOUT))
    @apidocs.schema(
        parameters=[
            apidocs.query_parameter(
                "mfe",
                str,
                description="Name of an MFE (a.k.a. an APP_ID).",
            ),
        ],
    )
    def get(self, request):
        """
        Return the MFE configuration, optionally including MFE-specific overrides.

        This configuration currently also pulls specific settings from site configuration or
        django settings. This is a temporary change as a part of the migration of some legacy
        pages to MFEs. This is a temporary compatibility layer which will eventually be deprecated.

        See [DEPR ticket](https://github.com/openedx/edx-platform/issues/37210) for more details.

        The compatibility means that settings from the legacy locations will continue to work but
        the settings listed below in the `get_legacy_config` function should be added to the MFE
        config by operators.

        **Usage**

          Get common config:
          GET /api/mfe_config/v1

          Get app config (common + app-specific overrides):
          GET /api/mfe_config/v1?mfe=name_of_mfe

        **GET Response Values**
        ```
        {
            "BASE_URL": "https://name_of_mfe.example.com",
            "LANGUAGE_PREFERENCE_COOKIE_NAME": "example-language-preference",
            "CREDENTIALS_BASE_URL": "https://credentials.example.com",
            "DISCOVERY_API_BASE_URL": "https://discovery.example.com",
            "LMS_BASE_URL": "https://courses.example.com",
            "LOGIN_URL": "https://courses.example.com/login",
            "LOGOUT_URL": "https://courses.example.com/logout",
            "STUDIO_BASE_URL": "https://studio.example.com",
            "LOGO_URL": "https://courses.example.com/logo.png",
            "ENABLE_COURSE_SORTING_BY_START_DATE": True,
            "HOMEPAGE_COURSE_MAX": 10,
            ... and so on
        }
        ```
        """

        if not settings.ENABLE_MFE_CONFIG_API:
            return HttpResponseNotFound()

        mfe_name = (
            str(request.query_params.get("mfe"))
            if request.query_params.get("mfe")
            else None
        )

        merged_config = get_legacy_config() | get_mfe_config()

        if mfe_name:
            merged_config |= get_mfe_config_overrides().get(mfe_name, {})

        return JsonResponse(merged_config, status=status.HTTP_200_OK)


def mfe_name_to_app_id(mfe_name: str) -> str:
    """Convert a legacy MFE name to a frontend-base appId.

    Uses an explicit mapping of known MFE names to reverse-domain appIds.
    Falls back to a programmatic kebab-to-camelCase conversion for unknown names.
    """
    app_id = MFE_NAME_TO_APP_ID.get(mfe_name)
    if app_id:
        return app_id

    parts = mfe_name.split("-")
    camel_case = parts[0] + "".join(part.capitalize() for part in parts[1:])
    return f"org.openedx.frontend.app.{camel_case}"


def translate_legacy_mfe_config() -> dict:
    """Translate legacy MFE_CONFIG/MFE_CONFIG_OVERRIDES into frontend-base site config format.

    This entire function is a compatibility layer that can be removed once legacy
    MFE configuration (MFE_CONFIG, MFE_CONFIG_OVERRIDES, and the related
    get_legacy_config/get_mfe_config/get_mfe_config_overrides helpers) is fully
    deprecated.

    Returns a dict in the shape expected by frontend-base's SiteConfig.
    """
    mfe_config = get_mfe_config()
    mfe_config_overrides = get_mfe_config_overrides()

    # Split MFE_CONFIG into site-level (translated to camelCase) and app-level. Legacy
    # config seeds common_app_config at lowest precedence. Note: siteId has no legacy
    # Django equivalent, but at the same time it's not expected to be set at runtime; if
    # needed, operators can configure it via FRONTEND_SITE_CONFIG.
    site_config = {}
    common_app_config = get_legacy_config()
    for key, value in mfe_config.items():
        if key in SITE_CONFIG_TRANSLATION_MAP:
            site_config[SITE_CONFIG_TRANSLATION_MAP[key]] = value
        else:
            common_app_config[key] = value

    site_config["commonAppConfig"] = common_app_config

    # If LOGOUT_URL was translated, also expose it as an external route so
    # that frontend-base can redirect to the platform logout endpoint.
    if "logoutUrl" in site_config:
        site_config.setdefault("externalRoutes", []).append(
            {
                "role": "org.openedx.frontend.role.logout",
                "url": site_config["logoutUrl"],
            }
        )

    # Build the apps array from MFE_CONFIG_OVERRIDES. Site-level keys are stripped from
    # per-app overrides so they don't leak into app config.
    # Note: frontend-base ignores app-specific configuration for apps that are not
    # registered in site.config at build-time.
    apps = []
    for mfe_name in sorted(mfe_config_overrides):
        overrides = {
            k: v
            for k, v in mfe_config_overrides[mfe_name].items()
            if k not in SITE_CONFIG_TRANSLATION_MAP
        }
        apps.append(
            {
                "appId": mfe_name_to_app_id(mfe_name),
                "config": overrides,
            }
        )

    if apps:
        site_config["apps"] = apps

    return site_config


class FrontendSiteConfigView(APIView):
    """
    Provides the frontend site configuration endpoint.

    Returns the contents of ``FRONTEND_SITE_CONFIG`` merged on top of a
    compatibility translation of the legacy ``MFE_CONFIG`` /
    ``MFE_CONFIG_OVERRIDES`` settings.  Once legacy configuration is fully
    deprecated, the translation layer can be removed and this view will
    simply return ``FRONTEND_SITE_CONFIG`` as-is.

    See `frontend-base SiteConfig
    <https://github.com/openedx/frontend-base/blob/main/types.ts>`_.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @method_decorator(cache_page(settings.MFE_CONFIG_API_CACHE_TIMEOUT))
    def get(self, request):
        """
        Return frontend site configuration.

        **Usage**

          GET /api/frontend_site_config/v1/

        **GET Response Values**
        ```
        {
            "siteName": "My Open edX Site",
            "baseUrl": "https://apps.example.com",
            "lmsBaseUrl": "https://courses.example.com",
            "loginUrl": "https://courses.example.com/login",
            "logoutUrl": "https://courses.example.com/logout",
            ...
        }
        ```
        """
        if not settings.ENABLE_MFE_CONFIG_API:
            raise NotFound()

        # Legacy translation (removable once MFE_CONFIG is deprecated).
        site_config = translate_legacy_mfe_config()

        # FRONTEND_SITE_CONFIG takes highest precedence.  Deep-merge
        # nested keys so that the translated legacy values are extended
        # rather than clobbered.
        frontend_site_config = get_frontend_site_config()

        # Deep-merge commonAppConfig.
        if "commonAppConfig" in frontend_site_config and "commonAppConfig" in site_config:
            site_config["commonAppConfig"].update(frontend_site_config.pop("commonAppConfig"))

        # Merge apps by appId.
        if "apps" in frontend_site_config and "apps" in site_config:
            existing_apps = {app["appId"]: app for app in site_config["apps"]}
            for app in frontend_site_config.pop("apps"):
                app_id = app.get("appId")
                if app_id and app_id in existing_apps:
                    existing_apps[app_id]["config"].update(app.get("config", {}))
                else:
                    site_config["apps"].append(app)

        site_config.update(frontend_site_config)

        return Response(site_config)
