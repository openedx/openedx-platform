"""Serializers for the values a rerun of a course starts from."""

from rest_framework import serializers


class CourseRerunDefaultsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The values Studio fills in when an author starts a rerun of a course."""

    allow_unicode_course_id = serializers.BooleanField(
        help_text=(
            "Whether this site accepts characters outside ASCII in the organization, number and run of "
            "a new course."
        ),
    )
    course_creator_status = serializers.CharField(
        help_text=(
            "Whether the caller may create courses. Only global staff may read these values, and they "
            "always may, so the value is 'granted'."
        ),
    )
    display_name = serializers.CharField(
        allow_null=True,
        help_text="Name of the course, which the rerun starts with, or null when the course has none.",
    )
    number = serializers.CharField(help_text="Course number of the course, which the rerun keeps.")
    org = serializers.CharField(help_text="Organization of the course, which the rerun keeps.")
    run = serializers.CharField(help_text="Run of the course. The rerun needs a different one.")

    class Meta:
        ref_name = "CourseRerunDefaults.v2"
