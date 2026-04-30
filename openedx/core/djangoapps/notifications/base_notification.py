"""
Base setup for Notification Apps and Types.
"""
from typing import Any, Literal, NotRequired, TypedDict

from django.utils.translation import gettext_lazy as _

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole

from ..django_comment_common.models import FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_COMMUNITY_TA, FORUM_ROLE_MODERATOR
from .email_notifications import EmailCadence
from .notification_content import get_notification_type_context_function
from .settings_override import get_notification_apps_config, get_notification_types_config

FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE = 'filter_audit_expired_users_with_no_role'


class NotificationType(TypedDict):
    """
    Define the fields for values in COURSE_NOTIFICATION_TYPES
    """
    # The notification app associated with this notification.
    # Must be a key in COURSE_NOTIFICATION_APPS.
    notification_app: str
    # Unique identifier for this notification type.
    name: str
    # Whether this notification type uses the notification app's default settings.
    # When True, user preferences are taken from the notification app's configuration,
    # overriding the `web`, `email`, `push`, `email_cadence`, and `non_editable` attributes set here.
    use_app_defaults: bool
    # Template string for notification content.
    # Wrap in gettext_lazy (_) for translation support.
    content_template: str
    # A map of variable names that can be used in the template, along with their descriptions.
    # The values for these variables are passed to the templates when generating the notification.
    # NOTE: this field is for documentation purposes only; it is not used.
    content_context: dict[str, Any]
    filters: list[str]

    # All fields below are required unless `use_app_defaults` is True.

    # Set to True to enable delivery on web.
    web: NotRequired[bool]
    # Set to True to enable delivery via email.
    email: NotRequired[bool]
    # Set to True to enable delivery via push notifications.
    # NOTE: push notifications are not implemented yet
    push: NotRequired[bool]
    # How often email notifications are sent.
    email_cadence: NotRequired[Literal[
        EmailCadence.DAILY, EmailCadence.WEEKLY, EmailCadence.IMMEDIATELY, EmailCadence.NEVER
    ]]
    # Items in the list represent delivery channels
    # where the user is blocked from changing from what is defined for the notification here
    # (see `web`, `email`, and `push` above).
    non_editable: NotRequired[list[Literal["web", "email", "push"]]]
    # Descriptive information about the notification.
    info: NotRequired[str]


# For help defining new notifications, see
# https://docs.openedx.org/en/latest/site_ops/how-tos/enable_notifications.html#creating-a-new-notification
_COURSE_NOTIFICATION_TYPES = {
    'new_comment_on_response': {
        'notification_app': 'discussion',
        'name': 'new_comment_on_response',
        'use_app_defaults': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> commented on your response to the post '
                              '<{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_comment': {
        'notification_app': 'discussion',
        'name': 'new_comment',
        'use_app_defaults': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> commented on <{strong}>{author_name}'
                              '</{strong}> response to your post <{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'author_name': 'author name',
            'replier_name': 'replier name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_response': {
        'notification_app': 'discussion',
        'name': 'new_response',
        'use_app_defaults': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> responded to your '
                              'post <{strong}>{post_title}</{strong}></{p}>'),
        'grouped_content_template': _('<{p}><{strong}>{replier_name}</{strong}> and others have responded to your post '
                                      '<{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_discussion_post': {
        'notification_app': 'discussion',
        'name': 'new_discussion_post',

        'info': '',
        'web': False,
        'email': False,
        'email_cadence': EmailCadence.DAILY,
        'push': False,
        'non_editable': ['push'],
        'content_template': _('<{p}><{strong}>{username}</{strong}> posted <{strong}>{post_title}</{strong}></{p}>'),
        'grouped_content_template': _('<{p}><{strong}>{replier_name}</{strong}> and others started new discussions'
                                      '</{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'username': 'Post author name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_question_post': {
        'notification_app': 'discussion',
        'name': 'new_question_post',

        'info': '',
        'web': False,
        'email': False,
        'email_cadence': EmailCadence.DAILY,
        'push': False,
        'non_editable': ['push'],
        'content_template': _('<{p}><{strong}>{username}</{strong}> asked <{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'username': 'Post author name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'response_on_followed_post': {
        'notification_app': 'discussion',
        'name': 'response_on_followed_post',
        'use_app_defaults': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> responded to a post you’re following: '
                              '<{strong}>{post_title}</{strong}></{p}>'),
        'grouped_content_template': _('<{p}><{strong}>{replier_name}</{strong}> and others responded to a post you’re '
                                      'following: <{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'comment_on_followed_post': {
        'notification_app': 'discussion',
        'name': 'comment_on_followed_post',
        'use_app_defaults': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> commented on <{strong}>{author_name}'
                              '</{strong}> response in a post you’re following <{strong}>{post_title}'
                              '</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'author_name': 'author name',
            'replier_name': 'replier name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'content_reported': {
        'notification_app': 'discussion',
        'name': 'content_reported',

        'info': '',
        'web': True,
        'email': True,
        'email_cadence': EmailCadence.DAILY,
        'push': False,
        'non_editable': ['push'],
        'content_template': _('<p><strong>{username}’s </strong> {content_type} has been reported <strong> {'
                              'content}</strong></p>'),

        'content_context': {
            'post_title': 'Post title',
            'author_name': 'author name',
            'replier_name': 'replier name',
        },

        'visible_to': [FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA]
    },
    'response_endorsed_on_thread': {
        'notification_app': 'discussion',
        'name': 'response_endorsed_on_thread',
        'use_app_defaults': True,
        'content_template': _('<{p}><{strong}>{replier_name}\'s</{strong}> response has been endorsed in your post '
                              '<{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'response_endorsed': {
        'notification_app': 'discussion',
        'name': 'response_endorsed',
        'use_app_defaults': True,
        'content_template': _('<{p}>Your response has been endorsed on the post <{strong}>{post_title}</{strong}></{'
                              'p}>'),
        'content_context': {
            'post_title': 'Post title',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'course_updates': {
        'notification_app': 'updates',
        'name': 'course_updates',

        'info': '',
        'web': True,
        'email': True,
        'push': False,
        'email_cadence': EmailCadence.DAILY,
        'non_editable': ['push'],
        'content_template': _('<{p}><{strong}>{course_update_content}</{strong}></{p}>'),
        'content_context': {
            'course_update_content': 'Course update',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'ora_staff_notifications': {
        'notification_app': 'grading',
        'name': 'ora_staff_notifications',

        'info': 'Notifications for when a submission is made for ORA that includes staff grading step.',
        'web': True,
        'email': False,
        'push': False,
        'email_cadence': EmailCadence.DAILY,
        'non_editable': ['push'],
        'content_template': _('<{p}>You have a new open response submission awaiting review for '
                              '<{strong}>{ora_name}</{strong}></{p}>'),
        'grouped_content_template': _('<{p}>You have multiple submissions awaiting review for '
                                      '<{strong}>{ora_name}</{strong}></{p}>'),
        'content_context': {
            'ora_name': 'Name of ORA in course',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE],
        'visible_to': [CourseStaffRole.ROLE, CourseInstructorRole.ROLE]
    },
    'ora_grade_assigned': {
        'notification_app': 'grading',
        'name': 'ora_grade_assigned',

        'info': '',
        'web': True,
        'email': True,
        'push': False,
        'email_cadence': EmailCadence.DAILY,
        'non_editable': ['push'],
        'content_template': _('<{p}>You have received {points_earned} out of {points_possible} on your assessment: '
                              '<{strong}>{ora_name}</{strong}></{p}>'),
        'content_context': {
            'ora_name': 'Name of ORA in course',
            'points_earned': 'Points earned',
            'points_possible': 'Points possible',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE],
    },
    'new_instructor_all_learners_post': {
        'notification_app': 'discussion',
        'name': 'new_instructor_all_learners_post',

        'info': '',
        'web': True,
        'email': True,
        'email_cadence': EmailCadence.DAILY,
        'push': False,
        'non_editable': ['push'],
        'content_template': _('<{p}>Your instructor posted <{strong}>{post_title}</{strong}></{p}>'),
        'grouped_content_template': '',
        'content_context': {
            'post_title': 'Post title',
        },

        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
}


class NotificationApp(TypedDict):
    """
    Define the fields for values in COURSE_NOTIFICATION_APPS

    An instance of this type describes a notification app,
    which is a way of grouping configuration of types of notifications for users.

    Each notification type defined in COURSE_NOTIFICATION_TYPES also references an app.

    In this case, the delivery preferences for that notification are taken
    """
    # Set to True to enable this app and linked notification types.
    enabled: bool
    # Description to be displayed about grouped notifications for this app.
    # This string should be wrapped in the gettext_lazy function (imported as `_`) to support translation.
    info: str
    # Set to True to enable delivery for associated grouped notifications on web.
    web: bool
    # Set to True to enable delivery for associated grouped notifications via emails.
    email: bool
    # Set to True to enable delivery for associated grouped notifications via push notifications.
    # NOTE: push notifications are not implemented yet
    push: bool
    # How often email notifications are sent for associated grouped notifications.
    email_cadence: Literal[EmailCadence.DAILY, EmailCadence.WEEKLY, EmailCadence.IMMEDIATELY, EmailCadence.NEVER]
    # Items in the list represent grouped notification delivery channels
    # where the user is blocked from changing from what is defined for the app here
    # (see `web`, `email`, and `push` above).
    non_editable: list[Literal["web", "email", "push"]]


# For help defining new notifications and notification apps, see ./docs/creating_a_new_notification_guide.md
_COURSE_NOTIFICATION_APPS: dict[str, NotificationApp] = {
    'discussion': {
        'enabled': True,
        'info': _('Notifications for responses and comments on your posts, and the ones you’re '
                  'following, including endorsements to your responses and on your posts.'),
        'web': True,
        'email': True,
        'push': True,
        'email_cadence': EmailCadence.DAILY,
        'non_editable': []
    },
    'updates': {
        'enabled': True,
        'info': _('Notifications for new announcements and updates from the course team.'),
        'web': True,
        'email': True,
        'push': True,
        'email_cadence': EmailCadence.DAILY,
        'non_editable': []
    },
    'grading': {
        'enabled': True,
        'info': _('Notifications for submission grading.'),
        'web': True,
        'email': True,
        'push': True,
        'email_cadence': EmailCadence.DAILY,
        'non_editable': []
    },
}

COURSE_NOTIFICATION_TYPES = get_notification_types_config()
COURSE_NOTIFICATION_APPS = get_notification_apps_config()


def get_notification_content(notification_type: str, context: dict[str, Any]):
    """
    Returns notification content for the given notification type with provided context.

    Args:
    notification_type (str): The type of notification (e.g., 'course_update').
    context (dict): The context data to be used in the notification template.

    Returns:
    str: Rendered notification content based on the template and context.
    """
    context.update({
        'strong': 'strong',
        'p': 'p',
    })

    # Retrieve the function associated with the notification type.
    context_function = get_notification_type_context_function(notification_type)

    # Fix a specific case where 'course_update' needs to be renamed to 'course_updates'.
    if notification_type == 'course_update':
        notification_type = 'course_updates'

    # Retrieve the notification type object from the default preferences (derived from COURSE_NOTIFICATION_TYPES).
    notification_type = get_default_values_of_preferences().get(notification_type, None)

    if notification_type:
        # Check if the notification is grouped.
        is_grouped = context.get('grouped', False)

        # Determine the correct template key based on whether it's grouped or not.
        template_key = "grouped_content_template" if is_grouped else "content_template"

        # Get the corresponding template from the notification type.
        template = notification_type.get(template_key, None)

        # Apply the context function to transform or modify the context.
        context = context_function(context)

        if template:
            # Handle grouped templates differently by modifying the context using a different function.
            return template.format(**context)

    return ''


def get_default_values_of_preferences() -> dict[str, dict[str, Any]]:
    """
    Returns default preferences for all notification apps
    """
    preferences = {}
    for name, values in COURSE_NOTIFICATION_TYPES.items():
        if values.get('use_app_defaults', None):
            app_defaults = COURSE_NOTIFICATION_APPS[values['notification_app']]
            preferences[name] = {**app_defaults, **values}
        else:
            preferences[name] = {**values}
    return preferences


def filter_notification_types_by_app(app_name, use_app_defaults=None) -> dict[str, dict[str, Any]]:
    """
    Filter notification types by app name and optionally by use_app_defaults flag.

    Args:
        app_name (str): The notification app name to filter by (e.g., 'discussion', 'grading', 'updates')
        use_app_defaults (bool, optional): If provided, additionally filter by use_app_defaults value

    Returns:
        dict: Filtered dictionary containing only matching notification types
    """
    notification_types = get_default_values_of_preferences()
    if use_app_defaults is None:
        return {k: v for k, v in notification_types.items()
                if v.get('notification_app') == app_name}

    return {k: v for k, v in notification_types.items()
            if v.get('notification_app') == app_name
            and v.get('use_app_defaults', False) == use_app_defaults}
