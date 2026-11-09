"""
Types used by content tagging API and implementation
"""
from __future__ import annotations

from opaque_keys.edx.keys import CollectionKey, ContainerKey, CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_tagging.models import Taxonomy

type ContentKey = LibraryLocatorV2 | CourseKey | UsageKey | CollectionKey | ContainerKey
type ContextKey = LibraryLocatorV2 | CourseKey

type TagValuesByTaxonomyIdDict = dict[int, list[str]]
type TagValuesByObjectIdDict = dict[str, TagValuesByTaxonomyIdDict]
type TaxonomyDict = dict[int, Taxonomy]
