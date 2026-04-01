"""
Models for content staging (and clipboard)
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import ContainerKey, LearningContextKey, UsageKey
from openedx_django_lib.fields import MultiCollationTextField, case_insensitive_char_field

from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none

from .data import CLIPBOARD_PURPOSE, StagedContentStatus

log = logging.getLogger(__name__)

User = get_user_model()

CASE_SENSITIVE_COLLATIONS = {
    "sqlite": "BINARY",
    "mysql": "utf8mb4_bin",
}


class StagedContent(models.Model):
    """
    Each StagedContent instance represents a "piece" of content (e.g. a single
    XBlock, or a single Unit, or a single Subsection of a course) that is not
    currently part of any course or library, but which is available to be copied
    into a course or library.

    Use as a clipboard: for any given user, the most recent row with
    purpose=CLIPBOARD is the "current" clipboard content. But it can only be
    pasted if its status is READY.
    """

    class Meta:
        verbose_name_plural = _("Staged Content")

    id = models.AutoField(primary_key=True)
    # The user that created and owns this staged content. Only this user can read it.
    user = models.ForeignKey(User, null=False, on_delete=models.CASCADE)
    created = models.DateTimeField(null=False, auto_now_add=True)
    # What this StagedContent is for (e.g. "clipboard" for clipboard)
    purpose = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=StagedContentStatus.choices)

    block_type = models.CharField(
        max_length=100,
        help_text=_("""
            What type of content is staged. Only OLX content is supported, and
            this field must be the same as the root tag of the OLX.
            e.g. "video" if a video is staged, or "vertical" for a unit.
        """),
    )
    olx = MultiCollationTextField(null=False, blank=False, db_collations=CASE_SENSITIVE_COLLATIONS)
    # The display name of whatever item is staged here, i.e. the root XBlock.
    display_name = case_insensitive_char_field(max_length=768)
    # A _suggested_ URL name to use for this content. Since this suggestion may already be in use, it's fine to generate
    # a new url_name instead.
    suggested_url_name = models.CharField(max_length=1024)
    # If applicable, an int >=1 indicating the version of copied content. If not applicable, zero (default).
    version_num = models.PositiveIntegerField(default=0)

    # Tags applied to the original source block(s) will be copied to the new block(s) on paste.
    tags: models.JSONField[dict | None, dict | None] = models.JSONField(
        null=True, help_text=_("Content tags applied to these blocks")
    )

    @property
    def olx_filename(self) -> str:
        """ Get a filename that can be used for the OLX content of this staged content """
        return f"{self.suggested_url_name}.xml"

    def __str__(self):
        """ String representation of this instance """
        return f'Staged {self.block_type} block "{self.display_name}" ({self.status})'


class StagedContentFile(models.Model):
    """
    A data file ("Static Asset") associated with some StagedContent.

    These usually come from a course's Files & Uploads page, but can also come
    from per-xblock file storage (e.g. video transcripts or images used in
    v2 content libraries).
    """
    for_content = models.ForeignKey(StagedContent, on_delete=models.CASCADE, related_name="files")
    filename = models.CharField(max_length=255, blank=False)
    # Everything below is optional:
    data_file = models.FileField(upload_to="staged-content-temp/", blank=True)
    # If this asset came from Files & Uploads in a course, this is an AssetKey
    # as a string. If this asset came from an XBlock's filesystem, this is the
    # UsageKey of the XBlock.
    source_key_str = models.CharField(max_length=255, blank=True)
    md5_hash = models.CharField(max_length=32, blank=True)


class UserClipboard(models.Model):
    """
    Each user has a clipboard that can hold one item at a time, where an item
    is some OLX content that can be used in a course, such as an XBlock, a Unit,
    or a Subsection.
    """
    # The user that copied something. Clipboards are user-specific and
    # previously copied items are not kept.
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    content = models.ForeignKey(StagedContent, on_delete=models.CASCADE)
    _source_usage_key = models.CharField(
        max_length=255,
        help_text=_("Original usage key/ID of the thing that is in the clipboard."),
    )

    @property
    def source_usage_key(self) -> UsageKey | ContainerKey:
        """ Get the original usage key of the object that is in the clipboard"""
        try:
            return UsageKey.from_string(self._source_usage_key)
        except InvalidKeyError:
            try:
                return ContainerKey.from_string(self._source_usage_key)
            except InvalidKeyError as e:
                raise ValidationError(f"Invalid source_usage_key: {self._source_usage_key}") from e

    @source_usage_key.setter
    def source_usage_key(self, value: UsageKey | ContainerKey):
        """ Set the original usage key of the object that is in the clipboard """
        if not isinstance(value, (UsageKey, ContainerKey)):
            raise ValidationError("source_usage_key must be a UsageKey or ContainerKey.")
        self._source_usage_key = str(value)

    @property
    def source_context_key(self) -> LearningContextKey:
        """ Get the context (course/library) that this was copied from """
        return self.source_usage_key.context_key

    def get_source_context_title(self) -> str:
        """ Get the title of the source context, if any """
        if self.source_context_key.is_course:
            course_overview = get_course_overview_or_none(self.source_context_key)
            if course_overview:
                return course_overview.display_name_with_default
        # Just return the ID as the name, if it's empty or is not a course.
        return str(self.source_context_key)

    def clean(self):
        """ Check that this model is being used correctly. """
        if self.user.id != self.content.user.id:
            raise ValidationError("User ID mismatch.")
        if self.content.purpose != CLIPBOARD_PURPOSE:
            raise ValidationError(
                f"StagedContent.purpose must be '{CLIPBOARD_PURPOSE}' to use it as clipboard content."
            )

    def save(self, *args, **kwargs):
        """ Save this model instance """
        # Enforce checks on save:
        self.full_clean()
        return super().save(*args, **kwargs)
