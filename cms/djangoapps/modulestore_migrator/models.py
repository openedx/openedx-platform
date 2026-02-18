"""
Models for the modulestore migration tool.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import (
    LearningContextKeyField,
    UsageKeyField,
)
from openedx_content.models_api import (
    Collection,
    DraftChangeLog,
    DraftChangeLogRecord,
    LearningPackage,
    PublishableEntity,
)
from user_tasks.models import UserTaskStatus

from .data import CompositionLevel, RepeatHandlingStrategy

User = get_user_model()


class ModulestoreSource(models.Model):
    """
    A legacy learning context (course or library) which can be a source of a migration.

    One source can be associated with multiple (successful or unsuccessful) ModulestoreMigrations.
    If a source has been migrated multiple times, then at most one of them can be considered the
    "official" or "authoritative" migration; this is indicated by setting the `forwarded` field to
    that ModulestoreMigration object.

    Note that `forwarded` can be NULL even when 1+ migrations have happened for this source. This just
    means that none of them were authoritative. In other words, they were all "imports"/"copies" rather
    than true "migrations".

    In practice, as of Ulmo:
    * The `forwarded` field is used to decide how to update legacy library_content references.
    * When using the Libraries Migration UI in Studio, `forwarded` is always set to the first
      successful ModulestoreMigration.
    * When using the REST API directly, the default is to use the same behavior as the UI, but
      clients can also explicitly specify the `forward_source_to_target` boolean param in order to
      control whether `forwarded` is set to any given migration.
    """
    key = LearningContextKeyField(
        max_length=255,
        unique=True,
        help_text=_('Key of the content source (a course or a legacy library)'),
    )
    forwarded = models.OneToOneField(
        'modulestore_migrator.ModulestoreMigration',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_('If set, the system will forward references of this source over to the target of this migration'),
    )

    def __str__(self):
        return f"{self.key}"

    __repr__ = __str__


class ModulestoreMigration(models.Model):
    """
    Tracks the action of a user importing a Modulestore-based course or legacy library into a
    learning-core based learning package

    Notes:
    * As of Ulmo, a learning package is always associated with a v2 content library, but we
      will not bake that assumption into this model)
    * Each Migration is tied to a single UserTaskStatus, which connects it to a user and
      contains the progress of the import.
    * A single ModulestoreSource may very well have multiple ModulestoreMigrations; however,
      at most one of them with be the "authoritative" migration, as indicated by `forwarded`.
    """

    ## MIGRATION SPECIFICATION
    source = models.ForeignKey(
        ModulestoreSource,
        on_delete=models.CASCADE,
        related_name="migrations",
    )
    source_version = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_('Migrated content version, the hash of published content version'),
    )
    composition_level = models.CharField(
        max_length=255,
        choices=CompositionLevel.supported_choices(),
        default=CompositionLevel.Component.value,
        help_text=_('Maximum hierachy level at which content should be aggregated in target library'),
    )
    repeat_handling_strategy = models.CharField(
        choices=RepeatHandlingStrategy.supported_choices(),
        default=RepeatHandlingStrategy.default().value,
        max_length=24,
        help_text=_(
            "If a piece of content already exists in the content library, choose how to handle it."
        ),
    )
    preserve_url_slugs = models.BooleanField(
        default=False,
        help_text=_(
            "Should the migration preserve the location IDs of the existing blocks?"
            "If not, then new, unique human-readable IDs will be generated based on the block titles."
        ),
    )
    target = models.ForeignKey(
        LearningPackage,
        on_delete=models.CASCADE,
        help_text=_('Content will be imported into this library'),
    )
    target_collection = models.ForeignKey(
        Collection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_('Optional - Collection (within the target library) into which imported content will be grouped'),
    )

    ## MIGRATION ARTIFACTS
    task_status = models.ForeignKey(
        UserTaskStatus,
        on_delete=models.RESTRICT,
        help_text=_(
            "Tracks the status of the task which is executing this migration. "
            "In a bulk migration, the same task can be multiple migrations"
        ),
        related_name="migrations",
    )
    change_log = models.ForeignKey(
        DraftChangeLog,
        on_delete=models.SET_NULL,
        null=True,
        help_text=_("Changelog entry in the target learning package which records this migration"),
    )
    staged_content = models.OneToOneField(
        "content_staging.StagedContent",
        null=True,
        on_delete=models.SET_NULL,  # Staged content is liable to be deleted in order to save space
        help_text=_(
            "Modulestore content is processed and staged before importing it to a learning packge. "
            "We temporarily save the staged content to allow for troubleshooting of failed migrations."
        )
    )
    # Mostly used in bulk migrations. The `UserTaskStatus` represents the status of the entire bulk migration;
    # a `FAILED` status means that the entire bulk-migration has failed.
    # Each `ModulestoreMigration` saves the data of the migration of each legacy library.
    # The `is_failed` value is to keep track a failed legacy library in the bulk migration,
    # but allow continuing with the migration of the rest of the legacy libraries.
    is_failed = models.BooleanField(
        default=False,
        help_text=_(
            "is the migration failed?"
        ),
    )

    def __str__(self):
        return (
            f"{self.__class__.__name__} #{self.pk}: "
            f"{self.source.key} → {self.target_collection or self.target}"
        )

    def __repr__(self):
        return (
            f"{self.__class__.__name__}("
            f"id={self.id}, source='{self.source}',"
            f"target='{self.target_collection or self.target}')"
        )


class ModulestoreBlockSource(TimeStampedModel):
    """
    A legacy block usage (in a course or library) which can be a source of a block migration.

    The semantics of `forwarded` directly mirror those of `ModulestoreSource.forwarded`. Please see
    that class's docstring for details.
    """
    overall_source = models.ForeignKey(
        ModulestoreSource,
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    key = UsageKeyField(
        max_length=255,
        unique=True,
        help_text=_('Original usage key of the XBlock that has been imported.'),
    )
    forwarded = models.OneToOneField(
        'modulestore_migrator.ModulestoreBlockMigration',
        null=True,
        on_delete=models.SET_NULL,
        help_text=_(
            'If set, the system will forward references of this block source over to the '
            'target of this block migration'
        ),
    )

    def __str__(self):
        return f"{self.__class__.__name__}('{self.key}')"

    __repr__ = __str__


class ModulestoreBlockMigration(TimeStampedModel):
    """
    The migration of a single legacy block into a learning package.

    Is always tied to a greater overall ModulestoreMigration.

    Note:
    * A single ModulestoreBlockSource may very well have multiple ModulestoreBlockMigrations; however,
      at most one of them with be the "authoritative" migration, as indicated by `forwarded`.
      This will coincide with the `overall_migration` being pointed to by `forwarded` as well.
    """
    overall_migration = models.ForeignKey(
        ModulestoreMigration,
        on_delete=models.CASCADE,
        related_name="block_migrations",
    )
    source = models.ForeignKey(
        ModulestoreBlockSource,
        on_delete=models.CASCADE,
    )
    target = models.ForeignKey(
        PublishableEntity,
        on_delete=models.CASCADE,
        help_text=_('The target entity of this block migration, set to null if it fails to migrate'),
        null=True,
        blank=True,
    )
    change_log_record = models.OneToOneField(
        DraftChangeLogRecord,
        # a changelog record can be pruned, which would set this to NULL, but not delete the
        # entire import record
        null=True,
        on_delete=models.SET_NULL,
    )
    unsupported_reason = models.TextField(
        null=True,
        blank=True,
        help_text=_('Reason if the block is unsupported and target is set to null'),
    )

    class Meta:
        unique_together = [
            ('overall_migration', 'source'),
            # By default defining a unique index on a nullable column will only enforce unicity of non-null values.
            ('overall_migration', 'target'),
        ]

    def __str__(self):
        return (
            f"{self.__class__.__name__} #{self.pk}: "
            f"{self.source.key} → {self.target}"
        )

    def __repr__(self):
        return (
            f"{self.__class__.__name__}("
            f"id={self.id}, source='{self.source}',"
            f"target='{self.target}')"
        )
