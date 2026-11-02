# pylint: disable=missing-docstring


from datetime import datetime, timedelta

import factory
from zoneinfo import ZoneInfo
from factory.django import DjangoModelFactory
from factory.fuzzy import FuzzyText
from oauth2_provider.models import AccessToken, Application, RefreshToken

from openedx.core.djangoapps.oauth_dispatch.models import ApplicationAccess
from common.djangoapps.student.tests.factories import UserFactory


class ApplicationFactory(DjangoModelFactory):
    class Meta:
        model = Application

    user = factory.SubFactory(UserFactory)
    client_id = factory.Sequence('client_{}'.format)
    client_secret = 'some_secret'
    client_type = 'confidential'
    authorization_grant_type = Application.CLIENT_CONFIDENTIAL
    name = FuzzyText(prefix='name', length=8)


class ApplicationAccessFactory(DjangoModelFactory):
    class Meta:
        model = ApplicationAccess

    application = factory.SubFactory(ApplicationFactory)
    scopes = ['grades:read']


class AccessTokenFactory(DjangoModelFactory):
    class Meta:
        model = AccessToken
        django_get_or_create = ('user', 'application')

    token = FuzzyText(length=32)
    expires = datetime.now(ZoneInfo("UTC")) + timedelta(days=1)


class RefreshTokenFactory(DjangoModelFactory):
    class Meta:
        model = RefreshToken
        django_get_or_create = ('user', 'application')

    token = FuzzyText(length=32)
