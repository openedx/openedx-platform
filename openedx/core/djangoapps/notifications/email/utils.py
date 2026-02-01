"""
Email Notifications Utils
"""
import datetime

from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import BadRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from pytz import utc
from waffle import get_waffle_flag_model  # pylint: disable=invalid-django-waffle-import

from lms.djangoapps.branding.api import get_logo_url_for_email
from lms.djangoapps.discussion.notification_prefs.views import UsernameCipher, UsernameDecryptionException
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.notifications.base_notification import (
    COURSE_NOTIFICATION_TYPES,
    get_default_values_of_preferences
)
from openedx.core.djangoapps.notifications.config.waffle import ENABLE_EMAIL_NOTIFICATIONS
from openedx.core.djangoapps.notifications.email import ONE_CLICK_EMAIL_UNSUB_KEY
from openedx.core.djangoapps.notifications.email_notifications import EmailCadence
from openedx.core.djangoapps.notifications.events import notification_preference_unsubscribe_event
from openedx.core.djangoapps.notifications.models import NotificationPreference
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from xmodule.modulestore.django import modulestore

from .notification_icons import NotificationTypeIcons
from ..utils import create_account_notification_pref_if_not_exists

User = get_user_model()


def is_email_notification_flag_enabled(user=None):
    """
    Returns if waffle flag is enabled for user or not
    """
    flag_model = get_waffle_flag_model()
    try:
        flag = flag_model.objects.get(name=ENABLE_EMAIL_NOTIFICATIONS.name)
    except flag_model.DoesNotExist:
        return False
    if flag.everyone is not None:
        return flag.everyone
    if user:
        role_value = flag.is_active_for_user(user)
        if role_value is not None:
            return role_value
        try:
            return flag.users.contains(user)
        except ValueError:
            pass
    return False


def create_datetime_string(datetime_instance):
    """
    Returns string for datetime object
    """
    return datetime_instance.strftime('%A, %b %d')


def get_icon_url_for_notification_type(notification_type):
    """
    Returns icon url for notification type
    """
    return NotificationTypeIcons.get_icon_url_for_notification_type(notification_type)


def get_unsubscribe_link(username):
    """
    Returns unsubscribe url for username with patch preferences
    """
    encrypted_username = encrypt_string(username)
    learning_microfrontend_url = configuration_helpers.get_value(
        'LEARNING_MICROFRONTEND_URL',
        settings.LEARNING_MICROFRONTEND_URL,
    )

    return f'{learning_microfrontend_url}/preferences-unsubscribe/{encrypted_username}/'


def create_email_template_context(username):
    """
    Creates email context for header and footer
    """
    social_media_urls = settings.SOCIAL_MEDIA_FOOTER_ACE_URLS
    social_media_icons = settings.SOCIAL_MEDIA_LOGO_URLS
    social_media_info = {
        social_platform: {
            'url': social_media_urls[social_platform],
            'icon': social_media_icons[social_platform]
        }
        for social_platform in social_media_urls.keys()
        if social_media_icons.get(social_platform)
    }
    patch = {
        'channel': 'email',
        'value': False
    }
    account_base_url = (settings.ACCOUNT_MICROFRONTEND_URL or "").rstrip('/')
    return {
        "platform_name": settings.PLATFORM_NAME,
        "mailing_address": settings.CONTACT_MAILING_ADDRESS,
        "logo_url": get_logo_url_for_email(),
        "logo_notification_cadence_url": settings.NOTIFICATION_DIGEST_LOGO,
        "social_media": social_media_info,
        "notification_settings_url": f"{account_base_url}/#notifications",
        "unsubscribe_url": get_unsubscribe_link(username)
    }


def create_email_digest_context(app_notifications_dict, username, start_date, end_date=None, digest_frequency="Daily",
                                courses_data=None):
    """
    Creates email context based on content
    app_notifications_dict: Mapping of notification app and its count, title and notifications
    start_date: datetime instance
    end_date: datetime instance
    digest_frequency: EmailCadence.DAILY or EmailCadence.WEEKLY
    courses_data: Dictionary to cache course info (avoid additional db calls)
    """
    context = create_email_template_context(username)
    start_date_str = create_datetime_string(start_date)
    end_date_str = create_datetime_string(end_date if end_date else start_date)
    email_digest_updates = [
        {
            'title': value['title'],
            'count': value['count'],
            'translated_title': value.get('translated_title', value['title']),
        }
        for key, value in app_notifications_dict.items()
    ]
    lookup = {
        'Updates': 1,
        'Grading': 2,
        'Discussion': 3,
    }
    email_digest_updates.sort(key=lambda x: lookup.get(x['title'], 4), reverse=False)
    email_digest_updates.append({
        'title': 'Total Notifications',
        'translated_title': _('Total Notifications'),
        'count': sum(value['count'] for value in app_notifications_dict.values())
    })

    email_content = []
    notifications_in_app = 5

    learner_home_microfrontend_url = configuration_helpers.get_value(
        'LEARNER_HOME_MICROFRONTEND_URL',
        settings.LEARNER_HOME_MICROFRONTEND_URL,
    )

    for key, value in app_notifications_dict.items():
        total = value['count']
        app_content = {
            'title': value['title'],
            'translated_title': value.get('translated_title', value['title']),
            'help_text': value.get('help_text', ''),
            'help_text_url': value.get('help_text_url', ''),
            'notifications': add_additional_attributes_to_notifications(
                value.get('notifications', []), courses_data=courses_data
            ),
            'total': total,
            'show_remaining_count': False,
            'remaining_count': 0,
            'url': f'{learner_home_microfrontend_url}/?showNotifications=true&app={key}',
        }
        if total > notifications_in_app:
            app_content['notifications'] = app_content['notifications'][:notifications_in_app]
            app_content['show_remaining_count'] = True
            app_content['remaining_count'] = total - notifications_in_app
        email_content.append(app_content)

    context.update({
        "start_date": start_date_str,
        "end_date": end_date_str,
        "digest_frequency": digest_frequency,
        "email_digest_updates": email_digest_updates,
        "email_content": email_content,
    })
    return context


def add_headers_to_email_message(message, context):
    """
    Add headers to email message
    """
    if context.get('unsubscribe_url'):
        message.headers['List-Unsubscribe-Post'] = f"<{context['unsubscribe_url']}>"
        message.headers['List-Unsubscribe'] = f"<{context['unsubscribe_url']}>"
    return message


def get_start_end_date(cadence_type):
    """
    Returns start_date and end_date for email digest
    """
    if cadence_type not in [EmailCadence.DAILY, EmailCadence.WEEKLY]:
        raise ValueError('Invalid cadence_type')
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=1, minutes=15)
    if cadence_type == EmailCadence.WEEKLY:
        start_date = start_date - datetime.timedelta(days=6)
    return utc.localize(start_date), utc.localize(end_date)


def get_course_info(course_key):
    """
    Returns course info for course_key
    """
    store = modulestore()
    course = store.get_course(course_key)
    return {'name': course.display_name}


def get_time_ago(datetime_obj):
    """
    Returns time_ago for datetime instance
    """
    current_date = utc.localize(datetime.datetime.today())
    days_diff = (current_date - datetime_obj).days
    if days_diff == 0:
        return _("Today")
    if days_diff >= 7:
        return f"{int(days_diff / 7)}w"
    return f"{days_diff}d"


def add_zero_margin_to_root(html_string):
    """
    Adds to zero margin to root element of html string
    """
    soup = BeautifulSoup(html_string, 'html.parser')
    element = soup.find()
    if not element:
        return html_string
    element['style'] = "margin: 0;"
    return str(soup)


def add_additional_attributes_to_notifications(notifications, courses_data=None):
    """
    Add attributes required for email content to notification instance
    notifications: list[Notification]
    course_data: Cache course info
    """
    if courses_data is None:
        courses_data = {}

    for notification in notifications:
        notification_type = notification.notification_type
        course_key = notification.course_id
        course_key_str = str(course_key)
        if course_key_str not in courses_data.keys():
            courses_data[course_key_str] = get_course_info(course_key)
        course_info = courses_data[course_key_str]
        notification.course_name = course_info.get('name', '')
        notification.icon = get_icon_url_for_notification_type(notification_type)
        notification.time_ago = get_time_ago(notification.created)
        notification.email_content = add_zero_margin_to_root(notification.content)
        notification.details = add_zero_margin_to_root(notification.content_context.get('email_content', ''))
        notification.view_text = get_text_for_notification_type(notification_type)
    return notifications


def create_app_notifications_dict(notifications):
    """
    Return a dictionary with notification app as key and
    title, count and notifications as its value
    """
    app_names = list({notification.app_name for notification in notifications})
    app_notifications = {
        name: {
            'count': 0,
            'title': name.title(),
            'translated_title': get_translated_app_title(name),
            'notifications': []
        }
        for name in app_names
    }
    for notification in notifications:
        app_data = app_notifications[notification.app_name]
        app_data['count'] += 1
        app_data['notifications'].append(notification)
    return app_notifications


def get_unique_course_ids(notifications):
    """
    Returns unique course_ids from notifications
    """
    course_ids = []
    for notification in notifications:
        if notification.course_id not in course_ids:
            course_ids.append(notification.course_id)
    return course_ids


def filter_email_enabled_notifications(notifications, preferences, user, cadence_type=EmailCadence.DAILY):
    """
    Filter notifications with email enabled in account level preferences
    """
    preferences = create_account_notification_pref_if_not_exists(
        [user.id],
        preferences,
        [notification.notification_type for notification in notifications]
    )
    enabled_course_prefs = [
        preference.type
        for preference in preferences
        if preference.email and preference.email_cadence == cadence_type
    ]
    filtered_notifications = []
    for notification in notifications:
        if notification.notification_type in enabled_course_prefs:
            filtered_notifications.append(notification)
    filtered_notifications.sort(key=lambda elem: elem.created, reverse=True)
    return filtered_notifications


def encrypt_string(string):
    """
    Encrypts input string
    """
    return UsernameCipher.encrypt(string)


def decrypt_string(string):
    """
    Decrypts input string
    """
    return UsernameCipher.decrypt(string).decode()


def username_from_hash(group, request):
    """
    Django ratelimit key to return username from hash
    """
    username = request.resolver_match.kwargs.get("username")
    if username:
        try:
            return decrypt_string(username)
        except UsernameDecryptionException as exc:
            raise BadRequest("Bad request") from exc
    return None


def update_user_preferences_from_patch(encrypted_username):
    """
    Decrypt username and patch and updates user preferences
    Allowed parameters for decrypted patch
        app_name: name of app
        notification_type: notification type name
        channel: channel name ('web', 'push', 'email')
        value: True or False
        course_id: course key string
    """
    username = decrypt_string(encrypted_username)
    user = get_object_or_404(User, username=username)

    NotificationPreference.create_default_preferences_for_user(user.id)

    updated_count = NotificationPreference.objects.filter(user=user, email=True).update(email=False)
    is_preference_updated = updated_count > 0

    notification_preference_unsubscribe_event(user, is_preference_updated)
    UserPreference.objects.get_or_create(user_id=user.id, key=ONE_CLICK_EMAIL_UNSUB_KEY)


def is_notification_type_channel_editable(notification_type, channel):
    """
    Returns if notification type channel is editable
    """
    default_preferences = get_default_values_of_preferences()
    return channel not in default_preferences.get(notification_type, {}).get('non_editable', [])


def get_translated_app_title(name):
    """
    Returns translated string from notification app_name key
    """
    mapping = {
        'discussion': _('Discussion'),
        'updates': _('Updates'),
        'grading': _('Grades'),
    }
    return mapping.get(name, '')


def get_language_preference_for_users(user_ids):
    """
    Returns mapping of user_id and language preference for users
    """
    prefs = UserPreference.get_preference_for_users(user_ids, LANGUAGE_KEY)
    return {pref.user_id: pref.value for pref in prefs}


def get_text_for_notification_type(notification_type):
    """
    Returns text for notification type
    """
    app_name = COURSE_NOTIFICATION_TYPES.get(notification_type, {}).get('notification_app')
    if not app_name:
        return ""
    mapping = {
        'discussion': _('discussion'),
        'updates': _('update'),
        'grading': _('assessment'),
    }
    return mapping.get(app_name, "")
