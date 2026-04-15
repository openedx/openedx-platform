"""
Contains tests for OAuth2 model-retirement methods.
"""


import datetime

from django.test import TestCase
from oauth2_provider.models import AccessToken as DOTAccessToken
from oauth2_provider.models import Application as DOTApplication
from oauth2_provider.models import Grant as DOTGrant
from oauth2_provider.models import RefreshToken as DOTRefreshToken

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.tests import factories

from ..oauth2_retirement_utils import retire_dot_oauth2_models


class RetireDOTModelsTest(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def test_delete_dot_models(self):
        user = UserFactory.create()
        app = factories.ApplicationFactory(user=user)
        access_token = factories.AccessTokenFactory(
            user=user,
            application=app
        )
        factories.RefreshTokenFactory(
            user=user,
            application=app,
            access_token=access_token,
        )
        DOTGrant.objects.create(
            user=user,
            application=app,
            expires=datetime.datetime(2018, 1, 1),
        )

        retire_dot_oauth2_models(user)

        applications = DOTApplication.objects.filter(user_id=user.id)
        access_tokens = DOTAccessToken.objects.filter(user_id=user.id)
        refresh_tokens = DOTRefreshToken.objects.filter(user_id=user.id)
        grants = DOTGrant.objects.filter(user=user)

        query_sets = [applications, access_tokens, refresh_tokens, grants]

        for query_set in query_sets:
            assert not query_set.exists()
