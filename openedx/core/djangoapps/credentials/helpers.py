"""
Helpers for the credentials service.
"""

from edx_toggles.toggles import SettingToggle

from openedx.core.djangoapps.site_configuration import helpers as config_helpers

# .. toggle_name: ENABLE_LEARNER_RECORDS
# .. toggle_implementation: SettingToggle
# .. toggle_default: True
# .. toggle_description: Enable learner records for the whole platform. This setting may be overridden by site- and
#   org-specific site configurations with the same name.
# .. toggle_warning: Enabling this feature requires that the definition of the ``CREDENTIALS_PUBLIC_SERVICE_URL``
#   setting.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-10-01
ENABLE_LEARNER_RECORDS = SettingToggle(
    "ENABLE_LEARNER_RECORDS", default=True, module_name=__name__
)


def is_learner_records_enabled():
    return config_helpers.get_value(
        "ENABLE_LEARNER_RECORDS", ENABLE_LEARNER_RECORDS.is_enabled()
    )


def is_learner_records_enabled_for_org(org):
    return config_helpers.get_value_for_org(
        org, "ENABLE_LEARNER_RECORDS", ENABLE_LEARNER_RECORDS.is_enabled()
    )
