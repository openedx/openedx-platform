# pylint: disable=missing-module-docstring
from django.contrib import admin

from .models import UserCalendarSyncConfig

admin.site.register(UserCalendarSyncConfig)
