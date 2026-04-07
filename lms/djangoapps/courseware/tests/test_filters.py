"""
Test that various filters are fired for courseware views.
"""
from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CourseAboutRenderStarted
from rest_framework import status

from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestRenderInvalidCourseAbout(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """
        Pipeline step that stops the course about render process.

        When raising PreventCourseAboutRender, this filter overrides the course about
        template name so the view renders a module-error instead.
        """
        raise CourseAboutRenderStarted.RenderInvalidCourseAbout(
            "You can't access the courses about page.",
            course_about_template="module-error.html",
            template_context=context,
        )


class TestRedirectToPage(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """
        Pipeline step that redirects to the course survey.

        When raising RedirectToPage, this filter uses a redirect_to field handled by
        the course about view that redirects to that URL.
        """
        course_key = str(context.get("course").id)
        raise CourseAboutRenderStarted.RedirectToPage(
            "You can't access this courses about page, redirecting to the correct location.",
            redirect_to=f"courses/{course_key}/survey",
        )


class TestRedirectToDefaultPage(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """
        Pipeline step that redirects to the default page when redirect_to is not specified.

        When raising RedirectToPage, this filter uses a redirect_to field handled by
        the course about view that redirects to that URL.
        """
        course_key = str(context.get("course").id)
        raise CourseAboutRenderStarted.RedirectToPage(
            "You can't access this courses about page, redirecting to the correct location.",
        )


class TestRenderCustomResponse(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """
        Pipeline step that redirects to the course survey.

        When raising RenderCustomResponse, this filter uses a redirect_to field handled by
        the course about view that redirects to that URL.
        """
        response = HttpResponse("Here's the text of the web page.")

        raise CourseAboutRenderStarted.RenderCustomResponse(
            "You can't access this courses home page.",
            response=response,
        )


class TestCourseAboutRender(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, context, template_name):  # pylint: disable=arguments-differ
        """Pipeline that gives staff view to the current user."""
        context["staff_access"] = True
        context["studio_url"] = "http://madeup-studio.com"
        return {
            "context": context, template_name: template_name,
        }


@skip_unless_lms
class CourseAboutFiltersTest(ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the course about rendering process.

    This class guarantees that the following filters are triggered during the course about rendering:

    - CourseAboutRenderStarted
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create()
        self.course_about_url = reverse(
            "about_course",
            kwargs={
                "course_id": self.course.id,
            }
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_about.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.courseware.tests.test_filters.TestCourseAboutRender",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_course_about_render_filter_executed(self):
        """
        Test whether the course about filter is triggered before the course about view
        renders.

        Expected result:
            - CourseAboutRenderStarted is triggered and executes TestCourseAboutRender.
            - The course about renders with View About Page in studio.
        """
        response = self.client.get(self.course_about_url)

        self.assertContains(response, "View About Page in studio", status_code=200)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_about.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.courseware.tests.test_filters.TestRenderInvalidCourseAbout",
                ],
                "fail_silently": False,
            },
        },
        PLATFORM_NAME="My site",
    )
    def test_course_about_render_alternative(self):
        """
        Test rendering an error template after catching PreventCourseAboutRender exception.

        Expected result:
            - CourseAboutRenderStarted is triggered and executes TestRenderInvalidCourseAbout.
            - The module-error template is rendered instead of the usual course about.
        """
        response = self.client.get(self.course_about_url)

        self.assertContains(response, "There has been an error on the <em>My site</em> servers")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_about.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.courseware.tests.test_filters.TestRedirectToPage",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_course_about_redirect(self):
        """
        Test redirecting to a new page after catching RedirectCourseAboutPage exception.

        Expected result:
            - CourseAboutRenderStarted is triggered and executes TestRedirectToPage.
            - The view response is a redirection.
        """
        response = self.client.get(self.course_about_url)

        self.assertEqual(status.HTTP_302_FOUND, response.status_code)
        self.assertEqual(f"courses/{self.course.id}/survey", response.url)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_about.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.courseware.tests.test_filters.TestRedirectToDefaultPage",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_course_about_redirect_default(self):
        """
        Test redirecting to the default page after catching RedirectCourseAboutPage exception.

        Expected result:
            - CourseAboutRenderStarted is triggered and executes TestRedirectToPage.
            - The view response is a redirection.
        """
        response = self.client.get(self.course_about_url)

        self.assertEqual(status.HTTP_302_FOUND, response.status_code)
        self.assertEqual(f"{reverse('dashboard')}", response.url)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_about.render.started.v1": {
                "pipeline": [
                    "lms.djangoapps.courseware.tests.test_filters.TestRenderCustomResponse",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_course_about_custom_response(self):
        """
        Test redirecting to a new page after catching RenderCustomResponse exception.

        Expected result:
            - CourseAboutRenderStarted is triggered and executes TestRenderCustomResponse.
            - The view response is a redirection.
        """
        response = self.client.get(self.course_about_url)

        self.assertContains(response, "Here's the text of the web page.")

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_course_about_render_without_filter_config(self):
        """
        Test whether the course about filter is triggered before the course about
        render without affecting its execution flow.

        Expected result:
            - CourseAboutRenderStarted executes a noop (empty pipeline). Without any
            modification comparing it with the effects of TestCourseAboutRender.
            - The view response is HTTP_200_OK.
        """
        response = self.client.get(self.course_about_url)

        self.assertNotContains(response, "View About Page in studio", status_code=200)
