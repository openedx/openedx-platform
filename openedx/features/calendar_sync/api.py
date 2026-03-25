""" API for the Calendar Sync Application """


from .models import UserCalendarSyncConfig  # noqa: I001

SUBSCRIBE = 'subscribe'
UNSUBSCRIBE = 'unsubscribe'


def subscribe_user_to_calendar(user, course_key):
    """
    Enables the Calendar Sync config for a particular user and course.
    Will create if needed.

    Parameters:
        user (User): The user to subscribe
        course_key (CourseKey): The course key for the subscription
    """
    defaults = {'enabled': True}
    UserCalendarSyncConfig.objects.update_or_create(user=user, course_key=course_key, defaults=defaults)


def unsubscribe_user_to_calendar(user, course_key):
    """
    Disables the Calendar Sync config for a particular user and course.
    If the instance does not exist, this function will do nothing.

    Parameters:
        user (User): The user to subscribe
        course_key (CourseKey): The course key for the subscription
    """
    UserCalendarSyncConfig.objects.filter(user=user, course_key=course_key).update(enabled=False)
