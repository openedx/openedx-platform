"""
REST API views for containers (sections, subsections, units) in content libraries
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db.transaction import non_atomic_requests
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from opaque_keys.edx.locator import LibraryLocatorV2, LibraryContainerLocator
from openedx_authz.constants import permissions as authz_permissions
from openedx_content import api as content_api
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT, HTTP_200_OK

from openedx.core.djangoapps.content_libraries import api, permissions
from openedx.core.lib.api.view_utils import view_auth_classes
from openedx.core.types.http import RestRequest

from . import serializers
from .utils import convert_exceptions

User = get_user_model()
log = logging.getLogger(__name__)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainersView(GenericAPIView):
    """
    Views to work with Containers in a specific content library.
    """
    serializer_class = serializers.LibraryContainerMetadataSerializer

    @convert_exceptions
    @swagger_auto_schema(
        request_body=serializers.LibraryContainerMetadataSerializer,
        responses={200: serializers.LibraryContainerMetadataSerializer}
    )
    def post(self, request, lib_key_str):
        """
        Create a new Container in this content library
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(library_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        serializer = serializers.LibraryContainerMetadataSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        container_type = serializer.validated_data['container_type']
        container = api.create_container(
            library_key,
            container_type,
            title=serializer.validated_data['display_name'],
            slug=serializer.validated_data.get('slug'),
            user_id=request.user.id,
        )

        return Response(serializers.LibraryContainerMetadataSerializer(container).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainerView(GenericAPIView):
    """
    View to retrieve, delete or update data about a specific container (a section, subsection, or unit)
    """
    serializer_class = serializers.LibraryContainerMetadataSerializer

    @convert_exceptions
    @swagger_auto_schema(
        responses={200: serializers.LibraryContainerMetadataSerializer}
    )
    def get(self, request, container_key: LibraryContainerLocator):
        """
        Get information about a container
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
        )
        container = api.get_container(container_key, include_collections=True)
        return Response(serializers.LibraryContainerMetadataSerializer(container).data)

    @convert_exceptions
    @swagger_auto_schema(
        request_body=serializers.LibraryContainerUpdateSerializer,
        responses={200: serializers.LibraryContainerMetadataSerializer}
    )
    def patch(self, request, container_key: LibraryContainerLocator):
        """
        Update a Container.
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        serializer = serializers.LibraryContainerUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        container = api.update_container(
            container_key,
            display_name=serializer.validated_data['display_name'],
            user_id=request.user.id,
        )

        return Response(serializers.LibraryContainerMetadataSerializer(container).data)

    @convert_exceptions
    def delete(self, request, container_key: LibraryContainerLocator):
        """
        Delete a Container (soft delete).
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )

        api.delete_container(
            container_key,
        )

        return Response({}, status=HTTP_204_NO_CONTENT)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainerChildrenView(GenericAPIView):
    """
    View to get or update children of specific container (a section, subsection, or unit)
    """
    serializer_class = serializers.LibraryXBlockMetadataSerializer

    @convert_exceptions
    @swagger_auto_schema(
        responses={
            HTTP_200_OK: serializers.UnionLibraryMetadataSerializer()
        }
    )
    def get(self, request, container_key: LibraryContainerLocator):
        """
        Get children of given container
        Example:
        GET /api/libraries/v2/containers/<container_key>/children/
        Result:
        [
            {
                'block_type': 'problem',
                'can_stand_alone': True,
                'collections': [],
                'created': '2025-03-21T13:53:55Z',
                'def_key': None,
                'display_name': 'Blank Problem',
                'has_unpublished_changes': True,
                'id': 'lb:CL-TEST:containers:problem:Problem1',
                'last_draft_created': '2025-03-21T13:53:55Z',
                'last_draft_created_by': 'Bob',
                'last_published': None,
                'modified': '2025-03-21T13:53:55Z',
                'published_by': None,
            },
            {
                'block_type': 'html',
                'can_stand_alone': False,
                'collections': [],
                'created': '2025-03-21T13:53:55Z',
                'def_key': None,
                'display_name': 'Text',
                'has_unpublished_changes': True,
                'id': 'lb:CL-TEST:containers:html:Html1',
                'last_draft_created': '2025-03-21T13:53:55Z',
                'last_draft_created_by': 'Bob',
                'last_published': None,
                'modified': '2025-03-21T13:53:55Z',
                'published_by': None,
            }
        ]
        """
        published = request.GET.get('published', 'false').lower() == 'true'
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
        )
        child_entities = api.get_container_children(container_key, published=published)
        if container_key.container_type == api.ContainerType.Unit.value:
            data = serializers.LibraryXBlockMetadataSerializer(child_entities, many=True).data
        else:
            data = serializers.LibraryContainerMetadataSerializer(child_entities, many=True).data
        return Response(data)

    def _update_component_children(
        self,
        request,
        container_key: LibraryContainerLocator,
        action: content_api.ChildrenEntitiesAction,
    ):
        """
        Helper function to update children in container.
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        serializer = serializers.ContentLibraryItemContainerKeysSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        container = api.update_container_children(
            container_key,
            children_ids=serializer.validated_data["usage_keys"],
            user_id=request.user.id,
            entities_action=action,
        )
        return Response(serializers.LibraryContainerMetadataSerializer(container).data)

    @convert_exceptions
    @swagger_auto_schema(
        request_body=serializers.ContentLibraryItemContainerKeysSerializer,
        responses={200: serializers.LibraryContainerMetadataSerializer}
    )
    def post(self, request, container_key: LibraryContainerLocator):
        """
        Add items to container
        Example:
        POST /api/libraries/v2/containers/<container_key>/children/
        Request body:
        {"usage_keys": ['lb:CL-TEST:containers:problem:Problem1', 'lb:CL-TEST:containers:html:Html1']}
        """
        return self._update_component_children(
            request,
            container_key,
            action=content_api.ChildrenEntitiesAction.APPEND,
        )

    @convert_exceptions
    @swagger_auto_schema(
        request_body=serializers.ContentLibraryItemContainerKeysSerializer,
        responses={200: serializers.LibraryContainerMetadataSerializer}
    )
    def delete(self, request, container_key: LibraryContainerLocator):
        """
        Remove items from container
        Example:
        DELETE /api/libraries/v2/containers/<container_key>/children/
        Request body:
        {"usage_keys": ['lb:CL-TEST:containers:problem:Problem1', 'lb:CL-TEST:containers:html:Html1']}
        """
        return self._update_component_children(
            request,
            container_key,
            action=content_api.ChildrenEntitiesAction.REMOVE,
        )

    @convert_exceptions
    @swagger_auto_schema(
        request_body=serializers.ContentLibraryItemContainerKeysSerializer,
        responses={200: serializers.LibraryContainerMetadataSerializer}
    )
    def patch(self, request, container_key: LibraryContainerLocator):
        """
        Replace items in container, can be used to reorder items as well.
        Example:
        PATCH /api/libraries/v2/containers/<container_key>/children/
        Request body:
        {"usage_keys": ['lb:CL-TEST:containers:problem:Problem1', 'lb:CL-TEST:containers:html:Html1']}
        """
        return self._update_component_children(
            request,
            container_key,
            action=content_api.ChildrenEntitiesAction.REPLACE,
        )


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainerRestore(GenericAPIView):
    """
    View to restore soft-deleted library containers.
    """

    @convert_exceptions
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
        ),
        responses={
            HTTP_204_NO_CONTENT: "No content"
        }
    )
    def post(self, request, container_key: LibraryContainerLocator) -> Response:
        """
        Restores a soft-deleted library container
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        api.restore_container(container_key)
        return Response(None, status=HTTP_204_NO_CONTENT)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainerCollectionsView(GenericAPIView):
    """
    View to set collections for a container.
    """

    @convert_exceptions
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
        ),
        responses={
            HTTP_200_OK: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'count': openapi.Schema(type=openapi.TYPE_INTEGER)
                }
            )
        }
    )
    def patch(self, request: RestRequest, container_key: LibraryContainerLocator) -> Response:
        """
        Sets Collections for a Component.

        Collection and Components must all be part of the given library/learning package.
        """
        content_library = api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY
        )
        serializer = serializers.ContentLibraryItemCollectionsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        collection_keys = serializer.validated_data['collection_keys']
        api.set_library_item_collections(
            library_key=container_key.lib_key,
            entity_key=container_key.container_id,
            collection_keys=collection_keys,
            created_by=request.user.id,
            content_library=content_library,
        )

        return Response({'count': len(collection_keys)})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainerPublishView(GenericAPIView):
    """
    View to publish a container, or revert to last published.
    """

    @convert_exceptions
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
        ),
        responses={
            HTTP_200_OK: openapi.Schema(
                type=openapi.TYPE_OBJECT,
            )
        }
    )
    def post(self, request: RestRequest, container_key: LibraryContainerLocator) -> Response:
        """
        Publish the container and its children
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            authz_permissions.PUBLISH_LIBRARY_CONTENT
        )
        api.publish_container_changes(container_key, request.user.id)
        # If we need to in the future, we could return a list of all the child containers/components that were
        # auto-published as a result.
        return Response({})


@view_auth_classes()
class LibraryContainerCopyView(GenericAPIView):
    """
    View to copy a container to clipboard
    """
    @convert_exceptions
    def post(self, request: RestRequest, container_key: LibraryContainerLocator) -> Response:
        """
        Copy a Container to clipboard
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
        )
        assert request.user.id is not None, "User must be authenticated to copy a container"

        api.copy_container(
            container_key,
            user_id=request.user.id,
        )

        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryContainerHierarchy(GenericAPIView):
    """
    View to return the full hierarchy of containers that contain and are contained by a library container.
    """
    serializer_class = serializers.ContainerHierarchySerializer

    @convert_exceptions
    @swagger_auto_schema(
        responses={200: serializers.ContainerHierarchySerializer}
    )
    def get(self, request: RestRequest, container_key: LibraryContainerLocator) -> Response:
        """
        Fetches and returns the full container hierarchy for the given library block.
        """
        api.require_permission_for_library_key(
            container_key.lib_key,
            request.user,
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
        )
        hierarchy = api.get_library_object_hierarchy(container_key)
        return Response(self.serializer_class(hierarchy).data)
