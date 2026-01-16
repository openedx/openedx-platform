"""
API Serializers for unit page
"""

from django.urls import reverse
from rest_framework import serializers

from cms.djangoapps.contentstore.helpers import (
    xblock_studio_url,
    xblock_type_display_name,
)
from openedx.core.djangoapps.content_tagging.toggles import is_tagging_feature_disabled


class MessageValidation(serializers.Serializer):
    """
    Serializer for representing XBlock error.
    """

    text = serializers.CharField()
    type = serializers.CharField()


class ChildAncestorSerializer(serializers.Serializer):
    """
    Serializer for representing child blocks in the ancestor XBlock.
    """

    url = serializers.SerializerMethodField()
    display_name = serializers.CharField(source="display_name_with_default")
    usage_key = serializers.CharField(source="location")  # need it for frontend authoring app

    def get_url(self, obj):
        """
        Method to generate studio URL for the child block.
        """
        return xblock_studio_url(obj)


class AncestorXBlockSerializer(serializers.Serializer):
    """
    Serializer for representing the ancestor XBlock and its children.
    """

    children = ChildAncestorSerializer(many=True)
    title = serializers.CharField()
    is_last = serializers.BooleanField()


class ContainerXBlock(serializers.Serializer):
    """
    Serializer for representing XBlock data. Doesn't include all data about XBlock.
    """

    display_name = serializers.CharField(source="display_name_with_default")
    display_type = serializers.SerializerMethodField()
    category = serializers.CharField()

    def get_display_type(self, obj):
        """
        Method to get the display type name for the container XBlock.
        """
        return xblock_type_display_name(obj)


class ContainerHandlerSerializer(serializers.Serializer):
    """
    Serializer for container handler
    """

    language_code = serializers.CharField()
    action = serializers.CharField()
    xblock = ContainerXBlock()
    is_unit_page = serializers.BooleanField()
    is_collapsible = serializers.BooleanField()
    position = serializers.IntegerField(min_value=1)
    prev_url = serializers.CharField(allow_null=True)
    next_url = serializers.CharField(allow_null=True)
    new_unit_category = serializers.CharField()
    outline_url = serializers.CharField()
    ancestor_xblocks = AncestorXBlockSerializer(many=True)
    component_templates = serializers.ListField(child=serializers.DictField())
    xblock_info = serializers.DictField()
    draft_preview_link = serializers.CharField()
    published_preview_link = serializers.CharField()
    show_unit_tags = serializers.BooleanField()
    user_clipboard = serializers.DictField()
    is_fullwidth_content = serializers.BooleanField()
    assets_url = serializers.SerializerMethodField()
    unit_block_id = serializers.CharField(source="unit.location.block_id")
    subsection_location = serializers.CharField(source="subsection.location")
    course_sequence_ids = serializers.ListField(child=serializers.CharField())
    library_content_picker_url = serializers.CharField()

    def get_assets_url(self, obj):
        """
        Method to get the assets URL based on the course id.
        """

        context_course = obj.get("context_course", None)
        if context_course:
            return reverse(
                "assets_handler", kwargs={"course_key_string": context_course.id}
            )
        return None


class UpstreamChildrenInfoSerializer(serializers.Serializer):
    """
    Serializer holding the information about the children of an xblock that is syncing.
    """
    name = serializers.CharField()
    upstream = serializers.CharField(allow_null=True)
    id = serializers.CharField()


class UpstreamLinkSerializer(serializers.Serializer):
    """
    Serializer holding info for syncing a block with its upstream (eg, a library block).
    """
    upstream_ref = serializers.CharField()
    version_synced = serializers.IntegerField()
    version_available = serializers.IntegerField(allow_null=True)
    version_declined = serializers.IntegerField(allow_null=True)
    error_message = serializers.CharField(allow_null=True)
    ready_to_sync = serializers.BooleanField()
    downstream_customized = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    top_level_parent_key = serializers.CharField(allow_null=True)
    ready_to_sync_children = UpstreamChildrenInfoSerializer(many=True, required=False)


class ContainerChildSerializer(serializers.Serializer):
    """
    Serializer for representing a xblock child of vertical container.
    """

    name = serializers.CharField()
    block_id = serializers.CharField()
    block_type = serializers.CharField()
    user_partition_info = serializers.DictField()
    user_partitions = serializers.ListField()
    upstream_link = UpstreamLinkSerializer(allow_null=True)
    actions = serializers.SerializerMethodField()
    validation_messages = MessageValidation(many=True)
    render_error = serializers.CharField()

    def get_actions(self, obj):  # pylint: disable=unused-argument
        """
        Method to get actions for each child xlock of the unit.
        """

        can_manage_tags = not is_tagging_feature_disabled()
        xblock = obj["xblock"]
        is_course = xblock.scope_ids.usage_id.context_key.is_course
        xblock_url = xblock_studio_url(xblock)
        # Responsible for the ability to edit container xblock(copy, duplicate, move and manage access).
        # It was used in the legacy and transferred here with simplification.
        # After the investigation it was determined that the "show_other_action"
        # condition below is sufficient to enable/disable actions on each xblock.
        show_inline = xblock.has_children and not xblock_url
        # All except delete and manage tags
        show_other_action = not show_inline and is_course
        actions = {
            "can_copy": show_other_action,
            "can_duplicate": show_other_action,
            "can_move": show_other_action,
            "can_manage_access": show_other_action,
            "can_delete": is_course,
            "can_manage_tags": can_manage_tags,
        }

        return actions


class ContainerChildrenSerializer(serializers.Serializer):
    """
    Serializer for representing a vertical container with state and children.
    """

    class UpstreamReadyToSyncChildrenInfoSerializer(serializers.Serializer):
        """
        Serializer used for the `upstream_ready_to_sync_children_info` field
        """
        id = serializers.CharField()
        name = serializers.CharField()
        upstream = serializers.CharField()
        block_type = serializers.CharField()
        downstream_customized = serializers.ListField(child=serializers.CharField(), allow_empty=True)

    children = ContainerChildSerializer(many=True)
    is_published = serializers.BooleanField()
    can_paste_component = serializers.BooleanField()
    display_name = serializers.CharField()
    upstream_ready_to_sync_children_info = UpstreamReadyToSyncChildrenInfoSerializer(
        many=True,
        required=False,
        help_text="List of dictionaries describing upstream child components readiness to sync."
    )
