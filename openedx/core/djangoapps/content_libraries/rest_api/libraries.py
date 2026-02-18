"""
=======================
Content Libraries Views
=======================

This module contains the REST APIs for openedx_content-based content libraries,
and LTI 1.3 views (though I'm not sure how functional the LTI piece of this is
right now).

Most of the real work is intended to happen in the api.py module. The views are
intended to be thin ones that do:

1. Permissions checking
2. Input/output data conversion via serializers
3. Pagination

Everything else should be delegated to api.py for the actual business logic. If
you see business logic happening in these views, consider refactoring them into
the api module instead.

.. warning::
    **NOTICE: DO NOT USE THE @atomic DECORATOR FOR THESE VIEWS!!!**

    Views in ths module are decorated with:
      @method_decorator(non_atomic_requests, name="dispatch")

    This forces the views to execute without an implicit view-level transaction,
    even if the project is configured to use view-level transactions by default.
    (So no matter what you set the ATOMIC_REQUESTS setting to.)

    We *must* use manual transactions for content libraries related views, or
    we'll run into mysterious race condition bugs. We should NOT use the @atomic
    decorator over any of these views.

    The problem is this: Code outside of this app will want to listen for
    content lifecycle events like ``LIBRARY_BLOCK_CREATED`` and take certain
    actions based on them. We see this pattern used extensively with courses.
    Another common pattern is to use celery to queue up an asynchronous task to
    do that work.

    If there is an implicit database transaction around the entire view
    execution, the celery task may start up just before the view finishes
    executing. When that happens, the celery task doesn't see the new content
    change, because the view transaction hasn't finished committing it to the
    database yet.

    The worst part of this is that dev environments and tests often won't catch
    this because celery is typically configured to run in-process in those
    situations. When it's run in-process, celery is already inside the view's
    transaction so it will "see" the new changes and everything will appear to
    be fine–only to fail intermittently when deployed to production.

    We can and should continue to use atomic() as a context manager when we want
    to make changes to multiple models. But this should happen at the api module
    layer, not in the view. Other apps are permitted to call functions in the
    public api.py module, and we want to make sure those api calls manage their
    own transactions and don't assume that they're being called in an atomic
    block.

    Historical note: These views used to be wrapped with @atomic because we
    wanted to make all views that operated on Blockstore (the predecessor
    to openedx_content) atomic:
        https://github.com/openedx/edx-platform/pull/30456
"""
import itertools
import json
import logging

import edx_api_doc_tools as apidocs
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.models import Group
from django.db.transaction import atomic, non_atomic_requests
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import TemplateResponseMixin, View
from drf_yasg.utils import swagger_auto_schema
from user_tasks.models import UserTaskStatus

from opaque_keys.edx.locator import LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_authz.constants import permissions as authz_permissions
from organizations.api import ensure_organization
from organizations.exceptions import InvalidOrganizationException
from organizations.models import Organization
from pylti1p3.contrib.django import DjangoCacheDataStorage, DjangoDbToolConf, DjangoMessageLaunch, DjangoOIDCLogin
from pylti1p3.exception import LtiException, OIDCException
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

import openedx.core.djangoapps.site_configuration.helpers as configuration_helpers
from cms.djangoapps.contentstore.views.course import (
    get_allowed_organizations_for_libraries,
    user_can_create_organizations
)
from cms.djangoapps.contentstore.storage import course_import_export_storage
from openedx.core.djangoapps.content_libraries.tasks import restore_library

from openedx.core.djangoapps.content_libraries import api, permissions
from openedx.core.djangoapps.content_libraries.api.libraries import get_backup_task_status
from openedx.core.djangoapps.content_libraries.rest_api.serializers import (
    ContentLibraryAddPermissionByEmailSerializer,
    ContentLibraryBlockImportTaskCreateSerializer,
    ContentLibraryBlockImportTaskSerializer,
    ContentLibraryFilterSerializer,
    ContentLibraryMetadataSerializer,
    ContentLibraryPermissionLevelSerializer,
    ContentLibraryPermissionSerializer,
    ContentLibraryUpdateSerializer,
    LibraryBackupResponseSerializer,
    LibraryBackupTaskStatusSerializer,
    LibraryRestoreFileSerializer,
    LibraryRestoreTaskRequestSerializer,
    LibraryRestoreTaskResultSerializer,
    LibraryXBlockCreationSerializer,
    LibraryXBlockMetadataSerializer,
    LibraryXBlockTypeSerializer,
    PublishableItemSerializer
)
from openedx.core.djangoapps.content_libraries.tasks import backup_library
from openedx.core.djangoapps.safe_sessions.middleware import mark_user_change_as_expected
from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.lib.api.view_utils import view_auth_classes

from ..models import ContentLibrary, LtiGradedResource, LtiProfile
from .utils import convert_exceptions

User = get_user_model()
log = logging.getLogger(__name__)


class LibraryApiPaginationDocs:
    """
    API docs for query params related to paginating ContentLibraryMetadata objects.
    """
    apidoc_params = [
        apidocs.query_parameter(
            'pagination',
            bool,
            description="Enables paginated schema",
        ),
        apidocs.query_parameter(
            'page',
            int,
            description="Page number of result. Defaults to 1",
        ),
        apidocs.query_parameter(
            'page_size',
            int,
            description="Page size of the result. Defaults to 50",
        ),
    ]


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryRootView(GenericAPIView):
    """
    Views to list, search for, and create content libraries.
    """
    serializer_class = ContentLibraryMetadataSerializer

    @apidocs.schema(
        responses={200: ContentLibraryMetadataSerializer(many=True)},
        parameters=[
            *LibraryApiPaginationDocs.apidoc_params,
            apidocs.query_parameter(
                'org',
                str,
                description="The organization short-name used to filter libraries",
            ),
            apidocs.query_parameter(
                'text_search',
                str,
                description="The string used to filter libraries by searching in title, id, org, or description",
            ),
            apidocs.query_parameter(
                'order',
                str,
                description=(
                    "Name of the content library field to sort the results by. Prefix with a '-' to sort descending."
                ),
            ),
        ],
    )
    def get(self, request):
        """
        Return a list of all content libraries that the user has permission to view.
        """
        serializer = ContentLibraryFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        org = serializer.validated_data['org']
        text_search = serializer.validated_data['text_search']
        order = serializer.validated_data['order']

        queryset = api.get_libraries_for_user(
            request.user,
            org=org,
            text_search=text_search,
            order=order,
        )
        paginated_qs = self.paginate_queryset(queryset)
        result = api.get_metadata(paginated_qs)

        serializer = ContentLibraryMetadataSerializer(result, many=True)
        # Verify `pagination` param to maintain compatibility with older
        # non pagination-aware clients
        if request.GET.get('pagination', 'false').lower() == 'true':
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def post(self, request):
        """
        Create a new content library.
        """
        if not api.user_can_create_library(request.user):
            raise PermissionDenied
        serializer = ContentLibraryMetadataSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        # Converting this over because using the reserved names 'type' and 'license' would shadow the built-in
        # definitions elsewhere.
        data['library_license'] = data.pop('license')
        key_data = data.pop("key")
        # Move "slug" out of the "key.slug" pseudo-field that the serializer added:
        data["slug"] = key_data["slug"]
        # Get the organization short_name out of the "key.org" pseudo-field that the serializer added:
        org_name = key_data["org"]
        try:
            ensure_organization(org_name)
        except InvalidOrganizationException:
            raise ValidationError(  # lint-amnesty, pylint: disable=raise-missing-from
                detail={"org": f"No such organization '{org_name}' found."}
            )
        # Ensure the user is allowed to create libraries under this org
        if not (
            user_can_create_organizations(request.user) or
            org_name in get_allowed_organizations_for_libraries(request.user)
        ):
            raise ValidationError(  # lint-amnesty, pylint: disable=raise-missing-from
                detail={"org": f"User not allowed to create libraries in '{org_name}'."}
            )
        org = Organization.objects.get(short_name=org_name)

        try:
            with atomic():
                result = api.create_library(org=org, **data)
                # Grant the current user admin permissions on the library:
                api.set_library_user_permissions(result.key, request.user, api.AccessLevel.ADMIN_LEVEL)

                # Grant the current user the library admin role for this library.
                # Other role assignments are handled by openedx-authz and the Console MFE.
                # This ensures the creator has access to new libraries. From the library views,
                # users can then manage roles for others.
                api.assign_library_role_to_user(result.key, request.user, api.AccessLevel.ADMIN_LEVEL)
        except api.LibraryAlreadyExists:
            raise ValidationError(detail={"slug": "A library with that ID already exists."})  # lint-amnesty, pylint: disable=raise-missing-from

        return Response(ContentLibraryMetadataSerializer(result).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryDetailsView(APIView):
    """
    Views to work with a specific content library
    """
    @convert_exceptions
    def get(self, request, lib_key_str):
        """
        Get a specific content library
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        result = api.get_library(key)
        serializer = ContentLibraryMetadataSerializer(result, context={'request': self.request})
        return Response(serializer.data)

    @convert_exceptions
    def patch(self, request, lib_key_str):
        """
        Update a content library
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        serializer = ContentLibraryUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        # Prevent ourselves from shadowing global names.
        if 'license' in data:
            data['library_license'] = data.pop('license')
        try:
            api.update_library(key, **data)
        except api.IncompatibleTypesError as err:
            raise ValidationError({'type': str(err)})  # lint-amnesty, pylint: disable=raise-missing-from
        result = api.get_library(key)
        return Response(ContentLibraryMetadataSerializer(result).data)

    @convert_exceptions
    def delete(self, request, lib_key_str):  # pylint: disable=unused-argument
        """
        Delete a content library
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_DELETE_THIS_CONTENT_LIBRARY)
        api.delete_library(key)
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryTeamView(APIView):
    """
    View to get the list of users/groups who can access and edit the content
    library.

    Note also the 'allow_public_' settings which can be edited by PATCHing the
    library itself (LibraryDetailsView.patch).
    """
    @convert_exceptions
    def post(self, request, lib_key_str):
        """
        Add a user to this content library via email, with permissions specified in the
        request body.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM)
        serializer = ContentLibraryAddPermissionByEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = User.objects.get(email=serializer.validated_data.get('email'))
        except User.DoesNotExist:
            raise ValidationError({'email': _('We could not find a user with that email address.')})  # lint-amnesty, pylint: disable=raise-missing-from
        grant = api.get_library_user_permissions(key, user)
        if grant:
            return Response(
                {'email': [_('This user already has access to this library.')]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            api.set_library_user_permissions(key, user, access_level=serializer.validated_data["access_level"])
        except api.LibraryPermissionIntegrityError as err:
            raise ValidationError(detail=str(err))  # lint-amnesty, pylint: disable=raise-missing-from
        grant = api.get_library_user_permissions(key, user)
        return Response(ContentLibraryPermissionSerializer(grant).data)

    @convert_exceptions
    def get(self, request, lib_key_str):
        """
        Get the list of users and groups who have permissions to view and edit
        this library.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY_TEAM)
        team = api.get_library_team(key)
        return Response(ContentLibraryPermissionSerializer(team, many=True).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryTeamUserView(APIView):
    """
    View to add/remove/edit an individual user's permissions for a content
    library.
    """
    @convert_exceptions
    def put(self, request, lib_key_str, username):
        """
        Add a user to this content library, with permissions specified in the
        request body.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM)
        serializer = ContentLibraryPermissionLevelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_object_or_404(User, username=username)
        try:
            api.set_library_user_permissions(key, user, access_level=serializer.validated_data["access_level"])
        except api.LibraryPermissionIntegrityError as err:
            raise ValidationError(detail=str(err))  # lint-amnesty, pylint: disable=raise-missing-from
        grant = api.get_library_user_permissions(key, user)
        return Response(ContentLibraryPermissionSerializer(grant).data)

    @convert_exceptions
    def get(self, request, lib_key_str, username):
        """
        Gets the current permissions settings for a particular user.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY_TEAM)
        user = get_object_or_404(User, username=username)
        grant = api.get_library_user_permissions(key, user)
        if not grant:
            raise NotFound
        return Response(ContentLibraryPermissionSerializer(grant).data)

    @convert_exceptions
    def delete(self, request, lib_key_str, username):
        """
        Remove the specified user's permission to access or edit this content
        library.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM)
        user = get_object_or_404(User, username=username)
        try:
            api.set_library_user_permissions(key, user, access_level=None)
        except api.LibraryPermissionIntegrityError as err:
            raise ValidationError(detail=str(err))  # lint-amnesty, pylint: disable=raise-missing-from
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryTeamGroupView(APIView):
    """
    View to add/remove/edit a group's permissions for a content library.
    """
    @convert_exceptions
    def put(self, request, lib_key_str, group_name):
        """
        Add a group to this content library, with permissions specified in the
        request body.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM)
        serializer = ContentLibraryPermissionLevelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = get_object_or_404(Group, name=group_name)
        api.set_library_group_permissions(key, group, access_level=serializer.validated_data["access_level"])
        return Response({})

    @convert_exceptions
    def delete(self, request, lib_key_str, username):
        """
        Remove the specified user's permission to access or edit this content
        library.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM)
        group = get_object_or_404(Group, username=username)
        api.set_library_group_permissions(key, group, access_level=None)
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockTypesView(APIView):
    """
    View to get the list of XBlock types that can be added to this library
    """
    @convert_exceptions
    def get(self, request, lib_key_str):
        """
        Get the list of XBlock types that can be added to this library
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_VIEW_THIS_CONTENT_LIBRARY)
        result = api.get_allowed_block_types(key)
        return Response(LibraryXBlockTypeSerializer(result, many=True).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryCommitView(APIView):
    """
    Commit/publish or revert all of the draft changes made to the library.
    """
    @convert_exceptions
    def post(self, request, lib_key_str):
        """
        Commit the draft changes made to the specified block and its
        descendants.
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(
            key,
            request.user,
            authz_permissions.PUBLISH_LIBRARY_CONTENT
        )
        api.publish_changes(key, request.user.id)
        return Response({})

    @convert_exceptions
    def delete(self, request, lib_key_str):  # pylint: disable=unused-argument
        """
        Revert the draft changes made to the specified block and its
        descendants. Restore it to the last published version
        """
        key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        api.revert_changes(key, request.user.id)
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryPasteClipboardView(GenericAPIView):
    """
    Paste content of clipboard into Library.
    """
    serializer_class = PublishableItemSerializer

    @convert_exceptions
    @swagger_auto_schema(
        responses={200: PublishableItemSerializer}
    )
    def post(self, request, lib_key_str):
        """
        Import the contents of the user's clipboard and paste them into the Library
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(library_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)

        try:
            result = api.import_staged_content_from_user_clipboard(library_key, request.user)
        except api.IncompatibleTypesError as err:
            raise ValidationError(detail={'block_type': str(err)}) from err

        return Response(PublishableItemSerializer(result).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlocksView(GenericAPIView):
    """
    Views to work with XBlocks in a specific content library.
    """
    serializer_class = LibraryXBlockMetadataSerializer

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
        serializer = LibraryXBlockMetadataSerializer(paginated_xblock_metadata, many=True)
        return self.get_paginated_response(serializer.data)

    @convert_exceptions
    @swagger_auto_schema(
        request_body=LibraryXBlockCreationSerializer,
        responses={200: LibraryXBlockMetadataSerializer}
    )
    def post(self, request, lib_key_str):
        """
        Add a new XBlock to this content library
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(library_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)
        serializer = LibraryXBlockCreationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Create a new regular top-level block:
        try:
            result = api.create_library_block(library_key, user_id=request.user.id, **serializer.validated_data)
        except api.IncompatibleTypesError as err:
            raise ValidationError(  # lint-amnesty, pylint: disable=raise-missing-from
                detail={'block_type': str(err)},
            )

        return Response(LibraryXBlockMetadataSerializer(result).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBlockView(APIView):
    """
    Views to work with an existing XBlock in a content library.
    """
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

        return Response(LibraryXBlockMetadataSerializer(result).data)

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
        api.delete_library_block(key)
        return Response({})


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryImportTaskViewSet(GenericViewSet):
    """
    Import blocks from Courseware through modulestore.
    """

    queryset = []  # type: ignore[assignment]
    serializer_class = ContentLibraryBlockImportTaskSerializer

    @convert_exceptions
    def list(self, request, lib_key_str):
        """
        List all import tasks for this library.
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(
            library_key,
            request.user,
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY
        )
        queryset = ContentLibrary.objects.get_by_key(library_key).import_tasks
        result = ContentLibraryBlockImportTaskSerializer(queryset, many=True).data

        return self.get_paginated_response(
            self.paginate_queryset(result)
        )

    @convert_exceptions
    @swagger_auto_schema(
        request_body=ContentLibraryBlockImportTaskCreateSerializer,
        responses={200: ContentLibraryBlockImportTaskSerializer}
    )
    def create(self, request, lib_key_str):
        """
        Create and queue an import tasks for this library.
        """

        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(
            library_key,
            request.user,
            permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )

        serializer = ContentLibraryBlockImportTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course_key = serializer.validated_data['course_key']

        import_task = api.import_blocks_create_task(library_key, course_key)
        return Response(ContentLibraryBlockImportTaskSerializer(import_task).data)

    @convert_exceptions
    def retrieve(self, request, lib_key_str, pk=None):
        """
        Retrieve a import task for inspection.
        """

        library_key = LibraryLocatorV2.from_string(lib_key_str)
        api.require_permission_for_library_key(
            library_key,
            request.user,
            permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
        )

        import_task = api.ContentLibraryBlockImportTask.objects.get(pk=pk)
        return Response(ContentLibraryBlockImportTaskSerializer(import_task).data)


# Library Backup Views
# ====================

@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryBackupView(APIView):
    """
    **Use Case**
    * Start an asynchronous task to back up the content of a library to a .zip file
    * Get a status on an asynchronous export task

    **Example Requests**
        POST /api/libraries/v2/{library_id}/backup/
        GET  /api/libraries/v2/{library_id}/backup/?task_id={task_id}

    **POST Response Values**

        If the import task is started successfully, an HTTP 200 "OK" response is
        returned.

        The HTTP 200 response has the following values:

        * task_id: UUID of the created task, usable for checking status

    **Example POST Response**

        {
            "task_id": "7069b95b-ccea-4214-b6db-e00f27065bf7"
        }

    **GET Parameters**

        A GET request must include the following parameters:

        * task_id: (required) The UUID of the task to check.

    **GET Response Values**

        If the import task is found successfully by the UUID provided, an HTTP
        200 "OK" response is returned.

        The HTTP 200 response has the following values:

        * state: String description of the state of the task.
            Possible states: "Pending", "Exporting", "Succeeded", "Failed".
        * url: (may be null) If the task is complete, a URL to download the .zip file

    **Example GET Response**
        {
            "state": "Succeeded",
            "url": "/media/user_tasks/2025/10/03/lib-wgu-csprob-2025-10-03-153633.zip"
        }

    """

    @apidocs.schema(
        body=None,
        responses={200: LibraryBackupResponseSerializer}
    )
    @convert_exceptions
    def post(self, request, lib_key_str):
        """
        Start backup task for the specified library.
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        # Using CAN_EDIT_THIS_CONTENT_LIBRARY permission for now. This should eventually become its own permission
        api.require_permission_for_library_key(library_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)

        async_result = backup_library.delay(request.user.id, str(library_key))
        result = {'task_id': async_result.task_id}

        return Response(LibraryBackupResponseSerializer(result).data)

    @apidocs.schema(
        parameters=[
            apidocs.query_parameter(
                'task_id',
                str,
                description="The ID of the backup task to retrieve."
            ),
        ],
        responses={200: LibraryBackupTaskStatusSerializer}
    )
    @convert_exceptions
    def get(self, request, lib_key_str):
        """
        Get the status of the specified backup task for the specified library.
        """
        library_key = LibraryLocatorV2.from_string(lib_key_str)
        # Using CAN_EDIT_THIS_CONTENT_LIBRARY permission for now. This should eventually become its own permission
        api.require_permission_for_library_key(library_key, request.user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY)

        task_id = request.query_params.get('task_id', None)
        if not task_id:
            raise ValidationError(detail={'task_id': _('This field is required.')})
        result = get_backup_task_status(request.user.id, task_id)

        if not result:
            raise NotFound(detail="No backup found for this library.")
        # Passing request context to the serializer so the url absolute path is correctly generated
        return Response(LibraryBackupTaskStatusSerializer(result, context={'request': request}).data)


@method_decorator(non_atomic_requests, name="dispatch")
@view_auth_classes()
class LibraryRestoreView(APIView):
    """
    Restore a library from a backup file.

    After the file is uploaded, a background task will be started to process the
    file and restore the library contents. You can use the returned `task_id` to
    check the status of the restore task.

    The result of the restore task will be a "staged" learning package that can
    then be saved into a content library.

    **POST Parameters**

        A POST request must include the following parameters.

        * file: (required) The backup file to restore the library from. Must be a
          .zip file.

    **GET Parameters**

        A GET request must include the following parameters.

        * task_id: (required) The UUID of a restore task.
    """
    @apidocs.schema(
        body=LibraryRestoreFileSerializer,
        responses={200: LibraryRestoreFileSerializer}
    )
    def post(self, request):
        """
        Restore a library from a backup file.
        """
        if not api.user_can_create_library(request.user):
            raise PermissionDenied

        serializer = LibraryRestoreFileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data['file']

        storage_path = course_import_export_storage.save(f'library_restore/{upload.name}', upload)

        log.info("Learning package archive upload %s: Upload complete", upload.name)

        async_result = restore_library.delay(request.user.id, storage_path)

        return Response(LibraryRestoreFileSerializer({'task_id': async_result.task_id}).data)

    @apidocs.schema(
        parameters=[
            apidocs.query_parameter(
                'task_id',
                str,
                description="The ID of the restore library task to retrieve."
            ),
        ],
        responses={200: LibraryRestoreTaskResultSerializer}
    )
    def get(self, request):
        """
        Check the status of a library restore task.
        """
        # validate input
        serializer = LibraryRestoreTaskRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        task_id = serializer.validated_data.get('task_id')

        # get task status and related artifact
        task_status = get_object_or_404(UserTaskStatus, task_id=task_id, user=request.user)

        # serialize and return result
        result_serializer = LibraryRestoreTaskResultSerializer.from_task_status(task_status, request)
        return Response(result_serializer.data)


# LTI 1.3 Views
# =============


def requires_lti_enabled(view_func):
    """
    Modify the view function to raise 404 if content librarie LTI tool was not
    enabled.
    """
    def wrapped_view(*args, **kwargs):
        lti_enabled = (settings.FEATURES.get('ENABLE_CONTENT_LIBRARIES')
                       and settings.FEATURES.get('ENABLE_CONTENT_LIBRARIES_LTI_TOOL'))
        if not lti_enabled:
            raise Http404()
        return view_func(*args, **kwargs)
    return wrapped_view


@method_decorator(non_atomic_requests, name="dispatch")
@method_decorator(requires_lti_enabled, name='dispatch')
class LtiToolView(View):
    """
    Base LTI View initializing common attributes.
    """

    # pylint: disable=attribute-defined-outside-init
    def setup(self, request, *args, **kwds):
        """
        Initialize attributes shared by all LTI views.
        """
        super().setup(request, *args, **kwds)
        self.lti_tool_config = DjangoDbToolConf()
        self.lti_tool_storage = DjangoCacheDataStorage(cache_name='default')


@method_decorator(non_atomic_requests, name="dispatch")
@method_decorator(csrf_exempt, name='dispatch')
class LtiToolLoginView(LtiToolView):
    """
    Third-party Initiated Login view.

    The LTI platform will start the OpenID Connect flow by redirecting the User
    Agent (UA) to this view. The redirect may be a form POST or a GET.  On
    success the view should redirect the UA to the LTI platform's authentication
    URL.
    """

    LAUNCH_URI_PARAMETER = 'target_link_uri'

    def get(self, request):
        return self.post(request)

    def post(self, request):
        """Initialize 3rd-party login requests to redirect."""
        oidc_login = DjangoOIDCLogin(
            self.request,
            self.lti_tool_config,
            launch_data_storage=self.lti_tool_storage)
        launch_url = (self.request.POST.get(self.LAUNCH_URI_PARAMETER)
                      or self.request.GET.get(self.LAUNCH_URI_PARAMETER))
        try:
            return oidc_login.redirect(launch_url)
        except OIDCException as exc:
            # Relying on downstream error messages, attempt to sanitize it up
            # for customer facing errors.
            log.error('LTI OIDC login failed: %s', exc)
            return HttpResponseBadRequest('Invalid LTI login request.')


@method_decorator(non_atomic_requests, name="dispatch")
@method_decorator(csrf_exempt, name='dispatch')
@method_decorator(xframe_options_exempt, name='dispatch')
class LtiToolLaunchView(TemplateResponseMixin, LtiToolView):
    """
    LTI platform tool launch view.

    The launch view supports resource link launches and AGS, when enabled by the
    LTI platform.  Other features and resouces are ignored.
    """

    template_name = 'xblock_v2/xblock_iframe.html'

    @property
    def launch_data(self):
        return self.launch_message.get_launch_data()

    def _authenticate_and_login(self, usage_key):
        """
        Authenticate and authorize the user for this LTI message launch.

        We automatically create LTI profile for every valid launch, and
        authenticate the LTI user associated with it.
        """

        # Check library authorization.

        if not ContentLibrary.authorize_lti_launch(
                usage_key.lib_key,
                issuer=self.launch_data['iss'],
                client_id=self.launch_data['aud']
        ):
            return None

        # Check LTI profile.

        LtiProfile.objects.get_or_create_from_claims(
            iss=self.launch_data['iss'],
            aud=self.launch_data['aud'],
            sub=self.launch_data['sub'])
        edx_user = authenticate(
            self.request,
            iss=self.launch_data['iss'],
            aud=self.launch_data['aud'],
            sub=self.launch_data['sub'])

        if edx_user is not None:
            login(self.request, edx_user)
            perms = api.get_library_user_permissions(
                usage_key.lib_key,
                self.request.user)
            if not perms:
                api.set_library_user_permissions(
                    usage_key.lib_key,
                    self.request.user,
                    api.AccessLevel.ADMIN_LEVEL)

        return edx_user

    def _bad_request_response(self):
        """
        A default response for bad requests.
        """
        return HttpResponseBadRequest('Invalid LTI tool launch.')

    def get_context_data(self):
        """
        Setup the template context data.
        """

        handler_urls = {
            str(key): xblock_api.get_handler_url(key, 'handler_name', self.request.user)
            for key
            in itertools.chain([self.block.scope_ids.usage_id],
                               getattr(self.block, 'children', []))
        }

        # We are defaulting to student view due to current use case (resource
        # link launches).  Launches within other views are not currently
        # supported.
        fragment = self.block.render('student_view')
        lms_root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
        return {
            'fragment': fragment,
            'handler_urls_json': json.dumps(handler_urls),
            'lms_root_url': lms_root_url,
        }

    def get_launch_message(self):
        """
        Return the LTI 1.3 launch message object for the current request.
        """
        launch_message = DjangoMessageLaunch(
            self.request,
            self.lti_tool_config,
            launch_data_storage=self.lti_tool_storage)
        # This will force the LTI launch validation steps.
        launch_message.get_launch_data()
        return launch_message

    # pylint: disable=attribute-defined-outside-init
    def post(self, request):
        """
        Process LTI platform launch requests.
        """

        # Parse LTI launch message.

        try:
            self.launch_message = self.get_launch_message()
        except LtiException as exc:
            log.exception('LTI 1.3: Tool launch failed: %s', exc)
            return self._bad_request_response()

        log.info("LTI 1.3: Launch message body: %s",
                 json.dumps(self.launch_data))

        # Parse content key.

        usage_key_str = request.GET.get('id')
        if not usage_key_str:
            return self._bad_request_response()
        usage_key = LibraryUsageLocatorV2.from_string(usage_key_str)
        log.info('LTI 1.3: Launch block: id=%s', usage_key)

        # Authenticate the launch and setup LTI profiles.

        edx_user = self._authenticate_and_login(usage_key)
        if not edx_user:
            return self._bad_request_response()

        # Get the block.

        self.block = xblock_api.load_block(
            usage_key,
            user=self.request.user)

        # Handle Assignment and Grade Service request.

        self.handle_ags()

        # Render context and response.
        context = self.get_context_data()
        response = self.render_to_response(context)
        mark_user_change_as_expected(edx_user.id)
        return response

    def handle_ags(self):
        """
        Handle AGS-enabled launches for block in the request.
        """

        # Validate AGS.

        if not self.launch_message.has_ags():
            return

        endpoint_claim = 'https://purl.imsglobal.org/spec/lti-ags/claim/endpoint'
        endpoint = self.launch_data[endpoint_claim]
        required_scopes = [
            'https://purl.imsglobal.org/spec/lti-ags/scope/lineitem',
            'https://purl.imsglobal.org/spec/lti-ags/scope/score'
        ]

        for scope in required_scopes:
            if scope not in endpoint['scope']:
                log.info('LTI 1.3: AGS: LTI platform does not support a required '
                         'scope: %s', scope)
                return
        lineitem = endpoint.get('lineitem')
        if not lineitem:
            log.info("LTI 1.3: AGS: LTI platform didn't pass lineitem, ignoring "
                     "request: %s", endpoint)
            return

        # Create graded resource in the database for the current launch.

        resource_claim = 'https://purl.imsglobal.org/spec/lti/claim/resource_link'
        resource_link = self.launch_data.get(resource_claim)

        resource = LtiGradedResource.objects.upsert_from_ags_launch(
            self.request.user, self.block, endpoint, resource_link
        )

        log.info("LTI 1.3: AGS: Upserted LTI graded resource from launch: %s",
                 resource)


@method_decorator(non_atomic_requests, name="dispatch")
class LtiToolJwksView(LtiToolView):
    """
    JSON Web Key Sets view.
    """

    def get(self, request):
        """
        Return the JWKS.
        """
        return JsonResponse(self.lti_tool_config.get_jwks(), safe=False)
