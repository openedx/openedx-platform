"""
REST API views for content staging
"""
from __future__ import annotations

import edx_api_doc_tools as apidocs
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import CourseLocator, LibraryLocatorV2
from openedx_authz.constants import permissions as authz_permissions
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.auth import has_studio_read_access
from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.lib.api.view_utils import view_auth_classes
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from . import api
from .data import StagedContentStatus
from .models import StagedContent
from .serializers import PostToClipboardSerializer, UserClipboardSerializer


@view_auth_classes(is_authenticated=True)
class StagedContentOLXEndpoint(APIView):
    """
    API Endpoint to get the OLX of any given StagedContent.
    """

    def get(self, request, id):  # pylint: disable=redefined-builtin
        """
        Get the OLX of the given StagedContent object.
        """
        staged_content = get_object_or_404(StagedContent, pk=id)
        if staged_content.user.id != request.user.id:
            raise PermissionDenied("Users can only access their own staged content")
        if staged_content.status != StagedContentStatus.READY:
            # If the status is LOADING, the OLX may not be generated/valid yet.
            # If the status is ERROR or EXPIRED, this row is no longer usable.
            raise NotFound("The requested content is not available.")
        return HttpResponse(staged_content.olx, headers={
            "Content-Type": f"application/vnd.openedx.xblock.v1.{staged_content.block_type}+xml",
            "Content-Disposition": f'attachment; filename="{staged_content.olx_filename}"',
        })


@method_decorator(transaction.non_atomic_requests, name='dispatch')
@view_auth_classes(is_authenticated=True)
class ClipboardEndpoint(APIView):
    """
    API Endpoint that can be used to get the status of the current user's
    clipboard or to POST some content to the clipboard.
    """

    @apidocs.schema(
        responses={
            200: UserClipboardSerializer,
        }
    )
    def get(self, request):
        """
        Get the detailed status of the user's clipboard. This does not return the OLX.
        """
        return Response(api.get_user_clipboard_json(request.user.id, request))

    @apidocs.schema(
        body=PostToClipboardSerializer,
        responses={
            200: UserClipboardSerializer,
            403: "You do not have permission to read the specified usage key.",
            404: "The requested usage key does not exist.",
        },
    )
    def post(self, request):
        """
        Put some piece of content into the user's clipboard.

        FIXME: This API needs to be deprecated and replaced by dedicated APIs
        within each learning context (POST /course/foo/bar/copy, POST
        /library/foo/bar/copy, etc.) We don't want to encode course- and
        library-specific logic in content staging, and it shouldn't import
        course or library modules.
        """
        # Check if the content exists and the user has permission to read it.
        # Parse the usage key:
        try:
            usage_key = UsageKey.from_string(request.data["usage_key"])
        except (ValueError, InvalidKeyError):
            raise ValidationError('Invalid usage key')  # pylint: disable=raise-missing-from  # noqa: B904
        if usage_key.block_type in ('course', 'chapter', 'sequential'):
            raise ValidationError('Requested XBlock tree is too large')
        course_key = usage_key.context_key

        # Load the block and copy it to the user's clipboard
        try:
            if isinstance(course_key, CourseLocator):
                # Make sure the user has permission on that course
                if not has_studio_read_access(request.user, course_key):
                    raise PermissionDenied(
                        "You must be a member of the course team in Studio to export OLX using this API."
                    )
                block = modulestore().get_item(usage_key)
                version_num = None

            elif isinstance(course_key, LibraryLocatorV2):
                from openedx.core.djangoapps.content_libraries import api as lib_api

                lib_api.require_permission_for_library_key(
                    course_key,
                    request.user,
                    authz_permissions.REUSE_LIBRARY_CONTENT
                )
                block = xblock_api.load_block(usage_key, user=None)
                version_num = lib_api.get_library_block(usage_key).draft_version_num

            else:
                raise ValidationError("Invalid usage_key for the content.")

        except ItemNotFoundError as exc:
            raise NotFound("The requested usage key does not exist.") from exc

        clipboard = api.save_xblock_to_user_clipboard(block=block, version_num=version_num, user_id=request.user.id)

        # Return the current clipboard exactly as if GET was called:
        serializer = UserClipboardSerializer(clipboard, context={"request": request})
        return Response(serializer.data)
