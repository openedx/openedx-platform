"""Serializers for the legacy library list."""

from rest_framework import serializers

MIGRATION_FIELDS = (
    "migrated_to_key",
    "migrated_to_title",
    "migrated_to_collection_key",
    "migrated_to_collection_title",
)


class LegacyLibraryListQuerySerializer(serializers.Serializer):
    """
    Query parameters of the legacy library list.

    Validate a plain ``dict`` of the query string, not the ``QueryDict``: DRF
    reads an absent boolean in form-style input as ``false``, which would
    silently filter the list.
    """

    org = serializers.CharField(
        required=False,
        help_text=(
            "Return only libraries whose key has exactly this organization, for example `OpenedX`. "
            "The match is case-sensitive: `openedx` does not match `OpenedX`."
        ),
    )
    is_migrated = serializers.BooleanField(
        required=False,
        help_text=(
            "`true` returns only libraries that have been migrated to a content library they now forward to. "
            "`false` returns all others, including libraries copied to a content library without forwarding. "
            "Omit it to return both."
        ),
    )


class LegacyLibrarySerializer(serializers.Serializer):
    """
    A legacy library the requester can read.

    The four `migrated_to_*` fields are present only when `is_migrated` is
    true, and the two collection fields are then null when the migration did
    not target a collection.
    """

    display_name = serializers.CharField(help_text="Display name of the library.")
    library_key = serializers.CharField(help_text="Key of the library, for example `library-v1:OpenedX+Lib1`.")
    url = serializers.CharField(help_text="Path of the library's page in Studio.")
    org = serializers.CharField(help_text="Organization part of the library key.")
    number = serializers.CharField(help_text="Library code part of the library key.")
    can_edit = serializers.BooleanField(help_text="Whether the requester can edit the library.")
    is_migrated = serializers.BooleanField(
        help_text="Whether the library has been migrated to a content library that it now forwards to.",
    )
    migrated_to_key = serializers.CharField(
        required=False,
        help_text="Key of the content library the library was migrated to.",
    )
    migrated_to_title = serializers.CharField(
        required=False,
        help_text="Title of the content library the library was migrated to.",
    )
    migrated_to_collection_key = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Key of the collection the library was migrated into, or null if none.",
    )
    migrated_to_collection_title = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Title of the collection the library was migrated into, or null if none.",
    )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # A nullable field reads a key missing from the row as null, so the
        # collection fields are removed again for a library that is not migrated.
        if not data["is_migrated"]:
            for field_name in MIGRATION_FIELDS:
                data.pop(field_name, None)
        return data
