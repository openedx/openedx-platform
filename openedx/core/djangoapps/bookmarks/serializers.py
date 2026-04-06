"""
Serializers for Bookmarks.
"""


from edx_api_doc_tools import is_schema_request
from rest_framework import serializers

from openedx.core.lib.api.serializers import CourseKeyField, UsageKeyField

from . import DEFAULT_FIELDS, OPTIONAL_FIELDS
from .models import Bookmark


class BookmarkSerializer(serializers.ModelSerializer):
    """
    Serializer for the Bookmark model.
    """
    id = serializers.SerializerMethodField(     # pylint: disable=invalid-name
        help_text="The identifier string for the bookmark: {user_id},{usage_id}.",
    )
    course_id = CourseKeyField(
        source='course_key',
        help_text="The identifier string of the bookmark's course.",
    )
    usage_id = UsageKeyField(
        source='usage_key',
        help_text="The identifier string of the bookmark's XBlock.",
    )
    block_type = serializers.ReadOnlyField(source='usage_key.block_type')
    display_name = serializers.ReadOnlyField(
        help_text="Display name of the XBlock.",
    )
    path = serializers.SerializerMethodField(
        help_text="""
            List of dicts containing {"usage_id": <usage-id>, display_name:<display-name>}
            for the XBlocks from the top of the course tree till the parent of the bookmarked XBlock.
        """,
    )

    def __init__(self, *args, **kwargs):
        # Don't pass the 'fields' arg up to the superclass
        try:
            fields = kwargs['context'].pop('fields', DEFAULT_FIELDS) or DEFAULT_FIELDS
        except KeyError:
            fields = DEFAULT_FIELDS
        # Instantiate the superclass normally
        super().__init__(*args, **kwargs)

        # Drop any fields that are not specified in the `fields` argument.
        required_fields = set(fields)

        if 'context' in kwargs and 'request' in kwargs['context'] and is_schema_request(kwargs['context']['request']):
            # We are serving the schema: include everything
            required_fields.update(OPTIONAL_FIELDS)

        all_fields = set(self.fields.keys())
        for field_name in all_fields - required_fields:
            self.fields.pop(field_name)

    class Meta:
        """ Serializer metadata. """
        model = Bookmark
        fields = (
            'id',
            'course_id',
            'usage_id',
            'block_type',
            'display_name',
            'path',
            'created',
        )

    def get_id(self, bookmark):
        """
        Return the REST resource id: {username,usage_id}.
        """
        return f"{bookmark.user.username},{bookmark.usage_key}"

    def get_path(self, bookmark):
        """
        Serialize and return the path data of the bookmark.
        """
        path_items = [path_item._asdict() for path_item in bookmark.path]
        for path_item in path_items:
            path_item['usage_key'] = str(path_item['usage_key'])
        return path_items
