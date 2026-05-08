"""
These views handle all actions in Studio related to import and exporting of
courses
"""


import base64
import json
import logging
import os
import re
import shutil
from wsgiref.util import FileWrapper

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseNotFound, StreamingHttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods
from edx_django_utils.monitoring import set_custom_attribute, set_custom_attributes_for_course_key
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator
from openedx_authz.constants.permissions import COURSES_EXPORT_COURSE, COURSES_IMPORT_COURSE
from path import Path as path
from storages.backends.s3boto3 import S3Boto3Storage
from user_tasks.conf import settings as user_tasks_settings
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.util.json_request import JsonResponse
from common.djangoapps.util.monitoring import monitor_import_failure
from common.djangoapps.util.views import ensure_valid_course_key
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from ..storage import course_import_export_storage
from ..tasks import CourseExportTask, CourseImportTask, export_olx, import_olx
from ..toggles import use_new_export_page, use_new_import_page
from ..utils import IMPORTABLE_FILE_TYPES, get_export_url, get_import_url, reverse_course_url, reverse_library_url

__all__ = [
    'import_handler', 'import_status_handler',
    'export_handler', 'export_output_handler', 'export_status_handler',
]

log = logging.getLogger(__name__)

# Regex to capture Content-Range header ranges.
CONTENT_RE = re.compile(r"(?P<start>\d{1,11})-(?P<stop>\d{1,11})/(?P<end>\d{1,11})")

STATUS_FILTERS = user_tasks_settings.USER_TASKS_STATUS_FILTERS


@transaction.non_atomic_requests
@login_required
@ensure_csrf_cookie
@require_http_methods(("GET", "POST", "PUT"))
@ensure_valid_course_key
def import_handler(request, course_key_string):
    """
    The restful handler for importing a course.

    GET
        html: return html page for import page
        json: not supported
    POST or PUT
        json: import a course via the .tar.gz or .zip file specified in request.FILES
    """
    courselike_key = CourseKey.from_string(course_key_string)
    library = isinstance(courselike_key, LibraryLocator)
    if library:
        successful_url = reverse_library_url('library_handler', courselike_key)
        context_name = 'context_library'
        courselike_block = modulestore().get_library(courselike_key)
    else:
        successful_url = reverse_course_url('course_handler', courselike_key)
        context_name = 'context_course'
        courselike_block = modulestore().get_course(courselike_key)
    if not user_has_course_permission(
        user=request.user,
        authz_permission=COURSES_IMPORT_COURSE.identifier,
        course_key=courselike_key,
        legacy_permission=LegacyAuthoringPermission.WRITE
    ):
        raise PermissionDenied()

    if 'application/json' in request.META.get('HTTP_ACCEPT', 'application/json'):
        if request.method == 'GET':  # pylint: disable=no-else-raise
            raise NotImplementedError('coming soon')
        else:
            return _write_chunk(request, courselike_key)
    elif request.method == 'GET':  # assume html

        if use_new_import_page(courselike_key) and not library:
            return redirect(get_import_url(courselike_key))
        status_url = reverse_course_url(
            "import_status_handler", courselike_key, kwargs={'filename': "fillerName"}
        )
        return render_to_response('import.html', {
            context_name: courselike_block,
            'successful_import_redirect_url': successful_url,
            'import_status_url': status_url,
            'library': isinstance(courselike_key, LibraryLocator)
        })
    else:
        return HttpResponseNotFound()


def _save_request_status(request, key, status):
    """
    Save import status for a course in request session
    """
    session_status = request.session.get('import_status')
    if session_status is None:
        session_status = request.session.setdefault("import_status", {})

    session_status[key] = status
    request.session.save()


def _write_chunk(request, courselike_key):  # pylint: disable=too-many-statements
    """
    Write the OLX file data chunk from the given request to the local filesystem.
    """
    # Upload .tar.gz or .zip to local filesystem for one-server installations not using S3 or Swift
    data_root = path(settings.GITHUB_REPO_ROOT)
    subdir = base64.urlsafe_b64encode(repr(courselike_key).encode('utf-8')).decode('utf-8')
    course_dir = data_root / subdir
    filename = request.FILES['course-data'].name
    set_custom_attributes_for_course_key(courselike_key)
    current_step = 'Uploading'

    def error_response(message, status, stage):
        """Returns Json error response"""
        return JsonResponse({'ErrMsg': message, 'Stage': stage}, status=status)

    courselike_string = str(courselike_key) + filename
    # Do everything in a try-except block to make sure everything is properly cleaned up.
    try:
        # Use sessions to keep info about import progress
        _save_request_status(request, courselike_string, 0)

        if not filename.endswith(IMPORTABLE_FILE_TYPES):
            error_message = _('We support uploading files in one of the following formats: {IMPORTABLE_FILE_TYPES}')
            _save_request_status(request, courselike_string, -1)
            monitor_import_failure(courselike_key, current_step, message=error_message)
            return error_response(error_message, 415, 0)

        temp_filepath = course_dir / filename
        if not course_dir.isdir():
            os.mkdir(course_dir)

        logging.info(f'Course import {courselike_key}: importing course to {temp_filepath}')

        # Get upload chunks byte ranges
        try:
            matches = CONTENT_RE.search(request.META["HTTP_CONTENT_RANGE"])
            content_range = matches.groupdict()
        except KeyError:  # Single chunk
            # no Content-Range header, so make one that will work
            logging.info(f'Course import {courselike_key}: single chunk found')
            content_range = {'start': 0, 'stop': 1, 'end': 2}

        # stream out the uploaded files in chunks to disk
        is_initial_import_request = int(content_range['start']) == 0
        if is_initial_import_request:
            mode = "wb+"
            set_custom_attribute('course_import_init', True)
        else:
            mode = "ab+"
            # Appending to fail would fail if the file doesn't exist.
            if not temp_filepath.exists():
                error_message = _('Some chunks missed during file upload. Please try again')
                _save_request_status(request, courselike_string, -1)
                log.error(f'Course Import {courselike_key}: {error_message}')
                monitor_import_failure(courselike_key, current_step, message=error_message)
                return error_response(error_message, 409, 0)

            size = os.path.getsize(temp_filepath)
            # Check to make sure we haven't missed a chunk
            # This shouldn't happen, even if different instances are handling
            # the same session, but it's always better to catch errors earlier.
            if size < int(content_range['start']):
                error_message = _('File upload failed. Please try again')
                _save_request_status(request, courselike_string, -1)
                log.error(f'Course import {courselike_key}: A chunk has been missed')
                monitor_import_failure(courselike_key, current_step, message=error_message)
                return error_response(error_message, 409, 0)

            # The last request sometimes comes twice. This happens because
            # nginx sends a 499 error code when the response takes too long.
            elif size > int(content_range['stop']) and size == int(content_range['end']):
                return JsonResponse({'ImportStatus': 1})

        with open(temp_filepath, mode) as temp_file:
            for chunk in request.FILES['course-data'].chunks():
                temp_file.write(chunk)

        size = os.path.getsize(temp_filepath)

        if int(content_range['stop']) != int(content_range['end']) - 1:
            # More chunks coming
            return JsonResponse({
                "files": [{
                    "name": filename,
                    "size": size,
                    "deleteUrl": "",
                    "deleteType": "",
                    "url": reverse_course_url('import_handler', courselike_key),
                    "thumbnailUrl": ""
                }]
            })

        log.info(f'Course import {courselike_key}: Upload complete')
        with open(temp_filepath, 'rb') as local_file:
            django_file = File(local_file)
            storage_path = course_import_export_storage.save('olx_import/' + filename, django_file)
        import_olx.delay(
            request.user.id, str(courselike_key), storage_path, filename, request.LANGUAGE_CODE)

    # Send errors to client with stage at which error occurred.
    except Exception as exception:  # pylint: disable=broad-except
        _save_request_status(request, courselike_string, -1)
        if course_dir.isdir():
            shutil.rmtree(course_dir)
            log.info("Course import %s: Temp data cleared", courselike_key)

        monitor_import_failure(courselike_key, current_step, exception=exception)
        log.exception(f'Course import {courselike_key}: error importing course.')
        return error_response(str(exception), 400, -1)

    return JsonResponse({'ImportStatus': 1})


@transaction.non_atomic_requests
@require_GET
@ensure_csrf_cookie
@login_required
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@ensure_valid_course_key
def import_status_handler(request, course_key_string, filename=None):
    """
    Returns an integer corresponding to the status of a file import. These are:

        -X : Import unsuccessful due to some error with X as stage [0-3]
        0 : No status info found (import done or upload still in progress)
        1 : Unpacking
        2 : Verifying
        3 : Updating
        4 : Import successful

    """
    course_key = CourseKey.from_string(course_key_string)
    if not user_has_course_permission(
        user=request.user,
        authz_permission=COURSES_IMPORT_COURSE.identifier,
        course_key=course_key,
        legacy_permission=LegacyAuthoringPermission.WRITE
    ):
        raise PermissionDenied()

    # The task status record is authoritative once it's been created
    args = {'course_key_string': course_key_string, 'archive_name': filename}
    name = CourseImportTask.generate_name(args)
    task_status = UserTaskStatus.objects.filter(name=name)
    message = ''
    for status_filter in STATUS_FILTERS:
        task_status = status_filter().filter_queryset(request, task_status, import_status_handler)
    task_status = task_status.order_by('-created').first()
    if task_status is None:
        # The task hasn't been initialized yet; did we store info in the session already?
        try:
            session_status = request.session["import_status"]
            status = session_status[course_key_string + filename]
        except KeyError:
            status = 0
    elif task_status.state == UserTaskStatus.SUCCEEDED:
        status = 4
    elif task_status.state in (UserTaskStatus.FAILED, UserTaskStatus.CANCELED):
        status = max(-(task_status.completed_steps + 1), -3)
        artifact = UserTaskArtifact.objects.filter(name='Error', status=task_status).order_by('-created').first()
        if artifact:
            message = artifact.text
    else:
        status = min(task_status.completed_steps + 1, 3)

    return JsonResponse({"ImportStatus": status, "Message": message})


def send_tarball(tarball, size):
    """
    Renders a tarball to response, for use when sending a tar.gz file to the user.
    """
    wrapper = FileWrapper(tarball, settings.COURSE_EXPORT_DOWNLOAD_CHUNK_SIZE)
    response = StreamingHttpResponse(wrapper, content_type='application/x-tgz')
    response['Content-Disposition'] = 'attachment; filename=%s' % os.path.basename(tarball.name)  # noqa: UP031
    response['Content-Length'] = size
    return response


@transaction.non_atomic_requests
@ensure_csrf_cookie
@login_required
@require_http_methods(('GET', 'POST'))
@ensure_valid_course_key
def export_handler(request, course_key_string):
    """
    The restful handler for exporting a course.

    GET
        html: return html page for import page
        json: not supported
    POST
        Start a Celery task to export the course

    The Studio UI uses a POST request to start the export asynchronously, with
    a link appearing on the page once it's ready.
    """
    course_key = CourseKey.from_string(course_key_string)
    if not user_has_course_permission(
        user=request.user,
        authz_permission=COURSES_EXPORT_COURSE.identifier,
        course_key=course_key,
        legacy_permission=LegacyAuthoringPermission.WRITE
    ):
        raise PermissionDenied()
    library = isinstance(course_key, LibraryLocator)
    if library:
        courselike_block = modulestore().get_library(course_key)
        context = {
            'context_library': courselike_block,
            'courselike_home_url': reverse_library_url("library_handler", course_key),
            'library': True
        }
    else:
        courselike_block = modulestore().get_course(course_key)
        if courselike_block is None:
            raise Http404
        context = {
            'context_course': courselike_block,
            'courselike_home_url': reverse_course_url("course_handler", course_key),
            'library': False
        }
    context['status_url'] = reverse_course_url('export_status_handler', course_key)

    # an _accept URL parameter will be preferred over HTTP_ACCEPT in the header.
    requested_format = request.GET.get('_accept', request.META.get('HTTP_ACCEPT', 'text/html'))

    if request.method == 'POST':
        export_olx.delay(request.user.id, course_key_string, request.LANGUAGE_CODE)
        return JsonResponse({'ExportStatus': 1})
    elif 'text/html' in requested_format:
        if use_new_export_page(course_key) and not library:
            return redirect(get_export_url(course_key))
        return render_to_response('export.html', context)
    else:
        # Only HTML request format is supported (no JSON).
        return HttpResponse(status=406)


@transaction.non_atomic_requests
@require_GET
@ensure_csrf_cookie
@login_required
@ensure_valid_course_key
def export_status_handler(request, course_key_string):
    """
    Returns an integer corresponding to the status of a file export. These are:

        -X : Export unsuccessful due to some error with X as stage [0-3]
        0 : No status info found (export done or task not yet created)
        1 : Exporting
        2 : Compressing
        3 : Export successful

    If the export was successful, a URL for the generated .tar.gz file is also
    returned.
    """
    course_key = CourseKey.from_string(course_key_string)
    if not user_has_course_permission(
        user=request.user,
        authz_permission=COURSES_EXPORT_COURSE.identifier,
        course_key=course_key,
        legacy_permission=LegacyAuthoringPermission.WRITE
    ):
        raise PermissionDenied()

    # The task status record is authoritative once it's been created
    task_status = _latest_task_status(request, course_key_string, export_status_handler)
    output_url = None
    error = None
    if task_status is None:
        # The task hasn't been initialized yet; did we store info in the session already?
        try:
            session_status = request.session["export_status"]
            status = session_status[course_key_string]
        except KeyError:
            status = 0
    elif task_status.state == UserTaskStatus.SUCCEEDED:
        status = 3
        artifact = UserTaskArtifact.objects.get(status=task_status, name='Output')
        if isinstance(artifact.file.storage, FileSystemStorage):
            output_url = reverse_course_url('export_output_handler', course_key)
        elif isinstance(artifact.file.storage, S3Boto3Storage):
            filename = os.path.basename(artifact.file.name)
            disposition = f'attachment; filename="{filename}"'
            output_url = artifact.file.storage.url(artifact.file.name, parameters={
                'ResponseContentDisposition': disposition,
                'ResponseContentEncoding': 'application/octet-stream',
                'ResponseContentType': 'application/x-tgz'
            })
        else:
            output_url = artifact.file.storage.url(artifact.file.name)
    elif task_status.state in (UserTaskStatus.FAILED, UserTaskStatus.CANCELED):
        status = max(-(task_status.completed_steps + 1), -2)
        errors = UserTaskArtifact.objects.filter(status=task_status, name='Error')
        if errors:
            error = errors[0].text
            try:
                error = json.loads(error)
            except ValueError:
                # Wasn't JSON, just use the value as a string
                pass
    else:
        status = min(task_status.completed_steps + 1, 2)

    response = {"ExportStatus": status}
    if output_url:
        response['ExportOutput'] = output_url
    elif error:
        response['ExportError'] = error
    return JsonResponse(response)


@transaction.non_atomic_requests
@require_GET
@ensure_csrf_cookie
@login_required
@ensure_valid_course_key
def export_output_handler(request, course_key_string):
    """
    Returns the OLX .tar.gz produced by a file export.  Only used in
    environments such as devstack where the output is stored in a local
    filesystem instead of an external service like S3.
    """
    course_key = CourseKey.from_string(course_key_string)
    if not user_has_course_permission(
        user=request.user,
        authz_permission=COURSES_EXPORT_COURSE.identifier,
        course_key=course_key,
        legacy_permission=LegacyAuthoringPermission.WRITE
    ):
        raise PermissionDenied()

    task_status = _latest_task_status(request, course_key_string, export_output_handler)
    if task_status and task_status.state == UserTaskStatus.SUCCEEDED:
        artifact = None
        try:
            artifact = UserTaskArtifact.objects.get(status=task_status, name='Output')
            tarball = course_import_export_storage.open(artifact.file.name)
            return send_tarball(tarball, artifact.file.storage.size(artifact.file.name))
        except UserTaskArtifact.DoesNotExist:
            raise Http404  # pylint: disable=raise-missing-from  # noqa: B904
        finally:
            if artifact:
                artifact.file.close()
    else:
        raise Http404


def _latest_task_status(request, course_key_string, view_func=None):
    """
    Get the most recent export status update for the specified course/library
    key.
    """
    args = {'course_key_string': course_key_string}
    name = CourseExportTask.generate_name(args)
    task_status = UserTaskStatus.objects.filter(name=name)
    for status_filter in STATUS_FILTERS:
        task_status = status_filter().filter_queryset(request, task_status, view_func)
    return task_status.order_by('-created').first()
