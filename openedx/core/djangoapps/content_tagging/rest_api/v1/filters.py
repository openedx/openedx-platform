"""
API Filters for content tagging org
"""

from django.db.models import Exists, OuterRef, Q
from rest_framework.filters import BaseFilterBackend

import openedx_tagging.rules as oel_tagging

from ...rules import get_admin_orgs, get_user_orgs
from ...models import TaxonomyOrg


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

    def filter_queryset(self, request, queryset, _):
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
