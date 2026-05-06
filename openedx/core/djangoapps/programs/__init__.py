"""
Platform support for Programs.

This package is a thin wrapper around interactions with the Programs service,
supporting learner- and author-facing features involving that service
if and only if the service is deployed in the Open edX installation.

To ensure maximum separation of concerns, and a minimum of interdependencies,
this package should be kept small, thin, and stateless.
"""
from edx_toggles.toggles import WaffleSwitch

PROGRAMS_WAFFLE_SWITCH_NAMESPACE = 'programs'

# This is meant to be enabled until https://openedx.atlassian.net/browse/LEARNER-5573 needs to be resolved
ALWAYS_CALCULATE_PROGRAM_PRICE_AS_ANONYMOUS_USER = WaffleSwitch(  # pylint: disable=toggle-missing-annotation
    f'{PROGRAMS_WAFFLE_SWITCH_NAMESPACE}.always_calculate_program_price_as_anonymous_user',
    __name__
)
