"""HomePageCoursesViewV2 APIView for getting content available to the logged in user."""

import edx_api_doc_tools as apidocs
from collections import OrderedDict
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from openedx.core.lib.api.view_utils import view_auth_classes

from cms.djangoapps.contentstore.utils import get_course_context_v2
from cms.djangoapps.contentstore.rest_api.v2.serializers import CourseHomeTabSerializerV2


class HomePageCoursesPaginator(PageNumberPagination):
    """Custom paginator for the home page courses view version 2."""
    page_size_query_param = 'page_size'

    def get_paginated_response(self, data):
        """Return a paginated style `Response` object for the given output data."""
        return Response(OrderedDict([
            ('count', self.page.paginator.count),
            ('num_pages', self.page.paginator.num_pages),
            ('next', self.get_next_link()),
            ('previous', self.get_previous_link()),
            ('results', data),
        ]))

    def paginate_queryset(self, queryset, request, view=None):
        """
        Paginate a queryset if required, either returning a page object,
        or `None` if pagination is not configured for this view.

        This method is a modified version of the original `paginate_queryset` method
        from the `PageNumberPagination` class. The original method was modified to
        handle the case where the `queryset` is a `filter` object.
        """
        if isinstance(queryset, filter):
            queryset = list(queryset)

        return super().paginate_queryset(queryset, request, view)


@view_auth_classes(is_authenticated=True)
class HomePageCoursesViewV2(APIView):
    """View for getting all courses available to the logged in user."""

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "org",
                apidocs.ParameterLocation.QUERY,
                description="Query param to filter by course org",
            ),
            apidocs.string_parameter(
                "search",
                apidocs.ParameterLocation.QUERY,
                description="Query param to filter by course name, org, or number",
            ),
            apidocs.string_parameter(
                "order",
                apidocs.ParameterLocation.QUERY,
                description="Query param to order by course name, org, or number",
            ),
            apidocs.string_parameter(
                "active_only",
                apidocs.ParameterLocation.QUERY,
                description="Query param to filter by active courses only",
            ),
            apidocs.string_parameter(
                "archived_only",
                apidocs.ParameterLocation.QUERY,
                description="Query param to filter by archived courses only",
            ),
            apidocs.string_parameter(
                "page",
                apidocs.ParameterLocation.QUERY,
                description="Query param to paginate the courses",
            ),
            apidocs.string_parameter(
                "page_size",
                apidocs.ParameterLocation.QUERY,
                description="Query param to set page size",
            ),
        ],
        responses={
            200: CourseHomeTabSerializerV2,
            401: "The requester is not authenticated.",
        },
    )
    def get(self, request: Request):
        """
        Get an object containing all courses.

        **Example Request**

            GET /api/contentstore/v2/home/courses
            GET /api/contentstore/v2/home/courses?org=edX
            GET /api/contentstore/v2/home/courses?search=E2E
            GET /api/contentstore/v2/home/courses?order=-org
            GET /api/contentstore/v2/home/courses?active_only=true
            GET /api/contentstore/v2/home/courses?archived_only=true
            GET /api/contentstore/v2/home/courses?page=2
            GET /api/contentstore/v2/home/courses?page_size=20

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's home.

        **Example Response**

        ```json
        {
            "courses": [
                 {
                    "course_key": "course-v1:edX+E2E-101+course",
                    "display_name": "E2E Test Course",
                    "lms_link": "//localhost:18000/courses/course-v1:edX+E2E-101+course",
                    "cms_link": "//localhost:18010/course/course-v1:edX+E2E-101+course",
                    "number": "E2E-101",
                    "org": "edX",
                    "rerun_link": "/course_rerun/course-v1:edX+E2E-101+course",
                    "run": "course",
                    "url": "/course/course-v1:edX+E2E-101+course",
                    "is_active": true
                },
            ],
            "in_process_course_actions": [],
        }
        ```
        """
        courses, in_process_course_actions = get_course_context_v2(request)
        paginator = HomePageCoursesPaginator()
        courses_page = paginator.paginate_queryset(
            courses,
            self.request,
            view=self
        )
        serializer = CourseHomeTabSerializerV2({
            'courses': courses_page,
            'in_process_course_actions': in_process_course_actions,
        })
        return paginator.get_paginated_response(serializer.data)
