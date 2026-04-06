"""
Django forms for accounts
"""


from django import forms
from django.core.exceptions import ValidationError

from openedx.core.djangoapps.user_api.accounts.utils import handle_retirement_cancellation


class RetirementQueueDeletionForm(forms.Form):
    """
    Admin form to facilitate learner retirement cancellation
    """
    cancel_retirement = forms.BooleanField(required=True)

    def save(self, retirement):
        """
        When the form is POSTed we double-check the retirment status
        and perform the necessary steps to cancel the retirement
        request.
        """
        if retirement.current_state.state_name != 'PENDING':
            self.add_error(
                None,
                # Translators: 'current_state' is a string from an enumerated list indicating the learner's retirement
                # state. Example: FORUMS_COMPLETE
                "Retirement requests can only be cancelled for users in the PENDING state."  # noqa: UP032
                " Current request state for '{original_username}': {current_state}".format(
                    original_username=retirement.original_username,
                    current_state=retirement.current_state.state_name
                )
            )
            raise ValidationError('Retirement is in the wrong state!')

        handle_retirement_cancellation(retirement)
