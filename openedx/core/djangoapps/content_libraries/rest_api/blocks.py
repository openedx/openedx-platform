"""
Content Library REST APIs related to XBlocks/Components and their static assets
"""
from uuid import UUID

import edx_api_doc_tools as apidocs
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.transaction import non_atomic_requests
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_authz.constants import permissions as authz_permissions
from openedx_content import api as content_api
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

import openedx.core.djangoapps.site_configuration.helpers as configuration_helpers
from openedx.core.djangoapps.content_libraries import api, permissions
from openedx.core.djangoapps.content_libraries.rest_api import serializers
from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.lib.api.view_utils import view_auth_classes
from openedx.core.types.http import RestRequest

from .libraries import LibraryApiPaginationDocs
from .utils import convert_exceptions


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlocksView(GenericAPIView):
    """
    Views to work with XBlocks in a specific content library.
    """
    serializer_class = serializers.LibraryXBlockMetadataSerializer

    @apidocs.schema(
        parameters=[
            *LibraryApiPaginationDocs.apidoc_params,
            apidocs.query_parameter(
                'text_search',
                str,
                description="The string used to filter libraries by searching in title, id, org, or description",
            ),
            apidocs.query_parameter(
                'block_type',
                str,
                description="The block type to search for. If omitted or blank, searches for all types. "
                            "May be specified multiple times to match multiple types."
            )
        ],
    )
    @convert_exceptions
    def get(self, request, lib_key_str):
        """
        Get the list of all top-level blocks in this content library
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        text_search = request.query_params.get('text_search', None)
        block_types = request.query_params.getlist('block_type') or None

        api.require_permission_for_library_key(key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        components = api.get_library_components(key, text_search=text_search, block_types=block_types)

        paginated_xblock_metadata = [
            api.LibraryXBlockMetadata.from_component(key, component)
            for component in self.paginate_queryset(components)
        ]
        serializer = self.serializer_class(paginated_xblock_metadata, many=True)
        return self.get_paginated_response(serializer.data)

    @convert_exceptions
    @swagger_auto_schema(
        request_body=serializers.LibraryXBlockCreationSerializer,
        responses={200: serializers.LibraryXBlockMetadataSerializer}
    )
    def post(self, request, lib_key_str):
        """
        Add a new XBlock to this content library
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(library_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        serializer = serializers.LibraryXBlockCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Create a new regular top-level block:
        try:
            result = api.create_library_block(library_key, user_id=request.user.id, **serializer.validated_data)
        except api.IncompatibleTypesError as err:
            raise ValidationError(  # pylint: disable=raise-missing-from  # noqa: B904
                detail={'block_type': str(err)},
            )

        return Response(self.serializer_class(result).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockView(APIView):
    """
    Views to work with an existing XBlock in a content library.
    """
    serializer_class = serializers.LibraryXBlockMetadataSerializer

    @convert_exceptions
    def get(self, request, usage_key_str):
        """
        Get metadata about an existing XBlock in the content library.

        This API doesn't support versioning; most of the information it returns
        is related to the latest draft version, or to all versions of the block.
        If you need to get the display name of a previous version, use the
        similar "metadata" API from djangoapps.xblock, which does support
        versioning.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        result = api.get_library_block(key, include_collections=True)

        return Response(self.serializer_class(result).data)

    @convert_exceptions
    def delete(self, request, usage_key_str):  # pylint: disable=unused-argument
        """
        Delete a usage of a block from the library (and any children it has).

        If this is the only usage of the block's definition within this library,
        both the definition and the usage will be deleted. If this is only one
        of several usages, the definition will be kept. Usages by linked bundles
        are ignored and will not prevent deletion of the definition.

        If the usage points to a definition in a linked bundle, the usage will
        be deleted but the link and the linked bundle will be unaffected.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        api.delete_library_block(key, user_id=request.user.id)
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryComponentDraftHistoryView(APIView):
    """
    View to get the draft change history of a library component.
    """
    serializer_class = serializers.LibraryHistoryEntrySerializer

    @convert_exceptions
    def get(self, request, usage_key_str):
        """
        Get the draft change history for a library component since its last publication.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        history = api.get_library_component_draft_history(key, request=request)
        return Response(self.serializer_class(history, many=True).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryComponentPublishHistoryView(APIView):
    """
    View to get the publish history of a library component as a list of publish events.
    """
    serializer_class = serializers.LibraryPublishHistoryGroupSerializer

    @convert_exceptions
    def get(self, request, usage_key_str):
        """
        Get the publish history for a library component, ordered most-recent-first.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        history = api.get_library_component_publish_history(key, request=request)
        return Response(self.serializer_class(history, many=True).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryPublishHistoryEntriesView(APIView):
    """
    Unified view to get individual draft change entries for a specific publish event.

    Accepts any library entity key (component usage_key or container key) via the
    scope_entity_key query parameter and routes to the appropriate API function.

    For containers, scope_entity_key identifies the container being viewed — not
    necessarily the entity that was directly published. In Post-Verawood a parent
    container may have been directly published, but scope_entity_key is the child
    Unit the user is currently browsing.
    """
    serializer_class = serializers.LibraryHistoryEntrySerializer

    @convert_exceptions
    def get(self, request, lib_key_str):
        """
        Get the draft change entries for a specific publish event, ordered most-recent-first.

        Query parameters:
          - scope_entity_key: the usage_key (component) or container_key (scope container)
          - publish_log_uuid: UUID of the publish event
        """
        lib_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        scope_entity_key_str = request.query_params.get("scope_entity_key", "")
        publish_log_uuid_str = request.query_params.get("publish_log_uuid", "")
        if not scope_entity_key_str or not publish_log_uuid_str:
            return Response({"error": "scope_entity_key and publish_log_uuid are required."}, status=400)
        try:
            publish_log_uuid = UUID(publish_log_uuid_str)
        except ValueError:
            return Response({"error": f"Invalid publish_log_uuid: {publish_log_uuid_str!r}"}, status=400)

        try:
            usage_key = LibraryUsageLocatorV2.from_string(scope_entity_key_str)
            entries = api.get_library_component_publish_history_entries(
                usage_key, publish_log_uuid, request=request
            )
        except ObjectDoesNotExist:
            entries = []
        except (InvalidKeyError, AttributeError):
            try:
                container_key = LibraryContainerLocator.from_string(scope_entity_key_str)
                entries = api.get_library_container_publish_history_entries(
                    container_key, publish_log_uuid, request=request
                )
            except (InvalidKeyError, AttributeError):
                return Response({"error": f"Invalid scope_entity_key: {scope_entity_key_str!r}"}, status=400)

        return Response(self.serializer_class(entries, many=True).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryComponentCreationEntryView(APIView):
    """
    View to get the creation entry for a library component.
    """
    serializer_class = serializers.LibraryHistoryEntrySerializer

    @convert_exceptions
    def get(self, request, usage_key_str):
        """
        Get the creation entry for a library component (the moment it was first saved).
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        entry = api.get_library_component_creation_entry(key, request=request)
        if entry is None:
            return Response(None)
        return Response(self.serializer_class(entry).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockAssetListView(APIView):
    """
    Views to list an existing XBlock's static asset files
    """
    serializer_class = serializers.LibraryXBlockStaticFilesSerializer

    @convert_exceptions
    def get(self, request, usage_key_str):
        """
        List the static asset files belonging to this block.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        files = api.get_library_block_static_asset_files(key)
        return Response(self.serializer_class({"files": files}).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockAssetView(APIView):
    """
    Views to work with an existing XBlock's static asset files
    """
    parser_classes = (MultiPartParser, )
    serializer_class = serializers.LibraryXBlockStaticFileSerializer

    @convert_exceptions
    def get(self, request, usage_key_str, file_path):
        """
        Get a static asset file belonging to this block.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        files = api.get_library_block_static_asset_files(key)
        for f in files:
            if f.path == file_path:
                return Response(self.serializer_class(f).data)
        raise NotFound

    @convert_exceptions
    def put(self, request, usage_key_str, file_path):
        """
        Replace a static asset file belonging to this block.
        """
        file_path = file_path.replace(" ", "_")  # Messes up url/name correspondence due to URL encoding.
        usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(
            usage_key.lib_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        file_wrapper = request.data['content']
        if file_wrapper.size > 20 * 1024 * 1024:  # > 20 MiB
            # TODO: This check was written when V2 Libraries were backed by the Blockstore micro-service.
            #       Now that we're on openedx_content, do we still need it? Here's the original comment:
            #         In the future, we need a way to use file_wrapper.chunks() to read
            #         the file in chunks and stream that to Blockstore, but Blockstore
            #         currently lacks an API for streaming file uploads.
            #       Ref:  https://github.com/openedx/edx-platform/issues/34737
            raise ValidationError("File too big")
        file_content = file_wrapper.read()
        try:
            result = api.add_library_block_static_asset_file(usage_key, file_path, file_content, request.user)
        except ValueError:
            raise ValidationError("Invalid file path")  # pylint: disable=raise-missing-from  # noqa: B904
        return Response(self.serializer_class(result).data)

    @convert_exceptions
    def delete(self, request, usage_key_str, file_path):
        """
        Delete a static asset file belonging to this block.
        """
        usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(
            usage_key.lib_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        try:
            api.delete_library_block_static_asset_file(usage_key, file_path, request.user)
        except ValueError:
            raise ValidationError("Invalid file path")  # pylint: disable=raise-missing-from  # noqa: B904
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockPublishView(APIView):
    """
    Commit/publish all of the draft changes made to the component.
    """

    @convert_exceptions
    def post(self, request, usage_key_str):
        """
        Publish the draft changes made to this component.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(
            key.lib_key,
            request.user,
            authz_permissions.PUBLISH_LIBRARY_CONTENT
        )
        api.publish_component_changes(key, request.user.id)
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockCollectionsView(APIView):
    """
    View to set collections for a component.
    """
    @convert_exceptions
    def patch(self, request: RestRequest, usage_key_str) -> Response:
        """
        Sets Collections for a Component.

        Collection and Components must all be part of the given library/learning package.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        content_library = api.require_permission_for_library_key(
            key.lib_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY
        )
        serializer = serializers.ContentLibraryItemCollectionsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        component = api.get_component_from_usage_key(key)
        collection_keys = serializer.validated_data['collection_keys']
        api.set_library_item_collections(
            library_key=key.lib_key,
            entity_ref=component.publishable_entity.entity_ref,
            collection_keys=collection_keys,
            created_by=request.user.id,
            content_library=content_library,
        )

        return Response({'count': len(collection_keys)})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockOlxView(APIView):
    """
    Views to work with an existing XBlock's OLX
    """
    serializer_class = serializers.LibraryXBlockOlxSerializer

    @convert_exceptions
    def get(self, request, usage_key_str):
        """
        DEPRECATED. Use get_block_olx_view() in xblock REST-API.
        Can be removed post-Teak.

        Get the block's OLX
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        xml_str = xblock_api.get_block_draft_olx(key)
        return Response(self.serializer_class({"olx": xml_str}).data)

    @convert_exceptions
    def post(self, request, usage_key_str):
        """
        Replace the block's OLX.

        This API is only meant for use by developers or API client applications.
        Very little validation is done.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_olx_str = serializer.validated_data["olx"]
        try:
            version_num = api.set_library_block_olx(key, new_olx_str).version_num
        except ValueError as err:
            raise ValidationError(detail=str(err))  # pylint: disable=raise-missing-from  # noqa: B904
        return Response(self.serializer_class({"olx": new_olx_str, "version_num": version_num}).data)


@view_auth_classes()
class LibraryBlockRestore(APIView):
    """
    View to restore soft-deleted library xblocks.
    """
    @convert_exceptions
    def post(self, request, usage_key_str) -> Response:
        """
        Restores a soft-deleted library block that belongs to a Content Library
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        api.restore_library_block(key, request.user.id)
        return Response(None, status=status.HTTP_204_NO_CONTENT)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockHierarchy(GenericAPIView):
    """
    View to return the full hierarchy of containers that contain a library block.
    """
    serializer_class = serializers.ContainerHierarchySerializer

    @convert_exceptions
    def get(self, request, usage_key_str) -> Response:
        """
        Fetches and returns the full container hierarchy for the given library block.
        """
        key = LibraryUsageLocatorV2.from_string(usage_key_str)
        api.require_permission_for_library_key(key.lib_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        hierarchy = api.get_library_object_hierarchy(key)
        return Response(self.serializer_class(hierarchy).data)


def get_component_version_asset(request, component_version_uuid, asset_path):
    """
    Serves static assets associated with particular Component versions.

    Important notes:
    * This is meant for Studio/authoring use ONLY. It requires read access to
      the content library.
    * It uses the UUID because that's easier to parse than the key field (which
      could be part of an OpaqueKey, but could also be almost anything else).
    * This is not very performant, and we still want to use the X-Accel-Redirect
      method for serving LMS traffic in the longer term (and probably Studio
      eventually).
    """
    try:
        component_version = content_api.get_component_version_by_uuid(
            component_version_uuid
        )
    except ObjectDoesNotExist as exc:
        raise Http404() from exc

    # Permissions check...
    learning_package = component_version.component.learning_package
    library_key = LibraryLocatorV2.from_string(learning_package.package_ref)
    api.require_permission_for_library_key(
        library_key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
    )

    # We already have logic for getting the correct content and generating the
    # proper headers in openedx_content, but the response generated here is an
    # X-Accel-Redirect and lacks the actual content. We eventually want to use
    # this response in conjunction with a media reverse proxy (Caddy or Nginx),
    # but in the short term we're just going to remove the redirect and stream
    # the content directly.
    redirect_response = content_api.get_redirect_response_for_component_asset(
        component_version_uuid,
        asset_path,
        public=False,
    )

    # If there was any error, we return that response because it will have the
    # correct headers set and won't have any X-Accel-Redirect header set.
    if redirect_response.status_code != 200:
        return redirect_response

    # If we got here, we know that the asset exists and it's okay to download.
    cv_media = component_version.componentversionmedia_set.get(path=asset_path)
    media = cv_media.media

    # Delete the re-direct part of the response headers. We'll copy the rest.
    headers = redirect_response.headers
    headers.pop('X-Accel-Redirect')

    # We need to set the content size header manually because this is a
    # streaming response. It's not included in the redirect headers because it's
    # not needed there (the reverse-proxy would have direct access to the file).
    headers['Content-Length'] = media.size

    # Some assets, such as PDFs, need to be embedded in an iFrame in the MFE
    # studio. Permit this, so long as the file is in the cors_origin_whitelist.
    cors_origin_whitelist = configuration_helpers.get_value(
        'CORS_ORIGIN_WHITELIST', getattr(settings, 'CORS_ORIGIN_WHITELIST', []),
    )
    headers["Content-Security-Policy"] = f"frame-ancestors 'self' {' '.join(cors_origin_whitelist)};"

    if request.method == "HEAD":
        return HttpResponse(headers=headers)

    # Otherwise it's going to be a GET response. We don't support response
    # offsets or anything fancy, because we don't expect to run this view at
    # LMS-scale.
    return StreamingHttpResponse(
        media.read_file().chunks(),
        headers=redirect_response.headers,
    )


@view_auth_classes()
class LibraryComponentAssetView(APIView):
    """
    Serves static assets associated with particular Component versions.
    """
    @convert_exceptions
    def get(self, request, component_version_uuid, asset_path):
        """
        GET API for fetching static asset for given component_version_uuid.
        """
        return get_component_version_asset(request, component_version_uuid, asset_path)


@view_auth_classes()
class LibraryComponentDraftAssetView(APIView):
    """
    Serves the draft version of static assets associated with a Library Component.

    See `get_component_version_asset` for more details
    """
    @convert_exceptions
    def get(self, request, usage_key, asset_path):
        """
        Fetches component_version_uuid for given usage_key and returns component asset.
        """
        try:
            component_version_uuid = api.get_component_from_usage_key(usage_key).versioning.draft.uuid
        except ObjectDoesNotExist as exc:
            raise Http404() from exc

        return get_component_version_asset(request, component_version_uuid, asset_path)
