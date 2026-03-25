"""
Toggles for Dashboard page.
"""
from edx_toggles.toggles import WaffleFlag, WaffleSwitch  # noqa: I001

# Namespace for student waffle flags.
WAFFLE_FLAG_NAMESPACE = 'student'


# Waffle flag to enable course enrollment confirmation email.
# .. toggle_name: student.enable_enrollment_confirmation_email
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enable course enrollment email template
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2022-08-05
# .. toggle_target_removal_date: None
# .. toggle_warning: None
# .. toggle_tickets: VAN-1129
ENROLLMENT_CONFIRMATION_EMAIL = WaffleFlag(
    f'{WAFFLE_FLAG_NAMESPACE}.enable_enrollment_confirmation_email', __name__
)


def should_send_enrollment_email():
    return ENROLLMENT_CONFIRMATION_EMAIL.is_enabled()


# Waffle flag to enable control redirecting after enrolment.
# .. toggle_name: student.redirect_to_courseware_after_enrollment
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: Redirect to courseware after enrollment instead of dashboard.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-02-06
# .. toggle_target_removal_date: None
# .. toggle_warning: None
REDIRECT_TO_COURSEWARE_AFTER_ENROLLMENT = WaffleSwitch(
    f'{WAFFLE_FLAG_NAMESPACE}.redirect_to_courseware_after_enrollment', __name__
)


def should_redirect_to_courseware_after_enrollment():
    return REDIRECT_TO_COURSEWARE_AFTER_ENROLLMENT.is_enabled()
