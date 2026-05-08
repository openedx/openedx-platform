# pylint: disable=missing-module-docstring

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _
from edx_django_utils.plugins import PluginSignals  # pylint: disable=unused-import  # noqa: F401

# pylint: disable-next=unused-import
from openedx.core.djangoapps.plugins.constants import (
    ProjectType,  # noqa: F401
)


class SchedulesConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.schedules'
    verbose_name = _('Schedules')

    def ready(self):
        # noinspection PyUnresolvedReferences
        from . import signals, tasks  # pylint: disable=unused-import, unused-variable  # noqa: F401
