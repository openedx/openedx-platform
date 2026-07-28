"""
Views related to the video upload feature
"""


import logging

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from edx_toggles.toggles import WaffleSwitch
from rest_framework.decorators import api_view

from cms.djangoapps.contentstore.video_storage_handlers import (
    check_video_images_upload_enabled,
    enabled_video_features,
    get_course_youtube_edx_video_ids,
    get_video_encodings_download,
    handle_generate_video_upload_link,
    handle_transcript_preferences,
    handle_video_images,
    handle_videos,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    convert_video_status as convert_video_status_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    get_all_transcript_languages as get_all_transcript_languages_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    is_status_update_request as is_status_update_request_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    send_video_status_update as send_video_status_update_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    storage_service_bucket as storage_service_bucket_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    storage_service_key as storage_service_key_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import (
    validate_transcript_preferences as validate_transcript_preferences_source_function,
)
from cms.djangoapps.contentstore.video_storage_handlers import videos_index_html as videos_index_html_source_function
from cms.djangoapps.contentstore.video_storage_handlers import videos_index_json as videos_index_json_source_function
from cms.djangoapps.contentstore.video_storage_handlers import videos_post as videos_post_source_function
from common.djangoapps.util.json_request import expect_json
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from openedx.core.lib.api.view_utils import view_auth_classes

__all__ = [
    'videos_handler',
    'video_encodings_download',
    'video_images_handler',
    'video_images_upload_enabled',
    'get_video_features',
    'transcript_preferences_handler',
    'generate_video_upload_link_handler',
    'get_course_youtube_edx_videos_ids',
]

LOGGER = logging.getLogger(__name__)

# Waffle switches namespace for videos
WAFFLE_NAMESPACE = 'videos'

# Waffle switch for enabling/disabling video image upload feature
VIDEO_IMAGE_UPLOAD_ENABLED = WaffleSwitch(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.video_image_upload_enabled', __name__
)

# Waffle flag namespace for studio
WAFFLE_STUDIO_FLAG_NAMESPACE = 'studio'

ENABLE_VIDEO_UPLOAD_PAGINATION = CourseWaffleFlag(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_STUDIO_FLAG_NAMESPACE}.enable_video_upload_pagination', __name__
)
# Default expiration, in seconds, of one-time URLs used for uploading videos.
KEY_EXPIRATION_IN_SECONDS = 86400

VIDEO_SUPPORTED_FILE_FORMATS = {
    '.mp4': 'video/mp4',
    '.mov': 'video/quicktime',
}

VIDEO_UPLOAD_MAX_FILE_SIZE_GB = 5

# maximum time for video to remain in upload state
MAX_UPLOAD_HOURS = 24

VIDEOS_PER_PAGE = 100


@expect_json
@login_required
@require_http_methods(("GET", "POST", "DELETE"))
def videos_handler(request, course_key_string, edx_video_id=None):
    """
    The restful handler for video uploads.

    GET
        html: return an HTML page to display previous video uploads and allow
            new ones
        json: return json representing the videos that have been uploaded and
            their statuses
    POST
        json: create a new video upload; the actual files should not be provided
            to this endpoint but rather PUT to the respective upload_url values
            contained in the response. Example payload:
                {
                    "files": [{
                        "file_name": "video.mp4",
                        "content_type": "video/mp4"
                    }]
                }
    DELETE
        soft deletes a video for particular course
    """
    return handle_videos(request, course_key_string, edx_video_id)


@api_view(['POST'])
@view_auth_classes()
@expect_json
def generate_video_upload_link_handler(request, course_key_string):
    """
    API for creating a video upload.  Returns an edx_video_id and a presigned URL that can be used
    to upload the video to AWS S3.
    """
    return handle_generate_video_upload_link(request, course_key_string)


@expect_json
@login_required
@require_POST
def video_images_handler(request, course_key_string, edx_video_id=None):
    """Function to handle image files"""
    return handle_video_images(request, course_key_string, edx_video_id)


@login_required
@require_GET
def video_images_upload_enabled(request):
    """Function to check if images can be uploaded"""
    return check_video_images_upload_enabled(request)


@login_required
@require_GET
def get_video_features(request):
    """ Return a dict with info about which video features are enabled """
    return enabled_video_features(request)


def validate_transcript_preferences(provider, cielo24_fidelity, cielo24_turnaround,
                                    three_play_turnaround, video_source_language, preferred_languages):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return validate_transcript_preferences_source_function(provider, cielo24_fidelity, cielo24_turnaround,
                                                           three_play_turnaround, video_source_language,
                                                           preferred_languages)


@expect_json
@login_required
@require_http_methods(('POST', 'DELETE'))
def transcript_preferences_handler(request, course_key_string):
    """
    JSON view handler to post the transcript preferences.

    Arguments:
        request: WSGI request object
        course_key_string: string for course key

    Returns: valid json response or 400 with error message
    """
    return handle_transcript_preferences(request, course_key_string)


@login_required
@require_GET
def video_encodings_download(request, course_key_string):
    """
    Returns a CSV report containing the encoded video URLs for video uploads
    in the following format:

    Video ID,Name,Status,Profile1 URL,Profile2 URL
    aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa,video.mp4,Complete,http://example.com/prof1.mp4,http://example.com/prof2.mp4
    """
    return get_video_encodings_download(request, course_key_string)


def convert_video_status(video, is_video_encodes_ready=False):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return convert_video_status_source_function(video, is_video_encodes_ready)


def get_all_transcript_languages():
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return get_all_transcript_languages_source_function()


def videos_index_html(course, pagination_conf=None):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return videos_index_html_source_function(course, pagination_conf)


def videos_index_json(course):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return videos_index_json_source_function(course)


def videos_post(course, request):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return videos_post_source_function(course, request)


def storage_service_bucket():
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return storage_service_bucket_source_function()


def storage_service_key(bucket, file_name):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return storage_service_key_source_function(bucket, file_name)


def send_video_status_update(updates):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return send_video_status_update_source_function(updates)


def is_status_update_request(request_data):
    """
    Exposes helper method without breaking existing bindings/dependencies
    """
    return is_status_update_request_source_function(request_data)


@api_view(['GET'])
@view_auth_classes()
@require_GET
def get_course_youtube_edx_videos_ids(request, course_key_string):
    """
    Get an object containing course videos.
    **Example Request**
        GET /api/contentstore/v1/videos/youtube_ids{course_id}
    **Response Values**
    If the request is successful, an HTTP 200 "OK" response is returned.
    The HTTP 200 response contains a list of youtube edx_video_ids for a given course.
    """
    return get_course_youtube_edx_video_ids(course_key_string)
