"""
Views that implement a RESTful API for interacting with XBlocks.
"""
import json
from pathlib import Path

import openassessment
from corsheaders.signals import check_request_enabled
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.transaction import atomic
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from opaque_keys.edx.keys import UsageKeyV2
from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes  # pylint: disable=unused-import
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied
from rest_framework.fields import BooleanField
from rest_framework.response import Response
from rest_framework.views import APIView
from xblock.django.request import DjangoWebobRequest, webob_to_django_response
from xblock.exceptions import NoSuchUsage
from xblock.fields import Scope

import openedx.core.djangoapps.site_configuration.helpers as configuration_helpers
from common.djangoapps.util.json_request import JsonResponse
from openedx.core.djangoapps.xblock.learning_context.manager import get_learning_context_impl
from openedx.core.lib.api.view_utils import view_auth_classes

from ..api import CheckPerm, LatestVersion, get_block_display_name, get_block_metadata, get_block_olx, load_block
from ..api import get_handler_url as _get_handler_url
from ..api import render_block_view as _render_block_view
from ..utils import validate_secure_token_for_xblock_handler
from .serializers import XBlockOlxSerializer
from .url_converters import VersionConverter

User = get_user_model()


@api_view(['GET'])
@view_auth_classes(is_authenticated=False)
@permission_classes((permissions.AllowAny, ))  # Permissions are handled at a lower level, by the learning context
def block_metadata(request, usage_key: UsageKeyV2, version: LatestVersion | int = LatestVersion.AUTO):
    """
    Get metadata about the specified block.

    Accepts the following query parameters:

    * "include": a comma-separated list of keys to include.
      Valid keys are "index_dictionary" and "student_view_data".
    """
    block = load_block(usage_key, request.user, version=version)
    includes = request.GET.get("include", "").split(",")
    metadata_dict = get_block_metadata(block, includes=includes)
    if 'children' in metadata_dict:
        metadata_dict['children'] = [str(key) for key in metadata_dict['children']]
    if 'editable_children' in metadata_dict:
        metadata_dict['editable_children'] = [str(key) for key in metadata_dict['editable_children']]
    return Response(metadata_dict)


@api_view(['GET'])
@view_auth_classes(is_authenticated=False)
@permission_classes((permissions.AllowAny, ))  # Permissions are handled at a lower level, by the learning context
def render_block_view(
    request,
    usage_key: UsageKeyV2,
    view_name: str,
    version: LatestVersion | int = LatestVersion.AUTO,
):
    """
    Get the HTML, JS, and CSS needed to render the given XBlock.
    """
    try:
        block = load_block(usage_key, request.user, version=version)
    except NoSuchUsage as exc:
        raise NotFound(f"{usage_key} not found") from exc

    fragment = _render_block_view(block, view_name, request.user)
    response_data = get_block_metadata(block)
    response_data.update(fragment.to_dict())
    return Response(response_data)


@api_view(['GET'])
@view_auth_classes(is_authenticated=False)
@permission_classes((permissions.AllowAny, ))  # Permissions are handled at a lower level, by the learning context
@xframe_options_exempt
def embed_block_view(request, usage_key: UsageKeyV2, view_name: str):
    """
    Render the given XBlock in an <iframe>

    Unstable - may change after Sumac
    """
    # Check if a specific version has been requested. TODO: move this to a URL path param like the other views?
    show_title = request.GET.get('show_title', False)
    if show_title is not None:
        show_title = BooleanField().to_internal_value(show_title)

    try:
        version = VersionConverter().to_python(request.GET.get("version"))
    except ValueError as exc:
        raise serializers.ValidationError("Invalid version specifier") from exc

    try:
        block = load_block(usage_key, request.user, check_permission=CheckPerm.CAN_LEARN, version=version)
    except NoSuchUsage as exc:
        raise NotFound(f"{usage_key} not found") from exc

    fragment = _render_block_view(block, view_name, request.user)
    handler_urls = {
        str(block.usage_key): _get_handler_url(block.usage_key, 'handler_name', request.user, version=version)
    }
    # Currently we don't support child blocks so we don't need this pre-loading of child handler URLs:
    # handler_urls = {
    #     str(key): _get_handler_url(key, 'handler_name', request.user)
    #     for key in itertools.chain([block.scope_ids.usage_id], getattr(block, 'children', []))
    # }
    lms_root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    cms_root_url = configuration_helpers.get_value('CMS_ROOT_URL', settings.CMS_ROOT_URL)
    openassessment_path = Path(openassessment.__path__[0])
    oa_manifest_path = openassessment_path / "xblock" / "static" / "dist" / "manifest.json"

    new_oa_manifest = {}
    if oa_manifest_path.exists():
        with open(oa_manifest_path, "r") as f:  # noqa: UP015
            oa_manifest = json.load(f)
            new_oa_manifest = {
                # When we add the RTL style, it automatically applies that style (right-to-left reading) regardless
                # of the language.
                # We weren't sure of where to place that conditional code, so we just defaulted to the LTR style for
                # now, until we are more clear on how to handle rtl/ltr conditionally.
                'oa_ltr_css': oa_manifest.get("openassessment-ltr.css", ""),
                'oa_ltr_js': oa_manifest.get("openassessment-ltr.js", ""),
                'oa_editor_textarea_js': oa_manifest.get("openassessment-editor-textarea.js", ""),
                'oa_editor_tinymce_js': oa_manifest.get("openassessment-editor-tinymce.js", ""),
            }

    context = {
        'fragment': fragment,
        'handler_urls_json': json.dumps(handler_urls),
        'lms_root_url': lms_root_url,
        'cms_root_url': cms_root_url,
        'view_name': view_name,
        'is_development': settings.DEBUG,
        'oa_manifest': new_oa_manifest,
        'display_name': block.display_name,
        'show_title': show_title,
    }
    response = render(request, 'xblock_v2/xblock_iframe.html', context, content_type='text/html')

    # Only allow this iframe be embedded if the parent is in the CORS_ORIGIN_WHITELIST
    cors_origin_whitelist = configuration_helpers.get_value('CORS_ORIGIN_WHITELIST', settings.CORS_ORIGIN_WHITELIST)
    response["Content-Security-Policy"] = f"frame-ancestors 'self' {' '.join(cors_origin_whitelist)};"

    return response


@api_view(['GET'])
@view_auth_classes(is_authenticated=False)
def get_handler_url(
    request,
    usage_key: UsageKeyV2,
    handler_name: str,
    version: LatestVersion | int = LatestVersion.AUTO,
):
    """
    Get an absolute URL which can be used (without any authentication) to call
    the given XBlock handler.

    The URL will expire but is guaranteed to be valid for a minimum of 2 days.
    """
    handler_url = _get_handler_url(usage_key, handler_name, request.user, version=version)
    return Response({"handler_url": handler_url})


# We cannot use DRF for this endpoint because its Request object is incompatible
# with the API expected by XBlock handlers.
# See https://github.com/openedx/edx-platform/pull/19253
# and https://github.com/openedx/XBlock/pull/383 for context.
@csrf_exempt
@xframe_options_exempt
def xblock_handler(
    request,
    user_id,
    secure_token: str,
    usage_key: UsageKeyV2,
    handler_name: str,
    suffix: str | None = None,
    version: LatestVersion | int = LatestVersion.AUTO,
):
    """
    Run an XBlock's handler and return the result

    This endpoint has a unique authentication scheme that involves a temporary
    auth token included in the URL (see below). As a result it can be exempt
    from CSRF, session auth, and JWT/OAuth.
    """
    # To support sandboxed XBlocks, custom frontends, and other use cases, we
    # authenticate requests using a secure token in the URL. see
    # openedx.core.djangoapps.xblock.utils.get_secure_token_for_xblock_handler
    # for details and rationale.
    if not validate_secure_token_for_xblock_handler(user_id, str(usage_key), secure_token):
        raise PermissionDenied("Invalid/expired auth token.")
    if request.user.is_authenticated:
        # The user authenticated twice, e.g. with session auth and the token.
        # This can happen if not running the XBlock in a sandboxed iframe.
        # Just make sure the session auth matches the token:
        if request.user.id != int(user_id):
            raise AuthenticationFailed("Authentication conflict.")
        user = request.user
    elif user_id.isdigit():
        # This is a normal (integer) user ID for a registered user.
        # This is the "normal" way this view gets used, with a sandboxed iframe.
        user = User.objects.get(pk=int(user_id))
    elif user_id.startswith("anon"):
        # This is a non-registered (anonymous) user:
        assert request.user.is_anonymous
        assert not hasattr(request.user, 'xblock_id_for_anonymous_user')
        user = request.user  # An AnonymousUser
        # Since this particular view usually gets called from a sandboxed iframe
        # we won't have access to the LMS session data for this user (the iframe
        # has a new, empty session). So we need to save the identifier for this
        # anonymous user (from the URL) on the user object, so that the runtime
        # can get it (instead of generating a new one and saving it into this
        # new empty session)
        # See djangoapps.xblock.utils.get_xblock_id_for_anonymous_user()
        user.xblock_id_for_anonymous_user = user_id
    else:
        raise AuthenticationFailed("Invalid user ID format.")

    request_webob = DjangoWebobRequest(request)  # Convert from django request to the webob format that XBlocks expect

    block = load_block(usage_key, user, version=version)
    # Run the handler, and save any resulting XBlock field value changes:
    response_webob = block.handle(handler_name, request_webob, suffix)
    response = webob_to_django_response(response_webob)
    return response


@api_view(['GET'])
@view_auth_classes(is_authenticated=False)
def get_block_olx_view(
    request,
    usage_key: UsageKeyV2,
    version: LatestVersion | int = LatestVersion.AUTO,
):
    """
    Get the OLX (XML serialization) of the specified XBlock
    """
    context_impl = get_learning_context_impl(usage_key)
    if not context_impl.can_view_block_for_editing(request.user, usage_key):
        raise PermissionDenied(f"You don't have permission to access the OLX of component '{usage_key}'.")
    olx = get_block_olx(usage_key, version=version)
    return Response(XBlockOlxSerializer({"olx": olx}).data)


def cors_allow_xblock_handler(sender, request, **kwargs):  # pylint: disable=unused-argument
    """
    Sandboxed XBlocks need to be able to call XBlock handlers via POST,
    from a different domain. See 'xblock_handler' method for details and how security is
    enforced.
    Per the corsheaders docs, a signal is the only way to achieve this for
    just a specific view/URL.
    """
    return request.path.startswith('/api/xblock/v2/xblocks/') and '/handler/' in request.path


check_request_enabled.connect(cors_allow_xblock_handler)


@view_auth_classes()
class BlockFieldsView(APIView):
    """
    View to get/edit the field values of an XBlock as JSON (in the v2 runtime)

    This class mimics the functionality of xblock_handler in block.py (for v1 xblocks), but for v2 xblocks.
    However, it only implements the exact subset of functionality needed to support the v2 editors (in
    the Course Authoring MFE). As such, it only supports GET and POST, and only the
    POSTing of data/metadata fields.
    """

    @atomic
    def get(self, request, usage_key: UsageKeyV2, version: LatestVersion | int = LatestVersion.AUTO):
        """
        retrieves the xblock, returning display_name, data, and metadata
        """

        # The "fields" view requires "read as author" permissions because the fields can contain answers, etc.
        block = load_block(usage_key, request.user, check_permission=CheckPerm.CAN_READ_AS_AUTHOR, version=version)
        # It would make more sense if this just had a "fields" dict with all the content+settings fields, but
        # for backwards compatibility we call the settings metadata and split it up like this, ignoring all content
        # fields except "data".
        block_dict = {
            "display_name": get_block_display_name(block),  # note this is also present in metadata
            "metadata": self.get_explicitly_set_fields_by_scope(block, Scope.settings),
        }
        if hasattr(block, "data"):
            block_dict["data"] = block.data
        return Response(block_dict)

    @atomic
    def post(self, request, usage_key, version: LatestVersion | int = LatestVersion.AUTO):
        """
        edits the xblock, saving changes to data and metadata only (display_name included in metadata)
        """
        if version != LatestVersion.AUTO:
            raise serializers.ValidationError("Cannot specify a version when saving changes")
        user = request.user
        block = load_block(usage_key, user, check_permission=CheckPerm.CAN_EDIT)
        data = request.data.get("data")
        metadata = request.data.get("metadata")

        old_metadata = self.get_explicitly_set_fields_by_scope(block, Scope.settings)
        old_content = self.get_explicitly_set_fields_by_scope(block, Scope.content)

        # only update data if it was passed
        if data is not None:
            block.data = data

        # update existing metadata with submitted metadata (which can be partial)
        # IMPORTANT NOTE: if the client passed 'null' (None) for a piece of metadata that means 'remove it'.
        if metadata is not None:
            for metadata_key, value in metadata.items():
                field = block.fields[metadata_key]

                if value is None:
                    field.delete_from(block)
                else:
                    try:
                        value = field.from_json(value)
                    except ValueError as verr:
                        reason = _("Invalid data")
                        if str(verr):
                            reason = _("Invalid data ({details})").format(
                                details=str(verr)
                            )
                        return JsonResponse({"error": reason}, 400)

                    field.write_to(block, value)

        if callable(getattr(block, "editor_saved", None)):
            block.editor_saved(user, old_metadata, old_content)

        # Save after the callback so any changes made in the callback will get persisted.
        block.save()

        block_dict = {
            "id": str(block.usage_key),
            "display_name": get_block_display_name(block),  # note this is also present in metadata
            "metadata": self.get_explicitly_set_fields_by_scope(block, Scope.settings),
        }
        if hasattr(block, "data"):
            block_dict["data"] = block.data
        return Response(block_dict)

    def get_explicitly_set_fields_by_scope(self, block, scope=Scope.content):
        """
        Get a dictionary of the fields for the given scope which are set explicitly on the given xblock.

        (Including any set to None.)
        """
        result = {}
        for field in block.fields.values():  # pylint: disable=no-member
            if field.scope == scope and field.is_set_on(block):
                try:
                    result[field.name] = field.read_json(block)
                except TypeError as exc:
                    raise TypeError(f"Unable to read field {field.name} from block {block.usage_key}") from exc
        return result
