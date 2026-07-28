"""
API Serializers for course home V2 API.
"""
from django.conf import settings
from rest_framework import serializers

from cms.djangoapps.contentstore.utils import get_lms_link_for_item, reverse_course_url
from cms.djangoapps.contentstore.views.course import _get_rerun_link_for_item
from openedx.core.lib.api.serializers import CourseKeyField


class UnsucceededCourseSerializerV2(serializers.Serializer):
    """Serializer for unsucceeded course."""

    display_name = serializers.CharField()
    course_key = CourseKeyField()
    org = serializers.CharField()
    number = serializers.CharField()
    run = serializers.CharField()
    is_failed = serializers.BooleanField()
    is_in_progress = serializers.BooleanField()
    dismiss_link = serializers.CharField()


class CourseCommonSerializerV2(serializers.Serializer):
    """Serializer for course common fields V2."""

    course_key = CourseKeyField(source='id')
    display_name = serializers.CharField()
    lms_link = serializers.SerializerMethodField()
    cms_link = serializers.SerializerMethodField()
    number = serializers.CharField()
    display_number = serializers.CharField(source='display_number_with_default')
    org = serializers.CharField()
    display_org = serializers.CharField(source='display_org_with_default')
    rerun_link = serializers.SerializerMethodField()
    run = serializers.CharField(source='id.run')
    url = serializers.SerializerMethodField()
    is_active = serializers.SerializerMethodField()

    def get_lms_link(self, obj):
        """Get LMS link for course."""
        return get_lms_link_for_item(obj.location)

    def get_cms_link(self, obj):
        """Get CMS link for course."""
        return f"//{settings.CMS_BASE}{reverse_course_url('course_handler', obj.id)}"

    def get_rerun_link(self, obj):
        """Get rerun link for course."""
        return _get_rerun_link_for_item(obj.id)

    def get_url(self, obj):
        """Get URL from the course handler."""
        return reverse_course_url('course_handler', obj.id)

    def get_is_active(self, obj):
        """Get whether the course is active or not."""
        return not obj.has_ended()


class CourseHomeTabSerializerV2(serializers.Serializer):
    """Serializer for course home tab V2 with unsucceeded courses and in process course actions."""

    courses = CourseCommonSerializerV2(required=False, many=True)
    in_process_course_actions = UnsucceededCourseSerializerV2(
        many=True,
        required=False,
        allow_null=True
    )
