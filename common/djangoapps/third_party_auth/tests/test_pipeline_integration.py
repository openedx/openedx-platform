"""Integration tests for pipeline.py."""


import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import ddt
import pytest
from django import test
from django.contrib.auth import models, REDIRECT_FIELD_NAME
from django.core import mail
from social_django import models as social_models

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth import pipeline, provider
from common.djangoapps.third_party_auth.tests import testutil
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth
from lms.djangoapps.verify_student.models import SSOVerification

# Get Django User model by reference from python-social-auth. Not a type
# constant, pylint.
User = social_models.DjangoStorage.user.user_model()  # pylint: disable=invalid-name


@skip_unless_thirdpartyauth()
class TestCase(testutil.TestCase, test.TestCase):
    """Base test case."""

    def setUp(self):
        super().setUp()
        self.enabled_provider = self.configure_google_provider(enabled=True)


class GetAuthenticatedUserTestCase(TestCase):
    """Tests for get_authenticated_user."""

    def setUp(self):
        super().setUp()
        self.user = social_models.DjangoStorage.user.create_user(username='username', password='password')

    def get_by_username(self, username):
        """Gets a User by username."""
        return social_models.DjangoStorage.user.user_model().objects.get(username=username)

    def test_raises_does_not_exist_if_user_missing(self):
        with pytest.raises(models.User.DoesNotExist):
            pipeline.get_authenticated_user(self.enabled_provider, 'new_' + self.user.username, 'user@example.com')

    def test_raises_does_not_exist_if_user_found_but_no_association(self):
        backend_name = 'backend'

        assert self.get_by_username(self.user.username) is not None
        assert not any(provider.Registry.get_enabled_by_backend_name(backend_name))

        with pytest.raises(models.User.DoesNotExist):
            pipeline.get_authenticated_user(self.enabled_provider, self.user.username, 'user@example.com')

    def test_raises_does_not_exist_if_user_and_association_found_but_no_match(self):
        assert self.get_by_username(self.user.username) is not None
        social_models.DjangoStorage.user.create_social_auth(
            self.user, 'uid', 'other_' + self.enabled_provider.backend_name)

        with pytest.raises(models.User.DoesNotExist):
            pipeline.get_authenticated_user(self.enabled_provider, self.user.username, 'uid')

    def test_returns_user_with_is_authenticated_and_backend_set_if_match(self):
        social_models.DjangoStorage.user.create_social_auth(self.user, 'uid', self.enabled_provider.backend_name)
        user = pipeline.get_authenticated_user(self.enabled_provider, self.user.username, 'uid')

        assert self.user == user
        assert self.enabled_provider.get_authentication_backend() == user.backend


class GetProviderUserStatesTestCase(TestCase):
    """Tests generation of ProviderUserStates."""

    def setUp(self):
        super().setUp()
        self.configure_google_provider(enabled=False)
        self.user = social_models.DjangoStorage.user.create_user(username='username', password='password')

    def test_returns_empty_list_if_no_enabled_providers(self):
        assert not provider.Registry.enabled()
        assert not pipeline.get_provider_user_states(self.user)

    def test_state_not_returned_for_disabled_provider(self):
        disabled_provider = self.configure_google_provider(enabled=False)
        enabled_provider = self.configure_facebook_provider(enabled=True)
        social_models.DjangoStorage.user.create_social_auth(self.user, 'uid', disabled_provider.backend_name)
        states = pipeline.get_provider_user_states(self.user)

        assert 1 == len(states)
        assert disabled_provider.provider_id not in (state.provider.provider_id for state in states)
        assert enabled_provider.provider_id in (state.provider.provider_id for state in states)

    def test_states_for_enabled_providers_user_has_accounts_associated_with(self):
        # Enable two providers - Google and LinkedIn:
        google_provider = self.configure_google_provider(enabled=True)
        linkedin_provider = self.configure_linkedin_provider(enabled=True)
        user_social_auth_google = social_models.DjangoStorage.user.create_social_auth(
            self.user, 'uid', google_provider.backend_name)
        user_social_auth_linkedin = social_models.DjangoStorage.user.create_social_auth(
            self.user, 'uid', linkedin_provider.backend_name)
        states = pipeline.get_provider_user_states(self.user)

        assert 2 == len(states)

        google_state = [state for state in states if state.provider.provider_id == google_provider.provider_id][0]
        linkedin_state = [state for state in states if state.provider.provider_id == linkedin_provider.provider_id][0]

        assert google_state.has_account
        assert google_provider.provider_id == google_state.provider.provider_id
        # Also check the row ID. Note this 'id' changes whenever the configuration does:
        assert google_provider.id == google_state.provider.id
        assert self.user == google_state.user
        assert user_social_auth_google.id == google_state.association_id

        assert linkedin_state.has_account
        assert linkedin_provider.provider_id == linkedin_state.provider.provider_id
        assert linkedin_provider.id == linkedin_state.provider.id
        assert self.user == linkedin_state.user
        assert user_social_auth_linkedin.id == linkedin_state.association_id

    def test_states_for_enabled_providers_user_has_no_account_associated_with(self):
        # Enable two providers - Google and LinkedIn:
        google_provider = self.configure_google_provider(enabled=True)
        linkedin_provider = self.configure_linkedin_provider(enabled=True)
        assert len(provider.Registry.enabled()) == 2

        states = pipeline.get_provider_user_states(self.user)

        assert not list(social_models.DjangoStorage.user.objects.all())
        assert 2 == len(states)

        google_state = [state for state in states if state.provider.provider_id == google_provider.provider_id][0]
        linkedin_state = [state for state in states if state.provider.provider_id == linkedin_provider.provider_id][0]

        assert not google_state.has_account
        assert google_provider.provider_id == google_state.provider.provider_id
        # Also check the row ID. Note this 'id' changes whenever the configuration does:
        assert google_provider.id == google_state.provider.id
        assert self.user == google_state.user

        assert not linkedin_state.has_account
        assert linkedin_provider.provider_id == linkedin_state.provider.provider_id
        assert linkedin_provider.id == linkedin_state.provider.id
        assert self.user == linkedin_state.user


class UrlFormationTestCase(TestCase):
    """Tests formation of URLs for pipeline hook points."""

    def test_complete_url_raises_value_error_if_provider_not_enabled(self):
        provider_name = 'oa2-not-enabled'

        assert provider.Registry.get(provider_name) is None

        with pytest.raises(ValueError):
            pipeline.get_complete_url(provider_name)

    def test_complete_url_returns_expected_format(self):
        complete_url = pipeline.get_complete_url(self.enabled_provider.backend_name)

        assert complete_url.startswith('/auth/complete')
        assert self.enabled_provider.backend_name in complete_url

    def test_disconnect_url_raises_value_error_if_provider_not_enabled(self):
        provider_name = 'oa2-not-enabled'

        assert provider.Registry.get(provider_name) is None

        with pytest.raises(ValueError):
            pipeline.get_disconnect_url(provider_name, 1000)

    def test_disconnect_url_returns_expected_format(self):
        disconnect_url = pipeline.get_disconnect_url(self.enabled_provider.provider_id, 1000)
        disconnect_url = disconnect_url.rstrip('?')
        assert disconnect_url == '/auth/disconnect_json/{backend}/{association_id}/'\
            .format(backend=self.enabled_provider.backend_name, association_id=1000)

    def test_login_url_raises_value_error_if_provider_not_enabled(self):
        provider_id = 'oa2-not-enabled'

        assert provider.Registry.get(provider_id) is None

        with pytest.raises(ValueError):
            pipeline.get_login_url(provider_id, pipeline.AUTH_ENTRY_LOGIN)

    def test_login_url_returns_expected_format(self):
        login_url = pipeline.get_login_url(self.enabled_provider.provider_id, pipeline.AUTH_ENTRY_LOGIN)

        assert login_url.startswith('/auth/login')
        assert self.enabled_provider.backend_name in login_url
        assert login_url.endswith(pipeline.AUTH_ENTRY_LOGIN)

    def test_for_value_error_if_provider_id_invalid(self):
        provider_id = 'invalid'  # Format is normally "{prefix}-{identifier}"

        with pytest.raises(ValueError):
            provider.Registry.get(provider_id)

        with pytest.raises(ValueError):
            pipeline.get_login_url(provider_id, pipeline.AUTH_ENTRY_LOGIN)

        with pytest.raises(ValueError):
            pipeline.get_disconnect_url(provider_id, 1000)

        with pytest.raises(ValueError):
            pipeline.get_complete_url(provider_id)


class TestPipelineUtilityFunctions(TestCase):
    """
    Test some of the isolated utility functions in the pipeline
    """
    def setUp(self):
        super().setUp()
        self.user = social_models.DjangoStorage.user.create_user(username='username', password='password')
        self.social_auth = social_models.UserSocialAuth.objects.create(
            user=self.user,
            uid='fake uid',
            provider='fake provider'
        )

    def test_get_real_social_auth_from_dict(self):
        """
        Test that we can use a dictionary with a UID entry to retrieve a
        database-backed UserSocialAuth object.
        """
        request = mock.MagicMock()
        pipeline_partial = {
            'kwargs': {
                'social': {
                    'uid': 'fake uid'
                }
            }
        }

        with mock.patch('common.djangoapps.third_party_auth.pipeline.get') as get_pipeline:
            get_pipeline.return_value = pipeline_partial
            real_social = pipeline.get_real_social_auth_object(request)
            assert real_social == self.social_auth

    def test_get_real_social_auth(self):
        """
        Test that trying to get a database-backed UserSocialAuth from an existing
        instance returns correctly.
        """
        request = mock.MagicMock()
        pipeline_partial = {
            'kwargs': {
                'social': self.social_auth
            }
        }

        with mock.patch('common.djangoapps.third_party_auth.pipeline.get') as get_pipeline:
            get_pipeline.return_value = pipeline_partial
            real_social = pipeline.get_real_social_auth_object(request)
            assert real_social == self.social_auth

    def test_get_real_social_auth_no_pipeline(self):
        """
        Test that if there's no running pipeline, we return None when looking
        for a database-backed UserSocialAuth object.
        """
        request = mock.MagicMock(session={})
        real_social = pipeline.get_real_social_auth_object(request)
        assert real_social is None

    def test_get_real_social_auth_no_social(self):
        """
        Test that if a UserSocialAuth object hasn't been attached to the pipeline as
        `social`, we return none
        """
        request = mock.MagicMock(
            session={
                'running_pipeline': {
                    'kwargs': {}
                }
            }
        )
        real_social = pipeline.get_real_social_auth_object(request)
        assert real_social is None

    def test_quarantine(self):
        """
        Test that quarantining a session adds the correct flags, and that
        lifting the quarantine similarly removes those flags.
        """
        request = mock.MagicMock(
            session={}
        )
        pipeline.quarantine_session(request, locations=('my_totally_real_module', 'other_real_module',))
        assert request.session['third_party_auth_quarantined_modules'] ==\
               ('my_totally_real_module', 'other_real_module')
        pipeline.lift_quarantine(request)
        assert 'third_party_auth_quarantined_modules' not in request.session


@ddt.ddt
class EnsureUserInformationTestCase(TestCase):
    """Tests ensuring that we have the necessary user information to proceed with the pipeline."""

    def setUp(self):
        super().setUp()
        self.user = social_models.DjangoStorage.user.create_user(
            username='username',
            password='password',
            email='email@example.com',
        )

    @ddt.data(
        (True, '/register'),
        (False, '/login')
    )
    @ddt.unpack
    def test_provider_settings_redirect_to_registration(self, send_to_registration_first, expected_redirect_url):
        """
        Test if user is not authenticated, that they get redirected to the appropriate page
        based on the provider's setting for send_to_registration_first.
        """

        provider = mock.MagicMock(  # lint-amnesty, pylint: disable=redefined-outer-name
            send_to_registration_first=send_to_registration_first,
            skip_email_verification=False
        )

        with mock.patch('common.djangoapps.third_party_auth.pipeline.provider.Registry.get_from_pipeline') as get_from_pipeline:  # lint-amnesty, pylint: disable=line-too-long
            get_from_pipeline.return_value = provider
            with mock.patch('social_core.pipeline.partial.partial_prepare') as partial_prepare:
                partial_prepare.return_value = mock.MagicMock(token='')
                strategy = mock.MagicMock()
                response = pipeline.ensure_user_information(
                    strategy=strategy,
                    backend=None,
                    auth_entry=pipeline.AUTH_ENTRY_LOGIN,
                    pipeline_index=0
                )
                assert response.status_code == 302
                assert response.url == expected_redirect_url

    @ddt.data(
        ('non_existing_user_email@example.com', '/register', True),
        ('email@example.com', '/login', True),
        (None, '/register', True),
        ('non_existing_user_email@example.com', '/register', False),
        ('email@example.com', '/login', False),
        (None, '/login', False),
    )
    @ddt.unpack
    def test_redirect_for_saml_based_on_email_only(self, email, expected_redirect_url, is_saml):
        """
        Test that only email(and not username) is used by saml based auth flows
        to determine if a user already exists
        """
        saml_provider = mock.MagicMock(
            slug='unique_slug',
            send_to_registration_first=True,
            skip_email_verification=False
        )
        with mock.patch('common.djangoapps.third_party_auth.pipeline.provider.Registry.get_from_pipeline') as get_from_pipeline:  # lint-amnesty, pylint: disable=line-too-long
            get_from_pipeline.return_value = saml_provider
            with mock.patch(
                'common.djangoapps.third_party_auth.pipeline.provider.Registry.get_enabled_by_backend_name'
            ) as enabled_saml_providers:
                enabled_saml_providers.return_value = [saml_provider, ] if is_saml else []
                with mock.patch('social_core.pipeline.partial.partial_prepare') as partial_prepare:
                    partial_prepare.return_value = mock.MagicMock(token='')
                    strategy = mock.MagicMock()
                    response = pipeline.ensure_user_information(
                        strategy=strategy,
                        backend=None,
                        auth_entry=pipeline.AUTH_ENTRY_LOGIN,
                        pipeline_index=0,
                        details={'username': self.user.username, 'email': email}
                    )
                    assert response.status_code == 302
                    assert response.url == expected_redirect_url


class UserDetailsForceSyncTestCase(TestCase):
    """Tests to ensure learner profile data is properly synced if the provider requires it."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.old_email = self.user.email
        self.old_username = self.user.username
        self.old_fullname = self.user.profile.name
        self.details = {
            'email': f'new+{self.user.email}',
            'username': f'new_{self.user.username}',
            'fullname': f'Grown Up {self.user.profile.name}',
            'country': 'PK',
            'non_existing_field': 'value',
        }

        # Mocks
        self.strategy = mock.MagicMock()
        self.strategy.storage.user.changed.side_effect = lambda user: user.save()

        get_from_pipeline = mock.patch('common.djangoapps.third_party_auth.pipeline.provider.Registry.get_from_pipeline')  # lint-amnesty, pylint: disable=line-too-long
        self.get_from_pipeline = get_from_pipeline.start()
        self.get_from_pipeline.return_value = mock.MagicMock(sync_learner_profile_data=True)
        self.addCleanup(get_from_pipeline.stop)

    def test_user_details_force_sync(self):
        """
        The user details are synced properly and an email is sent when the email is changed.
        """
        # Begin the pipeline.
        pipeline.user_details_force_sync(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN,
            strategy=self.strategy,
            details=self.details,
            user=self.user,
        )

        # User now has updated information in the DB.
        user = User.objects.get()
        assert user.email == f'new+{self.old_email}'
        assert user.profile.name == f'Grown Up {self.old_fullname}'
        assert user.profile.country == 'PK'

        # Now verify that username field is not updated
        assert user.username == self.old_username

        assert len(mail.outbox) == 1

    def test_user_details_force_sync_email_conflict(self):
        """
        The user details were attempted to be synced but the incoming email already exists for another account.
        """
        # Create a user with an email that conflicts with the incoming value.
        UserFactory.create(email=f'new+{self.old_email}')

        # Begin the pipeline.
        pipeline.user_details_force_sync(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN,
            strategy=self.strategy,
            details=self.details,
            user=self.user,
        )

        # The email is not changed, but everything else is.
        user = User.objects.get(pk=self.user.pk)
        assert user.email == self.old_email
        assert user.profile.name == f'Grown Up {self.old_fullname}'
        assert user.profile.country == 'PK'

        # Now verify that username field is not updated
        assert user.username == self.old_username

        # No email should be sent for an email change.
        assert len(mail.outbox) == 0

    def test_user_details_force_sync_username_conflict(self):
        """
        The user details were attempted to be synced but the incoming username already exists for another account.

        An email should still be sent in this case.
        """
        # Create a user with an email that conflicts with the incoming value.
        UserFactory.create(username=f'new_{self.old_username}')

        # Begin the pipeline.
        pipeline.user_details_force_sync(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN,
            strategy=self.strategy,
            details=self.details,
            user=self.user,
        )

        # The username is not changed, but everything else is.
        user = User.objects.get(pk=self.user.pk)
        assert user.email == f'new+{self.old_email}'
        assert user.username == self.old_username
        assert user.profile.name == f'Grown Up {self.old_fullname}'
        assert user.profile.country == 'PK'

        # An email should still be sent because the email changed.
        assert len(mail.outbox) == 1


class SetIDVerificationStatusTestCase(TestCase):
    """Tests to ensure SSO ID Verification for the user is set if the provider requires it."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.provider_class_name = 'common.djangoapps.third_party_auth.models.SAMLProviderConfig'
        self.provider_slug = 'default'
        self.details = {}

        # Mocks
        self.strategy = mock.MagicMock()
        self.strategy.storage.user.changed.side_effect = lambda user: user.save()

        get_from_pipeline = mock.patch('common.djangoapps.third_party_auth.pipeline.provider.Registry.get_from_pipeline')  # lint-amnesty, pylint: disable=line-too-long
        self.get_from_pipeline = get_from_pipeline.start()
        self.get_from_pipeline.return_value = mock.MagicMock(
            enable_sso_id_verification=True,
            full_class_name=self.provider_class_name,
            slug=self.provider_slug,
        )
        self.addCleanup(get_from_pipeline.stop)

    def test_set_id_verification_status_new_user(self):
        """
        The user details are synced properly and an email is sent when the email is changed.
        """
        # Begin the pipeline.
        pipeline.set_id_verification_status(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN,
            strategy=self.strategy,
            details=self.details,
            user=self.user,
        )

        verification = SSOVerification.objects.get(user=self.user)

        assert verification.identity_provider_type == self.provider_class_name
        assert verification.identity_provider_slug == self.provider_slug
        assert verification.status == 'approved'
        assert verification.name == self.user.profile.name

    def test_set_id_verification_status_returning_user(self):
        """
        The user details are synced properly and an email is sent when the email is changed.
        """

        SSOVerification.objects.create(
            user=self.user,
            status="approved",
            name=self.user.profile.name,
            identity_provider_type=self.provider_class_name,
            identity_provider_slug=self.provider_slug,
        )

        # Begin the pipeline.
        pipeline.set_id_verification_status(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN,
            strategy=self.strategy,
            details=self.details,
            user=self.user,
        )

        assert SSOVerification.objects.filter(user=self.user).count() == 1

    def test_set_id_verification_status_expired(self):
        """
        The user details are synced properly and an email is sent when the email is changed.
        """

        SSOVerification.objects.create(
            user=self.user,
            status="approved",
            name=self.user.profile.name,
            identity_provider_type=self.provider_class_name,
            identity_provider_slug=self.provider_slug,
        )

        with mock.patch('common.djangoapps.third_party_auth.pipeline.earliest_allowed_verification_date') as earliest_date:  # lint-amnesty, pylint: disable=line-too-long
            earliest_date.return_value = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=1)
            # Begin the pipeline.
            pipeline.set_id_verification_status(
                auth_entry=pipeline.AUTH_ENTRY_LOGIN,
                strategy=self.strategy,
                details=self.details,
                user=self.user,
            )

            assert SSOVerification.objects.filter(
                user=self.user,
                status="approved",
                name=self.user.profile.name,
                identity_provider_type=self.provider_class_name,
                identity_provider_slug=self.provider_slug,
            ).count() == 2

    def test_verification_signal(self):
        """
        Verification signal is sent upon approval.
        """
        with mock.patch('openedx.core.djangoapps.signals.signals.LEARNER_SSO_VERIFIED.send_robust') as mock_signal:
            # Begin the pipeline.
            pipeline.set_id_verification_status(
                auth_entry=pipeline.AUTH_ENTRY_LOGIN,
                strategy=self.strategy,
                details=self.details,
                user=self.user,
            )

        # Ensure a verification signal was sent
        assert mock_signal.call_count == 1


class ParseQueryParamsPipelineTestCase(TestCase):
    """Tests to ensure reading queryparams from the auth/login URL works as expected."""

    def setUp(self):
        super().setUp()
        self.strategy = mock.MagicMock()
        self.response = mock.MagicMock()

    def test_login_url_with_auth_entry(self):
        """
        Parsing query params with auth entry results in dictionary with the auth entry.
        """
        expected_query_params = {
            "auth_entry": "login",
        }
        self.strategy.request.session = expected_query_params

        query_params = pipeline.parse_query_params(self.strategy, self.response)

        self.assertDictEqual(expected_query_params, query_params)

    def test_login_url_with_auth_entry_none(self):
        """
        Parsing query params with auth entry equals to None results in dictionary with default auth entry.
        """
        expected_query_params = {
            "auth_entry": "login",
        }
        self.strategy.request.session = {
            "auth_entry": None,
        }

        query_params = pipeline.parse_query_params(self.strategy, self.response)

        self.assertDictEqual(expected_query_params, query_params)

    def test_login_url_without_auth_entry(self):
        """
        Parsing query params without auth entry results in dictionary with default auth entry.
        """
        expected_query_params = {
            "auth_entry": "login",
        }
        self.strategy.request.session = {}

        query_params = pipeline.parse_query_params(self.strategy, self.response)

        self.assertDictEqual(expected_query_params, query_params)

    def test_login_url_invalid_auth_entry(self):
        """
        Parsing query params with invalid auth entry results in AuthEntryError.
        """
        self.strategy.request.session = {
            "auth_entry": "not-valid",
        }

        with self.assertRaises(pipeline.AuthEntryError):
            pipeline.parse_query_params(self.strategy, self.response)


class EnsureRedirectUrlIsSafeTestCase(TestCase):
    """Tests to ensure that the redirect url is safe and user can redirect to it."""

    def setUp(self):
        super().setUp()

        self.strategy = mock.MagicMock()
        self.request = self.strategy.request

        self.request.get_host.return_value = 'localhost:18000'
        self.request.is_secure.return_value = True

        self.strategy.session_get = self._session_get
        self.strategy.session_set = self._session_set

    def _session_get(self, key, default=None):
        if key in self.strategy.session:
            return self.strategy.session[key]

        return default

    def _session_set(self, key, value):
        self.strategy.session[key] = value

    @test.override_settings(SOCIAL_AUTH_LOGIN_REDIRECT_URL='dashboard_url')
    def test_unsafe_redirect_url(self):
        """
        Test that if unsafe url is set as the redirect url then
        redirect url is updated to SOCIAL_AUTH_LOGIN_REDIRECT_URL
        """
        self.strategy.session = {
            REDIRECT_FIELD_NAME: 'https://evil.com',
        }

        pipeline.ensure_redirect_url_is_safe(self.strategy)
        assert self.strategy.session[REDIRECT_FIELD_NAME] == 'dashboard_url'

    @test.override_settings(LOGIN_REDIRECT_WHITELIST=['localhost:1991'])
    def test_whitelisted_redirect_urls(self):
        """
        Test that redirect url remains same if its whitelisted
        """
        safe_redirect_url = 'https://localhost:1991/course'
        self.strategy.session = {REDIRECT_FIELD_NAME: safe_redirect_url}

        pipeline.ensure_redirect_url_is_safe(self.strategy)
        assert self.strategy.session[REDIRECT_FIELD_NAME] == safe_redirect_url
