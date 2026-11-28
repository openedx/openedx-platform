"""
API for choosing which backend stores each course's Files.

While the legacy MongoDB contentstore is being replaced (see openedx-core ADR
0016, "Replacing Contentstore"), each course run's Files are stored either in
the legacy contentstore or in ``openedx_content`` (via
:class:`~.store.VersionedContentStoreShim`). ``RoutingContentStore`` uses
:func:`get_course_asset_backend_status` to decide where to send each request.
"""

from __future__ import annotations

import logging

from django.db.transaction import atomic, on_commit
from edx_django_utils.cache import TieredCache
from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun

from .models import CourseAssetBackend

log = logging.getLogger(__name__)

Status = CourseAssetBackend.Status

# How long to cache each course's status. The cache is cleared whenever a status
# changes, so this only limits how long a stale value could survive if clearing
# the cache failed for some reason.
_STATUS_CACHE_TIMEOUT = 5 * 60  # seconds


def _status_cache_key(course_key: CourseKey) -> str:
    return f"contentstore_shim.course_asset_backend_status.{course_key}"


def get_course_asset_backend_status(course_key: CourseKey) -> Status:
    """
    Get the status of the given course run's Files, i.e. which backend stores
    them.

    Returns ``Status.LEGACY`` for course runs (and libraries) with no
    ``CourseAssetBackend`` row, including those with no ``CourseRun``.

    This is cached, so it's cheap to call for every contentstore request.
    """
    cache_key = _status_cache_key(course_key)
    cached = TieredCache.get_cached_response(cache_key)
    if cached.is_found:
        return Status(cached.value)
    status = (
        CourseAssetBackend.objects
        .filter(course_run__course_key__exact=course_key)
        .values_list("status", flat=True)
        .first()
    ) or Status.LEGACY
    TieredCache.set_all_tiers(cache_key, str(status), _STATUS_CACHE_TIMEOUT)
    return Status(status)


def set_course_asset_backend_status(course_key: CourseKey, status: Status) -> None:
    """
    Set the status of the given course run's Files.

    ⚠️ This doesn't copy any Files between backends, so after switching
    backends, the course will only see the Files in the new backend.

    Setting a course run to ``MIGRATED`` creates its ``CourseContent`` (and
    Learning Package), if it doesn't have one yet.

    Raises ``CourseRun.DoesNotExist`` if no such course run exists.
    """
    course_run = catalog_api.get_course_run(course_key)
    with atomic():
        backend, _created = CourseAssetBackend.objects.select_for_update().get_or_create(course_run=course_run)
        backend.status = status
        backend.save()


def inherit_course_asset_backend_status(source_course_key: CourseKey, dest_course_key: CourseKey) -> None:
    """
    Make a new course run (e.g. a rerun) store its Files in the same backend as
    the course run that it's copied from.

    If the source course run is ``MIGRATED``, the destination course run is set
    to ``MIGRATED`` too, so that a rerun never quietly moves back to the legacy
    contentstore. This creates the destination's ``CourseRun`` if it doesn't
    exist yet, which is normally the case for a new rerun, since its
    ``CourseRun`` is only created when it's first published. The title is copied
    from the source, and gets updated when the destination is published.

    Otherwise, the source's Files are in the legacy contentstore (even if its
    migration is ``IN_PROGRESS``), so this does nothing, and the destination
    course run stays ``LEGACY``.

    If the destination course run already has a status, it's kept, and the
    caller must copy the Files between backends.
    """
    if get_course_asset_backend_status(source_course_key) != Status.MIGRATED:
        return
    if CourseAssetBackend.objects.filter(course_run__course_key__exact=dest_course_key).exists():
        return
    with atomic():
        try:
            dest_course_run = catalog_api.get_course_run(dest_course_key)
        except CourseRun.DoesNotExist:
            source_course_run = catalog_api.get_course_run(source_course_key)
            dest_course_run = catalog_api.create_course_run_for_modulestore_course_with(
                dest_course_key,
                title=source_course_run.title,
                language_short=source_course_run.catalog_course.language_short,
            )
        CourseAssetBackend.objects.create(course_run=dest_course_run, status=Status.MIGRATED)
    log.info("Course %s inherited the %s Files backend from %s", dest_course_key, Status.MIGRATED, source_course_key)


def clear_course_asset_backend_status_cache(course_key: CourseKey) -> None:
    """
    Clear the cached status of the given course run.

    This is called automatically whenever a status changes.
    """
    cache_key = _status_cache_key(course_key)
    TieredCache.delete_all_tiers(cache_key)
    # Clear it again once the change is committed, in case another process re-cached the old status before then.
    on_commit(lambda: TieredCache.delete_all_tiers(cache_key))
