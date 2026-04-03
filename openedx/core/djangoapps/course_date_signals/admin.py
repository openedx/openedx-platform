"""
Django Admin pages for SelfPacedRelativeDatesConfig.
"""


from django.contrib import admin

from openedx.core.djangoapps.config_model_utils.admin import StackedConfigModelAdmin

from .models import SelfPacedRelativeDatesConfig

admin.site.register(SelfPacedRelativeDatesConfig, StackedConfigModelAdmin)
