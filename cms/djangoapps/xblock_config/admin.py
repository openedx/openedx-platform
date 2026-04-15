"""
Django admin dashboard configuration for LMS XBlock infrastructure.
"""


from config_models.admin import ConfigurationModelAdmin
from django.contrib import admin

from cms.djangoapps.xblock_config.models import StudioConfig

admin.site.register(StudioConfig, ConfigurationModelAdmin)
