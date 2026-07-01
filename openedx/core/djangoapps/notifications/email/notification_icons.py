"""
Notification Icons
"""
from django.conf import settings


class NotificationTypeIcons:
    """
    Notification Mapping with icons
    """
    CHECK_CIRCLE_GREEN = "CHECK_CIRCLE_GREEN"
    HELP_OUTLINE = "HELP_OUTLINE"
    NEWSPAPER = "NEWSPAPER"
    OPEN_RESPONSE_OUTLINE = "OPEN_RESPONSE_OUTLINE"
    POST_OUTLINE = "POST_OUTLINE"
    QUESTION_ANSWER_OUTLINE = "QUESTION_ANSWER_OUTLINE"
    REPORT_RED = "REPORT_RED"
    VERIFIED = "VERIFIED"

    @classmethod
    def get_icon_name_for_notification_type(cls, notification_type, default="POST_OUTLINE"):
        """
        Returns icon name for notification type
        """
        notification_type_dict = {
            "new_comment_on_response": cls.QUESTION_ANSWER_OUTLINE,
            "new_comment": cls.QUESTION_ANSWER_OUTLINE,
            "new_response": cls.QUESTION_ANSWER_OUTLINE,
            "new_discussion_post": cls.POST_OUTLINE,
            "new_question_post": cls.HELP_OUTLINE,
            "response_on_followed_post": cls.QUESTION_ANSWER_OUTLINE,
            "comment_on_followed_post": cls.QUESTION_ANSWER_OUTLINE,
            "content_reported": cls.REPORT_RED,
            "response_endorsed_on_thread": cls.VERIFIED,
            "response_endorsed": cls.CHECK_CIRCLE_GREEN,
            "course_updates": cls.NEWSPAPER,
            "ora_staff_notifications": cls.OPEN_RESPONSE_OUTLINE,
            "ora_grade_assigned": cls.OPEN_RESPONSE_OUTLINE,
            "ora_reminder": cls.OPEN_RESPONSE_OUTLINE,
        }
        return notification_type_dict.get(notification_type, default)

    @classmethod
    def get_icon_url_for_notification_type(cls, notification_type):
        """
        Returns icon url for notification type
        """
        icon_name = cls.get_icon_name_for_notification_type(notification_type)
        return settings.NOTIFICATION_TYPE_ICONS.get(icon_name, settings.DEFAULT_NOTIFICATION_ICON_URL)
