"""
Defines a form for providing validation of waffle overrides.
"""

from django import forms

from openedx.core.lib.courses import clean_course_id

from .models import WaffleFlagCourseOverrideModel, WaffleFlagOrgOverrideModel


class WaffleFlagCourseOverrideAdminForm(forms.ModelForm):
    """
    Input form for course-level override of waffle flags, allowing us to verify data.
    """
    class Meta:
        model = WaffleFlagCourseOverrideModel
        fields = '__all__'  # noqa: DJ007

    def clean_course_id(self):
        """
        Validate the course id
        """
        return clean_course_id(self)

    def clean_waffle_flag(self):
        """
        Validate the waffle flag is specified.
        """
        cleaned_flag = self.cleaned_data['waffle_flag'].strip()

        if not cleaned_flag:
            msg = 'Waffle flag must be supplied.'
            raise forms.ValidationError(msg)

        return cleaned_flag


class WaffleFlagOrgOverrideAdminForm(forms.ModelForm):
    """
    Input form for org-level override of waffle flags, allowing us to verify data.
    """
    class Meta:
        model = WaffleFlagOrgOverrideModel
        fields = '__all__'  # noqa: DJ007

    def clean_org(self):
        """
        Validate the org.
        """
        cleaned_flag = self.cleaned_data['org'].strip()

        if not cleaned_flag:
            msg = 'Organization must be supplied.'
            raise forms.ValidationError(msg)

        return cleaned_flag

    def clean_waffle_flag(self):
        """
        Validate the waffle flag is specified.
        """
        cleaned_flag = self.cleaned_data['waffle_flag'].strip()

        if not cleaned_flag:
            msg = 'Waffle flag must be supplied.'
            raise forms.ValidationError(msg)

        return cleaned_flag
