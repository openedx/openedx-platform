"""Serializers for the children of a course container."""

from rest_framework import serializers

CONTAINER_MINIMAL_VIEW = "minimal"
CONTAINER_FULL_VIEW = "full"


class CourseContainerChildSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A block placed directly in a container, as listed by default."""

    id = serializers.CharField(help_text="Usage key of the block.")
    category = serializers.CharField(
        help_text="Block type, such as html, problem, video, vertical (unit) or split_test.",
    )
    display_name = serializers.CharField(help_text="Name of the block shown to authors.")
    display_type = serializers.CharField(
        allow_null=True,
        help_text=(
            "Name of the block's type shown to authors, such as Unit, Problem or Text. Null for a block type "
            "that declares no display name."
        ),
    )
    has_children = serializers.BooleanField(
        help_text="Whether the block can hold children, as a unit or a split test does.",
    )

    class Meta:
        ref_name = "CourseContainerChild"


class CourseContainerChildMessageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A problem the block reports with its own settings."""

    text = serializers.CharField(help_text="What is wrong, written for the author.")
    type = serializers.CharField(help_text="Severity: warning or error.")

    class Meta:
        ref_name = "CourseContainerChildMessage"


class CourseContainerChildActionsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """What an author can do with a block placed in a container."""

    can_copy = serializers.BooleanField(help_text="Whether the block can be copied to the clipboard.")
    can_duplicate = serializers.BooleanField(help_text="Whether the block can be duplicated in place.")
    can_move = serializers.BooleanField(help_text="Whether the block can be moved to another container.")
    can_manage_access = serializers.BooleanField(
        help_text="Whether the block's access restrictions can be edited.",
    )
    can_delete = serializers.BooleanField(help_text="Whether the block can be deleted.")
    can_manage_tags = serializers.BooleanField(
        help_text="Whether the block can be tagged. False when tagging is turned off.",
    )

    class Meta:
        ref_name = "CourseContainerChildActions"


class CourseContainerChildUpstreamChildSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A block inside a linked container that can be synced from its own upstream block."""

    id = serializers.CharField(help_text="Usage key of the block in the course.")
    name = serializers.CharField(allow_null=True, help_text="Name of the block. Null when it has none set.")
    upstream = serializers.CharField(allow_null=True, help_text="Key of the library block the block is linked to.")

    class Meta:
        ref_name = "CourseContainerChildUpstreamChild"


class CourseContainerChildUpstreamLinkSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A block's link to the library block it was created from, and whether the library has moved on."""

    upstream_ref = serializers.CharField(help_text="Key of the library block or container the block is linked to.")
    version_synced = serializers.IntegerField(
        allow_null=True,
        help_text="Version of the library content the block holds. Null when the link records none.",
    )
    version_available = serializers.IntegerField(
        allow_null=True,
        help_text="Latest published version of the library content. Null when it cannot be determined.",
    )
    version_declined = serializers.IntegerField(
        allow_null=True,
        help_text="Version an author chose not to sync, if any.",
    )
    error_message = serializers.CharField(
        allow_null=True,
        help_text="Why the link cannot be followed, such as a library that no longer exists. Null for a working link.",
    )
    ready_to_sync = serializers.BooleanField(
        help_text="Whether a newer version is available and can be synced into the block.",
    )
    downstream_customized = serializers.ListField(
        child=serializers.CharField(),
        help_text="Names of the fields the author has changed in the course, which a sync would overwrite.",
    )
    top_level_parent_key = serializers.CharField(
        allow_null=True,
        help_text=(
            "Usage key of the top-level linked container this block belongs to, when it was added with one. "
            "Null otherwise."
        ),
    )
    ready_to_sync_children = CourseContainerChildUpstreamChildSerializer(
        many=True,
        required=False,
        help_text=(
            "The blocks inside a linked container that have a newer version available. Present only when the "
            "block is linked to a library container."
        ),
    )

    class Meta:
        ref_name = "CourseContainerChildUpstreamLink"


class CourseContainerChildFullSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A block placed directly in a container, with everything the Studio unit page shows for it."""

    name = serializers.CharField(help_text="Name of the block shown to authors.")
    block_id = serializers.CharField(help_text="Usage key of the block.")
    block_type = serializers.CharField(help_text="Block type, such as html, problem, video or split_test.")
    user_partition_info = serializers.DictField(
        help_text=(
            "The groups the block's visibility can be restricted to: selectable_partitions, "
            "selected_partition_index (-1 when unrestricted) and selected_groups_label."
        ),
    )
    user_partitions = serializers.ListField(
        child=serializers.DictField(),
        help_text="The course's content groups and enrollment tracks, and which of their groups the block uses.",
    )
    upstream_link = CourseContainerChildUpstreamLinkSerializer(
        allow_null=True,
        help_text="The block's link to a library block. Null when the block is not linked to one.",
    )
    actions = CourseContainerChildActionsSerializer(help_text="What the requesting user's Studio lets them do.")
    validation_messages = CourseContainerChildMessageSerializer(
        many=True,
        help_text="Problems the block reports with its own settings. Empty when there are none.",
    )
    render_error = serializers.CharField(
        allow_blank=True,
        help_text=(
            "A fixed message when the block fails to render in Studio, empty when it renders. The cause is "
            "recorded in the server log, not returned."
        ),
    )

    class Meta:
        ref_name = "CourseContainerChildFull"


class CourseContainerChildQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a container children read. A parameter sent with an empty value counts as omitted."""

    view = serializers.ChoiceField(
        choices=[CONTAINER_MINIMAL_VIEW, CONTAINER_FULL_VIEW],
        default=CONTAINER_MINIMAL_VIEW,
        error_messages={"invalid_choice": "Must be minimal or full."},
        help_text=(
            "minimal (the default) describes each child briefly. full adds what the Studio unit page shows "
            "for each: visibility groups, library link, available actions, validation messages and rendering "
            "errors."
        ),
    )

    class Meta:
        ref_name = "CourseContainerChildQuery"
