"""Configuration for the search index."""
from .documents import Fields


INDEX_DISTINCT_ATTRIBUTE = "usage_key"

# Mark which attributes can be used for filtering/faceted search:
INDEX_FILTERABLE_ATTRIBUTES = [
    # Get specific block/collection using combination of block_id and context_key
    Fields.block_id,
    Fields.block_type,
    Fields.context_key,
    Fields.usage_key,
    Fields.org,
    Fields.tags,
    Fields.tags + "." + Fields.tags_taxonomy,
    Fields.tags + "." + Fields.tags_level0,
    Fields.tags + "." + Fields.tags_level1,
    Fields.tags + "." + Fields.tags_level2,
    Fields.tags + "." + Fields.tags_level3,
    Fields.collections,
    Fields.collections + "." + Fields.collections_display_name,
    Fields.collections + "." + Fields.collections_key,
    Fields.type,
    Fields.access_id,
    Fields.last_published,
    Fields.content + "." + Fields.problem_types,
    Fields.publish_status,
    Fields.breadcrumbs + "." + Fields.usage_key,
]

# Mark which attributes are used for keyword search, in order of importance:
INDEX_SEARCHABLE_ATTRIBUTES = [
    # Keyword search does _not_ search the course name, course ID, breadcrumbs, block type, or other fields.
    Fields.display_name,
    Fields.block_id,
    Fields.content,
    Fields.description,
    Fields.tags,
    Fields.collections,
    # If we don't list the following sub-fields _explicitly_, they're only sometimes searchable - that is, they
    # are searchable only if at least one document in the index has a value. If we didn't list them here and,
    # say, there were no tags.level3 tags in the index, the client would get an error if trying to search for
    # these sub-fields: "Attribute `tags.level3` is not searchable."
    Fields.tags + "." + Fields.tags_taxonomy,
    Fields.tags + "." + Fields.tags_level0,
    Fields.tags + "." + Fields.tags_level1,
    Fields.tags + "." + Fields.tags_level2,
    Fields.tags + "." + Fields.tags_level3,
    Fields.collections + "." + Fields.collections_display_name,
    Fields.collections + "." + Fields.collections_key,
    Fields.published + "." + Fields.display_name,
    Fields.published + "." + Fields.published_description,
]

# Mark which attributes can be used for sorting search results:
INDEX_SORTABLE_ATTRIBUTES = [
    Fields.display_name,
    Fields.created,
    Fields.modified,
    Fields.last_published,
]

# Update the search ranking rules to let the (optional) "sort" parameter take precedence over keyword relevance.
INDEX_RANKING_RULES = [
    "sort",
    "words",
    "typo",
    "proximity",
    "attribute",
    "exactness",
]
