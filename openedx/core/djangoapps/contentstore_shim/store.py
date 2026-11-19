"""
A ``ContentStore`` that stores course "Files" as ``openedx_content`` Assets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from io import BytesIO
from typing import IO, Any, Literal, overload

import crum
from django.db.models import BooleanField, Case, CharField, F, QuerySet, Value, When
from django.db.models.functions import Concat
from django.db.transaction import atomic
from fs.osfs import OSFS
from opaque_keys.edx.keys import AssetKey, CourseKey
from openedx_content import api as content_api
from openedx_content.api import AssetPermissions
from openedx_content.models_api import Asset, AssetVersion, Draft, LearningPackage, Media, MediaType

from xmodule.contentstore.content import ContentStore, ContentstoreAsset, StaticContent, StaticContentStream
from xmodule.contentstore.routing import copy_course_assets_between_stores
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.django import ASSET_IGNORE_REGEX
from xmodule.util.misc import escape_invalid_characters
from xmodule.util.sandboxing import course_code_library_asset_name

from .models import MediaHashShim
from .mongo_query import QueryField, mongo_filter_to_q, mongo_sort_to_order_by

log = logging.getLogger(__name__)

# Relations from Asset to its published version and that version's Media
_PUBLISHED_VERSION = "publishable_entity__published__version"
_PUBLISHED_MEDIA = f"{_PUBLISHED_VERSION}__assetversion__media"

# The fields of ContentstoreAsset that can be used to filter and sort get_all_content_for_course(), and the
# annotations on the queryset from _listing_queryset() that hold their values.
_LISTING_FIELDS = {
    "displayname": QueryField("shim_displayname", case_insensitive_sort=True),
    "contentType": QueryField("shim_content_type"),
    "uploadDate": QueryField("shim_upload_date"),
    "length": QueryField("shim_length"),
    "locked": QueryField("shim_locked"),
}

# Attributes which can't be changed using set_attrs(). (Same as MongoContentStore.)
_PROTECTED_ATTRS = {"_id", "md5", "uploadDate", "length"}
# Attributes which can be changed using set_attrs(). Unlike MongoContentStore, we can't store arbitrary attributes.
_WRITABLE_ATTRS = {"locked", "displayname"}

# Keys of ContentstoreAsset which are not included in the policy file written by export_all_for_course().
# (Same as MongoContentStore.)
_EXCLUDED_POLICY_KEYS = {"uploadDate", "length", "asset_key"}

_FALLBACK_MEDIA_TYPE = "application/octet-stream"
_MEDIA_TYPE_RE = re.compile(r"[\w.-]+/[\w.-]+(\+[\w.-]+)?")


class VersionedContentStoreShim(ContentStore):
    """
    A ``ContentStore`` that stores each course's Files as Assets in the course's
    ``openedx_content`` Learning Package.

    This implements the legacy ``ContentStore`` API, which has no concept of
    versioning, drafts, or publishing, so:

    * Every change is published immediately.
    * Reads return the published version of each asset.
    * Deleting an asset soft-deletes it (and publishes the deletion), so its
      history is kept, and saving an asset with the same ``AssetKey`` later will
      restore it with a new version.

    Each asset's legacy ``AssetKey`` name (``AssetKey.path``, a.k.a.
    ``AssetLocator.block_id``, e.g. ``images_photo.png``) is stored in
    ``Asset.legacy_flat_path``, while ``Asset.path`` holds the file's real path
    (e.g. ``images/photo.png``) when it's known. The MD5 hash of each asset's
    data is stored in :class:`~.models.MediaHashShim`. The shim ignores any
    Assets that don't have a ``legacy_flat_path`` (e.g. which were created
    without using the shim), until it saves an asset with the same path.

    Differences from ``MongoContentStore``:

    * Thumbnails are not stored, and there are never any ``thumbnail``-type
      assets. For an image, ``generate_thumbnail()`` returns the image's own
      ``AssetKey`` as the thumbnail location, so that callers which use that
      location without checking whether a thumbnail was created get the full
      image.
    * Only the ``locked`` and ``displayname`` attributes can be changed with
      ``set_attr()``/``set_attrs()``.
    * ``locked`` assets are restricted to enrolled users
      (``AssetPermissions.ENROLLED``), except for the course's Python code
      library (``python_lib.zip``), which is always restricted to staff
      (``AssetPermissions.STAFF``), and can't be locked. It's reported as not
      ``locked``, which is its default in the legacy store, so that the
      contentserver applies its own rules for ``python_lib.zip`` (which allow
      course staff, who may not be enrolled) instead of requiring enrollment.
    * ``get_all_content_for_course()`` only supports the subset of MongoDB query
      syntax described in :mod:`.mongo_query`, and ignores the ``user_language``
      filter param.
    * The legacy ``import_path`` of an asset is only kept if it's a valid
      ``Asset.path`` that corresponds to the ``AssetKey``. Otherwise, the
      ``AssetKey`` name is used as the path, and ``import_path`` will be
      ``None``.
    """

    # Methods that act on a single asset

    def save(self, content: StaticContent) -> StaticContent:
        location = content.location
        if location.asset_type != "asset":
            raise ValueError(f"VersionedContentStoreShim can only store 'asset' content, not {location}")
        name = location.path  # e.g. "photo.png" or "images_photo.png"
        data = _get_content_bytes(content)
        permissions = self._get_permissions(name, locked=getattr(content, "locked", False))
        now = datetime.now(tz=UTC)
        user_id = _get_current_user_id()

        with atomic():
            learning_package_id = self._get_learning_package_id(location.course_key, create=True)
            assert learning_package_id is not None
            media = content_api.get_or_create_file_media(
                learning_package_id, _get_media_type(content.content_type).id, data=data, created=now
            )
            asset = self._get_asset(learning_package_id, name)
            if asset is None:
                asset = self._create_asset(
                    learning_package_id,
                    location,
                    import_path=content.import_path,
                    title=content.name,
                    media=media,
                    permissions=permissions,
                    created=now,
                    created_by=user_id,
                )
            else:
                content_api.update_asset(asset, permissions=permissions)
                content_api.create_next_asset_version(
                    asset, media=media, title=content.name, created=now, created_by=user_id
                )
            self._publish(learning_package_id, [asset.id], published_at=now, published_by=user_id)
            _save_md5(media.hash_digest, hashlib.md5(data, usedforsecurity=False).hexdigest())

        return content

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
        asset, version = self._get_published_asset(location)
        if asset is None or version is None:
            if throw_on_not_found:
                raise NotFoundError(location)
            return None

        media = version.media
        kwargs: dict[str, Any] = {
            "last_modified_at": version.created,
            "thumbnail_location": None,
            "import_path": _get_import_path(asset, location.path),
            "length": media.size,
            "locked": asset.permissions == AssetPermissions.ENROLLED,
            "content_digest": _get_md5(media),
        }
        if as_stream:
            return StaticContentStream(location, version.title, media.mime_type, _open_media(media), **kwargs)
        with _open_media(media) as f:
            data = f.read()
        return StaticContent(location, version.title, media.mime_type, data, **kwargs)

    def delete(self, location: AssetKey) -> None:
        if location.asset_type != "asset":
            return  # We never store anything else (e.g. thumbnails)
        learning_package_id = self._get_learning_package_id(location.course_key)
        if learning_package_id is None:
            return
        asset = self._get_asset(learning_package_id, location.path)
        if asset is None:
            return
        self._delete_assets(learning_package_id, [asset])

    def get_attrs(self, location: AssetKey) -> dict[str, Any]:
        asset, version = self._get_published_asset(location)
        if asset is None or version is None:
            raise NotFoundError(location)
        media = version.media
        info: ContentstoreAsset = {
            "asset_key": location,
            "displayname": version.title,
            "contentType": media.mime_type,
            "uploadDate": version.created,
            "length": media.size,
            "locked": asset.permissions == AssetPermissions.ENROLLED,
            "thumbnail_location": None,
            "import_path": _get_import_path(asset, location.path),
        }
        return dict(info)

    def set_attrs(self, location: AssetKey, attr_dict: dict[str, Any]) -> None:
        for attr in attr_dict:
            if attr in _PROTECTED_ATTRS:
                raise AttributeError(f"{attr} is a protected attribute.")
            if attr not in _WRITABLE_ATTRS:
                raise NotImplementedError(f"VersionedContentStoreShim does not support setting the {attr!r} attribute.")
        asset, version = self._get_published_asset(location)
        if asset is None or version is None:
            raise NotFoundError(location)

        with atomic():
            if "locked" in attr_dict:
                permissions = self._get_permissions(location.path, locked=bool(attr_dict["locked"]))
                content_api.update_asset(asset, permissions=permissions)
            if "displayname" in attr_dict and attr_dict["displayname"] != version.title:
                now = datetime.now(tz=UTC)
                user_id = _get_current_user_id()
                content_api.create_next_asset_version(
                    asset, media=None, title=attr_dict["displayname"], created=now, created_by=user_id
                )
                self._publish(asset.learning_package_id, [asset.id], published_at=now, published_by=user_id)

    def export(self, location: AssetKey, output_directory: str) -> None:  # output_directory e.g. "/tmp/abc/static"
        asset, version = self._get_published_asset(location)
        if asset is None or version is None:
            raise NotFoundError(location)
        # Like MongoContentStore, put the file in the subdirectory it was imported from (if any), but name it using
        # its display name.
        import_path = _get_import_path(asset, location.path)  # e.g. "images/photo.png" or None
        if import_path is not None:
            output_directory = output_directory + "/" + os.path.dirname(import_path)
        os.makedirs(output_directory, exist_ok=True)
        # e.g. "photo.png" or "My Photo.png"
        export_name = escape_invalid_characters(name=version.title, invalid_char_list=["/", "\\"])
        with _open_media(version.media) as source, OSFS(output_directory).open(export_name, "wb") as dest:
            shutil.copyfileobj(source, dest)

    def generate_thumbnail(
        self,
        content: StaticContent,
        tempfile_path: str | None = None,
        dimensions: tuple[int, int] | None = None,
    ) -> tuple[StaticContent | None, AssetKey]:
        """
        Thumbnails are not supported, so this never creates a thumbnail.

        It still returns a location, as callers may use it without checking
        whether a thumbnail was created. For an image, it's the location of the
        image itself, e.g. so that ``create_course_image_thumbnail()`` returns
        the URL of the full image instead of one that doesn't exist. For other
        files, it's the location that a thumbnail would have.
        """
        if content.content_type is not None and content.content_type.split("/")[0] == "image":
            return None, content.location
        _name, thumbnail_location = self._get_thumbnail_name_and_location(content, dimensions)
        return None, thumbnail_location

    # Methods that act on all the assets of a course

    def get_all_content_for_course(
        self,
        course_key: CourseKey,
        start: int = 0,
        maxresults: int = -1,
        sort: Sequence[tuple[str, int]] | None = None,
        filter_params: dict[str, Any] | None = None,
    ) -> tuple[list[ContentstoreAsset], int]:
        learning_package_id = self._get_learning_package_id(course_key)
        if learning_package_id is None:
            return [], 0

        qs = self._listing_queryset(learning_package_id)
        filter_params = dict(filter_params or {})
        # MongoContentStore uses this for case-insensitive sorting by displayname. We sort case-insensitively in all
        # languages, so we don't need it. Note that unlike MongoContentStore, we don't modify the caller's dict.
        filter_params.pop("user_language", None)
        if filter_params:
            qs = qs.filter(mongo_filter_to_q(filter_params, _LISTING_FIELDS))
        count = qs.count()

        # Like MongoContentStore, the default order is the order in which the assets were last saved. "path" breaks
        # ties, so that the order is stable for pagination.
        order_by = mongo_sort_to_order_by(sort, _LISTING_FIELDS) or [F("shim_upload_date").asc()]
        qs = qs.order_by(*order_by, "path")
        start = max(start, 0)
        rows = qs.values(  # type: ignore[misc]  # (mypy can't see the annotations from _listing_queryset)
            "path",
            "legacy_flat_path",
            "shim_displayname",
            "shim_content_type",
            "shim_upload_date",
            "shim_length",
            "shim_locked",
        )
        rows = rows[start : start + maxresults] if maxresults > 0 else rows[start:]

        assets: list[ContentstoreAsset] = []
        for row in rows:
            name = row["legacy_flat_path"]  # e.g. "photo.png" or "images_photo.png"
            assets.append({
                "asset_key": course_key.make_asset_key("asset", name),
                "displayname": row["shim_displayname"],
                "contentType": row["shim_content_type"],
                "uploadDate": row["shim_upload_date"],
                "length": row["shim_length"],
                "locked": row["shim_locked"],
                "thumbnail_location": None,
                "import_path": row["path"] if row["path"] != name else None,
            })
        return assets, count

    def get_all_content_thumbnails_for_course(self, course_key: CourseKey) -> list[ContentstoreAsset]:
        return []  # Thumbnails are not supported.

    def export_all_for_course(self, course_key: CourseKey, output_directory: str, assets_policy_file: str) -> None:
        policy: dict[str, dict[str, Any]] = {}
        assets, _count = self.get_all_content_for_course(course_key)
        for asset in assets:
            self.export(asset["asset_key"], output_directory)
            policy[asset["asset_key"].path] = {
                key: value for key, value in asset.items() if key not in _EXCLUDED_POLICY_KEYS
            }
        with open(assets_policy_file, "w") as f:
            json.dump(policy, f, sort_keys=True, indent=4)

    def delete_all_course_assets(self, course_key: CourseKey) -> None:
        learning_package_id = self._get_learning_package_id(course_key)
        if learning_package_id is None:
            return
        # _delete_assets() skips any that are already deleted
        assets = content_api.get_assets(learning_package_id).filter(legacy_flat_path__isnull=False)
        self._delete_assets(learning_package_id, assets)

    def copy_all_course_assets(self, source_course_key: CourseKey, dest_course_key: CourseKey) -> None:
        copy_course_assets_between_stores(self, source_course_key, self, dest_course_key)

    # Methods that act on all courses

    def remove_redundant_content_for_courses(self) -> int:
        """
        Delete redundant files from all courses, i.e. Assets whose legacy
        ``AssetKey`` name matches ``ASSET_IGNORE_REGEX`` (by default,
        ``.DS_Store``, macOS ``._*`` metadata files, and ``*~`` editor backups).
        These usually come from importing a course that was zipped on a Mac.

        Like ``MongoContentStore``, this matches the flat asset key name
        (``Asset.legacy_flat_path``), not the real path. So e.g.
        ``images/._photo.png`` (``images_._photo.png``) is not deleted.

        Unlike ``MongoContentStore``, which deletes the files permanently, this
        soft-deletes the Assets and publishes the deletions, like ``delete()``.

        Returns the number of assets deleted (excluding any that were already
        deleted).
        """
        # There's no API for querying the Assets of every Learning Package at once, so use the model directly.
        learning_package_ids = (
            Asset.objects
            .filter(legacy_flat_path__regex=ASSET_IGNORE_REGEX)
            .order_by("learning_package_id")
            .values_list("learning_package_id", flat=True)
            .distinct()
        )
        return sum(
            self._delete_assets(
                learning_package_id,
                # _delete_assets() skips any that are already deleted
                content_api.get_assets(learning_package_id).filter(
                    legacy_flat_path__regex=ASSET_IGNORE_REGEX,
                ),
            )
            for learning_package_id in learning_package_ids
        )

    # Helper methods

    @staticmethod
    def _get_learning_package_id(course_key: CourseKey, *, create: bool = False) -> LearningPackage.ID | None:
        """
        Get the ID of the Learning Package that holds the given course's Files.

        If the course doesn't have one yet, returns ``None``, or creates one if
        ``create`` is True.
        """
        if create:
            course_content, _created = content_api.get_or_create_course_content(course_key)
            return course_content.learning_package_id
        try:
            return content_api.get_learning_package_for_course_run(course_key).id
        except LearningPackage.DoesNotExist:
            return None

    @staticmethod
    def _get_asset(learning_package_id: LearningPackage.ID, name: str) -> Asset | None:
        """
        Get the Asset with the given legacy ``AssetKey`` name, whether or not
        it's deleted.

        ``name`` is e.g. ``"photo.png"``, or ``"images_photo.png"`` for a file
        that was imported as ``images/photo.png``.
        """
        # This includes deleted assets.
        try:
            return content_api.get_asset_by_legacy_flat_path(learning_package_id, name)
        except Asset.DoesNotExist:
            return None

    def _get_published_asset(self, location: AssetKey) -> tuple[Asset | None, AssetVersion | None]:
        """
        Get the Asset for the given ``AssetKey``, and its published version.

        Returns ``(None, None)`` if there's no such asset, or ``(asset, None)``
        if it's been deleted.
        """
        if location.asset_type != "asset":
            return None, None
        learning_package_id = self._get_learning_package_id(location.course_key)
        if learning_package_id is None:
            return None, None
        asset = self._get_asset(learning_package_id, location.path)
        if asset is None:
            return None, None
        return asset, asset.versioning.published

    @staticmethod
    def _create_asset(
        learning_package_id: LearningPackage.ID,
        location: AssetKey,
        *,
        import_path: str | None,
        title: str,
        media: Media,
        permissions: AssetPermissions,
        created: datetime,
        created_by: int | None,
    ) -> Asset:
        """
        Create the Asset for a new ``AssetKey`` (or restore a deleted one).

        The Asset's path is the ``import_path`` if possible, so that the Asset
        has the file's real path, or else the ``AssetKey`` name.

        ``import_path`` is the path that the file was imported from, e.g.
        ``"images/photo.png"``, or ``None`` if it was uploaded (e.g. to the
        "Files" page).
        """
        name = location.path  # e.g. "photo.png" or "images_photo.png"
        path = name
        if import_path and import_path != name:  # e.g. "images/photo.png"
            if StaticContent.compute_location(location.course_key, import_path).path != name:
                log.warning("Ignoring import_path %r which does not match the asset key %s", import_path, location)
            elif not content_api.is_valid_asset_path(import_path):
                log.info("Ignoring import_path %r which is not a valid asset path, for %s", import_path, location)
            else:
                path = import_path
        if not content_api.is_valid_asset_path(path):
            raise ValueError(f"Cannot store {location}: its name is not a valid asset path")

        try:
            asset, _version = content_api.create_asset_and_version(
                learning_package_id,
                path,
                title=title,
                media=media,
                permissions=permissions,
                created=created,
                created_by=created_by,
                legacy_flat_path=name,
            )
        except content_api.AssetAlreadyExists:
            # There's an Asset with this path that wasn't created by the shim (or else we would have found it using its
            # legacy_flat_path). Since the asset key name is derived from the path, it must be the Asset for this
            # AssetKey, so adopt it.
            asset = content_api.get_asset_by_path(learning_package_id, path)
            content_api.update_asset(asset, permissions=permissions, legacy_flat_path=name)
            content_api.create_next_asset_version(
                asset, media=media, title=title, created=created, created_by=created_by
            )
        return asset

    @staticmethod
    def _get_permissions(name: str, *, locked: bool) -> AssetPermissions:
        """
        Get the permissions for the asset with the given ``AssetKey`` name,
        e.g. ``"photo.png"`` or ``"python_lib.zip"``.
        """
        if name == course_code_library_asset_name():
            # The course's Python code library is used by the code sandbox, and should never be served to learners.
            return AssetPermissions.STAFF
        return AssetPermissions.ENROLLED if locked else AssetPermissions.PUBLIC

    @staticmethod
    def _publish(
        learning_package_id: LearningPackage.ID,
        asset_ids: Iterable[Asset.ID],
        *,
        published_at: datetime | None = None,
        published_by: int | None = None,
    ) -> None:
        """Publish the current drafts of the given assets."""
        # We can't use get_all_drafts() here, because it excludes soft-deleted drafts (Drafts with a NULL version),
        # so deletions would never be published. TODO: switch to content_api.publish_entities() once it's available:
        # https://github.com/openedx/openedx-core/pull/947
        content_api.publish_from_drafts(
            learning_package_id,
            Draft.objects.filter(entity_id__in=list(asset_ids)),
            published_at=published_at,
            published_by=published_by,
        )

    def _delete_assets(self, learning_package_id: LearningPackage.ID, assets: Iterable[Asset]) -> int:
        """
        Soft-delete the given assets and publish the deletions.

        Returns the number of assets deleted (excluding those that were already
        deleted).
        """
        asset_ids = [
            asset.id for asset in assets if asset.versioning.draft is not None or asset.versioning.published is not None
        ]
        if not asset_ids:
            return 0
        now = datetime.now(tz=UTC)
        user_id = _get_current_user_id()
        with atomic():
            with content_api.bulk_draft_changes_for(learning_package_id, changed_by=user_id, changed_at=now):
                for asset_id in asset_ids:
                    content_api.soft_delete_draft(asset_id, deleted_by=user_id)
            self._publish(learning_package_id, asset_ids, published_at=now, published_by=user_id)
        return len(asset_ids)

    @staticmethod
    def _listing_queryset(learning_package_id: LearningPackage.ID) -> QuerySet[Asset]:
        """
        Get a queryset of the course's (published, non-deleted) assets,
        annotated with the fields that ``get_all_content_for_course()`` returns
        and can filter/sort on.
        """
        media_type = f"{_PUBLISHED_MEDIA}__media_type"
        return content_api.get_assets(learning_package_id, published=True).filter(
            legacy_flat_path__isnull=False,
        ).annotate(
            shim_displayname=F(f"{_PUBLISHED_VERSION}__title"),
            shim_upload_date=F(f"{_PUBLISHED_VERSION}__created"),
            shim_length=F(f"{_PUBLISHED_MEDIA}__size"),
            # Same as str(MediaType)
            shim_content_type=Concat(
                f"{media_type}__type",
                Value("/"),
                f"{media_type}__sub_type",
                Case(
                    When(**{f"{media_type}__suffix": ""}, then=Value("")),
                    default=Concat(Value("+"), f"{media_type}__suffix", output_field=CharField()),
                ),
                output_field=CharField(),
            ),
            shim_locked=Case(
                When(permissions=AssetPermissions.ENROLLED, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )


def _get_current_user_id() -> int | None:
    user = crum.get_current_user()
    if user is not None and user.is_authenticated:
        return user.id
    return None


def _get_content_bytes(content: StaticContent) -> bytes:
    """
    Get the data of the given ``StaticContent`` as bytes.

    Like ``MongoContentStore``, this accepts data in the various forms that
    callers provide.
    """
    if isinstance(content, StaticContentStream):
        return b"".join(content.stream_data())
    data = content.data
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    if hasattr(data, "read"):  # A file-like object, e.g. BytesIO
        return data.read()
    if data is None:
        raise ValueError(f"No data provided for {content.location}")
    return b"".join(data)  # An iterable of chunks, e.g. UploadedFile.chunks()


def _get_media_type(content_type: str | None) -> MediaType:
    """
    Get the openedx_content ``MediaType`` for the given ``content_type`` (e.g.
    ``"image/png"`` or ``"text/html; charset=utf-8"``). Any parameters (e.g.
    ``; charset=utf-8``) are dropped, and unrecognized content types are stored
    as ``application/octet-stream``.
    """
    media_type = (content_type or "").split(";")[0].strip()
    if not _MEDIA_TYPE_RE.fullmatch(media_type):
        media_type = _FALLBACK_MEDIA_TYPE
    return content_api.get_or_create_media_type(media_type)


def _get_import_path(asset: Asset, name: str) -> str | None:
    """
    Get the legacy ``import_path`` of the given asset. That's its path, unless
    it's the same as its ``AssetKey`` name (in which case, it was not imported
    from a subdirectory, or its import path was not valid).

    For example, if ``asset.path`` is ``"images/photo.png"`` and ``name`` is
    ``"images_photo.png"``, this returns ``"images/photo.png"``. If both are
    ``"photo.png"``, it returns ``None``.
    """
    return asset.path if asset.path != name else None


def _open_media(media: Media) -> IO[bytes]:
    """
    Open the data of the given ``Media`` for reading.
    """
    if media.has_file:
        return media.read_file()
    # Media created by the shim always have a file, but Assets adopted by the shim may have text-only Media.
    return BytesIO((media.text or "").encode("utf-8"))


def _get_md5(media: Media) -> str:
    """
    Get the MD5 hash of the data in the given ``Media``, computing it if it
    hasn't been stored yet.
    """
    try:
        return MediaHashShim.objects.get(hash_digest=media.hash_digest).md5
    except MediaHashShim.DoesNotExist:
        pass
    md5 = hashlib.md5(usedforsecurity=False)
    with _open_media(media) as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5.update(chunk)
    return _save_md5(media.hash_digest, md5.hexdigest())


def _save_md5(hash_digest: str, md5: str) -> str:
    """
    Store the MD5 hash of the data with the given ``Media.hash_digest``. Returns
    the stored MD5 hash.

    ``hash_digest`` is 40 hex digits (BLAKE2b), and ``md5`` is 32 hex digits,
    e.g. ``"8d777f385d3dfec8815d20f7496026dc"``.
    """
    row, _created = MediaHashShim.objects.get_or_create(hash_digest=hash_digest, defaults={"md5": md5})
    return row.md5
