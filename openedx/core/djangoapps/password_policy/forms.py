"""
Forms for the password policy app.
"""
from django.contrib import messages
from django.contrib.admin.forms import AdminAuthenticationForm
from django.forms import ValidationError

from openedx.core.djangoapps.password_policy import compliance as password_policy_compliance
from openedx.core.djangolib.markup import HTML


class PasswordPolicyAwareAdminAuthForm(AdminAuthenticationForm):
    """
    Custom AdminAuthenticationForm that can enforce password policy rules on login.
    """

    def clean(self):
        """
        Overrides the clean method to allow for the enforcement of password policy requirements.
        """
        cleaned_data = super().clean()

        if password_policy_compliance.should_enforce_compliance_on_login():
            try:
                password_policy_compliance.enforce_compliance_on_login(self.user_cache, cleaned_data['password'])
            except password_policy_compliance.NonCompliantPasswordWarning as e:
                # Allow login, but warn the user that they will be required to reset their password soon.
                messages.warning(self.request, HTML(str(e)))
            except password_policy_compliance.NonCompliantPasswordException as e:
                # Prevent the login attempt.
                raise ValidationError(HTML(str(e)))  # pylint: disable=raise-missing-from  # noqa: B904

        return cleaned_data
