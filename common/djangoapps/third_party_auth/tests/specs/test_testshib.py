"""
Third_party_auth integration tests using a mock version of the TestShib provider
"""

import datetime
import json
import logging
import os
from unittest import skip
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, quote, urlparse

import ddt
import httpretty
from django.contrib import auth
from freezegun import freeze_time
from social_core import actions
from social_django import views as social_views
from social_django.models import UserSocialAuth
from testfixtures import LogCapture

from common.djangoapps.third_party_auth import pipeline
from common.djangoapps.third_party_auth.exceptions import IncorrectConfigurationException
from common.djangoapps.third_party_auth.saml import SapSuccessFactorsIdentityProvider
from common.djangoapps.third_party_auth.saml import log as saml_log
from common.djangoapps.third_party_auth.signals import SAMLAccountDisconnected
from common.djangoapps.third_party_auth.tasks import fetch_saml_metadata
from common.djangoapps.third_party_auth.tests import testutil, utils
from common.test.utils import assert_dict_contains_subset
from openedx.core.djangoapps.user_authn.views.login import login_user

from .base import IntegrationTestMixin

TESTSHIB_ENTITY_ID = "https://idp.testshib.org/idp/shibboleth"
TESTSHIB_METADATA_URL = "https://mock.testshib.org/metadata/testshib-providers.xml"
TESTSHIB_METADATA_URL_WITH_CACHE_DURATION = "https://mock.testshib.org/metadata/testshib-providers-cache.xml"
TESTSHIB_SSO_URL = "https://idp.testshib.org/idp/profile/SAML2/Redirect/SSO"


class SamlIntegrationTestUtilities:
    """
    Class contains methods particular to SAML integration testing so that they
    can be separated out from the actual test methods.
    """

    PROVIDER_ID = "saml-testshib"
    PROVIDER_NAME = "TestShib"
    PROVIDER_BACKEND = "tpa-saml"
    PROVIDER_IDP_SLUG = "testshib"

    USER_EMAIL = "myself@testshib.org"
    USER_NAME = "Me Myself And I"
    USER_USERNAME = "myself"

    def setUp(self):
        super().setUp()  # pylint: disable=no-member, super-with-arguments
        self.enable_saml(  # pylint: disable=no-member
            private_key=self._get_private_key(),  # pylint: disable=no-member
            public_key=self._get_public_key(),  # pylint: disable=no-member
            entity_id="https://saml.example.none",
        )
        # Mock out HTTP requests that may be made to TestShib:
        httpretty.enable()
        httpretty.reset()
        self.addCleanup(httpretty.reset)  # pylint: disable=no-member
        self.addCleanup(httpretty.disable)  # pylint: disable=no-member

        def metadata_callback(_request, _uri, headers):
            """Return a cached copy of TestShib's metadata by reading it from disk"""
            return (
                200,
                headers,
                self.read_data_file("testshib_metadata.xml"),
            )  # pylint: disable=no-member

        httpretty.register_uri(httpretty.GET, TESTSHIB_METADATA_URL, content_type="text/xml", body=metadata_callback)

        def cache_duration_metadata_callback(_request, _uri, headers):
            """Return a cached copy of TestShib's metadata with a cacheDuration attribute"""
            return (
                200,
                headers,
                self.read_data_file("testshib_metadata_with_cache_duration.xml"),
            )  # pylint: disable=no-member

        httpretty.register_uri(
            httpretty.GET,
            TESTSHIB_METADATA_URL_WITH_CACHE_DURATION,
            content_type="text/xml",
            body=cache_duration_metadata_callback,
        )

        # Configure the SAML library to use the same request ID for every request.
        # Doing this and freezing the time allows us to play back recorded request/response pairs
        uid_patch = patch("onelogin.saml2.utils.OneLogin_Saml2_Utils.generate_unique_id", return_value="TESTID")
        uid_patch.start()
        self.addCleanup(uid_patch.stop)  # pylint: disable=no-member
        self._freeze_time(timestamp=1434326820)  # This is the time when the saved request/response was recorded.

    def _freeze_time(self, timestamp):
        """Mock the current time for SAML, so we can replay canned requests/responses"""
        now_patch = patch("onelogin.saml2.utils.OneLogin_Saml2_Utils.now", return_value=timestamp)
        now_patch.start()
        self.addCleanup(now_patch.stop)  # pylint: disable=no-member

    def _configure_testshib_provider(self, **kwargs):
        """Enable and configure the TestShib SAML IdP as a third_party_auth provider"""
        fetch_metadata = kwargs.pop("fetch_metadata", True)
        assert_metadata_updates = kwargs.pop("assert_metadata_updates", True)
        kwargs.setdefault("name", self.PROVIDER_NAME)
        kwargs.setdefault("enabled", True)
        kwargs.setdefault("visible", True)
        kwargs.setdefault("backend_name", "tpa-saml")
        kwargs.setdefault("slug", self.PROVIDER_IDP_SLUG)
        kwargs.setdefault("entity_id", TESTSHIB_ENTITY_ID)
        kwargs.setdefault("metadata_source", TESTSHIB_METADATA_URL)
        kwargs.setdefault("icon_class", "fa-university")
        kwargs.setdefault("attr_email", "urn:oid:1.3.6.1.4.1.5923.1.1.1.6")  # eduPersonPrincipalName
        kwargs.setdefault("max_session_length", None)
        kwargs.setdefault("send_to_registration_first", False)
        kwargs.setdefault("skip_email_verification", False)
        saml_provider = self.configure_saml_provider(**kwargs)  # pylint: disable=no-member

        if fetch_metadata:
            assert httpretty.is_enabled()  # pylint: disable=no-member
            num_total, num_skipped, num_attempted, num_updated, num_failed, failure_messages = fetch_saml_metadata()
            if assert_metadata_updates:
                assert num_total == 1  # pylint: disable=no-member
                assert num_skipped == 0  # pylint: disable=no-member
                assert num_attempted == 1  # pylint: disable=no-member
                assert num_updated == 1  # pylint: disable=no-member
                assert num_failed == 0  # pylint: disable=no-member
                assert len(failure_messages) == 0  # pylint: disable=no-member
        return saml_provider

    def do_provider_login(self, provider_redirect_url):
        """Mocked: the user logs in to TestShib and then gets redirected back"""
        # The SAML provider (TestShib) will authenticate the user, then get the browser to POST a response:
        assert provider_redirect_url.startswith(TESTSHIB_SSO_URL)  # pylint: disable=no-member

        saml_response_xml = utils.read_and_pre_process_xml(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "testshib_saml_response.xml")
        )

        data = utils.prepare_saml_response_from_xml(saml_response_xml)

        # Extract RelayState from the redirect to IdP
        parsed_url = urlparse(provider_redirect_url)
        query_params = parse_qs(parsed_url.query)
        relay_state = query_params.get('RelayState', [''])[0]

        if relay_state:
            data += '&RelayState=' + quote(relay_state)  # Append as string to the URL-encoded data

        return self.client.post(  # pylint: disable=no-member
            self.complete_url,  # pylint: disable=no-member
            content_type="application/x-www-form-urlencoded",
            data=data,
        )


@ddt.ddt
@utils.skip_unless_thirdpartyauth()
class TestIndexExceptionTest(SamlIntegrationTestUtilities, IntegrationTestMixin, testutil.SAMLTestCase):
    """
    To test SAML error handling when presented with an empty-list attribute value
    """

    TOKEN_RESPONSE_DATA = {
        "access_token": "access_token_value",
        "expires_in": "expires_in_value",
    }
    USER_RESPONSE_DATA = {
        "lastName": "lastName_value",
        "id": "id_value",
        "firstName": "firstName_value",
        "idp_name": "testshib",
        "attributes": {"urn:oid:0.9.2342.19200300.100.1.1": [], "name_id": "1"},
        "session_index": "1",
    }

    def test_index_error_from_empty_list_saml_attribute(self):
        """
        The `urn:oid:0.9.2342.19200300.100.1.1` attribute is an empty list,
        should throw a specific exception NOT an IndexException
        """
        self.provider = self._configure_testshib_provider()
        request, strategy = self.get_request_and_strategy(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN, redirect_uri="social:complete"
        )
        with self.assertRaises(IncorrectConfigurationException):  # noqa: PT027
            request.backend.auth_complete = MagicMock(return_value=self.fake_auth_complete(strategy))

    def get_response_data(self):
        """Gets dict (string -> object) of merged data about the user."""
        response_data = dict(self.TOKEN_RESPONSE_DATA)
        response_data.update(self.USER_RESPONSE_DATA)
        return response_data


@ddt.ddt
@utils.skip_unless_thirdpartyauth()
class TestShibIntegrationTest(SamlIntegrationTestUtilities, IntegrationTestMixin, testutil.SAMLTestCase):
    """
    TestShib provider Integration Test, to test SAML functionality
    """

    TOKEN_RESPONSE_DATA = {
        "access_token": "access_token_value",
        "expires_in": "expires_in_value",
    }
    USER_RESPONSE_DATA = {
        "lastName": "lastName_value",
        "id": "id_value",
        "firstName": "firstName_value",
        "idp_name": "testshib",
        "attributes": {"urn:oid:0.9.2342.19200300.100.1.1": ["myself"], "name_id": "1"},
        "session_index": "1",
    }

    def test_full_pipeline_succeeds_for_unlinking_testshib_account(self):

        # First, create, the request and strategy that store pipeline state,
        # configure the backend, and mock out wire traffic.
        self.provider = self._configure_testshib_provider()
        request, strategy = self.get_request_and_strategy(
            auth_entry=pipeline.AUTH_ENTRY_LOGIN, redirect_uri="social:complete"
        )
        request.backend.auth_complete = MagicMock(return_value=self.fake_auth_complete(strategy))
        user = self.create_user_models_for_existing_account(
            strategy, "user@example.com", "password", self.get_username()
        )
        self.assert_social_auth_exists_for_user(user, strategy)

        request.user = user

        # We're already logged in, so simulate that the cookie is set correctly
        self.set_logged_in_cookies(request)

        # Instrument the pipeline to get to the dashboard with the full expected state.
        self.client.get(pipeline.get_login_url(self.provider.provider_id, pipeline.AUTH_ENTRY_LOGIN))

        actions.do_complete(
            request.backend, social_views._do_login, request=request  # pylint: disable=protected-access
        )

        with self._patch_edxmako_current_request(strategy.request):
            login_user(strategy.request)
            actions.do_complete(
                request.backend, social_views._do_login, user=user, request=request  # pylint: disable=protected-access
            )

        # First we expect that we're in the linked state, with a backend entry.
        self.assert_social_auth_exists_for_user(request.user, strategy)

        # Track signal emissions to verify the disconnect signal is sent.
        signal_calls = []

        def signal_receiver(sender, **kwargs):
            signal_calls.append(kwargs)

        SAMLAccountDisconnected.connect(signal_receiver)
        try:
            # Fire off the disconnect pipeline to unlink.
            self.assert_redirect_after_pipeline_completes(
                actions.do_disconnect(
                    request.backend, user, None, redirect_field_name=auth.REDIRECT_FIELD_NAME, request=request
                )
            )
        finally:
            SAMLAccountDisconnected.disconnect(signal_receiver)

        # Now we expect to be in the unlinked state, with no backend entry.
        self.assert_third_party_accounts_state(request, linked=False)
        self.assert_social_auth_does_not_exist_for_user(user, strategy)
        # Verify that the SAMLAccountDisconnected signal was emitted.
        # The actual enterprise user unlinking is handled by edx-enterprise's
        # signal handler, not by openedx-platform.
        assert len(signal_calls) == 1, f"Expected 1 signal emission, got {len(signal_calls)}"

    def get_response_data(self):
        """Gets dict (string -> object) of merged data about the user."""
        response_data = dict(self.TOKEN_RESPONSE_DATA)
        response_data.update(self.USER_RESPONSE_DATA)
        return response_data

    def get_username(self):
        response_data = self.get_response_data()
        return response_data.get("idp_name")

    def test_login_before_metadata_fetched(self):
        self._configure_testshib_provider(fetch_metadata=False)
        # The user goes to the login page, and sees a button to login with TestShib:
        testshib_login_url = self._check_login_page()
        # The user clicks on the TestShib button:
        try_login_response = self.client.get(testshib_login_url)
        # The user should be redirected to back to the login page:
        assert try_login_response.status_code == 302
        assert try_login_response["Location"] == self.login_page_url
        # When loading the login page, the user will see an error message:
        response = self.client.get(self.login_page_url)
        self.assertContains(response, "Authentication with TestShib is currently unavailable.")

    def test_login(self):
        """Configure TestShib before running the login test"""
        self._configure_testshib_provider()
        self._test_login()

    def test_register(self):
        """Configure TestShib before running the register test"""
        self._configure_testshib_provider()
        self._test_register()

    def test_login_records_attributes(self):
        """
        Test that attributes sent by a SAML provider are stored in the UserSocialAuth table.
        """
        self.test_login()
        record = UserSocialAuth.objects.get(
            user=self.user, provider=self.PROVIDER_BACKEND, uid__startswith=self.PROVIDER_IDP_SLUG
        )
        attributes = record.extra_data
        assert attributes.get("urn:oid:1.3.6.1.4.1.5923.1.1.1.9") == ["Member@testshib.org", "Staff@testshib.org"]
        assert attributes.get("urn:oid:2.5.4.3") == ["Me Myself And I"]
        assert attributes.get("urn:oid:0.9.2342.19200300.100.1.1") == ["myself"]
        assert attributes.get("urn:oid:2.5.4.20") == ["555-5555"]
        # Phone number

    @ddt.data(True, False)
    def test_debug_mode_login(self, debug_mode_enabled):
        """Test SAML login logs with debug mode enabled or not"""
        self._configure_testshib_provider(debug_mode=debug_mode_enabled)
        with patch.object(saml_log, "info") as mock_log:
            self._test_login()
        if debug_mode_enabled:
            # We expect that test_login() does two full logins, and each attempt generates two
            # logs - one for the request and one for the response
            assert mock_log.call_count == 4

            expected_next_url = "/dashboard"
            (msg, action_type, idp_name, request_data, next_url, xml), _kwargs = mock_log.call_args_list[0]
            assert msg.startswith("SAML login %s")
            assert action_type == "request"
            assert idp_name == self.PROVIDER_IDP_SLUG
            assert_dict_contains_subset(
                self,
                {"idp": idp_name, "auth_entry": "login", "next": expected_next_url},
                request_data,
            )
            assert next_url == expected_next_url
            assert "<samlp:AuthnRequest" in xml

            (msg, action_type, idp_name, response_data, next_url, xml), _kwargs = mock_log.call_args_list[1]
            assert msg.startswith("SAML login %s")
            assert action_type == "response"
            assert idp_name == self.PROVIDER_IDP_SLUG
            expected_relay_state = json.dumps({"idp": idp_name, "next": expected_next_url})  # Remove "auth_entry"
            assert_dict_contains_subset(self, {"RelayState": expected_relay_state}, response_data)
            assert "SAMLResponse" in response_data
            assert next_url == expected_next_url
            assert "<saml2p:Response" in xml
        else:
            assert not mock_log.called

    def test_configure_testshib_provider_with_cache_duration(self):
        """Enable and configure the TestShib SAML IdP as a third_party_auth provider"""
        kwargs = {}
        kwargs.setdefault("name", self.PROVIDER_NAME)
        kwargs.setdefault("enabled", True)
        kwargs.setdefault("visible", True)
        kwargs.setdefault("slug", self.PROVIDER_IDP_SLUG)
        kwargs.setdefault("entity_id", TESTSHIB_ENTITY_ID)
        kwargs.setdefault("metadata_source", TESTSHIB_METADATA_URL_WITH_CACHE_DURATION)
        kwargs.setdefault("icon_class", "fa-university")
        kwargs.setdefault("attr_email", "urn:oid:1.3.6.1.4.1.5923.1.1.1.6")  # eduPersonPrincipalName
        self.configure_saml_provider(**kwargs)
        assert httpretty.is_enabled()
        num_total, num_skipped, num_attempted, num_updated, num_failed, failure_messages = fetch_saml_metadata()
        assert num_total == 1
        assert num_skipped == 0
        assert num_attempted == 1
        assert num_updated == 1
        assert num_failed == 0
        assert len(failure_messages) == 0

    def test_login_with_testshib_provider_short_session_length(self):
        """
        Test that when we have a TPA provider which as an explicit maximum
        session length set, waiting for longer than that between requests
        results in us being logged out.
        """
        # Configure the provider with a 10-second timeout
        self._configure_testshib_provider(max_session_length=10)

        now = datetime.datetime.utcnow()
        with freeze_time(now):
            # Test the login flow, adding the user in the process
            self._test_login()

        # Wait 30 seconds; longer than the manually-set 10-second timeout
        later = now + datetime.timedelta(seconds=30)
        with freeze_time(later):
            # Test returning as a logged in user; this method verifies that we're logged out first.
            self._test_return_login(previous_session_timed_out=True)


@utils.skip_unless_thirdpartyauth()
class SuccessFactorsIntegrationTest(SamlIntegrationTestUtilities, IntegrationTestMixin, testutil.SAMLTestCase):
    """
    Test basic SAML capability using the TestShib details, and then check that we're able
    to make the proper calls using the SAP SuccessFactors API.
    """

    # Note that these details are different than those that will be provided by the SAML
    # assertion metadata. Rather, they will be fetched from the mocked SAPSuccessFactors API.
    USER_EMAIL = "john@smith.com"
    USER_NAME = "John Smith"
    USER_USERNAME = "John"

    def setUp(self):
        """
        Mock out HTTP calls to various endpoints using httpretty.
        """
        super().setUp()

        # Mock the call to the SAP SuccessFactors assertion endpoint
        SAPSF_ASSERTION_URL = "http://successfactors.com/oauth/idp"

        def assertion_callback(_request, _uri, headers):
            """
            Return a fake assertion after checking that the input is what we expect.
            """
            assert b"private_key=fake_private_key_here" in _request.body
            assert b"user_id=myself" in _request.body
            assert b"token_url=http%3A%2F%2Fsuccessfactors.com%2Foauth%2Ftoken" in _request.body
            assert b"client_id=TatVotSEiCMteSNWtSOnLanCtBGwNhGB" in _request.body
            return (200, headers, "fake_saml_assertion")

        httpretty.register_uri(httpretty.POST, SAPSF_ASSERTION_URL, content_type="text/plain", body=assertion_callback)

        SAPSF_BAD_ASSERTION_URL = "http://successfactors.com/oauth-fake/idp"

        def bad_callback(_request, _uri, headers):
            """
            Return a 404 error when someone tries to call the URL.
            """
            return (404, headers, "NOT AN ASSERTION")

        httpretty.register_uri(httpretty.POST, SAPSF_BAD_ASSERTION_URL, content_type="text/plain", body=bad_callback)

        # Mock the call to the SAP SuccessFactors token endpoint
        SAPSF_TOKEN_URL = "http://successfactors.com/oauth/token"

        def token_callback(_request, _uri, headers):
            """
            Return a fake assertion after checking that the input is what we expect.
            """
            assert b"assertion=fake_saml_assertion" in _request.body
            assert b"company_id=NCC1701D" in _request.body
            assert b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Asaml2-bearer" in _request.body
            assert b"client_id=TatVotSEiCMteSNWtSOnLanCtBGwNhGB" in _request.body
            return (200, headers, '{"access_token": "faketoken"}')

        httpretty.register_uri(httpretty.POST, SAPSF_TOKEN_URL, content_type="application/json", body=token_callback)

        # Mock the call to the SAP SuccessFactors OData user endpoint
        ODATA_USER_URL = (
            "http://api.successfactors.com/odata/v2/User(userId='myself')"
            "?$select=firstName,lastName,defaultFullName,email"
        )

        def user_callback(request, _uri, headers):
            auth_header = request.headers.get("Authorization")
            assert auth_header == "Bearer faketoken"
            return (
                200,
                headers,
                json.dumps(
                    {
                        "d": {
                            "username": "jsmith",
                            "firstName": "John",
                            "lastName": "Smith",
                            "defaultFullName": "John Smith",
                            "email": "john@smith.com",
                            "country": "United States",
                        }
                    }
                ),
            )

        httpretty.register_uri(httpretty.GET, ODATA_USER_URL, content_type="application/json", body=user_callback)

    def _mock_odata_api_for_error(self, odata_api_root_url, username):
        """
        Mock an error response when calling the OData API for user details.
        """

        def callback(request, uri, headers):  # pylint: disable=unused-argument
            """
            Return a 500 error when someone tries to call the URL.
            """
            headers["CorrelationId"] = "aefd38b7-c92c-445a-8c7a-487a3f0c7a9d"
            headers["RequestNo"] = "[787177]"  # This is the format SAPSF returns for the transaction request number
            return 500, headers, "Failure!"

        fields = ",".join(SapSuccessFactorsIdentityProvider.default_field_mapping.copy())
        url = "{root_url}User(userId='{user_id}')?$select={fields}".format(  # noqa: UP032
            root_url=odata_api_root_url,
            user_id=username,
            fields=fields,
        )
        httpretty.register_uri(httpretty.GET, url, body=callback, content_type="application/json")
        return url

    def test_register_insufficient_sapsf_metadata(self):
        """
        Configure the provider such that it doesn't have enough details to contact the SAP
        SuccessFactors API, and test that it falls back to the data it receives from the SAML assertion.
        """
        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings='{"key_i_dont_need":"value_i_also_dont_need"}',
        )
        # Because we're getting details from the assertion, fall back to the initial set of details.
        self.USER_EMAIL = "myself@testshib.org"
        self.USER_NAME = "Me Myself And I"
        self.USER_USERNAME = "myself"
        self._test_register()

    @patch.dict("django.conf.settings.REGISTRATION_EXTRA_FIELDS", country="optional")
    def test_register_sapsf_metadata_present(self):
        """
        Configure the provider such that it can talk to a mocked-out version of the SAP SuccessFactors
        API, and ensure that the data it gets that way gets passed to the registration form.

        Check that value mappings overrides work in cases where we override a value other than
        what we're looking for, and when an empty override is provided (expected behavior is that
        existing value maps will be left alone).
        """
        expected_country = "US"
        provider_settings = {
            "sapsf_oauth_root_url": "http://successfactors.com/oauth/",
            "sapsf_private_key": "fake_private_key_here",
            "odata_api_root_url": "http://api.successfactors.com/odata/v2/",
            "odata_company_id": "NCC1701D",
            "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
        }

        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(provider_settings),
        )
        self._test_register(country=expected_country)

    def test_register_sapsf_with_value_default(self):
        """
        Configure the provider such that it can talk to a mocked-out version of the SAP SuccessFactors
        API, and ensure that the data it gets that way gets passed to the registration form.

        Check that value mappings overrides work in cases where we override a value other than
        what we're looking for, and when an empty override is provided it should use the default value
        provided by the configuration.
        """
        # Mock the call to the SAP SuccessFactors OData user endpoint
        ODATA_USER_URL = (
            "http://api.successfactors.com/odata/v2/User(userId='myself')"
            "?$select=firstName,country,lastName,defaultFullName,email"
        )

        def user_callback(request, _uri, headers):
            auth_header = request.headers.get("Authorization")
            assert auth_header == "Bearer faketoken"
            return (
                200,
                headers,
                json.dumps(
                    {
                        "d": {
                            "username": "jsmith",
                            "firstName": "John",
                            "lastName": "Smith",
                            "defaultFullName": "John Smith",
                            "country": "United States",
                        }
                    }
                ),
            )

        httpretty.register_uri(httpretty.GET, ODATA_USER_URL, content_type="application/json", body=user_callback)

        provider_settings = {
            "sapsf_oauth_root_url": "http://successfactors.com/oauth/",
            "sapsf_private_key": "fake_private_key_here",
            "odata_api_root_url": "http://api.successfactors.com/odata/v2/",
            "odata_company_id": "NCC1701D",
            "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
        }

        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(provider_settings),
            default_email="default@testshib.org",
        )
        self.USER_EMAIL = "default@testshib.org"
        self._test_register()

    @patch.dict("django.conf.settings.REGISTRATION_EXTRA_FIELDS", country="optional")
    def test_register_sapsf_metadata_present_override_relevant_value(self):
        """
        Configure the provider such that it can talk to a mocked-out version of the SAP SuccessFactors
        API, and ensure that the data it gets that way gets passed to the registration form.

        Check that value mappings overrides work in cases where we override a value other than
        what we're looking for, and when an empty override is provided (expected behavior is that
        existing value maps will be left alone).
        """
        value_map = {"country": {"United States": "NZ"}}
        expected_country = "NZ"
        provider_settings = {
            "sapsf_oauth_root_url": "http://successfactors.com/oauth/",
            "sapsf_private_key": "fake_private_key_here",
            "odata_api_root_url": "http://api.successfactors.com/odata/v2/",
            "odata_company_id": "NCC1701D",
            "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
        }
        if value_map:
            provider_settings["sapsf_value_mappings"] = value_map

        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(provider_settings),
        )
        self._test_register(country=expected_country)

    @patch.dict("django.conf.settings.REGISTRATION_EXTRA_FIELDS", country="optional")
    def test_register_sapsf_metadata_present_override_other_value(self):
        """
        Configure the provider such that it can talk to a mocked-out version of the SAP SuccessFactors
        API, and ensure that the data it gets that way gets passed to the registration form.

        Check that value mappings overrides work in cases where we override a value other than
        what we're looking for, and when an empty override is provided (expected behavior is that
        existing value maps will be left alone).
        """
        value_map = {"country": {"Australia": "blahfake"}}
        expected_country = "US"
        provider_settings = {
            "sapsf_oauth_root_url": "http://successfactors.com/oauth/",
            "sapsf_private_key": "fake_private_key_here",
            "odata_api_root_url": "http://api.successfactors.com/odata/v2/",
            "odata_company_id": "NCC1701D",
            "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
        }
        if value_map:
            provider_settings["sapsf_value_mappings"] = value_map

        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(provider_settings),
        )
        self._test_register(country=expected_country)

    @patch.dict("django.conf.settings.REGISTRATION_EXTRA_FIELDS", country="optional")
    def test_register_sapsf_metadata_present_empty_value_override(self):
        """
        Configure the provider such that it can talk to a mocked-out version of the SAP SuccessFactors
        API, and ensure that the data it gets that way gets passed to the registration form.

        Check that value mappings overrides work in cases where we override a value other than
        what we're looking for, and when an empty override is provided (expected behavior is that
        existing value maps will be left alone).
        """

        value_map = {"country": {}}
        expected_country = "US"
        provider_settings = {
            "sapsf_oauth_root_url": "http://successfactors.com/oauth/",
            "sapsf_private_key": "fake_private_key_here",
            "odata_api_root_url": "http://api.successfactors.com/odata/v2/",
            "odata_company_id": "NCC1701D",
            "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
        }
        if value_map:
            provider_settings["sapsf_value_mappings"] = value_map

        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(provider_settings),
        )
        self._test_register(country=expected_country)

    def test_register_http_failure(self):
        """
        Ensure that if there's an HTTP failure while fetching metadata, we continue, using the
        metadata from the SAML assertion.
        """
        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(
                {
                    "sapsf_oauth_root_url": "http://successfactors.com/oauth-fake/",
                    "sapsf_private_key": "fake_private_key_here",
                    "odata_api_root_url": "http://api.successfactors.com/odata/v2/",
                    "odata_company_id": "NCC1701D",
                    "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
                }
            ),
        )
        # Because we're getting details from the assertion, fall back to the initial set of details.
        self.USER_EMAIL = "myself@testshib.org"
        self.USER_NAME = "Me Myself And I"
        self.USER_USERNAME = "myself"
        self._test_register()

    def test_register_http_failure_in_odata(self):
        """
        Ensure that if there's an HTTP failure while fetching user details from
        SAP SuccessFactors OData API.
        """
        # Because we're getting details from the assertion, fall back to the initial set of details.
        self.USER_EMAIL = "myself@testshib.org"
        self.USER_NAME = "Me Myself And I"
        self.USER_USERNAME = "myself"

        odata_company_id = "NCC1701D"
        odata_api_root_url = "http://api.successfactors.com/odata/v2/"
        mocked_odata_api_url = self._mock_odata_api_for_error(odata_api_root_url, self.USER_USERNAME)
        self._configure_testshib_provider(
            identity_provider_type="sap_success_factors",
            metadata_source=TESTSHIB_METADATA_URL,
            other_settings=json.dumps(
                {
                    "sapsf_oauth_root_url": "http://successfactors.com/oauth/",
                    "sapsf_private_key": "fake_private_key_here",
                    "odata_api_root_url": odata_api_root_url,
                    "odata_company_id": odata_company_id,
                    "odata_client_id": "TatVotSEiCMteSNWtSOnLanCtBGwNhGB",
                }
            ),
        )
        with LogCapture(level=logging.WARNING) as log_capture:
            self._test_register()
            logging_messages = str([log_msg.getMessage() for log_msg in log_capture.records]).replace("\\", "")
            assert odata_company_id in logging_messages
            assert mocked_odata_api_url in logging_messages
            assert self.USER_USERNAME in logging_messages
            assert "SAPSuccessFactors" in logging_messages
            assert "Error message" in logging_messages
            assert "System message" in logging_messages
            assert "Headers" in logging_messages

    @skip("Test not necessary for this subclass")
    def test_get_saml_idp_class_with_fake_identifier(self):
        pass

    @skip("Test not necessary for this subclass")
    def test_login(self):
        pass

    @skip("Test not necessary for this subclass")
    def test_register(self):
        pass
