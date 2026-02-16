"""
Content libraries data classes related to Containers.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from django.db.models import QuerySet

from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_content import api as content_api
from openedx_content.models_api import (
    Component,
    Container,
    ContainerVersion,
    Unit,
    UnitVersion,
    Subsection,
    SubsectionVersion,
    Section,
    SectionVersion,
    PublishableEntity,
)

from openedx.core.djangoapps.content_tagging.api import get_object_tag_counts
from openedx.core.djangoapps.xblock.api import get_component_from_usage_key

from ..models import ContentLibrary
from .exceptions import ContentLibraryContainerNotFound
from .libraries import PublishableItem, library_component_usage_key

# The public API is only the following symbols:
__all__ = [
    # Models
    "ContainerMetadata",
    "ContainerType",
    # Methods
    "library_container_locator",
]


class ContainerType(Enum):
    """
    The container types supported by content_libraries, and logic to map them to OLX.
    """
    Unit = "unit"
    Subsection = "subsection"
    Section = "section"

    @property
    def container_model_classes(self) -> tuple[type[Container], type[ContainerVersion]]:
        """
        Get the container, containerversion subclasses associated with this type.
        @@TODO Is this what we want, a hard mapping between container_types and Container classes?
          * If so, then expand on this pattern, so that all ContainerType logic is contained within
            this class, and get rid of the match-case statements that are all over the content_libraries
            app.
          * If not, then figure out what to do instead.
        """
        match self:
            case self.Unit:
                return (Unit, UnitVersion)
            case self.Subsection:
                return (Subsection, SubsectionVersion)
            case self.Section:
                return (Section, SectionVersion)
        raise TypeError(f"unexpected ContainerType: {self!r}")

    @property
    def olx_tag(self) -> str:
        """
        Canonical XML tag to use when representing this container as OLX.

        For example, Units are encoded as <vertical>...</vertical>.

        These tag names are historical. We keep them around for the backwards compatibility of OLX
        and for easier interaction with legacy modulestore-powered structural XBlocks
        (e.g., copy-paste of Units between courses and V2 libraries).
        """
        match self:
            case self.Unit:
                return "vertical"
            case self.Subsection:
                return "sequential"
            case self.Section:
                return "chapter"
        raise TypeError(f"unexpected ContainerType: {self!r}")

    @classmethod
    def from_source_olx_tag(cls, olx_tag: str) -> 'ContainerType':
        """
        Get the ContainerType that this OLX tag maps to.
        """
        if olx_tag == "unit":
            # There is an alternative implementation to VerticalBlock called UnitBlock whose
            # OLX tag is <unit>. When converting from OLX, we want to handle both <vertical>
            # and <unit> as Unit containers, although the canonical serialization is still <vertical>.
            return cls.Unit
        try:
            return next(ct for ct in cls if olx_tag == ct.olx_tag)
        except StopIteration:
            raise ValueError(f"no container_type for XML tag: <{olx_tag}>") from None


@dataclass(frozen=True, kw_only=True)
class ContainerMetadata(PublishableItem):
    """
    Class that represents the metadata about a Container (e.g. Unit) in a content library.
    """
    container_key: LibraryContainerLocator
    container_type: ContainerType
    container_pk: int

    @classmethod
    def from_container(cls, library_key, container: Container, associated_collections=None):
        """
        Construct a ContainerMetadata object from a Container object.
        """
        last_publish_log = container.versioning.last_publish_log
        container_key = library_container_locator(
            library_key,
            container=container,
        )
        container_type = ContainerType(container_key.container_type)
        published_by = None
        if last_publish_log and last_publish_log.published_by:
            published_by = last_publish_log.published_by.username

        draft = container.versioning.draft
        published = container.versioning.published
        last_draft_created = draft.created if draft else None
        if draft and draft.publishable_entity_version.created_by:
            last_draft_created_by = draft.publishable_entity_version.created_by.username
        else:
            last_draft_created_by = ""
        tags = get_object_tag_counts(str(container_key), count_implicit=True)

        return cls(
            container_key=container_key,
            container_type=container_type,
            container_pk=container.pk,
            display_name=draft.title,
            created=container.created,
            modified=draft.created,
            draft_version_num=draft.version_num,
            published_version_num=published.version_num if published else None,
            published_display_name=published.title if published else None,
            last_published=None if last_publish_log is None else last_publish_log.published_at,
            published_by=published_by,
            last_draft_created=last_draft_created,
            last_draft_created_by=last_draft_created_by,
            has_unpublished_changes=content_api.contains_unpublished_changes(container.pk),
            tags_count=tags.get(str(container_key), 0),
            collections=associated_collections or [],
        )


@dataclass(frozen=True, kw_only=True)
class ContainerHierarchy:
    """
    Describes the full ancestry and descendents of a given library object.

    TODO: We intend to replace this implementation with a more efficient one that makes fewer
    database queries in the future. More details being discussed in
    https://github.com/openedx/edx-platform/pull/36813#issuecomment-3136631767
    """
    sections: list[ContainerHierarchyMember] = dataclass_field(default_factory=list)
    subsections: list[ContainerHierarchyMember] = dataclass_field(default_factory=list)
    units: list[ContainerHierarchyMember] = dataclass_field(default_factory=list)
    components: list[ContainerHierarchyMember] = dataclass_field(default_factory=list)
    object_key: LibraryUsageLocatorV2 | LibraryContainerLocator

    class Level(Enum):
        """
        Enumeratable levels contained by the ContainerHierarchy.
        """
        none = 0
        components = 1
        units = 2
        subsections = 3
        sections = 4

        def __bool__(self) -> bool:
            """
            Level.none is False
            All others are True.
            """
            return self != ContainerHierarchy.Level.none

        @property
        def parent(self) -> ContainerHierarchy.Level:
            """
            Returns the parent level above the given level,
            or Level.none if this is already the top level.
            """
            if not self:
                return self
            try:
                return ContainerHierarchy.Level(self.value + 1)
            except ValueError:
                return ContainerHierarchy.Level.none

        @property
        def child(self) -> ContainerHierarchy.Level:
            """
            Returns the name of the child field below the given level,
            or None if level is already the lowest level.
            """
            if not self:
                return self
            try:
                return ContainerHierarchy.Level(self.value - 1)
            except ValueError:
                return ContainerHierarchy.Level.none

    def append(
        self,
        level: Level,
        *items: Component | Container,
    ) -> list[ContainerHierarchyMember]:
        """
        Appends the metadata for the given items to the given level of the hierarchy.
        Returns the resulting list.

        Arguments:
        * level: a valid Level (not Level.none)
        * ...list of Components or Containers to add to this level.
        """
        assert level
        for item in items:
            getattr(self, level.name).append(
                ContainerHierarchyMember.create(
                    self.object_key.context_key,
                    item,
                )
            )

        return getattr(self, level.name)

    @classmethod
    def create_from_library_object_key(
        cls,
        object_key: LibraryUsageLocatorV2 | LibraryContainerLocator,
    ):
        """
        Returns a ContainerHierarchy populated from the library object represented by the given object_key.
        """
        root_items: list[Component] | list[Container]
        root_level: ContainerHierarchy.Level

        if isinstance(object_key, LibraryUsageLocatorV2):
            root_items = [get_component_from_usage_key(object_key)]
            root_level = ContainerHierarchy.Level.components

        elif isinstance(object_key, LibraryContainerLocator):
            root_items = [get_container_from_key(object_key)]
            root_level = ContainerHierarchy.Level[f"{object_key.container_type}s"]

        if not root_level:
            raise TypeError(f"Unexpected '{object_key}': must be LibraryUsageLocatorv2 or LibraryContainerLocator")

        # Fill in root level of hierarchy
        hierarchy = cls(object_key=object_key)
        root_members = hierarchy.append(root_level, *root_items)

        # Fill in hierarchy up through parents
        level = root_level
        members = root_members
        while level := level.parent:
            items = list(_get_containers_with_entities(members).all())
            members = hierarchy.append(level, *items)

        # Fill in hierarchy down from root_level.
        if root_level != cls.Level.components:  # Components have no children
            level = root_level
            members = root_members
            while level := level.child:
                children = _get_containers_children(level, members)
                members = hierarchy.append(level, *children)

        return hierarchy


def _get_containers_with_entities(
    members: list[ContainerHierarchyMember],
    *,
    ignore_pinned=False,
) -> QuerySet[Container]:
    """
    Find all draft containers that directly contain the given entities.

    Args:
        entities: iterable list or queryset of PublishableEntities.
        ignore_pinned: if true, ignore any pinned references to the entity.
    """
    qs = Container.objects.none()
    for member in members:
        qs = qs.union(content_api.get_containers_with_entity(
            member.entity.pk,
            ignore_pinned=ignore_pinned,
        ))
    return qs


def _get_containers_children(
    level: ContainerHierarchy.Level,
    members: list[ContainerHierarchyMember],
    *,
    published=False,
) -> list[Component | Container]:
    """
    Find all components or containers directly contained by the given hierarchy members.

    Args:
        containers: iterable list or queryset of Containers of the same type.
        published: `True` if we want the published version of the children, or
            `False` for the draft version.
    """
    children: list[Component | Container] = []
    for member in members:
        container = member.container
        assert container
        for entry in content_api.get_entities_in_container(
            container,
            published=published,
        ):
            match level:
                case ContainerHierarchy.Level.components:
                    children.append(entry.entity_version.componentversion.component)
                case _:
                    children.append(entry.entity_version.containerversion.container)

    return children


@dataclass(frozen=True, kw_only=True)
class ContainerHierarchyMember:
    """
    Represents an individual member of ContainerHierarchy which is ready to be serialized.
    """
    id: LibraryContainerLocator | LibraryUsageLocatorV2
    display_name: str
    has_unpublished_changes: bool
    component: Component | None
    container: Container | None

    @classmethod
    def create(
        cls,
        library_key: LibraryLocatorV2,
        entity: Container | Component,
    ) -> ContainerHierarchyMember:
        """
        Creates a ContainerHierarchyMember.

        Arguments:
        * library_key: required for generating a usage/locator key for the given entitity.
        * entity: the Container or Component
        """
        if isinstance(entity, Component):
            return ContainerHierarchyMember(
                id=library_component_usage_key(library_key, entity),
                display_name=entity.versioning.draft.title,
                has_unpublished_changes=entity.versioning.has_unpublished_changes,
                component=entity,
                container=None,
            )
        assert isinstance(entity, Container)
        return ContainerHierarchyMember(
            id=library_container_locator(
                library_key,
                container=entity,
            ),
            display_name=entity.versioning.draft.title,
            has_unpublished_changes=content_api.contains_unpublished_changes(entity.pk),
            container=entity,
            component=None,
        )

    @property
    def entity(self) -> PublishableEntity:
        """
        Returns the PublishableEntity associated with this member.

        Raises AssertError if there isn't a Component or Container set.
        """
        entity = self.component or self.container
        assert entity
        return entity.publishable_entity


def library_container_locator(
    library_key: LibraryLocatorV2,
    container: Container,
) -> LibraryContainerLocator:
    """
    Returns a LibraryContainerLocator for the given library + container.
    """
    container_type = None
    if hasattr(container, 'unit'):
        container_type = ContainerType.Unit
    elif hasattr(container, 'subsection'):
        container_type = ContainerType.Subsection
    elif hasattr(container, 'section'):
        container_type = ContainerType.Section
    else:
        # This should never happen, but we assert to ensure that we handle all cases.
        # If this fails, it means that a new Container type was added without updating this code.
        raise ValueError(f"Unexpected container type: {container!r}")

    return LibraryContainerLocator(
        library_key,
        container_type=container_type.value,
        container_id=container.publishable_entity.key,
    )


def get_container_from_key(container_key: LibraryContainerLocator, include_deleted=False) -> Container:
    """
    Internal method to fetch the Container object from its LibraryContainerLocator

    Raises ContentLibraryContainerNotFound if no container found, or if the container has been soft deleted.
    """
    assert isinstance(container_key, LibraryContainerLocator)
    content_library = ContentLibrary.objects.get_by_key(container_key.lib_key)
    learning_package = content_library.learning_package
    assert learning_package is not None
    container = content_api.get_container_by_key(
        learning_package.id,
        key=container_key.container_id,
    )
    # We only return the container if it exists and either:
    # 1. the container has a draft version (which means it is not soft-deleted) OR
    # 2. the container was soft-deleted but the `include_deleted` flag is set to True
    if container and (include_deleted or container.versioning.draft):
        return container
    raise ContentLibraryContainerNotFound
