"""
API Serializers for content tagging org
"""

from __future__ import annotations

from rest_framework import serializers, fields

from openedx_tagging.rest_api.v1.serializers import (
    ObjectTagMinimalSerializer,
    TaxonomyListQueryParamsSerializer,
    TaxonomySerializer,
)

from organizations.models import Organization

from ...models import TaxonomyOrg


class TaxonomyOrgListQueryParamsSerializer(TaxonomyListQueryParamsSerializer):
    """
    Serializer for the query params for the GET view
    """

    org: fields.Field = serializers.CharField(
        required=False,
    )
    unassigned: fields.Field = serializers.BooleanField(required=False)

    def validate(self, attrs: dict) -> dict:
        """
        Validate the serializer data
        """
        if "org" in attrs and "unassigned" in attrs:
            raise serializers.ValidationError(
                "'org' and 'unassigned' params cannot be both defined"
            )

        return attrs


class TaxonomyUpdateOrgBodySerializer(serializers.Serializer):
    """
    Serializer for the body params for the update orgs action
    """

    orgs: fields.Field = serializers.SlugRelatedField(
        many=True,
        slug_field="short_name",
        queryset=Organization.objects.all(),
        required=False,
    )

    all_orgs: fields.Field = serializers.BooleanField(required=False)

    def validate(self, attrs: dict) -> dict:
        """
        Validate the serializer data
        """
        if bool(attrs.get("orgs") is not None) == bool(attrs.get("all_orgs")):
            raise serializers.ValidationError(
                "You must specify either orgs or all_orgs, but not both."
            )

        return attrs


class TaxonomyOrgSerializer(TaxonomySerializer):
    """
    Serializer for Taxonomy objects inclusing the associated orgs
    """

    orgs = serializers.SerializerMethodField()
    all_orgs = serializers.SerializerMethodField()

    def get_orgs(self, obj) -> list[str]:
        """
        Return the list of orgs for the taxonomy.
        """
        return [
            taxonomy_org.org.short_name for taxonomy_org in obj.taxonomyorg_set.all()
            if taxonomy_org.org and taxonomy_org.rel_type == TaxonomyOrg.RelType.OWNER
        ]

    def get_all_orgs(self, obj) -> bool:
        """
        Return True if the taxonomy is associated with all orgs.
        """
        for taxonomy_org in obj.taxonomyorg_set.all():
            if taxonomy_org.org_id is None and taxonomy_org.rel_type == TaxonomyOrg.RelType.OWNER:
                return True
        return False

    class Meta:
        model = TaxonomySerializer.Meta.model
        fields = TaxonomySerializer.Meta.fields + ["orgs", "all_orgs"]
        read_only_fields = ["orgs", "all_orgs"]


class ObjectTagCopiedMinimalSerializer(ObjectTagMinimalSerializer):
    """
    Serializer for Object Tags.

    This override `get_can_delete_objecttag` to avoid delete
    object tags if is copied.
    """

    is_copied = serializers.BooleanField(read_only=True)

    class Meta(ObjectTagMinimalSerializer.Meta):
        fields = ObjectTagMinimalSerializer.Meta.fields + ["is_copied"]

    def get_can_delete_objecttag(self, instance):
        """
        Verify if the user can delete the object tag.

        Override to return `False` if the object tag is copied.
        """
        if instance.is_copied:
            # The user can't delete copied tags.
            return False

        return super().get_can_delete_objecttag(instance)
