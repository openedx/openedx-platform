"""
Celery tasks for Content Libraries.

Architecture note:

    Several functions in this file manage the copying/updating of blocks in modulestore
    and openedx_content. These operations should only be performed within the context of CMS.
    However, due to existing edx-platform code structure, we've had to define the functions
    in shared source tree (openedx/) and the tasks are registered in both LMS and CMS.

    To ensure that we're not accidentally importing things from openedx_content in the LMS context,
    we use ensure_cms throughout this module.

    A longer-term solution to this issue would be to move the content_libraries app to cms:
    https://github.com/openedx/edx-platform/issues/33428
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Iterable
from io import StringIO
from tempfile import NamedTemporaryFile

from celery import Task, shared_task
from celery.exceptions import TimeoutError as CeleryTimeout
from celery.result import AsyncResult
from celery.utils.log import get_task_logger
from celery_utils.logged_task import LoggedTask
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from edx_django_utils.monitoring import (
    set_code_owner_attribute,
    set_code_owner_attribute_from_module,
    set_custom_attribute,
)
from opaque_keys import OpaqueKey
from opaque_keys.edx.locator import (
    BlockUsageLocator,
    LibraryContainerLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)
from openedx_content import api as content_api
from openedx_content.models_api import LearningPackage, PublishableEntity, PublishLog
from openedx_events.content_authoring.data import (
    ContentObjectChangedData,
    LibraryBlockData,
    LibraryContainerData,
)
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_BLOCK_CREATED,
    LIBRARY_BLOCK_DELETED,
    LIBRARY_BLOCK_PUBLISHED,
    LIBRARY_BLOCK_UPDATED,
    LIBRARY_CONTAINER_CREATED,
    LIBRARY_CONTAINER_DELETED,
    LIBRARY_CONTAINER_PUBLISHED,
    LIBRARY_CONTAINER_UPDATED,
)
from user_tasks.models import UserTaskArtifact
from user_tasks.tasks import UserTask, UserTaskStatus
from xblock.fields import Scope

from cms.djangoapps.contentstore.storage import course_import_export_storage
from openedx.core.lib import ensure_cms
from xmodule.capa_block import ProblemBlock
from xmodule.library_content_block import ANY_CAPA_TYPE_VALUE, LegacyLibraryContentBlock
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.mixed import MixedModuleStore

from . import api
from .models import ContentLibrary

log = logging.getLogger(__name__)
TASK_LOGGER = get_task_logger(__name__)

User = get_user_model()

DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'  # Should match serializer format. Redefined to avoid circular import.


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def send_change_events_for_modified_entities(
    learning_package_id: LearningPackage.ID,
    change_list: list[dict],  # we want list[ChangeLogRecordData], but that's not JSON serializable, so use dicts
):
    """
    Sends a various library-specific events for each modified library entity in
    the given change log, after any kind of edit was made in the library. This
    could be in response to an entity (component or container) being created,
    modified, deleted, un-deleted, or one of its dependencies doing those
    things.

    ⏳ This task is designed to be run asynchronously so it can handle many
       entities, but you can also call it synchronously if you are only
       processing a single entity. Handlers of the events that we emit here
       should be synchronous and fast, to support the "update one item
       synchronously" use case, but can be async if needed.
    """
    changes = [content_api.signals.ChangeLogRecordData(**r) for r in change_list]
    library = ContentLibrary.objects.get(learning_package_id=learning_package_id)
    changes_by_entity_id = {change.entity_id: change for change in changes}
    entities = (
        content_api.get_publishable_entities(learning_package_id)
        .filter(id__in=changes_by_entity_id.keys())
        .select_related("component", "container")
    )

    for entity in entities:
        change = changes_by_entity_id[entity.id]
        if hasattr(entity, "component"):
            # This is a library XBlock (component)
            block_key = api.library_component_usage_key(library.library_key, entity.component)
            event_data = LibraryBlockData(library_key=library.library_key, usage_key=block_key)
            if change.old_version is None and change.new_version:
                # .. event_implemented_name: LIBRARY_BLOCK_CREATED
                # .. event_type: org.openedx.content_authoring.library_block.created.v1
                LIBRARY_BLOCK_CREATED.send_event(library_block=event_data)
            elif change.old_version and change.new_version is None:
                # .. event_implemented_name: LIBRARY_BLOCK_DELETED
                # .. event_type: org.openedx.content_authoring.library_block.deleted.v1
                LIBRARY_BLOCK_DELETED.send_event(library_block=event_data)
            else:
                # This component was modified.
                # .. event_implemented_name: LIBRARY_BLOCK_UPDATED
                # .. event_type: org.openedx.content_authoring.library_block.updated.v1
                LIBRARY_BLOCK_UPDATED.send_event(library_block=event_data)

        elif hasattr(entity, "container"):
            container_key = api.library_container_locator(library.library_key, entity.container)
            event_data = LibraryContainerData(container_key=container_key)
            if change.old_version is None and change.new_version:
                # .. event_implemented_name: LIBRARY_CONTAINER_CREATED
                # .. event_type: org.openedx.content_authoring.content_library.container.created.v1
                LIBRARY_CONTAINER_CREATED.send_event(library_container=event_data)
            elif change.old_version and change.new_version is None:
                # .. event_implemented_name: LIBRARY_CONTAINER_DELETED
                # .. event_type: org.openedx.content_authoring.content_library.container.deleted.v1
                LIBRARY_CONTAINER_DELETED.send_event(library_container=event_data)
            else:
                # .. event_implemented_name: LIBRARY_CONTAINER_UPDATED
                # .. event_type: org.openedx.content_authoring.content_library.container.updated.v1
                LIBRARY_CONTAINER_UPDATED.send_event(library_container=event_data)
                # TODO: to optimze this, once we have https://github.com/openedx/openedx-events/pull/570 merged,
                # change the above event to use `send_async=not container_itself_changed`, so that direct changes are
                # processed immediately but side effects can happen async.

            # If the version numbers are different, this container was modified.
            # If not, it was included as a side effect of some other change, like its child being modified.
            container_itself_changed = change.old_version != change.new_version

            if container_itself_changed:
                # If entities were added/removed from this container, we need to notify things like the search index
                # that the list of parent containers for each entity has changed.
                check_container_content_changes.delay(
                    container_key_str=str(container_key),
                    old_version_id=change.old_version_id,
                    new_version_id=change.new_version_id,
                )
        else:
            log.error("Unknown publishable entity type: %s", entity)
            continue


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def check_container_content_changes(
    container_key_str: str,
    old_version_id: int | None,
    new_version_id: int | None,
):
    """
    Whenever a container is edited, we need to check if child entities were
    added or removed, and if so send out a CONTENT_OBJECT_ASSOCIATIONS_CHANGED
    event for each added/removed child.

    For example, removing an entity from a unit should result in::

        CONTENT_OBJECT_ASSOCIATIONS_CHANGED(
            object_id=...,
            changes=["units"],
        )

    ⏳ This task is always run asynchronously.
    """
    if old_version_id == new_version_id:
        return  # Same versions

    old_version = content_api.get_container_version(old_version_id) if old_version_id else None
    new_version = content_api.get_container_version(new_version_id) if new_version_id else None

    # TODO: there is no "get entity list for container version" API in openedx_content
    old_child_ids: Iterable[PublishableEntity.ID] = (
        old_version.entity_list.entitylistrow_set.values_list("entity_id", flat=True) if old_version else []
    )
    new_child_ids: Iterable[PublishableEntity.ID]= (
        new_version.entity_list.entitylistrow_set.values_list("entity_id", flat=True) if new_version else []
    )

    # If the title has changed, we notify ALL children that their parent container(s) have changed, e.g. to update the
    # list of "units this component is used in", "sections this subsection is used in", etc. in the search index.
    old_title = old_version.title if old_version else ""
    new_title = new_version.title if new_version else ""
    if old_title != new_title:
        # notify ALL current children, plus any deleted children, that their parent container(s) changed
        changed_child_ids = list(set(new_child_ids) | (set(old_child_ids) - set(new_child_ids)))
    else:
        # Normal case: we only need to notify any added or removed children that their parent container(s) changed:
        changed_child_ids = list(set(old_child_ids) ^ set(new_child_ids))

    container_key = LibraryContainerLocator.from_string(container_key_str)
    library = ContentLibrary.objects.get_by_key(container_key.lib_key)
    entities = (
        content_api.get_publishable_entities(library.learning_package_id)
        .filter(id__in=changed_child_ids)
        .select_related("component", "container")
    )
    for entity in entities:
        child_key: LibraryUsageLocatorV2 | LibraryContainerLocator
        if hasattr(entity, "component"):
            child_key = api.library_component_usage_key(library.library_key, entity.component)
        elif hasattr(entity, "container"):
            child_key = api.library_container_locator(library.library_key, entity.container)
        else:
            log.error("Unknown publishable entity type: %s", entity)
            continue
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(
                object_id=str(child_key),
                changes=[container_key.container_type + "s"],  # e.g. "units"
            ),
        )


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def send_collections_changed_events(
    publishable_entity_ids: list[PublishableEntity.ID],
    learning_package_id: LearningPackage.ID,
    library_key_str: str,
):
    """
    Sends a CONTENT_OBJECT_ASSOCIATIONS_CHANGED event for each modified library
    entity in the given list, because their associated collections have changed.

    ⏳ This task is designed to be run asynchronously so it can handle many
       entities, but you can also call it synchronously if you are only
       processing a single entity. Handlers should be synchronous and fast, to
       support the "update one item synchronously" use case, but can be async if
       needed.
    """
    library_key = LibraryLocatorV2.from_string(library_key_str)
    entities = (
        content_api.get_publishable_entities(learning_package_id)
        .filter(id__in=publishable_entity_ids)
        .select_related("component", "container")
    )

    for entity in entities:
        opaque_key: OpaqueKey

        if hasattr(entity, "component"):
            opaque_key = api.library_component_usage_key(library_key, entity.component)
        elif hasattr(entity, "container"):
            opaque_key = api.library_container_locator(library_key, entity.container)
        else:
            log.error("Unknown publishable entity type: %s", entity)
            continue

        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            content_object=ContentObjectChangedData(object_id=str(opaque_key), changes=["collections"]),
        )


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def send_events_after_publish(publish_log_id: int, library_key_str: str) -> None:
    """
    Send events to trigger actions like updating the search index, after we've
    published some items in a library.

    We use the PublishLog record so we can detect exactly what was changed,
    including any auto-published changes like child items in containers.

    This happens in a celery task so that it can be run asynchronously if
    needed, because the "publish all changes" action can potentially publish
    hundreds or even thousands of components/containers at once, and synchronous
    event handlers like updating the search index may a while to complete in
    that case.
    """
    publish_log = PublishLog.objects.get(id=publish_log_id)
    library_key = LibraryLocatorV2.from_string(library_key_str)
    affected_entities = publish_log.records.select_related(
        "entity", "entity__container", "entity__container__container_type", "entity__component",
    ).all()

    # Update anything that needs to be updated (e.g. search index):
    for record in affected_entities:
        if hasattr(record.entity, "component"):
            usage_key = api.library_component_usage_key(library_key, record.entity.component)
            # Note that this item may be newly created, updated, or even deleted - but all we care about for this event
            # is that the published version is now different. Only for draft changes do we send differentiated events.

            # .. event_implemented_name: LIBRARY_BLOCK_PUBLISHED
            # .. event_type: org.openedx.content_authoring.library_block.published.v1
            LIBRARY_BLOCK_PUBLISHED.send_event(
                library_block=LibraryBlockData(library_key=library_key, usage_key=usage_key)
            )
        elif hasattr(record.entity, "container"):
            container_key = api.library_container_locator(library_key, record.entity.container)
            # Note: this container may have been directly published, or perhaps one of its children was published and
            # it hasn't technically changed. Such ancestors of published entities are still included in the publish log.
            # .. event_implemented_name: LIBRARY_CONTAINER_PUBLISHED
            # .. event_type: org.openedx.content_authoring.content_library.container.published.v1
            LIBRARY_CONTAINER_PUBLISHED.send_event(
                library_container=LibraryContainerData(container_key=container_key)
            )
        else:
            log.warning(
                f"PublishableEntity {record.entity.pk} / {record.entity.entity_ref} "
                "was modified during publish operation but is of unknown type."
            )


def _filter_child(store, usage_key, capa_type):
    """
    Return whether this block is both a problem and has a `capa_type` which is included in the filter.
    """
    if usage_key.block_type != "problem":
        return False

    descriptor = store.get_item(usage_key, depth=0)
    assert isinstance(descriptor, ProblemBlock)
    return capa_type in descriptor.problem_types


def _problem_type_filter(store, library, capa_type):
    """ Filters library children by capa type."""
    return [key for key in library.children if _filter_child(store, key, capa_type)]


class LibrarySyncChildrenTask(UserTask):  # pylint: disable=abstract-method
    """
    Base class for tasks which operate upon library_content children.
    """

    @classmethod
    def generate_name(cls, arguments_dict) -> str:
        """
        Create a name for this particular import task instance.

        Should be both:
        a. semi human-friendly
        b. something we can query in order to determine whether the dest block has a task in progress

        Arguments:
            arguments_dict (dict): The arguments given to the task function
        """
        key = arguments_dict['dest_block_id']
        return f'Updating {key} from library'


# Note: The decorator @set_code_owner_attribute cannot be used here because the UserTaskMixin does stack
# inspection and can't handle additional decorators. So, wet set the code_owner attribute in the tasks' bodies instead.

@shared_task(base=LibrarySyncChildrenTask, bind=True)
def sync_from_library(
    self: LibrarySyncChildrenTask,
    user_id: int,
    dest_block_id: str,
    library_version: str | None,
) -> None:
    """
    Celery task to update the children of the library_content block at `dest_block_id`.

    FIXME: this is related to legacy modulestore libraries and shouldn't be part of the
    openedx.core.djangoapps.content_libraries app, which is the app for v2 libraries.
    """
    set_code_owner_attribute_from_module(__name__)
    store = modulestore()
    dest_block = store.get_item(BlockUsageLocator.from_string(dest_block_id))
    _sync_children(
        task=self,
        store=store,
        user_id=user_id,
        dest_block=dest_block,
        library_version=library_version,
    )


@shared_task(base=LibrarySyncChildrenTask, bind=True)
def duplicate_children(
    self: LibrarySyncChildrenTask,
    user_id: int,
    source_block_id: str,
    dest_block_id: str,
) -> None:
    """
    Celery task to duplicate the children from `source_block_id` to `dest_block_id`.

    FIXME: this is related to legacy modulestore libraries and shouldn't be part of the
    openedx.core.djangoapps.content_libraries app, which is the app for v2 libraries.
    """
    set_code_owner_attribute_from_module(__name__)
    store = modulestore()
    # First, populate the destination block with children imported from the library.
    # It's important that _sync_children does this at the currently-set version of the dest library
    # (someone may be duplicating an out-of-date block).
    dest_block = store.get_item(BlockUsageLocator.from_string(dest_block_id))
    _sync_children(
        task=self,
        store=store,
        user_id=user_id,
        dest_block=dest_block,
        library_version=dest_block.source_library_version,
    )
    # Then, copy over any overridden settings the course author may have applied to the blocks.
    source_block = store.get_item(BlockUsageLocator.from_string(source_block_id))
    with store.bulk_operations(source_block.scope_ids.usage_id.context_key):
        try:
            TASK_LOGGER.info('Copying Overrides from %s to %s', source_block_id, dest_block_id)
            _copy_overrides(store=store, user_id=user_id, source_block=source_block, dest_block=dest_block)
        except Exception as exception:  # pylint: disable=broad-except
            TASK_LOGGER.exception('Error Copying Overrides from %s to %s', source_block_id, dest_block_id)
            if self.status.state != UserTaskStatus.FAILED:
                self.status.fail({'raw_error_msg': str(exception)})


def _sync_children(
    task: LibrarySyncChildrenTask,
    store: MixedModuleStore,
    user_id: int,
    dest_block: LegacyLibraryContentBlock,
    library_version: str | None,
) -> None:
    """
    Implementation helper for `sync_from_library` and `duplicate_children` Celery tasks.

    Can update children with a specific library `library_version`, or latest (`library_version=None`).

    FIXME: this is related to legacy modulestore libraries and shouldn't be part of the
    openedx.core.djangoapps.content_libraries app, which is the app for v2 libraries.
    """
    source_blocks = []
    library_key = dest_block.source_library_key.for_branch(
        ModuleStoreEnum.BranchName.library
    ).for_version(library_version)
    try:
        library = store.get_library(library_key, remove_version=False, remove_branch=False, head_validation=False)
    except ItemNotFoundError:
        task.status.fail(f"Requested library {library_key} not found.")
        return
    filter_children = (dest_block.capa_type != ANY_CAPA_TYPE_VALUE)
    if filter_children:
        # Apply simple filtering based on CAPA problem types:
        source_blocks.extend(_problem_type_filter(store, library, dest_block.capa_type))
    else:
        source_blocks.extend(library.children)
    with store.bulk_operations(dest_block.scope_ids.usage_id.context_key):
        try:
            dest_block.source_library_version = str(library.location.library_key.version_guid)
            store.update_item(dest_block, user_id)
            dest_block.children = store.copy_from_template(
                source_blocks, dest_block.location, user_id, head_validation=True
            )
            # ^-- copy_from_template updates the children in the DB
            # but we must also set .children here to avoid overwriting the DB again
        except Exception as exception:  # pylint: disable=broad-except
            TASK_LOGGER.exception('Error importing children for %s', dest_block.scope_ids.usage_id, exc_info=True)
            if task.status.state != UserTaskStatus.FAILED:
                task.status.fail({'raw_error_msg': str(exception)})
            raise


def _copy_overrides(
    store: MixedModuleStore,
    user_id: int,
    source_block: LegacyLibraryContentBlock,
    dest_block: LegacyLibraryContentBlock
) -> None:
    """
    Copy any overrides the user has made on children of `source` over to the children of `dest_block`, recursively.

    FIXME: this is related to legacy modulestore libraries and shouldn't be part of the
    openedx.core.djangoapps.content_libraries app, which is the app for v2 libraries.
    """
    for field in source_block.fields.values():
        if field.scope == Scope.settings and field.is_set_on(source_block):
            setattr(dest_block, field.name, field.read_from(source_block))
    if source_block.has_children:
        for source_child_key, dest_child_key in zip(source_block.children, dest_block.children):  # noqa: B905
            _copy_overrides(
                store=store,
                user_id=user_id,
                source_block=store.get_item(source_child_key),
                dest_block=store.get_item(dest_child_key),
            )
    store.update_item(dest_block, user_id)


class LibraryBackupTask(UserTask):  # pylint: disable=abstract-method
    """
    Base class for tasks related with Library backup functionality.
    """
    NAME_PREFIX = "Library Learning Package Backup"

    @classmethod
    def generate_name(cls, arguments_dict) -> str:
        """
        Create a name for this particular backup task instance.

        Should be both:
        a. semi human-friendly
        b. something we can query in order to determine whether the library has a task in progress

        Arguments:
            arguments_dict (dict): The arguments given to the task function

        Returns:
            str: The generated name
        """
        key = arguments_dict['library_key_str']
        return f'{cls.NAME_PREFIX} of {key}'


@shared_task(base=LibraryBackupTask, bind=True)
# Note: The decorator @set_code_owner_attribute cannot be used here because the UserTaskMixin
#   does stack inspection and can't handle additional decorators.
def backup_library(self, user_id: int, library_key_str: str) -> None:
    """
    Export a library to a .zip archive and prepare it for download.
    Possible Task states:
        - Pending: Task is created but not started yet.
        - Exporting: Task is running and the library is being exported.
        - Succeeded: Task completed successfully and the exported file is available for download.
        - Failed: Task failed and the export did not complete.
    """
    ensure_cms("backup_library may only be executed in a CMS context")
    set_code_owner_attribute_from_module(__name__)
    library_key = LibraryLocatorV2.from_string(library_key_str)

    try:
        self.status.set_state('Exporting')
        set_custom_attribute("exporting_started", str(library_key))

        user = User.objects.get(id=user_id)
        _root_dir, file_path = api.create_library_v2_zip(library_key, user)
        set_custom_attribute("exporting_completed", str(library_key))

        with open(file_path, 'rb') as zipfile:
            artifact = UserTaskArtifact(status=self.status, name='Output')
            artifact.file.save(name=os.path.basename(zipfile.name), content=File(zipfile))
            artifact.save()
    except Exception as exception:  # pylint: disable=broad-except
        TASK_LOGGER.exception('Error exporting library %s', library_key, exc_info=True)
        if self.status.state != UserTaskStatus.FAILED:
            self.status.fail({'raw_error_msg': str(exception)})


class LibraryRestoreLoadError(Exception):
    def __init__(self, message, logfile=None):
        super().__init__(message)
        self.logfile = logfile


class LibraryRestoreTask(UserTask):
    """
    Base class for library restore tasks.
    """

    ARTIFACT_NAMES = {
        UserTaskStatus.FAILED: 'Error log',
        UserTaskStatus.SUCCEEDED: 'Library Restore',
    }

    ERROR_LOG_ARTIFACT_NAME = 'Error log'

    NAME_PREFIX = "Library Learning Package Restore"

    @classmethod
    def generate_name(cls, arguments_dict):
        storage_path = arguments_dict['storage_path']
        return f'{cls.NAME_PREFIX} of {storage_path}'

    def fail_with_error_log(self, logfile) -> None:
        """
        Helper method to create an error log artifact and fail the task.

        Args:
            logfile (io.StringIO): The error log content
        """
        # Prepare the error log to be saved as a file
        error_log_file = ContentFile(logfile.getvalue().encode("utf-8"))

        # Save the error log as an artifact
        artifact = UserTaskArtifact(status=self.status, name=self.ERROR_LOG_ARTIFACT_NAME)
        artifact.file.save(name=f'{self.status.task_id}-error.log', content=error_log_file)
        artifact.save()

        self.status.fail(json.dumps({'error': 'Error(s) restoring learning package'}))

    def load_learning_package(self, storage_path, user):
        """
        Load learning package from a backup file in storage.

        Args:
            storage_path (str): The path to the backup file in storage

        Returns:
            dict: The result of loading the learning package, including status and info
        Raises:
            LibraryRestoreLoadError: If there is an error loading the learning package
        """
        # First ensure the backup file exists
        if not course_import_export_storage.exists(storage_path):
            raise LibraryRestoreLoadError(f'Uploaded file {storage_path} not found')

        # Temporarily copy the file locally, and then load the learning package from it
        with NamedTemporaryFile(suffix=".zip") as tmp_file:
            with course_import_export_storage.open(storage_path, "rb") as storage_file:
                shutil.copyfileobj(storage_file, tmp_file)
                tmp_file.flush()

            TASK_LOGGER.info('Restoring learning package from temporary file %s', tmp_file.name)

            result = content_api.load_learning_package(tmp_file.name, user=user)

            # If there was an error during the load, fail the task with the error log
            if result.get("status") == "error":
                raise LibraryRestoreLoadError(
                    "Error(s) loading learning package",
                    logfile=result.get("log_file_error")
                )

            return result


@shared_task(base=LibraryRestoreTask, bind=True)
def restore_library(self, user_id, storage_path):
    """
    Restore a learning package from a backup file.
    """
    ensure_cms("restore_library may only be executed in a CMS context")
    set_code_owner_attribute_from_module(__name__)

    TASK_LOGGER.info('Starting restore of learning package from %s', storage_path)

    try:
        # Load the learning package from the backup file
        user = User.objects.get(id=user_id)
        result = self.load_learning_package(storage_path, user=user)
        learning_package_data = result.get("lp_restored_data", {})

        TASK_LOGGER.info(
            'Restored learning package (id: %s) with key %s',
            learning_package_data.get('id'),
            learning_package_data.get('package_ref')
        )

        # Save the restore details as an artifact in JSON format
        restore_data = json.dumps(result, cls=DjangoJSONEncoder)

        UserTaskArtifact.objects.create(
            status=self.status,
            name=self.ARTIFACT_NAMES[UserTaskStatus.SUCCEEDED],
            text=restore_data
        )
        TASK_LOGGER.info('Finished restore of learning package from %s', storage_path)

    except Exception as exc:  # pylint: disable=broad-except
        TASK_LOGGER.exception('Error restoring learning package from %s', storage_path)
        logfile = getattr(exc, 'logfile', StringIO("Unexpected error during library restore: " + str(exc)))
        self.fail_with_error_log(logfile)
    finally:
        # Make sure to clean up the uploaded file from storage
        course_import_export_storage.delete(storage_path)
        TASK_LOGGER.info('Deleted uploaded file %s after restore', storage_path)


def dispatch_and_wait(task_fn: Task, wait_for_full_completion: bool = False, **kwargs) -> None:
    """
    Try to wait for the given celery task to complete before returning,
    up to some reasonable timeout, and then finish anything remaining work
    asynchonrously.

    Note: we're not using async python, so this function will unfortunately
    block the current CMS worker for a few seconds.

    Usage example
    -------------

    Instead of::

        tasks.send_change_events_for_modified_entities.delay(...)

    Do::

        dispatch_and_wait(
            tasks.send_change_events_for_modified_entities,
            ...
        )

    The ``wait_for_full_completion`` param is to simplify a common pattern. When
    it's True, this will just call the function directly (not using celery) and
    wait indefinitely for it to complete. When it's False, we'll dispatch the
    task using celery and wait up to a given timeout. So you should set it True
    if you are fairly certain the task will be able to complete quickly (e.g.
    when processing a small number of changes).
    """
    if wait_for_full_completion:
        task_fn(**kwargs)
        return

    result: AsyncResult = task_fn.delay(**kwargs)
    # Try waiting a bit for the task to finish before we complete the request:
    try:
        result.get(timeout=10)
    except CeleryTimeout:
        pass
        # This is fine! The search index is still being updated, and/or other
        # event handlers are still following up on the results, but the action
        # that let to this event handler being called already *did* succeed,
        # and the events will continue to be processed in the background by the
        # celery worker until everything is updated.
