# pylint: disable=missing-module-docstring
from factory.django import DjangoModelFactory

from openedx.features.calendar_sync.models import UserCalendarSyncConfig


class UserCalendarSyncConfigFactory(DjangoModelFactory):
    """
    Factory class for SiteConfiguration model
    """
    class Meta:
        model = UserCalendarSyncConfig

    enabled = True
