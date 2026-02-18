"""
Models for contentstore
"""
import logging
from datetime import datetime, timezone
from itertools import chain

from config_models.models import ConfigurationModel
from django.db import models
from django.db.models import Case, Exists, ExpressionWrapper, OuterRef, Q, QuerySet, Value, When
from django.db.models.fields import BooleanField, IntegerField, TextField
from django.db.models.functions import Coalesce
from django.db.models.lookups import GreaterThan
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.django.models import ContainerKeyField, CourseKeyField, UsageKeyField
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryContainerLocator
from openedx_content.api import get_published_version
from openedx_content.models_api import Component, Container
from openedx_django_lib.fields import (
    immutable_uuid_field,
    key_field,
    manual_date_time_field,
)

logger = logging.getLogger(__name__)


class VideoUploadConfig(ConfigurationModel):
    """
    Configuration for the video upload feature.

    .. no_pii:
    """
    profile_whitelist = TextField(
        blank=True,
        help_text="A comma-separated list of names of profiles to include in video encoding downloads."
    )

    @classmethod
    def get_profile_whitelist(cls):
        """Get the list of profiles to include in the encoding download"""
        return [profile for profile in cls.current().profile_whitelist.split(",") if profile]


class BackfillCourseTabsConfig(ConfigurationModel):
    """
    Manages configuration for a run of the backfill_course_tabs management command.

    .. no_pii:
    """

    class Meta:
        verbose_name = 'Arguments for backfill_course_tabs'
        verbose_name_plural = 'Arguments for backfill_course_tabs'

    start_index = IntegerField(
        help_text='Index of first course to start backfilling (in an alphabetically sorted list of courses)',
        default=0,
    )
    count = IntegerField(
        help_text='How many courses to backfill in this run (or zero for all courses)',
        default=0,
    )


class CleanStaleCertificateAvailabilityDatesConfig(ConfigurationModel):
    """
    Manages configuration for a run of the `clean_stale_certificate_availability_dates` management command.

    .. no_pii:
    """
    class Meta:
        app_label = "contentstore"
        verbose_name = "Arguments for 'clean_stale_certificate_availability_dates'"
        verbose_name_plural = "Arguments for 'clean_stale_certificate_availability_dates'"

    arguments = TextField(
        blank=True,
        help_text=(
            "A space seperated collection of arguments to be used when running the "
            "`clean_stale_certificate_available_dates` management command.' See the management command for options."
        )
    )


class EntityLinkBase(models.Model):
    """
    Abstract base class that defines fields and functions for storing link between two publishable entities
    or links between publishable entity and a course xblock.
    """
    uuid = immutable_uuid_field()
    # Search by library/upstream context key
    upstream_context_key = key_field(
        help_text=_("Upstream context key i.e., learning_package/library key"),
        db_index=True,
    )
    # A downstream entity can only link to single upstream entity
    # whereas an entity can be upstream for multiple downstream entities.
    downstream_usage_key = UsageKeyField(max_length=255, unique=True)
    # Search by course/downstream key
    downstream_context_key = CourseKeyField(max_length=255, db_index=True)
    # This is present if the creation of this link is a consequence of
    # importing a container that has one or more levels of children.
    # This represents the parent (container) in the top level
    # at the moment of the import.
    top_level_parent = models.ForeignKey("ContainerLink", on_delete=models.SET_NULL, null=True, blank=True)
    version_synced = models.IntegerField()
    version_declined = models.IntegerField(null=True, blank=True)
    downstream_customized = models.JSONField(
        default=list,
        help_text=(
            'Names of the fields which have values set on the upstream block yet have been explicitly'
            ' overridden on this downstream block'
        ),
    )
    created = manual_date_time_field()
    updated = manual_date_time_field()

    @property
    def upstream_context_title(self) -> str:
        """
        Returns upstream context title.
        """
        raise NotImplementedError

    @property
    def published_at(self) -> str | None:
        """
        Returns the published date of the entity
        """
        raise NotImplementedError

    class Meta:
        abstract = True

    @classmethod
    def get_by_downstream_usage_key(cls, downstream_usage_key: UsageKey):
        return cls.objects.get(downstream_usage_key=downstream_usage_key)


class ComponentLink(EntityLinkBase):
    """
    This represents link between any two publishable entities or link between publishable entity and a course
    XBlock. It helps in tracking relationship between XBlocks imported from libraries and used in different courses.
    """
    upstream_block = models.ForeignKey(
        Component,
        on_delete=models.SET_NULL,
        related_name="links",
        null=True,
        blank=True,
    )
    upstream_usage_key = UsageKeyField(
        max_length=255,
        help_text=_(
            "Upstream block usage key, this value cannot be null"
            " and useful to track upstream library blocks that do not exist yet"
        )
    )

    class Meta:
        verbose_name = _("Component Link")
        verbose_name_plural = _("Component Links")

    def __str__(self):
        return f"ComponentLink<{self.upstream_usage_key}->{self.downstream_usage_key}>"

    @property
    def upstream_version_num(self) -> int | None:
        """
        Returns upstream block version number if available.
        """
        published_version = get_published_version(self.upstream_block.publishable_entity.id)
        return published_version.version_num if published_version else None

    @property
    def upstream_context_title(self) -> str:
        """
        Returns upstream context title.
        """
        return self.upstream_block.publishable_entity.learning_package.title

    @property
    def published_at(self) -> str | None:
        """
        Returns the published date of the component
        """
        if self.upstream_block.publishable_entity.published is None:
            raise AttributeError(_("The component must be published to access `published_at`"))
        return self.upstream_block.publishable_entity.published.publish_log_record.publish_log.published_at

    @classmethod
    def filter_links(
        cls,
        *,
        use_top_level_parents=False,
        **link_filter,
    ) -> QuerySet["EntityLinkBase"] | list["EntityLinkBase"]:
        """
        Get all links along with sync flag, upstream context title and version, with optional filtering.

        `use_top_level_parents` is an special filter, replace any result with the top-level parent if exists.
        Example: We have linkA and linkB with top-level parent as linkC, and linkD without top-level parent.
        After all other filters:
        Case 1: `use_top_level_parents` is False, the result is [linkA, linkB, linkC, linkD]
        Case 2: `use_top_level_parents` is True, the result is [linkC, linkD]
        """
        RELATED_FIELDS = [
            "upstream_block__publishable_entity__published__version",
            "upstream_block__publishable_entity__learning_package",
            "upstream_block__publishable_entity__published__publish_log_record__publish_log",
        ]

        ready_to_sync = link_filter.pop('ready_to_sync', None)
        result = cls.objects.filter(**link_filter).select_related(*RELATED_FIELDS).annotate(
            ready_to_sync=(
                GreaterThan(
                    Coalesce("upstream_block__publishable_entity__published__version__version_num", 0),
                    Coalesce("version_synced", 0)
                ) & GreaterThan(
                    Coalesce("upstream_block__publishable_entity__published__version__version_num", 0),
                    Coalesce("version_declined", 0)
                )
            ),
            # This is alwys False, the components doens't have children
            ready_to_sync_from_children=Value(False, output_field=BooleanField())
        )
        if ready_to_sync is not None:
            result = result.filter(ready_to_sync=ready_to_sync)

        # Handle top-level parents logic
        if use_top_level_parents:
            # Get objects without top_level_parent
            objects_without_top_level = result.filter(top_level_parent__isnull=True)

            # Get the top-level parent keys
            top_level_keys = result.filter(top_level_parent__isnull=False).values_list(
                'top_level_parent', flat=True,
            )

            # Get the top-level parents
            # Any top-level parent is a container
            top_level_objects = ContainerLink.filter_links(**{
                "id__in": top_level_keys
            })

            # Returns a list of `EntityLinkBase` as can be a combination of `ComponentLink``
            # and `ContainerLink``
            return list(chain(top_level_objects, objects_without_top_level))

        return result

    @classmethod
    def update_or_create(
        cls,
        upstream_block: Component | None,
        /,
        upstream_usage_key: UsageKey,
        upstream_context_key: str,
        downstream_usage_key: UsageKey,
        downstream_context_key: CourseKey,
        version_synced: int,
        top_level_parent_usage_key: UsageKey | None = None,
        version_declined: int | None = None,
        downstream_customized: list[str] | None = None,
        created: datetime | None = None,
    ) -> "ComponentLink":
        """
        Update or create entity link. This will only update `updated` field if something has changed.
        """
        if not created:
            created = datetime.now(tz=timezone.utc)
        top_level_parent = None
        if top_level_parent_usage_key is not None:
            try:
                top_level_parent = ContainerLink.get_by_downstream_usage_key(
                    top_level_parent_usage_key,
                )
            except ContainerLink.DoesNotExist:
                logger.info(f"Unable to find the link for the container with the link: {top_level_parent_usage_key}")

        new_values = {
            'upstream_usage_key': upstream_usage_key,
            'upstream_context_key': upstream_context_key,
            'downstream_usage_key': downstream_usage_key,
            'downstream_context_key': downstream_context_key,
            'version_synced': version_synced,
            'version_declined': version_declined,
            'top_level_parent': top_level_parent,
            'downstream_customized': downstream_customized,
        }
        if upstream_block:
            new_values['upstream_block'] = upstream_block
        try:
            link = cls.objects.get(downstream_usage_key=downstream_usage_key)
            has_changes = False
            for key, new_value in new_values.items():
                prev_value = getattr(link, key)
                if prev_value != new_value:
                    has_changes = True
                    setattr(link, key, new_value)
            if has_changes:
                link.updated = created
                link.save()
        except cls.DoesNotExist:
            link = cls(**new_values)
            link.created = created
            link.updated = created
            link.save()
        return link


class ContainerLink(EntityLinkBase):
    """
    This represents link between any two publishable entities or link between publishable entity and a course
    xblock. It helps in tracking relationship between xblocks imported from libraries and used in different courses.
    """
    upstream_container = models.ForeignKey(
        Container,
        on_delete=models.SET_NULL,
        related_name="links",
        null=True,
        blank=True,
    )
    upstream_container_key = ContainerKeyField(
        max_length=255,
        help_text=_(
            "Upstream block key (e.g. lct:...), this value cannot be null "
            "and is useful to track upstream library blocks that do not exist yet "
            "or were deleted."
        )
    )

    class Meta:
        verbose_name = _("Container Link")
        verbose_name_plural = _("Container Links")

    def __str__(self):
        return f"ContainerLink<{self.upstream_container_key}->{self.downstream_usage_key}>"

    @property
    def upstream_version_num(self) -> int | None:
        """
        Returns upstream container version number if available.
        """
        published_version = get_published_version(self.upstream_container.publishable_entity.id)
        return published_version.version_num if published_version else None

    @property
    def upstream_context_title(self) -> str:
        """
        Returns upstream context title.
        """
        return self.upstream_container.publishable_entity.learning_package.title

    @property
    def published_at(self) -> str | None:
        """
        Returns the published date of the container
        """
        if self.upstream_container.publishable_entity.published is None:
            raise AttributeError(_("The container must be published to access `published_at`"))
        return self.upstream_container.publishable_entity.published.publish_log_record.publish_log.published_at

    @classmethod
    def filter_links(
        cls,
        *,
        use_top_level_parents=False,
        **link_filter,
    ) -> QuerySet["EntityLinkBase"]:
        """
        Get all links along with sync flag, upstream context title and version, with optional filtering.

        `use_top_level_parents` is an special filter, replace any result with the top-level parent if exists.
        Example: We have linkA and linkB with top-level parent as linkC and linkD without top-level parent.
        After all other filters:
        Case 1: `use_top_level_parents` is False, the result is [linkA, linkB, linkC, linkD]
        Case 2: `use_top_level_parents` is True, the result is [linkC, linkD]
        """
        RELATED_FIELDS = [
            "upstream_container__publishable_entity__published__version",
            "upstream_container__publishable_entity__learning_package",
            "upstream_container__publishable_entity__published__publish_log_record__publish_log",
        ]

        ready_to_sync = link_filter.pop('ready_to_sync', None)
        result = cls._annotate_query_with_ready_to_sync(
            cls.objects.filter(**link_filter).select_related(*RELATED_FIELDS),
        )
        if ready_to_sync is not None:
            result = result.filter(Q(ready_to_sync=ready_to_sync) | Q(ready_to_sync_from_children=ready_to_sync))

        # Handle top-level parents logic
        if use_top_level_parents:
            # Get objects without top_level_parent
            objects_without_top_level = result.filter(top_level_parent__isnull=True)

            # Get the top-level parent keys
            top_level_keys = result.filter(top_level_parent__isnull=False).values_list(
                'top_level_parent', flat=True,
            )

            # Get the top-level parents
            # Any top-level parent is a container
            top_level_objects = cls._annotate_query_with_ready_to_sync(cls.objects.filter(
                id__in=top_level_keys,
            ).select_related(*RELATED_FIELDS))

            result = top_level_objects.union(objects_without_top_level)

        return result

    @classmethod
    def _annotate_query_with_ready_to_sync(cls, query_set: QuerySet["EntityLinkBase"]) -> QuerySet["EntityLinkBase"]:
        """
        Adds ready to sync related values to the query set:
        * `ready_to_sync`: When the container is ready to sync.
        * `ready_to_sync_from_children`: When any children is ready to sync.
        """
        # SubQuery to verify if some container children (associated with top-level parent)
        # needs sync.
        subq_container = cls.objects.filter(
            top_level_parent=OuterRef('pk')
        ).annotate(
            child_ready=Case(
                When(
                    GreaterThan(
                        Coalesce("upstream_container__publishable_entity__published__version__version_num", 0),
                        Coalesce("version_synced", 0)
                    ) & GreaterThan(
                        Coalesce("upstream_container__publishable_entity__published__version__version_num", 0),
                        Coalesce("version_declined", 0)
                    ),
                    then=1
                ),
                # If upstream block was deleted, set ready_to_sync = True
                When(
                    Q(upstream_container__publishable_entity__published__version__version_num__isnull=True),
                    then=1
                ),
                default=0,
                output_field=models.IntegerField()
            )
        ).filter(child_ready=1)

        # SubQuery to verify if some component children (assisiated with top-level parent)
        # needs sync.
        subq_components = ComponentLink.objects.filter(
            top_level_parent=OuterRef('pk')
        ).annotate(
            child_ready=Case(
                When(
                    GreaterThan(
                        Coalesce("upstream_block__publishable_entity__published__version__version_num", 0),
                        Coalesce("version_synced", 0)
                    ) & GreaterThan(
                        Coalesce("upstream_block__publishable_entity__published__version__version_num", 0),
                        Coalesce("version_declined", 0)
                    ),
                    then=1
                ),
                # If upstream block was deleted, set ready_to_sync = True
                When(
                    Q(upstream_block__publishable_entity__published__version__version_num__isnull=True),
                    then=1
                ),
                default=0,
                output_field=models.IntegerField()
            )
        ).filter(child_ready=1)

        # TODO: is there a way to run `subq_container` or `subq_components` depending on the container type?
        return query_set.annotate(
            ready_to_sync=(
                GreaterThan(
                    Coalesce("upstream_container__publishable_entity__published__version__version_num", 0),
                    Coalesce("version_synced", 0)
                ) & GreaterThan(
                    Coalesce("upstream_container__publishable_entity__published__version__version_num", 0),
                    Coalesce("version_declined", 0)
                )
            ),
            ready_to_sync_from_children=ExpressionWrapper(
                Exists(subq_container) | Exists(subq_components),
                output_field=BooleanField(),
            ),
        )

    @classmethod
    def update_or_create(
        cls,
        upstream_container_id: int | None,
        /,
        upstream_container_key: LibraryContainerLocator,
        upstream_context_key: str,
        downstream_usage_key: UsageKey,
        downstream_context_key: CourseKey,
        version_synced: int,
        top_level_parent_usage_key: UsageKey | None = None,
        version_declined: int | None = None,
        downstream_customized: list[str] | None = None,
        created: datetime | None = None,
    ) -> "ContainerLink":
        """
        Update or create entity link. This will only update `updated` field if something has changed.
        """
        if not created:
            created = datetime.now(tz=timezone.utc)
        top_level_parent = None
        if top_level_parent_usage_key is not None:
            try:
                top_level_parent = ContainerLink.get_by_downstream_usage_key(
                    top_level_parent_usage_key,
                )
            except ContainerLink.DoesNotExist:
                logger.info(f"Unable to find the link for the container with the link: {top_level_parent_usage_key}")

        new_values = {
            'upstream_container_key': upstream_container_key,
            'upstream_context_key': upstream_context_key,
            'downstream_usage_key': downstream_usage_key,
            'downstream_context_key': downstream_context_key,
            'version_synced': version_synced,
            'version_declined': version_declined,
            'top_level_parent': top_level_parent,
            'downstream_customized': downstream_customized,
        }
        if upstream_container_id:
            new_values['upstream_container_id'] = upstream_container_id
        try:
            link = cls.objects.get(downstream_usage_key=downstream_usage_key)
            has_changes = False
            for key, new_value in new_values.items():
                prev_value = getattr(link, key)
                if prev_value != new_value:
                    has_changes = True
                    setattr(link, key, new_value)
            if has_changes:
                link.updated = created
                link.save()
        except cls.DoesNotExist:
            link = cls(**new_values)
            link.created = created
            link.updated = created
            link.save()
        return link


class LearningContextLinksStatusChoices(models.TextChoices):
    """
    Enumerates the states that a LearningContextLinksStatus can be in.
    """
    PENDING = "pending", _("Pending")
    PROCESSING = "processing", _("Processing")
    FAILED = "failed", _("Failed")
    COMPLETED = "completed", _("Completed")


class LearningContextLinksStatus(models.Model):
    """
    This table stores current processing status of upstream-downstream links in ComponentLink table for a
    course or a learning context.
    """
    context_key = CourseKeyField(
        max_length=255,
        # Single entry for a learning context or course
        unique=True,
        help_text=_("Linking status for course context key"),
    )
    status = models.CharField(
        max_length=20,
        choices=LearningContextLinksStatusChoices.choices,
        help_text=_("Status of links in given learning context/course."),
    )
    created = manual_date_time_field()
    updated = manual_date_time_field()

    class Meta:
        verbose_name = _("Learning Context Links status")
        verbose_name_plural = _("Learning Context Links status")

    def __str__(self):
        return f"{self.status}|{self.context_key}"

    @classmethod
    def get_or_create(cls, context_key: str, created: datetime | None = None) -> "LearningContextLinksStatus":
        """
        Get or create course link status row from LearningContextLinksStatus table for given course key.

        Args:
            context_key: Learning context or Course key

        Returns:
            LearningContextLinksStatus object
        """
        if not created:
            created = datetime.now(tz=timezone.utc)
        status, _ = cls.objects.get_or_create(
            context_key=context_key,
            defaults={
                'status': LearningContextLinksStatusChoices.PENDING,
                'created': created,
                'updated': created,
            },
        )
        return status

    def update_status(
        self,
        status: LearningContextLinksStatusChoices,
        updated: datetime | None = None
    ) -> None:
        """
        Updates entity links processing status of given learning context.
        """
        self.status = status
        self.updated = updated or datetime.now(tz=timezone.utc)
        self.save()
