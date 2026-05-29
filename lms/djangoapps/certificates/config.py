"""
This module contains various configuration settings via
waffle switches for the Certificates app.
"""
from edx_toggles.toggles import WaffleFlag, WaffleSwitch

# Namespace
WAFFLE_NAMESPACE = 'certificates'

# .. toggle_name: certificates.auto_certificate_generation
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: This toggle will enable certificates to be automatically generated
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-09-14
AUTO_CERTIFICATE_GENERATION = WaffleSwitch(f"{WAFFLE_NAMESPACE}.auto_certificate_generation", __name__)

# .. toggle_name: certificates.enable_historical_pii_retirement
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Clears the `name` field in the django-simple-history audit table for
#      retiring users' certificate records.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2026-05-29
ENABLE_HISTORICAL_PII_RETIREMENT = WaffleFlag(
    f"{WAFFLE_NAMESPACE}.enable_historical_pii_retirement", __name__
)
