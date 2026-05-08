# pylint: disable=missing-module-docstring
# Template used to create cache keys for individual programs.
PROGRAM_CACHE_KEY_TPL = 'program-{uuid}'

# Cache key used to locate an item containing a list of all program UUIDs for a site.
SITE_PROGRAM_UUIDS_CACHE_KEY_TPL = 'program-uuids-{domain}'

# Template used to create cache keys for individual pathways
PATHWAY_CACHE_KEY_TPL = 'pathway-{id}'

# Cache key used to locate an item containing a list of all credit pathway ids for a site.
SITE_PATHWAY_IDS_CACHE_KEY_TPL = 'pathway-ids-{domain}'

# Template used to create cache keys for individual course runs to program uuids.
COURSE_PROGRAMS_CACHE_KEY_TPL = 'course-programs-{course_run_id}'

# Template used to create cache keys for individual Courses to program uuids.
CATALOG_COURSE_PROGRAMS_CACHE_KEY_TPL = 'catalog-course-programs-{course_uuid}'

# Site-aware cache key template used to locate an item containing
# a list of all program UUIDs with a certain program type (the Site is required
# because program_type values are likely to be shared between different sites
# that live in the same environment).
PROGRAMS_BY_TYPE_CACHE_KEY_TPL = 'programs-by-type-{site_id}-{program_type}'

# Site-aware cache key template used to locate an item containing
# a list of all program UUIDs with a certain program slug
PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL = 'programs-by-type-slug-{site_id}-{program_slug}'

# Template used to create cache keys for organization to program uuids.
PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL = 'organization-programs-{org_key}'
