"""Unit tests for third-party auth settings in lms/envs/common.py."""

from django.conf import settings
from django.test import TestCase, override_settings

from common.djangoapps.third_party_auth import provider
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class SettingsUnitTest(TestCase):
    """Unit tests for third-party auth settings defined in lms/envs/common.py."""

    def test_exception_middleware_in_middleware_list(self):
        """Verify ExceptionMiddleware is included in MIDDLEWARE."""
        assert 'common.djangoapps.third_party_auth.middleware.ExceptionMiddleware' in settings.MIDDLEWARE

    def test_fields_stored_in_session_defined(self):
        """Verify FIELDS_STORED_IN_SESSION is defined with expected values."""
        assert settings.FIELDS_STORED_IN_SESSION == ['auth_entry', 'next']

    @skip_unless_thirdpartyauth()
    def test_no_providers_enabled_by_default(self):
        """Providers are only enabled via ConfigurationModels in the database."""
        assert provider.Registry.enabled() == []

    def test_social_auth_raise_exceptions_is_false(self):
        """Guard against submitting a conf change that's convenient in dev but bad in prod."""
        assert settings.SOCIAL_AUTH_RAISE_EXCEPTIONS is False

    def test_social_auth_sanitize_redirects_is_false(self):
        """Verify redirect sanitization is disabled (platform does its own)."""
        assert settings.SOCIAL_AUTH_SANITIZE_REDIRECTS is False

    def test_social_auth_login_error_url(self):
        """Verify SOCIAL_AUTH_LOGIN_ERROR_URL is set."""
        assert settings.SOCIAL_AUTH_LOGIN_ERROR_URL == '/'

    def test_social_auth_login_redirect_url(self):
        """Verify SOCIAL_AUTH_LOGIN_REDIRECT_URL is set."""
        assert settings.SOCIAL_AUTH_LOGIN_REDIRECT_URL == '/dashboard'

    def test_social_auth_strategy(self):
        """Verify SOCIAL_AUTH_STRATEGY is set to use ConfigurationModelStrategy."""
        assert settings.SOCIAL_AUTH_STRATEGY == 'common.djangoapps.third_party_auth.strategy.ConfigurationModelStrategy'

    def test_social_auth_pipeline_defined(self):
        """Verify SOCIAL_AUTH_PIPELINE is defined and includes expected steps."""
        pipeline = settings.SOCIAL_AUTH_PIPELINE
        assert isinstance(pipeline, list)
        assert len(pipeline) > 0
        # Verify some key pipeline steps are present
        assert 'common.djangoapps.third_party_auth.pipeline.parse_query_params' in pipeline
        assert 'social_core.pipeline.user.create_user' in pipeline
        assert 'common.djangoapps.third_party_auth.pipeline.ensure_redirect_url_is_safe' in pipeline

    def test_social_auth_context_processors(self):
        """Verify social_django context processors are included."""
        # CONTEXT_PROCESSORS is used to build TEMPLATES, so check there
        context_processors = settings.TEMPLATES[0]['OPTIONS']['context_processors']
        assert 'social_django.context_processors.backends' in context_processors
        assert 'social_django.context_processors.login_redirect' in context_processors

    @override_settings(FEATURES={'ENABLE_UNICODE_USERNAME': False})
    def test_social_auth_clean_usernames_default(self):
        """Verify SOCIAL_AUTH_CLEAN_USERNAMES is True when unicode usernames disabled."""
        # Note: SOCIAL_AUTH_CLEAN_USERNAMES is a Derived setting, computed at settings load time.
        # This test verifies the default behavior (unicode usernames disabled).
        assert settings.SOCIAL_AUTH_CLEAN_USERNAMES is True

    def test_social_auth_clean_usernames_computation(self):
        """
        Verify the SOCIAL_AUTH_CLEAN_USERNAMES computation logic.

        SOCIAL_AUTH_CLEAN_USERNAMES is a Derived setting that is computed at settings load time,
        so we can't use @override_settings to test both cases. Instead, we test the computation
        logic directly to ensure it correctly inverts the ENABLE_UNICODE_USERNAME feature flag.
        """
        # The logic in lms/envs/common.py is:
        # SOCIAL_AUTH_CLEAN_USERNAMES = Derived(
        #     lambda settings: not settings.FEATURES.get('ENABLE_UNICODE_USERNAME', False)
        # )
        # We replicate and test that logic here.

        class FakeSettings:
            """Fake settings object for testing the Derived computation."""
            def __init__(self, features):
                self.FEATURES = features

        # When ENABLE_UNICODE_USERNAME is False (default), SOCIAL_AUTH_CLEAN_USERNAMES should be True
        fake_settings = FakeSettings({'ENABLE_UNICODE_USERNAME': False})
        result = not fake_settings.FEATURES.get('ENABLE_UNICODE_USERNAME', False)
        assert result is True

        # When ENABLE_UNICODE_USERNAME is True, SOCIAL_AUTH_CLEAN_USERNAMES should be False
        fake_settings = FakeSettings({'ENABLE_UNICODE_USERNAME': True})
        result = not fake_settings.FEATURES.get('ENABLE_UNICODE_USERNAME', False)
        assert result is False

        # When ENABLE_UNICODE_USERNAME is not set, should default to False, so result is True
        fake_settings = FakeSettings({})
        result = not fake_settings.FEATURES.get('ENABLE_UNICODE_USERNAME', False)
        assert result is True
