"""Views for assets"""


import json
import logging
import math
import re
from functools import partial
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest, HttpResponseNotFound
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST
from opaque_keys.edx.keys import AssetKey, CourseKey
from pymongo import ASCENDING, DESCENDING

from common.djangoapps.student.auth import has_course_author_access
from common.djangoapps.util.date_utils import get_default_time_display
from common.djangoapps.util.json_request import JsonResponse
from openedx.core.djangoapps.contentserver.caching import del_cached_content
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx_filters.content_authoring.filters import LMSPageURLRequested
from xmodule.contentstore.content import StaticContent  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.exceptions import NotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

from .exceptions import AssetNotFoundException, AssetSizeTooLargeException
from .utils import get_files_uploads_url, get_response_format, request_response_format_is_json


REQUEST_DEFAULTS = {
    'page': 0,
    'page_size': 50,
    'sort': 'date_added',
    'direction': '',
    'asset_type': '',
    'text_search': '',
    'display_name': '',
}


def handle_assets(request, course_key_string=None, asset_key_string=None):
    '''
    The restful handler for assets.
    It allows retrieval of all the assets (as an HTML page), as well as uploading new assets,
    deleting assets, and changing the 'locked' state of an asset.

    GET
        html: return an html page which will show all course assets. Note that only the asset container
            is returned and that the actual assets are filled in with a client-side request.
        json: returns a page of assets. The following parameters are supported:
            page: the desired page of results (defaults to 0)
            page_size: the number of items per page (defaults to 50)
            sort: the asset field to sort by (defaults to 'date_added')
            direction: the sort direction (defaults to 'descending')
            asset_type: the file type to filter items to (defaults to All)
            text_search: string to perform a search on filenames (defaults to '')
            display_name: string to filter results by exact display name (defaults to '').
                Use the display_name parameter multiple times to filter by multiple filenames.
    POST
        json: create or update an asset. The only updating that can be done is changing the lock state.
    PUT
        json: create or update an asset. The only updating that can be done is changing the lock state.
    DELETE
        json: delete an asset
    '''
    course_key = CourseKey.from_string(course_key_string)
    if not has_course_author_access(request.user, course_key):
        raise PermissionDenied()

    response_format = get_response_format(request)
    if request_response_format_is_json(request, response_format):
        if request.method == 'GET':
            return _assets_json(request, course_key)

        # POST, PUT, DELETE typically invoke this
        asset_key = AssetKey.from_string(asset_key_string) if asset_key_string else None
        return update_asset(request, course_key, asset_key)

    elif request.method == 'GET':  # assume html
        return _asset_index(request, course_key)

    return HttpResponseNotFound()


def get_asset_usage_path_json(request, course_key, asset_key_string):
    """
    Get a list of units with ancestors that use given asset.
    """
    course_key = CourseKey.from_string(course_key)
    if not has_course_author_access(request.user, course_key):
        raise PermissionDenied()
    asset_location = AssetKey.from_string(asset_key_string) if asset_key_string else None
    usage_locations = _get_asset_usage_path(course_key, [{'asset_key': asset_location}])
    return JsonResponse({'usage_locations': usage_locations})


def _get_asset_usage_path(course_key, assets):
    """
    Get a list of units with ancestors that use given asset.
    """
    store = modulestore()
    usage_locations = {str(asset['asset_key']): [] for asset in assets}
    verticals = store.get_items(
        course_key,
        qualifiers={
            'category': 'vertical'
        },
    )
    blocks = []

    for vertical in verticals:
        blocks.extend(vertical.get_children())

    for block in blocks:
        for asset in assets:
            asset_key = asset['asset_key']
            asset_key_string = str(asset_key)
            static_path = StaticContent.get_static_path_from_location(asset_key)
            is_video_block = getattr(block, 'category', '') == 'video'
            try:
                if is_video_block:
                    handout = getattr(block, 'handout', '')
                    if handout and asset_key_string in handout:
                        usage_dict = {'display_location': '', 'url': ''}
                        xblock_display_name = getattr(block, 'display_name', '')
                        xblock_location = str(block.location)
                        unit = block.get_parent()
                        unit_location = str(block.parent)
                        unit_display_name = getattr(unit, 'display_name', '')
                        subsection = unit.get_parent()
                        subsection_display_name = getattr(subsection, 'display_name', '')
                        current_locations = usage_locations[asset_key_string]
                        usage_dict['display_location'] = (f'{subsection_display_name} - '
                                                          f'{unit_display_name} / {xblock_display_name}')
                        usage_dict['url'] = f'/container/{unit_location}#{xblock_location}'
                        usage_locations[asset_key_string] = [*current_locations, usage_dict]
                else:
                    data = getattr(block, 'data', '')
                    if static_path in data or asset_key_string in data:
                        usage_dict = {'display_location': '', 'url': ''}
                        xblock_display_name = getattr(block, 'display_name', '')
                        xblock_location = str(block.location)
                        unit = block.get_parent()
                        unit_location = str(block.parent)
                        unit_display_name = getattr(unit, 'display_name', '')
                        subsection = unit.get_parent()
                        subsection_display_name = getattr(subsection, 'display_name', '')
                        current_locations = usage_locations[asset_key_string]
                        usage_dict['display_location'] = (f'{subsection_display_name} - '
                                                          f'{unit_display_name} / {xblock_display_name}')
                        usage_dict['url'] = f'/container/{unit_location}#{xblock_location}'
                        usage_locations[asset_key_string] = [*current_locations, usage_dict]
            except AttributeError:
                continue
    return usage_locations


def _asset_index(request, course_key):
    '''
    Display an editable asset library.
    '''
    return redirect(get_files_uploads_url(course_key))


def _assets_json(request, course_key):
    '''
    Display an editable asset library.

    Supports start (0-based index into the list of assets) and max query parameters.
    '''
    request_options = _parse_request_to_dictionary(request)

    filter_parameters = {
        'user_language': UserPreference.get_value(request.user, 'pref-lang') or 'en',
    }

    if request_options['requested_asset_type']:
        filters_are_invalid_error = _get_error_if_invalid_parameters(request_options['requested_asset_type'])

        if filters_are_invalid_error is not None:
            return filters_are_invalid_error

        filter_parameters.update(_get_content_type_filter_for_mongo(request_options['requested_asset_type']))

    if request_options['requested_display_names']:
        filter_parameters.update(_get_displayname_filter_for_mongo(request_options['requested_display_names']))

    if request_options['requested_text_search']:
        filter_parameters.update(_get_displayname_search_filter_for_mongo(request_options['requested_text_search']))

    sort_type_and_direction = _get_sort_type_and_direction(request_options)

    requested_page_size = request_options['requested_page_size']
    current_page = _get_current_page(request_options['requested_page'])
    first_asset_to_display_index = _get_first_asset_index(current_page, requested_page_size)

    query_options = {
        'current_page': current_page,
        'page_size': requested_page_size,
        'sort': sort_type_and_direction,
        'filter_params': filter_parameters
    }

    assets, total_count = _get_assets_for_page(course_key, query_options)

    assets_usage_locations_map = _get_asset_usage_path(course_key, assets)

    if request_options['requested_page'] > 0 and first_asset_to_display_index >= total_count and total_count > 0:  # lint-amnesty, pylint: disable=chained-comparison
        _update_options_to_requery_final_page(query_options, total_count)
        current_page = query_options['current_page']
        first_asset_to_display_index = _get_first_asset_index(current_page, requested_page_size)
        assets, total_count = _get_assets_for_page(course_key, query_options)

    last_asset_to_display_index = first_asset_to_display_index + len(assets)
    assets_in_json_format = _get_assets_in_json_format(assets, course_key, assets_usage_locations_map)

    response_payload = {
        'start': first_asset_to_display_index,
        'end': last_asset_to_display_index,
        'page': current_page,
        'pageSize': requested_page_size,
        'totalCount': total_count,
        'assets': assets_in_json_format,
        'sort': request_options['requested_sort'],
        'direction': request_options['requested_sort_direction'],
        'assetTypes': _get_requested_file_types_from_requested_filter(request_options['requested_asset_type']),
        'textSearch': request_options['requested_text_search'],
    }

    return JsonResponse(response_payload)


def _parse_request_to_dictionary(request):
    return {
        'requested_page': int(_get_requested_attribute(request, 'page')),
        'requested_page_size': int(_get_requested_attribute(request, 'page_size')),
        'requested_sort': _get_requested_attribute(request, 'sort'),
        'requested_sort_direction': _get_requested_attribute(request, 'direction'),
        'requested_asset_type': _get_requested_attribute(request, 'asset_type'),
        'requested_text_search': _get_requested_attribute(request, 'text_search'),
        'requested_display_names': _get_requested_attribute_list(request, 'display_name'),
    }


def _get_requested_attribute(request, attribute):
    return request.GET.get(attribute, REQUEST_DEFAULTS.get(attribute))


def _get_requested_attribute_list(request, attribute):
    return request.GET.getlist(attribute, REQUEST_DEFAULTS.get(attribute))


def _get_error_if_invalid_parameters(requested_filter):
    """Function for returning error messages on filters"""
    requested_file_types = _get_requested_file_types_from_requested_filter(requested_filter)
    invalid_filters = []

    # OTHER is not described in the settings file as a filter
    all_valid_file_types = set(_get_files_and_upload_type_filters().keys())
    all_valid_file_types.add('OTHER')

    for requested_file_type in requested_file_types:
        if requested_file_type not in all_valid_file_types:
            invalid_filters.append(requested_file_type)

    if invalid_filters:
        error_message = {
            'error_code': 'invalid_asset_type_filter',
            'developer_message': 'The asset_type parameter to the request is invalid. '
                                 'The {} filters are not described in the settings.FILES_AND_UPLOAD_TYPE_FILTERS '
                                 'dictionary.'.format(invalid_filters)
        }
        return JsonResponse({'error': error_message}, status=400)


def _get_content_type_filter_for_mongo(requested_filter):
    """
    Construct and return pymongo query dict for the given content type categories.
    """
    requested_file_types = _get_requested_file_types_from_requested_filter(requested_filter)
    type_filter = {
        "$or": []
    }

    if 'OTHER' in requested_file_types:
        type_filter["$or"].append(_get_mongo_expression_for_type_other())
        requested_file_types.remove('OTHER')

    type_filter["$or"].append(_get_mongo_expression_for_type_filter(requested_file_types))

    return type_filter


def _get_mongo_expression_for_type_other():
    """
    Construct and return pymongo expression dict for the 'OTHER' content type category.
    """
    content_types = [ext for extensions in _get_files_and_upload_type_filters().values() for ext in extensions]
    return {
        'contentType': {
            '$nin': content_types
        }
    }


def _get_mongo_expression_for_type_filter(requested_file_types):
    """
    Construct and return pymongo expression dict for the named content type categories.

    The named content categories are the keys of the FILES_AND_UPLOAD_TYPE_FILTERS setting that are not 'OTHER':
    'Images', 'Documents', 'Audio', and 'Code'.
    """
    content_types = []
    files_and_upload_type_filters = _get_files_and_upload_type_filters()

    for requested_file_type in requested_file_types:
        content_types.extend(files_and_upload_type_filters[requested_file_type])

    return {
        'contentType': {
            '$in': content_types
        }
    }


def _get_displayname_filter_for_mongo(displaynames):
    """
    Construct and return pymongo query dict, filtering for the given list of displaynames.
    """
    filters = []

    for displayname in displaynames:
        filters.append({
            'displayname': {
                '$eq': displayname,
            },
        })

    return {
        '$or': filters,
    }


def _get_displayname_search_filter_for_mongo(text_search):
    """
    Return a pymongo query dict for the given search string, using case insensitivity.
    """
    filters = []

    text_search_tokens = text_search.split()

    for token in text_search_tokens:
        escaped_token = re.escape(token)

        filters.append({
            'displayname': {
                '$regex': escaped_token,
                '$options': 'i',
            },
        })

    return {
        '$and': filters,
    }


def _get_files_and_upload_type_filters():
    return settings.FILES_AND_UPLOAD_TYPE_FILTERS


def _get_requested_file_types_from_requested_filter(requested_filter):
    return requested_filter.split(',') if requested_filter else []


def _get_sort_type_and_direction(request_options):
    sort_type = _get_mongo_sort_from_requested_sort(request_options['requested_sort'])
    sort_direction = _get_sort_direction_from_requested_sort(request_options['requested_sort_direction'])
    return [(sort_type, sort_direction)]


def _get_mongo_sort_from_requested_sort(requested_sort):
    """Function returns sorts dataset based on the key provided"""
    if requested_sort == 'date_added':
        sort = 'uploadDate'
    elif requested_sort == 'display_name':
        sort = 'displayname'
    else:
        sort = requested_sort
    return sort


def _get_sort_direction_from_requested_sort(requested_sort_direction):
    if requested_sort_direction.lower() == 'asc':
        return ASCENDING

    return DESCENDING


def _get_current_page(requested_page):
    return max(requested_page, 0)


def _get_first_asset_index(current_page, page_size):
    return current_page * page_size


def _get_assets_for_page(course_key, options):
    """returns course content for given course and options"""
    current_page = options['current_page']
    page_size = options['page_size']
    sort = options['sort']
    filter_params = options['filter_params'] if options['filter_params'] else None
    start = current_page * page_size
    return contentstore().get_all_content_for_course(
        course_key, start=start, maxresults=page_size, sort=sort, filter_params=filter_params
    )


def _update_options_to_requery_final_page(query_options, total_asset_count):
    """sets current_page value based on asset count and page_size"""
    query_options['current_page'] = int(math.floor((total_asset_count - 1) / query_options['page_size']))


def _get_assets_in_json_format(assets, course_key, assets_usage_locations_map):
    """returns assets information in JSON Format"""
    assets_in_json_format = []
    for asset in assets:
        asset_key = asset['asset_key']
        asset_key_string = str(asset_key)
        thumbnail_asset_key = _get_thumbnail_asset_key(asset, course_key)
        asset_is_locked = asset.get('locked', False)
        asset_file_size = asset.get('length', None)

        try:
            usage_locations = assets_usage_locations_map[asset_key_string]
        except KeyError:
            usage_locations = []

        asset_in_json = get_asset_json(
            asset['displayname'],
            asset['contentType'],
            asset['uploadDate'],
            asset_key,
            thumbnail_asset_key,
            asset_is_locked,
            course_key,
            asset_file_size,
            usage_locations,
        )

        assets_in_json_format.append(asset_in_json)

    return assets_in_json_format


def update_course_run_asset(course_key, upload_file):
    """returns contents of the uploaded file"""
    course_exists_response = _get_error_if_course_does_not_exist(course_key)

    if course_exists_response is not None:
        return course_exists_response

    file_metadata = _get_file_metadata_as_dictionary(upload_file)

    is_file_too_large = _check_file_size_is_too_large(file_metadata)
    if is_file_too_large:
        error_message = _get_file_too_large_error_message(file_metadata['filename'])
        raise AssetSizeTooLargeException(error_message)

    content, temporary_file_path = _get_file_content_and_path(file_metadata, course_key)

    (thumbnail_content, thumbnail_location) = contentstore().generate_thumbnail(content,
                                                                                tempfile_path=temporary_file_path)

    # delete cached thumbnail even if one couldn't be created this time (else the old thumbnail will continue to show)
    del_cached_content(thumbnail_location)

    if _check_thumbnail_uploaded(thumbnail_content):
        content.thumbnail_location = thumbnail_location

    contentstore().save(content)
    del_cached_content(content.location)

    return content


@require_POST
@ensure_csrf_cookie
@login_required
def _upload_asset(request, course_key):
    """uploads the file in request and returns JSON response"""
    course_exists_error = _get_error_if_course_does_not_exist(course_key)

    if course_exists_error is not None:
        return course_exists_error

    if course_key.deprecated:
        return JsonResponse({'error': 'Uploading assets for the legacy course is not available.'}, status=400)

    # compute a 'filename' which is similar to the location formatting, we're
    # using the 'filename' nomenclature since we're using a FileSystem paradigm
    # here. We're just imposing the Location string formatting expectations to
    # keep things a bit more consistent
    upload_file = request.FILES['file']

    try:
        content = update_course_run_asset(course_key, upload_file)
    except AssetSizeTooLargeException as exception:
        return JsonResponse({'error': str(exception)}, status=413)

    # readback the saved content - we need the database timestamp
    readback = contentstore().find(content.location)
    locked = getattr(content, 'locked', False)
    length = getattr(content, 'length', None)
    return JsonResponse({
        'asset': get_asset_json(
            content.name,
            content.content_type,
            readback.last_modified_at,
            content.location,
            content.thumbnail_location,
            locked,
            course_key,
            length,
        ),
        'msg': _('Upload completed')
    })


def _get_error_if_course_does_not_exist(course_key):  # lint-amnesty, pylint: disable=missing-function-docstring
    try:
        modulestore().get_course(course_key)
    except ItemNotFoundError:
        logging.error('Could not find course: %s', course_key)
        return HttpResponseBadRequest()


def _get_file_metadata_as_dictionary(upload_file):  # lint-amnesty, pylint: disable=missing-function-docstring
    # compute a 'filename' which is similar to the location formatting; we're
    # using the 'filename' nomenclature since we're using a FileSystem paradigm
    # here; we're just imposing the Location string formatting expectations to
    # keep things a bit more consistent
    return {
        'upload_file': upload_file,
        'filename': upload_file.name,
        'mime_type': upload_file.content_type,
        'upload_file_size': get_file_size(upload_file)
    }


def get_file_size(upload_file):
    """returns the size of the uploaded file"""
    # can be used for mocking test file sizes.
    return upload_file.size


def _check_file_size_is_too_large(file_metadata):
    """verifies whether file size is greater than allowed file size"""
    upload_file_size = file_metadata['upload_file_size']
    maximum_file_size_in_megabytes = settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB
    maximum_file_size_in_bytes = maximum_file_size_in_megabytes * 1000 ** 2

    return upload_file_size > maximum_file_size_in_bytes


def _get_file_too_large_error_message(filename):
    """returns formatted error message for large files"""

    return _(
        'File {filename} exceeds maximum size of '
        '{maximum_size_in_megabytes} MB.'
    ).format(
        filename=filename,
        maximum_size_in_megabytes=settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB,
    )


def _get_file_content_and_path(file_metadata, course_key):
    """returns contents of the uploaded file and path for temporary uploaded file"""
    content_location = StaticContent.compute_location(course_key, file_metadata['filename'])
    upload_file_size = str(file_metadata['upload_file_size'])
    upload_file = file_metadata['upload_file']

    file_can_be_chunked = upload_file.multiple_chunks()

    static_content_partial = partial(StaticContent, content_location, file_metadata['filename'],
                                     file_metadata['mime_type'], length=upload_file_size)

    if file_can_be_chunked:
        content = static_content_partial(upload_file.chunks())
        temporary_file_path = upload_file.temporary_file_path()
    else:
        content = static_content_partial(upload_file.read())
        temporary_file_path = None
    return content, temporary_file_path


def _check_thumbnail_uploaded(thumbnail_content):
    """returns whether thumbnail is None"""
    return thumbnail_content is not None


def _get_thumbnail_asset_key(asset, course_key):
    """returns thumbnail asset key"""
    # note, due to the schema change we may not have a 'thumbnail_location' in the result set
    thumbnail_location = asset.get('thumbnail_location', None)
    thumbnail_asset_key = None

    if thumbnail_location:
        thumbnail_path = thumbnail_location[4]
        thumbnail_asset_key = course_key.make_asset_key('thumbnail', thumbnail_path)
    return thumbnail_asset_key


# TODO: this method needs improvement. These view decorators should be at the top in an actual view method,
#  but this is just a method called by the asset_handler. The asset_handler used by the public CMS API
# just ignores all of this stuff.
@require_http_methods(('DELETE', 'POST', 'PUT'))
@login_required
@ensure_csrf_cookie
def update_asset(request, course_key, asset_key):
    """
    restful CRUD operations for a course asset.
    Currently only DELETE, POST, and PUT methods are implemented.

    asset_path_encoding: the odd /c4x/org/course/category/name repr of the asset (used by Backbone as the id)
    """
    if request.method == 'DELETE':
        try:
            delete_asset(course_key, asset_key)
            return JsonResponse()
        except AssetNotFoundException:
            return JsonResponse(status=404)

    elif request.method in ('PUT', 'POST'):
        if 'file' in request.FILES:
            return _upload_asset(request, course_key)

        # update existing asset
        try:
            modified_asset = json.loads(request.body.decode('utf8'))
        except ValueError:
            return HttpResponseBadRequest()
        contentstore().set_attr(asset_key, 'locked', modified_asset['locked'])
        # delete the asset from the cache so we check the lock status the next time it is requested.
        del_cached_content(asset_key)
        return JsonResponse(modified_asset, status=201)


def _save_content_to_trash(content):
    """saves the content to trash"""
    contentstore('trashcan').save(content)


def delete_asset(course_key, asset_key):
    """deletes the cached content based on asset key"""
    content = _check_existence_and_get_asset_content(asset_key)

    _save_content_to_trash(content)

    _delete_thumbnail(content.thumbnail_location, course_key, asset_key)
    contentstore().delete(content.get_id())
    del_cached_content(content.location)


def _check_existence_and_get_asset_content(asset_key):  # lint-amnesty, pylint: disable=missing-function-docstring
    try:
        content = contentstore().find(asset_key)
        return content
    except NotFoundError:
        raise AssetNotFoundException  # lint-amnesty, pylint: disable=raise-missing-from


def _delete_thumbnail(thumbnail_location, course_key, asset_key):  # lint-amnesty, pylint: disable=missing-function-docstring
    if thumbnail_location is not None:

        # We are ignoring the value of the thumbnail_location-- we only care whether
        # or not a thumbnail has been stored, and we can now easily create the correct path.
        thumbnail_location = course_key.make_asset_key('thumbnail', asset_key.block_id)

        try:
            thumbnail_content = contentstore().find(thumbnail_location)
            _save_content_to_trash(thumbnail_content)
            contentstore().delete(thumbnail_content.get_id())
            del_cached_content(thumbnail_location)
        except Exception:  # pylint: disable=broad-except
            logging.warning('Could not delete thumbnail: %s', thumbnail_location)


def get_asset_json(display_name, content_type, date, location, thumbnail_location,
                   locked, course_key, file_size=None, usage=None):
    '''
    Helper method for formatting the asset information to send to client.
    '''
    asset_url = StaticContent.serialize_asset_key_with_slash(location)

    ## .. filter_implemented_name: LMSPageURLRequested
    ## .. filter_type: org.openedx.content_authoring.lms.page.url.requested.v1
    lms_root, _ = LMSPageURLRequested.run_filter(
        url=configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL),
        org=location.org,
    )

    external_url = urljoin(lms_root, asset_url)
    portable_url = StaticContent.get_static_path_from_location(location)
    usage_locations = [] if usage is None else usage
    return {
        'display_name': display_name,
        'content_type': content_type,
        'date_added': get_default_time_display(date),
        'url': asset_url,
        'external_url': external_url,
        'portable_url': portable_url,
        'thumbnail': StaticContent.serialize_asset_key_with_slash(thumbnail_location) if thumbnail_location else None,
        'locked': locked,
        'static_full_url': StaticContent.get_canonicalized_asset_path(course_key, portable_url, '', []),
        # needed for Backbone delete/update.
        'id': str(location),
        'file_size': file_size,
        'usage_locations': usage_locations,
    }
