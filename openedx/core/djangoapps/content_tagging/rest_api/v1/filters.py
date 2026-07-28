"""
API Filters for content tagging org
"""

import openedx_tagging.rules as oel_tagging
from django.db.models import Exists, OuterRef, Q
from rest_framework.filters import BaseFilterBackend

from ...models import TaxonomyOrg
from ...rules import get_admin_orgs, get_user_orgs
from ...utils import rules_cache


class UserOrgFilterBackend(BaseFilterBackend):
    """
    Filter taxonomies based on user's orgs roles

    Taxonomy admin can see all taxonomies
    Org staff can see all taxonomies from their orgs
    Content creators and instructors can see enabled taxonomies avaliable to their orgs
    """

    def filter_queryset(self, request, queryset, _):
        if oel_tagging.is_taxonomy_admin(request.user):
            return queryset

        user_admin_orgs = get_admin_orgs(request.user)
        user_orgs = get_user_orgs(request.user)  # Orgs that the user is a content creator or instructor

        if len(user_orgs) == 0 and len(user_admin_orgs) == 0:
            return queryset.none()

        return queryset.filter(
            # Get enabled taxonomies available to all orgs, or from orgs that the user is
            # a content creator or instructor
            Q(
                Exists(
                    TaxonomyOrg.objects
                    .filter(
                        taxonomy=OuterRef("pk"),
                        rel_type=TaxonomyOrg.RelType.OWNER,
                    )
                    .filter(
                        Q(org=None) |
                        Q(org__in=user_orgs)
                    )
                ),
                enabled=True,
            ) |
            # Get all taxonomies from orgs that the user is OrgStaff
            Q(
                Exists(
                    TaxonomyOrg.objects
                    .filter(taxonomy=OuterRef("pk"), rel_type=TaxonomyOrg.RelType.OWNER)
                    .filter(org__in=user_admin_orgs)
                )
            )
        )


class ObjectTagTaxonomyOrgFilterBackend(BaseFilterBackend):
    """
    Filter for ObjectTagViewSet to only show taxonomies that the user can view.
    """

    def filter_queryset(self, request, queryset, view):
        # Authz path: filter by course org only.
        # The legacy validation layer (check_taxonomy_context_key_org in rules.py) enforces
        # that tags can only be applied to a course from global taxonomies or taxonomies
        # owned by the course's org. This is narrower than the legacy filter path below,
        # which filters by all the user's orgs, but it matches the actual enforcement —
        # a user can never apply an OrgA taxonomy to an OrgB course regardless of their
        # permissions.
        should_use_authz, course_key = getattr(view, '_authz_check', (False, None))
        if should_use_authz and course_key:
            course_orgs = rules_cache.get_orgs([course_key.org]) if course_key.org else []
            return queryset.filter(taxonomy__enabled=True).filter(
                Exists(
                    TaxonomyOrg.objects
                    .filter(taxonomy=OuterRef("taxonomy_id"), rel_type=TaxonomyOrg.RelType.OWNER)
                    .filter(Q(org=None) | Q(org__in=course_orgs))
                )
            ).prefetch_related('taxonomy__taxonomyorg_set')

        # Legacy path: filter by all the user's orgs.
        # This is broader than necessary for this endpoint (see authz path comment above).
        # Users may see taxonomies from orgs unrelated to the current course — those
        # taxonomies can't have tags applied here (blocked by check_taxonomy_context_key_org),
        # but they still appear in the response with can_tag_object=false.
        if oel_tagging.is_taxonomy_admin(request.user):
            return queryset.prefetch_related('taxonomy__taxonomyorg_set')

        user_admin_orgs = get_admin_orgs(request.user)
        user_orgs = get_user_orgs(request.user)
        user_or_admin_orgs = list(set(user_orgs) | set(user_admin_orgs))

        return queryset.filter(taxonomy__enabled=True).filter(
            # Get ObjectTags from taxonomies available to all orgs, or from orgs that the user is
            # a OrgStaff, content creator or instructor
            Q(
                Exists(
                    TaxonomyOrg.objects
                    .filter(
                        taxonomy=OuterRef("taxonomy_id"),
                        rel_type=TaxonomyOrg.RelType.OWNER,
                    )
                    .filter(
                        Q(org=None) |
                        Q(org__in=user_or_admin_orgs)
                    )
                )
            )
        ).prefetch_related('taxonomy__taxonomyorg_set')
