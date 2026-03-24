"""
REST API for getting modulestore XBlocks as OLX
"""
from django.http import HttpResponse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import CourseLocator
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from common.djangoapps.student.auth import has_studio_read_access
from openedx.core.lib.api.view_utils import view_auth_classes
from xmodule.modulestore.django import modulestore

from openedx.core.lib.xblock_serializer.api import serialize_modulestore_block_for_openedx_content


@api_view(['GET'])
@view_auth_classes()
def get_block_olx(request, usage_key_str):
    """
    Given a modulestore XBlock usage ID (block-v1:...), get its OLX and a list
    of any static asset files it uses.
    (There are other APIs for getting the OLX of openedx_content XBlocks.)
    """
    # Parse the usage key:
    try:
        usage_key = UsageKey.from_string(usage_key_str)
    except (ValueError, InvalidKeyError):
        raise ValidationError('Invalid usage key')  # lint-amnesty, pylint: disable=raise-missing-from
    if usage_key.block_type in ('course', 'chapter', 'sequential'):
        raise ValidationError('Requested XBlock tree is too large - export verticals or their children only')
    course_key = usage_key.context_key
    if not isinstance(course_key, CourseLocator):
        raise ValidationError('Invalid usage key: not a modulestore course')
    # Make sure the user has permission on that course
    if not has_studio_read_access(request.user, course_key):
        raise PermissionDenied("You must be a member of the course team in Studio to export OLX using this API.")

    # Step 1: Serialize the XBlocks to OLX files + static asset files

    serialized_blocks = {}  # Key is each XBlock's original usage key

    def serialize_block(block_key):
        """ Inner method to recursively serialize an XBlock to OLX """
        if block_key in serialized_blocks:
            return

        block = modulestore().get_item(block_key)
        serialized_blocks[block_key] = serialize_modulestore_block_for_openedx_content(block)

        if block.has_children:
            for child_id in block.children:
                serialize_block(child_id)

    serialize_block(usage_key)

    result = {
        "root_block_id": str(usage_key),
        "blocks": {},
    }

    # For each XBlock that we're exporting:
    for this_usage_key, data in serialized_blocks.items():
        block_data_out = {"olx": data.olx_str}

        for asset_file in data.static_files:
            if asset_file.url:
                url = request.build_absolute_uri(asset_file.url)
            else:
                # The file is not in GridFS so we don't have a URL for it; serve it
                # via our own get_block_exportfs_file API endpoint.
                url = request.build_absolute_uri(
                    '/api/olx-export/v1/xblock-export-file/' + str(this_usage_key) + '/' + asset_file.name,
                )
            block_data_out.setdefault("static_files", {})[asset_file.name] = {"url": url}

        result["blocks"][str(data.orig_block_key)] = block_data_out

    return Response(result)


@api_view(['GET'])
@view_auth_classes()
def get_block_exportfs_file(request, usage_key_str, path):
    """
    Serve a static file that got added to the XBlock's export_fs during XBlock
    serialization. Typically these would be video transcript files.
    """
    # Parse the usage key:
    try:
        usage_key = UsageKey.from_string(usage_key_str)
    except (ValueError, InvalidKeyError):
        raise ValidationError('Invalid usage key')  # lint-amnesty, pylint: disable=raise-missing-from
    if usage_key.block_type in ('course', 'chapter', 'sequential'):
        raise ValidationError('Requested XBlock tree is too large - export verticals or their children only')
    course_key = usage_key.context_key
    if not isinstance(course_key, CourseLocator):
        raise ValidationError('Invalid usage key: not a modulestore course')
    # Make sure the user has permission on that course
    if not has_studio_read_access(request.user, course_key):
        raise PermissionDenied("You must be a member of the course team in Studio to export OLX using this API.")

    block = modulestore().get_item(usage_key)
    serialized = serialize_modulestore_block_for_openedx_content(block)
    static_file = None
    for f in serialized.static_files:
        if f.name == path:
            static_file = f
            break
    if static_file is None:
        raise NotFound

    response = HttpResponse(static_file.data, content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{path}"'
    return response
