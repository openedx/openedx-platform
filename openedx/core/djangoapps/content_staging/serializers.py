"""
Serializers for the content libraries REST API
"""
from rest_framework import serializers

from common.djangoapps.student.auth import has_studio_read_access
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from .models import StagedContent


class StagedContentSerializer(serializers.ModelSerializer):
    """
    Serializer for staged content. Doesn't include the OLX by default.
    """
    olx_url = serializers.HyperlinkedIdentityField(view_name="staged-content-olx", lookup_field="id")
    block_type_display = serializers.SerializerMethodField(source="get_block_type_display")

    class Meta:
        model = StagedContent
        fields = [
            'id',
            'user_id',
            'created',
            'purpose',
            'status',
            'block_type',
            'block_type_display',
            # We don't include OLX; it may be large. But we include the URL to retrieve it.
            'olx_url',
            'display_name',
        ]

    def get_block_type_display(self, obj):
        """ Get the friendly name for this XBlock/component type """
        from cms.djangoapps.contentstore.helpers import xblock_type_display_name

        return xblock_type_display_name(obj.block_type)


class UserClipboardSerializer(serializers.Serializer):
    """
    Serializer for the status of the user's clipboard (a UserClipboardData instance)
    """
    content = StagedContentSerializer(allow_null=True)
    source_usage_key = serializers.CharField(allow_blank=True)
    # The title of the course that the content came from originally, if relevant
    source_context_title = serializers.CharField(allow_blank=True)
    # The URL where the original content can be seen, if it still exists and the current user can view it
    source_edit_url = serializers.SerializerMethodField(source="get_source_edit_url")

    def get_source_edit_url(self, obj) -> str:
        """ Get the URL where the user can edit the given XBlock, if it exists """
        from cms.djangoapps.contentstore.helpers import xblock_studio_url

        request = self.context.get("request", None)
        user = request.user if request else None
        if not user:
            return ""
        if not obj.source_usage_key.context_key.is_course:
            return ""  # Linking back to libraries is not implemented yet
        if not has_studio_read_access(user, obj.source_usage_key.course_key):
            return ""
        try:
            block = modulestore().get_item(obj.source_usage_key)
        except ItemNotFoundError:
            return ""
        edit_url = xblock_studio_url(block, find_parent=True)
        if edit_url and request:
            return request.build_absolute_uri(edit_url)
        return ""


class PostToClipboardSerializer(serializers.Serializer):
    """
    Serializer for the POST request body when putting a new XBlock into the
    user's clipboard.
    """
    usage_key = serializers.CharField(help_text="Usage key to copy into the clipboard")
