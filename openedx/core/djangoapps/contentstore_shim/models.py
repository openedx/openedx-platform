"""
Models for the contentstore shim.

These hold the data that the legacy ``ContentStore`` API needs but which the
``openedx_content`` Assets API doesn't store. They are internal to
:class:`~.store.VersionedContentStoreShim`, and should not be used elsewhere.
"""

from django.db import models
from openedx_content.api import ASSET_PATH_MAX_LENGTH
from openedx_content.models_api import Asset, LearningPackage
from openedx_django_lib.fields import case_sensitive_char_field, hash_field


class AssetKeyName(models.Model):
    """
    The name used in the legacy ``AssetKey`` of each Asset managed by the shim.

    The legacy contentstore identifies each file in a course by an ``AssetKey``
    like ``asset-v1:Org+Course+Run+type@asset+block@images_photo.png``. The name
    part of the key (``images_photo.png``) is derived from the file's path
    (``images/photo.png``) by replacing slashes with underscores and cleaning
    out other special characters (see ``StaticContent.compute_location()``).
    That conversion is lossy: ``images/photo.png`` and ``images_photo.png``
    have the same asset key name.

    ``Asset.path`` holds the real path of the file, so this table is needed to
    find the Asset for a given ``AssetKey``. The shim only works with Assets
    that have a row in this table.

    Beware of the naming clash: the asset key name is what opaque-keys calls
    ``AssetKey.path`` (a.k.a. ``AssetLocator.block_id``), e.g.
    ``images_photo.png``, which is not the same as ``Asset.path``, e.g.
    ``images/photo.png``.

    .. no_pii:
    """

    asset = models.OneToOneField(
        Asset,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="contentstore_shim_key",
    )
    learning_package = models.ForeignKey(
        LearningPackage,
        on_delete=models.CASCADE,
        related_name="+",
    )
    """The Learning Package that holds the Asset. This is redundant with
    ``asset.learning_package``, but having it here lets us enforce that each
    asset key name is unique within its Learning Package."""

    # Each asset key name is derived from an Asset.path in a way that never makes it longer (or is the path itself),
    # so the maximum Asset.path length is long enough.
    asset_key_name = case_sensitive_char_field(max_length=ASSET_PATH_MAX_LENGTH, blank=False)
    """
    The name part (``AssetKey.path``) of the legacy ``AssetKey``, e.g.
    ``images_photo.png``. Case-sensitive, like ``AssetKey``s.
    """

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["learning_package", "asset_key_name"],
                name="contentstore_shim_uniq_asset_key_name",
            ),
        ]

    def __str__(self) -> str:
        return self.asset_key_name


class MediaHashShim(models.Model):
    """
    This table maps newer BLAKE2b hash digests to legacy MD5 hashes.

    The legacy ``ContentStore`` API provides an MD5 hash of each file
    (``StaticContent.content_digest``), which is used to build versioned asset
    URLs and to detect whether a file being pasted is the same as an existing
    file.

    However, ``openedx_content`` identifies data using a different hash
    algorithm (``Media.hash_digest``). So in order to emulate the legacy API, we
    use this table to track the MD5 hash of every Media object referenced.

    Since both hashes are computed only from the data, the mapping between them
    never goes out of date, and is the same for all copies of the same data, in
    any Learning Package and with any media type.

    .. no_pii:
    """

    hash_digest = hash_field(primary_key=True)
    """``Media.hash_digest`` (BLAKE2b) of the data"""

    md5 = models.CharField(max_length=32, blank=False, null=False, editable=False)
    """Hex-encoded MD5 hash of the data"""

    def __str__(self) -> str:
        return f"{self.hash_digest} -> {self.md5}"
