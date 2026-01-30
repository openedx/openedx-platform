"""
Views for the notifications API.
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count
from django_ratelimit.core import is_ratelimited
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from zoneinfo import ZoneInfo
from rest_framework import generics, status
from rest_framework.decorators import api_view
from rest_framework.generics import UpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.notifications.email.utils import update_user_preferences_from_patch, username_from_hash
from openedx.core.djangoapps.notifications.models import NotificationPreference
from openedx.core.djangoapps.notifications.permissions import allow_any_authenticated_user

from .base_notification import COURSE_NOTIFICATION_APPS, NotificationAppManager, COURSE_NOTIFICATION_TYPES, \
    NotificationTypeManager, filter_notification_types_by_app
from .events import (
    notification_preference_update_event,
    notification_read_event,
    notification_tray_opened_event,
    notifications_app_all_read_event
)
from .models import Notification
from .serializers import (
    NotificationSerializer,
    UserNotificationPreferenceUpdateAllSerializer,
    add_info_to_notification_config,
    add_non_editable_in_preference
)
from .tasks import create_notification_preference
from .utils import (
    get_show_notifications_tray,
    exclude_inaccessible_preferences, create_account_notification_pref_if_not_exists
)


@allow_any_authenticated_user()
class NotificationListAPIView(generics.ListAPIView):
    """
    API view for listing notifications for a user.

    **Permissions**: User must be authenticated.
    **Response Format** (paginated):

        {
            "results" : [
                {
                    "id": (int) notification_id,
                    "app_name": (str) app_name,
                    "notification_type": (str) notification_type,
                    "content": (str) content,
                    "content_context": (dict) content_context,
                    "content_url": (str) content_url,
                    "last_read": (datetime) last_read,
                    "last_seen": (datetime) last_seen
                },
                ...
            ],
            "count": (int) total_number_of_notifications,
            "next": (str) url_to_next_page_of_notifications,
            "previous": (str) url_to_previous_page_of_notifications,
            "page_size": (int) number_of_notifications_per_page,

        }

    Response Error Codes:
    - 403: The requester cannot access resource.
    """

    serializer_class = NotificationSerializer

    def get_queryset(self):
        """
        Override the get_queryset method to filter the queryset by app name, request.user and created
        """
        expiry_date = datetime.now(ZoneInfo("UTC")) - timedelta(days=settings.NOTIFICATIONS_EXPIRY)
        app_name = self.request.query_params.get('app_name')

        if self.request.query_params.get('tray_opened'):
            unseen_count = Notification.objects.filter(user_id=self.request.user, last_seen__isnull=True).count()
            notification_tray_opened_event(self.request.user, unseen_count)
        params = {
            'user': self.request.user,
            'created__gte': expiry_date,
            'web': True
        }

        if app_name:
            params['app_name'] = app_name
        return Notification.objects.filter(**params).order_by('-created')


@allow_any_authenticated_user()
class NotificationCountView(APIView):
    """
    API view for getting the unseen notifications count and show_notification_tray flag for a user.
    """

    def get(self, request):
        """
        Get the unseen notifications count and show_notification_tray flag for a user.

        **Permissions**: User must be authenticated.
        **Response Format**:
        ```json
        {
            "show_notifications_tray": (bool) show_notifications_tray,
            "count": (int) total_number_of_unseen_notifications,
            "count_by_app_name": {
                (str) app_name: (int) number_of_unseen_notifications,
                ...
            },
            "notification_expiry_days": 60
        }
        ```
        **Response Error Codes**:
        - 403: The requester cannot access resource.
        """
        # Get the unseen notifications count for each app name.
        count_by_app_name = (
            Notification.objects
            .filter(user_id=request.user, last_seen__isnull=True, web=True)
            .values('app_name')
            .annotate(count=Count('*'))
        )
        count_total = 0
        show_notifications_tray = get_show_notifications_tray()
        count_by_app_name_dict = {
            app_name: 0
            for app_name in COURSE_NOTIFICATION_APPS
        }

        for item in count_by_app_name:
            app_name = item['app_name']
            count = item['count']
            count_total += count
            count_by_app_name_dict[app_name] = count

        return Response({
            "show_notifications_tray": show_notifications_tray,
            "count": count_total,
            "count_by_app_name": count_by_app_name_dict,
            "notification_expiry_days": settings.NOTIFICATIONS_EXPIRY,
        })


@allow_any_authenticated_user()
class MarkNotificationsSeenAPIView(UpdateAPIView):
    """
    API view for marking user's all notifications seen for a provided app_name.
    """

    def update(self, request, *args, **kwargs):
        """
        Marks all notifications for the given app name seen for the authenticated user.

        **Args:**
            app_name: The name of the app to mark notifications seen for.
        **Response Format:**
            A `Response` object with a 200 OK status code if the notifications were successfully marked seen.
        **Response Error Codes**:
        - 400: Bad Request status code if the app name is invalid.
        """
        app_name = self.kwargs.get('app_name')

        if not app_name:
            return Response({'error': _('Invalid app name.')}, status=400)

        notifications = Notification.objects.filter(
            user=request.user,
            app_name=app_name,
            last_seen__isnull=True,
        )

        notifications.update(last_seen=datetime.now())

        return Response({'message': _('Notifications marked as seen.')}, status=200)


@allow_any_authenticated_user()
class NotificationReadAPIView(APIView):
    """
    API view for marking user notifications as read, either all notifications or a single notification
    """

    def patch(self, request, *args, **kwargs):
        """
        Marks all notifications or single notification read for the given
        app name or notification id for the authenticated user.

        Requests:
        PATCH /api/notifications/read/

        Parameters:
            request (Request): The request object containing the app name or notification id.
                {
                    "app_name": (str) app_name,
                    "notification_id": (int) notification_id
                }

        Returns:
        - 200: OK status code if the notification or notifications were successfully marked read.
        - 400: Bad Request status code if the app name is invalid.
        - 403: Forbidden status code if the user is not authenticated.
        - 404: Not Found status code if the notification was not found.
        """
        notification_id = request.data.get('notification_id', None)
        read_at = datetime.now(ZoneInfo("UTC"))

        if notification_id:
            notification = get_object_or_404(Notification, pk=notification_id, user=request.user)
            first_time_read = notification.last_read is None
            notification.last_read = read_at
            notification.save()
            notification_read_event(request.user, notification, first_time_read)
            return Response({'message': _('Notification marked read.')}, status=status.HTTP_200_OK)

        app_name = request.data.get('app_name', '')

        if app_name in COURSE_NOTIFICATION_APPS:
            notifications = Notification.objects.filter(
                user=request.user,
                app_name=app_name,
                last_read__isnull=True,
            )
            notifications.update(last_read=read_at)
            notifications_app_all_read_event(request.user, app_name)
            return Response({'message': _('Notifications marked read.')}, status=status.HTTP_200_OK)

        return Response({'error': _('Invalid app_name or notification_id.')}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'POST'])
def preference_update_from_encrypted_username_view(request, username, patch=""):
    """
    View to update user preferences from encrypted username and patch.
    username and patch must be string
    """
    if is_ratelimited(
        request=request, group="unsubscribe", key=username_from_hash,
        rate=settings.ONE_CLICK_UNSUBSCRIBE_RATE_LIMIT, increment=True,
    ):
        return Response({"error": "Too many requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    update_user_preferences_from_patch(username)
    return Response({"result": "success"}, status=status.HTTP_200_OK)


@allow_any_authenticated_user()
class NotificationPreferencesView(APIView):
    """
    API view to retrieve and structure the notification preferences for the
    authenticated user.
    """

    def get(self, request):
        """
        Handles GET requests to retrieve notification preferences.

        This method fetches the user's active notification preferences and
        merges them with a default structure provided by NotificationAppManager.
        This provides a complete view of all possible notifications and the
        user's current settings for them.

        Returns:
            Response: A DRF Response object containing the structured
                      notification preferences or an error message.
        """
        user_preferences_qs = NotificationPreference.objects.filter(user=request.user)
        user_preferences_map = {pref.type: pref for pref in user_preferences_qs}

        # Ensure all notification types are present in the user's preferences.
        # If any are missing, create them with default values.
        diff = set(COURSE_NOTIFICATION_TYPES.keys()) - set(user_preferences_map.keys())
        missing_types = []
        for missing_type in diff:
            new_pref = create_notification_preference(
                user_id=request.user.id,
                notification_type=missing_type,

            )
            missing_types.append(new_pref)
            user_preferences_map[missing_type] = new_pref
        if missing_types:
            NotificationPreference.objects.bulk_create(missing_types)

        # If no user preferences are found, return an error response.
        if not user_preferences_map:
            return Response({
                'status': 'error',
                'message': 'No active notification preferences found for this user.'
            }, status=status.HTTP_404_NOT_FOUND)

        # Get the structured preferences from the NotificationAppManager.
        # This will include all apps and their notification types.
        structured_preferences = NotificationAppManager().get_notification_app_preferences()

        for app_name, app_settings in structured_preferences.items():
            notification_types = app_settings.get('notification_types', {})

            # Process all notification types (core and non-core) in a single loop.
            for type_name, type_details in notification_types.items():
                if type_name == 'core':
                    if structured_preferences[app_name]['core_notification_types']:
                        # If the app has core notification types, use the first one as the type name.
                        # This assumes that the first core notification type is representative of the core settings.
                        notification_type = structured_preferences[app_name]['core_notification_types'][0]
                    else:
                        notification_type = 'core'
                    user_pref = user_preferences_map.get(notification_type)
                else:
                    user_pref = user_preferences_map.get(type_name)
                if user_pref:
                    # If a preference exists, update the dictionary for this type.
                    # This directly modifies the 'type_details' dictionary.
                    type_details['web'] = user_pref.web
                    type_details['email'] = user_pref.email
                    type_details['push'] = user_pref.push
                    type_details['email_cadence'] = user_pref.email_cadence
        exclude_inaccessible_preferences(structured_preferences, request.user)
        structured_preferences = add_non_editable_in_preference(
            add_info_to_notification_config(structured_preferences)
        )
        return Response({
            'status': 'success',
            'message': 'Notification preferences retrieved successfully.',
            'show_preferences': get_show_notifications_tray(),
            'data': structured_preferences
        }, status=status.HTTP_200_OK)

    def put(self, request):
        """
        Handles PUT requests to update notification preferences.

        This method updates the user's notification preferences based on the
        provided data in the request body. It expects a dictionary with
        notification types and their settings.

        Returns:
            Response: A DRF Response object indicating success or failure.
        """
        # Validate incoming data
        serializer = UserNotificationPreferenceUpdateAllSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                'status': 'error',
                'message': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        # Get validated data for easier access
        validated_data = serializer.validated_data

        # Build query set based on notification type
        query_set = NotificationPreference.objects.filter(user_id=request.user.id)

        if validated_data['notification_type'] == 'core':
            # Get core notification types for the app
            __, core_types = NotificationTypeManager().get_notification_app_preference(
                notification_app=validated_data['notification_app']
            )
            query_set = query_set.filter(type__in=core_types)
        else:
            # Filter by single notification type
            query_set = query_set.filter(type=validated_data['notification_type'])

        # Prepare update data based on channel type
        updated_data = self._prepare_update_data(validated_data)

        # Update preferences
        query_set.update(**updated_data)

        # Log the event
        self._log_preference_update_event(request.user, validated_data)

        # Prepare and return response
        response_data = self._prepare_response_data(validated_data)
        return Response(response_data, status=status.HTTP_200_OK)

    def _prepare_update_data(self, validated_data):
        """
        Prepare the data dictionary for updating notification preferences.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Dictionary with update data
        """
        channel = validated_data['notification_channel']

        if channel == 'email_cadence':
            return {channel: validated_data['email_cadence']}
        else:
            return {channel: validated_data['value']}

    def _log_preference_update_event(self, user, validated_data):
        """
        Log the notification preference update event.

        Args:
            user: The user making the update
            validated_data (dict): Validated serializer data
        """
        event_data = {
            'notification_app': validated_data['notification_app'],
            'notification_type': validated_data['notification_type'],
            'notification_channel': validated_data['notification_channel'],
            'value': validated_data.get('value'),
            'email_cadence': validated_data.get('email_cadence'),
        }
        notification_preference_update_event(user, [], event_data)

    def _prepare_response_data(self, validated_data):
        """
        Prepare the response data dictionary.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Response data dictionary
        """
        email_cadence = validated_data.get('email_cadence', None)
        # Determine the updated value
        updated_value = validated_data.get('value', email_cadence if email_cadence else None)

        # Determine the channel
        channel = validated_data.get('notification_channel')
        if not channel and validated_data.get('email_cadence'):
            channel = 'email_cadence'

        return {
            'status': 'success',
            'message': 'Notification preferences update completed',
            'show_preferences': get_show_notifications_tray(),
            'data': {
                'updated_value': updated_value,
                'notification_type': validated_data['notification_type'],
                'channel': channel,
                'app': validated_data['notification_app'],
            }
        }


@allow_any_authenticated_user()
class NotificationPreferencesViewV3(APIView):
    """
    API view to retrieve and structure the notification preferences for the
    authenticated user.
    """

    def get(self, request):
        """
        Handles GET requests to retrieve notification preferences.

        This method fetches the user's active notification preferences and
        merges them with a default structure provided by NotificationAppManager.
        This provides a complete view of all possible notifications and the
        user's current settings for them.

        Returns:
            Response: A DRF Response object containing the structured
                      notification preferences or an error message.
        """
        user_preferences_qs = NotificationPreference.objects.filter(user=request.user)

        # Ensure all notification types are present in the user's preferences.
        user_preferences_qs = create_account_notification_pref_if_not_exists(
            user_ids=[request.user.id],
            existing_preferences=user_preferences_qs,
            notification_types=COURSE_NOTIFICATION_TYPES.keys()
        )
        structured_preferences = {
            app_name: {
                'notification_types': {},
                'enabled': COURSE_NOTIFICATION_APPS[app_name].get('enabled', True),
                'non_editable': []

            } for app_name in COURSE_NOTIFICATION_APPS.keys()}

        for user_preference in user_preferences_qs:
            app_name = user_preference.app
            type_name = user_preference.type

            if user_preference.is_grouped:
                structured_preferences[app_name]['notification_types']['grouped_notification'] = {
                    **user_preference.config
                }
                continue

            structured_preferences[app_name]['notification_types'][type_name] = {**user_preference.config}

        exclude_inaccessible_preferences(structured_preferences, request.user)
        structured_preferences = add_non_editable_in_preference(structured_preferences)

        return Response({
            'status': 'success',
            'message': 'Notification preferences retrieved successfully.',
            'show_preferences': get_show_notifications_tray(),
            'data': structured_preferences
        }, status=status.HTTP_200_OK)

    def put(self, request):
        """
        Handles PUT requests to update notification preferences.

        This method updates the user's notification preferences based on the
        provided data in the request body. It expects a dictionary with
        notification types and their settings.

        Returns:
            Response: A DRF Response object indicating success or failure.
        """
        # Validate incoming data
        serializer = UserNotificationPreferenceUpdateAllSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                'status': 'error',
                'message': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        # Get validated data for easier access
        validated_data = serializer.validated_data

        # Build query set based on notification type
        query_set = NotificationPreference.objects.filter(user_id=request.user.id)

        if validated_data['notification_type'] == 'grouped_notification':
            # Get core notification types for the app
            grouped_types = filter_notification_types_by_app(validated_data['notification_app'], use_app_defaults=True)
            query_set = query_set.filter(type__in=grouped_types.keys())
        else:
            # Filter by single notification type
            query_set = query_set.filter(type=validated_data['notification_type'])

        # Prepare update data based on channel type
        updated_data = self._prepare_update_data(validated_data)

        # Update preferences
        query_set.update(**updated_data)

        # Log the event
        self._log_preference_update_event(request.user, validated_data)

        # Prepare and return response
        response_data = self._prepare_response_data(validated_data)
        return Response(response_data, status=status.HTTP_200_OK)

    def _prepare_update_data(self, validated_data):
        """
        Prepare the data dictionary for updating notification preferences.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Dictionary with update data
        """
        channel = validated_data['notification_channel']

        if channel == 'email_cadence':
            return {channel: validated_data['email_cadence']}
        else:
            return {channel: validated_data['value']}

    def _log_preference_update_event(self, user, validated_data):
        """
        Log the notification preference update event.

        Args:
            user: The user making the update
            validated_data (dict): Validated serializer data
        """
        event_data = {
            'notification_app': validated_data['notification_app'],
            'notification_type': validated_data['notification_type'],
            'notification_channel': validated_data['notification_channel'],
            'value': validated_data.get('value'),
            'email_cadence': validated_data.get('email_cadence'),
        }
        notification_preference_update_event(user, [], event_data)

    def _prepare_response_data(self, validated_data):
        """
        Prepare the response data dictionary.

        Args:
            validated_data (dict): Validated serializer data

        Returns:
            dict: Response data dictionary
        """
        email_cadence = validated_data.get('email_cadence', None)
        # Determine the updated value
        updated_value = validated_data.get('value', email_cadence if email_cadence else None)

        # Determine the channel
        channel = validated_data.get('notification_channel')
        if not channel and validated_data.get('email_cadence'):
            channel = 'email_cadence'

        return {
            'status': 'success',
            'message': 'Notification preferences update completed',
            'show_preferences': get_show_notifications_tray(),
            'data': {
                'updated_value': updated_value,
                'notification_type': validated_data['notification_type'],
                'channel': channel,
                'app': validated_data['notification_app'],
            }
        }
