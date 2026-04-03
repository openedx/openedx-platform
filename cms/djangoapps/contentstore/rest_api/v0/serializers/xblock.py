"""
API Serializers for xblocks
"""
from rest_framework import serializers

from cms.djangoapps.contentstore.rest_api.serializers.common import StrictSerializer

# The XblockSerializer is designed to be scalable and generic. As such, its structure
# should remain as general as possible. Avoid indiscriminately adding fields to it,
# especially those that are xblock-specific. In the future, we aim to develop a solution
# that can generate serializer fields dynamically based on the xblock definitions.


class XblockSerializer(StrictSerializer):
    """
    A serializer for xblocks that enforces strict validation.

    The serializer ensures:
    1. All top-level fields have the expected data types.
    2. No unexpected fields are passed in.

    Note: The current list of fields is not exhaustive. It is primarily designed
    to support the CMS API demo. While optional fields have been added, they were
    chosen based on ease of discovery, not comprehensiveness.
    """
    id = serializers.CharField(required=False, allow_null=True)
    parent_locator = serializers.CharField(required=False, allow_null=True)
    display_name = serializers.CharField(required=False, allow_null=True)
    category = serializers.CharField(required=False, allow_null=True)
    data = serializers.CharField(required=False, allow_null=True)
    metadata = serializers.DictField(required=False, allow_null=True)
    has_changes = serializers.BooleanField(required=False, allow_null=True)
    children = serializers.ListField(required=False, allow_null=True)
    fields = serializers.DictField(required=False, allow_null=True)
    has_children = serializers.BooleanField(required=False, allow_null=True)
    video_sharing_enabled = serializers.BooleanField(required=False, allow_null=True)
    video_sharing_options = serializers.CharField(required=False, allow_null=True)
    video_sharing_doc_url = serializers.CharField(required=False, allow_null=True)
    edited_on = serializers.CharField(required=False, allow_null=True)
    published = serializers.BooleanField(required=False, allow_null=True)
    published_on = serializers.JSONField(required=False, allow_null=True)
    studio_url = serializers.CharField(required=False, allow_null=True)
    released_to_students = serializers.BooleanField(required=False, allow_null=True)
    release_date = serializers.JSONField(required=False, allow_null=True)
    nullout = serializers.JSONField(required=False, allow_null=True)
    graderType = serializers.JSONField(required=False, allow_null=True)
    visibility_state = serializers.CharField(required=False, allow_null=True)
    has_explicit_staff_lock = serializers.BooleanField(
        required=False, allow_null=True
    )
    start = serializers.CharField(required=False, allow_null=True)
    graded = serializers.BooleanField(required=False, allow_null=True)
    due_date = serializers.CharField(required=False, allow_null=True)
    due = serializers.JSONField(required=False, allow_null=True)
    relative_weeks_due = serializers.JSONField(required=False, allow_null=True)
    format = serializers.JSONField(required=False, allow_null=True)
    course_graders = serializers.ListField(required=False, allow_null=True)
    actions = serializers.DictField(required=False, allow_null=True)
    explanatory_message = serializers.Field(required=False, allow_null=True)
    group_access = serializers.DictField(required=False, allow_null=True)
    user_partitions = serializers.ListField(required=False, allow_null=True)
    show_correctness = serializers.CharField(required=False, allow_null=True)
    discussion_enabled = serializers.BooleanField(required=False, allow_null=True)
    ancestor_has_staff_lock = serializers.BooleanField(required=False, allow_null=True)
    user_partition_info = serializers.DictField(required=False, allow_null=True)
    summary_configuration_enabled = serializers.JSONField(required=False, allow_null=True)
    isPrereq = serializers.BooleanField(required=False, allow_null=True)
    prereqUsageKey = serializers.CharField(required=False, allow_null=True)
    prereqMinScore = serializers.IntegerField(required=False, allow_null=True)
    prereqMinCompletion = serializers.IntegerField(required=False, allow_null=True)
    publish = serializers.ChoiceField(
        required=False,
        allow_null=True,
        choices=['make_public', 'republish', 'discard_changes']
    )
    duplicate_source_locator = serializers.CharField(required=False, allow_null=True)
    move_source_locator = serializers.CharField(required=False, allow_null=True)
    target_index = serializers.IntegerField(required=False, allow_null=True)
    boilerplate = serializers.JSONField(required=False, allow_null=True)
    staged_content = serializers.CharField(required=False, allow_null=True)
    hide_from_toc = serializers.BooleanField(required=False, allow_null=True)
