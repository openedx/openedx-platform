"""
Serializer for Course Deadlines (Mobile)
"""
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers

from lms.djangoapps.course_home_api.serializers import DatesBannerSerializer
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class CourseDeadlinesMobileSerializer(DatesBannerSerializer):
    """Serializer for course deadlines"""
    has_ended = serializers.SerializerMethodField()

    def get_has_ended(self, _):  # lint-amnesty, pylint: disable=missing-function-docstring
        course_key_string = self.context['view'].kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)
        course = CourseOverview.get_from_id(course_key)

        return course.has_ended()
