"""
Experimentation admin config
"""


from django.contrib import admin

from .models import ExperimentData, ExperimentKeyValue


@admin.register(ExperimentData)
class ExperimentDataAdmin(admin.ModelAdmin):  # pylint: disable=missing-class-docstring
    list_display = ('user', 'experiment_id', 'key',)
    list_filter = ('experiment_id',)
    ordering = ('experiment_id', 'user', 'key',)
    raw_id_fields = ('user',)
    readonly_fields = ('created', 'modified',)
    search_fields = ('experiment_id', 'user', 'key',)


@admin.register(ExperimentKeyValue)
class ExperimentKeyValueAdmin(admin.ModelAdmin):  # pylint: disable=missing-class-docstring
    list_display = ('experiment_id', 'key',)
    list_filter = ('experiment_id',)
    ordering = ('experiment_id', 'key',)
    readonly_fields = ('created', 'modified',)
    search_fields = ('experiment_id', 'key',)
