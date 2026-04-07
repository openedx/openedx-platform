"""
Tests for the DjangoXBlockUserService.
"""

import ddt
import pytest
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import anonymous_id_for_user
from common.djangoapps.student.tests.factories import AnonymousUserFactory, UserFactory
from common.djangoapps.xblock_django.user_service import (
    ATTR_KEY_ANONYMOUS_USER_ID,
    ATTR_KEY_IS_AUTHENTICATED,
    ATTR_KEY_REQUEST_COUNTRY_CODE,
    ATTR_KEY_USER_ID,
    ATTR_KEY_USER_IS_BETA_TESTER,
    ATTR_KEY_USER_IS_GLOBAL_STAFF,
    ATTR_KEY_USER_IS_STAFF,
    ATTR_KEY_USER_PREFERENCES,
    ATTR_KEY_USER_ROLE,
    ATTR_KEY_USERNAME,
    USER_PREFERENCES_WHITE_LIST,
    DjangoXBlockUserService,
)
from openedx.core.djangoapps.external_user_ids.models import ExternalIdType
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference


@ddt.ddt
class UserServiceTestCase(TestCase):
    """
    Tests for the DjangoXBlockUserService.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory(username="tester", email="test@tester.com")
        self.user.profile.name = "Test Tester"
        set_user_preference(self.user, 'pref-lang', 'en')
        set_user_preference(self.user, 'time_zone', 'US/Pacific')
        set_user_preference(self.user, 'not_white_listed', 'hidden_value')
        self.anon_user = AnonymousUserFactory()

    def assert_is_anon_xb_user(self, xb_user, request_country_code):
        """
        A set of assertions for an anonymous XBlockUser.
        """
        assert not xb_user.opt_attrs[ATTR_KEY_IS_AUTHENTICATED]
        assert xb_user.opt_attrs[ATTR_KEY_REQUEST_COUNTRY_CODE] == request_country_code
        assert xb_user.full_name is None
        self.assertListEqual(xb_user.emails, [])

    def assert_xblock_user_matches_django(
        self,
        xb_user,
        dj_user,
        user_is_staff=False,
        user_role=None,
        anonymous_user_id=None,
        request_country_code=None,
        user_is_global_staff=False,
        user_is_beta_tester=False,
    ):
        """
        A set of assertions for comparing a XBlockUser to a django User
        """
        assert xb_user.opt_attrs[ATTR_KEY_IS_AUTHENTICATED]
        assert xb_user.emails[0] == dj_user.email
        assert xb_user.full_name == dj_user.profile.name
        assert xb_user.opt_attrs[ATTR_KEY_USERNAME] == dj_user.username
        assert xb_user.opt_attrs[ATTR_KEY_USER_ID] == dj_user.id
        assert xb_user.opt_attrs[ATTR_KEY_USER_IS_BETA_TESTER] == user_is_beta_tester
        assert xb_user.opt_attrs[ATTR_KEY_USER_IS_STAFF] == user_is_staff
        assert xb_user.opt_attrs[ATTR_KEY_USER_IS_GLOBAL_STAFF] == user_is_global_staff
        assert xb_user.opt_attrs[ATTR_KEY_USER_ROLE] == user_role
        assert xb_user.opt_attrs[ATTR_KEY_ANONYMOUS_USER_ID] == anonymous_user_id
        assert xb_user.opt_attrs[ATTR_KEY_REQUEST_COUNTRY_CODE] == request_country_code
        assert all((pref in USER_PREFERENCES_WHITE_LIST) for pref in xb_user.opt_attrs[ATTR_KEY_USER_PREFERENCES])

    def test_convert_anon_user(self):
        """
        Tests for convert_django_user_to_xblock_user behavior when django user is AnonymousUser.
        """
        country_code = 'UK'
        django_user_service = DjangoXBlockUserService(self.anon_user, request_country_code=country_code)
        xb_user = django_user_service.get_current_user()
        assert xb_user.is_current_user
        self.assert_is_anon_xb_user(xb_user, request_country_code=country_code)

    @ddt.data(
        (False, None, None, None, False, False),
        (True, 'instructor', None, None, False, False),
        (True, 'staff', None, None, False, False),
        (False, 'student', 'abcdef0123', None, False, False),
        (True, 'student', 'abcdef0123', 'uk', False, False),
        (False, None, None, None, True, False),
        (False, None, None, None, False, True),
    )
    @ddt.unpack
    def test_convert_authenticate_user(
        self,
        user_is_staff,
        user_role,
        anonymous_user_id,
        request_country_code,
        user_is_global_staff,
        user_is_beta_tester,
    ):
        """
        Tests for convert_django_user_to_xblock_user behavior when django user is User.
        """
        django_user_service = DjangoXBlockUserService(
            self.user,
            user_is_staff=user_is_staff,
            user_role=user_role,
            anonymous_user_id=anonymous_user_id,
            request_country_code=request_country_code,
            user_is_global_staff=user_is_global_staff,
            user_is_beta_tester=user_is_beta_tester,
        )
        xb_user = django_user_service.get_current_user()
        assert xb_user.is_current_user
        self.assert_xblock_user_matches_django(
            xb_user,
            self.user,
            user_is_staff,
            user_role,
            anonymous_user_id,
            request_country_code,
            user_is_global_staff,
            user_is_beta_tester,
        )

    def test_get_anonymous_user_id_returns_none_for_non_staff_users(self):
        """
        Tests for anonymous_user_id method to return None if user is Non-Staff.
        """
        django_user_service = DjangoXBlockUserService(self.user, user_is_staff=False)

        anonymous_user_id = django_user_service.get_anonymous_user_id(
            username=self.user.username,
            course_id='edx/toy/2012_Fall'
        )
        assert anonymous_user_id is None

    def test_get_anonymous_user_id_returns_none_for_non_existing_users(self):
        """
        Tests for anonymous_user_id method to return None username does not exist in system.
        """
        django_user_service = DjangoXBlockUserService(self.user, user_is_staff=True)

        anonymous_user_id = django_user_service.get_anonymous_user_id(username="No User", course_id='edx/toy/2012_Fall')
        assert anonymous_user_id is None

    def test_get_anonymous_user_id_returns_id_for_existing_users(self):
        """
        Tests for anonymous_user_id method returns anonymous user id for a user.
        """
        course_key = CourseKey.from_string('edX/toy/2012_Fall')
        anon_user_id = anonymous_id_for_user(
            user=self.user,
            course_id=course_key
        )

        django_user_service = DjangoXBlockUserService(self.user, user_is_staff=True)
        anonymous_user_id = django_user_service.get_anonymous_user_id(
            username=self.user.username,
            course_id='edX/toy/2012_Fall'
        )

        assert anonymous_user_id == anon_user_id

    def test_get_user_by_anonymous_id(self):
        """
        Tests that get_user_by_anonymous_id returns the expected user.
        """
        course_key = CourseKey.from_string('edX/toy/2012_Fall')
        anon_user_id = anonymous_id_for_user(
            user=self.user,
            course_id=course_key
        )

        django_user_service = DjangoXBlockUserService(self.user)
        user = django_user_service.get_user_by_anonymous_id(anon_user_id)
        assert user == self.user

    def test_get_user_by_anonymous_id_not_found(self):
        """
        Tests that get_user_by_anonymous_id returns None for an unassigned anonymous user id.
        """
        django_user_service = DjangoXBlockUserService(self.user)
        assert django_user_service.get_user_by_anonymous_id('invalid-anon-id') is None

    def test_external_id(self):
        """
        Tests that external ids differ based on type.
        """
        ExternalIdType.objects.create(name='test1', description='Test type 1')
        ExternalIdType.objects.create(name='test2', description='Test type 2')
        django_user_service = DjangoXBlockUserService(self.user, user_is_staff=True)
        ext_id1 = django_user_service.get_external_user_id('test1')
        ext_id2 = django_user_service.get_external_user_id('test2')
        assert ext_id1 != ext_id2
        with pytest.raises(ValueError):
            django_user_service.get_external_user_id('unknown')

    def test_get_user_by_anonymous_id_assume_id(self):
        """
        Tests that get_user_by_anonymous_id uses the anonymous user ID given to the service if none is provided.
        """
        course_key = CourseKey.from_string('edX/toy/2012_Fall')
        anon_user_id = anonymous_id_for_user(
            user=self.user,
            course_id=course_key
        )

        django_user_service = DjangoXBlockUserService(self.user, anonymous_user_id=anon_user_id)
        user = django_user_service.get_user_by_anonymous_id()
        assert user == self.user
