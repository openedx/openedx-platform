""" receivers of course_published and library_updated events in order to trigger indexing task """


import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.dispatch import receiver
from opaque_keys.edx.keys import CourseKey
from openedx_events.content_authoring.data import (
    CourseCatalogData,
    CourseData,
    CourseScheduleData,
    LibraryBlockData,
    LibraryContainerData,
    XBlockData,
)
from openedx_events.content_authoring.signals import (
    COURSE_CATALOG_INFO_CHANGED,
    COURSE_IMPORT_COMPLETED,
    COURSE_RERUN_COMPLETED,
    LIBRARY_BLOCK_DELETED,
    LIBRARY_CONTAINER_DELETED,
    XBLOCK_CREATED,
    XBLOCK_DELETED,
    XBLOCK_UPDATED,
)
from pytz import UTC

from cms.djangoapps.contentstore.courseware_index import (
    CourseAboutSearchIndexer,
    CoursewareSearchIndexer,
    LibrarySearchIndexer,
)
from common.djangoapps.track.event_transaction_utils import get_event_transaction_id, get_event_transaction_type
from common.djangoapps.util.block_utils import yield_dynamic_block_descendants
from lms.djangoapps.grades.api import task_compute_all_grades_for_course
from openedx.core.djangoapps.content.learning_sequences.api import key_supports_outlines
from openedx.core.djangoapps.discussions.tasks import update_discussions_settings_from_course_task
from openedx.core.lib.gating import api as gating_api
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import SignalHandler, modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from ..models import ComponentLink, ContainerLink
from ..tasks import (
    create_or_update_upstream_links,
    handle_create_xblock_upstream_link,
    handle_update_xblock_upstream_link,
    handle_unlink_upstream_block,
    handle_unlink_upstream_container,
)
from .signals import GRADING_POLICY_CHANGED

log = logging.getLogger(__name__)

GRADING_POLICY_COUNTDOWN_SECONDS = 3600


def locked(expiry_seconds, key):  # lint-amnesty, pylint: disable=missing-function-docstring
    def task_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f'{func.__name__}-{kwargs[key]}'
            if cache.add(cache_key, "true", expiry_seconds):
                log.info('Locking task in cache with key: %s for %s seconds', cache_key, expiry_seconds)
                return func(*args, **kwargs)
            else:
                log.info('Task with key %s already exists in cache', cache_key)
        return wrapper
    return task_decorator


def _create_catalog_data_for_signal(course_key: CourseKey) -> (Optional[datetime], Optional[CourseCatalogData]):
    """
    Creates data for catalog-info-changed signal when course is published.

    Arguments:
        course_key: Key of the course to announce catalog info changes for

    Returns:
        (datetime, CourseCatalogData): Tuple including the timestamp of the
            event, and data for signal, or (None, None) if not appropriate
            to send on this signal.
    """
    # Only operate on real courses, not libraries.
    if not course_key.is_course:
        return None, None

    store = modulestore()
    with store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
        course = store.get_course(course_key)
        timestamp = course.subtree_edited_on.replace(tzinfo=timezone.utc)
        return timestamp, CourseCatalogData(
            course_key=course_key.for_branch(None),  # Shouldn't be necessary, but just in case...
            name=course.display_name,
            schedule_data=CourseScheduleData(
                start=course.start,
                pacing='self' if course.self_paced else 'instructor',
                end=course.end,
                enrollment_start=course.enrollment_start,
                enrollment_end=course.enrollment_end,
            ),
            hidden=course.catalog_visibility in ['about', 'none'] or course_key.deprecated,
            invitation_only=course.invitation_only,
        )


def emit_catalog_info_changed_signal(course_key: CourseKey):
    """
    Given the key of a recently published course, send course data to catalog-info-changed signal.
    """
    timestamp, catalog_info = _create_catalog_data_for_signal(course_key)
    if catalog_info is not None:
        # .. event_implemented_name: COURSE_CATALOG_INFO_CHANGED
        # .. event_type: org.openedx.content_authoring.course.catalog_info.changed.v1
        COURSE_CATALOG_INFO_CHANGED.send_event(time=timestamp, catalog_info=catalog_info)


@receiver(SignalHandler.course_published)
def listen_for_course_publish(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Receives publishing signal and performs publishing related workflows, such as
    registering proctored exams, building up credit requirements, and performing
    search indexing
    """
    # import here, because signal is registered at startup, but items in tasks are not yet able to be loaded
    from cms.djangoapps.contentstore.tasks import (
        update_outline_from_modulestore_task,
        update_search_index,
        update_special_exams_and_publish
    )

    # DEVELOPER README: probably all tasks here should use transaction.on_commit
    # to avoid stale data, but the tasks are owned by many teams and are often
    # working well enough. Several instead use a waiting strategy.
    # If you are in here trying to figure out why your task is not working correctly,
    # consider whether it is getting stale data and if so choose to wait for the transaction
    # like exams or put your task to sleep for a while like discussions.
    # You will not be able to replicate these errors in an environment where celery runs
    # in process because it will be inside the transaction. Use the settings from
    # devstack_with_worker.py, and consider adding a time.sleep into send_bulk_published_signal
    # if you really want to make sure that the task happens before the data is ready.

    # register special exams asynchronously after the data is ready
    course_key_str = str(course_key)
    transaction.on_commit(lambda: update_special_exams_and_publish.delay(course_key_str))

    if key_supports_outlines(course_key):
        # Push the course outline to learning_sequences asynchronously.
        update_outline_from_modulestore_task.delay(course_key_str)

    # Kick off a courseware indexing action after the data is ready
    if CoursewareSearchIndexer.indexing_is_enabled() and CourseAboutSearchIndexer.indexing_is_enabled():
        transaction.on_commit(lambda: update_search_index.delay(course_key_str, datetime.now(UTC).isoformat()))

    update_discussions_settings_from_course_task.apply_async(
        args=[course_key_str],
        countdown=settings.DISCUSSION_SETTINGS['COURSE_PUBLISH_TASK_DELAY'],
    )

    # Send to a signal for catalog info changes as well, but only once we know the transaction is committed.
    transaction.on_commit(lambda: emit_catalog_info_changed_signal(course_key))


@receiver(SignalHandler.course_deleted)
def listen_for_course_delete(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Catches the signal that a course has been deleted
    and removes its entry from the Course About Search index.
    """
    CourseAboutSearchIndexer.remove_deleted_items(course_key)


@receiver(SignalHandler.library_updated)
def listen_for_library_update(sender, library_key, **kwargs):  # pylint: disable=unused-argument
    """
    Receives signal and kicks off celery task to update search index
    """

    if LibrarySearchIndexer.indexing_is_enabled():
        # import here, because signal is registered at startup, but items in tasks are not yet able to be loaded
        from cms.djangoapps.contentstore.tasks import update_library_index

        update_library_index.delay(str(library_key), datetime.now(UTC).isoformat())


@receiver(SignalHandler.item_deleted)
def handle_item_deleted(**kwargs):
    """
    Receives the item_deleted signal sent by Studio when an XBlock is removed from
    the course structure and removes any gating milestone data associated with it or
    its descendants.

    Arguments:
        kwargs (dict): Contains the content usage key of the item deleted

    Returns:
        None
    """

    usage_key = kwargs.get('usage_key')
    if usage_key:
        # Strip branch info
        usage_key = usage_key.for_branch(None)
        course_key = usage_key.course_key
        try:
            deleted_block = modulestore().get_item(usage_key)
        except ItemNotFoundError:
            return
        id_list = {deleted_block.location}
        for block in yield_dynamic_block_descendants(deleted_block, kwargs.get('user_id')):
            # Remove prerequisite milestone data
            gating_api.remove_prerequisite(block.location)
            # Remove any 'requires' course content milestone relationships
            gating_api.set_required_content(course_key, block.location, None, None, None)
            id_list.add(block.location)

        ComponentLink.objects.filter(downstream_usage_key__in=id_list).delete()
        ContainerLink.objects.filter(downstream_usage_key__in=id_list).delete()


@receiver(GRADING_POLICY_CHANGED)
@locked(expiry_seconds=GRADING_POLICY_COUNTDOWN_SECONDS, key='course_key')
def handle_grading_policy_changed(sender, **kwargs):
    # pylint: disable=unused-argument
    """
    Receives signal and kicks off celery task to recalculate grades
    """
    kwargs = {
        'course_key': str(kwargs.get('course_key')),
        'grading_policy_hash': str(kwargs.get('grading_policy_hash')),
        'event_transaction_id': str(get_event_transaction_id()),
        'event_transaction_type': str(get_event_transaction_type()),
    }
    result = task_compute_all_grades_for_course.apply_async(kwargs=kwargs, countdown=GRADING_POLICY_COUNTDOWN_SECONDS)
    log.info("Grades: Created {task_name}[{task_id}] with arguments {kwargs}".format(
        task_name=task_compute_all_grades_for_course.name,
        task_id=result.task_id,
        kwargs=kwargs,
    ))


@receiver(XBLOCK_CREATED)
def create_upstream_downstream_link_handler(**kwargs):
    """
    Automatically create upstream->downstream link in database.
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):
        log.error("Received null or incorrect data for event")
        return

    handle_create_xblock_upstream_link.delay(str(xblock_info.usage_key))


@receiver(XBLOCK_UPDATED)
def update_upstream_downstream_link_handler(**kwargs):
    """
    Automatically update upstream->downstream link in database.
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):
        log.error("Received null or incorrect data for event")
        return

    handle_update_xblock_upstream_link.delay(str(xblock_info.usage_key))


@receiver(XBLOCK_DELETED)
def delete_upstream_downstream_link_handler(**kwargs):
    """
    Delete upstream->downstream link from database on xblock delete.
    """
    xblock_info = kwargs.get("xblock_info", None)
    if not xblock_info or not isinstance(xblock_info, XBlockData):
        log.error("Received null or incorrect data for event")
        return

    ComponentLink.objects.filter(
        downstream_usage_key=xblock_info.usage_key
    ).delete()
    ContainerLink.objects.filter(
        downstream_usage_key=xblock_info.usage_key
    ).delete()


@receiver([COURSE_IMPORT_COMPLETED, COURSE_RERUN_COMPLETED])
def handle_upstream_links_on_signal(**kwargs):
    """
    Automatically create upstream->downstream links for course in database on new import or rerun.
    """
    course_data = kwargs.get("course", None)
    if not course_data or not isinstance(course_data, CourseData):
        log.error("Received null or incorrect data for event")
        return

    create_or_update_upstream_links.delay(
        str(course_data.course_key),
        force=True,
        replace=True
    )


@receiver(LIBRARY_BLOCK_DELETED)
def unlink_upstream_block_handler(**kwargs):
    """
    Handle unlinking the upstream (library) block from any downstream (course) blocks.
    """
    library_block = kwargs.get("library_block", None)
    if not library_block or not isinstance(library_block, LibraryBlockData):
        log.error("Received null or incorrect data for event")
        return

    handle_unlink_upstream_block.delay(str(library_block.usage_key))


@receiver(LIBRARY_CONTAINER_DELETED)
def unlink_upstream_container_handler(**kwargs):
    """
    Handle unlinking the upstream (library) container from any downstream (course) blocks.
    """
    library_container = kwargs.get("library_container", None)
    if not library_container or not isinstance(library_container, LibraryContainerData):  # pragma: no cover
        log.error("Received null or incorrect data for event")
        return

    handle_unlink_upstream_container.delay(str(library_container.container_key))
