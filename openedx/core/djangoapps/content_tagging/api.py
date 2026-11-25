"""
Content Tagging APIs
"""
from __future__ import annotations

import csv
import io
import logging
from itertools import groupby
from typing import Iterator  # noqa: UP035

import openedx_tagging.api as oel_tagging
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, ProtectedError, Q, QuerySet
from django.utils.timezone import now
from opaque_keys.edx.keys import CollectionKey, ContainerKey, CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_events.content_authoring.data import ContentObjectChangedData, ContentObjectData
from openedx_events.content_authoring.signals import CONTENT_OBJECT_ASSOCIATIONS_CHANGED, CONTENT_OBJECT_TAGS_CHANGED
from openedx_tagging.models import ObjectTag, Taxonomy
from openedx_tagging.models.utils import TAGS_CSV_SEPARATOR
from organizations.models import Organization

from .helpers.objecttag_export_helpers import build_object_tree_with_objecttags, iterate_with_level
from .models import TaxonomyOrg
from .types import ContentKey, TagValuesByObjectIdDict, TagValuesByTaxonomyIdDict, TaxonomyDict
from .utils import check_taxonomy_context_key_org, get_content_key_from_string, get_context_key_from_key

log = logging.getLogger(__name__)


class InvalidOrgException(ValidationError):
    """
    Exception when attempting to use a taxonomy that's not valid for the given
    org's content.
    """

    def __init__(self, message: str):
        """Initialize with the detailed error message"""
        super().__init__(message)

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


def course_object_id_clause(course_key: CourseKey) -> Q:
    """
    Returns a filter on ``ObjectTag.object_id`` that matches the course key itself and, for a ``course-v1:`` key,
    every block of the course.
    """
    clause = Q(object_id=str(course_key))
    if str(course_key).startswith("course-v1:"):
        # The "+type@" delimiter keeps the prefix from matching a sibling run such as "run2".
        block_prefix = str(course_key).replace("course-v1:", "block-v1:", 1) + "+type@"
        clause |= Q(object_id__startswith=block_prefix)
    return clause


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
    ignore_invalid_orgs=False,
) -> None:
    """
    Sets the tags for the given content object.

    If any of the taxonomies are not configured for use by the org that owns
    the content, this will raise ``InvalidOrgException``, unless you also pass
    ``ignore_invalid_orgs=True``.
    """
    for taxonomy_id, tags_values in object_tags.items():
        taxonomy = oel_tagging.get_taxonomy(taxonomy_id)
        if not taxonomy:
            continue

        try:
            tag_object(object_id=str(content_key), taxonomy=taxonomy, tags=tags_values)
        except InvalidOrgException:
            if ignore_invalid_orgs:
                pass
            else:
                raise


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
    Sets the tags of the given content object to the exported tags, applying only the differences.

    Taxonomies that the export no longer lists are cleared from the object, except free-text taxonomies,
    which the export cannot represent. A row whose taxonomy was deleted is removed, unless the file lists
    its export id and no existing taxonomy claims that export id. A row whose tag the file still lists keeps
    its id.

    Clearing stale taxonomies and removing taxonomy-less rows leave an object tag that
    other data protects in place, log a warning, and continue. Applying the listed taxonomies' tags still
    raises ``ProtectedError``.
    """
    content_key_str = str(content_key)

    taxonomies_by_export_id = {
        str(taxonomy_export_id): oel_tagging.get_taxonomy_by_export_id(str(taxonomy_export_id))
        for taxonomy_export_id, tags_values in exported_tags.items()
        if tags_values
    }

    # Clear what the file no longer lists before applying the listed tags: ``tag_object`` counts the object's
    # tags in other taxonomies toward the per-object limit, so stale rows could make a valid import fail.
    # The existing rows are read from the model, not through ``get_object_tags``, which hides the rows of a
    # disabled taxonomy.
    existing_object_tags = list(ObjectTag.objects.filter(object_id=content_key_str).select_related("taxonomy"))
    listed_taxonomy_ids = {taxonomy.pk for taxonomy in taxonomies_by_export_id.values() if taxonomy}
    # The export omits free-text values, so the absence of a free-text taxonomy from the file says nothing.
    stale_taxonomies = {
        object_tag.taxonomy_id: object_tag.taxonomy
        for object_tag in existing_object_tags
        if object_tag.taxonomy_id
        and object_tag.taxonomy_id not in listed_taxonomy_ids
        and not object_tag.taxonomy.allow_free_text
    }
    for taxonomy in stale_taxonomies.values():
        try:
            # The savepoint keeps a failed delete from poisoning the surrounding transaction.
            with transaction.atomic():
                oel_tagging.tag_object(object_id=content_key_str, taxonomy=taxonomy, tags=[])
        except ProtectedError:
            log.warning(
                "Unable to clear taxonomy %s from %s, other data protects its tags", taxonomy.pk, content_key_str
            )

    # A row whose taxonomy was deleted keeps only the export id, and a row of a live taxonomy can share that
    # export id, so ``tag_object``'s export-id lookup could touch the wrong rows. These rows are removed here,
    # keyed on the missing taxonomy.
    unresolved_export_ids = {
        export_id for export_id, taxonomy in taxonomies_by_export_id.items() if taxonomy is None
    }
    taxonomy_less_pks = [
        object_tag.pk
        for object_tag in existing_object_tags
        if object_tag.taxonomy_id is None and object_tag.export_id not in unresolved_export_ids
    ]
    try:
        with transaction.atomic():
            ObjectTag.objects.filter(pk__in=taxonomy_less_pks).delete()
    except ProtectedError:
        log.warning("Unable to remove the taxonomy-less tags of %s, other data protects them", content_key_str)

    for taxonomy_export_id, taxonomy in taxonomies_by_export_id.items():
        oel_tagging.tag_object(
            object_id=content_key_str,
            taxonomy=taxonomy,
            tags=exported_tags[taxonomy_export_id],
            create_invalid=True,
            taxonomy_export_id=taxonomy_export_id,
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
    Imports the tags of a course from a csv file generated on export.

    Each row listed in the file is applied with ``set_exported_object_tags``. Then the tags of the
    course's objects that the file does not list are removed, because the file holds the full tag state
    of the course. A file with no data rows changes nothing. The sweep leaves the tags of a protected
    object in place and logs a warning.
    """
    # Open csv file and extract the tags
    with open(csv_path, 'r') as csv_file:  # noqa: UP015
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

    if not tags_in_blocks:
        # A real export always writes at least the course row, so a file without data rows is corrupt input
        # and must not sweep the tags of the whole course.
        return

    course_key = CourseKey.from_string(str(course_id))
    imported_object_ids = set()

    for block in tags_in_blocks:
        exported_tags = get_exported_tags(block)
        block_type = block.get('Type', '')
        block_id = block.get('ID', '')

        if not block_type or not block_id:
            raise ValueError(f"Invalid format of csv in: '{block}'.")

        if block_type == 'course':
            content_key = course_key
        else:
            content_key = course_key.make_usage_key(block_type, block_id)
        set_exported_object_tags(content_key, exported_tags)
        imported_object_ids.add(str(content_key))

    # Blocks created after the export are not in the file, and the file is the full tag state of the course.
    tagged_object_ids = (
        ObjectTag.objects.filter(course_object_id_clause(course_key)).values_list("object_id", flat=True).distinct()
    )
    for object_id in set(tagged_object_ids) - imported_object_ids:
        try:
            with transaction.atomic():
                oel_tagging.delete_object_tags(object_id)
        except ProtectedError:
            log.warning("Unable to delete the tags of %s, other data protects them", object_id)


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
        try:
            tag_object(
                object_id=str(dest_content_key),
                taxonomy=taxonomy,
                tags=tags,
            )
        except InvalidOrgException:
            pass


def tag_object(
    object_id: str,
    taxonomy: Taxonomy,
    tags: list[str],
) -> None:
    """
    Replaces the existing ObjectTag entries for the given taxonomy + object_id
    with the given list of tags, if the taxonomy can be used by the given object_id.

    This is a wrapper around oel_tagging.tag_object that adds emitting the `CONTENT_OBJECT_ASSOCIATIONS_CHANGED` event
    when tagging an object.

    tags: A list of the values of the tags from this taxonomy to apply.

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
    else:
        raise InvalidOrgException(
            f'Taxonomy "{taxonomy.name}" is not configured for use with organization "{context_key.org}".'
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
