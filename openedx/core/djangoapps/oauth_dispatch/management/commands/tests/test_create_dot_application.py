"""
Tests the ``create_dot_application`` management command.
"""
import ddt
import pytest
from django.core.management import call_command
from django.test import TestCase
from oauth2_provider.models import get_application_model

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.models import ApplicationAccess

from ..create_dot_application import Command

Application = get_application_model()


@ddt.ddt
class TestCreateDotApplication(TestCase):
    """
    Tests the ``create_dot_application`` management command.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def tearDown(self):
        super().tearDown()
        Application.objects.filter(user=self.user).delete()

    def test_update_dot_application(self):
        APP_NAME = "update_test_application"
        URI_OLD = "https://example.com/old"
        URI_NEW = "https://example.com/new"
        SCOPES_X = ["email", "profile", "user_id"]
        SCOPES_Y = ["email", "profile"]
        base_call_args = [
            APP_NAME,
            self.user.username,
            "--update",
            "--grant-type",
            Application.GRANT_CLIENT_CREDENTIALS,
            "--public",
            "--redirect-uris",
        ]

        # Make sure we can create Application with --update
        call_args = base_call_args + [URI_OLD]
        call_command(Command(), *call_args)
        app = Application.objects.get(name=APP_NAME)
        assert app.redirect_uris == URI_OLD
        with pytest.raises(ApplicationAccess.DoesNotExist):
            ApplicationAccess.objects.get(application_id=app.id)

        # Make sure we can call again with no changes
        call_args = base_call_args + [URI_OLD]
        call_command(Command(), *call_args)
        app = Application.objects.get(name=APP_NAME)
        assert app.redirect_uris == URI_OLD
        with pytest.raises(ApplicationAccess.DoesNotExist):
            ApplicationAccess.objects.get(application_id=app.id)

        # Make sure calling with new URI changes URI, but does not add access
        call_args = base_call_args + [URI_NEW]
        call_command(Command(), *call_args)
        app = Application.objects.get(name=APP_NAME)
        assert app.redirect_uris == URI_NEW
        with pytest.raises(ApplicationAccess.DoesNotExist):
            ApplicationAccess.objects.get(application_id=app.id)

        # Make sure calling with scopes adds access
        call_args = base_call_args + [URI_NEW, "--scopes", ",".join(SCOPES_X)]
        call_command(Command(), *call_args)
        app = Application.objects.get(name=APP_NAME)
        assert app.redirect_uris == URI_NEW
        access = ApplicationAccess.objects.get(application_id=app.id)
        assert access.scopes == SCOPES_X

        # Make sure calling with new scopes changes them
        call_args = base_call_args + [URI_NEW, "--scopes", ",".join(SCOPES_Y)]
        call_command(Command(), *call_args)
        app = Application.objects.get(name=APP_NAME)
        assert app.redirect_uris == URI_NEW
        access = ApplicationAccess.objects.get(application_id=app.id)
        assert access.scopes == SCOPES_Y

    @ddt.data(
        (None, None, None, None, False, None),
        (None, None, 'client-abc', None, False, None),
        (None, None, None, 'great-big-secret', False, 'email,profile,user_id'),
        ('password', True, 'client-dce', 'has-a-great-big-secret', True, None),
    )
    @ddt.unpack
    def test_create_dot_application(self, grant_type, public, client_id, client_secret, skip_auth, scopes):
        # Add optional arguments if provided
        call_args = ['testing_application', self.user.username]
        if grant_type:
            call_args.append('--grant-type')
            call_args.append(grant_type)
        else:
            grant_type = Application.GRANT_CLIENT_CREDENTIALS

        if public:
            call_args.append('--public')
            client_type = Application.CLIENT_PUBLIC
        else:
            client_type = Application.CLIENT_CONFIDENTIAL

        if client_id:
            call_args.append('--client-id')
            call_args.append(client_id)
        if client_secret:
            call_args.append('--client-secret')
            call_args.append(client_secret)
        if skip_auth:
            call_args.append('--skip-authorization')
        if scopes:
            call_args.append('--scopes')
            call_args.append(scopes)

        call_command(Command(), *call_args)

        apps = Application.objects.filter(name='testing_application')
        assert 1 == len(apps)
        application = apps[0]
        assert 'testing_application' == application.name
        assert self.user == application.user
        assert grant_type == application.authorization_grant_type
        assert client_type == application.client_type
        assert '' == application.redirect_uris
        assert skip_auth == application.skip_authorization

        if client_id:
            assert client_id == application.client_id
        if client_secret:
            assert client_secret == application.client_secret

        if scopes:
            app_access_list = ApplicationAccess.objects.filter(application_id=application.id)
            assert 1 == len(app_access_list)
            app_access = app_access_list[0]
            assert scopes.split(',') == app_access.scopes

        # When called a second time with the same arguments, the command should
        # exit gracefully without creating a second application.
        call_command(Command(), *call_args)
        apps = Application.objects.filter(name='testing_application')
        assert 1 == len(apps)
        if scopes:
            app_access_list = ApplicationAccess.objects.filter(application_id=application.id)
            assert 1 == len(app_access_list)
