"""
Models for the contentstore shim.

``CourseAssetBackend`` records which backend stores each course's Files. Use
the functions in :mod:`.api` to read or change it.

``MediaHashShim`` holds data that the legacy ``ContentStore`` API needs but
which the ``openedx_content`` Assets API doesn't store. It's internal to
:class:`~.store.VersionedContentStoreShim`, and should not be used elsewhere.
"""

from django.db import models
from django.db.transaction import atomic
from django.utils.translation import gettext_lazy as _
from openedx_catalog.models_api import CourseRun
from openedx_content import api as content_api
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


class CourseAssetBackend(models.Model):
    """
    Which backend stores the Files (assets) of a course run.

    A course run with no row in this table is ``LEGACY``.

    While the legacy MongoDB contentstore is being replaced (see openedx-core
    ADR 0016, "Replacing Contentstore"), each course run's Files are stored in
    exactly one backend, and ``RoutingContentStore`` uses this table to send
    each request to the right one.

    Changing the status does not copy any Files between backends.

    .. no_pii:
    """

    class Status(models.TextChoices):
        """
        The possible statuses of a course run's Files.
        """

        LEGACY = "legacy", _("Legacy: Files are in the MongoDB contentstore")
        IN_PROGRESS = "in_progress", _(
            "Migration in progress: Files are read from the MongoDB contentstore, and can't be changed"
        )
        MIGRATED = "migrated", _("Migrated: Files are in openedx_content")

    course_run = models.OneToOneField(
        CourseRun,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.LEGACY,
        help_text=_(
            "Changing this does not copy any Files: the course will only see the Files in the backend that it is "
            "switched to."
        ),
    )

    class Meta:
        verbose_name = _("Course asset backend")

    def __str__(self) -> str:
        return f"{self.course_run.course_key}: {self.status}"

    def save(self, *args, **kwargs) -> None:
        """
        Save the status.

        Saving a course run as ``MIGRATED`` also creates its ``CourseContent``
        (and Learning Package), if it doesn't have one yet.
        """
        with atomic():
            super().save(*args, **kwargs)
            if self.status == self.Status.MIGRATED:
                content_api.get_or_create_course_content(self.course_run)
