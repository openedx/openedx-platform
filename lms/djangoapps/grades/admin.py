"""
Django admin page for grades models
"""


from config_models.admin import ConfigurationModelAdmin  # noqa: I001
from django.contrib import admin

from lms.djangoapps.grades.config.models import (
    ComputeGradesSetting
)


admin.site.register(ComputeGradesSetting, ConfigurationModelAdmin)
