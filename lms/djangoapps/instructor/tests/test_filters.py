"""
Test that various filters are fired for models/views in the instructor app.
"""
import re

from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import InstructorDashboardRenderStarted
from rest_framework import status

from common.djangoapps.student.tests.factories import AdminFactory, CourseAccessRoleFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestDashboardRenderPipelineStep(PipelineStep):
    """
    Pipeline step for testing the instructor dashboard rendering process.

    This step is used to modify the dashboard data before it's rendered emptying
    the sections list.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that modifies dashboard data."""
        context["sections"] = []
        return {
            "context": context,
            "template_name": template_name,
        }


class TestRenderInvalidDashboard(PipelineStep):
    """
    Pipeline step for testing the instructor dashboard rendering process.

    This step is used to modify the dashboard data before it's rendered by raising
    an exception which will be caught by the instructor dashboard filter, rendering
    a different template.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that stops the dashboard render process."""
        raise InstructorDashboardRenderStarted.RenderInvalidDashboard(
            "You can't render this dashboard.",
            instructor_template="static_templates/server-error.html"
        )


class TestRedirectDashboardPageStep(PipelineStep):
    """
    Pipeline step for testing the instructor dashboard rendering process.

    This step is used to modify the dashboard data before it's rendered by raising
    an exception which will be caught by the instructor dashboard filter and redirect
    to a new page.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that redirects before the dashboard is rendered."""
        raise InstructorDashboardRenderStarted.RedirectToPage(
            "You can't see this site's instructor dashboard, redirecting to the correct location.",
            redirect_to="https://custom-dashboard.com",
        )


class TestRenderCustomResponse(PipelineStep):
    """
    Pipeline step for testing the instructor dashboard rendering process.

    This step is used to modify the dashboard data before it's rendered by raising
    an exception which will be caught by the instructor dashboard filter, returning
    a custom response.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline step that changes dashboard view response before the dashboard is rendered."""
        response = HttpResponse("This is a custom response.")
        raise InstructorDashboardRenderStarted.RenderCustomResponse(
            "You can't see this site's dashboard.",
            response=response,
        )


@skip_unless_lms
class InstructorDashboardFiltersTest(ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the instructor dashboard rendering process.

    This class guarantees that the following filters are triggered during the instructor dashboard rendering:
    - InstructorDashboardRenderStarted
    """

    def setUp(self):  # pylint: disable=arguments-differ
        """
        Setup the test suite.
        """
        super().setUp()
        self.instructor = AdminFactory.create()
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)
        self.course = CourseFactory.create(
            org="test1", course="course1", display_name="run1",
        )
        self.dashboard_url = reverse("instructor_dashboard", kwargs={"course_id": str(self.course.id)})
        CourseAccessRoleFactory(
            course_id=self.course.id,
            user=self.instructor,
            role="instructor",
            org=self.course.id.org
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.instructor.dashboard.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.instructor.tests.test_filters.TestDashboardRenderPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_render_filter_executed(self):
        """
        Test whether the instructor dashboard filter is triggered before the instructor's
        dashboard rendering process.

        Expected result:
            - InstructorDashboardRenderStarted is triggered and executes TestDashboardRenderPipelineStep.
            - The dashboard is rendered using the empty sections list.
        """
        response = self.client.get(self.dashboard_url)

        matches = re.findall(
            rb'<li class="nav-item"><button type="button" class="btn-link .*" data-section=".*">.*',
            response.content
        )

        self.assertFalse(matches)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.instructor.dashboard.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.instructor.tests.test_filters.TestRenderInvalidDashboard",
                ],
                "fail_silently": False,
            },
        },
        PLATFORM_NAME="My site",
    )
    def test_dashboard_render_invalid(self):
        """
        Test rendering an invalid template after catching RenderInvalidDashboard exception.

        Expected result:
            - InstructorDashboardRenderStarted is triggered and executes TestRenderInvalidDashboard.
            - The server error template is rendered instead of the usual dashboard.
        """
        response = self.client.get(self.dashboard_url)

        self.assertContains(response, "There has been a 500 error on the <em>My site</em> servers")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.instructor.dashboard.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.instructor.tests.test_filters.TestRedirectDashboardPageStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_redirect(self):
        """
        Test redirecting to a new page after catching RedirectToPage exception.

        Expected result:
            - InstructorDashboardRenderStarted is triggered and executes TestRedirectDashboardPageStep.
            - The view response is a redirection.
            - The redirection url is the custom dashboard specified in the filter.
        """
        response = self.client.get(self.dashboard_url)

        self.assertEqual(status.HTTP_302_FOUND, response.status_code)
        self.assertEqual("https://custom-dashboard.com", response.url)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.instructor.dashboard.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.instructor.tests.test_filters.TestRenderCustomResponse",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_dashboard_custom_response(self):
        """
        Test returning a custom response after catching RenderCustomResponse exception.

        Expected result:
            - InstructorDashboardRenderStarted is triggered and executes TestRenderCustomResponse.
            - The view response contains the custom response text.
        """
        response = self.client.get(self.dashboard_url)

        self.assertEqual("This is a custom response.", response.content.decode("utf-8"))

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_dashboard_render_without_filter_config(self):
        """
        Test whether the student dashboard filter is triggered before the user's
        dashboard rendering process without any modification in the app flow.

        Expected result:
            - InstructorDashboardRenderStarted executes a noop (empty pipeline).
            - The view response is HTTP_200_OK.
            - There's no modification in the instructor dashboard regarding the sections list.
        """
        response = self.client.get(self.dashboard_url)

        matches = re.findall(
            rb'<li class="nav-item"><button type="button" class="btn-link .*" data-section=".*">.*',
            response.content
        )

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertTrue(matches)
