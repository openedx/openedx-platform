"""
Defines a form to provide validations for course-specific configuration.
"""
from django import forms

from openedx.core.djangoapps.video_config.forms import CourseSpecificFlagAdminBaseForm
from openedx.core.djangoapps.video_pipeline.models import CourseVideoUploadsEnabledByDefault, VEMPipelineIntegration


class CourseVideoUploadsEnabledByDefaultAdminForm(CourseSpecificFlagAdminBaseForm):
    """
    Form for course-specific Video Uploads enabled by default configuration.
    """

    class Meta:
        model = CourseVideoUploadsEnabledByDefault
        fields = '__all__'


class VEMPipelineIntegrationAdminForm(forms.ModelForm):
    """
    Form for VEM Pipeline Integration Admin class.
    """
    class Meta:
        model = VEMPipelineIntegration
        fields = '__all__'
