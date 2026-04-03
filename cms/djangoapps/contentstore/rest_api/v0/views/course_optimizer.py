"""API Views for Course Optimizer."""

import edx_api_doc_tools as apidocs
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from user_tasks.models import UserTaskStatus

from cms.djangoapps.contentstore.core.course_optimizer_provider import (
    get_course_link_update_data,
    get_link_check_data,
    sort_course_sections
)
from cms.djangoapps.contentstore.rest_api.v0.serializers.course_optimizer import (
    CourseRerunLinkUpdateRequestSerializer,
    CourseRerunLinkUpdateStatusSerializer,
    LinkCheckSerializer
)
from cms.djangoapps.contentstore.tasks import check_broken_links, update_course_rerun_links
from cms.djangoapps.contentstore.toggles import enable_course_optimizer_check_prev_run_links
from common.djangoapps.student.auth import has_course_author_access, has_studio_read_access
from common.djangoapps.util.json_request import JsonResponse
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes


@view_auth_classes(is_authenticated=True)
class LinkCheckView(DeveloperErrorViewMixin, APIView):
    """
    View for queueing a celery task to scan a course for broken links.
    """
    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: "Celery task queued.",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def post(self, request: Request, course_id: str):
        """
        Queue celery task to scan a course for broken links.

        **Example Request**
            POST /api/contentstore/v0/link_check/{course_id}

        **Response Values**
        ```json
        {
            "LinkCheckStatus": "Pending"
        }
        """
        course_key = CourseKey.from_string(course_id)

        if not has_studio_read_access(request.user, course_key):
            self.permission_denied(request)

        check_broken_links.delay(request.user.id, course_id, request.LANGUAGE_CODE)
        return JsonResponse({'LinkCheckStatus': UserTaskStatus.PENDING})


@view_auth_classes()
class LinkCheckStatusView(DeveloperErrorViewMixin, APIView):
    """
    View for checking the status of the celery task and returning the results.
    """
    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
        ],
        responses={
            200: "OK",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request: Request, course_id: str):
        """
        **Use Case**

            GET handler to return the status of the link_check task from UserTaskStatus.
            If no task has been started for the course, return 'Uninitiated'.
            If link_check task was successful, an output result is also returned.

            For reference, the following status are in UserTaskStatus:
                'Pending', 'In Progress' (sent to frontend as 'In-Progress'),
                'Succeeded', 'Failed', 'Canceled', 'Retrying'
            This function adds a status for when status from UserTaskStatus is None:
                'Uninitiated'

        **Example Request**

            GET /api/contentstore/v0/link_check_status/{course_id}

        **Example Response**

        ```json
        {
            "LinkCheckStatus": "Succeeded",
            "LinkCheckCreatedAt": "2025-02-05T14:32:01.294587Z",
            "LinkCheckOutput": {
                "sections": [
                    {
                        "id": <string>,
                        "displayName": <string>,
                        "subsections": [
                            {
                                "id": <string>,
                                "displayName": <string>,
                                "units": [
                                    {
                                        "id": <string>,
                                        "displayName": <string>,
                                        "blocks": [
                                            {
                                                "id": <string>,
                                                "url": <string>,
                                                "brokenLinks": [<string>, ...],
                                                "lockedLinks": [<string>, ...],
                                                "externalForbiddenLinks": [<string>, ...],
                                                "previousRunLinks": [
                                                    {
                                                        "originalLink": <string>,
                                                        "isUpdated": <boolean>,
                                                        "updatedLink": <string>
                                                    },
                                                    ...
                                                ]
                                            },
                                            { <another block> },
                                        ],
                                    },
                                    { <another unit> },
                                ],
                            },
                            { <another subsection },
                        ],
                    },
                    { <another section> },
                ],
                "course_updates": [
                    {
                        "id": <string>,
                        "displayName": <string>,
                        "url": <string>,
                        "brokenLinks": [<string>, ...],
                        "lockedLinks": [<string>, ...],
                        "externalForbiddenLinks": [<string>, ...],
                        "previousRunLinks": [
                            {
                                "originalLink": <string>,
                                "isUpdated": <boolean>,
                                "updatedLink": <string>
                            },
                            ...
                        ]
                    },
                    ...,
                    { <another course-updates> },
                    ...,
                    {
                        "id": <string>,
                        "displayName": "handouts",
                        "url": <string>,
                        "brokenLinks": [<string>, ...],
                        "lockedLinks": [<string>, ...],
                        "externalForbiddenLinks": [<string>, ...],
                        "previousRunLinks": [
                            {
                                "originalLink": <string>,
                                "isUpdated": <boolean>,
                                "updatedLink": <string>
                            },
                            ...
                        ]
                    }
                ],
                "custom_pages": [
                    {
                        "id": <string>,
                        "displayName": <string>,
                        "url": <string>,
                        "brokenLinks": [<string>, ...],
                        "lockedLinks": [<string>, ...],
                        "externalForbiddenLinks": [<string>, ...],
                        "previousRunLinks": [
                            {
                                "originalLink": <string>,
                                "isUpdated": <boolean>,
                                "updatedLink": <string>
                            },
                            ...
                        ]
                    },
                    ...,
                    { <another page> },
                ]
            },
        }
        """
        course_key = CourseKey.from_string(course_id)
        if not has_course_author_access(request.user, course_key):
            self.permission_denied(request)

        link_check_data = get_link_check_data(request, course_id)
        sorted_sections = sort_course_sections(course_key, link_check_data)

        serializer = LinkCheckSerializer(sorted_sections)
        return Response(serializer.data)


@view_auth_classes(is_authenticated=True)
class RerunLinkUpdateView(DeveloperErrorViewMixin, APIView):
    """
    View for queueing a celery task to update course links to the latest re-run.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id", apidocs.ParameterLocation.PATH, description="Course ID"
            )
        ],
        body=CourseRerunLinkUpdateRequestSerializer,
        responses={
            200: "Celery task queued.",
            400: "Bad request - invalid action or missing data.",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def post(self, request: Request, course_id: str):
        """
        Queue celery task to update course links to the latest re-run.

        **Example Request - Update All Links**
            POST /api/contentstore/v0/rerun_link_update/{course_id}
            ```json
            {
                "action": "all"
            }
            ```

        **Example Request - Update Single Links**
            POST /api/contentstore/v0/rerun_link_update/{course_id}
            ```json
            {
                "action": "single",
                "data": [
                    {
                        "url": "http://localhost:18000/course/course-v1:edX+DemoX+Demo_Course/course",
                        "type": "course_updates",
                        "id": "block_id_123"
                    }
                ]
            }
            ```

        **Response Values**
        ```json
        {
            "status": "pending"
        }
        ```
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except (InvalidKeyError, IndexError):
            return JsonResponse(
                {"error": "Invalid course id, it does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check course author permissions
        if not has_course_author_access(request.user, course_key):
            self.permission_denied(request)

        if not enable_course_optimizer_check_prev_run_links(course_key):
            return JsonResponse(
                {
                    "error": "Course optimizer check for previous run links is not enabled."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = request.data.get("action")
        if not action or action not in ["all", "single"]:
            return JsonResponse(
                {"error": 'Invalid or missing action. Must be "all" or "single".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action == "single":
            data = request.data.get("data")
            if not data or not isinstance(data, list):
                return JsonResponse(
                    {
                        'data': "This field is required when action is 'single'."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        update_course_rerun_links.delay(
            request.user.id,
            course_id,
            action,
            request.data.get("data", []),
            request.LANGUAGE_CODE,
        )

        return JsonResponse({"status": UserTaskStatus.PENDING})


@view_auth_classes()
class RerunLinkUpdateStatusView(DeveloperErrorViewMixin, APIView):
    """
    View for checking the status of the course link update task and returning the results.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "course_id", apidocs.ParameterLocation.PATH, description="Course ID"
            ),
        ],
        responses={
            200: "OK",
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    def get(self, request: Request, course_id: str):
        """
        **Use Case**

            GET handler to return the status of the course link update task from UserTaskStatus.
            If no task has been started for the course, return 'uninitiated'.
            If the task was successful, the updated links results are also returned.

            Possible statuses:
                'pending', 'in_progress', 'completed', 'failed', 'uninitiated'

        **Example Request**

            GET /api/contentstore/v0/rerun_link_update_status/{course_id}

        **Example Response - Task In Progress**

        ```json
        {
            "status": "pending"
        }
        ```

        **Example Response - Task Completed**

        ```json
        {
            "status": "completed",
            "results": [
                {
                    "id": "block_id_123",
                    "type": "course_updates",
                    "new_url": "http://localhost:18000/course/course-v1:edX+DemoX+2024_Q2/course",
                    "success": true
                },
                {
                    "id": "block_id_456",
                    "type": "course_updates",
                    "new_url": "http://localhost:18000/course/course-v1:edX+DemoX+2024_Q2/progress",
                    "success": true
                }
            ]
        }
        ```

        **Example Response - Task Failed**

        ```json
        {
            "status": "failed",
            "error": "Target course run not found or inaccessible"
        }
        ```
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except (InvalidKeyError, IndexError):
            return JsonResponse(
                {"error": "Invalid course id, it does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check course author permissions
        if not has_course_author_access(request.user, course_key):
            self.permission_denied(request)

        if not enable_course_optimizer_check_prev_run_links(course_key):
            return JsonResponse(
                {
                    "error": "Course optimizer check for previous run links is not enabled."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = get_course_link_update_data(request, course_id)
        serializer = CourseRerunLinkUpdateStatusSerializer(data)
        return Response(serializer.data)
