# lint-amnesty, pylint: disable=missing-module-docstring
from django.contrib import admin  # noqa: I001

from .models import UserCalendarSyncConfig

admin.site.register(UserCalendarSyncConfig)
