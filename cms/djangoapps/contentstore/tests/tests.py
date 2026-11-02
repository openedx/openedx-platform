"""
This test file will test registration, login, activation, and session activity timeouts

TODO: Rewrite several of these assertions so that they check the output of the REST or Python
APIs rather than parsing HTML from the deprecated legacy frontend pages. In particular, any
test case using override_waffle_flag(toggles.LEGACY_STUDIO_*, True) will need to be fixed.
Part of https://github.com/openedx/edx-platform/issues/36275.
"""


import datetime
import time
from unittest import mock
from urllib.parse import quote_plus, unquote

from ddt import data, ddt, unpack
from django.conf import settings
from django.core.cache import cache
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from pytz import UTC

from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.tests.test_course_settings import CourseTestCase
from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient, parse_json, registration, user
from cms.djangoapps.contentstore.utils import get_studio_home_url
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class ContentStoreTestCase(ModuleStoreTestCase):
    """Test class to verify user account operations"""

    def _login(self, email, password):
        """
        Login.  View should always return 200.  The success/fail is in the
        returned json
        """
        resp = self.client.post(
            reverse('user_api_login_session', kwargs={'api_version': 'v1'}),
            {'email': email, 'password': password}
        )
        return resp

    def login(self, email, password):
        """Login, check that it worked."""
        resp = self._login(email, password)
        self.assertEqual(resp.status_code, 200)
        return resp

    def _create_account(self, username, email, password):
        """Try to create an account.  No error checking"""
        registration_url = reverse('user_api_registration')
        resp = self.client.post(registration_url, {
            'username': username,
            'email': email,
            'password': password,
            'location': 'home',
            'language': 'Franglish',
            'name': 'Fred Weasley',
            'terms_of_service': 'true',
            'honor_code': 'true',
        })
        return resp

    def create_account(self, username, email, password):
        """Create the account and check that it worked"""
        resp = self._create_account(username, email, password)
        self.assertEqual(resp.status_code, 200)
        json_data = parse_json(resp)
        self.assertEqual(json_data['success'], True)

        # Check both that the user is created, and inactive
        self.assertFalse(user(email).is_active)

        return resp

    def _activate_user(self, email):
        """Look up the activation key for the user, then hit the activate view.
        No error checking"""
        activation_key = registration(email).activation_key

        # and now we try to activate
        resp = self.client.get(reverse('activate', kwargs={'key': activation_key}))
        return resp

    def activate_user(self, email):
        resp = self._activate_user(email)
        self.assertEqual(resp.status_code, 200)
        # Now make sure that the user is now actually activated
        self.assertTrue(user(email).is_active)


@ddt
# HIBP settings are only defined in lms envs but needed for cms auth related tests.
@override_settings(
    ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY=False,
    ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY=False,
    ENABLE_AUTHN_REGISTER_HIBP_POLICY=False,
)
class AuthTestCase(ContentStoreTestCase):
    """Check that various permissions-related things work"""

    CREATE_USER = False
    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']

    def setUp(self):
        super().setUp()

        self.email = 'a@b.com'
        self.pw = 'password1234'
        self.username = 'testuser'
        self.client = AjaxEnabledTestClient()
        # clear the cache so ratelimiting won't affect these tests
        cache.clear()

    def test_private_pages_auth(self):
        """Make sure pages that do require login work."""
        auth_pages = (
            '/home/',
        )

        # These are pages that should just load when the user is logged in
        # (no data needed)
        simple_auth_pages = (
            '/home/',
        )

        # need an activated user
        self.create_account(self.username, self.email, self.pw)
        self.activate_user(self.email)

        # Create a new session
        self.client = AjaxEnabledTestClient()

        # Not logged in.  Should redirect to login.
        print('Not logged in')
        for page in auth_pages:
            print(f"Checking '{page}'")
            resp = self.client.get_html(page)
            assert resp.status_code == 302
            assert resp.url == unquote(reverse("login", query={"next": page}))

        # Logged in should work.
        self.login(self.email, self.pw)

        print('Logged in')
        for page in simple_auth_pages:
            print(f"Checking '{page}'")
            resp = self.client.get_html(page)
            assert resp.status_code == 302
            assert resp.url == get_studio_home_url()

    @override_settings(SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=1)
    def test_inactive_session_timeout(self):
        """
        Verify that an inactive session times out and redirects to the
        login page
        """
        self.create_account(self.username, self.email, self.pw)
        self.activate_user(self.email)

        self.login(self.email, self.pw)

        # make sure we can access courseware immediately
        course_url = '/home/'
        resp = self.client.get_html(course_url)
        assert resp.status_code == 302
        assert resp.url == get_studio_home_url()

        # then wait a bit and see if we get timed out
        time.sleep(2)

        resp = self.client.get_html(course_url)

        # re-request, and we should get a redirect to login page
        self.assertRedirects(resp, settings.LOGIN_URL + '?next=/home/', target_status_code=302)

    @data(
        (True, 'assertContains'),
        (False, 'assertNotContains'))
    @unpack
    @override_waffle_flag(toggles.LEGACY_STUDIO_LOGGED_OUT_HOME, True)
    def test_signin_and_signup_buttons_index_page(self, allow_account_creation, assertion_method_name):
        """
        Navigate to the home page and check the Sign Up button is hidden when ALLOW_PUBLIC_ACCOUNT_CREATION flag
        is turned off, and not when it is turned on.  The Sign In button should always appear.
        """
        with mock.patch.dict(settings.FEATURES, {"ALLOW_PUBLIC_ACCOUNT_CREATION": allow_account_creation}):
            response = self.client.get(reverse('homepage'))
            assertion_method = getattr(self, assertion_method_name)
            login_url = quote_plus(f"http://testserver{settings.LOGIN_URL}")
            assertion_method(
                response,
                f'<a class="action action-signup" href="{settings.LMS_ROOT_URL}/register'
                f'?next={login_url}">Sign Up</a>'
            )
            self.assertContains(
                response,
                '<a class="action action-signin" href="/login/?next=http%3A%2F%2Ftestserver%2F">'
                'Sign In</a>'
            )


class ForumTestCase(CourseTestCase):
    """Tests class to verify course to forum operations"""

    def setUp(self):
        """ Creates the test course. """
        super().setUp()
        self.course = CourseFactory.create(org='testX', number='727', display_name='Forum Course')

    def set_blackout_dates(self, blackout_dates):
        """Helper method to set blackout dates in course."""
        self.course.discussion_blackouts = [
            [start_date.isoformat(), end_date.isoformat()] for start_date, end_date in blackout_dates
        ]

    def test_blackouts(self):
        now = datetime.datetime.now(UTC)
        times1 = [
            (now - datetime.timedelta(days=14), now - datetime.timedelta(days=11)),
            (now + datetime.timedelta(days=24), now + datetime.timedelta(days=30))
        ]
        self.set_blackout_dates(times1)
        self.assertTrue(self.course.forum_posts_allowed)
        times2 = [
            (now - datetime.timedelta(days=14), now + datetime.timedelta(days=2)),
            (now + datetime.timedelta(days=24), now + datetime.timedelta(days=30))
        ]
        self.set_blackout_dates(times2)
        self.assertFalse(self.course.forum_posts_allowed)

        # Single date set for allowed forum posts.
        self.course.discussion_blackouts = [
            now + datetime.timedelta(days=24),
            now + datetime.timedelta(days=30)
        ]
        self.assertTrue(self.course.forum_posts_allowed)

        # Single date set for restricted forum posts.
        self.course.discussion_blackouts = [
            now - datetime.timedelta(days=24),
            now + datetime.timedelta(days=30)
        ]
        self.assertFalse(self.course.forum_posts_allowed)

        # test if user gives empty blackout date it should return true for forum_posts_allowed
        self.course.discussion_blackouts = [[]]
        self.assertTrue(self.course.forum_posts_allowed)


@ddt
class CourseKeyVerificationTestCase(CourseTestCase):
    """Test class to verify course decorator operations"""

    def setUp(self):
        """
        Create test course.
        """
        super().setUp()
        self.course = CourseFactory.create(org='edX', number='test_course_key', display_name='Test Course')

    @data(('edX/test_course_key/Test_Course', 200), ('garbage:edX+test_course_key+Test_Course', 404))
    @unpack
    @override_waffle_flag(toggles.LEGACY_STUDIO_IMPORT, True)
    def test_course_key_decorator(self, course_key, status_code):
        """
        Tests for the ensure_valid_course_key decorator.
        """
        url = f'/import/{course_key}'
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, status_code)

        url = '/import_status/{course_key}/{filename}'.format(
            course_key=course_key,
            filename='xyz.tar.gz'
        )
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, status_code)
