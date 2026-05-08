"""
========================
Content Libraries Models
========================

This module contains the models for new Content Libraries.
"""
from __future__ import annotations

import logging
import warnings
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_content.models_api import LearningPackage
from organizations.models import Organization  # pylint: disable=wrong-import-order

from openedx.core.djangoapps.content_libraries.constants import ALL_RIGHTS_RESERVED, LICENSE_OPTIONS

log = logging.getLogger(__name__)


warnings.warn(  # noqa: B028
    (
        "ContentLibraryPermission model and related content library authorization "
        "APIs are deprecated. See https://github.com/openedx/openedx-platform/issues/37409."
    ),
    DeprecationWarning
)

User = get_user_model()


class ContentLibraryManager(models.Manager["ContentLibrary"]):
    """
    Custom manager for ContentLibrary class.
    """
    def get_by_key(self, library_key) -> "ContentLibrary":  # noqa: UP037
        """
        Get the ContentLibrary for the given LibraryLocatorV2 key.
        """
        assert isinstance(library_key, LibraryLocatorV2)
        return self.select_related('learning_package') \
                   .get(org__short_name=library_key.org, slug=library_key.slug)


class ContentLibrary(models.Model):
    """
    A Content Library is a collection of content (XBlocks and/or static assets)

    All actual content is stored in openedx_content, and any data that we'd want to
    transfer to another instance if this library were exported and then
    re-imported on another Open edX instance should be kept in openedx_content. This
    model in Studio should only be used to track settings specific to this Open
    edX instance, like who has permission to edit this content library.

    .. no_pii:
    """
    objects: ClassVar[ContentLibraryManager] = ContentLibraryManager()

    id = models.AutoField(primary_key=True)
    # Every Library is uniquely and permanently identified by an 'org' and a
    # 'slug' that are set during creation/import. Both will appear in the
    # library's opaque key:
    # e.g. "lib:org:slug" is the opaque key for a library.
    org = models.ForeignKey(Organization, on_delete=models.PROTECT, null=False)
    slug = models.SlugField(allow_unicode=True)

    license = models.CharField(max_length=25, default=ALL_RIGHTS_RESERVED, choices=LICENSE_OPTIONS)
    learning_package = models.OneToOneField(
        LearningPackage,
        # We can't delete the LearningPackage that holds a Library's content
        # unless we're deleting both at the same time.
        on_delete=models.RESTRICT,
        # This is nullable mostly for backwards compatibility, though it should
        # be possible to have the abstract notion of a Library with no actual
        # content in it yet.
        null=True,
        default=None,
    )

    # How is this library going to be used?
    allow_public_learning = models.BooleanField(
        default=False,
        help_text=("""
            Allow any user (even unregistered users) to view and interact with
            content in this library (in the LMS; not in Studio). If this is not
            enabled, then the content in this library is not directly accessible
            in the LMS, and learners will only ever see this content if it is
            explicitly added to a course. If in doubt, leave this unchecked.
        """),
    )
    allow_public_read = models.BooleanField(
        default=False,
        help_text=("""
            Allow any user with Studio access to view this library's content in
            Studio, use it in their courses, and copy content out of this
            library. If in doubt, leave this unchecked.
        """),
    )

    class Meta:
        verbose_name_plural = "Content Libraries"
        unique_together = ("org", "slug")

    @property
    def library_key(self):
        """
        Get the LibraryLocatorV2 opaque key for this library
        """
        return LibraryLocatorV2(org=self.org.short_name, slug=self.slug)

    def __str__(self):  # noqa: DJ012
        return f"ContentLibrary ({str(self.library_key)})"


class ContentLibraryPermission(models.Model):
    """
    Row recording permissions for a content library

    Deprecated https://github.com/openedx/openedx-platform/issues/37409.

    .. no_pii:
    """
    library = models.ForeignKey(ContentLibrary, on_delete=models.CASCADE, related_name="permission_grants")
    # One of the following must be set (but not both):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.CASCADE)
    # What level of access is granted to the above user or group:
    ADMIN_LEVEL = 'admin'
    AUTHOR_LEVEL = 'author'
    READ_LEVEL = 'read'
    ACCESS_LEVEL_CHOICES = (
        (ADMIN_LEVEL, _("Administer users and author content")),
        (AUTHOR_LEVEL, _("Author content")),
        (READ_LEVEL, _("Read-only")),
    )
    access_level = models.CharField(max_length=30, choices=ACCESS_LEVEL_CHOICES)

    class Meta:
        ordering = ('user__username', 'group__name')
        unique_together = [
            ('library', 'user'),
            ('library', 'group'),
        ]

    def save(self, *args, **kwargs):  # pylint: disable=arguments-differ, signature-differs
        """
        Validate any constraints on the model.

        We can remove this and replace it with a proper database constraint
        once we're upgraded to Django 2.2+
        """
        # if both are nonexistent or both are existing, error
        if (not self.user) == (not self.group):
            raise ValidationError(_("One and only one of 'user' and 'group' must be set."))
        return super().save(*args, **kwargs)

    def __str__(self):  # noqa: DJ012
        who = self.user.username if self.user else self.group.name
        return f"ContentLibraryPermission ({self.access_level} for {who})"
