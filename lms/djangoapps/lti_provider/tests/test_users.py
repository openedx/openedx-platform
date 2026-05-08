"""
Tests for the LTI user management functionality
"""

import itertools
import string
from unittest.mock import MagicMock, PropertyMock, patch

import ddt
import pytest
from django.contrib.auth.models import AnonymousUser, User  # pylint: disable=imported-auth-user
from django.core.exceptions import PermissionDenied
from django.db.utils import IntegrityError
from django.test import TestCase
from django.test.client import RequestFactory

from common.djangoapps.student.tests.factories import UserFactory

from .. import users
from ..models import LtiConsumer, LtiUser


class UserManagementHelperTest(TestCase):
    """
    Tests for the helper functions in users.py
    """

    def setUp(self):
        super().setUp()
        self.request = RequestFactory().post('/')
        self.old_user = UserFactory.create()
        self.new_user = UserFactory.create()
        self.new_user.save()
        self.request.user = self.old_user
        self.lti_consumer = LtiConsumer(
            consumer_name='TestConsumer',
            consumer_key='TestKey',
            consumer_secret='TestSecret'
        )
        self.lti_consumer.save()
        self.lti_user = LtiUser(
            lti_user_id='lti_user_id',
            edx_user=self.new_user
        )

    @patch('django.contrib.auth.authenticate', return_value=None)
    def test_permission_denied_for_unknown_user(self, _authenticate_mock):  # noqa: PT019
        with pytest.raises(PermissionDenied):
            users.switch_user(self.request, self.lti_user, self.lti_consumer)

    @patch('lms.djangoapps.lti_provider.users.login')
    def test_authenticate_called(self, _login_mock):  # noqa: PT019
        with patch('lms.djangoapps.lti_provider.users.authenticate', return_value=self.new_user) as authenticate:
            users.switch_user(self.request, self.lti_user, self.lti_consumer)
            authenticate.assert_called_with(
                username=self.new_user.username,
                lti_user_id=self.lti_user.lti_user_id,
                lti_consumer=self.lti_consumer
            )

    @patch('lms.djangoapps.lti_provider.users.login')
    def test_login_called(self, login_mock):
        with patch('lms.djangoapps.lti_provider.users.authenticate', return_value=self.new_user):
            users.switch_user(self.request, self.lti_user, self.lti_consumer)
            login_mock.assert_called_with(self.request, self.new_user)

    def test_random_username_generator(self):
        for _idx in range(1000):
            username = users.generate_random_edx_username()
            assert len(username) <= 30, 'Username too long'
            # Check that the username contains only allowable characters
            for char in range(len(username)):  # pylint: disable=consider-using-enumerate
                assert username[char] in (string.ascii_letters + string.digits), \
                    f"Username has forbidden character '{username[char]}'"


@ddt.ddt
@patch('lms.djangoapps.lti_provider.users.switch_user', autospec=True)
@patch('lms.djangoapps.lti_provider.users.create_lti_user', autospec=True)
class AuthenticateLtiUserTest(TestCase):
    """
    Tests for the authenticate_lti_user function in users.py
    """

    def setUp(self):
        super().setUp()
        self.lti_consumer = LtiConsumer(
            consumer_name='TestConsumer',
            consumer_key='TestKey',
            consumer_secret='TestSecret'
        )
        self.lti_consumer.save()
        self.lti_user_id = 'lti_user_id'
        self.edx_user_id = 'edx_user_id'
        self.old_user = UserFactory.create()
        self.request = RequestFactory().post('/')
        self.request.user = self.old_user
        self.auto_linking_consumer = LtiConsumer(
            consumer_name='AutoLinkingConsumer',
            consumer_key='AutoLinkingKey',
            consumer_secret='AutoLinkingSecret',
            require_user_account=True
        )
        self.auto_linking_consumer.save()

    def create_lti_user_model(self, consumer=None):
        """
        Generate and save a User and an LTI user model
        """
        edx_user = User(username=self.edx_user_id)
        edx_user.save()
        lti_user = LtiUser(
            lti_consumer=consumer or self.lti_consumer,
            lti_user_id=self.lti_user_id,
            edx_user=edx_user
        )
        lti_user.save()
        return lti_user

    def test_authentication_with_new_user(self, _create_user, switch_user):  # noqa: PT019
        lti_user = MagicMock()
        lti_user.edx_user_id = self.edx_user_id
        with patch('lms.djangoapps.lti_provider.users.create_lti_user', return_value=lti_user) as create_user:
            users.authenticate_lti_user(self.request, self.lti_user_id, self.lti_consumer)
            create_user.assert_called_with(self.lti_user_id, self.lti_consumer)
            switch_user.assert_called_with(self.request, lti_user, self.lti_consumer)

    def test_authentication_with_authenticated_user(self, create_user, switch_user):
        lti_user = self.create_lti_user_model()
        self.request.user = lti_user.edx_user
        assert self.request.user.is_authenticated
        users.authenticate_lti_user(self.request, self.lti_user_id, self.lti_consumer)
        assert not create_user.called
        assert not switch_user.called

    def test_authentication_with_unauthenticated_user(self, create_user, switch_user):
        lti_user = self.create_lti_user_model()
        self.request.user = lti_user.edx_user
        with patch('django.contrib.auth.models.User.is_authenticated', new_callable=PropertyMock) as mock_is_auth:
            mock_is_auth.return_value = False
            users.authenticate_lti_user(self.request, self.lti_user_id, self.lti_consumer)
            assert not create_user.called
            switch_user.assert_called_with(self.request, lti_user, self.lti_consumer)

    def test_authentication_with_wrong_user(self, create_user, switch_user):
        lti_user = self.create_lti_user_model()
        self.request.user = self.old_user
        assert self.request.user.is_authenticated
        users.authenticate_lti_user(self.request, self.lti_user_id, self.lti_consumer)
        assert not create_user.called
        switch_user.assert_called_with(self.request, lti_user, self.lti_consumer)

    def test_auto_linking_of_users_using_lis_person_contact_email_primary(self, create_user, switch_user):
        request = RequestFactory().post("/", {"lis_person_contact_email_primary": self.old_user.email})
        request.user = self.old_user

        users.authenticate_lti_user(request, self.lti_user_id, self.lti_consumer)
        create_user.assert_called_with(self.lti_user_id, self.lti_consumer)

        users.authenticate_lti_user(request, self.lti_user_id, self.auto_linking_consumer)
        create_user.assert_called_with(self.lti_user_id, self.auto_linking_consumer, {
            "email": self.old_user.email,
            "full_name": "",
        })

    def test_auto_linking_of_users_using_lis_person_contact_email_primary_case_insensitive(self, create_user, switch_user):  # pylint: disable=line-too-long
        request = RequestFactory().post("/", {"lis_person_contact_email_primary": self.old_user.email.upper()})
        request.user = self.old_user

        users.authenticate_lti_user(request, self.lti_user_id, self.lti_consumer)
        create_user.assert_called_with(self.lti_user_id, self.lti_consumer)

        users.authenticate_lti_user(request, self.lti_user_id, self.auto_linking_consumer)
        create_user.assert_called_with(self.lti_user_id, self.auto_linking_consumer, {
            "email": self.old_user.email,
            "full_name": "",
        })

    def test_raise_exception_trying_to_auto_link_unauthenticate_user(self, create_user, switch_user):
        request = RequestFactory().post("/")
        request.user = AnonymousUser()

        with self.assertRaises(PermissionDenied):  # noqa: PT027
            users.authenticate_lti_user(request, self.lti_user_id, self.auto_linking_consumer)

    def test_raise_exception_on_mismatched_user_and_lis_email(self, create_user, switch_user):
        request = RequestFactory().post("/", {"lis_person_contact_email_primary": "wrong_email@example.com"})
        request.user = self.old_user

        with self.assertRaises(PermissionDenied):  # noqa: PT027
            users.authenticate_lti_user(request, self.lti_user_id, self.auto_linking_consumer)

    def test_authenticate_unauthenticated_user_after_auto_linking_of_user_account(self, create_user, switch_user):
        lti_user = self.create_lti_user_model(self.auto_linking_consumer)
        self.request.user = AnonymousUser()

        users.authenticate_lti_user(self.request, self.lti_user_id, self.auto_linking_consumer)
        assert not create_user.called
        switch_user.assert_called_with(self.request, lti_user, self.auto_linking_consumer)

    @ddt.data(
        *itertools.product(
            (
                (
                    {
                        "lis_person_contact_email_primary": "some_email@example.com",
                        "lis_person_name_given": "John",
                        "lis_person_name_family": "Doe",
                    },
                    "some_email@example.com",
                    "John Doe",
                ),
                (
                    {
                        "lis_person_contact_email_primary": "some_email@example.com",
                        "lis_person_name_full": "John Doe",
                        "lis_person_name_given": "Jacob",
                    },
                    "some_email@example.com",
                    "John Doe",
                ),
                (
                    {"lis_person_contact_email_primary": "some_email@example.com", "lis_person_name_full": "John Doe"},
                    "some_email@example.com",
                    "John Doe",
                ),
                ({"lis_person_contact_email_primary": "some_email@example.com"}, "some_email@example.com", ""),
                ({"lis_person_contact_email_primary": ""}, "", ""),
                ({"lis_person_contact_email_primary": ""}, "", ""),
                ({}, "", ""),
            ),
            [True, False],
        )
    )
    @ddt.unpack
    def test_create_user_when_user_account_not_required(self, params, enable_lti_pii, create_user, switch_user):
        post_params, email, name = params
        self.auto_linking_consumer.require_user_account = False
        self.auto_linking_consumer.use_lti_pii = enable_lti_pii
        self.auto_linking_consumer.save()
        request = RequestFactory().post("/", post_params)
        request.user = AnonymousUser()
        users.authenticate_lti_user(request, self.lti_user_id, self.auto_linking_consumer)
        if enable_lti_pii:
            profile = {"email": email, "full_name": name, "username": self.lti_user_id}
            create_user.assert_called_with(self.lti_user_id, self.auto_linking_consumer, profile)
        else:
            create_user.assert_called_with(self.lti_user_id, self.auto_linking_consumer)


@ddt.ddt
class CreateLtiUserTest(TestCase):
    """
    Tests for the create_lti_user function in users.py
    """

    def setUp(self):
        super().setUp()
        self.lti_consumer = LtiConsumer(
            consumer_name='TestConsumer',
            consumer_key='TestKey',
            consumer_secret='TestSecret'
        )
        self.lti_consumer.save()
        self.existing_user = UserFactory.create()

    def test_create_lti_user_creates_auth_user_model(self):
        users.create_lti_user('lti_user_id', self.lti_consumer)
        assert User.objects.count() == 2

    @patch('uuid.uuid4', return_value='random_uuid')
    @patch('lms.djangoapps.lti_provider.users.generate_random_edx_username', return_value='edx_id')
    def test_create_lti_user_creates_correct_user(self, uuid_mock, _username_mock):  # noqa: PT019
        users.create_lti_user('lti_user_id', self.lti_consumer)
        assert User.objects.count() == 2
        user = User.objects.get(username='edx_id')
        assert user.email == 'edx_id@lti.example.com'
        uuid_mock.assert_called_with()

    @patch('lms.djangoapps.lti_provider.users.generate_random_edx_username', side_effect=['edx_id', 'new_edx_id'])
    def test_unique_username_created(self, username_mock):
        User(username='edx_id').save()
        users.create_lti_user('lti_user_id', self.lti_consumer, None)
        assert username_mock.call_count == 2
        assert User.objects.count() == 3
        user = User.objects.get(username='new_edx_id')
        assert user.email == 'new_edx_id@lti.example.com'

    def test_existing_user_is_linked(self):
        lti_user = users.create_lti_user('lti_user_id', self.lti_consumer, {"email": self.existing_user.email})
        assert lti_user.lti_consumer == self.lti_consumer
        assert lti_user.edx_user == self.existing_user

    def test_only_one_lti_user_edx_user_for_each_lti_consumer(self):
        users.create_lti_user('lti_user_id', self.lti_consumer, {"email": self.existing_user.email})

        with pytest.raises(IntegrityError):
            users.create_lti_user('lti_user_id', self.lti_consumer, {"email": self.existing_user.email})

    def test_create_multiple_lti_users_for_edx_user_if_lti_consumer_varies(self):
        lti_consumer_2 = LtiConsumer(
            consumer_name="SecondConsumer",
            consumer_key="SecondKey",
            consumer_secret="SecondSecret",
        )
        lti_consumer_2.save()

        lti_user_1 = users.create_lti_user('lti_user_id', self.lti_consumer, {"email": self.existing_user.email})
        lti_user_2 = users.create_lti_user('lti_user_id', lti_consumer_2, {"email": self.existing_user.email})

        assert lti_user_1.edx_user == lti_user_2.edx_user

    def test_create_lti_user_with_full_profile(self):
        lti_user = users.create_lti_user('lti_user_id', self.lti_consumer, {
            "email": "some.user@example.com",
            "full_name": "John Doe",
            "username": "john_doe",
        })
        assert lti_user.edx_user.email == "some.user@example.com"
        assert lti_user.edx_user.username == "john_doe"
        assert lti_user.edx_user.profile.name == "John Doe"

    @patch('lms.djangoapps.lti_provider.users.generate_random_edx_username', side_effect=['edx_id'])
    def test_create_lti_user_with_missing_username_in_profile(self, mock):
        lti_user = users.create_lti_user('lti_user_id', self.lti_consumer, {
            "email": "some.user@example.com",
            "full_name": "John Doe",
        })
        assert lti_user.edx_user.email == "some.user@example.com"
        assert lti_user.edx_user.username == "edx_id"
        assert lti_user.edx_user.profile.name == "John Doe"

    @patch('lms.djangoapps.lti_provider.users.generate_random_edx_username', side_effect=['edx_id', 'edx_id123'])
    def test_create_lti_user_with_duplicate_username_in_profile(self, mock):
        lti_user = users.create_lti_user('lti_user_id', self.lti_consumer, {
            "email": "some.user@example.com",
            "full_name": "John Doe",
            "username": self.existing_user.username,
        })
        assert lti_user.edx_user.email == "some.user@example.com"
        assert lti_user.edx_user.username == "edx_id"
        assert lti_user.edx_user.profile.name == "John Doe"


class LtiBackendTest(TestCase):
    """
    Tests for the authentication backend that authenticates LTI users.
    """

    def setUp(self):
        super().setUp()
        self.edx_user = UserFactory.create()
        self.edx_user.save()
        self.lti_consumer = LtiConsumer(
            consumer_key="Consumer Key",
            consumer_secret="Consumer Secret"
        )
        self.lti_consumer.save()
        self.lti_user_id = 'LTI User ID'
        LtiUser(
            lti_consumer=self.lti_consumer,
            lti_user_id=self.lti_user_id,
            edx_user=self.edx_user
        ).save()
        self.old_user = UserFactory.create()
        self.request = RequestFactory().post('/')
        self.request.user = self.old_user

    def test_valid_user_authenticates(self):
        user = users.LtiBackend().authenticate(
            self.request,
            username=self.edx_user.username,
            lti_user_id=self.lti_user_id,
            lti_consumer=self.lti_consumer
        )
        assert user == self.edx_user

    def test_missing_user_returns_none(self):
        user = users.LtiBackend().authenticate(
            self.request,
            username=self.edx_user.username,
            lti_user_id='Invalid Username',
            lti_consumer=self.lti_consumer
        )
        assert user is None

    def test_non_lti_user_returns_none(self):
        non_edx_user = UserFactory.create()
        non_edx_user.save()
        user = users.LtiBackend().authenticate(
            self.request,
            username=non_edx_user.username,
        )
        assert user is None

    def test_missing_lti_id_returns_null(self):
        user = users.LtiBackend().authenticate(
            self.request,
            username=self.edx_user.username,
            lti_consumer=self.lti_consumer
        )
        assert user is None

    def test_missing_lti_consumer_returns_null(self):
        user = users.LtiBackend().authenticate(
            self.request,
            username=self.edx_user.username,
            lti_user_id=self.lti_user_id,
        )
        assert user is None

    def test_existing_user_returned_by_get_user(self):
        user = users.LtiBackend().get_user(self.edx_user.id)
        assert user == self.edx_user

    def test_get_user_returns_none_for_invalid_user(self):
        user = users.LtiBackend().get_user(-1)
        assert user is None
