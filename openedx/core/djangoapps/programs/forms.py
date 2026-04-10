"""
Forms for discussions.
"""
from django import forms

from .models import ProgramDiscussionsConfiguration, ProgramLiveConfiguration


class ProgramDiscussionsConfigurationForm(forms.ModelForm):
    """
    Custom ProgramDiscussionsConfiguration form for admin page
    """
    pii_share_username = forms.BooleanField(required=False, initial=False)
    pii_share_email = forms.BooleanField(required=False, initial=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.lti_configuration:
            self.fields['pii_share_username'].initial = self.instance.lti_configuration.pii_share_username
            self.fields['pii_share_email'].initial = self.instance.lti_configuration.pii_share_email

    def save(self, commit=True):
        pii_share_username = self.cleaned_data.get('pii_share_username', False)
        pii_share_email = self.cleaned_data.get('pii_share_email', False)
        lti_configuration = self.cleaned_data.get('lti_configuration', None)
        if lti_configuration:
            lti_configuration.pii_share_username = pii_share_username
            lti_configuration.pii_share_email = pii_share_email
            lti_configuration.save()
        return super().save(commit=commit)

    class Meta:
        model = ProgramDiscussionsConfiguration
        fields = '__all__'  # noqa: DJ007


class ProgramLiveConfigurationForm(forms.ModelForm):
    """
    Custom ProgramLiveConfigurationForm form for admin page
    """
    pii_share_username = forms.BooleanField(required=False, initial=False)
    pii_share_email = forms.BooleanField(required=False, initial=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.lti_configuration:
            self.fields['pii_share_username'].initial = self.instance.lti_configuration.pii_share_username
            self.fields['pii_share_email'].initial = self.instance.lti_configuration.pii_share_email

    def save(self, commit=True):
        pii_share_username = self.cleaned_data.get('pii_share_username', False)
        pii_share_email = self.cleaned_data.get('pii_share_email', False)
        lti_configuration = self.cleaned_data.get('lti_configuration', None)
        if lti_configuration:
            lti_configuration.pii_share_username = pii_share_username
            lti_configuration.pii_share_email = pii_share_email
            lti_configuration.save()
        return super().save(commit=commit)

    class Meta:
        model = ProgramLiveConfiguration
        fields = '__all__'  # noqa: DJ007
