### How to override default notification preferences

This document explains how to override notification preferences defaults for new users who sign up on the platform. Learn more about notification, preferences and defaults here: https://docs.openedx.org/en/latest/learners/sfd_notifications/index.html

**Note:** These overrides will only apply to new users who sign up after the override has been configured.

### Definitions

**Notification:** An alert that goes out to the user as a result of some activity e.g. "Mark responded to your post" etc.

**Notification Preference:** A per-user setting for each notification type (web, email etc.). This is explained in the table below.

**Notification apps:** For organization purposes, notification preferences are grouped by apps/platform workflows that they are related to. As of now, we have 3 apps: discussions, grading, and updates. Note that there is no app-level on/off switch for notifications types in it.

**App-level defaults / Bundled preferences:** Each app discussed above has a set of app-level defaults (bundled preferences). A notification type marked `use_app_defaults: True` inherits its preferences from the app's app-level defaults instead of using its own default preferences. This enables grouping several notification types under one row of preferences visible to the user. For example, in case of discussions, 7 notification types are controlled by the app-level defaults of the discussions app, which appears on the Account Settings page as a single row labeled "Activity Notifications". This reduces clutter on the settings page.

### What you can override

The table below lists all the preferences that you can override for each notification along with possible values. You can override defaults for both using the following dictionaries in your `lms.yml`, `cms.yml`, or `settings.py`:

- Use `NOTIFICATION_TYPES_OVERRIDE` to override defaults for individual notification types.
- Use `NOTIFICATION_APPS_OVERRIDE` to override app-level defaults for notification types marked as `use_app_defaults: True`. Use the internal keys listed below (`web`, `email`, `push`, `email_cadence`, `non_editable`).

| Key | Type | Possible values | What it does |
|-----|------|-----------------|--------------|
| web | bool | True OR False | Determines if the user gets notifications in the tray. |
| email | bool | True OR False | Determines if the user gets notification in email. |
| push | bool | True OR False | Determines if user gets push notification in mobile apps (not implemented) |
| email_cadence | string | 'Immediately' OR 'Daily' OR 'Weekly' OR 'Never' | Determines when a user receives email notification. |
| non_editable | list of strings | Any subset of ['web','email','push'] e.g. ['email', 'web'] | Determines toggles of which of the 3 channels will not be editable by the user. |

Notification keys are listed in the table below. More notifications may be added in the future. You can find notification keys in this code: https://github.com/openedx/edx-platform/blob/2aeac459945e3e11c153fdb5203ea020514548d5/openedx/core/djangoapps/notifications/base_notification.py#L66

| # | Notification app | Notification key | uses app defaults | Appears on Account Settings page as |
|---|------------------|------------------|-------------------|-------------------------------------|
| 1 | discussion | new_response | True              | Activity Notifications |
| 2 | discussion | new_comment | True              | Activity Notifications |
| 3 | discussion | new_comment_on_response | True              | Activity Notifications |
| 4 | discussion | response_on_followed_post | True              | Activity Notifications |
| 5 | discussion | comment_on_followed_post | True              | Activity Notifications |
| 6 | discussion | response_endorsed_on_thread | True              | Activity Notifications |
| 7 | discussion | response_endorsed | True              | Activity Notifications |
| 8 | discussion | content_reported | False             | Reported content |
| 9 | discussion | new_question_post | False             | New question posts |
| 10 | discussion | new_discussion_post | False             | New discussion posts |
| 11 | discussion | new_instructor_all_learners_post | False             | New posts from instructors |
| 12 | updates | course_updates | False             | Course updates |
| 13 | grading | ora_staff_notifications | False             | New ORA submission for staff |
| 14 | grading | ora_grade_assigned | False             | ORA grade received |

### Example configuration for overriding notification preferences:

```python
NOTIFICATION_TYPES_OVERRIDE = {
    # Turn off tray and turn on email notifications for new discussion posts and set daily cadence.
    'new_discussion_post': {
        'email': True,
        'web': False,
        'email_cadence': 'Daily',
    },
    # Turn off email notifications for "Course Updates" and prevent users from changing it.
    'course_updates': {
        'email': False,
        'non_editable': ['email'],
    }
}
```

### Example configuration for overriding app-level defaults for notifications marked as use_app_defaults: True

```python
NOTIFICATION_APPS_OVERRIDE = {
    # For the 'discussion' app, set tray on and email off for all app-default notifications.
    'discussion': {
        'email': False,
        'web': True,
        'email_cadence': 'Immediately',
    }
}
```

### Why isn't my override working?

- See if you are using the exact key name (e.g. `new_discussion_post` and not `New_discussion_post`).
- If a notification is marked as `use_app_defaults: True` in the code, it will ignore overrides in `NOTIFICATION_TYPES_OVERRIDE`. You must override using `NOTIFICATION_APPS_OVERRIDE` instead.
