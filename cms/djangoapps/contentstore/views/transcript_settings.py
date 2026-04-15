"""
Views related to the transcript preferences feature
"""


import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotFound
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from opaque_keys.edx.keys import CourseKey
from rest_framework.decorators import api_view

from cms.djangoapps.contentstore.transcript_storage_handlers import (
    delete_video_transcript,
    handle_transcript_credentials,
    handle_transcript_download,
    upload_transcript,
    validate_transcript_upload_data,
)
from common.djangoapps.student.auth import has_studio_write_access
from common.djangoapps.util.json_request import JsonResponse, expect_json
from openedx.core.lib.api.view_utils import view_auth_classes

__all__ = [
    'transcript_credentials_handler',
    'transcript_download_handler',
    'transcript_upload_handler',
    'transcript_delete_handler',
    'transcript_upload_api',
]

LOGGER = logging.getLogger(__name__)


@expect_json
@login_required
@require_POST
def transcript_credentials_handler(request, course_key_string):
    """
    JSON view handler to update the transcript organization credentials.

    Arguments:
        request: WSGI request object
        course_key_string: A course identifier to extract the org.

    Returns:
        - A 200 response if credentials are valid and successfully updated in edx-video-pipeline.
        - A 404 response if transcript feature is not enabled for this course.
        - A 400 if credentials do not pass validations, hence not updated in edx-video-pipeline.
    """
    return handle_transcript_credentials(request, course_key_string)


@login_required
@require_GET
def transcript_download_handler(request):
    """
    JSON view handler to download a transcript.

    Arguments:
        request: WSGI request object

    Returns:
        - A 200 response with SRT transcript file attached.
        - A 400 if there is a validation error.
        - A 404 if there is no such transcript.
    """
    return handle_transcript_download(request)


# New version of this transcript upload API in contentstore/rest_api/transcripts.py
# Keeping the old API for backward compatibility
@api_view(['POST'])
@view_auth_classes()
@expect_json
def transcript_upload_api(request):
    """
    (Old) API View for uploading transcript files.

    Arguments:
        request: A WSGI request object

        Transcript file in SRT format

        Returns:
            - A 400 if any validation fails
            - A 200 if the transcript has been uploaded successfully
    """
    error = validate_transcript_upload_data(data=request.POST, files=request.FILES)
    if error:
        response = JsonResponse({'error': error}, status=400)
    else:
        response = upload_transcript(request)
    return response


@login_required
@require_POST
def transcript_upload_handler(request):
    """
    View to upload a transcript file.

    Arguments:
        request: A WSGI request object

    Transcript file, edx video id and transcript language are required.
    Transcript file should be in SRT(SubRip) format.

    Returns
        - A 400 if any of the validation fails
        - A 200 if transcript has been uploaded successfully
    """
    error = validate_transcript_upload_data(data=request.POST, files=request.FILES)
    if error:
        response = JsonResponse({'error': error}, status=400)
    else:
        response = upload_transcript(request)
    return response


@login_required
@require_http_methods(["DELETE"])
def transcript_delete_handler(request, course_key_string, edx_video_id, language_code):
    """
    View to delete a transcript file.

    Arguments:
        request: A WSGI request object
        course_key_string: Course key identifying a course.
        edx_video_id: edX video identifier whose transcript need to be deleted.
        language_code: transcript's language code.

    Returns
        - A 404 if the user does not have required permisions
        - A 200 if transcript is deleted without any error(s)
    """
    # Check whether the feature is available for this course.
    course_key = CourseKey.from_string(course_key_string)
    # User needs to have studio write access for this course.
    if not has_studio_write_access(request.user, course_key):
        return HttpResponseNotFound()

    delete_video_transcript(video_id=edx_video_id, language_code=language_code)

    return JsonResponse(status=200)
