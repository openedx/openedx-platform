"""
API Serializers for course details
"""

from rest_framework import serializers

from openedx.core.lib.api.serializers import CourseKeyField


class InstructorInfoSerializer(serializers.Serializer):
    """ Serializer for instructor info """
    name = serializers.CharField(allow_blank=True, required=False)
    title = serializers.SerializerMethodField()
    organization = serializers.CharField(allow_blank=True, required=False)
    image = serializers.CharField(allow_blank=True, required=False)
    bio = serializers.CharField(allow_blank=True, required=False)

    def get_title(self, obj):
        return obj.title() if isinstance(obj, str) else ""


class InstructorsSerializer(serializers.Serializer):
    """ Serializer for instructors """
    instructors = InstructorInfoSerializer(many=True, allow_empty=True, allow_null=True, required=False)


class CourseDetailsSerializer(serializers.Serializer):
    """ Serializer for course details """
    about_sidebar_html = serializers.CharField(allow_null=True, allow_blank=True)
    banner_image_name = serializers.CharField(allow_blank=True)
    banner_image_asset_path = serializers.CharField()
    certificate_available_date = serializers.DateTimeField()
    certificates_display_behavior = serializers.CharField(allow_null=True)
    course_id = serializers.CharField()
    course_image_asset_path = serializers.CharField(allow_blank=True)
    course_image_name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    duration = serializers.CharField(allow_blank=True)
    effort = serializers.CharField(allow_null=True, allow_blank=True)
    end_date = serializers.DateTimeField(allow_null=True)
    enrollment_end = serializers.DateTimeField(allow_null=True)
    enrollment_start = serializers.DateTimeField(allow_null=True)
    entrance_exam_enabled = serializers.CharField(allow_blank=True)
    entrance_exam_id = serializers.CharField(allow_blank=True)
    entrance_exam_minimum_score_pct = serializers.CharField(allow_blank=True)
    instructor_info = InstructorsSerializer()
    intro_video = serializers.CharField(allow_null=True)
    language = serializers.CharField(allow_null=True)
    learning_info = serializers.ListField(child=serializers.CharField(allow_blank=True))
    license = serializers.CharField(allow_null=True)
    org = serializers.CharField()
    overview = serializers.CharField(allow_blank=True)
    pre_requisite_courses = serializers.ListField(child=CourseKeyField())
    run = serializers.CharField()
    self_paced = serializers.BooleanField()
    has_changes = serializers.BooleanField()
    short_description = serializers.CharField(allow_blank=True)
    start_date = serializers.DateTimeField()
    subtitle = serializers.CharField(allow_blank=True)
    syllabus = serializers.CharField(allow_null=True)
    title = serializers.CharField(allow_blank=True)
    video_thumbnail_image_asset_path = serializers.CharField()
    video_thumbnail_image_name = serializers.CharField(allow_blank=True)
