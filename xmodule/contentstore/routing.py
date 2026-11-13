"""
A ContentStore that routes each request to the appropriate backend for the course.

Course "Files" are being moved out of the legacy MongoDB/GridFS contentstore and
into ``openedx_content`` (see openedx-core ADR 0016, "Replacing Contentstore").
During the transition, each course run's files are stored in exactly one
backend, and :class:`RoutingContentStore` sends each request to the right one,
so that the rest of the platform can keep using the ``ContentStore`` API
without knowing where a given course's files are stored.

Currently, the legacy store is the only backend.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, overload

from opaque_keys.edx.keys import AssetKey, CourseKey

from .content import ContentStore, ContentstoreAsset, StaticContent, StaticContentStream


class RoutingContentStore(ContentStore):
    """
    A ``ContentStore`` that delegates each request to the backend that stores the files for the relevant course.
    """

    def __init__(self, legacy_store: ContentStore):
        """
        Initialize the router.

        ``legacy_store`` is the legacy (MongoDB/GridFS) ``ContentStore``.
        """
        super().__init__()
        self._legacy_store = legacy_store

    @property
    def legacy_store(self) -> ContentStore:
        """
        The legacy (MongoDB/GridFS) store.

        Most code should not use this directly, but it's needed for e.g. migrating assets out of the legacy store.
        """
        return self._legacy_store

    @property
    def _all_stores(self) -> list[ContentStore]:
        """All of the backends, for operations that aren't specific to one course."""
        return [self._legacy_store]

    def _store_for_course(self, course_key: CourseKey) -> ContentStore:  # pylint: disable=unused-argument
        """Get the backend that stores the files for the given course."""
        return self._legacy_store

    def _store_for_asset(self, location: AssetKey) -> ContentStore:
        return self._store_for_course(location.course_key)

    # Methods that act on a single asset

    def save(self, content: StaticContent) -> StaticContent:
        return self._store_for_asset(content.location).save(content)

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
            self._store_for_asset(location).delete(location)

    def get_attrs(self, location: AssetKey) -> dict[str, Any]:
        return self._store_for_asset(location).get_attrs(location)

    def set_attrs(self, location: AssetKey, attr_dict: dict[str, Any]) -> None:
        self._store_for_asset(location).set_attrs(location, attr_dict)

    def get_attr(self, location: AssetKey, attr: str, default: Any = None) -> Any:
        return self._store_for_asset(location).get_attr(location, attr, default)

    def set_attr(self, location: AssetKey, attr: str, value: Any = True) -> None:
        self._store_for_asset(location).set_attr(location, attr, value)

    def export(self, location: AssetKey, output_directory: str) -> None:
        self._store_for_asset(location).export(location, output_directory)

    def generate_thumbnail(
        self,
        content: StaticContent,
        tempfile_path: str | None = None,
        dimensions: tuple[int, int] | None = None,
    ) -> tuple[StaticContent | None, AssetKey]:
        # Delegate, so that each backend can decide how (or whether) to store thumbnails.
        return self._store_for_asset(content.location).generate_thumbnail(
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
        self._store_for_course(course_key).delete_all_course_assets(course_key)

    def copy_all_course_assets(self, source_course_key: CourseKey, dest_course_key: CourseKey) -> None:
        source_store = self._store_for_course(source_course_key)
        dest_store = self._store_for_course(dest_course_key)
        if source_store is not dest_store:
            raise NotImplementedError("Copying assets between different ContentStore backends is not yet supported.")
        source_store.copy_all_course_assets(source_course_key, dest_course_key)

    # Methods that act on all courses, or on the stores themselves

    def remove_redundant_content_for_courses(self) -> int:
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
