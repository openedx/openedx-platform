"""
Test that various filters are fired for the vies in the user_authn app.
"""
from unittest.mock import Mock, patch

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


class TestFormDescriptionPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, form_desc, running_pipeline, current_provider):  # pylint: disable=arguments-differ
        """Pipeline step that overrides the default value of the email field."""
        form_desc.override_field_properties("email", default="filtered@example.com")
        return {
            "form_desc": form_desc,
            "running_pipeline": running_pipeline,
            "current_provider": current_provider,
        }


class TestLogistrationContextPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context):  # pylint: disable=arguments-differ
        """Pipeline step that modifies the logistration page context."""
        context["data"]["platform_name"] = "Filtered Platform Name"
        return {
            "context": context,
        }


class TestLogistrationResponsePipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, response, context):  # pylint: disable=arguments-differ
        """Pipeline step that sets a cookie on the logistration response."""
        response.set_cookie("logistration-filter", "applied")
        return {
            "response": response,
            "context": context,
        }


class TestPostLoginRedirectPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, redirect_url, user):  # pylint: disable=arguments-differ
        """Pipeline step that overrides the post-login redirect URL."""
        return {
            "redirect_url": "/custom/post/login",
            "user": user,
        }


class TestUnsafePostLoginRedirectPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, redirect_url, user):  # pylint: disable=arguments-differ
        """Pipeline step that overrides the post-login redirect URL with an off-site one."""
        return {
            "redirect_url": "http://evil.example.com/phish",
            "user": user,
        }


class TestAuthnMFEContextPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, extra_context):  # pylint: disable=arguments-differ
        """
        Pipeline step that modifies a declared context entry and contributes an undeclared one.
        """
        context["platformName"] = "Filtered Platform Name"
        extra_context["brandingStrings"] = {"welcome": "Filtered Welcome"}
        return {
            "context": context,
            "extra_context": extra_context,
        }


@skip_unless_lms
class LoginFormFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the login form description.

    This class guarantees that the following filter is triggered while the login form
    description is built, on every request (not only during third-party auth):

    - LoginFormGenerated
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_login_session", kwargs={"api_version": "v1"})

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.login.form.generated.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestFormDescriptionPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_login_form_filter_executed(self):
        """
        Test whether the login form filter is triggered while the form is built, without
        any third-party-auth pipeline running.

        Expected result:
            - LoginFormGenerated is triggered and executes TestFormDescriptionPipelineStep.
            - The email field default is overridden in the serialized form description.
        """
        response = self.client.get(self.url)

        self.assertContains(response, "filtered@example.com")

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_login_form_without_filter_configuration(self):
        """
        Test usual login form description, without filter's intervention.

        Expected result:
            - LoginFormGenerated does not have any effect on the form description.
        """
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        self.assertNotContains(response, "filtered@example.com")


@skip_unless_lms
class RegistrationFormFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the registration form description.

    This class guarantees that the following filter is triggered while the registration
    form description is built, on every request (not only during third-party auth):

    - RegistrationFormGenerated
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_registration")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.registration.form.generated.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestFormDescriptionPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_registration_form_filter_executed(self):
        """
        Test whether the registration form filter is triggered while the form is built,
        without any third-party-auth pipeline running.

        Expected result:
            - RegistrationFormGenerated is triggered and executes TestFormDescriptionPipelineStep.
            - The email field default is overridden in the serialized form description.
        """
        response = self.client.get(self.url)

        self.assertContains(response, "filtered@example.com")

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_registration_form_without_filter_configuration(self):
        """
        Test usual registration form description, without filter's intervention.

        Expected result:
            - RegistrationFormGenerated does not have any effect on the form description.
        """
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        self.assertNotContains(response, "filtered@example.com")


@skip_unless_lms
class LogistrationPageFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the legacy logistration page.

    This class guarantees that the following filters are triggered while the combined
    login/registration page is rendered:

    - LogistrationViewContextGenerated
    - LogistrationViewRenderCompleted
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("signin_user")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.logistration_view.context.generated.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestLogistrationContextPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_logistration_context_filter_executed(self):
        """
        Test whether the logistration context filter is triggered before the page is rendered.

        Expected result:
            - LogistrationViewContextGenerated is triggered and executes TestLogistrationContextPipelineStep.
            - The platform name overridden by the pipeline step is rendered into the page.
        """
        response = self.client.get(self.url, HTTP_ACCEPT="text/html")

        self.assertContains(response, "Filtered Platform Name")

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_logistration_context_without_filter_configuration(self):
        """
        Test usual logistration page rendering, without filter's intervention.

        Expected result:
            - LogistrationViewContextGenerated does not have any effect on the context.
        """
        response = self.client.get(self.url, HTTP_ACCEPT="text/html")

        assert response.status_code == status.HTTP_200_OK
        self.assertNotContains(response, "Filtered Platform Name")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.logistration_view.render.completed.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestLogistrationResponsePipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_logistration_response_filter_executed(self):
        """
        Test whether the logistration response filter is triggered after the page is rendered.

        Expected result:
            - LogistrationViewRenderCompleted is triggered and executes TestLogistrationResponsePipelineStep.
            - The cookie set by the pipeline step is present on the response.
        """
        response = self.client.get(self.url, HTTP_ACCEPT="text/html")

        assert response.status_code == status.HTTP_200_OK
        assert response.cookies["logistration-filter"].value == "applied"

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_logistration_response_without_filter_configuration(self):
        """
        Test usual logistration page rendering, without filter's intervention.

        Expected result:
            - LogistrationViewRenderCompleted does not have any effect on the response.
        """
        response = self.client.get(self.url, HTTP_ACCEPT="text/html")

        assert response.status_code == status.HTTP_200_OK
        assert "logistration-filter" not in response.cookies


@skip_unless_lms
class PostLoginRedirectFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the post-login redirect URL.

    This class guarantees that the following filters are triggered after a successful login:

    - LoginAltRedirectURLRequested
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.user = UserFactory.create(
            username="test",
            email="test@example.com",
            password="password",
        )
        self.user_profile = UserProfileFactory.create(user=self.user, name="Test Example")
        self.url = reverse("login_api")

    @patch(
        "openedx.core.djangoapps.user_authn.views.login.should_redirect_to_authn_microfrontend",
        Mock(return_value=True),
    )
    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.login.alt_redirect_url.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestPostLoginRedirectPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_login_redirect_filter_executed(self):
        """
        Test whether the post-login redirect filter is triggered after a successful login.

        Expected result:
            - LoginAltRedirectURLRequested is triggered and executes TestPostLoginRedirectPipelineStep.
            - The redirect URL returned in the response comes from the pipeline step.
        """
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        response = self.client.post(self.url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["redirect_url"].endswith("/custom/post/login")

    @patch(
        "openedx.core.djangoapps.user_authn.views.login.should_redirect_to_authn_microfrontend",
        Mock(return_value=True),
    )
    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.login.alt_redirect_url.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters."
                    "TestUnsafePostLoginRedirectPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_login_redirect_filter_returning_unsafe_url(self):
        """
        Test that an off-site redirect URL returned by the filter is discarded.

        Expected result:
            - LoginAltRedirectURLRequested is triggered and executes
              TestUnsafePostLoginRedirectPipelineStep.
            - The off-site URL is rejected and the user is redirected to the default next URL.
        """
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        response = self.client.post(self.url, data)

        assert response.status_code == status.HTTP_200_OK
        assert "evil.example.com" not in response.json()["redirect_url"]
        assert response.json()["redirect_url"].endswith("/dashboard")

    @patch(
        "openedx.core.djangoapps.user_authn.views.login.should_redirect_to_authn_microfrontend",
        Mock(return_value=True),
    )
    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_post_login_redirect_without_filter_configuration(self):
        """
        Test usual post-login redirect, without filter's intervention.

        Expected result:
            - LoginAltRedirectURLRequested does not have any effect on the redirect URL.
            - The user is redirected to the default next URL.
        """
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        response = self.client.post(self.url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["redirect_url"].endswith("/dashboard")


@skip_unless_lms
class AuthnMFEContextFiltersTest(UserAPITestCase):
    """
    Tests for the Open edX Filters associated with the authentication MFE context.

    This class guarantees that the following filter is triggered while the context served
    to the authentication MFE is built:

    - AuthnMFEContextGenerated
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("mfe_context")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.authentication.mfe.context.generated.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.user_authn.views.tests.test_filters.TestAuthnMFEContextPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_authn_mfe_context_filter_executed(self):
        """
        Test whether the authentication MFE context filter is triggered while the context is
        built, and that both of its arguments reach the response.

        Expected result:
            - AuthnMFEContextGenerated is triggered and executes TestAuthnMFEContextPipelineStep.
            - The declared platformName entry is overridden by the pipeline step.
            - The undeclared brandingStrings entry contributed through extra_context is merged
              into contextData with its nesting preserved.
        """
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        context_data = response.json()["contextData"]
        assert context_data["platformName"] == "Filtered Platform Name"
        assert context_data["brandingStrings"] == {"welcome": "Filtered Welcome"}

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_authn_mfe_context_without_filter_configuration(self):
        """
        Test usual authentication MFE context, without filter's intervention.

        Expected result:
            - AuthnMFEContextGenerated does not have any effect on the context.
            - No entry is added to contextData beyond the serializer's declared fields.
        """
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        context_data = response.json()["contextData"]
        assert context_data["platformName"] != "Filtered Platform Name"
        assert "brandingStrings" not in context_data
