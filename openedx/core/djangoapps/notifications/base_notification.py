"""
Base setup for Notification Apps and Types.
"""
from typing import Any, Literal, TypedDict, NotRequired

from django.utils.translation import gettext_lazy as _

from .email_notifications import EmailCadence
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole

from .settings_override import get_notification_types_config, get_notification_apps_config

from ..django_comment_common.models import FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA
from .notification_content import get_notification_type_context_function

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
    # Mark this as a core notification.
    # When True, user preferences are taken from the notification app's configuration,
    # overriding the `web`, `email`, `push`, `email_cadence`, and `non_editable` attributes set here.
    use_app_defaults: bool
    # Template string for notification content (see ./docs/templates.md).
    # Wrap in gettext_lazy (_) for translation support.
    content_template: str
    # A map of variable names that can be used in the template, along with their descriptions.
    # The values for these variables are passed to the templates when generating the notification.
    # NOTE: this field is for documentation purposes only; it is not used.
    content_context: dict[str, Any]
    filters: list[str]

    # All fields below are required unless `is_core` is True.
    # Core notifications take this config from the associated notification app instead (and ignore anything set here).

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


# For help defining new notifications, see ./docs/creating_a_new_notification_guide.md
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

    Each notification type can also be optionally defined as a core notification.
    In this case, the delivery preferences for that notification are taken
    from the `core_*` fields of the associated notification app.
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


class NotificationTypeManager:
    """
    Manager for notification types
    """

    def __init__(self):
        self.notification_types = COURSE_NOTIFICATION_TYPES

    def get_notification_types_by_app(self, notification_app: str):
        """
        Returns notification types for the given notification app name.
        """
        return [
            notification_type.copy() for _, notification_type in self.notification_types.items()
            if notification_type.get('notification_app', None) == notification_app
        ]

    def get_core_and_non_core_notification_types(
        self, notification_app: str
    ) -> tuple[NotificationType, NotificationType]:
        """
        Returns notification types for the given app name, split by core and non core.

        Return type is a tuple of (core_notification_types, non_core_notification_types).
        """
        notification_types = self.get_notification_types_by_app(notification_app)
        core_notification_types = []
        non_core_notification_types = []
        for notification_type in notification_types:
            if notification_type.get('use_app_defaults', None):
                core_notification_types.append(notification_type)
            else:
                non_core_notification_types.append(notification_type)
        return core_notification_types, non_core_notification_types

    @staticmethod
    def get_non_core_notification_type_preferences(non_core_notification_types, email_opt_out=False):
        """
        Returns non-core notification type preferences for the given notification types.
        """
        non_core_notification_type_preferences = {}
        for notification_type in non_core_notification_types:
            non_core_notification_type_preferences[notification_type.get('name')] = {
                'web': notification_type.get('web', False),
                'email': False if email_opt_out else notification_type.get('email', False),
                'push': notification_type.get('push', False),
                'email_cadence': notification_type.get('email_cadence', 'Daily'),
            }
        return non_core_notification_type_preferences

    def get_notification_app_preference(self, notification_app, email_opt_out=False):
        """
        Returns notification app preferences for the given notification app.
        """
        core_notification_types, non_core_notification_types = self.get_core_and_non_core_notification_types(
            notification_app,
        )
        non_core_notification_types_preferences = self.get_non_core_notification_type_preferences(
            non_core_notification_types, email_opt_out
        )
        core_notification_types_name = [notification_type.get('name') for notification_type in core_notification_types]
        return non_core_notification_types_preferences, core_notification_types_name


class NotificationAppManager:
    """
    Notification app manager
    """

    def add_core_notification_preference(self, notification_app_attrs, notification_types, email_opt_out=False):
        """
        Adds core notification preference for the given notification app.
        """
        notification_types['core'] = {
            'web': notification_app_attrs.get('web', False),
            'email': False if email_opt_out else notification_app_attrs.get('email', False),
            'push': notification_app_attrs.get('push', False),
            'email_cadence': notification_app_attrs.get('email_cadence', 'Daily'),
        }

    def get_notification_app_preferences(self, email_opt_out=False):
        """
        Returns notification app preferences for the given name.
        """
        course_notification_preference_config = {}
        for notification_app_key, notification_app_attrs in COURSE_NOTIFICATION_APPS.items():
            notification_app_preferences = {}
            notification_types, core_notifications = NotificationTypeManager().get_notification_app_preference(
                notification_app_key,
                email_opt_out
            )
            self.add_core_notification_preference(notification_app_attrs, notification_types, email_opt_out)

            notification_app_preferences['enabled'] = notification_app_attrs.get('enabled', False)
            notification_app_preferences['core_notification_types'] = core_notifications
            notification_app_preferences['notification_types'] = notification_types
            course_notification_preference_config[notification_app_key] = notification_app_preferences
        return course_notification_preference_config


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

    # Retrieve the notification type object from NotificationTypeManager.
    notification_type = NotificationTypeManager().notification_types.get(notification_type, None)

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


def get_default_values_of_preference(notification_app, notification_type):
    """
    Returns default preference for notification_type
    """
    default_prefs = NotificationAppManager().get_notification_app_preferences()
    app_prefs = default_prefs.get(notification_app, {})
    core_notification_types = app_prefs.get('core_notification_types', [])
    notification_types = app_prefs.get('notification_types', {})
    if notification_type in core_notification_types:
        return notification_types.get('core', {})
    return notification_types.get(notification_type, {})


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
