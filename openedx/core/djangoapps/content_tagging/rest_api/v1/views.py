"""
Tagging Org API Views
"""
from __future__ import annotations

from django.db.models import Count
from django.http import StreamingHttpResponse
from openedx_tagging import rules as oel_tagging_rules
from openedx_tagging.rest_api.v1.views import ObjectTagView, TaxonomyView
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from openedx_events.content_authoring.data import ContentObjectData, ContentObjectChangedData
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    CONTENT_OBJECT_TAGS_CHANGED,
)

from openedx.core.types.http import RestRequest

from ...auth import has_view_object_tags_access
from ...api import (
    create_taxonomy,
    generate_csv_rows,
    get_taxonomies,
    get_taxonomies_for_org,
    get_taxonomy,
    get_unassigned_taxonomies,
    set_taxonomy_orgs
)
from ...rules import get_admin_orgs
from .filters import ObjectTagTaxonomyOrgFilterBackend, UserOrgFilterBackend
from .serializers import (
    ObjectTagCopiedMinimalSerializer,
    TaxonomyOrgListQueryParamsSerializer,
    TaxonomyOrgSerializer,
    TaxonomyUpdateOrgBodySerializer,
)


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
    minimal_serializer_class = ObjectTagCopiedMinimalSerializer
    filter_backends = [ObjectTagTaxonomyOrgFilterBackend]

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

        class Echo(object):
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
