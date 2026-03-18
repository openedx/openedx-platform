"""
Types used by content tagging API and implementation
"""
from __future__ import annotations

from typing import Dict, List, Union

from opaque_keys.edx.keys import CourseKey, UsageKey, CollectionKey, ContainerKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_tagging.models import Taxonomy

ContentKey = Union[LibraryLocatorV2, CourseKey, UsageKey, CollectionKey, ContainerKey]
ContextKey = Union[LibraryLocatorV2, CourseKey]

TagValuesByTaxonomyIdDict = Dict[int, List[str]]
TagValuesByObjectIdDict = Dict[str, TagValuesByTaxonomyIdDict]
TaxonomyDict = Dict[int, Taxonomy]
