"""
Public python API for content staging
"""
from __future__ import annotations

import hashlib
import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpRequest
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import AssetKey, ContainerKey, UsageKey
from xblock.core import XBlock

from openedx.core.djangoapps.content_tagging.api import TagValuesByObjectIdDict
from openedx.core.lib.xblock_serializer.api import StaticFile, XBlockSerializer
from xmodule import block_metadata_utils
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore

from .data import (
    CLIPBOARD_PURPOSE,
    StagedContentData,
    StagedContentFileData,
    StagedContentID,
    StagedContentStatus,
    UserClipboardData,
)
from .models import StagedContent as _StagedContent
from .models import StagedContentFile as _StagedContentFile
from .models import UserClipboard as _UserClipboard
from .serializers import UserClipboardSerializer as _UserClipboardSerializer
from .tasks import delete_expired_clipboards

log = logging.getLogger(__name__)


def _save_xblock_to_staged_content(
    block: XBlock, user_id: int, purpose: str, version_num: int | None = None
) -> _StagedContent:
    """
    Generic function to save an XBlock's OLX to staged content.
    Used by both clipboard and library sync functionality.
    """
    block_data = XBlockSerializer(
        block,
        fetch_asset_data=True,
    )
    usage_key = block.usage_key

    staged_content = _save_data_to_staged_content(
        user_id=user_id,
        purpose=purpose,
        block_type=usage_key.block_type,
        olx=block_data.olx_str,
        display_name=block_metadata_utils.display_name_with_default(block),
        suggested_url_name=usage_key.block_id,
        tags=block_data.tags or {},
        copied_from=usage_key,
        version_num=(version_num or 0),
        static_files=block_data.static_files,
    )

    return staged_content


def _save_data_to_staged_content(
    user_id: int,
    purpose: str,
    block_type: str,
    olx: str,
    display_name: str,
    suggested_url_name: str,
    tags: TagValuesByObjectIdDict,
    copied_from: UsageKey | ContainerKey,
    version_num: int | None = None,
    static_files: list[StaticFile] | None = None,
) -> _StagedContent:
    """
    Save arbitrary OLX data to staged content.
    This is used by the library sync functionality to save OLX data
    that is not associated with any XBlock.
    """

    expired_ids = []
    with transaction.atomic():
        if purpose == CLIPBOARD_PURPOSE:
            # Mark all of the user's existing StagedContent rows as EXPIRED
            to_expire = _StagedContent.objects.filter(
                user_id=user_id,
                purpose=purpose,
            ).exclude(
                status=StagedContentStatus.EXPIRED,
            )
            for sc in to_expire:
                expired_ids.append(sc.id)
                sc.status = StagedContentStatus.EXPIRED
                sc.save()

        # Insert a new StagedContent row for this
        staged_content = _StagedContent.objects.create(
            user_id=user_id,
            purpose=purpose,
            status=StagedContentStatus.READY,
            block_type=block_type,
            olx=olx,
            display_name=display_name,
            suggested_url_name=suggested_url_name,
            tags=tags or {},
            version_num=(version_num or 0),
        )

    if static_files:
        # Try to copy the static files. If this fails, we still consider the overall save attempt to have succeeded,
        # because intra-course operations will still work fine, and users can manually resolve file issues.
        try:
            _save_static_assets_to_staged_content(static_files, copied_from, staged_content)
        except Exception:  # pylint: disable=broad-except
            log.exception(f"Unable to copy static files to staged content for component {copied_from}")

    if expired_ids:
        # Enqueue a (potentially slow) task to delete the old staged content
        try:
            delete_expired_clipboards.delay(expired_ids)
        except Exception:  # pylint: disable=broad-except
            log.exception(f"Unable to enqueue cleanup task for StagedContents: {','.join(str(x) for x in expired_ids)}")

    return staged_content


def _save_static_assets_to_staged_content(
    static_files: list[StaticFile], usage_key: UsageKey | ContainerKey, staged_content: _StagedContent
):
    """
    Helper method for saving static files into staged content.
    Used by both clipboard and library sync functionality.
    """
    for f in static_files:
        source_key = (
            StaticContent.get_asset_key_from_path(usage_key.context_key if usage_key else "", f.url)
            if (f.url and f.url.startswith('/')) else None
        )
        # Compute the MD5 hash and get the content:
        content: bytes | None = f.data
        if content:
            # This asset came from the XBlock's filesystem, e.g. a video block's transcript file
            source_key = usage_key
        # Check if the asset file exists. It can be absent if an XBlock contains an invalid link.
        elif source_key and (sc := contentstore().find(source_key, throw_on_not_found=False)):
            content = sc.data
            # Note that sc.content_digest has an md5_hash but it's sometimes NULL so we just compute it ourselves.
        if not content:
            continue  # Skip this file - we don't need a reference to a non-existent file.
        # Compute the md5 hash
        md5_hash = hashlib.md5(content).hexdigest()

        # Because we store clipboard files on S3, uploading really large files will be too slow. And it's wasted if
        # the copy-paste is just happening within a single course. So for files > 10MB, users must copy the files
        # manually. In the future we can consider removing this or making it configurable or filterable.
        limit = 10 * 1024 * 1024
        if content and len(content) > limit:
            content = None

        try:
            _StagedContentFile.objects.create(
                for_content=staged_content,
                filename=f.name,
                # In some cases (e.g. really large files), we don't store the data here but we still keep track of
                # the metadata. You can still use the metadata to determine if the file is already present or not,
                # and then either inform the user or find another way to import the file (e.g. if the file still
                # exists in the "Files & Uploads" contentstore of the source course, based on source_key_str).
                data_file=ContentFile(content=content, name=f.name) if content else None,
                source_key_str=str(source_key) if source_key else "",
                md5_hash=md5_hash,
            )
        except Exception:  # pylint: disable=broad-except
            log.exception(f"Unable to copy static file {f.name} to clipboard for component {usage_key}")


def save_xblock_to_user_clipboard(block: XBlock, user_id: int, version_num: int | None = None) -> UserClipboardData:
    """
    Copy an XBlock's OLX to the user's clipboard.
    """
    staged_content = _save_xblock_to_staged_content(block, user_id, CLIPBOARD_PURPOSE, version_num)
    usage_key = block.usage_key

    # Create/update the clipboard entry
    (clipboard, _created) = _UserClipboard.objects.update_or_create(
        user_id=user_id,
        defaults={
            "content": staged_content,
            "source_usage_key": usage_key,
        },
    )

    return _user_clipboard_model_to_data(clipboard)


def save_content_to_user_clipboard(
    user_id: int,
    block_type: str,
    olx: str,
    display_name: str,
    suggested_url_name: str,
    tags: TagValuesByObjectIdDict,
    copied_from: UsageKey | ContainerKey,
    version_num: int | None = None,
    static_files: list[StaticFile] | None = None,
) -> UserClipboardData:
    """
    Copy arbitrary OLX data to the user's clipboard.
    """
    staged_content = _save_data_to_staged_content(
        user_id=user_id,
        purpose=CLIPBOARD_PURPOSE,
        block_type=block_type,
        olx=olx,
        display_name=display_name,
        suggested_url_name=suggested_url_name,
        tags=tags,
        copied_from=copied_from,
        version_num=version_num,
        static_files=static_files,
    )

    # Create/update the clipboard entry
    (clipboard, _created) = _UserClipboard.objects.update_or_create(
        user_id=user_id,
        defaults={
            "content": staged_content,
            "source_usage_key": copied_from,
        },
    )

    return _user_clipboard_model_to_data(clipboard)


def stage_xblock_temporarily(
    block: XBlock, user_id: int, purpose: str, version_num: int | None = None,
) -> _StagedContent:
    """
    "Stage" an XBlock by copying it (and its associated children + static assets)
    into the content staging area. This XBlock can then be accessed and manipulated
    using any of the staged content APIs, before being deleted.
    """
    staged_content = _save_xblock_to_staged_content(block, user_id, purpose, version_num)
    return staged_content


def get_user_clipboard(user_id: int, only_ready: bool = True) -> UserClipboardData | None:
    """
    Get the details of the user's clipboard.

    By default, will only return a value if the clipboard is READY to use for
    pasting etc. Pass only_ready=False to get the clipboard data regardless.

    To get the actual OLX content, use get_staged_content_olx(content.id)
    """
    try:
        clipboard = _UserClipboard.objects.get(user_id=user_id)
    except _UserClipboard.DoesNotExist:
        # This user does not have any content on their clipboard.
        return None
    if only_ready and clipboard.content.status != StagedContentStatus.READY:
        # The clipboard content is LOADING, ERROR, or EXPIRED
        return None
    return _user_clipboard_model_to_data(clipboard)


def get_user_clipboard_json(user_id: int, request: HttpRequest | None = None):
    """
    Get the detailed status of the user's clipboard, in exactly the same format
    as returned from the
        /api/content-staging/v1/clipboard/
    REST API endpoint. This version of the API is meant for "preloading" that
    REST API endpoint so it can be embedded in a larger response sent to the
    user's browser. If you just want to get the clipboard data from python, use
    get_user_clipboard() instead, since it's fully typed.

    (request is optional; including it will make the "olx_url" absolute instead
    of relative.)
    """
    try:
        clipboard = _UserClipboard.objects.get(user_id=user_id)
    except _UserClipboard.DoesNotExist:
        # This user does not have any content on their clipboard.
        return {"content": None, "source_usage_key": "", "source_context_title": "", "source_edit_url": ""}

    serializer = _UserClipboardSerializer(
        _user_clipboard_model_to_data(clipboard),
        context={'request': request},
    )
    return serializer.data


def _staged_content_to_data(content: _StagedContent) -> StagedContentData:
    """
    Convert a StagedContent model instance to an immutable data object.
    """
    return StagedContentData(
        id=content.id,
        user_id=content.user_id,
        created=content.created,
        purpose=content.purpose,
        status=content.status,
        block_type=content.block_type,
        display_name=content.display_name,
        tags=content.tags or {},
        version_num=content.version_num,
    )


def _user_clipboard_model_to_data(clipboard: _UserClipboard) -> UserClipboardData:
    """
    Convert a UserClipboard model instance to an immutable data object.
    """
    return UserClipboardData(
        content=_staged_content_to_data(clipboard.content),
        source_usage_key=clipboard.source_usage_key,
        source_context_title=clipboard.get_source_context_title(),
    )


def get_staged_content_olx(staged_content_id: StagedContentID) -> str | None:
    """
    Get the OLX (as a string) for the given StagedContent.

    Does not check permissions!
    """
    try:
        sc = _StagedContent.objects.get(id=staged_content_id)
        return sc.olx
    except _StagedContent.DoesNotExist:
        return None


def get_staged_content_static_files(staged_content_id: StagedContentID) -> list[StagedContentFileData]:
    """
    Get the filenames and metadata for any static files used by the given staged content.

    Does not check permissions!
    """
    sc = _StagedContent.objects.get(id=staged_content_id)

    def str_to_key(source_key_str: str):
        if not source_key_str:
            return None
        try:
            return AssetKey.from_string(source_key_str)
        except InvalidKeyError:
            try:
                return UsageKey.from_string(source_key_str)
            except InvalidKeyError:
                return ContainerKey.from_string(source_key_str)

    return [
        StagedContentFileData(
            filename=f.filename,
            # For performance, we don't load data unless it's needed, so there's
            # a separate API call for that.
            data=None,
            source_key=str_to_key(f.source_key_str),
            md5_hash=f.md5_hash,
        )
        for f in sc.files.all()
    ]


def get_staged_content_static_file_data(staged_content_id: StagedContentID, filename: str) -> bytes | None:
    """
    Get the data for the static asset associated with the given staged content.

    Does not check permissions!
    """
    sc = _StagedContent.objects.get(id=staged_content_id)
    file_data_obj = sc.files.filter(filename=filename).first()
    if file_data_obj:
        return file_data_obj.data_file.open().read()
    return None
