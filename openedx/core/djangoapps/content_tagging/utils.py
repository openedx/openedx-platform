"""
Utils functions for tagging
"""
from __future__ import annotations

from edx_django_utils.cache import RequestCache
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, CollectionKey, ContainerKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_tagging.models import Taxonomy
from organizations.models import Organization

from .models import TaxonomyOrg
from .types import ContentKey, ContextKey


def get_content_key_from_string(key_str: str) -> ContentKey:
    """
    Get content key from string
    """
    for key_type in (LibraryLocatorV2, CourseKey, UsageKey, ContainerKey, CollectionKey):
        try:
            return key_type.from_string(key_str)
        except InvalidKeyError:
            continue
    raise ValueError(
        "For tagging, object_id must be one of the following "
        "key types: LibraryLocatorV2, CourseKey, UsageKey, ContainerKey, CollectionKey. "
        f"got: {key_str}"
    )


def get_context_key_from_key(content_key: ContentKey) -> ContextKey:
    """
    Returns the context key from a given content key.
    """
    # If the content key is a CourseKey or a LibraryLocatorV2, return it
    if isinstance(content_key, (LibraryLocatorV2, CourseKey)):
        return content_key
    else:
        assert isinstance(content_key.context_key, (CourseKey, LibraryLocatorV2))  # for type checker
        return content_key.context_key


def get_context_key_from_key_string(key_str: str) -> ContextKey:
    """
    Get context key from an key string
    """
    content_key = get_content_key_from_string(key_str)
    return get_context_key_from_key(content_key)


def check_taxonomy_context_key_org(taxonomy: Taxonomy, context_key: ContextKey) -> bool:
    """
    Returns True if the given taxonomy can tag a object with the given context_key.
    """
    if not context_key.org:
        return False

    is_all_org, taxonomy_orgs = TaxonomyOrg.get_organizations(taxonomy)

    if is_all_org:
        return True

    # Ensure the object_id's org is among the allowed taxonomy orgs
    object_org = rules_cache.get_orgs([context_key.org])
    return bool(object_org) and object_org[0] in taxonomy_orgs


class TaggingRulesCache:
    """
    Caches data required for computing rules for the duration of the request.
    """

    def __init__(self):
        """
        Initializes the request cache.
        """
        self.request_cache = RequestCache('openedx.core.djangoapps.content_tagging.utils')

    def clear(self):
        """
        Clears the rules cache.
        """
        self.request_cache.clear()

    def get_orgs(self, org_names: list[str] | None = None) -> list[Organization]:
        """
        Returns the Organizations with the given name(s), or all Organizations if no names given.

        Organization instances are cached for the duration of the request.
        """
        cache_key = 'all_orgs'
        all_orgs = self.request_cache.data.get(cache_key)
        if all_orgs is None:
            all_orgs = {
                org.short_name: org
                for org in Organization.objects.all()
            }
            self.request_cache.set(cache_key, all_orgs)

        if org_names:
            return [
                all_orgs[org_name] for org_name in org_names if org_name in all_orgs
            ]

        return all_orgs.values()

    def get_library_orgs(self, user, org_names: list[str]) -> list[Organization]:
        """
        Returns the Organizations that are associated with libraries that the given user has explicitly been granted
        access to.

        These library orgs are cached for the duration of the request.
        """
        # Import the content_libraries api here to avoid circular imports.
        from openedx.core.djangoapps.content_libraries.api import get_libraries_for_user

        cache_key = f'library_orgs:{user.id}'
        library_orgs = self.request_cache.data.get(cache_key)
        if library_orgs is None:
            library_orgs = {
                library.org.short_name: library.org
                # Note: We don't actually need .learning_package here, but it's already select_related'ed by
                # get_libraries_for_user(), so we need to include it in .only() otherwise we get an ORM error.
                for library in get_libraries_for_user(user).select_related('org').only('org', 'learning_package')
            }
            self.request_cache.set(cache_key, library_orgs)

        return [
            library_orgs[org_name] for org_name in org_names if org_name in library_orgs
        ]


rules_cache = TaggingRulesCache()
