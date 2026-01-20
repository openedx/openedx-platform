"""
Test the use cases of the views of the mfe api.
"""

from unittest.mock import call, patch

import ddt
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from lms.djangoapps.mfe_config_api.views import mfe_name_to_app_id

# Default legacy configuration values, used in tests to build a correct expected response
default_legacy_config = {
    "COURSE_ABOUT_TWITTER_ACCOUNT": "@YourPlatformTwitterAccount",
    "NON_BROWSABLE_COURSES": False,
    "ENABLE_COURSE_SORTING_BY_START_DATE": True,
    "HOMEPAGE_COURSE_MAX": None,
    "HOMEPAGE_PROMO_VIDEO_YOUTUBE_ID": None,
    "ENABLE_COURSE_DISCOVERY": False,
}


@ddt.ddt
class MFEConfigTestCase(APITestCase):
    """
    Test the use case that exposes the site configuration with the mfe api.
    """

    def setUp(self):
        self.mfe_config_api_url = reverse("mfe_config_api:config")
        cache.clear()
        return super().setUp()

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_get_mfe_config(self, configuration_helpers_mock):
        """Test the get mfe config from site configuration with the mfe api.

        Expected result:
        - The get_value method of the configuration_helpers in the views is called once with the
        parameters ("MFE_CONFIG", settings.MFE_CONFIG)
        - The status of the response of the request is a HTTP_200_OK.
        - The json of the response of the request is equal to the mocked configuration.
        """
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {"EXAMPLE_VAR": "value"}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.mfe_config_api_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {**default_legacy_config, "EXAMPLE_VAR": "value"})

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_get_mfe_config_with_queryparam(self, configuration_helpers_mock):
        """Test the get mfe config with a query param from site configuration.

        Expected result:
        - The get_value method of the configuration_helpers in the views is called twice, once with the
        parameters ("MFE_CONFIG", settings.MFE_CONFIG)
        and once with the parameters ("MFE_CONFIG_OVERRIDES", settings.MFE_CONFIG_OVERRIDES).
        - The json of the response is the merge of both mocked configurations.
        """
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {"EXAMPLE_VAR": "value", "OTHER": "other"}
            if key == "MFE_CONFIG_OVERRIDES":
                return {"mymfe": {"EXAMPLE_VAR": "mymfe_value"}}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(f"{self.mfe_config_api_url}?mfe=mymfe")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        calls = [call("MFE_CONFIG", settings.MFE_CONFIG),
                 call("MFE_CONFIG_OVERRIDES", settings.MFE_CONFIG_OVERRIDES)]
        configuration_helpers_mock.get_value.assert_has_calls(calls)
        self.assertEqual(
            response.json(), {**default_legacy_config, "EXAMPLE_VAR": "mymfe_value", "OTHER": "other"}
        )

    @ddt.unpack
    @ddt.data(
        dict(
            mfe_config={},
            mfe_config_overrides={},
            expected_response={**default_legacy_config},
        ),
        dict(
            mfe_config={"EXAMPLE_VAR": "value"},
            mfe_config_overrides={},
            expected_response={**default_legacy_config, "EXAMPLE_VAR": "value"},
        ),
        dict(
            mfe_config={},
            mfe_config_overrides={"mymfe": {"EXAMPLE_VAR": "mymfe_value"}},
            expected_response={**default_legacy_config, "EXAMPLE_VAR": "mymfe_value"},
        ),
        dict(
            mfe_config={"EXAMPLE_VAR": "value"},
            mfe_config_overrides={"mymfe": {"EXAMPLE_VAR": "mymfe_value"}},
            expected_response={**default_legacy_config, "EXAMPLE_VAR": "mymfe_value"},
        ),
        dict(
            mfe_config={"EXAMPLE_VAR": "value", "OTHER": "other"},
            mfe_config_overrides={"mymfe": {"EXAMPLE_VAR": "mymfe_value"}},
            expected_response={**default_legacy_config, "EXAMPLE_VAR": "mymfe_value", "OTHER": "other"},
        ),
        dict(
            mfe_config={"EXAMPLE_VAR": "value"},
            mfe_config_overrides={"yourmfe": {"EXAMPLE_VAR": "yourmfe_value"}},
            expected_response={**default_legacy_config, "EXAMPLE_VAR": "value"},
        ),
        dict(
            mfe_config={"EXAMPLE_VAR": "value"},
            mfe_config_overrides={
                "yourmfe": {"EXAMPLE_VAR": "yourmfe_value"},
                "mymfe": {"EXAMPLE_VAR": "mymfe_value"},
            },
            expected_response={**default_legacy_config, "EXAMPLE_VAR": "mymfe_value"},
        ),
    )
    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_get_mfe_config_with_queryparam_multiple_configs(
        self,
        configuration_helpers_mock,
        mfe_config,
        mfe_config_overrides,
        expected_response,
    ):
        """Test the get mfe config with a query param and different settings in mfe_config and mfe_config_overrides with
        the site configuration to test that the merge of the configurations is done correctly and mymfe config take
        precedence.

        Expected result:
        - The get_value method of the configuration_helpers in the views is called twice, once with the
        parameters ("MFE_CONFIG", settings.MFE_CONFIG)
        and once with the parameters ("MFE_CONFIG_OVERRIDES", settings.MFE_CONFIG_OVERRIDES).
        - The json of the response is the expected_response passed by ddt.data.
        """
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return mfe_config
            if key == "MFE_CONFIG_OVERRIDES":
                return mfe_config_overrides
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(f"{self.mfe_config_api_url}?mfe=mymfe")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        calls = [call("MFE_CONFIG", settings.MFE_CONFIG),
                 call("MFE_CONFIG_OVERRIDES", settings.MFE_CONFIG_OVERRIDES)]
        configuration_helpers_mock.get_value.assert_has_calls(calls)
        self.assertEqual(response.json(), expected_response)

    def test_get_mfe_config_from_django_settings(self):
        """Test that when there is no site configuration, the API takes the django settings.

        Expected result:
        - The status of the response of the request is a HTTP_200_OK.
        - The json response is equal to MFE_CONFIG in lms/envs/test.py"""
        response = self.client.get(self.mfe_config_api_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), default_legacy_config | settings.MFE_CONFIG)

    def test_get_mfe_config_with_queryparam_from_django_settings(self):
        """Test that when there is no site configuration, the API with queryparam takes the django settings.

        Expected result:
        - The status of the response of the request is a HTTP_200_OK.
        - The json response is equal to MFE_CONFIG merged with MFE_CONFIG_OVERRIDES['mymfe']
        """
        response = self.client.get(f"{self.mfe_config_api_url}?mfe=mymfe")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected = default_legacy_config | settings.MFE_CONFIG | settings.MFE_CONFIG_OVERRIDES["mymfe"]
        self.assertEqual(response.json(), expected)

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    @override_settings(ENABLE_MFE_CONFIG_API=False)
    def test_404_get_mfe_config(self, configuration_helpers_mock):
        """Test the 404 not found response from get mfe config.

        Expected result:
        - The get_value method of configuration_helpers is not called.
        - The status of the response of the request is a HTTP_404_NOT_FOUND.
        """
        response = self.client.get(self.mfe_config_api_url)
        configuration_helpers_mock.get_value.assert_not_called()
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_get_mfe_config_for_catalog(self, configuration_helpers_mock):
        """Test the mfe config by explicitly using catalog mfe as an example.

        Expected result:
        - The configuration_helpers get_value is called for each catalog-specific configuration.
        - The catalog-specific values are included in the response.
        """
        mfe_config = {"BASE_URL": "https://catalog.example.com", "COURSE_ABOUT_TWITTER_ACCOUNT": "@TestAccount"}
        mfe_config_overrides = {
            "catalog": {
                "SOME_SETTING": "catalog_value",
                "NON_BROWSABLE_COURSES": True,
            }
        }

        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return mfe_config
            if key == "MFE_CONFIG_OVERRIDES":
                return mfe_config_overrides
            if key == "ENABLE_COURSE_SORTING_BY_START_DATE":
                return True
            if key == "homepage_promo_video_youtube_id":
                return None
            if key == "HOMEPAGE_COURSE_MAX":
                return 8
            return default

        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(f"{self.mfe_config_api_url}?mfe=catalog")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.json()
        self.assertEqual(data["BASE_URL"], "https://catalog.example.com")
        self.assertEqual(data["SOME_SETTING"], "catalog_value")
        self.assertEqual(data["ENABLE_COURSE_SORTING_BY_START_DATE"], True)
        self.assertEqual(data["HOMEPAGE_PROMO_VIDEO_YOUTUBE_ID"], None)
        self.assertEqual(data["HOMEPAGE_COURSE_MAX"], 8)
        self.assertEqual(data["COURSE_ABOUT_TWITTER_ACCOUNT"], "@TestAccount")
        self.assertEqual(data["NON_BROWSABLE_COURSES"], True)
        self.assertEqual(data["ENABLE_COURSE_DISCOVERY"], False)

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_config_order_of_precedence(self, configuration_helpers_mock):
        """Test the precedence of configuration values by explicitly using catalog MFE as an example.

        Expected result:
        - Values should be taken in this order (highest to lowest precedence):
            1. MFE_CONFIG_OVERRIDES from site conf
            2. MFE_CONFIG_OVERRIDES from settings
            3. MFE_CONFIG from site conf
            4. MFE_CONFIG from settings
            5. Plain site configuration
            6. Plain settings
        """
        mfe_config = {
            "HOMEPAGE_COURSE_MAX": 10,
            "ENABLE_COURSE_SORTING_BY_START_DATE": False,
            "PRESERVED_SETTING": "preserved"
        }
        mfe_config_overrides = {
            "catalog": {
                "HOMEPAGE_COURSE_MAX": 15,
            }
        }

        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return mfe_config
            if key == "MFE_CONFIG_OVERRIDES":
                return mfe_config_overrides
            if key == "HOMEPAGE_COURSE_MAX":
                return 5  # Plain site configuration
            if key == "homepage_promo_video_youtube_id":
                return "site-conf-youtube-id"
            return default

        configuration_helpers_mock.get_value.side_effect = side_effect

        with override_settings(
            HOMEPAGE_COURSE_MAX=3,  # Plain settings (lowest precedence)
            FEATURES={              # Settings FEATURES
                "ENABLE_COURSE_SORTING_BY_START_DATE": True,
                "ENABLE_COURSE_DISCOVERY": True,
            }
        ):
            response = self.client.get(f"{self.mfe_config_api_url}?mfe=catalog")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # MFE_CONFIG_OVERRIDES from site conf (highest precedence)
        self.assertEqual(data["HOMEPAGE_COURSE_MAX"], 15)

        # MFE_CONFIG from site conf takes precedence over plain site configuration and settings
        self.assertEqual(data["ENABLE_COURSE_SORTING_BY_START_DATE"], False)

        # Plain site configuration takes precedence over plain settings
        self.assertEqual(data["HOMEPAGE_PROMO_VIDEO_YOUTUBE_ID"], "site-conf-youtube-id")

        # Value in original MFE_CONFIG not overridden by catalog config should be preserved
        self.assertEqual(data["PRESERVED_SETTING"], "preserved")


class MfeNameToAppIdTests(SimpleTestCase):
    """Tests for the mfe_name_to_app_id helper."""

    def test_simple_name(self):
        self.assertEqual(mfe_name_to_app_id("authn"), "org.openedx.frontend.app.authn")

    def test_kebab_case_name(self):
        self.assertEqual(
            mfe_name_to_app_id("learner-dashboard"),
            "org.openedx.frontend.app.learnerDashboard",
        )

    def test_mapped_alias(self):
        """course-authoring is an alias for authoring in the explicit map."""
        self.assertEqual(
            mfe_name_to_app_id("course-authoring"),
            "org.openedx.frontend.app.authoring",
        )

    def test_fallback_for_unknown_name(self):
        """Unknown names fall back to programmatic kebab-to-camelCase conversion."""
        self.assertEqual(
            mfe_name_to_app_id("admin-portal-enterprise"),
            "org.openedx.frontend.app.adminPortalEnterprise",
        )


class FrontendSiteConfigTestCase(APITestCase):
    """Tests for the FrontendSiteConfigView endpoint."""

    def setUp(self):
        self.url = reverse("frontend_site_config:frontend_site_config")
        cache.clear()
        return super().setUp()

    @override_settings(ENABLE_MFE_CONFIG_API=False)
    def test_404_when_disabled(self):
        """API returns 404 when ENABLE_MFE_CONFIG_API is False."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_site_level_keys_translated(self, configuration_helpers_mock):
        """Keys that map to RequiredSiteConfig/OptionalSiteConfig appear at the top level in camelCase."""
        mfe_config = {
            "SITE_NAME": "Test Site",
            "BASE_URL": "https://apps.example.com",
            "LMS_BASE_URL": "https://courses.example.com",
            "LOGIN_URL": "https://courses.example.com/login",
            "LOGOUT_URL": "https://courses.example.com/logout",
            "LOGO_URL": "https://courses.example.com/logo.png",
            "ACCESS_TOKEN_COOKIE_NAME": "edx-jwt",
            "LANGUAGE_PREFERENCE_COOKIE_NAME": "lang-pref",
            "USER_INFO_COOKIE_NAME": "edx-user-info",
            "CSRF_TOKEN_API_PATH": "/csrf/api/v1/token",
            "REFRESH_ACCESS_TOKEN_API_PATH": "/login_refresh",
            "SEGMENT_KEY": "abc123",
        }

        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return mfe_config
            if key == "MFE_CONFIG_OVERRIDES":
                return {}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # RequiredSiteConfig
        self.assertEqual(data["siteName"], "Test Site")
        self.assertEqual(data["baseUrl"], "https://apps.example.com")
        self.assertEqual(data["lmsBaseUrl"], "https://courses.example.com")
        self.assertEqual(data["loginUrl"], "https://courses.example.com/login")
        self.assertEqual(data["logoutUrl"], "https://courses.example.com/logout")
        # OptionalSiteConfig
        self.assertEqual(data["headerLogoImageUrl"], "https://courses.example.com/logo.png")
        self.assertEqual(data["accessTokenCookieName"], "edx-jwt")
        self.assertEqual(data["languagePreferenceCookieName"], "lang-pref")
        self.assertEqual(data["userInfoCookieName"], "edx-user-info")
        self.assertEqual(data["csrfTokenApiPath"], "/csrf/api/v1/token")
        self.assertEqual(data["refreshAccessTokenApiPath"], "/login_refresh")
        self.assertEqual(data["segmentKey"], "abc123")
        # LOGOUT_URL also generates an externalRoute for frontend-base
        self.assertEqual(
            data["externalRoutes"],
            [{"role": "org.openedx.frontend.role.logout", "url": "https://courses.example.com/logout"}],
        )

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_unmapped_keys_in_app_config(self, configuration_helpers_mock):
        """Keys that don't map to SiteConfig fields are included in each app's config."""
        mfe_config = {
            "LMS_BASE_URL": "https://courses.example.com",
            "CREDENTIALS_BASE_URL": "https://credentials.example.com",
            "STUDIO_BASE_URL": "https://studio.example.com",
        }

        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return mfe_config
            if key == "MFE_CONFIG_OVERRIDES":
                return {"authn": {"SOME_KEY": "value"}}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        # Site-level key translated to top level
        self.assertEqual(data["lmsBaseUrl"], "https://courses.example.com")
        # Unmapped MFE_CONFIG keys appear in commonAppConfig (not at the top level)
        self.assertNotIn("CREDENTIALS_BASE_URL", data)
        common = data["commonAppConfig"]
        self.assertEqual(common["CREDENTIALS_BASE_URL"], "https://credentials.example.com")
        self.assertEqual(common["STUDIO_BASE_URL"], "https://studio.example.com")
        # Legacy config keys also appear in commonAppConfig
        for legacy_key in default_legacy_config:
            self.assertIn(legacy_key, common)

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_apps_from_overrides(self, configuration_helpers_mock):
        """Each MFE_CONFIG_OVERRIDES entry becomes an app with shared base config + overrides."""
        mfe_config_overrides = {
            "authn": {
                "ALLOW_PUBLIC_ACCOUNT_CREATION": True,
                "ACTIVATION_EMAIL_SUPPORT_LINK": None,
            },
            "learner-dashboard": {
                "LEARNING_BASE_URL": "http://apps.local.openedx.io:2000",
                "ENABLE_PROGRAMS": False,
            },
        }

        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {
                    "LMS_BASE_URL": "https://courses.example.com",
                    "SHARED_SETTING": "shared_value",
                }
            if key == "MFE_CONFIG_OVERRIDES":
                return mfe_config_overrides
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        self.assertIn("apps", data)
        self.assertEqual(len(data["apps"]), 2)

        # Shared config (unmapped MFE_CONFIG keys + legacy config) is in commonAppConfig.
        common = data["commonAppConfig"]
        self.assertEqual(common["SHARED_SETTING"], "shared_value")
        for legacy_key in default_legacy_config:
            self.assertIn(legacy_key, common)

        # Apps should be sorted by MFE name; each carries only its own overrides.
        authn = data["apps"][0]
        self.assertEqual(authn["appId"], "org.openedx.frontend.app.authn")
        self.assertEqual(authn["config"]["ALLOW_PUBLIC_ACCOUNT_CREATION"], True)
        self.assertIsNone(authn["config"]["ACTIVATION_EMAIL_SUPPORT_LINK"])
        # Shared keys are NOT duplicated into per-app config
        self.assertNotIn("SHARED_SETTING", authn["config"])

        dashboard = data["apps"][1]
        self.assertEqual(dashboard["appId"], "org.openedx.frontend.app.learnerDashboard")
        self.assertEqual(dashboard["config"]["LEARNING_BASE_URL"], "http://apps.local.openedx.io:2000")
        self.assertEqual(dashboard["config"]["ENABLE_PROGRAMS"], False)
        self.assertNotIn("SHARED_SETTING", dashboard["config"])

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_app_overrides_separate_from_common(self, configuration_helpers_mock):
        """App-specific overrides appear in per-app config; shared keys in commonAppConfig."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {"SOME_KEY": "base_value"}
            if key == "MFE_CONFIG_OVERRIDES":
                return {"authn": {"SOME_KEY": "overridden_value"}}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        self.assertEqual(data["commonAppConfig"]["SOME_KEY"], "base_value")
        self.assertEqual(data["apps"][0]["config"]["SOME_KEY"], "overridden_value")

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_site_level_keys_stripped_from_app_overrides(self, configuration_helpers_mock):
        """Site-level keys in MFE_CONFIG_OVERRIDES are stripped from app config."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {
                    "LMS_BASE_URL": "https://courses.example.com",
                    "LOGO_URL": "https://courses.example.com/logo.png",
                }
            if key == "MFE_CONFIG_OVERRIDES":
                return {
                    "authn": {
                        "BASE_URL": "https://authn.example.com",
                        "LOGIN_URL": "https://authn.example.com/login",
                        "APP_SPECIFIC_KEY": "app_value",
                    },
                }
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        app_config = data["apps"][0]["config"]
        # Site-level keys from overrides must not appear in app config
        self.assertNotIn("BASE_URL", app_config)
        self.assertNotIn("LOGIN_URL", app_config)
        # Non-site-level override keys are kept
        self.assertEqual(app_config["APP_SPECIFIC_KEY"], "app_value")
        # Site-level keys from overrides also must not appear in commonAppConfig
        self.assertNotIn("BASE_URL", data["commonAppConfig"])
        self.assertNotIn("LOGIN_URL", data["commonAppConfig"])

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_no_apps_when_no_overrides(self, configuration_helpers_mock):
        """The apps key is omitted when MFE_CONFIG_OVERRIDES is empty."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {"LMS_BASE_URL": "https://courses.example.com"}
            if key == "MFE_CONFIG_OVERRIDES":
                return {}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        self.assertNotIn("apps", data)
        # commonAppConfig is still present with legacy keys
        self.assertIn("commonAppConfig", data)
        for legacy_key in default_legacy_config:
            self.assertIn(legacy_key, data["commonAppConfig"])

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_unmapped_keys_in_common_app_config_without_overrides(self, configuration_helpers_mock):
        """Unmapped MFE_CONFIG keys appear in commonAppConfig even without overrides."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {
                    "LMS_BASE_URL": "https://courses.example.com",
                    "CREDENTIALS_BASE_URL": "https://credentials.example.com",
                    "STUDIO_BASE_URL": "https://studio.example.com",
                }
            if key == "MFE_CONFIG_OVERRIDES":
                return {}
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        # Site-level key is promoted to the top level
        self.assertEqual(data["lmsBaseUrl"], "https://courses.example.com")
        # Unmapped keys are preserved in commonAppConfig
        common = data["commonAppConfig"]
        self.assertEqual(common["CREDENTIALS_BASE_URL"], "https://credentials.example.com")
        self.assertEqual(common["STUDIO_BASE_URL"], "https://studio.example.com")

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_invalid_override_entry_skipped(self, configuration_helpers_mock):
        """Non-dict override entries are silently skipped."""
        mfe_config_overrides = {
            "authn": {"SOME_KEY": "value"},
            "broken": "not-a-dict",
        }

        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {}
            if key == "MFE_CONFIG_OVERRIDES":
                return mfe_config_overrides
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        self.assertEqual(len(data["apps"]), 1)
        self.assertEqual(data["apps"][0]["appId"], "org.openedx.frontend.app.authn")

    def test_from_django_settings(self):
        """When there is no site configuration, the API uses django settings."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        # settings.MFE_CONFIG in test.py has LANGUAGE_PREFERENCE_COOKIE_NAME and LOGO_URL
        self.assertEqual(data.get("languagePreferenceCookieName"), "example-language-preference")
        self.assertEqual(data.get("headerLogoImageUrl"), "https://courses.example.com/logo.png")

        # Legacy config keys live in commonAppConfig
        for legacy_key in default_legacy_config:
            self.assertIn(legacy_key, data["commonAppConfig"])

        # MFE_CONFIG_OVERRIDES in test.py has mymfe and yourmfe
        self.assertIn("apps", data)
        app_ids = [app["appId"] for app in data["apps"]]
        self.assertIn("org.openedx.frontend.app.mymfe", app_ids)
        self.assertIn("org.openedx.frontend.app.yourmfe", app_ids)

        # Site-level keys from overrides (LANGUAGE_PREFERENCE_COOKIE_NAME,
        # LOGO_URL in test settings) are stripped from per-app config
        for app in data["apps"]:
            self.assertNotIn("LANGUAGE_PREFERENCE_COOKIE_NAME", app["config"])
            self.assertNotIn("LOGO_URL", app["config"])

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_frontend_site_config_overrides_translated(self, configuration_helpers_mock):
        """FRONTEND_SITE_CONFIG takes highest precedence, overriding translated MFE_CONFIG values."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {
                    "LMS_BASE_URL": "https://courses.example.com",
                    "LOGIN_URL": "https://courses.example.com/login",
                    "LOGOUT_URL": "https://courses.example.com/logout",
                }
            if key == "MFE_CONFIG_OVERRIDES":
                return {}
            if key == "FRONTEND_SITE_CONFIG":
                return {
                    "logoutUrl": "https://courses.example.com/custom-logout",
                    "externalRoutes": [
                        {"role": "learnerDashboard", "url": "https://courses.example.com/dashboard"},
                    ],
                }
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        # Translated value is overridden by FRONTEND_SITE_CONFIG
        self.assertEqual(data["logoutUrl"], "https://courses.example.com/custom-logout")
        # Translated value not in FRONTEND_SITE_CONFIG is preserved
        self.assertEqual(data["loginUrl"], "https://courses.example.com/login")
        # New keys from FRONTEND_SITE_CONFIG are included
        self.assertEqual(
            data["externalRoutes"],
            [{"role": "learnerDashboard", "url": "https://courses.example.com/dashboard"}],
        )

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_frontend_site_config_deep_merges_common_app_config(self, configuration_helpers_mock):
        """FRONTEND_SITE_CONFIG commonAppConfig is merged with (not replacing) translated values."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {
                    "LMS_BASE_URL": "https://courses.example.com",
                    "CREDENTIALS_BASE_URL": "https://credentials.example.com",
                }
            if key == "MFE_CONFIG_OVERRIDES":
                return {}
            if key == "FRONTEND_SITE_CONFIG":
                return {
                    "commonAppConfig": {
                        "CREDENTIALS_BASE_URL": "https://new-credentials.example.com",
                        "NEW_KEY": "new_value",
                    },
                }
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        common = data["commonAppConfig"]
        # FRONTEND_SITE_CONFIG overrides individual keys
        self.assertEqual(common["CREDENTIALS_BASE_URL"], "https://new-credentials.example.com")
        # New keys from FRONTEND_SITE_CONFIG are added
        self.assertEqual(common["NEW_KEY"], "new_value")
        # Legacy translated keys are preserved
        for legacy_key in default_legacy_config:
            self.assertIn(legacy_key, common)

    @patch("lms.djangoapps.mfe_config_api.views.configuration_helpers")
    def test_frontend_site_config_deep_merges_apps(self, configuration_helpers_mock):
        """FRONTEND_SITE_CONFIG apps are merged by appId with translated app entries."""
        def side_effect(key, default=None):
            if key == "MFE_CONFIG":
                return {"LMS_BASE_URL": "https://courses.example.com"}
            if key == "MFE_CONFIG_OVERRIDES":
                return {
                    "authn": {"LEGACY_KEY": "legacy_value", "SHARED_KEY": "old_value"},
                }
            if key == "FRONTEND_SITE_CONFIG":
                return {
                    "apps": [
                        {
                            "appId": "org.openedx.frontend.app.authn",
                            "config": {"SHARED_KEY": "new_value", "NEW_KEY": "added"},
                        },
                        {
                            "appId": "org.openedx.frontend.app.brand.new",
                            "config": {"BRAND_NEW_KEY": "value"},
                        },
                    ],
                }
            return default
        configuration_helpers_mock.get_value.side_effect = side_effect

        response = self.client.get(self.url)
        data = response.json()

        apps_by_id = {app["appId"]: app for app in data["apps"]}
        # Existing app's config is merged, not replaced
        authn = apps_by_id["org.openedx.frontend.app.authn"]["config"]
        self.assertEqual(authn["LEGACY_KEY"], "legacy_value")
        self.assertEqual(authn["SHARED_KEY"], "new_value")
        self.assertEqual(authn["NEW_KEY"], "added")
        # Brand new app from FRONTEND_SITE_CONFIG is appended
        brand_new = apps_by_id["org.openedx.frontend.app.brand.new"]["config"]
        self.assertEqual(brand_new["BRAND_NEW_KEY"], "value")
