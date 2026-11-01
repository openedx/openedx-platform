"""Tests for the lms module itself."""


import logging
import mimetypes
from unittest import mock

from django.conf import settings  # pylint: disable=unused-import  # noqa: F401
from django.core.cache import cache
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import Resolver404, URLResolver, path, resolve, reverse
from django.urls.resolvers import RegexPattern
from drf_spectacular.generators import SchemaGenerator
from edx_django_utils.plugins import get_plugin_url_patterns

import lms.urls
from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from openedx.core.apidocs import cached_schema_view
from openedx.core.djangoapps.plugins.constants import ProjectType
from openedx.core.djangoapps.user_api.v2.views import AccountViewSet as CurrentAccountViewSet
from openedx.core.djangoapps.user_api.v2.views import UnknownAddressView
from openedx.core.djangoapps.user_authn.views.login import LoginSessionView
from openedx.core.djangoapps.user_authn.views.register import RegistrationView

log = logging.getLogger(__name__)


class LmsModuleTests(TestCase):
    """
    Tests for lms module itself.
    """

    def test_new_mimetypes(self):
        extensions = ['eot', 'otf', 'ttf', 'woff']
        for extension in extensions:
            mimetype, _ = mimetypes.guess_type('test.' + extension)
            assert mimetype is not None

    def test_api_docs(self):
        """
        Tests that requests to the `/api-docs/` endpoint do not raise an exception.
        """
        response = self.client.get('/api-docs/')
        assert response.status_code == 200

    def test_api_docs_schema(self):
        """
        Tests that the OpenAPI schema generates without raising an exception.

        The `/api-docs/` view above only renders the Swagger UI shell, so this
        is what actually exercises schema generation across the whole service.
        """
        response = self.client.get('/api-docs/schema/')
        assert response.status_code == 200

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=60,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_api_docs_schema_cache(self):
        """
        The schema cache is keyed on method and language, so neither an OPTIONS
        response nor another language's document is served to a later GET.

        The suite runs with OPENAPI_CACHE_TIMEOUT = 0 and a DummyCache, so the
        cached branch is only reachable with both overridden.
        """
        cache.clear()
        options = self.client.options('/api-docs/schema/')
        spanish = self.client.get('/api-docs/schema/', HTTP_ACCEPT_LANGUAGE='es-419')
        english = self.client.get('/api-docs/schema/', HTTP_ACCEPT_LANGUAGE='en')
        cached = self.client.get('/api-docs/schema/', HTTP_ACCEPT_LANGUAGE='en')
        assert options.status_code == 200
        assert english.status_code == 200
        assert english.content.startswith(b'openapi:')
        assert english.content != spanish.content
        assert english.content == cached.content

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=60,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_api_docs_schema_cache_per_urlconf(self):
        """
        The key covers the URLconf: LMS and CMS serve this at the same path and
        share one cache, so a document generated under one must not come back
        under the other.
        """
        cache.clear()
        full = self.client.get('/api-docs/schema/')
        with override_settings(ROOT_URLCONF='lms.tests'):
            other = self.client.get('/api-docs/schema/')
        again = self.client.get('/api-docs/schema/')
        assert full.status_code == 200
        assert other.status_code == 200
        assert full.content != other.content
        assert again.content == full.content


class NarrowSchemaCacheTests(TestCase):
    """
    The document this service publishes for client generation is served from the cache.

    It is public, it costs a full generation pass, and it is the one schema
    address with nothing in front of it. The cache may only serve it if every
    caller is given the same document, so that is measured here too.
    """

    ADDRESS = '/lms-api/schema/'

    def _generations(self):
        """Count the passes the generator makes, leaving it otherwise alone."""
        return mock.patch.object(
            SchemaGenerator,
            'get_schema',
            autospec=True,
            side_effect=SchemaGenerator.get_schema,
        )

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=60,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_two_requests_generate_the_document_once(self):
        cache.clear()
        with self._generations() as generated:
            first = self.client.get(self.ADDRESS)
            second = self.client.get(self.ADDRESS)
        assert first.status_code == 200
        assert first.content.startswith(b'openapi:')
        assert second.content == first.content
        assert second['Content-Type'] == first['Content-Type']
        assert generated.call_count == 1

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=0,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_without_a_timeout_each_request_generates_it_again(self):
        """A deployment that turns the cache off is served exactly what it was before."""
        cache.clear()
        with self._generations() as generated:
            first = self.client.get(self.ADDRESS)
            second = self.client.get(self.ADDRESS)
        assert first.status_code == 200
        assert second.content == first.content
        assert generated.call_count == 2

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=60,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_the_two_documents_this_service_serves_are_not_stored_under_one_key(self):
        cache.clear()
        narrow = self.client.get(self.ADDRESS)
        wide = self.client.get('/api-docs/schema/')
        assert narrow.status_code == 200
        assert wide.status_code == 200
        assert len(narrow.content) < len(wide.content)
        assert self.client.get(self.ADDRESS).content == narrow.content
        assert self.client.get('/api-docs/schema/').content == wide.content

    @override_settings(
        OPENAPI_CACHE_TIMEOUT=60,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_another_language_is_not_served_the_document_already_stored(self):
        cache.clear()
        english = self.client.get(self.ADDRESS, HTTP_ACCEPT_LANGUAGE='en')
        spanish = self.client.get(self.ADDRESS, HTTP_ACCEPT_LANGUAGE='es-419')
        assert english.status_code == 200
        assert spanish.status_code == 200
        assert english.content != spanish.content
        assert self.client.get(self.ADDRESS, HTTP_ACCEPT_LANGUAGE='en').content == english.content

    def test_every_caller_is_given_the_same_document(self):
        """What the cache may hold: one document, not one per caller."""
        staff = UserFactory.create(username='narrowstaff', password=TEST_PASSWORD, is_staff=True)
        root = UserFactory.create(
            username='narrowroot', password=TEST_PASSWORD, is_staff=True, is_superuser=True,
        )
        anonymous = self.client.get(self.ADDRESS)
        served = {'anonymous': anonymous.content}
        for user in (staff, root):
            assert self.client.login(username=user.username, password=TEST_PASSWORD)
            served[user.username] = self.client.get(self.ADDRESS).content
            self.client.logout()
        assert anonymous.status_code == 200
        assert set(served.values()) == {anonymous.content}


def _calendars_of_an_application(request):  # pylint: disable=unused-argument
    """Stand-in for an address an installed application serves under a claimed prefix."""
    return HttpResponse()


class ClaimedPrefixTests(TestCase):
    """
    A prefix whose mount answers every address under it that its own routes do not.

    The current version of the user API is mounted that way, so nothing after
    it is ever consulted for an address under that prefix. An installed
    application contributes its patterns at one point in the URLconf, which
    leaves the order between the two as what decides whether an address such an
    application registers under the prefix can be served at all.

    The authentication application registers two addresses there, so that order
    is measurable against real patterns rather than asserted about a copy.
    """

    LANDMARK = "api/user/v2/account/registration/"

    def _index_of(self, route):
        """Where in the URLconf the pattern that answers that address sits."""
        for index, pattern in enumerate(lms.urls.urlpatterns):
            try:
                if pattern.resolve(route):
                    return index
            except Resolver404:
                continue
        raise AssertionError(f"nothing in the URLconf answers {route}")

    def test_the_landmark_address_is_one_an_installed_application_contributes(self):
        """The order below says nothing unless an application really registers an address here."""
        contributed = URLResolver(RegexPattern(r"^/"), get_plugin_url_patterns(ProjectType.LMS))
        assert contributed.resolve(f"/{self.LANDMARK}").func.cls is RegistrationView

    def test_the_mount_that_claims_the_prefix_is_consulted_after_those_applications(self):
        claimed = self._index_of("api/user/v2/nothing_here/")
        assert claimed > self._index_of(self.LANDMARK)

    def test_an_address_an_application_registers_under_the_prefix_reaches_its_view(self):
        patterns = list(lms.urls.urlpatterns)
        patterns.insert(
            self._index_of(self.LANDMARK) + 1,
            path("api/user/v2/calendars/", _calendars_of_an_application),
        )
        resolved = URLResolver(RegexPattern(r"^/"), patterns).resolve("/api/user/v2/calendars/")
        assert resolved.func is _calendars_of_an_application, resolved.func

    def test_an_address_nothing_registers_is_still_answered_by_the_mount(self):
        assert resolve("/api/user/v2/nothing_here/").func.cls is UnknownAddressView

    def test_the_version_serves_its_own_resources(self):
        assert resolve("/api/user/v2/accounts/").func.cls is CurrentAccountViewSet

    def test_the_addresses_another_application_serves_under_the_prefix_still_reach_it(self):
        reached = {
            address: resolve(address).func.cls
            for address in (
                "/api/user/v2/account/registration/",
                "/api/user/v2/account/login_session/",
            )
        }
        assert reached == {
            "/api/user/v2/account/registration/": RegistrationView,
            "/api/user/v2/account/login_session/": LoginSessionView,
        }

    def test_those_addresses_keep_the_names_they_are_reversed_by(self):
        reversed_names = {
            "user_api_registration_v2": reverse("user_api_registration_v2"),
            "user_api_login_session": reverse("user_api_login_session", kwargs={"api_version": "v2"}),
        }
        assert reversed_names == {
            "user_api_registration_v2": "/api/user/v2/account/registration/",
            "user_api_login_session": "/api/user/v2/account/login_session/",
        }

    def test_one_of_them_sent_without_its_trailing_slash_is_still_redirected_to_it(self):
        response = self.client.get("/api/user/v2/account/registration")
        assert response.status_code == 301
        assert response["Location"] == "/api/user/v2/account/registration/"


urlpatterns = [path('api-docs/schema/', cached_schema_view())]
