"""
Base setup for Notification Apps and Types.
"""
from django.utils.translation import gettext_lazy as _

from .email_notifications import EmailCadence
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from .utils import find_app_in_normalized_apps, find_pref_in_normalized_prefs
from ..django_comment_common.models import FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA
from .notification_content import get_notification_type_context_function

FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE = 'filter_audit_expired_users_with_no_role'

COURSE_NOTIFICATION_TYPES = {
    'new_comment_on_response': {
        'notification_app': 'discussion',
        'name': 'new_comment_on_response',
        'is_core': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> commented on your response to the post '
                              '<{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_comment': {
        'notification_app': 'discussion',
        'name': 'new_comment',
        'is_core': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> commented on <{strong}>{author_name}'
                              '</{strong}> response to your post <{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'author_name': 'author name',
            'replier_name': 'replier name',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_response': {
        'notification_app': 'discussion',
        'name': 'new_response',
        'is_core': True,
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> responded to your '
                              'post <{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_discussion_post': {
        'notification_app': 'discussion',
        'name': 'new_discussion_post',
        'is_core': False,
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
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'new_question_post': {
        'notification_app': 'discussion',
        'name': 'new_question_post',
        'is_core': False,
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
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'response_on_followed_post': {
        'notification_app': 'discussion',
        'name': 'response_on_followed_post',
        'is_core': True,
        'info': '',
        'non_editable': [],
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> responded to a post you’re following: '
                              '<{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'comment_on_followed_post': {
        'notification_app': 'discussion',
        'name': 'comment_on_followed_post',
        'is_core': True,
        'info': '',
        'non_editable': [],
        'content_template': _('<{p}><{strong}>{replier_name}</{strong}> commented on <{strong}>{author_name}'
                              '</{strong}> response in a post you’re following <{strong}>{post_title}'
                              '</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'author_name': 'author name',
            'replier_name': 'replier name',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'content_reported': {
        'notification_app': 'discussion',
        'name': 'content_reported',
        'is_core': False,
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
        'email_template': '',
        'visible_to': [FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA]
    },
    'response_endorsed_on_thread': {
        'notification_app': 'discussion',
        'name': 'response_endorsed_on_thread',
        'is_core': True,
        'info': '',
        'non_editable': [],
        'content_template': _('<{p}><{strong}>{replier_name}\'s</{strong}> response has been endorsed in your post '
                              '<{strong}>{post_title}</{strong}></{p}>'),
        'content_context': {
            'post_title': 'Post title',
            'replier_name': 'replier name',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'response_endorsed': {
        'notification_app': 'discussion',
        'name': 'response_endorsed',
        'is_core': True,
        'info': '',
        'non_editable': [],
        'content_template': _('<{p}>Your response has been endorsed on the post <{strong}>{post_title}</{strong}></{'
                              'p}>'),
        'content_context': {
            'post_title': 'Post title',
        },
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'course_updates': {
        'notification_app': 'updates',
        'name': 'course_updates',
        'is_core': False,
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
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
    'ora_staff_notifications': {
        'notification_app': 'grading',
        'name': 'ora_staff_notifications',
        'is_core': False,
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
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE],
        'visible_to': [CourseStaffRole.ROLE, CourseInstructorRole.ROLE]
    },
    'ora_grade_assigned': {
        'notification_app': 'grading',
        'name': 'ora_grade_assigned',
        'is_core': False,
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
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE],
    },
    'new_instructor_all_learners_post': {
        'notification_app': 'discussion',
        'name': 'new_instructor_all_learners_post',
        'is_core': False,
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
        'email_template': '',
        'filters': [FILTER_AUDIT_EXPIRED_USERS_WITH_NO_ROLE]
    },
}

COURSE_NOTIFICATION_APPS = {
    'discussion': {
        'enabled': True,
        'core_info': _('Notifications for responses and comments on your posts, and the ones you’re '
                       'following, including endorsements to your responses and on your posts.'),
        'core_web': True,
        'core_email': True,
        'core_push': True,
        'core_email_cadence': EmailCadence.DAILY,
        'non_editable': []
    },
    'updates': {
        'enabled': True,
        'core_info': _('Notifications for new announcements and updates from the course team.'),
        'core_web': True,
        'core_email': True,
        'core_push': True,
        'core_email_cadence': EmailCadence.DAILY,
        'non_editable': []
    },
    'grading': {
        'enabled': True,
        'core_info': _('Notifications for submission grading.'),
        'core_web': True,
        'core_email': True,
        'core_push': True,
        'core_email_cadence': EmailCadence.DAILY,
        'non_editable': []
    },
}


class NotificationPreferenceSyncManager:
    """
    Sync Manager for Notification Preferences
    """

    @staticmethod
    def normalize_preferences(preferences):
        """
        Normalizes preferences to reduce depth of structure.
        This simplifies matching of preferences reducing effort to get difference.
        """
        apps = []
        prefs = []
        non_editable = {}
        core_notifications = {}

        for app, app_pref in preferences.items():
            apps.append({
                'name': app,
                'enabled': app_pref.get('enabled')
            })
            for pref_name, pref_values in app_pref.get('notification_types', {}).items():
                prefs.append({
                    'name': pref_name,
                    'app_name': app,
                    **pref_values
                })
            non_editable[app] = app_pref.get('non_editable', {})
            core_notifications[app] = app_pref.get('core_notification_types', [])

        normalized_preferences = {
            'apps': apps,
            'preferences': prefs,
            'non_editable': non_editable,
            'core_notifications': core_notifications,
        }
        return normalized_preferences

    @staticmethod
    def denormalize_preferences(normalized_preferences):
        """
        Denormalizes preference from simplified to normal structure for saving it in database
        """
        denormalized_preferences = {}
        for app in normalized_preferences.get('apps', []):
            app_name = app.get('name')
            app_toggle = app.get('enabled')
            denormalized_preferences[app_name] = {
                'enabled': app_toggle,
                'core_notification_types': normalized_preferences.get('core_notifications', {}).get(app_name, []),
                'notification_types': {},
                'non_editable': normalized_preferences.get('non_editable', {}).get(app_name, {}),
            }

        for preference in normalized_preferences.get('preferences', []):
            pref_name = preference.get('name')
            app_name = preference.get('app_name')
            denormalized_preferences[app_name]['notification_types'][pref_name] = {
                'web': preference.get('web'),
                'push': preference.get('push'),
                'email': preference.get('email'),
                'email_cadence': preference.get('email_cadence'),
            }
        return denormalized_preferences

    @staticmethod
    def update_preferences(preferences, email_opt_out=False):
        """
        Creates a new preference version from old preferences.
        New preference is created instead of updating old preference

        Steps to update existing user preference
            1) Normalize existing user preference
            2) Normalize default preferences
            3) Iterate over all the apps in default preference, if app_name exists in
               existing preference, update new preference app enabled value as
               existing enabled value
            4) Iterate over all preferences, if preference_name exists in existing
               preference, update new preference values of web, email and push as
               existing web, email and push respectively
            5) Denormalize new preference
        """
        old_preferences = NotificationPreferenceSyncManager.normalize_preferences(preferences)
        default_prefs = NotificationAppManager().get_notification_app_preferences(email_opt_out)
        new_prefs = NotificationPreferenceSyncManager.normalize_preferences(default_prefs)

        for app in new_prefs.get('apps'):
            app_pref = find_app_in_normalized_apps(app.get('name'), old_preferences.get('apps'))
            if app_pref:
                app['enabled'] = app_pref['enabled']

        for preference in new_prefs.get('preferences'):
            pref_name = preference.get('name')
            app_name = preference.get('app_name')
            pref = find_pref_in_normalized_prefs(pref_name, app_name, old_preferences.get('preferences'))
            if pref:
                for channel in ['web', 'email', 'push', 'email_cadence']:
                    preference[channel] = pref.get(channel, preference.get(channel))
        return NotificationPreferenceSyncManager.denormalize_preferences(new_prefs)


class NotificationTypeManager:
    """
    Manager for notification types
    """

    def __init__(self):
        self.notification_types = COURSE_NOTIFICATION_TYPES

    def get_notification_types_by_app(self, notification_app):
        """
        Returns notification types for the given notification app.
        """
        return [
            notification_type.copy() for _, notification_type in self.notification_types.items()
            if notification_type.get('notification_app', None) == notification_app
        ]

    def get_core_and_non_core_notification_types(self, notification_app):
        """
        Returns core notification types for the given app name.
        """
        notification_types = self.get_notification_types_by_app(notification_app)
        core_notification_types = []
        non_core_notification_types = []
        for notification_type in notification_types:
            if notification_type.get('is_core', None):
                core_notification_types.append(notification_type)
            else:
                non_core_notification_types.append(notification_type)
        return core_notification_types, non_core_notification_types

    @staticmethod
    def get_non_editable_notification_channels(notification_types):
        """
        Returns non_editable notification channels for the given notification types.
        """
        non_editable_notification_channels = {}
        for notification_type in notification_types:
            if notification_type.get('non_editable', None):
                non_editable_notification_channels[notification_type.get('name')] = \
                    notification_type.get('non_editable')
        return non_editable_notification_channels

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
            'web': notification_app_attrs.get('core_web', False),
            'email': False if email_opt_out else notification_app_attrs.get('core_email', False),
            'push': notification_app_attrs.get('core_push', False),
            'email_cadence': notification_app_attrs.get('core_email_cadence', 'Daily'),
        }

    def add_core_notification_non_editable(self, notification_app_attrs, non_editable_channels):
        """
        Adds non_editable for core notification.
        """
        if notification_app_attrs.get('non_editable', None):
            non_editable_channels['core'] = notification_app_attrs.get('non_editable')

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


def get_notification_content(notification_type, context):
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
