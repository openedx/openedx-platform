"""
Test that various filters are fired for the vies in the user_authn app.
"""
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import StudentLoginRequested, StudentRegistrationRequested
from rest_framework import status

from common.djangoapps.student.tests.factories import UserFactory, UserProfileFactory
from openedx.core.djangoapps.user_api.tests.test_views import UserAPITestCase
from openedx.core.djangolib.testing.utils import skip_unless_lms

User = get_user_model()


class TestRegisterPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, form_data):  # pylint: disable=arguments-differ
        """Pipeline steps that changes the user's username."""
        username = f"{form_data.get('username')}-OpenEdx"
        form_data["username"] = username
        return {
            "form_data": form_data,
        }


class TestAnotherRegisterPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, form_data):  # pylint: disable=arguments-differ
        """Pipeline steps that changes the user's username."""
        username = f"{form_data.get('username')}-Test"
        form_data["username"] = username
        return {
            "form_data": form_data,
        }


class TestStopRegisterPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, form_data):  # pylint: disable=arguments-differ
        """Pipeline steps that stops the user's registration process."""
        raise StudentRegistrationRequested.PreventRegistration("You can't register on this site.", status_code=403)


class TestLoginPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user):  # pylint: disable=arguments-differ
        """Pipeline steps that adds a field to the user's profile."""
        user.profile.set_meta({"logged_in": True})
        user.profile.save()
        return {
            "user": user
        }


class TestAnotherLoginPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user):  # pylint: disable=arguments-differ
        """Pipeline steps that adds a field to the user's profile."""
        new_meta = user.profile.get_meta()
        new_meta.update({"another_logged_in": True})
        user.profile.set_meta(new_meta)
        user.profile.save()
        return {
            "user": user
        }


class TestStopLoginPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user):  # pylint: disable=arguments-differ
        """Pipeline steps that stops the user's login."""
        raise StudentLoginRequested.PreventLogin("You can't login on this site.")


@skip_unless_lms
class RegistrationFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the user registration process.

    This class guarantees that the following filters are triggered during the user's registration:

    - StudentRegistrationRequested
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_registration")
        self.user_info = {
            "email": "user@example.com",
            "name": "Test User",
            "username": "test",
            "password": "password",
            "honor_code": "true",
        }

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.student.registration.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestRegisterPipelineStep",
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestAnotherRegisterPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_register_filter_executed(self):
        """
        Test whether the student register filter is triggered before the user's
        registration process.

        Expected result:
            - StudentRegistrationRequested is triggered and executes TestRegisterPipelineStep.
            - The user's username is updated.
        """
        self.client.post(self.url, self.user_info)

        user = User.objects.filter(username=f"{self.user_info.get('username')}-OpenEdx-Test")
        self.assertTrue(user)  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.student.registration.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestRegisterPipelineStep",
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestStopRegisterPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_register_filter_prevent_registration(self):
        """
        Test prevent the user's registration through a pipeline step.

        Expected result:
            - StudentRegistrationRequested is triggered and executes TestStopRegisterPipelineStep.
            - The user's registration stops.
        """
        response = self.client.post(self.url, self.user_info)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)  # noqa: PT009

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_register_without_filter_configuration(self):
        """
        Test usual registration process, without filter's intervention.

        Expected result:
            - StudentRegistrationRequested does not have any effect on the registration process.
            - The registration process ends successfully.
        """
        self.client.post(self.url, self.user_info)

        user = User.objects.filter(username=f"{self.user_info.get('username')}")
        self.assertTrue(user)  # noqa: PT009


@skip_unless_lms
class LoginFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the user login process.

    This class guarantees that the following filters are triggered during the user's login:

    - StudentLoginRequested
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.user = UserFactory.create(
            username="test",
            email="test@example.com",
            password="password",
        )
        self.user_profile = UserProfileFactory.create(user=self.user, name="Test Example")
        self.url = reverse('login_api')

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.student.login.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestLoginPipelineStep",
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestAnotherLoginPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_login_filter_executed(self):
        """
        Test whether the student login filter is triggered before the user's
        login process.

        Expected result:
            - StudentLoginRequested is triggered and executes TestLoginPipelineStep.
            - The user's profile is updated.
        """
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        self.client.post(self.url, data)

        user = User.objects.get(username=self.user.username)
        self.assertDictEqual({"logged_in": True, "another_logged_in": True}, user.profile.get_meta())  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.student.login.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestLoginPipelineStep",
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestStopLoginPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_login_filter_prevent_login(self):
        """
        Test prevent the user's login through a pipeline step.

        Expected result:
            - StudentLoginRequested is triggered and executes TestStopLoginPipelineStep.
            - Test prevent the user's login through a pipeline step.
        """
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        response = self.client.post(self.url, data)

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)  # noqa: PT009

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_login_without_filter_configuration(self):
        """
        Test usual login process, without filter's intervention.

        Expected result:
            - StudentLoginRequested does not have any effect on the login process.
            - The login process ends successfully.
        """
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        response = self.client.post(self.url, data)

        self.assertEqual(status.HTTP_200_OK, response.status_code)  # noqa: PT009
