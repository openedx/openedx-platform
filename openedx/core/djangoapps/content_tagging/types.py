"""
Types used by content tagging API and implementation
"""
from __future__ import annotations

from typing import Dict, List, Union  # noqa: UP035

from opaque_keys.edx.keys import CollectionKey, ContainerKey, CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_tagging.models import Taxonomy

ContentKey = Union[LibraryLocatorV2, CourseKey, UsageKey, CollectionKey, ContainerKey]  # noqa: UP007
ContextKey = Union[LibraryLocatorV2, CourseKey]  # noqa: UP007

TagValuesByTaxonomyIdDict = Dict[int, List[str]]  # noqa: UP006
TagValuesByObjectIdDict = Dict[str, TagValuesByTaxonomyIdDict]  # noqa: UP006
TaxonomyDict = Dict[int, Taxonomy]  # noqa: UP006
