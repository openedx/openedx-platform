"""
Settings for ace_common app.
"""
from openedx.core.djangoapps.ace_common.utils import setup_firebase_app

ACE_ROUTING_KEY = 'edx.lms.core.default'


def plugin_settings(settings):  # pylint: disable=missing-function-docstring, missing-module-docstring
    if 'push_notifications' not in settings.INSTALLED_APPS:
        settings.INSTALLED_APPS.append('push_notifications')
    settings.ACE_ENABLED_CHANNELS = [
        'django_email'
    ]
    settings.ACE_ENABLED_POLICIES = [
        'bulk_email_optout'
    ]
    settings.ACE_CHANNEL_SAILTHRU_DEBUG = True
    settings.ACE_CHANNEL_SAILTHRU_TEMPLATE_NAME = 'Automated Communication Engine Email'
    settings.ACE_CHANNEL_SAILTHRU_API_KEY = None
    settings.ACE_CHANNEL_SAILTHRU_API_SECRET = None
    settings.ACE_CHANNEL_DEFAULT_EMAIL = 'django_email'
    settings.ACE_CHANNEL_TRANSACTIONAL_EMAIL = 'django_email'

    settings.ACE_ROUTING_KEY = ACE_ROUTING_KEY

    settings.test_django_plugin = True
    settings.FCM_APP_NAME = 'fcm-edx-platform'

    settings.ACE_CHANNEL_DEFAULT_PUSH = 'push_notification'
    # Note: To local development with Firebase, you must set FIREBASE_CREDENTIALS_PATH
    # (path to json file with FIREBASE_CREDENTIALS)
    # or FIREBASE_CREDENTIALS dictionary.
    settings.FIREBASE_CREDENTIALS_PATH = None
    settings.FIREBASE_CREDENTIALS = None

    settings.FIREBASE_APP = setup_firebase_app(
        settings.FIREBASE_CREDENTIALS_PATH or settings.FIREBASE_CREDENTIALS, settings.FCM_APP_NAME
    )

    if getattr(settings, 'FIREBASE_APP', None):
        settings.ACE_ENABLED_CHANNELS.append(settings.ACE_CHANNEL_DEFAULT_PUSH)
        settings.ACE_ENABLED_POLICIES.append('course_push_notification_optout')

        settings.PUSH_NOTIFICATIONS_SETTINGS = {
            'CONFIG': 'push_notifications.conf.AppConfig',
            'APPLICATIONS': {
                settings.FCM_APP_NAME: {
                    'PLATFORM': 'FCM',
                    'FIREBASE_APP': settings.FIREBASE_APP,
                },
            },
            'UPDATE_ON_DUPLICATE_REG_ID': True,
        }
