"""
Content Tagging APIs
"""
from __future__ import annotations
import io

from itertools import groupby
import csv
from typing import Iterator
from opaque_keys.edx.keys import CourseKey, CollectionKey, ContainerKey, UsageKey

import openedx_tagging.api as oel_tagging
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils.timezone import now
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_tagging.models import ObjectTag, Taxonomy
from openedx_tagging.models.utils import TAGS_CSV_SEPARATOR
from organizations.models import Organization
from .helpers.objecttag_export_helpers import build_object_tree_with_objecttags, iterate_with_level
from openedx_events.content_authoring.data import ContentObjectData, ContentObjectChangedData
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    CONTENT_OBJECT_TAGS_CHANGED,
)

from .models import TaxonomyOrg
from .types import ContentKey, TagValuesByObjectIdDict, TagValuesByTaxonomyIdDict, TaxonomyDict
from .utils import check_taxonomy_context_key_org, get_content_key_from_string, get_context_key_from_key


def create_taxonomy(
    name: str,
    description: str | None = None,
    enabled=True,
    allow_multiple=True,
    allow_free_text=False,
    orgs: list[Organization] | None = None,
    export_id: str | None = None,
) -> Taxonomy:
    """
    Creates, saves, and returns a new Taxonomy with the given attributes.
    """
    taxonomy = oel_tagging.create_taxonomy(
        name=name,
        description=description,
        enabled=enabled,
        allow_multiple=allow_multiple,
        allow_free_text=allow_free_text,
        export_id=export_id,
    )

    if orgs is not None:
        set_taxonomy_orgs(taxonomy=taxonomy, all_orgs=False, orgs=orgs)

    return taxonomy


def set_taxonomy_orgs(
    taxonomy: Taxonomy,
    all_orgs=False,
    orgs: list[Organization] | None = None,
    relationship: TaxonomyOrg.RelType = TaxonomyOrg.RelType.OWNER,
):
    """
    Updates the list of orgs associated with the given taxonomy.

    Currently, we only have an "owner" relationship, but there may be other types added in future.

    When an org has an "owner" relationship with a taxonomy, that taxonomy is available for use by content in that org,
    mies

    If `all_orgs`, then the taxonomy is associated with all organizations, and the `orgs` parameter is ignored.

    If not `all_orgs`, the taxonomy is associated with each org in the `orgs` list. If that list is empty, the
    taxonomy is not associated with any orgs.
    """
    if taxonomy.system_defined:
        raise ValueError("Cannot set orgs for a system-defined taxonomy")

    TaxonomyOrg.objects.filter(
        taxonomy=taxonomy,
        rel_type=relationship,
    ).delete()

    # org=None means the relationship is with "all orgs"
    if all_orgs:
        orgs = [None]
    if orgs:
        TaxonomyOrg.objects.bulk_create(
            [
                TaxonomyOrg(
                    taxonomy=taxonomy,
                    org=org,
                    rel_type=relationship,
                )
                for org in orgs
            ]
        )


def get_taxonomies_for_org(
    enabled=True,
    org_short_name: str | None = None,
) -> QuerySet:
    """
    Generates a list of the enabled Taxonomies available for the given org, sorted by name.

    We return a QuerySet here for ease of use with Django Rest Framework and other query-based use cases.
    So be sure to use `Taxonomy.cast()` to cast these instances to the appropriate subclass before use.

    If no `org` is provided, then only Taxonomies which are available for _all_ Organizations are returned.

    If you want the disabled Taxonomies, pass enabled=False.
    If you want all Taxonomies (both enabled and disabled), pass enabled=None.
    """
    return oel_tagging.get_taxonomies(enabled=enabled).filter(
        Exists(
            TaxonomyOrg.get_relationships(
                taxonomy=OuterRef("pk"),  # type: ignore
                rel_type=TaxonomyOrg.RelType.OWNER,
                org_short_name=org_short_name,
            )
        )
    )


def get_unassigned_taxonomies(enabled=True) -> QuerySet:
    """
    Generate a list of the enabled orphaned Taxomonies, i.e. that do not belong to any
    organization. We don't use `TaxonomyOrg.get_relationships` as that returns
    Taxonomies which are available for all Organizations when no `org` is provided
    """
    return oel_tagging.get_taxonomies(enabled=enabled).filter(
        ~(
            Exists(
                TaxonomyOrg.objects.filter(
                    taxonomy=OuterRef("pk"),
                    rel_type=TaxonomyOrg.RelType.OWNER,
                )
            )
        )
    )


def get_all_object_tags(
    content_key: ContentKey,
    prefetch_orgs: bool = False,
) -> tuple[TagValuesByObjectIdDict, TaxonomyDict]:
    """
    Get all the object tags applied to components in the given course/library.

    Includes any tags applied to the course/library as a whole.
    Returns a tuple with a dictionary of grouped object tag values for all blocks and a dictionary of taxonomies.

    If `prefetch_orgs` is set, then the returned ObjectTag taxonomies will have their TaxonomyOrgs prefetched,
    which makes checking permissions faster.
    """
    context_key_str = str(content_key)
    # We use a block_id_prefix (i.e. the modified course id) to get the tags for the children of the Content
    # (course/library) in a single db query.
    if isinstance(content_key, CourseKey):
        block_id_prefix = context_key_str.replace("course-v1:", "block-v1:", 1)
    elif isinstance(content_key, LibraryLocatorV2):
        block_id_prefix = context_key_str.replace("lib:", "lb:", 1)
    else:
        # No context, so we'll just match the object_id, with no prefix.
        block_id_prefix = None

    # There is no API method in oel_tagging.api that does this yet,
    # so for now we have to build the ORM query directly.
    object_id_clause = Q(object_id=content_key)
    if block_id_prefix:
        object_id_clause |= Q(object_id__startswith=block_id_prefix)

    all_object_tags = ObjectTag.objects.filter(
        Q(tag__isnull=False, tag__taxonomy__isnull=False),
        object_id_clause,
    ).select_related("tag__taxonomy").order_by("object_id")

    if prefetch_orgs:
        all_object_tags = all_object_tags.prefetch_related("tag__taxonomy__taxonomyorg_set")

    grouped_object_tags: TagValuesByObjectIdDict = {}
    taxonomies: TaxonomyDict = {}

    for object_id, block_tags in groupby(all_object_tags, lambda x: x.object_id):
        grouped_object_tags[object_id] = {}
        block_tags_sorted = sorted(block_tags, key=lambda x: x.tag.taxonomy_id if x.tag else 0)  # type: ignore
        for taxonomy_id, taxonomy_tags in groupby(block_tags_sorted, lambda x: x.tag.taxonomy_id if x.tag else 0):
            object_tags_list = list(taxonomy_tags)
            grouped_object_tags[object_id][taxonomy_id] = [
                tag.value for tag in object_tags_list
            ]

            if taxonomy_id not in taxonomies:
                assert object_tags_list[0].tag
                assert object_tags_list[0].tag.taxonomy
                taxonomies[taxonomy_id] = object_tags_list[0].tag.taxonomy

    return grouped_object_tags, dict(sorted(taxonomies.items()))


def set_all_object_tags(
    content_key: ContentKey,
    object_tags: TagValuesByTaxonomyIdDict,
) -> None:
    """
    Sets the tags for the given content object.
    """
    for taxonomy_id, tags_values in object_tags.items():

        taxonomy = oel_tagging.get_taxonomy(taxonomy_id)

        if not taxonomy:
            continue

        tag_object(
            object_id=str(content_key),
            taxonomy=taxonomy,
            tags=tags_values,
        )


def generate_csv_rows(object_id, buffer) -> Iterator[str]:
    """
    Returns a CSV string with tags and taxonomies of all blocks of `object_id`
    """
    content_key = get_content_key_from_string(object_id)

    if isinstance(content_key, (UsageKey, CollectionKey, ContainerKey)):
        raise ValueError("The object_id must be a component, collection, or container.")

    all_object_tags, taxonomies = get_all_object_tags(content_key)
    tagged_content = build_object_tree_with_objecttags(content_key, all_object_tags)

    header = {"name": "Name", "type": "Type", "id": "ID"}

    # Prepare the header for the taxonomies
    for taxonomy_id, taxonomy in taxonomies.items():
        header[f"taxonomy_{taxonomy_id}"] = taxonomy.export_id

    csv_writer = csv.DictWriter(buffer, fieldnames=header.keys(), quoting=csv.QUOTE_NONNUMERIC)
    yield csv_writer.writerow(header)

    # Iterate over the blocks and yield the rows
    for item, level in iterate_with_level(tagged_content):
        block_key = get_content_key_from_string(item.block_id)

        block_data = {
            "name": level * "  " + item.display_name,
            "type": item.category,
            "id": getattr(block_key, 'block_id', item.block_id),
        }

        # Add the tags for each taxonomy
        for taxonomy_id in taxonomies:
            if taxonomy_id in item.object_tags:
                block_data[f"taxonomy_{taxonomy_id}"] = f"{TAGS_CSV_SEPARATOR} ".join(
                    list(item.object_tags[taxonomy_id])
                )

        yield csv_writer.writerow(block_data)


def export_tags_in_csv_file(object_id, file_dir, file_name) -> None:
    """
    Writes a CSV file with tags and taxonomies of all blocks of `object_id`
    """
    buffer = io.StringIO()
    for _ in generate_csv_rows(object_id, buffer):
        # The generate_csv_rows function is a generator,
        # we don't need to do anything with the result here
        pass

    with file_dir.open(file_name, 'w') as csv_file:
        buffer.seek(0)
        csv_file.write(buffer.read())


def set_exported_object_tags(
    content_key: ContentKey,
    exported_tags: TagValuesByTaxonomyIdDict,
) -> None:
    """
    Sets the tags for the given exported content object.
    """
    content_key_str = str(content_key)

    # Clear all tags related with the content.
    oel_tagging.delete_object_tags(content_key_str)

    for taxonomy_export_id, tags_values in exported_tags.items():
        if not tags_values:
            continue

        taxonomy = oel_tagging.get_taxonomy_by_export_id(str(taxonomy_export_id))
        oel_tagging.tag_object(
            object_id=content_key_str,
            taxonomy=taxonomy,
            tags=tags_values,
            create_invalid=True,
            taxonomy_export_id=str(taxonomy_export_id),
        )

        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            time=now(),
            content_object=ContentObjectChangedData(
                object_id=content_key_str,
                changes=["tags"],
            )
        )

        # Emit a (deprecated) CONTENT_OBJECT_TAGS_CHANGED event too
        # .. event_implemented_name: CONTENT_OBJECT_TAGS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.tags.changed.v1
        CONTENT_OBJECT_TAGS_CHANGED.send_event(
            time=now(),
            content_object=ContentObjectData(object_id=content_key_str)
        )


def import_course_tags_from_csv(csv_path, course_id) -> None:
    """
    Import tags from a csv file generated on export.
    """
    # Open csv file and extract the tags
    with open(csv_path, 'r') as csv_file:
        csv_reader = csv.DictReader(csv_file)
        tags_in_blocks = list(csv_reader)

    def get_exported_tags(block) -> TagValuesByTaxonomyIdDict:
        """
        Returns a map with taxonomy export_id and tags for this block.
        """
        result = {}
        for key, value in block.items():
            if key in ['Type', 'Name', 'ID'] or not value:
                continue
            result[key] = value.split(TAGS_CSV_SEPARATOR)
        return result

    course_key = CourseKey.from_string(str(course_id))

    for block in tags_in_blocks:
        exported_tags = get_exported_tags(block)
        block_type = block.get('Type', '')
        block_id = block.get('ID', '')

        if not block_type or not block_id:
            raise ValueError(f"Invalid format of csv in: '{block}'.")

        if block_type == 'course':
            set_exported_object_tags(course_key, exported_tags)
        else:
            block_key = course_key.make_usage_key(block_type, block_id)
            set_exported_object_tags(block_key, exported_tags)


def copy_object_tags(
    source_content_key: ContentKey,
    dest_content_key: ContentKey,
) -> None:
    """
    Copies the permitted object tags on source_object_id to dest_object_id.

    If an source object tag is not available for use on the dest_object_id, it will not be copied.
    """
    all_object_tags, taxonomies = get_all_object_tags(
        content_key=source_content_key,
        prefetch_orgs=True,
    )
    source_object_tags = all_object_tags.get(str(source_content_key), {})

    for taxonomy_id, taxonomy in taxonomies.items():
        tags = source_object_tags.get(taxonomy_id, [])
        tag_object(
            object_id=str(dest_content_key),
            taxonomy=taxonomy,
            tags=tags,
        )


def tag_object(
    object_id: str,
    taxonomy: Taxonomy,
    tags: list[str],
    object_tag_class: type[ObjectTag] = ObjectTag,
) -> None:
    """
    Replaces the existing ObjectTag entries for the given taxonomy + object_id
    with the given list of tags, if the taxonomy can be used by the given object_id.

    This is a wrapper around oel_tagging.tag_object that adds emitting the `CONTENT_OBJECT_ASSOCIATIONS_CHANGED` event
    when tagging an object.

    tags: A list of the values of the tags from this taxonomy to apply.

    object_tag_class: Optional. Use a proxy subclass of ObjectTag for additional
        validation. (e.g. only allow tagging certain types of objects.)

    Raised Tag.DoesNotExist if the proposed tags are invalid for this taxonomy.
    Preserves existing (valid) tags, adds new (valid) tags, and removes omitted
    (or invalid) tags.
    """
    content_key = get_content_key_from_string(object_id)
    context_key = get_context_key_from_key(content_key)

    if check_taxonomy_context_key_org(taxonomy, context_key):
        oel_tagging.tag_object(
            object_id=object_id,
            taxonomy=taxonomy,
            tags=tags,
        )
        # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
        CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
            time=now(),
            content_object=ContentObjectChangedData(
                object_id=object_id,
                changes=["tags"],
            )
        )

        # Emit a (deprecated) CONTENT_OBJECT_TAGS_CHANGED event too
        # .. event_implemented_name: CONTENT_OBJECT_TAGS_CHANGED
        # .. event_type: org.openedx.content_authoring.content.object.tags.changed.v1
        CONTENT_OBJECT_TAGS_CHANGED.send_event(
            time=now(),
            content_object=ContentObjectData(object_id=object_id)
        )

# Expose the oel_tagging APIs

add_tag_to_taxonomy = oel_tagging.add_tag_to_taxonomy
update_tag_in_taxonomy = oel_tagging.update_tag_in_taxonomy
delete_tags_from_taxonomy = oel_tagging.delete_tags_from_taxonomy
get_taxonomy = oel_tagging.get_taxonomy
get_taxonomy_by_export_id = oel_tagging.get_taxonomy_by_export_id
get_taxonomies = oel_tagging.get_taxonomies
get_tags = oel_tagging.get_tags
get_object_tag_counts = oel_tagging.get_object_tag_counts
delete_object_tags = oel_tagging.delete_object_tags
resync_object_tags = oel_tagging.resync_object_tags
get_object_tags = oel_tagging.get_object_tags
add_tag_to_taxonomy = oel_tagging.add_tag_to_taxonomy
copy_tags_as_read_only = oel_tagging.copy_tags
make_copied_tags_editable = oel_tagging.unmark_copied_tags
