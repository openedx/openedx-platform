"""
Test cases for the Open edX Filters associated with the schedule app.
"""

import datetime
from unittest.mock import Mock

from django.db.models.query import QuerySet
from django.test import override_settings
from openedx_filters import PipelineStep

from openedx.core.djangoapps.schedules.resolvers import BinnedSchedulesBaseResolver
from openedx.core.djangoapps.schedules.tests.test_resolvers import SchedulesResolverTestMixin
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class TestScheduleQuerySetRequestedPipelineStep(PipelineStep):
    """Pipeline step class to test a configured pipeline step"""

    filtered_schedules = Mock(spec=QuerySet, __len__=Mock(return_value=0))

    def run_filter(self, schedules: QuerySet):  # pylint: disable=arguments-differ
        """Pipeline step to filter the schedules"""
        return {
            "schedules": self.filtered_schedules,
        }


@skip_unless_lms
class ScheduleQuerySetRequestedFiltersTest(SchedulesResolverTestMixin, ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the schedule queryset requested.

    The following filters are tested:
    - ScheduleQuerySetRequested
    """

    def setUp(self):
        super().setUp()
        self.resolver = BinnedSchedulesBaseResolver(
            async_send_task=Mock(name="async_send_task"),
            site=self.site,
            target_datetime=datetime.datetime.now(),
            day_offset=3,
            bin_num=2,
        )
        self.resolver.schedule_date_field = "created"

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.schedule.queryset.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.schedules.tests.test_filters.TestScheduleQuerySetRequestedPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_schedule_with_queryset_requested_filter_enabled(self) -> None:
        """Test to verify the schedule queryset was modified by the pipeline step."""
        schedules = self.resolver.get_schedules_with_target_date_by_bin_and_orgs()

        self.assertEqual(TestScheduleQuerySetRequestedPipelineStep.filtered_schedules, schedules)  # noqa: PT009

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_schedule_with_queryset_requested_filter_disabled(self) -> None:
        """Test to verify the schedule queryset was not modified when the pipeline step is not configured."""
        schedules = self.resolver.get_schedules_with_target_date_by_bin_and_orgs()

        self.assertNotEqual(TestScheduleQuerySetRequestedPipelineStep.filtered_schedules, schedules)  # noqa: PT009
