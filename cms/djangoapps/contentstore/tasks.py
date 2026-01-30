"""
This file contains celery tasks for contentstore views
"""

import asyncio
import base64
import json
import os
import re
import shutil
import tarfile
from datetime import datetime, timezone
from importlib.metadata import entry_points
from tempfile import NamedTemporaryFile, mkdtemp
from urllib.parse import urlparse

import aiohttp
import olxcleaner
from ccx_keys.locator import CCXLocator
from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from django.core.files import File
from django.test import RequestFactory
from django.utils.text import get_valid_filename
from edx_django_utils.monitoring import (
    set_code_owner_attribute,
    set_code_owner_attribute_from_module,
    set_custom_attribute,
    set_custom_attributes_for_course_key
)
from olxcleaner.exceptions import ErrorLevel
from olxcleaner.reporting import report_error_summary, report_errors
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocator
from openedx_events.content_authoring.data import CourseData
from openedx_events.content_authoring.signals import COURSE_RERUN_COMPLETED
from organizations.api import add_organization_course, ensure_organization
from organizations.exceptions import InvalidOrganizationException
from organizations.models import Organization
from path import Path as path
from pytz import UTC
from user_tasks.models import UserTaskArtifact, UserTaskStatus
from user_tasks.tasks import UserTask

import cms.djangoapps.contentstore.errors as UserErrors
from cms.djangoapps.contentstore.courseware_index import (
    CoursewareSearchIndexer,
    LibrarySearchIndexer,
    SearchIndexingError
)
from cms.djangoapps.contentstore.storage import course_import_export_storage
from cms.djangoapps.contentstore.toggles import enable_course_optimizer_check_prev_run_links
from cms.djangoapps.contentstore.utils import (
    IMPORTABLE_FILE_TYPES,
    contains_course_reference,
    create_course_info_usage_key,
    create_or_update_xblock_upstream_link,
    delete_course,
    get_previous_run_course_key,
    initialize_permissions,
    reverse_usage_url,
    translation_language
)
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import get_block_info
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.static_replace import replace_static_urls
from common.djangoapps.student.auth import has_course_author_access
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, LibraryUserRole
from common.djangoapps.util.monitoring import monitor_import_failure
from openedx.core.djangoapps.content.learning_sequences.api import key_supports_outlines
from openedx.core.djangoapps.content_libraries import api as v2contentlib_api
from openedx.core.djangoapps.content_tagging.api import make_copied_tags_editable
from openedx.core.djangoapps.course_apps.toggles import exams_ida_enabled
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider
from openedx.core.djangoapps.discussions.tasks import update_unit_discussion_state_from_discussion_blocks
from openedx.core.djangoapps.embargo.models import CountryAccessRule, RestrictedCourse
from openedx.core.lib import ensure_cms
from openedx.core.lib.extract_archive import safe_extractall
from openedx.core.lib.xblock_utils import get_course_update_items
from xmodule.contentstore.django import contentstore
from xmodule.course_block import CourseFields
from xmodule.exceptions import SerializationError
from xmodule.modulestore import COURSE_ROOT, LIBRARY_ROOT, ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import DuplicateCourseError, InvalidProctoringProvider, ItemNotFoundError
from xmodule.modulestore.xml_exporter import export_course_to_xml, export_library_to_xml
from xmodule.modulestore.xml_importer import CourseImportException, import_course_from_xml, import_library_from_xml
from xmodule.tabs import StaticTab
from xmodule.util.keys import BlockKey

from .models import ComponentLink, ContainerLink, LearningContextLinksStatus, LearningContextLinksStatusChoices
from .outlines import update_outline_from_modulestore
from .outlines_regenerate import CourseOutlineRegenerate
from .toggles import bypass_olx_failure_enabled
from .utils import course_import_olx_validation_is_enabled
from .api import get_ready_to_migrate_legacy_library_content_blocks

User = get_user_model()

LOGGER = get_task_logger(__name__)
FILE_READ_CHUNK = 1024  # bytes
FULL_COURSE_REINDEX_THRESHOLD = 1
ALL_ALLOWED_XBLOCKS = frozenset(
    [entry_point.name for entry_point in entry_points(group="xblock.v1")]
)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/115.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

# DOI-specific headers
DOI_HEADERS = {
    "User-Agent": DEFAULT_HEADERS["User-Agent"],
    "Accept": "application/vnd.citationstyles.csl+json",
    "Connection": "keep-alive",
}

# Domain-specific header mapping
DOMAIN_HEADERS = {
    "doi.org": DOI_HEADERS,
}


class LinkState:
    """
    Links State Enumeration
    """
    BROKEN = 'broken'
    LOCKED = 'locked'
    EXTERNAL_FORBIDDEN = 'external-forbidden'
    PREVIOUS_RUN = 'previous-run'


def clone_instance(instance, field_values):
    """ Clones a Django model instance.

    The specified fields are replaced with new values.

    Arguments:
        instance (Model): Instance of a Django model.
        field_values (dict): Map of field names to new values.

    Returns:
        Model: New instance.
    """
    instance.pk = None

    for field, value in field_values.items():
        setattr(instance, field, value)

    instance.save()

    return instance


@shared_task
@set_code_owner_attribute
def rerun_course(source_course_key_string, destination_course_key_string, user_id, fields=None):
    """
    Reruns a course in a new celery task.
    """
    # import here, at top level this import prevents the celery workers from starting up correctly
    from edxval.api import copy_course_videos

    source_course_key = CourseKey.from_string(source_course_key_string)
    destination_course_key = CourseKey.from_string(destination_course_key_string)
    try:
        # deserialize the payload
        fields = deserialize_fields(fields) if fields else None

        # use the split modulestore as the store for the rerun course,
        # as the Mongo modulestore doesn't support multiple runs of the same course.
        store = modulestore()
        with store.default_store('split'):
            store.clone_course(source_course_key, destination_course_key, user_id, fields=fields)

        update_unit_discussion_state_from_discussion_blocks(destination_course_key, user_id)

        # set initial permissions for the user to access the course.
        initialize_permissions(destination_course_key, User.objects.get(id=user_id))

        # update state: Succeeded
        CourseRerunState.objects.succeeded(course_key=destination_course_key)

        COURSE_RERUN_COMPLETED.send_event(
            time=datetime.now(timezone.utc),
            course=CourseData(
                course_key=destination_course_key
            )
        )
        # call edxval to attach videos to the rerun
        copy_course_videos(source_course_key, destination_course_key)

        # Copy RestrictedCourse
        restricted_course = RestrictedCourse.objects.filter(course_key=source_course_key).first()

        if restricted_course:
            country_access_rules = CountryAccessRule.objects.filter(restricted_course=restricted_course)
            new_restricted_course = clone_instance(restricted_course, {'course_key': destination_course_key})
            for country_access_rule in country_access_rules:
                clone_instance(country_access_rule, {'restricted_course': new_restricted_course})

        org_data = ensure_organization(destination_course_key.org)
        add_organization_course(org_data, destination_course_key)
        return "succeeded"

    except DuplicateCourseError:
        # do NOT delete the original course, only update the status
        CourseRerunState.objects.failed(course_key=destination_course_key)
        LOGGER.exception('Course Rerun Error')
        return "duplicate course"

    # catch all exceptions so we can update the state and properly cleanup the course.
    except Exception as exc:  # pylint: disable=broad-except
        # update state: Failed
        CourseRerunState.objects.failed(course_key=destination_course_key)
        LOGGER.exception('Course Rerun Error')

        try:
            # cleanup any remnants of the course
            modulestore().delete_course(destination_course_key, user_id)
        except ItemNotFoundError:
            # it's possible there was an error even before the course block was created
            pass

        return "exception: " + str(exc)


def deserialize_fields(json_fields):
    fields = json.loads(json_fields)
    for field_name, value in fields.items():
        fields[field_name] = getattr(CourseFields, field_name).from_json(value)
    return fields


def _parse_time(time_isoformat):
    """ Parses time from iso format """
    return datetime.strptime(
        # remove the +00:00 from the end of the formats generated within the system
        time_isoformat.split('+')[0],
        "%Y-%m-%dT%H:%M:%S.%f"
    ).replace(tzinfo=UTC)


@shared_task
@set_code_owner_attribute
def update_search_index(course_id, triggered_time_isoformat):
    """ Updates course search index. """
    try:
        course_key = CourseKey.from_string(course_id)

        # We skip search indexing for CCX courses because there is currently
        # some issue around Modulestore caching that makes it prohibitively
        # expensive (sometimes hours-long for really complex courses).
        if isinstance(course_key, CCXLocator):
            LOGGER.warning(
                'Search indexing skipped for CCX Course %s (this is currently too slow to run in production)',
                course_id
            )
            return

        CoursewareSearchIndexer.index(modulestore(), course_key, triggered_at=(_parse_time(triggered_time_isoformat)))

    except SearchIndexingError as exc:
        error_list = exc.error_list
        LOGGER.error(
            "Search indexing error for complete course %s - %s - %s",
            course_id,
            str(exc),
            error_list,
        )
    else:
        LOGGER.debug('Search indexing successful for complete course %s', course_id)


@shared_task
@set_code_owner_attribute
def update_library_index(library_id, triggered_time_isoformat):
    """ Updates course search index. """
    try:
        library_key = CourseKey.from_string(library_id)
        LibrarySearchIndexer.index(modulestore(), library_key, triggered_at=(_parse_time(triggered_time_isoformat)))

    except SearchIndexingError as exc:
        LOGGER.error('Search indexing error for library %s - %s', library_id, str(exc))
    else:
        LOGGER.debug('Search indexing successful for library %s', library_id)


@shared_task
@set_code_owner_attribute
def update_special_exams_and_publish(course_key_str):
    """
    Registers special exams for a given course and calls publishing flow.

    on_course_publish expects that the edx-proctoring subsystem has been refreshed
    before being executed, so both functions are called here synchronously.
    """
    from cms.djangoapps.contentstore.exams import register_exams
    from cms.djangoapps.contentstore.proctoring import register_special_exams as register_exams_legacy
    from openedx.core.djangoapps.credit.signals.handlers import on_course_publish

    course_key = CourseKey.from_string(course_key_str)
    LOGGER.info('Attempting to register exams for course %s', course_key_str)

    # Call the appropriate handler for either the exams IDA or the edx-proctoring plugin
    register_exams_handler = register_exams if exams_ida_enabled(course_key) else register_exams_legacy
    try:
        register_exams_handler(course_key)
        LOGGER.info('Successfully registered exams for course %s', course_key_str)
    # pylint: disable=broad-except
    except Exception as exception:
        LOGGER.exception(exception)

    LOGGER.info('Publishing course %s', course_key_str)
    on_course_publish(course_key)


class CourseExportTask(UserTask):  # pylint: disable=abstract-method
    """
    Base class for course and library export tasks.
    """

    @staticmethod
    def calculate_total_steps(arguments_dict):
        """
        Get the number of in-progress steps in the export process, as shown in the UI.

        For reference, these are:

        1. Exporting
        2. Compressing
        """
        return 2

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Create a name for this particular import task instance.

        Arguments:
            arguments_dict (dict): The arguments given to the task function

        Returns:
            str: The generated name
        """
        key = arguments_dict['course_key_string']
        return f'Export of {key}'


@shared_task(base=CourseExportTask, bind=True)
# Note: The decorator @set_code_owner_attribute cannot be used here because the UserTaskMixin
#   does stack inspection and can't handle additional decorators.
def export_olx(self, user_id, course_key_string, language):
    """
    Export a course or library to an OLX .tar.gz archive and prepare it for download.
    """
    set_code_owner_attribute_from_module(__name__)
    courselike_key = CourseKey.from_string(course_key_string)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        with translation_language(language):
            self.status.fail(UserErrors.UNKNOWN_USER_ID.format(user_id))
        return
    if not has_course_author_access(user, courselike_key):
        with translation_language(language):
            self.status.fail(UserErrors.PERMISSION_DENIED)
        return

    if isinstance(courselike_key, LibraryLocator):
        courselike_block = modulestore().get_library(courselike_key)
    else:
        courselike_block = modulestore().get_course(courselike_key)

    try:
        self.status.set_state('Exporting')
        set_custom_attribute("exporting_started", str(courselike_key))
        tarball = create_export_tarball(courselike_block, courselike_key, {}, self.status)
        set_custom_attribute("exporting_completed", str(courselike_key))
        artifact = UserTaskArtifact(status=self.status, name='Output')
        artifact.file.save(name=os.path.basename(tarball.name), content=File(tarball))
        artifact.save()
    # catch all exceptions so we can record useful error messages
    except Exception as exception:  # pylint: disable=broad-except
        LOGGER.exception('Error exporting course %s', courselike_key, exc_info=True)
        if self.status.state != UserTaskStatus.FAILED:
            self.status.fail({'raw_error_msg': str(exception)})
        return


def create_export_tarball(course_block, course_key, context, status=None):
    """
    Generates the export tarball, or returns None if there was an error.

    Updates the context with any error information if applicable.
    """
    name = course_block.url_name
    export_file = NamedTemporaryFile(prefix=name + '.',
                                     suffix=".tar.gz")  # lint-amnesty, pylint: disable=consider-using-with
    root_dir = path(mkdtemp())

    try:
        if isinstance(course_key, LibraryLocator):
            export_library_to_xml(modulestore(), contentstore(), course_key, root_dir, name)
        else:
            set_custom_attribute("exporting_course_to_xml_started", str(course_key))
            export_course_to_xml(modulestore(), contentstore(), course_block.id, root_dir, name)

            set_custom_attribute("exporting_course_to_xml_completed", str(course_key))
        if status:
            status.set_state('Compressing')
            set_custom_attribute("compressing_started", str(course_key))
            status.increment_completed_steps()
        LOGGER.debug('tar file being generated at %s', export_file.name)
        with tarfile.open(name=export_file.name, mode='w:gz') as tar_file:
            tar_file.add(root_dir / name, arcname=name)

    except SerializationError as exc:
        LOGGER.exception('There was an error exporting %s', course_key, exc_info=True)
        parent = None
        try:
            failed_item = modulestore().get_item(exc.location)
            parent_loc = modulestore().get_parent_location(failed_item.location)

            if parent_loc is not None:
                parent = modulestore().get_item(parent_loc)
        except:  # pylint: disable=bare-except
            # if we have a nested exception, then we'll show the more generic error message
            pass

        context.update({
            'in_err': True,
            'raw_err_msg': str(exc),
            'edit_unit_url': reverse_usage_url("container_handler", parent.location) if parent else "",
        })
        if status:
            status.fail(json.dumps({'raw_error_msg': context['raw_err_msg'],
                                    'edit_unit_url': context['edit_unit_url']}))
        raise
    except Exception as exc:
        LOGGER.exception('There was an error exporting %s', course_key, exc_info=True)
        context.update({
            'in_err': True,
            'edit_unit_url': None,
            'raw_err_msg': str(exc)})
        if status:
            status.fail(json.dumps({'raw_error_msg': context['raw_err_msg']}))
        raise
    finally:
        if os.path.exists(root_dir / name):
            shutil.rmtree(root_dir / name)

    set_custom_attribute("compressing_completed", str(course_key))
    return export_file


class CourseImportTask(UserTask):  # pylint: disable=abstract-method
    """
    Base class for course and library import tasks.
    """

    @staticmethod
    def calculate_total_steps(arguments_dict):
        """
        Get the number of in-progress steps in the import process, as shown in the UI.

        For reference, these are:

        1. Unpacking
        2. Verifying
        3. Updating
        """
        return 3

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Create a name for this particular import task instance.

        Arguments:
            arguments_dict (dict): The arguments given to the task function

        Returns:
            str: The generated name
        """
        key = arguments_dict['course_key_string']
        filename = arguments_dict['archive_name']
        return f'Import of {key} from {filename}'


def sync_discussion_settings(course_key, user):
    """
    Syncs the discussion settings for a course with the DiscussionsConfiguration model.
    """
    course = modulestore().get_course(course_key)
    try:
        discussion_config = DiscussionsConfiguration.objects.get(context_key=course_key)
        discussion_settings = course.discussions_settings

        if (
            ENABLE_NEW_STRUCTURE_DISCUSSIONS.is_enabled()
            and not course.discussions_settings.get('provider_type', None) == Provider.OPEN_EDX
            and not course.discussions_settings.get('provider', None) == Provider.OPEN_EDX
        ):
            LOGGER.info(f"New structure is enabled, also updating {course_key} to use new provider")
            course.discussions_settings['enable_graded_units'] = False
            course.discussions_settings['unit_level_visibility'] = True
            course.discussions_settings['provider_type'] = Provider.OPEN_EDX
            modulestore().update_item(course, user.id)

            discussion_config.provider_type = Provider.OPEN_EDX

        discussion_config.enable_graded_units = discussion_settings['enable_graded_units']
        discussion_config.unit_level_visibility = discussion_settings['unit_level_visibility']
        discussion_config.save()
        LOGGER.info(f'Course import {course.id}: DiscussionsConfiguration synced as per course')
    except Exception as exc:  # pylint: disable=broad-except
        LOGGER.info(f'Course import {course.id}: DiscussionsConfiguration sync failed: {exc}')


@shared_task(base=CourseImportTask, bind=True)
# Note: The decorator @set_code_owner_attribute cannot be used here because the UserTaskMixin
#   does stack inspection and can't handle additional decorators.
# lint-amnesty, pylint: disable=too-many-statements
def import_olx(self, user_id, course_key_string, archive_path, archive_name, language):
    """
    Import a course or library from a provided OLX .tar.gz or .zip archive.
    """
    set_code_owner_attribute_from_module(__name__)
    current_step = 'Unpacking'
    courselike_key = CourseKey.from_string(course_key_string)
    set_custom_attributes_for_course_key(courselike_key)
    log_prefix = f'Course import {courselike_key}'
    self.status.set_state(current_step)

    data_root = path(settings.GITHUB_REPO_ROOT)
    subdir = base64.urlsafe_b64encode(repr(courselike_key).encode('utf-8')).decode('utf-8')
    course_dir = data_root / subdir

    def validate_user():
        """Validate if the user exists otherwise log error. """
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            with translation_language(language):
                self.status.fail(UserErrors.USER_PERMISSION_DENIED)
            LOGGER.error(f'{log_prefix}: Unknown User: {user_id}')
            monitor_import_failure(courselike_key, current_step, exception=exc)
            return

    def user_has_access(user):
        """Return True if user has studio write access to the given course."""
        has_access = has_course_author_access(user, courselike_key)
        if not has_access:
            message = f'User permission denied: {user.username}'
            with translation_language(language):
                self.status.fail(UserErrors.COURSE_PERMISSION_DENIED)
            LOGGER.error(f'{log_prefix}: {message}')
            monitor_import_failure(courselike_key, current_step, message=message)
        return has_access

    def file_is_supported():
        """Check if it is a supported file."""
        file_is_valid = archive_name.endswith(IMPORTABLE_FILE_TYPES)

        if not file_is_valid:
            message = f'Unsupported file {archive_name}'
            with translation_language(language):
                self.status.fail(UserErrors.INVALID_FILE_TYPE)
            LOGGER.error(f'{log_prefix}: {message}')
            monitor_import_failure(courselike_key, current_step, message=message)
        return file_is_valid

    def file_exists_in_storage():
        """Verify archive path exists in storage."""
        archive_path_exists = course_import_export_storage.exists(archive_path)

        if not archive_path_exists:
            message = f'Uploaded file {archive_path} not found'
            with translation_language(language):
                self.status.fail(UserErrors.FILE_NOT_FOUND)
            LOGGER.error(f'{log_prefix}: {message}')
            monitor_import_failure(courselike_key, current_step, message=message)
        return archive_path_exists

    def verify_root_name_exists(course_dir, root_name):
        """Verify root xml file exists."""

        def get_all_files(directory):
            """
            For each file in the directory, yield a 2-tuple of (file-name,
            directory-path)
            """
            for directory_path, _dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    yield (filename, directory_path)

        def get_dir_for_filename(directory, filename):
            """
            Returns the directory path for the first file found in the directory
            with the given name.  If there is no file in the directory with
            the specified name, return None.
            """
            for name, directory_path in get_all_files(directory):
                if name == filename:
                    return directory_path
            return None

        dirpath = get_dir_for_filename(course_dir, root_name)
        if not dirpath:
            message = UserErrors.FILE_MISSING.format(root_name)
            with translation_language(language):
                self.status.fail(message)
            LOGGER.error(f'{log_prefix}: {message}')
            monitor_import_failure(courselike_key, current_step, message=message)
            return
        return dirpath

    user = validate_user()
    if not user:
        return

    if not user_has_access(user):
        return

    if not file_is_supported():
        return

    is_library = isinstance(courselike_key, LibraryLocator)
    is_course = not is_library
    if is_library:
        root_name = LIBRARY_ROOT
        courselike_block = modulestore().get_library(courselike_key)
        import_func = import_library_from_xml
    else:
        root_name = COURSE_ROOT
        courselike_block = modulestore().get_course(courselike_key)
        import_func = import_course_from_xml

    # Locate the uploaded OLX archive (and download it from S3 if necessary)
    # Do everything in a try-except block to make sure everything is properly cleaned up.
    try:
        LOGGER.info(f'{log_prefix}: unpacking step started')

        temp_filepath = course_dir / get_valid_filename(archive_name)
        if not course_dir.isdir():
            os.mkdir(course_dir)

        LOGGER.info(f'{log_prefix}: importing course to {temp_filepath}')

        # Copy the OLX archive from where it was uploaded to (S3, Swift, file system, etc.)
        if not file_exists_in_storage():
            return

        with course_import_export_storage.open(archive_path, 'rb') as source:
            with open(temp_filepath, 'wb') as destination:
                def read_chunk():
                    """
                    Read and return a sequence of bytes from the source file.
                    """
                    return source.read(FILE_READ_CHUNK)

                for chunk in iter(read_chunk, b''):
                    destination.write(chunk)

        LOGGER.info(f'{log_prefix}: Download from storage complete')
        # Delete from source location
        course_import_export_storage.delete(archive_path)

        # If the course has an entrance exam then remove it and its corresponding milestone.
        # current course state before import.
        if is_course:
            if courselike_block.entrance_exam_enabled:
                fake_request = RequestFactory().get('/')
                fake_request.user = user
                from .views.entrance_exam import remove_entrance_exam_milestone_reference

                # TODO: Is this really ok?  Seems dangerous for a live course
                remove_entrance_exam_milestone_reference(fake_request, courselike_key)
                LOGGER.info(f'{log_prefix}: entrance exam milestone content reference has been removed')
    # Send errors to client with stage at which error occurred.
    except Exception as exception:  # pylint: disable=broad-except
        if course_dir.isdir():
            shutil.rmtree(course_dir)
            LOGGER.info(f'{log_prefix}: Temp data cleared')

        self.status.fail(UserErrors.UNKNOWN_ERROR_IN_UNPACKING)
        LOGGER.exception(f'{log_prefix}: Unknown error while unpacking', exc_info=True)
        monitor_import_failure(courselike_key, current_step, exception=exception)
        return

    # try-finally block for proper clean up after receiving file.
    try:
        try:
            safe_extractall(temp_filepath, course_dir)
        except SuspiciousOperation as exc:
            with translation_language(language):
                self.status.fail(UserErrors.UNSAFE_ARCHIVE_FILE)
            LOGGER.error(f'{log_prefix}: Unsafe archive file')
            monitor_import_failure(courselike_key, current_step, exception=exc)
            return

        current_step = 'Verifying'
        self.status.set_state(current_step)
        self.status.increment_completed_steps()
        LOGGER.info(f'{log_prefix}: Uploaded file extracted. Verification step started')

        dirpath = verify_root_name_exists(course_dir, root_name)
        if not dirpath:
            return

        if not validate_course_olx(courselike_key, dirpath, self.status):
            return

        dirpath = os.path.relpath(dirpath, data_root)

        current_step = 'Updating'
        self.status.set_state(current_step)
        self.status.increment_completed_steps()
        LOGGER.info(f'{log_prefix}: Extracted file verified. Updating course started')

        courselike_items = import_func(
            modulestore(), user.id,
            settings.GITHUB_REPO_ROOT, [dirpath],
            load_error_blocks=False,
            static_content_store=contentstore(),
            target_id=courselike_key,
            verbose=True,
        )

        new_location = courselike_items[0].location
        LOGGER.debug('new course at %s', new_location)

        LOGGER.info(f'{log_prefix}: Course import successful')
        set_custom_attribute('course_import_completed', True)
    except (CourseImportException, InvalidProctoringProvider, DuplicateCourseError) as known_exe:
        handle_course_import_exception(courselike_key, known_exe, self.status)
    except Exception as exception:  # pylint: disable=broad-except
        handle_course_import_exception(courselike_key, exception, self.status, known=False)
    finally:
        if course_dir.isdir():
            shutil.rmtree(course_dir)
            LOGGER.info(f'{log_prefix}: Temp data cleared')

        if self.status.state == 'Updating' and is_course:
            # Reload the course so we have the latest state
            course = modulestore().get_course(courselike_key)
            if course.entrance_exam_enabled:
                entrance_exam_chapter = modulestore().get_items(
                    course.id,
                    qualifiers={'category': 'chapter'},
                    settings={'is_entrance_exam': True}
                )[0]

                metadata = {'entrance_exam_id': str(entrance_exam_chapter.location)}
                CourseMetadata.update_from_dict(metadata, course, user)
                from .views.entrance_exam import add_entrance_exam_milestone
                add_entrance_exam_milestone(course.id, entrance_exam_chapter)
                LOGGER.info(f'Course import {course.id}: Entrance exam imported')
    if is_course:
        sync_discussion_settings(courselike_key, user)


@shared_task
@set_code_owner_attribute
def update_all_outlines_from_modulestore_task():
    """
    Celery task that creates multiple celery tasks - one per learning_sequence course outline
    to regenerate. The list of course keys to regenerate comes from the proxy model itself.
    """
    course_key_list = [str(course_key) for course_key in CourseOutlineRegenerate.get_course_outline_ids()]
    for course_key_str in course_key_list:
        try:
            course_key = CourseKey.from_string(course_key_str)
            if not key_supports_outlines(course_key):
                LOGGER.warning(
                    (
                        "update_multiple_outlines_from_modulestore_task called for course key"
                        " %s, which does not support learning_sequence outlines."
                    ),
                    course_key_str
                )
                continue

            update_outline_from_modulestore_task.delay(course_key_str)
        except Exception:  # pylint: disable=broad-except
            # Swallow the exception to continue the loop through course keys - but log it.
            LOGGER.exception("Could not create course outline for course %s", course_key_str)


@shared_task
@set_code_owner_attribute
def update_outline_from_modulestore_task(course_key_str: str):
    """
    Celery task that creates a learning_sequence course outline.
    """
    try:
        course_key = CourseKey.from_string(course_key_str)
        if not key_supports_outlines(course_key):
            LOGGER.warning(
                (
                    "update_outline_from_modulestore_task called for course key"
                    " %s, which does not support learning_sequence outlines."
                ),
                course_key_str
            )
            return

        update_outline_from_modulestore(course_key)
    except Exception:  # pylint: disable=broad-except
        LOGGER.exception("Could not create course outline for course %s", course_key_str)
        raise  # Re-raise so that errors are noted in reporting.


def validate_course_olx(courselike_key, course_dir, status):
    """
    Validates course olx and records the errors as an artifact.

    Arguments:
        courselike_key: A locator identifies a course resource.
        course_dir: complete path to the course olx
        status: UserTaskStatus object.
    """
    olx_is_valid = True
    log_prefix = f'Course import {courselike_key}'
    validation_failed_mesg = 'CourseOlx validation failed.'
    is_library = isinstance(courselike_key, LibraryLocator)

    if is_library:
        return olx_is_valid

    if not course_import_olx_validation_is_enabled():
        return olx_is_valid
    try:
        __, errorstore, __ = olxcleaner.validate(
            filename=course_dir,
            steps=settings.COURSE_OLX_VALIDATION_STAGE,
            ignore=settings.COURSE_OLX_VALIDATION_IGNORE_LIST,
            allowed_xblocks=ALL_ALLOWED_XBLOCKS
        )
    except Exception:  # pylint: disable=broad-except
        LOGGER.exception(f'{log_prefix}: CourseOlx could not be validated')
        return olx_is_valid

    has_errors = errorstore.return_error(ErrorLevel.ERROR.value)
    if not has_errors:
        return olx_is_valid

    LOGGER.error(f'{log_prefix}: {validation_failed_mesg}')
    log_errors_to_artifact(errorstore, status)

    if bypass_olx_failure_enabled():
        return olx_is_valid

    monitor_import_failure(courselike_key, status.state, message=validation_failed_mesg)
    status.fail(UserErrors.OLX_VALIDATION_FAILED)
    return False


def log_errors_to_artifact(errorstore, status):
    """Log errors as a task artifact."""

    def get_error_by_type(error_type):
        return [error for error in error_report if error.startswith(error_type)]

    error_summary = report_error_summary(errorstore)
    error_report = report_errors(errorstore)
    message = json.dumps({
        'summary': error_summary,
        'errors': get_error_by_type(ErrorLevel.ERROR.name),
        'warnings': get_error_by_type(ErrorLevel.WARNING.name),
    })
    UserTaskArtifact.objects.create(status=status, name='OLX_VALIDATION_ERROR', text=message)


def handle_course_import_exception(courselike_key, exception, status, known=True):
    """
    Handle course import exception and fail task status.
    Arguments:
        courselike_key: A locator identifies a course resource.
        exception: Exception object
        status: UserTaskStatus object.
        known: boolean indicating if this is a known failure or unknown.
    """
    exception_message = str(exception)
    log_prefix = f"Course import {courselike_key}:"
    LOGGER.exception(f"{log_prefix} Error while importing course: {exception_message}")
    task_fail_message = UserErrors.UNKNOWN_ERROR_IN_IMPORT
    monitor_import_failure(courselike_key, status.state, exception=exception)

    if known:
        task_fail_message = exception_message

    if status.state != UserTaskStatus.FAILED:
        status.fail(task_fail_message)


def _parse_organization(org_name):
    """Find a matching organization name, if one does not exist, specify that this is the *unspecfied* organization"""
    try:
        ensure_organization(org_name)
    except InvalidOrganizationException:
        return 'None'
    return Organization.objects.get(short_name=org_name)


def copy_v1_user_roles_into_v2_library(v2_library_key, v1_library_key):
    """
    write the access and edit permissions of a v1 library into a v2 library.
    """

    def _get_users_by_access_level(v1_library_key):
        """
        Get a permissions object for a library which contains a list of user IDs for every V2 permissions level,
        based on V1 library roles.
        The following mapping exists for a library:
        V1 Library Role -> V2 Permission Level
        LibraryUserRole -> READ_LEVEL
        CourseStaffRole -> AUTHOR_LEVEL
        CourseInstructorRole -> ADMIN_LEVEL
        """
        permissions = {}
        permissions[v2contentlib_api.AccessLevel.READ_LEVEL] = list(LibraryUserRole(v1_library_key).users_with_role())
        permissions[v2contentlib_api.AccessLevel.AUTHOR_LEVEL] = list(CourseStaffRole(v1_library_key).users_with_role())
        permissions[v2contentlib_api.AccessLevel.ADMIN_LEVEL] = list(
            CourseInstructorRole(v1_library_key).users_with_role()
        )
        return permissions

    permissions = _get_users_by_access_level(v1_library_key)
    for access_level in permissions.keys():  # lint-amnesty, pylint: disable=consider-iterating-dictionary
        for user in permissions[access_level]:
            v2contentlib_api.set_library_user_permissions(v2_library_key, user, access_level)


@shared_task(time_limit=30)
@set_code_owner_attribute
def delete_v1_library(v1_library_key_string):
    """
    Delete a v1 library index by key string.
    """
    v1_library_key = CourseKey.from_string(v1_library_key_string)
    if not modulestore().get_library(v1_library_key):
        raise KeyError(f"Library not found: {v1_library_key}")
    try:
        delete_course(v1_library_key, ModuleStoreEnum.UserID.mgmt_command, True)
        LOGGER.info(f"Deleted course {v1_library_key}")
    except Exception as error:  # lint-amnesty, pylint: disable=broad-except
        return {
            "v1_library_id": v1_library_key_string,
            "status": "FAILED",
            "msg":
            f"Error occurred deleting library: {str(error)}"
        }

    return {
        "v1_library_id": v1_library_key_string,
        "status": "SUCCESS",
        "msg": "SUCCESS"
    }


@shared_task(time_limit=30)
@set_code_owner_attribute
def validate_all_library_source_blocks_ids_for_course(course_key_string, v1_to_v2_lib_map):
    """Search a Modulestore for all library source blocks in a course by querying mongo.
        replace all source_library_ids with the corresponding v2 value from the map
    """
    course_id = CourseKey.from_string(course_key_string)
    store = modulestore()
    with store.bulk_operations(course_id):
        visited = []
        for branch in [ModuleStoreEnum.BranchName.draft, ModuleStoreEnum.BranchName.published]:
            blocks = store.get_items(
                course_id.for_branch(branch),
                settings={'source_library_id': {'$exists': True}}
            )
            for xblock in blocks:
                if xblock.source_library_id not in v1_to_v2_lib_map.values():
                    # lint-amnesty, pylint: disable=broad-except
                    raise Exception(
                        f'{xblock.source_library_id} in {course_id} is not found in mapping. Validation failed'
                    )
                visited.append(xblock.source_library_id)
    # return sucess
    return visited


@shared_task(time_limit=30)
@set_code_owner_attribute
def replace_all_library_source_blocks_ids_for_course(course_key_string, v1_to_v2_lib_map):  # lint-amnesty, pylint: disable=useless-return
    """Search a Modulestore for all library source blocks in a course by querying mongo.
        replace all source_library_ids with the corresponding v2 value from the map.

        This will trigger a publish on the course for every published library source block.
    """
    store = modulestore()
    course_id = CourseKey.from_string(course_key_string)

    with store.bulk_operations(course_id):
        #for branch in [ModuleStoreEnum.BranchName.draft, ModuleStoreEnum.BranchName.published]:
        draft_blocks, published_blocks = [
            store.get_items(
                course_id.for_branch(branch),
                settings={'source_library_id': {'$exists': True}}
            )
            for branch in [ModuleStoreEnum.BranchName.draft, ModuleStoreEnum.BranchName.published]
        ]

        published_dict = {block.location: block for block in published_blocks}

        for draft_library_source_block in draft_blocks:
            try:
                new_source_id = str(v1_to_v2_lib_map[draft_library_source_block.source_library_id])
            except KeyError:
                #skip invalid keys
                LOGGER.error(
                    'Key %s not found in mapping. Skipping block for course %s',
                    str({draft_library_source_block.source_library_id}),
                    str(course_id)
                )
                continue

            # The publsihed branch should be updated as well as the draft branch
            # This way, if authors "discard changes," they won't be reverted back to the V1 lib.
            # However, we also don't want to publish the draft branch.
            try:
                if published_dict[draft_library_source_block.location] is not None:
                    #temporarily set the published version to be the draft & publish it.
                    temp = published_dict[draft_library_source_block.location]
                    temp.source_library_id = new_source_id
                    store.update_item(temp, None)
                    store.publish(temp.location, None)
                    draft_library_source_block.source_library_id = new_source_id
                    store.update_item(draft_library_source_block, None)
            except KeyError:
                #Warn, but just update the draft block if no published block for draft block.
                LOGGER.warning(
                    'No matching published block for draft block %s',
                    str(draft_library_source_block.location)
                )
                draft_library_source_block.source_library_id = new_source_id
                store.update_item(draft_library_source_block, None)
    # return success
    return


@shared_task(time_limit=30)
@set_code_owner_attribute
def undo_all_library_source_blocks_ids_for_course(course_key_string, v1_to_v2_lib_map):  # lint-amnesty, pylint: disable=useless-return
    """Search a Modulestore for all library source blocks in a course by querying mongo.
        replace all source_library_ids with the corresponding v1 value from the inverted map.
        This is exists to undo changes made previously.
    """
    course_id = CourseKey.from_string(course_key_string)

    v2_to_v1_lib_map = {v: k for k, v in v1_to_v2_lib_map.items()}

    store = modulestore()
    draft_blocks, published_blocks = [
        store.get_items(
            course_id.for_branch(branch),
            settings={'source_library_id': {'$exists': True}}
        )
        for branch in [ModuleStoreEnum.BranchName.draft, ModuleStoreEnum.BranchName.published]
    ]

    published_dict = {block.location: block for block in published_blocks}

    for draft_library_source_block in draft_blocks:
        try:
            new_source_id = str(v2_to_v1_lib_map[draft_library_source_block.source_library_id])
        except KeyError:
            #skip invalid keys
            LOGGER.error(
                'Key %s not found in mapping. Skipping block for course %s',
                str({draft_library_source_block.source_library_id}),
                str(course_id)
            )
            continue

        # The publsihed branch should be updated as well as the draft branch
        # This way, if authors "discard changes," they won't be reverted back to the V1 lib.
        # However, we also don't want to publish the draft branch.
        try:
            if published_dict[draft_library_source_block.location] is not None:
                #temporarily set the published version to be the draft & publish it.
                temp = published_dict[draft_library_source_block.location]
                temp.source_library_id = new_source_id
                store.update_item(temp, None)
                store.publish(temp.location, None)
                draft_library_source_block.source_library_id = new_source_id
                store.update_item(draft_library_source_block, None)
        except KeyError:
            #Warn, but just update the draft block if no published block for draft block.
            LOGGER.warning(
                'No matching published block for draft block %s',
                str(draft_library_source_block.location)
            )
            draft_library_source_block.source_library_id = new_source_id
            store.update_item(draft_library_source_block, None)
    # return success
    return


class CourseLinkCheckTask(UserTask):  # pylint: disable=abstract-method
    """
    Base class for course link check tasks.
    """

    @staticmethod
    def calculate_total_steps(arguments_dict):
        """
        Get the number of in-progress steps in the link check process, as shown in the UI.

        For reference, these are:
        1. Scanning
        """
        return 1

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Create a name for this particular task instance.

        Arguments:
            arguments_dict (dict): The arguments given to the task function

        Returns:
            str: The generated name
        """
        key = arguments_dict['course_key_string']
        return f'Broken link check of {key}'


# -------------- Course optimizer functions ------------------


@shared_task(base=CourseLinkCheckTask, bind=True)
# Note: The decorator @set_code_owner_attribute cannot be used here because the UserTaskMixin
#   does stack inspection and can't handle additional decorators.
def check_broken_links(self, user_id, course_key_string, language):
    """
    Checks for broken links in a course and store the results in a file.
    """
    set_code_owner_attribute_from_module(__name__)
    return _check_broken_links(self, user_id, course_key_string, language)


def _check_broken_links(task_instance, user_id, course_key_string, language):
    """
    Checks for broken links in a course and stores the results in a file.
    Also checks for previous run links if the feature is enabled.
    """
    user = _validate_user(task_instance, user_id, language)

    task_instance.status.set_state(UserTaskStatus.IN_PROGRESS)
    course_key = CourseKey.from_string(course_key_string)

    url_list = _scan_course_for_links(course_key)
    previous_run_links = []
    urls_to_validate = url_list

    if enable_course_optimizer_check_prev_run_links(course_key):
        previous_run_course_key = get_previous_run_course_key(course_key)
        if previous_run_course_key:

            # Separate previous run links from regular links BEFORE validation
            urls_to_validate = []
            for block_id, url in url_list:
                if contains_course_reference(url, previous_run_course_key):
                    previous_run_links.append([block_id, url, LinkState.PREVIOUS_RUN])
                else:
                    urls_to_validate.append([block_id, url])

    validated_url_list = asyncio.run(_validate_urls_access_in_batches(urls_to_validate, course_key, batch_size=100))
    broken_or_locked_urls, retry_list = _filter_by_status(validated_url_list)

    if retry_list:
        retry_results = _retry_validation(retry_list, course_key, retry_count=3)
        broken_or_locked_urls.extend(retry_results)

    all_links = broken_or_locked_urls + previous_run_links
    try:
        task_instance.status.increment_completed_steps()

        file_name = str(course_key)
        broken_links_file = NamedTemporaryFile(prefix=file_name + '.', suffix='.json')
        LOGGER.debug(f'[Link Check] json file being generated at {broken_links_file.name}')

        with open(broken_links_file.name, 'w') as file:
            json.dump(all_links, file, indent=4)

        _write_broken_links_to_file(all_links, broken_links_file)

        artifact = UserTaskArtifact(status=task_instance.status, name='BrokenLinks')
        _save_broken_links_file(artifact, broken_links_file)

    # catch all exceptions so we can record useful error messages
    except Exception as e:  # pylint: disable=broad-except
        LOGGER.exception('Error checking links for course %s', course_key, exc_info=True)
        if task_instance.status.state != UserTaskStatus.FAILED:
            task_instance.status.fail({'raw_error_msg': str(e)})


def _validate_user(task, user_id, language):
    """Validate if the user exists. Otherwise log an unknown user id error."""
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist as exc:
        with translation_language(language):
            task.status.fail(UserErrors.UNKNOWN_USER_ID.format(user_id))
        return


def _scan_course_for_links(course_key):
    """
    Scans a course for links found in the data contents of
    blocks, course updates, handouts, and custom pages.

    Returns:
        list: block id and URL pairs

    Example return:
    [
        [block_id1, url1],
        [block_id2, url2],
        ...
    ]
    """
    verticals = modulestore().get_items(
        course_key,
        qualifiers={'category': 'vertical'},
        revision=ModuleStoreEnum.RevisionOption.published_only
    )
    blocks = []
    urls_to_validate = []
    course = modulestore().get_course(course_key)

    for vertical in verticals:
        blocks.extend(vertical.get_children())

    for block in blocks:
        # Excluding 'drag-and-drop-v2' as it contains data of object type instead of string, causing errors,
        # and it doesn't contain user-facing links to scan.
        if block.category == 'drag-and-drop-v2':
            continue
        block_id = str(block.location)
        block_info = get_block_info(block)
        block_data = block_info['data']
        url_list = extract_content_URLs_from_course(block_data)
        urls_to_validate += [[block_id, url] for url in url_list]

    course_updates_data = _scan_course_updates_for_links(course)
    handouts_data = _scan_course_handouts_for_links(course)
    custom_pages_data = _scan_custom_pages_for_links(course)

    for update in course_updates_data:
        for url in update['urls']:
            urls_to_validate.append([update['block_id'], url])

    for handout in handouts_data:
        for url in handout['urls']:
            urls_to_validate.append([handout['block_id'], url])

    for page in custom_pages_data:
        for url in page['urls']:
            urls_to_validate.append([page['block_id'], url])

    return urls_to_validate


def extract_content_URLs_from_course(content):
    """
    Finds and returns a list of URLs in the given content.
    Uses multiple regex patterns to find URLs in various contexts:
    - URLs in href and src attributes
    - Standalone URLs starting with http(s)://
    Excludes strings that are only '#' or start with 'data:'.

    Arguments:
        content (str): entire content of a block

    Returns:
        list: urls
    """
    url_list = set()

    # Regex to match URLs in href and src attributes, or standalone URLs
    regex = (
        r'(?:href|src)=["\'](?!#|data:)([^"\']+)["\']'
        r'|(?:^|[\s\'"(<>])((?:https?://|http://|https://|www\.)[^\s\'")<>]+)(?=[\s\'")<>]|$)'
    )

    # Update list to include URLs found in the content
    matches = re.findall(regex, content, re.IGNORECASE)
    for match in matches:
        url = match[0] or match[1]
        if url:
            url_list.add(url)

    return url_list


def _scan_course_updates_for_links(course):
    """
    Scans course updates for links.

    Returns:
        list: course update data with links
    """
    course_updates = []
    try:
        store = modulestore()
        usage_key = create_course_info_usage_key(course, "updates")
        updates_block = store.get_item(usage_key)

        if updates_block and hasattr(updates_block, "data"):
            update_items = get_course_update_items(updates_block)

            for update in update_items:
                if update.get("status") != "deleted":
                    update_content = update.get("content", "")
                    url_list = extract_content_URLs_from_course(update_content)

                    course_updates.append(
                        {
                            "displayName": update.get("date", "Unknown"),
                            "block_id": update.get("id", str(usage_key)),
                            "urls": url_list,
                        }
                    )

            return course_updates

        return course_updates
    except Exception as e:  # pylint: disable=broad-exception-caught
        LOGGER.debug(f"Error scanning course updates: {e}")
        return course_updates


def _scan_course_handouts_for_links(course):
    """
    Scans course handouts for links.

    Returns:
        list: handouts data with links
    """

    course_handouts = []
    try:
        store = modulestore()
        usage_key = create_course_info_usage_key(course, "handouts")
        handouts_block = store.get_item(usage_key)

        if handouts_block and hasattr(handouts_block, "data") and handouts_block.data:
            url_list = extract_content_URLs_from_course(handouts_block.data)
            course_handouts.append(
                {"name": "handouts", "block_id": str(usage_key), "urls": url_list}
            )

        return course_handouts
    except Exception as e:  # pylint: disable=broad-exception-caught
        LOGGER.debug(f"Error scanning course handouts: {e}")
        return course_handouts


def _scan_custom_pages_for_links(course):
    """
    Scans custom pages (static tabs) for links.

    Returns:
        list: custom pages data with links
    """

    custom_pages = []
    try:
        store = modulestore()
        course_key = course.id

        for tab in course.tabs:
            if isinstance(tab, StaticTab):
                try:
                    # Get the static tab content
                    static_tab_loc = course_key.make_usage_key(
                        "static_tab", tab.url_slug
                    )
                    static_tab_block = store.get_item(static_tab_loc)

                    if static_tab_block and hasattr(static_tab_block, "data"):
                        url_list = extract_content_URLs_from_course(static_tab_block.data)

                        custom_pages.append(
                            {
                                "displayName": tab.name,
                                "block_id": str(static_tab_loc),
                                "urls": url_list,
                            }
                        )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    LOGGER.debug(f"Error scanning static tab {tab.name}: {e}")
                    continue

        return custom_pages
    except Exception as e:  # pylint: disable=broad-exception-caught
        LOGGER.debug(f"Error scanning custom pages: {e}")
        return custom_pages


async def _validate_urls_access_in_batches(url_list, course_key, batch_size=100):
    """
    Returns the statuses of a list of URL requests.

    Arguments:
        url_list (list): block id and URL pairs

    Returns:
        list: dictionary containing URL, associated block id, and request status
    """
    responses = []
    url_count = len(url_list)

    for i in range(0, url_count, batch_size):
        batch = url_list[i:i + batch_size]
        batch_results = await _validate_batch(batch, course_key)
        responses.extend(batch_results)
        LOGGER.debug(f'[Link Check] request batch {i // batch_size + 1} of {url_count // batch_size + 1}')

    return responses


async def _validate_batch(batch, course_key):
    """Validate a batch of URLs"""
    async with aiohttp.ClientSession() as session:
        tasks = [_validate_url_access(session, url_data, course_key) for url_data in batch]
        batch_results = await asyncio.gather(*tasks)
        return batch_results


async def _validate_url_access(session, url_data, course_key):
    """
    Validates a URL.

    Arguments:
        url_data (list): block id and URL pairs
        course_key (str): locator id for a course

    Returns:
        dict: URL, associated block id, and request status

    Example return:
        {
            'block_id': block_id1,
            'url': url1,
            'status': status
        }
    """
    block_id, url = url_data
    url = url.strip()  # Trim leading/trailing whitespace
    result = {'block_id': block_id, 'url': url}
    standardized_url = _convert_to_standard_url(url, course_key)

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        headers = DOMAIN_HEADERS.get(domain, DEFAULT_HEADERS)
    except Exception as e:  # lint-amnesty, pylint: disable=broad-except
        LOGGER.debug(f'[Link Check] Error parsing URL {url}: {str(e)}')
        headers = DEFAULT_HEADERS

    try:
        async with session.get(standardized_url, headers=headers, timeout=5) as response:
            result.update({'status': response.status})
    except Exception as e:  # lint-amnesty, pylint: disable=broad-except
        result.update({'status': None})
        LOGGER.debug(f'[Link Check] Request error when validating {url}: {str(e)}')
    return result


def _convert_to_standard_url(url, course_key):
    """
    Returns standard URLs when given studio URLs. Otherwise returns the URL as is.

    Example URLs:
        /assets/courseware/v1/506da5d6f866e8f0be44c5df8b6e6b2a/...
            ...asset-v1:edX+DemoX+Demo_Course+type@asset+block/getting-started_x250.png
        /static/getting-started_x250.png
        /container/block-v1:edX+DemoX+Demo_Course+type@vertical+block@2152d4a4aadc4cb0af5256394a3d1fc7
        /jump_to_id/2152d4a4aadc4cb0af5256394a3d1fc7
    """
    if _is_studio_url_without_base(url):
        if url.startswith('/static/'):
            processed_url = replace_static_urls(f'\"{url}\"', course_id=course_key)[1:-1]
            return 'https://' + settings.CMS_BASE + processed_url
        elif url.startswith('/jump_to_id/'):
            return f'https://{settings.LMS_BASE}/courses/{course_key}{url}'
        elif url.startswith('/'):
            return 'https://' + settings.CMS_BASE + url
        else:
            return 'https://' + settings.CMS_BASE + '/container/' + url
    else:
        return url


def _is_studio_url(url):
    """Returns True if url is a studio url."""
    return _is_studio_url_with_base(url) or _is_studio_url_without_base(url)


def _is_studio_url_with_base(url):
    """Returns True if url is a studio url with cms base."""
    return url.startswith('http://' + settings.CMS_BASE) or url.startswith('https://' + settings.CMS_BASE)


def _is_studio_url_without_base(url):
    """Returns True if url is a studio url without cms base."""
    return not url.startswith('http://') and not url.startswith('https://')


def _filter_by_status(results):
    """
    Filter results by status.

    Statuses:
        200: OK. No need to do more
        403: Forbidden. Record as locked link if it is studio link.
        403: Forbidden. Record as external-forbidden link if it is external link
        None: Error. Retry up to 3 times.
        Other: Failure. Record as broken link.

    Arguments:
        results (list): URL, associated block id, and request status

    Returns:
        filtered_results (list): list of block id, URL and if URL is locked
        retry_list (list): block id and url pairs

    Example return:
        [
            [block_id1, filtered_results_url1, link_state],
            ...
        ],
        [
            [block_id1, retry_url1],
            ...
        ]
    """
    filtered_results = []
    retry_list = []
    for result in results:
        status, block_id, url = result['status'], result['block_id'], result['url']
        if status is None and _is_studio_url(url):
            retry_list.append([block_id, url])
        elif status == 200:
            continue
        elif status == 403 and _is_studio_url(url):
            filtered_results.append([block_id, url, LinkState.LOCKED])
        elif status in [403, 500, None] and not _is_studio_url(url):
            filtered_results.append([block_id, url, LinkState.EXTERNAL_FORBIDDEN])
        else:
            filtered_results.append([block_id, url, LinkState.BROKEN])

    return filtered_results, retry_list


def _retry_validation(url_list, course_key, retry_count=3):
    """
    Retry validation for URLs that failed due to connection error.

    Returns:
        list: URLs that could not be validated due to being locked or due to persistent connection problems
    """
    results = []
    retry_list = url_list
    for i in range(retry_count):
        if retry_list:
            LOGGER.debug(f'[Link Check] retry attempt #{i + 1}')
            retry_list = _retry_validation_and_filter_results(course_key, results, retry_list)
    results.extend(retry_list)

    return results


def _retry_validation_and_filter_results(course_key, results, retry_list):
    """
    Validates URLs and then filter them by status.

    Arguments:
        retry_list: list of urls to retry

    Returns:
        list: URLs that did not pass validation and should be retried
    """
    validated_url_list = asyncio.run(
        _validate_urls_access_in_batches(retry_list, course_key, batch_size=100)
    )
    filtered_url_list, retry_list = _filter_by_status(validated_url_list)
    results.extend(filtered_url_list)
    return retry_list


def _save_broken_links_file(artifact, file_to_save):
    artifact.file.save(name=os.path.basename(file_to_save.name), content=File(file_to_save))
    artifact.save()
    return True


def _write_broken_links_to_file(broken_or_locked_urls, broken_links_file):
    with open(broken_links_file.name, 'w') as file:
        json.dump(broken_or_locked_urls, file, indent=4)


@shared_task
@set_code_owner_attribute
def handle_create_xblock_upstream_link(usage_key):
    """
    Create upstream link for a single xblock.
    If the xblock has top-level parent, verify if the link for the parent is created,
    if not, create it before any subsequent operation.
    """
    ensure_cms("handle_create_xblock_upstream_link may only be executed in a CMS context")
    try:
        xblock = modulestore().get_item(UsageKey.from_string(usage_key))
    except (ItemNotFoundError, InvalidKeyError):
        LOGGER.exception(f'Could not find item for given usage_key: {usage_key}')
        return
    if not xblock.upstream or not xblock.upstream_version:
        return
    if xblock.top_level_downstream_parent_key is not None:
        block_key = BlockKey.from_string(xblock.top_level_downstream_parent_key)
        top_level_parent_usage_key = block_key.to_usage_key(xblock.course_id)
        try:
            ContainerLink.get_by_downstream_usage_key(top_level_parent_usage_key)
        except ContainerLink.DoesNotExist:
            # The top-level parent link does not exist yet,
            # it is necessary to create it first.
            handle_create_xblock_upstream_link(str(top_level_parent_usage_key))
    create_or_update_xblock_upstream_link(xblock)


@shared_task
@set_code_owner_attribute
def handle_update_xblock_upstream_link(usage_key):
    """
    Update upstream link for a single xblock.
    """
    ensure_cms("handle_update_xblock_upstream_link may only be executed in a CMS context")
    try:
        xblock = modulestore().get_item(UsageKey.from_string(usage_key))
    except (ItemNotFoundError, InvalidKeyError):
        LOGGER.exception(f'Could not find item for given usage_key: {usage_key}')
        return
    if not xblock.upstream or xblock.upstream_version is None:
        return
    create_or_update_xblock_upstream_link(xblock)


@shared_task
@set_code_owner_attribute
def create_or_update_upstream_links(
    course_key_str: str,
    force: bool = False,
    replace: bool = False,
    created: datetime | None = None,
):
    """
    A Celery task to create or update upstream downstream links in database from course xblock content.
    """
    ensure_cms("create_or_update_upstream_links may only be executed in a CMS context")

    if not created:
        created = datetime.now(timezone.utc)
    course_status = LearningContextLinksStatus.get_or_create(course_key_str, created)
    if course_status.status in [
        LearningContextLinksStatusChoices.COMPLETED,
        LearningContextLinksStatusChoices.PROCESSING
    ] and not force:
        return
    store = modulestore()
    course_key = CourseKey.from_string(course_key_str)
    course_status.update_status(
        LearningContextLinksStatusChoices.PROCESSING,
        updated=created,
    )
    if replace:
        ComponentLink.objects.filter(downstream_context_key=course_key).delete()
        ContainerLink.objects.filter(downstream_context_key=course_key).delete()
    try:
        xblocks = store.get_items(course_key, settings={"upstream": lambda x: x is not None})
    except ItemNotFoundError:
        LOGGER.exception(f'Could not find items for given course: {course_key}')
        course_status.update_status(LearningContextLinksStatusChoices.FAILED)
        return
    for xblock in xblocks:
        create_or_update_xblock_upstream_link(xblock, created)
    course_status.update_status(LearningContextLinksStatusChoices.COMPLETED)


@shared_task
@set_code_owner_attribute
def handle_unlink_upstream_block(upstream_usage_key_string: str) -> None:
    """
    Handle updates needed to downstream blocks when the upstream link is severed.
    """
    ensure_cms("handle_unlink_upstream_block may only be executed in a CMS context")

    try:
        upstream_usage_key = UsageKey.from_string(upstream_usage_key_string)
    except (InvalidKeyError):
        LOGGER.exception(f'Invalid upstream usage_key: {upstream_usage_key_string}')
        return

    for link in ComponentLink.objects.filter(
        upstream_usage_key=upstream_usage_key,
    ):
        make_copied_tags_editable(str(link.downstream_usage_key))


@shared_task
@set_code_owner_attribute
def handle_unlink_upstream_container(upstream_container_key_string: str) -> None:
    """
    Handle updates needed to downstream blocks when the upstream link is severed.
    """
    ensure_cms("handle_unlink_upstream_container may only be executed in a CMS context")

    try:
        upstream_container_key = LibraryContainerLocator.from_string(upstream_container_key_string)
    except (InvalidKeyError):
        LOGGER.exception(f'Invalid upstream container_key: {upstream_container_key_string}')
        return

    for link in ContainerLink.objects.filter(
        upstream_container_key=upstream_container_key,
    ):
        make_copied_tags_editable(str(link.downstream_usage_key))


class CourseLinkUpdateTask(UserTask):  # pylint: disable=abstract-method
    """
    Base class for course link update tasks.
    """

    @staticmethod
    def calculate_total_steps(arguments_dict):
        """
        Get the number of in-progress steps in the link update process, as shown in the UI.

        For reference, these are:
        1. Scanning
        2. Updating
        """
        return 2

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Create a name for this particular task instance.

        Arguments:
            arguments_dict (dict): The arguments given to the task function

        Returns:
            str: The generated name
        """
        key = arguments_dict["course_id"]
        return f"Course link update of {key}"


@shared_task(base=CourseLinkUpdateTask, bind=True)
def update_course_rerun_links(
    self, user_id, course_id, action, data=None, language=None
):
    """
    Updates course links to point to the latest re-run.
    """
    set_code_owner_attribute_from_module(__name__)
    return _update_course_rerun_links(
        self, user_id, course_id, action, data, language
    )


def _update_course_rerun_links(
    task_instance, user_id, course_id, action, data, language
):
    """
    Updates course links to point to the latest re-run.

    Args:
        task_instance: The Celery task instance
        user_id: ID of the user requesting the update
        course_id: String representation of the course key
        action: 'all' or 'single'
        data: List of specific links to update (when action='single')
        language: Language code for translations
    """
    user = _validate_user(task_instance, user_id, language)
    if not user:
        return

    task_instance.status.set_state(UserTaskStatus.IN_PROGRESS)
    course_key = CourseKey.from_string(course_id)
    prev_run_course_key = get_previous_run_course_key(course_key)
    try:
        task_instance.status.set_state("Scanning")

        if action == "all":
            url_list = _scan_course_for_links(course_key)
            links_to_update = []

            # Filter only course-specific links that need updating
            for block_id, url in url_list:
                if _course_link_update_required(url, course_key, prev_run_course_key):
                    links_to_update.append(
                        {
                            "id": block_id,
                            "url": url,
                            "type": _determine_link_type(block_id),
                        }
                    )
        else:
            # Process only single link updates
            links_to_update = data or []

        task_instance.status.increment_completed_steps()

        task_instance.status.set_state("Updating")

        updated_links = []
        for link_data in links_to_update:
            try:
                new_url = _update_link_to_latest_rerun(
                    link_data, course_key, prev_run_course_key, user
                )
                updated_links.append(
                    {
                        "original_url": link_data.get("url", ""),
                        "new_url": new_url,
                        "type": link_data.get("type", "unknown"),
                        "id": link_data.get("id", ""),
                        "success": True,
                    }
                )
            except Exception as e:  # pylint: disable=broad-except
                LOGGER.error(
                    f'Failed to update link {link_data.get("url", "")}: {str(e)}'
                )
                updated_links.append(
                    {
                        "original_url": link_data.get("url", ""),
                        "new_url": link_data.get("url", ""),
                        "type": link_data.get("type", "unknown"),
                        "id": link_data.get("id", ""),
                        "success": False,
                        "error_message": str(e),
                    }
                )

        task_instance.status.increment_completed_steps()

        file_name = f"{str(course_key)}_link_updates"
        results_file = NamedTemporaryFile(prefix=file_name + ".", suffix=".json")

        with open(results_file.name, "w") as file:
            json.dump(updated_links, file, indent=4)

        artifact = UserTaskArtifact(
            status=task_instance.status, name="LinkUpdateResults"
        )
        artifact.file.save(
            name=os.path.basename(results_file.name), content=File(results_file)
        )
        artifact.save()

        # Update the existing broken links file to reflect the updated links
        _update_broken_links_file_with_updated_links(course_key, updated_links)

        task_instance.status.succeed()

    except Exception as e:  # pylint: disable=broad-except
        LOGGER.exception(
            "Error updating links for course %s", course_key, exc_info=True
        )
        if task_instance.status.state != UserTaskStatus.FAILED:
            task_instance.status.fail({"raw_error_msg": str(e)})


def _course_link_update_required(url, course_key, prev_run_course_key):
    """
    Checks if a course link needs to be updated for a re-run.

    Args:
        url: The URL to check
        course_key: The current course key
        prev_run_course_key: The previous course run key

    Returns:
        bool: True if the link needs updating
    """

    if not all((url, course_key, prev_run_course_key)):
        return False

    course_id_match = contains_course_reference(url, prev_run_course_key)
    if course_id_match:
        return True

    return False


def _replace_exact_course_reference(url, old_course_key, new_course_key):
    """
    Replaces exact course key references in a URL, avoiding partial matches.

    Args:
        url: The URL to update
        old_course_key: The course key to replace
        new_course_key: The course key to replace with

    Returns:
        str: Updated URL with exact course key replacements
    """
    if not old_course_key or not new_course_key or not url:
        return url

    old_course_pattern = re.escape(str(old_course_key))

    # Ensure the course key is followed by '/' or end of string
    pattern = old_course_pattern + r'(?=/|$)'

    return re.sub(pattern, str(new_course_key), url, flags=re.IGNORECASE)


def _determine_link_type(block_id):
    """
    Determines the type of link based on block_id and URL.

    Args:
        block_id: The block ID containing the link
        url: The URL

    Returns:
        str: The type of link ('course_updates', 'handouts', 'custom_pages', 'course_content')
    """
    if not block_id:
        return "course_content"

    block_id_str = str(block_id)

    if isinstance(block_id, int):
        return "course_updates"

    if "course_info" in block_id_str and "handouts" in block_id_str:
        return "handouts"

    if "static_tab" in block_id_str:
        return "custom_pages"

    return "course_content"


def _update_link_to_latest_rerun(link_data, course_key, prev_run_course_key, user):
    """
    Updates a single link to point to the latest course re-run.

    Args:
        link_data: Dictionary containing link information
        course_key: The current course key
        prev_run_course_key: The previous course run key
        user: The authenticated user making the request

    Returns:
        str: The updated URL
    """
    original_url = link_data.get("url", "")
    block_id = link_data.get("id", "")
    link_type = link_data.get("type", "course_content")

    if not original_url:
        return original_url

    if prev_run_course_key == course_key:
        return original_url

    new_url = _replace_exact_course_reference(original_url, prev_run_course_key, course_key)

    if new_url == original_url:
        return original_url

    # condition because we're showing handouts as updates
    if link_type == "course_updates" and "handouts" in str(block_id):
        link_type = "handouts"

    _update_block_content_with_new_url(
        block_id, original_url, new_url, link_type, course_key, user
    )

    return new_url


def _update_course_updates_link(block_id, old_url, new_url, course_key, user):
    """
    Updates course updates with the new URL.

    Args:
        block_id: The ID of the block containing the link (can be usage key or update ID)
        old_url: The original URL to replace
        new_url: The new URL to use
        course_key: The current course key
        user: The authenticated user making the request
    """
    store = modulestore()
    course_updates = store.get_item(course_key.make_usage_key("course_info", "updates"))
    if hasattr(course_updates, "items"):
        for update in course_updates.items:
            update_matches = False
            if "course_info" in str(block_id) and "updates" in str(block_id):
                update_matches = True
            else:
                try:
                    update_matches = update.get("id", None) == int(block_id)
                except (ValueError, TypeError):
                    update_matches = False

            if update_matches and "content" in update:
                update["content"] = update["content"].replace(old_url, new_url)
                store.update_item(course_updates, user.id)
                LOGGER.info(
                    f"Updated course updates with new URL: {old_url} -> {new_url}"
                )


def _update_handouts_link(block_id, old_url, new_url, course_key, user):
    """
    Updates course handouts with the new URL.

    Args:
        block_id: The ID of the block containing the link
        old_url: The original URL to replace
        new_url: The new URL to use
        course_key: The current course key
        user: The authenticated user making the request
    """
    store = modulestore()
    handouts = store.get_item(course_key.make_usage_key("course_info", "handouts"))
    if hasattr(handouts, "data") and old_url in handouts.data:
        handouts.data = handouts.data.replace(old_url, new_url)
        store.update_item(handouts, user.id)
        LOGGER.info(f"Updated handouts with new URL: {old_url} -> {new_url}")


def _update_custom_pages_link(block_id, old_url, new_url, course_key, user):
    """
    Updates custom pages (static tabs) with the new URL.

    Args:
        block_id: The ID of the block containing the link (usage key string)
        old_url: The original URL to replace
        new_url: The new URL to use
        course_key: The current course key
        user: The authenticated user making the request
    """
    store = modulestore()
    try:
        usage_key = UsageKey.from_string(block_id)
        static_tab = store.get_item(usage_key)
        if hasattr(static_tab, "data") and old_url in static_tab.data:
            static_tab.data = static_tab.data.replace(old_url, new_url)
            store.update_item(static_tab, user.id)
            LOGGER.info(
                f"Updated static tab {block_id} with new URL: {old_url} -> {new_url}"
            )
    except InvalidKeyError:
        LOGGER.warning(f"Invalid usage key for static tab: {block_id}")


def _update_course_content_link(block_id, old_url, new_url, course_key, user):
    """
    Updates course content blocks with the new URL.

    Args:
        block_id: The ID of the block containing the link (usage key string)
        old_url: The original URL to replace
        new_url: The new URL to use
        course_key: The current course key
        user: The authenticated user making the request
    """
    store = modulestore()
    try:
        usage_key = UsageKey.from_string(block_id)
        block = store.get_item(usage_key)
        if hasattr(block, "data") and old_url in block.data:
            block.data = block.data.replace(old_url, new_url)
            store.update_item(block, user.id)
            store.publish(block.location, user.id)
            LOGGER.info(
                f"Updated block {block_id} data with new URL: {old_url} -> {new_url}"
            )

    except InvalidKeyError:
        LOGGER.warning(f"Invalid usage key for block: {block_id}")


def _update_block_content_with_new_url(block_id, old_url, new_url, link_type, course_key, user):
    """
    Updates the content of a block in the modulestore to replace old URL with new URL.

    Args:
        block_id: The ID of the block containing the link
        old_url: The original URL to replace
        new_url: The new URL to use
        link_type: The type of link ('course_content', 'course_updates', 'handouts', 'custom_pages')
        course_key: The current course key
        user: The authenticated user making the request
    """
    if link_type == "course_updates":
        _update_course_updates_link(block_id, old_url, new_url, course_key, user)
    elif link_type == "handouts":
        _update_handouts_link(block_id, old_url, new_url, course_key, user)
    elif link_type == "custom_pages":
        _update_custom_pages_link(block_id, old_url, new_url, course_key, user)
    else:
        _update_course_content_link(block_id, old_url, new_url, course_key, user)


def _update_broken_links_file_with_updated_links(course_key, updated_links):
    """
    Updates the existing broken links file to reflect the status of updated links.

    This function finds the latest broken links file for the course and updates it
    to remove successfully updated links or update their status.

    Args:
        course_key: The current course key
        updated_links: List of updated link results from the link update task
    """
    try:
        # Find the latest broken links task artifact for this course
        latest_artifact = UserTaskArtifact.objects.filter(
            name="BrokenLinks", status__name__contains=str(course_key)
        ).order_by("-created").first()

        if not latest_artifact or not latest_artifact.file:
            LOGGER.debug(f"No broken links file found for course {course_key}")
            return

        # Read the existing broken links file
        try:
            with latest_artifact.file.open("r") as file:
                existing_broken_links = json.load(file)
        except (json.JSONDecodeError, IOError) as e:
            LOGGER.error(
                f"Failed to read broken links file for course {course_key}: {e}"
            )
            return

        successful_results = []
        for result in updated_links:
            if not result.get("success"):
                continue
            original_url = result.get("original_url") or _get_original_url_from_updated_result(result, course_key)
            if not original_url:
                continue
            successful_results.append(
                {
                    "original_url": original_url,
                    "new_url": result.get("new_url"),
                    "type": result.get("type"),
                    "id": str(result.get("id")) if result.get("id") is not None else None,
                }
            )

        updated_broken_links = []
        for link in existing_broken_links:
            if len(link) >= 3:
                block_id, url, link_state = link[0], link[1], link[2]

                applied = False
                for res in successful_results:
                    if res["original_url"] != url:
                        continue

                    if _update_result_applies_to_block(res, block_id) and res.get('id') == str(block_id):
                        new_url = res["new_url"]
                        updated_broken_links.append([block_id, new_url, link_state])
                        applied = True
                        break

                if not applied:
                    updated_broken_links.append(link)
            else:
                updated_broken_links.append(link)

        # Create a new temporary file with updated data
        file_name = f"{course_key}_updated"
        updated_file = NamedTemporaryFile(prefix=file_name + ".", suffix=".json")

        with open(updated_file.name, "w") as file:
            json.dump(updated_broken_links, file, indent=4)

        # Update the existing artifact with the new file
        latest_artifact.file.save(
            name=os.path.basename(updated_file.name), content=File(updated_file)
        )
        latest_artifact.save()

        LOGGER.info(f"Successfully updated broken links file for course {course_key}")

    except Exception as e:  # pylint: disable=broad-except
        LOGGER.error(f"Failed to update broken links file for course {course_key}: {e}")


def _get_original_url_from_updated_result(update_result, course_key):
    """
    Reconstruct the original URL from an update result.

    Args:
        update_result: The update result containing new_url and other info
        course_key: The current course key

    Returns:
        str: The original URL before update, or None if it cannot be determined
    """
    try:
        new_url = update_result.get("new_url", "")
        if not new_url or str(course_key) not in new_url:
            return None

        prev_run_course_key = get_previous_run_course_key(course_key)
        if not prev_run_course_key:
            return None

        return new_url.replace(str(course_key), str(prev_run_course_key))

    except Exception as e:  # pylint: disable=broad-except
        LOGGER.debug(
            f"Failed to reconstruct original URL from update result: {e}"
        )
        return None


def _update_result_applies_to_block(result_entry, block_id):
    """
    Determine if a given update result applies to a specific broken-link block id.

    The task update results contain a 'type' and an 'id' indicating where the
    replacement was applied. A single URL may appear in multiple places (course
    content, course_updates, handouts, custom pages). We should only apply the
    replacement to broken-link entries that match the same target area.
    """
    try:
        result_type = (result_entry.get("type") or "course_content").lower()
        result_id = result_entry.get("id")
        block_id_str = str(block_id) if block_id is not None else ""
        result_id_str = str(result_id) if result_id is not None else None

        if result_id_str and block_id_str == result_id_str:
            return True

        is_course_info = "course_info" in block_id_str
        is_updates_section = "updates" in block_id_str
        is_handouts_section = "handouts" in block_id_str
        is_static_tab = "static_tab" in block_id_str

        block_category = (
            "course_updates" if is_course_info and is_updates_section else
            "handouts" if is_course_info and is_handouts_section else
            "custom_pages" if is_static_tab else
            "course_content"
        )

        return block_category == result_type
    except Exception:  # pylint: disable=broad-except
        return False


class LegacyLibraryContentToItemBank(UserTask):  # pylint: disable=abstract-method
    """
    Base class for course and library export tasks.
    """

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Create a name for this particular import task instance.

        Arguments:
            arguments_dict (dict): The arguments given to the task function

        Returns:
            str: The generated name
        """
        key = arguments_dict['course_key']
        return f'Updating legacy library content blocks references of {key}'


def _cancel_old_tasks(course_key: str, user: User, ignore_task_ids: list[str]):
    """
    Cancel all old instances of this particular migration task.
    """
    task_name = LegacyLibraryContentToItemBank.generate_name({'course_key': course_key})
    tasks_to_cancel = UserTaskStatus.objects.filter(
        user=user,
        name=task_name,
    ).exclude(
        # (excluding that aren't running)
        state__in=(UserTaskStatus.CANCELED, UserTaskStatus.FAILED, UserTaskStatus.SUCCEEDED)
    ).exclude(
        task_id__in=ignore_task_ids
    )
    for task in tasks_to_cancel:
        task.cancel()


@shared_task(base=LegacyLibraryContentToItemBank, bind=True)
def migrate_course_legacy_library_blocks_to_item_bank(self, user_id: int, course_key: str):
    """
    Migrate legacy course library blocks to Item Bank.

    Depending on the number of blocks and its children blocks this operation can take a significant
    amount of time and this is why it is run as a celery task.
    """
    ensure_cms("Legacy library content references may only be executed in CMS")
    set_code_owner_attribute_from_module(__name__)
    _cancel_old_tasks(course_key, self.status.user, [self.status.task_id])
    try:
        key = CourseKey.from_string(course_key)
    except InvalidKeyError as exc:
        LOGGER.exception(f'Invalid course key: {course_key}')
        self.status.fail(str(exc))
        return
    self.status.set_state(UserTaskStatus.IN_PROGRESS)
    blocks = get_ready_to_migrate_legacy_library_content_blocks(key)
    store = modulestore()
    try:
        with store.bulk_operations(key):
            for block in blocks:
                self.status.set_state(f'Migrating block: {block.usage_key}')
                block.v2_update_children_upstream_version(user_id)
    except Exception as exc:  # pylint: disable=broad-except
        LOGGER.exception(f'Error while migrating blocks: {exc}')
        self.status.fail(str(exc))
