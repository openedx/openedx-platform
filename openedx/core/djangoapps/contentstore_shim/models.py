"""
Models for the contentstore shim.

These hold the data that the legacy ``ContentStore`` API needs but which the
``openedx_content`` Assets API doesn't store. They are internal to
:class:`~.store.VersionedContentStoreShim`, and should not be used elsewhere.
"""

from django.db import models
from openedx_django_lib.fields import hash_field


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
