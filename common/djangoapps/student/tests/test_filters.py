"""
Test that various filters are fired for models/views in the student app.
"""
from django.conf import settings
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CourseEnrollmentStarted, CourseUnenrollmentStarted, DashboardRenderStarted
from rest_framework import status

from common.djangoapps.student.models import CourseEnrollment, EnrollmentNotAllowed, UnenrollmentNotAllowed
from common.djangoapps.student.tests.factories import UserFactory, UserProfileFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestEnrollmentPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user, course_key, mode):  # pylint: disable=arguments-differ
        """Pipeline steps that changes mode to honor."""
        if mode == "no-id-professional":
            raise CourseEnrollmentStarted.PreventEnrollment()
        return {"mode": "honor"}


class TestUnenrollmentPipelineStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, enrollment):  # pylint: disable=arguments-differ
        """Pipeline steps that modifies user's profile before unenrolling."""
        if enrollment.mode == "no-id-professional":
            raise CourseUnenrollmentStarted.PreventUnenrollment(
                "You can't un-enroll from this site."
            )

        enrollment.user.profile.set_meta({"unenrolled_from": str(enrollment.course_id)})
        enrollment.user.profile.save()
        return {}


class TestDashboardRenderPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that modifies dashboard data."""
        context["course_enrollments"] = []
        return {
            "context": context,
            "template_name": template_name,
        }


class TestRenderInvalidDashboard(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that stops the dashboard render process."""
        raise DashboardRenderStarted.RenderInvalidDashboard(
            "You can't render this sites dashboard.",
            dashboard_template="static_templates/server-error.html"
        )


class TestRedirectDashboardPageStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that redirects before the dashboard is rendered."""
        raise DashboardRenderStarted.RedirectToPage(
            "You can't see this site's dashboard, redirecting to the correct location.",
            redirect_to="https://custom-dashboard.com",
        )


class TestRedirectToAccSettingsPage(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that redirects to account settings before the dashboard is rendered."""
        raise DashboardRenderStarted.RedirectToPage(
            "You can't see this site's dashboard, redirecting to the correct location.",
        )


class TestRenderCustomResponse(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that changes dashboard view response before the dashboard is rendered."""
        response = HttpResponse("This is a custom response.")
        raise DashboardRenderStarted.RenderCustomResponse(
            "You can't see this site's dashboard.",
            response=response,
        )


@skip_unless_lms
class EnrollmentFiltersTest(ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the enrollment process through the enroll method.

    This class guarantees that the following filters are triggered during the user's enrollment:

    - CourseEnrollmentStarted
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(
            username="test",
            email="test@example.com",
            password="password",
        )
        self.user_profile = UserProfileFactory.create(user=self.user, name="Test Example")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.enrollment.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestEnrollmentPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_enrollment_filter_executed(self):
        """
        Test whether the student enrollment filter is triggered before the user's
        enrollment process.

        Expected result:
            - CourseEnrollmentStarted is triggered and executes TestEnrollmentPipelineStep.
            - The arguments that the receiver gets are the arguments used by the filter
            with the enrollment mode changed.
        """
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, mode='audit')

        self.assertEqual('honor', enrollment.mode)  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.enrollment.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestEnrollmentPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_enrollment_filter_prevent_enroll(self):
        """
        Test prevent the user's enrollment through a pipeline step.

        Expected result:
            - CourseEnrollmentStarted is triggered and executes TestEnrollmentPipelineStep.
            - The user can't enroll.
        """
        with self.assertRaises(EnrollmentNotAllowed):  # noqa: PT027
            CourseEnrollment.enroll(self.user, self.course.id, mode='no-id-professional')

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_enrollment_without_filter_configuration(self):
        """
        Test usual enrollment process, without filter's intervention.

        Expected result:
            - CourseEnrollmentStarted does not have any effect on the enrollment process.
            - The enrollment process ends successfully.
        """
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, mode='audit')

        self.assertEqual('audit', enrollment.mode)  # noqa: PT009
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, self.course.id))  # noqa: PT009


@skip_unless_lms
class UnenrollmentFiltersTest(ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the unenrollment process through the unenroll method.

    This class guarantees that the following filters are triggered during the user's unenrollment:

    - CourseUnenrollmentStarted
    """

    USERNAME = "test"
    EMAIL = "test@example.com"
    PASSWORD = "password"

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.unenrollment.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestUnenrollmentPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_unenrollment_filter_executed(self):
        """
        Test whether the student unenrollment filter is triggered before the user's
        unenrollment process.

        Expected result:
            - CourseUnenrollmentStarted is triggered and executes TestUnenrollmentPipelineStep.
            - The user's profile has unenrolled_from in its meta field.
        """
        CourseEnrollment.enroll(self.user, self.course.id, mode="audit")

        CourseEnrollment.unenroll(self.user, self.course.id)

        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course.id))  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.unenrollment.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestUnenrollmentPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_unenrollment_filter_prevent_unenroll(self):
        """
        Test prevent the user's unenrollment through a pipeline step.

        Expected result:
            - CourseUnenrollmentStarted is triggered and executes TestUnenrollmentPipelineStep.
            - The user can't unenroll.
        """
        CourseEnrollment.enroll(self.user, self.course.id, mode="no-id-professional")

        with self.assertRaises(UnenrollmentNotAllowed):  # noqa: PT027
            CourseEnrollment.unenroll(self.user, self.course.id)

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_unenrollment_without_filter_configuration(self):
        """
        Test usual unenrollment process without filter's intervention.

        Expected result:
            - CourseUnenrollmentStarted does not have any effect on the unenrollment process.
            - The unenrollment process ends successfully.
        """
        CourseEnrollment.enroll(self.user, self.course.id, mode="audit")

        CourseEnrollment.unenroll(self.user, self.course.id)

        self.assertFalse(CourseEnrollment.is_enrolled(self.user, self.course.id))  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.unenrollment.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestUnenrollmentPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_unenrollment_blocked_by_filter(self):
        """
        Test cannot unenroll using change_enrollment view course when UnenrollmentNotAllowed is
        raised by unenroll method.

        Expected result:
            - CourseUnenrollmentStarted does not have any effect on the unenrollment process.
            - The unenrollment process ends successfully.
        """
        CourseEnrollment.enroll(self.user, self.course.id, mode="no-id-professional")
        params = {
            "enrollment_action": "unenroll",
            "course_id": str(self.course.id)
        }

        response = self.client.post(reverse("change_enrollment"), params)

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)  # noqa: PT009
        self.assertEqual("You can't un-enroll from this site.", response.content.decode("utf-8"))  # noqa: PT009


@skip_unless_lms
class StudentDashboardFiltersTest(ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the dashboard rendering process.

    This class guarantees that the following filters are triggered during the students dashboard rendering:
    - DashboardRenderStarted
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.dashboard_url = reverse("dashboard")
        self.first_course = CourseFactory.create(
            org="test1", course="course1", display_name="run1",
        )
        self.second_course = CourseFactory.create(
            org="test2", course="course2", display_name="run1",
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.dashboard.render.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestDashboardRenderPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_render_filter_executed(self):
        """
        Test whether the student dashboard filter is triggered before the user's
        dashboard rendering process.

        Expected result:
            - DashboardRenderStarted is triggered and executes TestDashboardRenderPipelineStep.
            - The dashboard is rendered using the filtered enrollments list.
        """
        CourseEnrollment.enroll(self.user, self.first_course.id)
        CourseEnrollment.enroll(self.user, self.second_course.id)

        response = self.client.get(self.dashboard_url)

        self.assertNotContains(response, self.first_course.id)
        self.assertNotContains(response, self.second_course.id)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.dashboard.render.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestRenderInvalidDashboard",
                ],
                "fail_silently": False,
            },
        },
        PLATFORM_NAME="My site",
    )
    def test_dashboard_render_invalid(self):
        """
        Test rendering an invalid template after catching PreventDashboardRender exception.

        Expected result:
            - DashboardRenderStarted is triggered and executes TestRenderInvalidDashboard.
            - The server error template is rendered instead of the usual dashboard.
        """
        response = self.client.get(self.dashboard_url)

        self.assertContains(response, "There has been a 500 error on the <em>My site</em> servers")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.dashboard.render.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestRedirectDashboardPageStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_redirect(self):
        """
        Test redirecting to a new page after catching RedirectDashboardPage exception.

        Expected result:
            - DashboardRenderStarted is triggered and executes TestRedirectDashboardPageStep.
            - The view response is a redirection.
            - The redirection url is the custom dashboard specified in the filter.
        """
        response = self.client.get(self.dashboard_url)

        self.assertEqual(status.HTTP_302_FOUND, response.status_code)  # noqa: PT009
        self.assertEqual("https://custom-dashboard.com", response.url)  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.dashboard.render.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestRedirectToAccSettingsPage",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_redirect_account_settings(self):
        """
        Test redirecting to the account settings page after catching RedirectDashboardPage exception.

        Expected result:
            - DashboardRenderStarted is triggered and executes TestRedirectToAccSettingsPage.
            - The view response is a redirection.
            - The redirection url is the account settings (as the default when not specifying one).
        """
        response = self.client.get(self.dashboard_url)

        self.assertEqual(status.HTTP_302_FOUND, response.status_code)  # noqa: PT009
        self.assertEqual(settings.ACCOUNT_MICROFRONTEND_URL, response.url)  # noqa: PT009

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.dashboard.render.started.v1": {
                "pipeline": [
                    "common.djangoapps.student.tests.test_filters.TestRenderCustomResponse",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_custom_response(self):
        """
        Test returning a custom response after catching RenderCustomResponse exception.

        Expected result:
            - DashboardRenderStarted is triggered and executes TestRenderCustomResponse.
            - The view response contains the custom response text.
        """
        response = self.client.get(self.dashboard_url)

        self.assertEqual("This is a custom response.", response.content.decode("utf-8"))  # noqa: PT009

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_dashboard_render_without_filter_config(self):
        """
        Test whether the student dashboard filter is triggered before the user's
        dashboard rendering process without any modification in the app flow.

        Expected result:
            - DashboardRenderStarted executes a noop (empty pipeline).
            - The view response is HTTP_200_OK.
            - There's no modification in the dashboard.
        """
        CourseEnrollment.enroll(self.user, self.first_course.id)
        CourseEnrollment.enroll(self.user, self.second_course.id)

        response = self.client.get(self.dashboard_url)

        self.assertContains(response, self.first_course.id)
        self.assertContains(response, self.second_course.id)
