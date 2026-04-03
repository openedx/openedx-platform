"""
Django Admin pages for ContentTypeGatingConfig.
"""


from django.contrib import admin

from openedx.core.djangoapps.config_model_utils.admin import StackedConfigModelAdmin

from .models import ContentTypeGatingConfig

admin.site.register(ContentTypeGatingConfig, StackedConfigModelAdmin)
