"""
API Serializers for course index
"""

from rest_framework import serializers

from cms.djangoapps.contentstore.rest_api.serializers.common import ProctoringErrorListSerializer


class InitialIndexStateSerializer(serializers.Serializer):
    """Serializer for initial course index state"""
    expanded_locators = serializers.ListSerializer(child=serializers.CharField())
    locator_to_show = serializers.CharField()


class CourseIndexSerializer(serializers.Serializer):
    """Serializer for course index"""
    course_release_date = serializers.CharField()
    course_structure = serializers.DictField()
    deprecated_blocks_info = serializers.DictField()
    discussions_incontext_feedback_url = serializers.CharField()
    discussions_incontext_learnmore_url = serializers.CharField()
    discussions_settings = serializers.DictField()
    initial_state = InitialIndexStateSerializer()
    initial_user_clipboard = serializers.DictField()
    language_code = serializers.CharField()
    lms_link = serializers.CharField()
    mfe_proctored_exam_settings_url = serializers.CharField()
    # notification_dismiss_url is None when there is no pending CourseRerunState
    # for this course, so the field must accept null. See issue #34483.
    notification_dismiss_url = serializers.CharField(allow_null=True, required=False)
    proctoring_errors = ProctoringErrorListSerializer(many=True)
    reindex_link = serializers.CharField()
    rerun_notification_id = serializers.IntegerField()
    advance_settings_url = serializers.CharField()
    is_custom_relative_dates_active = serializers.BooleanField()
    created_on = serializers.DateTimeField()
