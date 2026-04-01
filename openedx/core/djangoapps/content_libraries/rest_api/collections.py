"""
Collections API Views
"""
from __future__ import annotations

from django.db.models import QuerySet
from django.utils.text import slugify
from django.db import transaction

from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.status import HTTP_204_NO_CONTENT

from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_authz.constants import permissions as authz_permissions
from openedx_content import api as content_api
from openedx_content.models_api import Collection

from .. import api, permissions
from ..models import ContentLibrary
from .utils import convert_exceptions
from .serializers import (
    ContentLibraryCollectionSerializer,
    ContentLibraryCollectionUpdateSerializer,
    ContentLibraryItemKeysSerializer,
)
from openedx.core.types.http import RestRequest


class LibraryCollectionsView(ModelViewSet):
    """
    Views to get, create and update Library Collections.
    """

    serializer_class = ContentLibraryCollectionSerializer
    lookup_field = 'key'

    def __init__(self, *args, **kwargs) -> None:
        """
        Caches the ContentLibrary for the duration of the request.
        """
        super().__init__(*args, **kwargs)
        self._content_library: ContentLibrary | None = None

    def get_content_library(self) -> ContentLibrary:
        """
        Returns the requested ContentLibrary object, if access allows.
        """
        if self._content_library:
            return self._content_library

        lib_key_str = self.kwargs["lib_key_str"]
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        permission = (
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY
            if self.request.method in ['OPTIONS', 'GET']
            else permissions.CAN_EDIT_THIS_CONTENT_LIBRARY
        )
        self._content_library = api.require_permission_for_library_key(
            library_key,
            self.request.user,
            permission,
        )
        return self._content_library

    def get_queryset(self) -> QuerySet[Collection]:
        """
        Returns a queryset for the requested Collections, if access allows.

        This method may raise exceptions; these are handled by the @convert_exceptions wrapper on the views.
        """
        content_library = self.get_content_library()
        assert content_library.learning_package_id
        return content_api.get_collections(content_library.learning_package_id)

    def get_object(self) -> Collection:
        """
        Returns the requested Collections, if access allows.

        This method may raise exceptions; these are handled by the @convert_exceptions wrapper on the views.
        """
        collection = super().get_object()
        content_library = self.get_content_library()

        # Ensure the ContentLibrary and Collection share the same learning package
        if collection.learning_package_id != content_library.learning_package_id:
            raise api.ContentLibraryCollectionNotFound
        return collection

    @convert_exceptions
    def retrieve(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        Retrieve the Content Library Collection
        """
        # View declared so we can wrap it in @convert_exceptions
        return super().retrieve(request, *args, **kwargs)

    @convert_exceptions
    def list(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        List Collections that belong to Content Library
        """
        # View declared so we can wrap it in @convert_exceptions
        return super().list(request, *args, **kwargs)

    @convert_exceptions
    def create(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        Create a Collection that belongs to a Content Library
        """
        content_library = self.get_content_library()
        api.require_permission_for_library_key(
            content_library.library_key,
            request.user,
            authz_permissions.CREATE_LIBRARY_COLLECTION
        )
        create_serializer = ContentLibraryCollectionUpdateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)

        title = create_serializer.validated_data['title']
        key = slugify(title)

        attempt = 0
        collection = None
        while not collection:
            modified_key = key if attempt == 0 else key + '-' + str(attempt)
            try:
                # Add transaction here to avoid TransactionManagementError on retry
                with transaction.atomic():
                    collection = api.create_library_collection(
                        library_key=content_library.library_key,
                        content_library=content_library,
                        collection_key=modified_key,
                        title=title,
                        description=create_serializer.validated_data["description"],
                        created_by=request.user.id,
                    )
            except api.LibraryCollectionAlreadyExists:
                attempt += 1

        serializer = self.get_serializer(collection)

        return Response(serializer.data)

    @convert_exceptions
    def partial_update(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        Update a Collection that belongs to a Content Library
        """
        content_library = self.get_content_library()
        api.require_permission_for_library_key(
            content_library.library_key,
            request.user,
            authz_permissions.EDIT_LIBRARY_COLLECTION
        )
        collection_key = kwargs["key"]

        update_serializer = ContentLibraryCollectionUpdateSerializer(
            data=request.data, partial=True
        )
        update_serializer.is_valid(raise_exception=True)
        updated_collection = api.update_library_collection(
            library_key=content_library.library_key,
            collection_key=collection_key,
            content_library=content_library,
            **update_serializer.validated_data
        )
        serializer = self.get_serializer(updated_collection)

        return Response(serializer.data)

    @convert_exceptions
    def destroy(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        Soft-deletes a Collection that belongs to a Content Library
        """
        content_library = self.get_content_library()
        api.require_permission_for_library_key(
            content_library.library_key,
            request.user,
            authz_permissions.DELETE_LIBRARY_COLLECTION
        )
        collection = super().get_object()
        assert collection.learning_package_id
        content_api.delete_collection(
            collection.learning_package_id,
            collection.key,
            hard_delete=False,
        )
        return Response(None, status=HTTP_204_NO_CONTENT)

    @convert_exceptions
    @action(detail=True, methods=['post'], url_path='restore', url_name='collection-restore')
    def restore(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        Restores a soft-deleted Collection that belongs to a Content Library
        """
        content_library = self.get_content_library()
        api.require_permission_for_library_key(
            content_library.library_key,
            request.user,
            authz_permissions.EDIT_LIBRARY_COLLECTION
        )
        assert content_library.learning_package_id
        collection_key = kwargs["key"]
        content_api.restore_collection(
            content_library.learning_package_id,
            collection_key,
        )
        return Response(None, status=HTTP_204_NO_CONTENT)

    @convert_exceptions
    @action(detail=True, methods=['delete', 'patch'], url_path='items', url_name='items-update')
    def update_items(self, request: RestRequest, *args, **kwargs) -> Response:
        """
        Adds (PATCH) or removes (DELETE) items to/from a Collection.

        Collection and items must all be part of the given library/learning package.
        """
        content_library = self.get_content_library()
        api.require_permission_for_library_key(
            content_library.library_key,
            request.user,
            authz_permissions.EDIT_LIBRARY_COLLECTION
        )
        collection_key = kwargs["key"]

        serializer = ContentLibraryItemKeysSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        opaque_keys = serializer.validated_data["usage_keys"]
        api.update_library_collection_items(
            library_key=content_library.library_key,
            content_library=content_library,
            collection_key=collection_key,
            opaque_keys=opaque_keys,
            created_by=request.user.id,
            remove=(request.method == "DELETE"),
        )

        return Response({'count': len(opaque_keys)})
