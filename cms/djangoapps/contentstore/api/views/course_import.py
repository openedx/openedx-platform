"""
APIs related to Course Import.
"""


import base64
import logging
import os

from django.conf import settings
from django.core.files import File
from edx_django_utils.monitoring import set_custom_attribute, set_custom_attributes_for_course_key
from path import Path as path
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from user_tasks.models import UserTaskStatus

from cms.djangoapps.contentstore.storage import course_import_export_storage
from cms.djangoapps.contentstore.tasks import CourseImportTask, import_olx
from cms.djangoapps.contentstore.utils import IMPORTABLE_FILE_TYPES
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes

from .utils import course_author_access_required

log = logging.getLogger(__name__)


@view_auth_classes()
class CourseImportExportViewMixin(DeveloperErrorViewMixin):
    """
    Mixin class for course import/export related views.
    """
    def perform_authentication(self, request):
        """
        Ensures that the user is authenticated (e.g. not an AnonymousUser)
        """
        super().perform_authentication(request)
        if request.user.is_anonymous:
            raise AuthenticationFailed


class CourseImportView(CourseImportExportViewMixin, GenericAPIView):
    """
    **Use Case**

        * Start an asynchronous task to import a course from a .tar.gz or .zip
        file into the specified course ID, overwriting the existing course
        * Get a status on an asynchronous task import

    **Example Requests**

        POST /api/courses/v0/import/{course_id}/
        GET /api/courses/v0/import/{course_id}/?task_id={task_id}

    **POST Parameters**

        A POST request must include the following parameters.

        * course_id: (required) A string representation of a Course ID,
                                e.g., course-v1:edX+DemoX+Demo_Course
        * course_data: (required) The course .tar.gz or .zip file to import

    **POST Response Values**

        If the import task is started successfully, an HTTP 200 "OK" response is
        returned.

        The HTTP 200 response has the following values.

        * task_id: UUID of the created task, usable for checking status
        * filename: string of the uploaded filename


    **Example POST Response**

        {
            "task_id": "4b357bb3-2a1e-441d-9f6c-2210cf76606f"
        }

    **GET Parameters**

        A GET request must include the following parameters.

        * task_id: (required) The UUID of the task to check, e.g. "4b357bb3-2a1e-441d-9f6c-2210cf76606f"
        * filename: (required) The filename of the uploaded course .tar.gz or .zip

    **GET Response Values**

        If the import task is found successfully by the UUID provided, an HTTP
        200 "OK" response is returned.

        The HTTP 200 response has the following values.

        * state: String description of the state of the task


    **Example GET Response**

        {
            "state": "Succeeded"
        }

    """
    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    swagger_schema = None

    @course_author_access_required
    def post(self, request, course_key):
        """
        Kicks off an asynchronous course import and returns an ID to be used to check
        the task's status
        """
        set_custom_attribute('course_import_init', True)
        set_custom_attributes_for_course_key(course_key)
        try:
            if 'course_data' not in request.FILES:
                raise self.api_error(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    developer_message='Missing required parameter',
                    error_code='internal_error',
                )

            filename = request.FILES['course_data'].name
            if not filename.endswith(IMPORTABLE_FILE_TYPES):
                raise self.api_error(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    developer_message='Parameter in the wrong format',
                    error_code='internal_error',
                )
            course_dir = path(settings.GITHUB_REPO_ROOT) / base64.urlsafe_b64encode(
                repr(course_key).encode('utf-8')
            ).decode('utf-8')
            temp_filepath = course_dir / filename
            if not course_dir.isdir():
                os.mkdir(course_dir)

            log.debug(f'importing course to {temp_filepath}')
            with open(temp_filepath, "wb+") as temp_file:
                for chunk in request.FILES['course_data'].chunks():
                    temp_file.write(chunk)

            log.info("Course import %s: Upload complete", course_key)
            with open(temp_filepath, 'rb') as local_file:
                django_file = File(local_file)
                storage_path = course_import_export_storage.save('olx_import/' + filename, django_file)

            async_result = import_olx.delay(
                request.user.id, str(course_key), storage_path, filename, request.LANGUAGE_CODE)
            return Response({
                'task_id': async_result.task_id
            })
        except Exception as e:
            log.exception(f'Course import {course_key}: Unknown error in import')
            raise self.api_error(  # noqa: B904
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                developer_message=str(e),
                error_code='internal_error'
            )

    @course_author_access_required
    def get(self, request, course_key):
        """
        Check the status of the specified task
        """
        try:
            task_id = request.GET['task_id']
            filename = request.GET['filename']
            args = {'course_key_string': str(course_key), 'archive_name': filename}
            name = CourseImportTask.generate_name(args)
            task_status = UserTaskStatus.objects.filter(name=name, task_id=task_id).first()
            return Response({
                'state': task_status.state
            })
        except Exception as e:
            log.exception(str(e))
            raise self.api_error(  # noqa: B904
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                developer_message=str(e),
                error_code='internal_error'
            )
