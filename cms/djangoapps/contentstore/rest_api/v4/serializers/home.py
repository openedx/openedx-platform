"""API serializers for course home V4. Re-exports V2 serializers under V4 names."""
from cms.djangoapps.contentstore.rest_api.v2.serializers.home import (
    CourseCommonSerializerV2,
    CourseHomeTabSerializerV2,
    UnsucceededCourseSerializerV2,
)

CourseCommonSerializerV4 = CourseCommonSerializerV2
CourseHomeTabSerializerV4 = CourseHomeTabSerializerV2
UnsucceededCourseSerializerV4 = UnsucceededCourseSerializerV2

__all__ = [
    "CourseCommonSerializerV4",
    "CourseHomeTabSerializerV4",
    "UnsucceededCourseSerializerV4",
]
