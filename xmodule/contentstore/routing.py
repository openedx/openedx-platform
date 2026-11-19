"""
A ContentStore that routes each request to the appropriate backend for the course.

Course "Files" are being moved out of the legacy MongoDB/GridFS contentstore and
into ``openedx_content`` (see openedx-core ADR 0016, "Replacing Contentstore").
During the transition, each course run's files are stored in exactly one
backend, and :class:`RoutingContentStore` sends each request to the right one,
so that the rest of the platform can keep using the ``ContentStore`` API
without knowing where a given course's files are stored.

The backends are:

* The legacy MongoDB/GridFS store (``MongoContentStore``), which is the default.
* ``VersionedContentStoreShim``, which stores files as ``openedx_content``
  Assets, for course runs whose ``CourseAssetBackend`` status is ``migrated``.

While a course run's status is ``in_progress`` (its files are being migrated),
its files are read from the legacy store, and any attempt to change them raises
:class:`~.content.ContentStoreReadOnlyError`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal, overload

from opaque_keys.edx.keys import AssetKey, CourseKey

from .content import ContentStore, ContentstoreAsset, ContentStoreReadOnlyError, StaticContent, StaticContentStream

log = logging.getLogger(__name__)


class RoutingContentStore(ContentStore):
    """
    A ``ContentStore`` that delegates each request to the backend that stores the files for the relevant course.
    """

    def __init__(self, legacy_store: ContentStore, versioned_store: ContentStore | None = None):
        """
        Initialize the router.

        ``legacy_store`` is the legacy (MongoDB/GridFS) ``ContentStore``.
        ``versioned_store`` is the store for course runs whose files have been
        migrated to ``openedx_content``. If it's not given, a
        ``VersionedContentStoreShim`` is created when it's first needed.
        """
        super().__init__()
        self._legacy_store = legacy_store
        self._versioned_store = versioned_store

    @property
    def legacy_store(self) -> ContentStore:
        """
        The legacy (MongoDB/GridFS) store.

        Most code should not use this directly, but it's needed for e.g. migrating assets out of the legacy store.
        """
        return self._legacy_store

    @property
    def versioned_store(self) -> ContentStore:
        """
        The store for course runs whose files have been migrated to ``openedx_content``.

        Most code should not use this directly.
        """
        if self._versioned_store is None:
            # Imported here because this needs Django apps to be loaded, and xmodule shouldn't depend on it at import
            # time.
            from openedx.core.djangoapps.contentstore_shim.store import (  # pylint: disable=import-outside-toplevel
                VersionedContentStoreShim,
            )
            self._versioned_store = VersionedContentStoreShim()
        return self._versioned_store

    @property
    def _all_stores(self) -> list[ContentStore]:
        """All of the backends, for operations that aren't specific to one course."""
        return [self._legacy_store, self.versioned_store]

    def _get_backend_status(self, course_key: CourseKey) -> str:
        """
        Get the ``CourseAssetBackend`` status of the given course: ``"legacy"``,
        ``"in_progress"``, or ``"migrated"``.
        """
        from openedx.core.djangoapps.contentstore_shim import api  # pylint: disable=import-outside-toplevel
        return api.get_course_asset_backend_status(course_key)

    def _store_for_course(self, course_key: CourseKey, *, write: bool = False) -> ContentStore:
        """
        Get the backend that stores the files for the given course.

        Pass ``write=True`` if the files will be changed. That raises
        ``ContentStoreReadOnlyError`` if the course's files are read-only
        because they're being migrated.
        """
        status = self._get_backend_status(course_key)
        if status == "migrated":
            return self.versioned_store
        if status == "in_progress" and write:
            raise ContentStoreReadOnlyError(
                f"The Files of {course_key} are being migrated to a new storage backend, so they can't be changed "
                "right now. Please try again later."
            )
        return self._legacy_store

    def _store_for_asset(self, location: AssetKey, *, write: bool = False) -> ContentStore:
        return self._store_for_course(location.course_key, write=write)

    # Methods that act on a single asset

    def save(self, content: StaticContent) -> StaticContent:
        return self._store_for_asset(content.location, write=True).save(content)

    @overload
    def find(
        self, location: AssetKey, throw_on_not_found: Literal[True] = True, as_stream: Literal[False] = False
    ) -> StaticContent: ...

    @overload
    def find(  # pylint: disable=arguments-differ  # (false positive with @overload)
        self, location: AssetKey, throw_on_not_found: Literal[True] = True, *, as_stream: Literal[True]
    ) -> StaticContentStream: ...

    @overload
    def find(
        self, location: AssetKey, throw_on_not_found: bool = True, as_stream: bool = False
    ) -> StaticContent | None: ...

    def find(
        self, location: AssetKey, throw_on_not_found: bool = True, as_stream: bool = False
    ) -> StaticContent | None:
        return self._store_for_asset(location).find(
            location, throw_on_not_found=throw_on_not_found, as_stream=as_stream
        )

    def delete(self, location: AssetKey | Any) -> None:
        if not isinstance(location, AssetKey):
            # MongoContentStore also accepts its own internal IDs.
            self._legacy_store.delete(location)
        else:
            self._store_for_asset(location, write=True).delete(location)

    def get_attrs(self, location: AssetKey) -> dict[str, Any]:
        return self._store_for_asset(location).get_attrs(location)

    def set_attrs(self, location: AssetKey, attr_dict: dict[str, Any]) -> None:
        self._store_for_asset(location, write=True).set_attrs(location, attr_dict)

    def get_attr(self, location: AssetKey, attr: str, default: Any = None) -> Any:
        return self._store_for_asset(location).get_attr(location, attr, default)

    def set_attr(self, location: AssetKey, attr: str, value: Any = True) -> None:
        self._store_for_asset(location, write=True).set_attr(location, attr, value)

    def export(self, location: AssetKey, output_directory: str) -> None:
        self._store_for_asset(location).export(location, output_directory)

    def generate_thumbnail(
        self,
        content: StaticContent,
        tempfile_path: str | None = None,
        dimensions: tuple[int, int] | None = None,
    ) -> tuple[StaticContent | None, AssetKey]:
        # Delegate, so that each backend can decide how (or whether) to store thumbnails.
        return self._store_for_asset(content.location, write=True).generate_thumbnail(
            content, tempfile_path=tempfile_path, dimensions=dimensions
        )

    # Methods that act on all the assets of a course

    def get_all_content_for_course(
        self,
        course_key: CourseKey,
        start: int = 0,
        maxresults: int = -1,
        sort: Sequence[tuple[str, int]] | None = None,
        filter_params: dict[str, Any] | None = None,
    ) -> tuple[list[ContentstoreAsset], int]:
        return self._store_for_course(course_key).get_all_content_for_course(
            course_key, start=start, maxresults=maxresults, sort=sort, filter_params=filter_params
        )

    def get_all_content_thumbnails_for_course(self, course_key: CourseKey) -> list[ContentstoreAsset]:
        return self._store_for_course(course_key).get_all_content_thumbnails_for_course(course_key)

    def export_all_for_course(self, course_key: CourseKey, output_directory: str, assets_policy_file: str) -> None:
        self._store_for_course(course_key).export_all_for_course(course_key, output_directory, assets_policy_file)

    def delete_all_course_assets(self, course_key: CourseKey) -> None:
        self._store_for_course(course_key, write=True).delete_all_course_assets(course_key)

    def copy_all_course_assets(self, source_course_key: CourseKey, dest_course_key: CourseKey) -> None:
        """
        Copy all of the files of a course to another course, e.g. for a rerun.

        If the destination course doesn't have a ``CourseAssetBackend`` status
        yet, it inherits the source course's backend first (see
        ``inherit_course_asset_backend_status()``), so a rerun of a migrated
        course is migrated too. If the two courses still use different backends,
        the files are copied from one backend to the other.
        """
        from openedx.core.djangoapps.contentstore_shim import api  # pylint: disable=import-outside-toplevel
        api.inherit_course_asset_backend_status(source_course_key, dest_course_key)

        source_store = self._store_for_course(source_course_key)
        dest_store = self._store_for_course(dest_course_key, write=True)
        if source_store is dest_store:
            source_store.copy_all_course_assets(source_course_key, dest_course_key)
        else:
            copy_course_assets_between_stores(source_store, source_course_key, dest_store, dest_course_key)

    # Methods that act on all courses, or on the stores themselves

    def remove_redundant_content_for_courses(self) -> int:
        """
        Delete redundant files (e.g. ``.DS_Store``, ``._*`` and ``*~`` files
        matching ``ASSET_IGNORE_REGEX``) from all courses, in every backend.

        This runs on every backend, regardless of which backend each course
        uses, so that it cleans up all the files that each backend holds. It's
        used by the ``cleanup_assets`` management command.

        Returns the total number of assets deleted.
        """
        return sum(store.remove_redundant_content_for_courses() for store in self._all_stores)

    def ensure_indexes(self) -> None:
        for store in self._all_stores:
            store.ensure_indexes()

    def close_connections(self) -> None:
        for store in self._all_stores:
            store.close_connections()

    def _drop_database(self, database: bool = True, collections: bool = True, connections: bool = True) -> None:
        for store in self._all_stores:
            store._drop_database(database, collections, connections)  # pylint: disable=protected-access


def copy_course_assets_between_stores(
    source_store: ContentStore,
    source_course_key: CourseKey,
    dest_store: ContentStore,
    dest_course_key: CourseKey,
) -> int:
    """
    Copy all of the files of a course in one ``ContentStore`` to a course in
    another ``ContentStore``, using only the generic ``ContentStore`` API.

    Thumbnails aren't copied: each store generates its own, if it stores them
    at all. Files that already exist in the destination course are replaced.

    Returns the number of files copied.
    """
    assets, _count = source_store.get_all_content_for_course(source_course_key)
    for asset in assets:
        content = source_store.find(asset["asset_key"])
        content.location = dest_course_key.make_asset_key("asset", content.location.path)
        content.thumbnail_location = None
        dest_store.save(content)
    log.info(
        "Copied %d files from %s in %s to %s in %s",
        len(assets),
        source_course_key,
        type(source_store).__name__,
        dest_course_key,
        type(dest_store).__name__,
    )
    return len(assets)
