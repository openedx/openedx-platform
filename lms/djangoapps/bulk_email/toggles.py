"""
Toggles for bulk_email app
"""

from edx_toggles.toggles import SettingToggle

# .. toggle_name: bulk_email.EMAIL_USE_COURSE_ID_FROM_FOR_BULK
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If False, use the same BULK_EMAIL_DEFAULT_FROM_EMAIL or DEFAULT_FROM_EMAIL as the from_addr for all bulk email, to avoid issues with spam filtering
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-10-01
# .. toggle_tickets: OSPR-4957


def is_email_use_course_id_from_for_bulk_enabled():
    return SettingToggle("EMAIL_USE_COURSE_ID_FROM_FOR_BULK", default=False).is_enabled()

# .. toggle_name: BULK_EMAIL_SEND_USING_EDX_ACE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If True, use edx-ace to send bulk email messages
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-02-10
# .. toggle_tickets: https://github.com/openedx/build-test-release-wg/issues/100


def is_bulk_email_edx_ace_enabled():
    return SettingToggle("BULK_EMAIL_SEND_USING_EDX_ACE", default=False).is_enabled()
