"""
Configuration toggles for ManualVerification.
"""

from edx_toggles.toggles import SettingToggle

# .. toggle_name: REDACT_MANUAL_VERIFICATION_HISTORICAL_PII
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Clears the `name` field for `ManualVerification` records
#      before deleting those rows during user retirement.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2026-07-15
REDACT_MANUAL_VERIFICATION_HISTORICAL_PII = SettingToggle(
    'REDACT_MANUAL_VERIFICATION_HISTORICAL_PII', default=False, module_name=__name__
)
