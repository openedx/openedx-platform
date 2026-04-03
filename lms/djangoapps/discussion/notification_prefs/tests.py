# pylint: disable=consider-iterating-dictionary, missing-module-docstring
import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.discussion.notification_prefs import NOTIFICATION_PREF_KEY
from lms.djangoapps.discussion.notification_prefs.views import (
    UsernameCipher,
    ajax_disable,
    ajax_enable,
    ajax_status,
    set_subscription,
)
from openedx.core.djangoapps.user_api.models import UserPreference


@override_settings(SECRET_KEY="test secret key")
class NotificationPrefViewTest(UrlResetMixin, TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    INITIALIZATION_VECTOR = b"\x00" * 16

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_FORUM_DAILY_DIGEST": True})
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username="testuser")
        # Tokens are intentionally hard-coded instead of computed to help us
        # avoid breaking existing links.
        self.tokens = {
            self.user: "AAAAAAAAAAAAAAAAAAAAAA8mMQo96FZfb1YKv1R5X6s=",
            # Username with length equal to AES block length to test padding
            UserFactory.create(username="sixteencharsuser"):
            "AAAAAAAAAAAAAAAAAAAAAPxPWCuI2Ay9TATBVnfw7eIj-hUh6erQ_-VkbDqHqm8D",
            # Even longer username
            UserFactory.create(username="thisusernameissoveryverylong"):
            "AAAAAAAAAAAAAAAAAAAAAPECbYqPI7_W4mRF8LbTaHuHt3tNXPggZ1Bke-zDyEiZ",
            # Non-ASCII username
            UserFactory.create(username="\u4e2d\u56fd"):
            "AAAAAAAAAAAAAAAAAAAAAMjfGAhZKIZsI3L-Z7nflTA="
        }
        self.request_factory = RequestFactory()

    def create_prefs(self):
        """Create all test preferences in the database"""
        for (user, token) in self.tokens.items():
            UserPreference.objects.get_or_create(user=user, key=NOTIFICATION_PREF_KEY, value=token)

    def assertPrefValid(self, user):
        """Ensure that the correct preference for the user is persisted"""
        pref = UserPreference.objects.get(user=user, key=NOTIFICATION_PREF_KEY)
        assert pref
        # check exists and only 1 (.get)
        # now coerce username to utf-8 encoded str, since we test with non-ascii unicdoe above and
        # the unittest framework has hard time coercing to unicode.
        # decrypt also can't take a unicode input, so coerce its input to str
        assert bytes(user.username.encode('utf-8')) == UsernameCipher().decrypt(str(pref.value))

    def assertNotPrefExists(self, user):
        """Ensure that the user does not have a persisted preference"""
        assert not UserPreference.objects.filter(user=user, key=NOTIFICATION_PREF_KEY).exists()

    # AJAX status view

    def test_ajax_status_get_0(self):
        request = self.request_factory.get("dummy")
        request.user = self.user
        response = ajax_status(request)
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8')) == {'status': 0}

    def test_ajax_status_get_1(self):
        self.create_prefs()
        request = self.request_factory.get("dummy")
        request.user = self.user
        response = ajax_status(request)
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8')) == {'status': 1}

    def test_ajax_status_post(self):
        request = self.request_factory.post("dummy")
        request.user = self.user
        response = ajax_status(request)
        assert response.status_code == 405

    def test_ajax_status_anon_user(self):
        request = self.request_factory.get("dummy")
        request.user = AnonymousUser()
        self.assertRaises(PermissionDenied, ajax_status, request)

    # AJAX enable view

    def test_ajax_enable_get(self):
        request = self.request_factory.get("dummy")
        request.user = self.user
        response = ajax_enable(request)
        assert response.status_code == 405
        self.assertNotPrefExists(self.user)

    def test_ajax_enable_anon_user(self):
        request = self.request_factory.post("dummy")
        request.user = AnonymousUser()
        self.assertRaises(PermissionDenied, ajax_enable, request)
        self.assertNotPrefExists(self.user)

    @patch("os.urandom")
    def test_ajax_enable_success(self, mock_urandom):
        mock_urandom.return_value = self.INITIALIZATION_VECTOR

        def test_user(user):
            request = self.request_factory.post("dummy")
            request.user = user
            response = ajax_enable(request)
            assert response.status_code == 204
            self.assertPrefValid(user)

        for user in self.tokens.keys():
            test_user(user)

    def test_ajax_enable_already_enabled(self):
        self.create_prefs()
        request = self.request_factory.post("dummy")
        request.user = self.user
        response = ajax_enable(request)
        assert response.status_code == 204
        self.assertPrefValid(self.user)

    def test_ajax_enable_distinct_values(self):
        request = self.request_factory.post("dummy")
        request.user = self.user
        ajax_enable(request)
        other_user = UserFactory.create()
        request.user = other_user
        ajax_enable(request)
        assert UserPreference.objects.get(
            user=self.user, key=NOTIFICATION_PREF_KEY
        ).value != UserPreference.objects.get(
            user=other_user, key=NOTIFICATION_PREF_KEY
        ).value

    # AJAX disable view

    def test_ajax_disable_get(self):
        self.create_prefs()
        request = self.request_factory.get("dummy")
        request.user = self.user
        response = ajax_disable(request)
        assert response.status_code == 405
        self.assertPrefValid(self.user)

    def test_ajax_disable_anon_user(self):
        self.create_prefs()
        request = self.request_factory.post("dummy")
        request.user = AnonymousUser()
        self.assertRaises(PermissionDenied, ajax_disable, request)
        self.assertPrefValid(self.user)

    def test_ajax_disable_success(self):
        self.create_prefs()
        request = self.request_factory.post("dummy")
        request.user = self.user
        response = ajax_disable(request)
        assert response.status_code == 204
        self.assertNotPrefExists(self.user)

    def test_ajax_disable_already_disabled(self):
        request = self.request_factory.post("dummy")
        request.user = self.user
        response = ajax_disable(request)
        assert response.status_code == 204
        self.assertNotPrefExists(self.user)

    # Unsubscribe view

    def test_unsubscribe_post(self):
        request = self.request_factory.post("dummy")
        response = set_subscription(request, "dummy", subscribe=False)
        assert response.status_code == 405

    def test_unsubscribe_invalid_token(self):
        def test_invalid_token(token, message):
            request = self.request_factory.get("dummy")
            self.assertRaisesRegex(Http404, f"^{message}$", set_subscription, request, token, False)

        # Invalid base64 encoding
        test_invalid_token("ZOMG INVALID BASE64 CHARS!!!", "base64url")
        test_invalid_token("Non-ASCII\xff", "base64url")
        test_invalid_token(self.tokens[self.user][:-1], "base64url")

        # Token not long enough to contain initialization vector
        test_invalid_token("AAAAAAAAAAA=", "initialization_vector")

        # Token length not a multiple of AES block length
        test_invalid_token(self.tokens[self.user][:-4], "aes")

        # Invalid padding (ends in 0 byte)
        # Encrypted value: "testuser" + "\x00" * 8
        test_invalid_token("AAAAAAAAAAAAAAAAAAAAAMoazRI7ePLjEWXN1N7keLw=", "padding")

        # Invalid padding (ends in byte > 16)
        # Encrypted value: "testusertestuser"
        test_invalid_token("AAAAAAAAAAAAAAAAAAAAAC6iLXGhjkFytJoJSBJZzJ4=", "padding")

        # Invalid padding (entire string is padding)
        # Encrypted value: "\x10" * 16
        test_invalid_token("AAAAAAAAAAAAAAAAAAAAANRGw8HDEmlcLVFawgY9wI8=", "padding")

        # Nonexistent user
        # Encrypted value: "nonexistentuser\x01"
        test_invalid_token("AAAAAAAAAAAAAAAAAAAAACpyUxTGIrUjnpuUsNi7mAY=", "username")

    def test_unsubscribe_success(self):
        self.create_prefs()

        def test_user(user):
            url = reverse('unsubscribe_forum_update', args=[self.tokens[user]])

            response = self.client.get(url)
            assert response.status_code == 200
            self.assertNotPrefExists(user)

        for user in self.tokens.keys():
            test_user(user)

    def test_unsubscribe_twice(self):
        self.create_prefs()

        url = reverse('unsubscribe_forum_update', args=[self.tokens[self.user]])
        self.client.get(url)
        response = self.client.get(url)
        assert response.status_code == 200
        self.assertNotPrefExists(self.user)

    def test_resubscribe_success(self):
        def test_user(user):
            # start without a pref key
            assert not UserPreference.objects.filter(user=user, key=NOTIFICATION_PREF_KEY)
            url = reverse('resubscribe_forum_update', args=[self.tokens[user]])
            response = self.client.get(url)
            assert response.status_code == 200
            self.assertPrefValid(user)

        for user in self.tokens.keys():
            test_user(user)
