""" Test User Authentication utilities """


from collections import namedtuple
from urllib.parse import urlencode  # pylint: disable=import-error

import ddt
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings

from openedx.core.djangoapps.oauth_dispatch.tests.factories import ApplicationFactory
from openedx.core.djangoapps.user_authn.utils import (
    generate_username_suggestions,
    is_safe_login_or_logout_redirect,
    remove_special_characters_from_name
)


@ddt.ddt
class TestRedirectUtils(TestCase):
    """Test redirect utility methods."""

    def setUp(self):
        super().setUp()
        self.request = RequestFactory()

    RedirectCase = namedtuple('RedirectCase', ['url', 'host', 'req_is_secure', 'expected_is_safe'])

    @staticmethod
    def _is_safe_redirect(req, url):
        return is_safe_login_or_logout_redirect(
            redirect_to=url,
            request_host=req.get_host(),
            dot_client_id=req.GET.get('client_id'),
            require_https=req.is_secure(),
        )

    @ddt.data(
        RedirectCase('/dashboard', 'testserver', req_is_secure=True, expected_is_safe=True),
        RedirectCase('https://test.edx.org/courses', 'edx.org', req_is_secure=True, expected_is_safe=True),
        RedirectCase('https://www.amazon.org', 'edx.org', req_is_secure=True, expected_is_safe=False),

        # https is required only if the request is_secure
        RedirectCase('https://edx.org/courses', 'edx.org', req_is_secure=True, expected_is_safe=True),
        RedirectCase('http://edx.org/courses', 'edx.org', req_is_secure=False, expected_is_safe=True),
        RedirectCase('http://edx.org/courses', 'edx.org', req_is_secure=True, expected_is_safe=False),

        # Django's url_has_allowed_host_and_scheme protects against "///"
        RedirectCase('http:///edx.org/courses', 'edx.org', req_is_secure=True, expected_is_safe=False),
    )
    @ddt.unpack
    @override_settings(LOGIN_REDIRECT_WHITELIST=['test.edx.org'])
    def test_safe_redirect(self, url, host, req_is_secure, expected_is_safe):
        """ Test safe next parameter """
        req = self.request.get('/login', HTTP_HOST=host)
        req.is_secure = lambda: req_is_secure
        actual_is_safe = self._is_safe_redirect(req, url)
        assert actual_is_safe == expected_is_safe

    @ddt.data(
        ('https://test.com/test', 'https://test.com/test', 'edx.org', True),
        ('https://test.com/test', 'https://fake.com', 'edx.org', False),
    )
    @ddt.unpack
    def test_safe_redirect_oauth2(self, client_redirect_uri, redirect_url, host, expected_is_safe):
        """ Test safe redirect_url parameter when logging out OAuth2 client. """
        application = ApplicationFactory(redirect_uris=client_redirect_uri)
        params = {
            'client_id': application.client_id,
            'redirect_url': redirect_url,
        }
        req = self.request.get(f'/logout?{urlencode(params)}', HTTP_HOST=host)
        actual_is_safe = self._is_safe_redirect(req, redirect_url)
        assert actual_is_safe == expected_is_safe


@ddt.ddt
class TestUsernameGeneration(TestCase):
    """Test username generation utility methods."""

    def test_remove_special_characters(self):
        """Test the removal of special characters from a name."""
        test_cases = [
            ('John Doe', 'JohnDoe'),
            ('John@Doe', 'JohnDoe'),
            ('John.Doe', 'JohnDoe'),
            ('John_Doe', 'John_Doe'),  # Underscore is allowed
            ('John-Doe', 'John-Doe'),  # Hyphen is allowed
            ('John$#@!Doe', 'JohnDoe'),
        ]
        for input_name, expected in test_cases:
            assert remove_special_characters_from_name(input_name) == expected

    @ddt.data(
        # Test normal ASCII name
        ('John Doe', True),  # Should return suggestions
        ('Jane Smith', True),  # Should return suggestions
        # Test non-ASCII names
        ('José García', False),  # Contains non-ASCII characters
        ('مریم میرزاخانی', False),  # Persian name
        ('明美 田中', False),  # Japanese name
        ('Σωκράτης', False),  # Greek name
        ('Владимир', False),  # Cyrillic characters
        # Test edge cases
        ('A B', True),  # Minimal valid name
        ('', True),  # Empty string
        ('   ', True),  # Just spaces
    )
    @ddt.unpack
    def test_username_suggestions_ascii_check(self, name, should_generate):
        """Test username suggestion generation for ASCII and non-ASCII names."""
        suggestions = generate_username_suggestions(name)

        if should_generate:
            if name.strip():  # If name is not empty or just spaces
                # Should generate up to 3 suggestions for valid ASCII names
                assert len(suggestions) <= 3
                # Verify all suggestions are ASCII
                for suggestion in suggestions:
                    assert suggestion.isascii()
                    assert suggestion.replace('_', '').replace('-', '').isalnum()
            else:
                # Empty or whitespace-only names should return empty list
                assert not suggestions
        else:
            # Should return empty list for non-ASCII names
            assert not suggestions

    def test_unique_suggestions(self):
        """Test that generated suggestions are unique."""
        name = "John Doe"
        suggestions = generate_username_suggestions(name)
        assert len(suggestions) == len(set(suggestions)), "All suggestions should be unique"

    def test_suggestion_length(self):
        """Test that generated suggestions respect the maximum length."""
        from openedx.core.djangoapps.user_api.accounts import USERNAME_MAX_LENGTH

        # Test with a very long name
        long_name = "John" * 50
        suggestions = generate_username_suggestions(long_name)

        for suggestion in suggestions:
            assert len(suggestion) <= USERNAME_MAX_LENGTH
