"""
Tagging Org API Views
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from django.db.models import Count
from django.http import StreamingHttpResponse
from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import COURSES_MANAGE_TAGS, COURSES_VIEW_COURSE
from openedx_events.content_authoring.data import ContentObjectChangedData, ContentObjectData
from openedx_events.content_authoring.signals import CONTENT_OBJECT_ASSOCIATIONS_CHANGED, CONTENT_OBJECT_TAGS_CHANGED
from openedx_tagging import rules as oel_tagging_rules
from openedx_tagging.rest_api.v1.views import ObjectTagView, TaxonomyView
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.types.http import RestRequest

from ...api import (
    create_taxonomy,
    generate_csv_rows,
    get_taxonomies,
    get_taxonomies_for_org,
    get_taxonomy,
    get_unassigned_taxonomies,
    set_taxonomy_orgs,
)
from ...auth import has_view_object_tags_access, should_use_course_authz_for_object
from ...rules import get_admin_orgs
from .filters import ObjectTagTaxonomyOrgFilterBackend, UserOrgFilterBackend
from .serializers import (
    ObjectTagCopiedMinimalSerializer,
    ObjectTagOrgByTaxonomySerializer,
    TaxonomyOrgListQueryParamsSerializer,
    TaxonomyOrgSerializer,
    TaxonomyUpdateOrgBodySerializer,
)

if TYPE_CHECKING:
    from opaque_keys.edx.keys import CourseKey


class TaxonomyOrgView(TaxonomyView):
    """
    View to list, create, retrieve, update, delete, export or import Taxonomies.
    This view extends the TaxonomyView to add Organization filters.

    Refer to TaxonomyView docstring for usage details.

    **Additional List Query Parameters**
        * org (optional) - Filter by organization.

    **List Example Requests**
        GET api/content_tagging/v1/taxonomies?org=orgA                 - Get all taxonomies for organization A
        GET api/content_tagging/v1/taxonomies?org=orgA&enabled=true    - Get all enabled taxonomies for organization A

    **List Query Returns**
        * 200 - Success
        * 400 - Invalid query parameter
        * 403 - Permission denied
    """

    filter_backends = [UserOrgFilterBackend]
    serializer_class = TaxonomyOrgSerializer

    def get_queryset(self):
        """
        Return a list of taxonomies.

        Returns all taxonomies by default.
        If you want the disabled taxonomies, pass enabled=False.
        If you want the enabled taxonomies, pass enabled=True.
        """
        query_params = TaxonomyOrgListQueryParamsSerializer(data=self.request.query_params.dict())
        query_params.is_valid(raise_exception=True)
        enabled = query_params.validated_data.get("enabled", None)
        org = query_params.validated_data.get("org", None)

        # If org filtering was requested, then use it, even if the org is invalid/None
        if "org" in query_params.validated_data:
            queryset = get_taxonomies_for_org(enabled, org)
        elif "unassigned" in query_params.validated_data:
            queryset = get_unassigned_taxonomies(enabled)
        else:
            queryset = get_taxonomies(enabled)

        # Prefetch taxonomyorgs so we can check permissions
        queryset = queryset.prefetch_related("taxonomyorg_set__org")

        # Annotate with tags_count to avoid selecting all the tags
        queryset = queryset.annotate(tags_count=Count("tag", distinct=True))

        return queryset

    def perform_create(self, serializer):
        """
        Create a new taxonomy.
        """
        user_admin_orgs = get_admin_orgs(self.request.user)
        serializer.instance = create_taxonomy(**serializer.validated_data, orgs=user_admin_orgs)

    @action(detail=False, url_path="import", methods=["post"])
    def create_import(self, request: RestRequest, **kwargs) -> Response:  # type: ignore
        """
        Creates a new taxonomy with the given orgs and imports the tags from the uploaded file.
        """
        response = super().create_import(request=request, **kwargs)  # type: ignore

        # If creation was successful, set the orgs for the new taxonomy
        if status.is_success(response.status_code):
            # ToDo: This code is temporary
            # In the future, the orgs parameter will be defined in the request body from the frontend
            # See: https://github.com/openedx/modular-learning/issues/116
            if oel_tagging_rules.is_taxonomy_admin(request.user):
                orgs = None
            else:
                orgs = get_admin_orgs(request.user)

            taxonomy = get_taxonomy(response.data["id"])
            assert taxonomy
            set_taxonomy_orgs(taxonomy, all_orgs=False, orgs=orgs)

            serializer = self.get_serializer(taxonomy)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return response

    @action(detail=True, methods=["put"])
    def orgs(self, request, **_kwargs) -> Response:
        """
        Update the orgs associated with taxonomies.
        """
        taxonomy = self.get_object()
        perm = "oel_tagging.update_orgs"
        if not request.user.has_perm(perm, taxonomy):
            raise PermissionDenied("You do not have permission to update the orgs associated with this taxonomy.")
        body = TaxonomyUpdateOrgBodySerializer(
            data=request.data,
        )
        body.is_valid(raise_exception=True)
        orgs = body.validated_data.get("orgs")
        all_orgs: bool = body.validated_data.get("all_orgs", False)

        set_taxonomy_orgs(taxonomy=taxonomy, all_orgs=all_orgs, orgs=orgs)

        return Response()


class ObjectTagOrgView(ObjectTagView):
    """
    View to create and retrieve ObjectTags for a provided Object ID (object_id).
    This view extends the ObjectTagView to add Organization filters for the results,
    and fires events when the tags are updated.

    Refer to ObjectTagView docstring for usage details.
    """
    # Serializer overrides
    minimal_serializer_class = ObjectTagCopiedMinimalSerializer
    object_tags_serializer_class = ObjectTagOrgByTaxonomySerializer

    filter_backends = [ObjectTagTaxonomyOrgFilterBackend]

    @functools.cached_property
    def _authz_check(self) -> tuple[bool, CourseKey | None]:
        """
        Cache the authz toggle + key-parsing result for the current object_id.

        Safe to cache per-instance because DRF creates a new view instance per request.
        """
        object_id = self.kwargs.get('object_id')
        if object_id:
            return should_use_course_authz_for_object(object_id)
        return False, None

    def get_permissions(self):
        """
        Override get_permissions when using openedx-authz.

        When the toggle is enabled for course objects, we need to change the default
        permission classes set by the parent ObjectTagView so that only openedx-authz
        permissions are used.
        """
        if self._authz_check[0]:
            return [IsAuthenticated()]

        return super().get_permissions()

    def ensure_has_view_object_tag_permission(self, user, taxonomy, object_id):
        """
        Check if user has permission to view object tags.

        This method is overridden to conditionally use openedx-authz when the toggle is enabled.
        """
        should_use_authz, course_key = self._authz_check
        if should_use_authz and not authz_api.is_user_allowed(
            user.username, COURSES_VIEW_COURSE.identifier, str(course_key)
        ):
            raise PermissionDenied("You do not have permission to view object tags.")
        if not should_use_authz:
            # Fall back to parent implementation
            super().ensure_has_view_object_tag_permission(user, taxonomy, object_id)

    def ensure_user_has_can_tag_object_permissions(self, user, tags_data, object_id):
        """
        Check if user has permission to tag object for each taxonomy in tags_data.

        This method is overridden to conditionally use openedx-authz when the toggle is enabled.

        When using openedx-authz, if the user has manage tags permission for the course,
        they can tag the object regardless of the taxonomy.
        """
        should_use_authz, course_key = self._authz_check
        if should_use_authz and not authz_api.is_user_allowed(
            user.username, COURSES_MANAGE_TAGS.identifier, str(course_key)
        ):
            raise PermissionDenied("You do not have permission to manage object tags.")
        if not should_use_authz:
            # Fall back to parent implementation
            super().ensure_user_has_can_tag_object_permissions(user, tags_data, object_id)

    def update(self, request, *args, **kwargs) -> Response:
        """
        Extend the update method to fire CONTENT_OBJECT_ASSOCIATIONS_CHANGED event
        """
        response = super().update(request, *args, **kwargs)
        if response.status_code == 200:
            object_id = kwargs.get('object_id')

            # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
            # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
            CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
                content_object=ContentObjectChangedData(
                    object_id=object_id,
                    changes=["tags"],
                )
            )

            # Emit a (deprecated) CONTENT_OBJECT_TAGS_CHANGED event too
            # .. event_implemented_name: CONTENT_OBJECT_TAGS_CHANGED
            # .. event_type: org.openedx.content_authoring.content.object.tags.changed.v1
            CONTENT_OBJECT_TAGS_CHANGED.send_event(
                content_object=ContentObjectData(object_id=object_id)
            )

        return response


class ObjectTagExportView(APIView):
    """"
    View to export a CSV with all children and tags for a given course/context.
    """
    def get(self, request: RestRequest, **kwargs) -> StreamingHttpResponse:
        """
        Export a CSV with all children and tags for a given course/context.
        """

        class Echo(object):  # noqa: UP004
            """
            Class that implements just the write method of the file-like interface,
            used for the streaming response.
            """
            def write(self, value):
                return value

        object_id: str | None = kwargs.get('context_id', None)
        pseudo_buffer = Echo()

        if not has_view_object_tags_access(self.request.user, object_id):
            raise PermissionDenied(
                "You do not have permission to view object tags for this object_id."
            )

        try:
            return StreamingHttpResponse(
                streaming_content=generate_csv_rows(
                    object_id,
                    pseudo_buffer,
                ),
                content_type="text/csv",
                headers={'Content-Disposition': f'attachment; filename="{object_id}_tags.csv"'},
            )
        except ValueError as e:
            raise ValidationError from e
