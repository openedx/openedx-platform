"""
Value objects
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import (
    CourseLocator,
    LibraryContainerLocator,
    LibraryLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)

from openedx.core.djangoapps.content_libraries.api import ContainerType


class CompositionLevel(Enum):
    """
    Enumeration of composition levels for legacy content.

    Defined in increasing order of complexity so that `is_higher_than` works correctly.
    """
    # Components are individual XBlocks, e.g. Problem
    Component = 'component'

    # Container types currently supported by Content Libraries
    Unit = ContainerType.Unit.value
    Subsection = ContainerType.Subsection.value
    Section = ContainerType.Section.value

    @property
    def is_container(self) -> bool:
        return self is not self.Component

    def is_higher_than(self, other: 'CompositionLevel') -> bool:
        """
        Is this composition level 'above' (more complex than) the other?
        """
        levels: list[CompositionLevel] = list(self.__class__)
        return levels.index(self) > levels.index(other)

    @classmethod
    def supported_choices(cls) -> list[tuple[str, str]]:
        """
        Returns all supported composition levels as a list of tuples,
        for use in a Django Models ChoiceField.
        """
        return [
            (composition_level.value, composition_level.name)
            for composition_level in cls
        ]


class RepeatHandlingStrategy(Enum):
    """
    Enumeration of repeat handling strategies for imported content.
    """
    Skip = 'skip'
    Fork = 'fork'
    Update = 'update'

    @classmethod
    def supported_choices(cls) -> list[tuple[str, str]]:
        """
        Returns all supported repeat handling strategies as a list of tuples,
        for use in a Django Models ChoiceField.
        """
        return [
            (strategy.value, strategy.name)
            for strategy in cls
        ]

    @classmethod
    def default(cls) -> RepeatHandlingStrategy:
        """
        Returns the default repeat handling strategy.
        """
        return cls.Skip


SourceContextKey: t.TypeAlias = CourseLocator | LibraryLocator


@dataclass(frozen=True)
class ModulestoreMigration:
    """
    Metadata on a migration of a course or legacy library to a v2 library in learning core.
    """
    pk: int
    source_key: SourceContextKey
    target_key: LibraryLocatorV2
    target_title: str
    target_collection_slug: str | None
    target_collection_title: str | None
    is_failed: bool
    task_uuid: UUID  # the UserTask which executed this migration


@dataclass(frozen=True)
class ModulestoreBlockMigrationResult:
    """
    Base class for a modulestore block that was part of an attempted migration to learning core.
    """
    source_key: UsageKey
    is_failed: t.ClassVar[bool]


@dataclass(frozen=True)
class ModulestoreBlockMigrationSuccess(ModulestoreBlockMigrationResult):
    """
    Info on a modulestore block which has been successfully migrated into an LC entity
    """
    target_entity_pk: int
    target_key: LibraryUsageLocatorV2 | LibraryContainerLocator
    target_title: str
    target_version_num: int | None
    is_failed: t.ClassVar[bool] = False


@dataclass(frozen=True)
class ModulestoreBlockMigrationFailure(ModulestoreBlockMigrationResult):
    """
    Info on a modulestore block which failed to be migrated into LC
    """
    unsupported_reason: str
    is_failed: t.ClassVar[bool] = True
