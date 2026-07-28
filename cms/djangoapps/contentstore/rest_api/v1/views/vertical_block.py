""" API Views for unit page """

import logging

import edx_api_doc_tools as apidocs
from django.http import HttpResponseBadRequest, HttpResponsePermanentRedirect
from django.urls import reverse
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from cms.djangoapps.contentstore.rest_api.v1.mixins import ContainerHandlerMixin
from cms.djangoapps.contentstore.rest_api.v1.serializers import ContainerHandlerSerializer
from cms.djangoapps.contentstore.utils import get_container_handler_context
from cms.djangoapps.contentstore.views.component import _get_item_in_course
from openedx.core.lib.api.view_utils import view_auth_classes
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@view_auth_classes(is_authenticated=True)
class ContainerHandlerView(APIView, ContainerHandlerMixin):
    """
    View for container xblock requests to get vertical data.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "usage_key_string",
                apidocs.ParameterLocation.PATH,
                description="Container usage key",
            ),
        ],
        responses={
            200: ContainerHandlerSerializer,
            401: "The requester is not authenticated.",
            404: "The requested locator does not exist.",
        },
    )
    def get(self, request: Request, usage_key_string: str):
        """
        Get an object containing vertical data.

        **Example Request**

            GET /api/contentstore/v1/container_handler/{usage_key_string}

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the vertical's container data.

        **Example Response**

        ```json
        {
            "language_code": "zh-cn",
            "action": "view",
            "xblock": {
                "display_name": "Labs and Demos",
                "display_type": "单元",
                "category": "vertical"
            },
            "is_unit_page": true,
            "is_collapsible": false,
            "position": 1,
            "prev_url": "block-v1-edX%2BDemo_Course%2Btype%40vertical%2Bblock%404e592689563243c484",
            "next_url": "block-v1%3AedX%2BDemoX%2BDemo_Course%2Btype%40vertical%2Bblock%40vertical_aae927868e55",
            "new_unit_category": "vertical",
            "outline_url": "/course/course-v1:edX+DemoX+Demo_Course?format=concise",
            "ancestor_xblocks": [
                {
                "children": [
                    {
                    "url": "/course/course-v1:edX+DemoX+Demo_Course?show=block-v1%3AedX%2BDemoX%2BDemo_Course%2Btype%",
                    "display_name": "Introduction",
                    "usage_key": "subsection_id"
                    },
                    ...
                ],
                "title": "Example Week 2: Get Interactive",
                "is_last": false
                },
                ...
            ],
            "component_templates": [
                {
                "type": "advanced",
                "templates": [
                    {
                    "display_name": "批注",
                    "category": "annotatable",
                    "boilerplate_name": null,
                    "hinted": false,
                    "tab": "common",
                    "support_level": true
                    },
                    ...
                },
                ...
            ],
            "xblock_info": {},
            "draft_preview_link": "//preview.localhost:18000/courses/course-v1:edX+DemoX+Demo_Course/...",
            "published_preview_link": "///courses/course-v1:edX+DemoX+Demo_Course/jump_to/...",
            "show_unit_tags": false,
            "user_clipboard": {
                "content": null,
                "source_usage_key": "",
                "source_context_title": "",
                "source_edit_url": ""
            },
            "is_fullwidth_content": false,
            "assets_url": "/assets/course-v1:edX+DemoX+Demo_Course/",
            "unit_block_id": "d6cee45205a449369d7ef8f159b22bdf",
            "subsection_location": "block-v1:edX+DemoX+Demo_Course+type@sequential+block@graded_simulations"
            "course_sequence_ids": [
                "block-v1:edX+DemoX+Demo_Course+type@sequential+block@graded_simulations",
                "block-v1:edX+DemoX+Demo_Course+type@sequential+block@something_else",
                ...
            ],
        }
        ```
        """
        usage_key = self.get_object(usage_key_string)
        course_key = usage_key.course_key
        with modulestore().bulk_operations(course_key):
            try:
                course, xblock, lms_link, preview_lms_link = _get_item_in_course(request, usage_key)
            except ItemNotFoundError:
                return HttpResponseBadRequest()

            context = get_container_handler_context(request, usage_key, course, xblock)
            context.update({
                'draft_preview_link': preview_lms_link,
                'published_preview_link': lms_link,
            })
            serializer = ContainerHandlerSerializer(context)
            return Response(serializer.data)


def vertical_container_children_redirect_view(request: Request, usage_key_string: str):
    """
    Redirects GET requests to container_children url.

    This view will return a 301 Moved Permanently response to the provided
    usage_key string and automatically redirect the browser to the correct URL.
    """
    redirect_location = reverse(
        'cms.djangoapps.contentstore:v1:container_children',
        kwargs={'usage_key_string': usage_key_string},
    )
    http_response = HttpResponsePermanentRedirect(redirect_location)
    return http_response
