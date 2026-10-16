"""
Outline Tab Serializers.
"""

from django.utils.translation import ngettext
from opaque_keys.edx.keys import UsageKey
from rest_framework import serializers

from lms.djangoapps.course_home_api.dates.serializers import DateSummarySerializer
from lms.djangoapps.course_home_api.progress.serializers import CertificateDataSerializer
from lms.djangoapps.course_home_api.serializers import DatesBannerSerializer, VerifiedModeSerializer
from lms.djangoapps.course_home_api.toggles import show_sequence_question_count


class CourseBlockSerializer(serializers.Serializer):
    """
    Serializer for Course Block Objects
    """
    blocks = serializers.SerializerMethodField()

    def get_blocks(self, block):  # pylint: disable=missing-function-docstring
        block_key = block['id']
        block_type = block['type']
        last_parent_block_type = 'vertical' if self.context.get('include_vertical') else 'sequential'
        children = block.get('children', []) if block_type != last_parent_block_type else []
        description = block.get('format')
        display_name = block['display_name']
        enable_links = self.context.get('enable_links')
        graded = block.get('graded')
        icon = None
        num_graded_problems = block.get('num_graded_problems', 0)
        scored = block.get('scored')

        if num_graded_problems and block_type == 'sequential':
            course_key = UsageKey.from_string(block_key).course_key
            if show_sequence_question_count(course_key):
                questions = ngettext('({number} Question)', '({number} Questions)', num_graded_problems)
                display_name += ' ' + questions.format(number=num_graded_problems)

        if graded and scored:
            icon = 'fa-pencil-square-o'

        if block_type == 'vertical':
            icon = self.get_vertical_icon_class(block)

        if 'special_exam_info' in block:
            description = block['special_exam_info'].get('short_description')
            icon = block['special_exam_info'].get('suggested_icon', 'fa-pencil-square-o')

        if self.context.get('enable_prerequisite_block_type', False) and block.get('accessible') is False:
            block_type = 'lock'

        serialized = {
            block_key: {
                'children': [child['id'] for child in children],
                'complete': block.get('complete', False),
                'description': description,
                'display_name': display_name,
                'due': block.get('due'),
                'effort_activities': block.get('effort_activities'),
                'effort_time': block.get('effort_time'),
                'icon': icon,
                'id': block_key,
                'lms_web_url': block['lms_web_url'] if enable_links else None,
                'resume_block': block.get('resume_block', False),
                'type': block_type,
                'has_scheduled_content': block.get('has_scheduled_content'),
                'hide_from_toc': block.get('hide_from_toc'),
            },
        }
        if 'special_exam_info' in self.context.get('extra_fields', []) and block.get('special_exam_info'):
            serialized[block_key]['special_exam_info'] = block.get('special_exam_info').get('short_description')
        if 'completion_stat' in self.context.get('extra_fields', []):
            serialized[block_key]['completion_stat'] = block.get('completion_stat', {})

        for child in children:
            serialized.update(self.get_blocks(child))
        return serialized

    @staticmethod
    def get_vertical_icon_class(block):
        """
        Get the icon class for a vertical block based priority of child blocks types.
        Currently, the priority for the icon is as follows:
            problem
            video
        """
        children = block.get('children', [])
        child_classes = {
            value
            for child in children
            for value in (child.get('type'), child.get('icon_class'))
        }
        if 'problem' in child_classes:
            return 'problem'
        if 'video' in child_classes:
            return 'video'
        return 'other'


class CourseGoalsSerializer(serializers.Serializer):
    """
    Serializer for Course Goal data
    """
    selected_goal = serializers.DictField()
    weekly_learning_goal_enabled = serializers.BooleanField(default=False)


class CourseToolSerializer(serializers.Serializer):
    """
    Serializer for Course Tool Objects
    """
    analytics_id = serializers.CharField()
    title = serializers.CharField()
    url = serializers.SerializerMethodField()

    def get_url(self, tool):
        course_overview = self.context.get('course_overview')
        url = tool.url(course_overview.id)
        request = self.context.get('request')
        return request.build_absolute_uri(url)


class DatesWidgetSerializer(serializers.Serializer):
    """
    Serializer for Dates Widget data
    """
    course_date_blocks = DateSummarySerializer(many=True)
    dates_tab_link = serializers.CharField()
    user_timezone = serializers.CharField()


class EnrollAlertSerializer(serializers.Serializer):
    """
    Serializer for enroll alert information
    """
    can_enroll = serializers.BooleanField()
    extra_text = serializers.CharField()


class ResumeCourseSerializer(serializers.Serializer):
    """
    Serializer for resume course data
    """
    has_visited_course = serializers.BooleanField()
    url = serializers.URLField()


class OutlineTabCourseAccessRedirectSerializer(serializers.Serializer):
    """
    Serializer for a Course Access Redirect response from the outline tab
    """
    url = serializers.URLField()
    error_code = serializers.CharField(source='access_error.error_code')
    developer_message = serializers.CharField(source='access_error.developer_message')
    user_message = serializers.CharField(source='access_error.user_message')


class OutlineTabSerializer(DatesBannerSerializer, VerifiedModeSerializer):
    """
    Serializer for the Outline Tab
    """
    access_expiration = serializers.DictField()
    cert_data = CertificateDataSerializer()
    course_blocks = CourseBlockSerializer()
    course_goals = CourseGoalsSerializer()
    course_tools = CourseToolSerializer(many=True)
    dates_widget = DatesWidgetSerializer()
    enroll_alert = EnrollAlertSerializer()
    enrollment_mode = serializers.CharField()
    enable_proctored_exams = serializers.BooleanField()
    handouts_html = serializers.CharField()
    has_ended = serializers.BooleanField()
    offer = serializers.DictField()
    resume_course = ResumeCourseSerializer()
    welcome_message_html = serializers.CharField()
    user_has_passing_grade = serializers.BooleanField()
