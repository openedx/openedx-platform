"""API Views for course index"""

import logging

import edx_api_doc_tools as apidocs
from django.conf import settings
from opaque_keys.edx.keys import CourseKey
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.fields import BooleanField

from cms.djangoapps.contentstore.config.waffle import CUSTOM_RELATIVE_DATES
from cms.djangoapps.contentstore.rest_api.v1.mixins import ContainerHandlerMixin
from cms.djangoapps.contentstore.rest_api.v1.serializers import (
    CourseIndexSerializer,
    ContainerChildrenSerializer,
)
from cms.djangoapps.contentstore.utils import (
    get_course_index_context,
    get_user_partition_info,
    get_visibility_partition_info,
    get_xblock_render_error,
    get_xblock_validation_messages,
)
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import get_xblock
from cms.lib.xblock.upstream_sync import UpstreamLink
from common.djangoapps.student.auth import has_studio_read_access
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists, view_auth_classes
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order


@view_auth_classes(is_authenticated=True)
class CourseIndexView(DeveloperErrorViewMixin, APIView):
    """View for Course Index"""

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter("course_id", apidocs.ParameterLocation.PATH, description="Course ID"),
            apidocs.string_parameter(
                "show",
                apidocs.ParameterLocation.QUERY,
                description="Query param to set initial state which fully expanded to see the item",
            )],
        responses={
            200: CourseIndexSerializer,
            401: "The requester is not authenticated.",
            403: "The requester cannot access the specified course.",
            404: "The requested course does not exist.",
        },
    )
    @verify_course_exists()
    def get(self, request: Request, course_id: str):
        """
        Get an object containing course index for outline.

        **Example Request**

            GET /api/contentstore/v1/course_index/{course_id}?show=block-v1:edx+101+y+type@course+block@course

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the course's outline.

        **Example Response**

        ```json
        {
            "course_release_date": "Set Date",
            "course_structure": {},
            "deprecated_blocks_info": {
                "deprecated_enabled_block_types": [],
                "blocks": [],
                "advance_settings_url": "/settings/advanced/course-v1:edx+101+y76"
            },
            "discussions_incontext_feedback_url": "",
            "discussions_incontext_learnmore_url": "",
            "initial_state": {
                "expanded_locators": [
                "block-v1:edx+101+y76+type@chapter+block@03de0adc9d1c4cc097062d80eb04abf6",
                "block-v1:edx+101+y76+type@sequential+block@8a85e287e30a47e98d8c1f37f74a6a9d"
                ],
                "locator_to_show": "block-v1:edx+101+y76+type@chapter+block@03de0adc9d1c4cc097062d80eb04abf6"
            },
            "initial_user_clipboard": {
                "content": null,
                "source_usage_key": "",
                "source_context_title": "",
                "source_edit_url": ""
            },
            "language_code": "en",
            "lms_link": "//localhost:18000/courses/course-v1:edx+101+y76/jump_to/block-v1:edx+101+y76",
            "mfe_proctored_exam_settings_url": "",
            "notification_dismiss_url": "/course_notifications/course-v1:edx+101+y76/2",
            "proctoring_errors": [],
            "reindex_link": "/course/course-v1:edx+101+y76/search_reindex",
            "rerun_notification_id": 2
        }
        ```
        """

        course_key = CourseKey.from_string(course_id)
        if not has_studio_read_access(request.user, course_key):
            self.permission_denied(request)
        course_index_context = get_course_index_context(request, course_key)
        course_index_context.update({
            "discussions_incontext_learnmore_url": settings.DISCUSSIONS_INCONTEXT_LEARNMORE_URL,
            "discussions_incontext_feedback_url": settings.DISCUSSIONS_INCONTEXT_FEEDBACK_URL,
            "is_custom_relative_dates_active": CUSTOM_RELATIVE_DATES.is_enabled(course_key),
        })

        serializer = CourseIndexSerializer(course_index_context)
        return Response(serializer.data)


@view_auth_classes(is_authenticated=True)
class ContainerChildrenView(APIView, ContainerHandlerMixin):
    """
    View for container xblock requests to get state and children data.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "usage_key_string",
                apidocs.ParameterLocation.PATH,
                description="Container usage key",
            ),
            apidocs.string_parameter(
                "get_upstream_info",
                apidocs.ParameterLocation.QUERY,
                description="Gets the info of all ready to sync children",
            ),
        ],
        responses={
            200: ContainerChildrenSerializer,
            401: "The requester is not authenticated.",
            404: "The requested locator does not exist.",
        },
    )
    def get(self, request: Request, usage_key_string: str):
        """
        Get an object containing vertical state with children data.

        **Example Request**

            GET /api/contentstore/v1/container/{usage_key_string}/children

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys that
        are the vertical's container children data.

        **Example Response**

        ```json
        {
            "children": [
                {
                    "name": "Drag and Drop",
                    "block_id": "block-v1:org+101+101+type@drag-and-drop-v2+block@7599275ace6b46f5a482078a2954ca16",
                    "block_type": "drag-and-drop-v2",
                    "user_partition_info": {},
                    "user_partitions": {}
                    "upstream_link": null,
                    "actions": {
                        "can_copy": true,
                        "can_duplicate": true,
                        "can_move": true,
                        "can_manage_access": true,
                        "can_delete": true,
                        "can_manage_tags": true,
                    },
                    "has_validation_error": false,
                    "validation_errors": [],
                },
                {
                    "name": "Video",
                    "block_id": "block-v1:org+101+101+type@video+block@0e3d39b12d7c4345981bda6b3511a9bf",
                    "block_type": "video",
                    "user_partition_info": {},
                    "user_partitions": {}
                    "upstream_link": {
                        "upstream_ref": "lb:org:mylib:video:404",
                        "version_synced": 16
                        "version_available": null,
                        "error_message": "Linked library item not found: lb:org:mylib:video:404",
                        "ready_to_sync": false,
                    },
                    "actions": {
                        "can_copy": true,
                        "can_duplicate": true,
                        "can_move": true,
                        "can_manage_access": true,
                        "can_delete": true,
                        "can_manage_tags": true,
                    }
                    "validation_messages": [],
                    "render_error": "",
                },
                {
                    "name": "Text",
                    "block_id": "block-v1:org+101+101+type@html+block@3e3fa1f88adb4a108cd14e9002143690",
                    "block_type": "html",
                    "user_partition_info": {},
                    "user_partitions": {},
                    "upstream_link": {
                        "upstream_ref": "lb:org:mylib:html:abcd",
                        "version_synced": 43,
                        "version_available": 49,
                        "error_message": null,
                        "ready_to_sync": true,
                        "is_ready_to_sync_individually": true,
                    },
                    "actions": {
                        "can_copy": true,
                        "can_duplicate": true,
                        "can_move": true,
                        "can_manage_access": true,
                        "can_delete": true,
                        "can_manage_tags": true,
                    },
                    "validation_messages": [
                        {
                            "text": "This component's access settings contradict its parent's access settings.",
                            "type": "error"
                        }
                    ],
                    "render_error": "Unterminated control keyword: 'if' in file '../problem.html'",
                },
            ],
            "is_published": false,
            "can_paste_component": true,
            "display_name": "Vertical block 1"
            "upstream_ready_to_sync_children_info": [{
                "name": "Text",
                "upstream": "lb:org:mylib:html:abcd",
                'block_type': "html",
                'downstream_customized': ["display_name"],
                'id': "block-v1:org+101+101+type@html+block@3e3fa1f88adb4a108cd14e9002143690",
            }]
        }
        ```
        """
        usage_key = self.get_object(usage_key_string)
        current_xblock = get_xblock(usage_key, request.user)
        get_upstream_info = BooleanField().to_internal_value(request.GET.get("get_upstream_info", False))
        is_course = current_xblock.scope_ids.usage_id.context_key.is_course

        with modulestore().bulk_operations(usage_key.course_key):
            # load course once to reuse it for user_partitions query
            course = modulestore().get_course(current_xblock.location.course_key)
            children = []
            if current_xblock.has_children:
                for child in current_xblock.children:
                    child_info = modulestore().get_item(child)
                    user_partition_info = get_visibility_partition_info(child_info, course=course)
                    user_partitions = get_user_partition_info(child_info, course=course)
                    upstream_link = UpstreamLink.try_get_for_block(child_info, log_error=False)
                    validation_messages = get_xblock_validation_messages(child_info)
                    render_error = get_xblock_render_error(request, child_info)

                    children.append({
                        "xblock": child_info,
                        "name": child_info.display_name_with_default,
                        "block_id": child_info.location,
                        "block_type": child_info.location.block_type,
                        "user_partition_info": user_partition_info,
                        "user_partitions": user_partitions,
                        "upstream_link": (
                            # If the block isn't linked to an upstream (which is by far the most common case) then just
                            # make this field null, which communicates the same info, but with less noise.
                            upstream_link.to_json(include_child_info=True) if upstream_link.upstream_ref
                            else None
                        ),
                        "validation_messages": validation_messages,
                        "render_error": render_error,
                    })

            is_published = False
            try:
                is_published = not modulestore().has_changes(current_xblock)
            except ItemNotFoundError:
                logging.error('Could not find any changes for block [%s]', usage_key)

            upstream_ready_to_sync_children_info = []
            if current_xblock.upstream and get_upstream_info:
                upstream_link = UpstreamLink.get_for_block(current_xblock)
                upstream_link_data = upstream_link.to_json(include_child_info=True)
                upstream_ready_to_sync_children_info = upstream_link_data["ready_to_sync_children"]

            container_data = {
                "children": children,
                "is_published": is_published,
                "can_paste_component": is_course,
                "upstream_ready_to_sync_children_info": upstream_ready_to_sync_children_info,
                "display_name": current_xblock.display_name_with_default,
            }
            serializer = ContainerChildrenSerializer(container_data)
            return Response(serializer.data)
