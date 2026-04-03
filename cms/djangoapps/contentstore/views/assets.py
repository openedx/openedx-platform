"""Views for assets"""


from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie

from cms.djangoapps.contentstore.asset_storage_handlers import delete_asset as delete_asset_source_function
from cms.djangoapps.contentstore.asset_storage_handlers import get_asset_json as get_asset_json_source_function
from cms.djangoapps.contentstore.asset_storage_handlers import get_asset_usage_path_json, handle_assets
from cms.djangoapps.contentstore.asset_storage_handlers import get_file_size as get_file_size_source_function
from cms.djangoapps.contentstore.asset_storage_handlers import update_asset as update_asset_source_function
from cms.djangoapps.contentstore.asset_storage_handlers import (
    update_course_run_asset as update_course_run_asset_source_function,
)

__all__ = ['assets_handler', 'asset_usage_path_handler']

REQUEST_DEFAULTS = {
    'page': 0,
    'page_size': 50,
    'sort': 'date_added',
    'direction': '',
    'asset_type': '',
    'text_search': '',
}


@login_required
@ensure_csrf_cookie
def assets_handler(request, course_key_string=None, asset_key_string=None):
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
            text_search: string to filter results by file name (defaults to '')
    POST
        json: create or update an asset. The only updating that can be done is changing the lock state.
    PUT
        json: create or update an asset. The only updating that can be done is changing the lock state.
    DELETE
        json: delete an asset
    '''
    return handle_assets(request, course_key_string, asset_key_string)


@login_required
@ensure_csrf_cookie
def asset_usage_path_handler(request, course_key_string, asset_key_string):
    return get_asset_usage_path_json(request, course_key_string, asset_key_string)


def update_course_run_asset(course_key, upload_file):
    """Exposes service method in asset_storage_handlers without breaking existing bindings/dependencies"""
    return update_course_run_asset_source_function(course_key, upload_file)


def get_file_size(upload_file):
    """Exposes service method in asset_storage_handlers without breaking existing bindings/dependencies"""
    return get_file_size_source_function(upload_file)


def delete_asset(course_key, asset_key):
    """Exposes service method in asset_storage_handlers without breaking existing bindings/dependencies"""
    return delete_asset_source_function(course_key, asset_key)


def _get_asset_json(display_name, content_type, date, location, thumbnail_location, locked, course_key):
    return get_asset_json_source_function(
        display_name,
        content_type,
        date,
        location,
        thumbnail_location,
        locked,
        course_key,
    )


def _update_asset(request, course_key, asset_key):
    return update_asset_source_function(request, course_key, asset_key)
