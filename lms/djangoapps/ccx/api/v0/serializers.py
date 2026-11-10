""" CCX API v0 Serializers. """


from ccx_keys.locator import CCXLocator
from rest_framework import serializers

from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.toggles import v0_course_modules_use_schedule
from lms.djangoapps.ccx.utils import get_ccx_visible_chapters


class CCXCourseSerializer(serializers.ModelSerializer):
    """
    Serializer for CCX courses
    """
    ccx_course_id = serializers.SerializerMethodField()
    master_course_id = serializers.CharField(source='course_id')
    display_name = serializers.CharField()
    coach_email = serializers.EmailField(source='coach.email')
    start = serializers.CharField(allow_blank=True)
    due = serializers.CharField(allow_blank=True)
    max_students_allowed = serializers.IntegerField(source='max_student_enrollments_allowed')
    course_modules = serializers.SerializerMethodField()

    class Meta:
        model = CustomCourseForEdX
        fields = (
            "ccx_course_id",
            "master_course_id",
            "display_name",
            "coach_email",
            "start",
            "due",
            "max_students_allowed",
            "course_modules",
        )
        read_only_fields = (
            "ccx_course_id",
            "master_course_id",
            "start",
            "due",
        )

    @staticmethod
    def get_ccx_course_id(obj):
        """
        Getter for the CCX Course ID
        """
        return str(CCXLocator.from_course_locator(obj.course.id, obj.id))

    @staticmethod
    def get_course_modules(obj):
        """
        Getter for the Course Blocks.

        With the ``ccx.v0_course_modules_use_schedule`` flag these are the
        chapters learners can see, otherwise the list stored in ``structure_json``.
        """
        if v0_course_modules_use_schedule(obj.course_id):
            return get_ccx_visible_chapters(obj.course, obj)
        return obj.structure or []
