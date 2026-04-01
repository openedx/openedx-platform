"""
Content Libraries Python API to import blocks from Courseware
=============================================================

Content Libraries can import blocks from Courseware (Modulestore).  The import
can be done per-course, by listing its content, and supports both access to
remote platform instances as well as local modulestore APIs.  Additionally,
there are Celery-based interfaces suitable for background processing controlled
through RESTful APIs (see :mod:`.views`).
"""
import abc
import collections
import base64
import hashlib
import logging

from django.conf import settings
import requests

from opaque_keys.edx.locator import (
    LibraryUsageLocatorV2,
    LibraryLocator as LibraryLocatorV1,
)
from opaque_keys.edx.keys import UsageKey
from edx_rest_api_client.client import OAuthAPIClient

from openedx.core.lib.xblock_serializer.api import serialize_modulestore_block_for_openedx_content
from xmodule.modulestore.django import modulestore

from .. import tasks
from ..models import ContentLibrary, ContentLibraryBlockImportTask
from .blocks import (
    LibraryBlockAlreadyExists,
    add_library_block_static_asset_file,
    create_library_block,
    get_library_block_static_asset_files,
    get_library_block,
    set_library_block_olx,
)
from .libraries import publish_changes

log = logging.getLogger(__name__)

__all__ = [
    "EdxModulestoreImportClient",
    "EdxApiImportClient",
    "import_blocks_create_task",
]


class BaseEdxImportClient(abc.ABC):
    """
    Base class for all courseware import clients.

    Import clients are wrappers tailored to implement the steps used in the
    import APIs and can leverage different backends.  It is not aimed towards
    being a generic API client for Open edX.
    """

    EXPORTABLE_BLOCK_TYPES = {
        "drag-and-drop-v2",
        "problem",
        "html",
        "video",
    }

    def __init__(self, library_key=None, library=None, use_course_key_as_block_id_suffix=True):
        """
        Initialize an import client for a library.

        The method accepts either a library object or a key to a library object.
        """
        self.use_course_key_as_block_id_suffix = use_course_key_as_block_id_suffix
        if bool(library_key) == bool(library):
            raise ValueError('Provide at least one of `library_key` or '
                             '`library`, but not both.')
        if library is None:
            library = ContentLibrary.objects.get_by_key(library_key)
        self.library = library

    @abc.abstractmethod
    def get_block_data(self, block_key):
        """
        Get the block's OLX and static files, if any.
        """

    @abc.abstractmethod
    def get_export_keys(self, course_key):
        """
        Get all exportable block keys of a given course.
        """

    @abc.abstractmethod
    def get_block_static_data(self, asset_file):
        """
        Get the contents of an asset_file..
        """

    def import_block(self, modulestore_key):
        """
        Import a single modulestore block.
        """
        block_data = self.get_block_data(modulestore_key)

        # Get or create the block in the library.
        #
        # To dedup blocks from different courses with the same ID, we hash the
        # course key into the imported block id.

        course_key_id = base64.b32encode(
            hashlib.blake2s(
                str(modulestore_key.course_key).encode()
            ).digest()
        )[:16].decode().lower()

        # add the course_key_id if use_course_key_as_suffix is enabled to increase the namespace.
        # The option exists to not use the course key as a suffix because
        # in order to preserve learner state in the v1 to v2 libraries migration,
        # the v2 and v1 libraries' child block ids must be the same.
        block_id = (
            # Prepend 'c' to allow changing hash without conflicts.
            f"{modulestore_key.block_id}_c{course_key_id}"
            if self.use_course_key_as_block_id_suffix
            else f"{modulestore_key.block_id}"
        )

        log.info('Importing to library block: id=%s', block_id)
        try:
            library_block = create_library_block(
                self.library.library_key,
                modulestore_key.block_type,
                block_id,
            )
            dest_key = library_block.usage_key
        except LibraryBlockAlreadyExists:
            dest_key = LibraryUsageLocatorV2(
                lib_key=self.library.library_key,
                block_type=modulestore_key.block_type,
                usage_id=block_id,
            )
            get_library_block(dest_key)
            log.warning('Library block already exists: Appending static files '
                        'and overwriting OLX: %s', str(dest_key))

        # Handle static files.

        files = [
            f.path for f in
            get_library_block_static_asset_files(dest_key)
        ]
        for filename, static_file in block_data.get('static_files', {}).items():
            if filename in files:
                # Files already added, move on.
                continue
            file_content = self.get_block_static_data(static_file)
            add_library_block_static_asset_file(dest_key, filename, file_content)
            files.append(filename)

        # Import OLX.

        set_library_block_olx(dest_key, block_data['olx'])

    def import_blocks_from_course(self, course_key, progress_callback):
        """
        Import all eligible blocks from course key.

        Progress is reported through ``progress_callback``, guaranteed to be
        called within an exception handler if ``exception is not None``.
        """

        # Query the course and rerieve all course blocks.

        export_keys = self.get_export_keys(course_key)
        if not export_keys:
            raise ValueError(f"The courseware course {course_key} does not have "
                             "any exportable content.  No action taken.")

        # Import each block, skipping the ones that fail.

        for index, block_key in enumerate(export_keys):
            try:
                log.info('Importing block: %s/%s: %s', index + 1, len(export_keys), block_key)
                self.import_block(block_key)
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Error importing block: %s", block_key)
                progress_callback(block_key, index + 1, len(export_keys), exc)
            else:
                log.info('Successfully imported: %s/%s: %s', index + 1, len(export_keys), block_key)
                progress_callback(block_key, index + 1, len(export_keys), None)

        log.info("Publishing library: %s", self.library.library_key)
        publish_changes(self.library.library_key)


class EdxModulestoreImportClient(BaseEdxImportClient):
    """
    An import client based on the local instance of modulestore.
    """

    def __init__(self, modulestore_instance=None, **kwargs):
        """
        Initialize the client with a modulestore instance.
        """
        super().__init__(**kwargs)
        self.modulestore = modulestore_instance or modulestore()

    def get_block_data(self, block_key):
        """
        Get block OLX by serializing it from modulestore directly.
        """
        block = self.modulestore.get_item(block_key)
        data = serialize_modulestore_block_for_openedx_content(block)
        return {'olx': data.olx_str,
                'static_files': {s.name: s for s in data.static_files}}

    def get_export_keys(self, course_key):
        """
        Retrieve the course from modulestore and traverse its content tree.
        """
        course = self.modulestore.get_course(course_key)
        if isinstance(course_key, LibraryLocatorV1):
            course = self.modulestore.get_library(course_key)
        export_keys = set()
        blocks_q = collections.deque(course.get_children())
        while blocks_q:
            block = blocks_q.popleft()
            usage_id = block.scope_ids.usage_id
            if usage_id in export_keys:
                continue
            if usage_id.block_type in self.EXPORTABLE_BLOCK_TYPES:
                export_keys.add(usage_id)
            if block.has_children:
                blocks_q.extend(block.get_children())
        return list(export_keys)

    def get_block_static_data(self, asset_file):
        """
        Get static content from its URL if available, otherwise from its data.
        """
        if asset_file.data:
            return asset_file.data
        resp = requests.get(f"http://{settings.CMS_BASE}" + asset_file.url)
        resp.raise_for_status()
        return resp.content


class EdxApiImportClient(BaseEdxImportClient):
    """
    An import client based on a remote Open Edx API interface.

    TODO: Look over this class. We'll probably need to completely re-implement
    the import process.
    """

    URL_COURSES = "/api/courses/v1/courses/{course_key}"

    URL_MODULESTORE_BLOCK_OLX = "/api/olx-export/v1/xblock/{block_key}/"

    def __init__(self, lms_url, studio_url, oauth_key, oauth_secret, *args, **kwargs):
        """
        Initialize the API client with URLs and OAuth keys.
        """
        super().__init__(**kwargs)
        self.lms_url = lms_url
        self.studio_url = studio_url
        self.oauth_client = OAuthAPIClient(
            self.lms_url,
            oauth_key,
            oauth_secret,
        )

    def get_block_data(self, block_key):
        """
        See parent's docstring.
        """
        olx_path = self.URL_MODULESTORE_BLOCK_OLX.format(block_key=block_key)
        resp = self._get(self.studio_url + olx_path)
        return resp['blocks'][str(block_key)]

    def get_export_keys(self, course_key):
        """
        See parent's docstring.
        """
        course_blocks_url = self._get_course(course_key)['blocks_url']
        course_blocks = self._get(
            course_blocks_url,
            params={'all_blocks': True, 'depth': 'all'})['blocks']
        export_keys = []
        for block_info in course_blocks.values():
            if block_info['type'] in self.EXPORTABLE_BLOCK_TYPES:
                export_keys.append(UsageKey.from_string(block_info['id']))
        return export_keys

    def get_block_static_data(self, asset_file):
        """
        See parent's docstring.
        """
        if (asset_file['url'].startswith(self.studio_url)
                and 'export-file' in asset_file['url']):
            # We must call download this file with authentication. But
            # we only want to pass the auth headers if this is the same
            # studio instance, or else we could leak credentials to a
            # third party.
            path = asset_file['url'][len(self.studio_url):]
            resp = self._call('get', path)
        else:
            resp = requests.get(asset_file['url'])
            resp.raise_for_status()
        return resp.content

    def _get(self, *args, **kwargs):
        """
        Perform a get request to the client.
        """
        return self._json_call('get', *args, **kwargs)

    def _get_course(self, course_key):
        """
        Request details for a course.
        """
        course_url = self.lms_url + self.URL_COURSES.format(course_key=course_key)
        return self._get(course_url)

    def _json_call(self, method, *args, **kwargs):
        """
        Wrapper around request calls that ensures valid json responses.
        """
        return self._call(method, *args, **kwargs).json()

    def _call(self, method, *args, **kwargs):
        """
        Wrapper around request calls.
        """
        response = getattr(self.oauth_client, method)(*args, **kwargs)
        response.raise_for_status()
        return response


def import_blocks_create_task(library_key, course_key, use_course_key_as_block_id_suffix=True):
    """
    Create a new import block task.

    This API will schedule a celery task to perform the import, and it returns a
    import task object for polling.
    """
    library = ContentLibrary.objects.get_by_key(library_key)
    import_task = ContentLibraryBlockImportTask.objects.create(
        library=library,
        course_id=course_key,
    )
    result = tasks.import_blocks_from_course.apply_async(
        args=(import_task.pk, str(course_key), use_course_key_as_block_id_suffix)
    )
    log.info(f"Import block task created: import_task={import_task} "
             f"celery_task={result.id}")
    return import_task
