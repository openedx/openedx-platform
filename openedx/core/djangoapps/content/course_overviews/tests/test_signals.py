"""
Tests for the course_overviews app's signal functionality.
"""


import datetime
from unittest.mock import patch
from collections import namedtuple
from zoneinfo import ZoneInfo

import pytest
import ddt

from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED,
    ModuleStoreTestCase,
    ImmediateOnCommitMixin,
)
from xmodule.modulestore.tests.factories import CourseFactory, check_mongo_calls

from ..models import CourseOverview

# represents a change of a course overview field. Used to avoid confusing indicies
Change = namedtuple("Change", ["field_name", "initial_value", "changed_value"])


@ddt.ddt
class CourseOverviewSignalsTestCase(ImmediateOnCommitMixin, ModuleStoreTestCase):
    """
    Tests for CourseOverview signals.
    """
    MODULESTORE = TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED
    ENABLED_SIGNALS = ['course_deleted', 'course_published']
    TODAY = datetime.datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    NEXT_WEEK = TODAY + datetime.timedelta(days=7)

    def assert_changed_signal_sent(self, changes, mock_signal):
        """
        Utility function used to verify that an emulated change to a course overview results in the expected signals
        being fired by the system.
        """
        course = CourseFactory.create(
            emit_signals=True,
            **{change.field_name: change.initial_value for change in changes}
        )

        # changing display name doesn't fire the signal
        course.display_name = course.display_name + 'changed'
        course = self.store.update_item(course, ModuleStoreEnum.UserID.test)
        assert not mock_signal.called

        # changing the given field fires the signal
        for change in changes:
            setattr(course, change.field_name, change.changed_value)
        self.store.update_item(course, ModuleStoreEnum.UserID.test)
        assert mock_signal.called

    def test_caching(self):
        """
        Tests that CourseOverview structures are actually getting cached.
        """
        # Creating a new course will trigger a publish event and the course will be cached
        course = CourseFactory.create(emit_signals=True)

        # The cache will be hit and mongo will not be queried
        with check_mongo_calls(0):
            CourseOverview.get_from_id(course.id)

    def test_cache_invalidation(self):
        """
        Tests that when a course is published or deleted, the corresponding course_overview is removed from the cache.
        """
        # Create a course where mobile_available is True.
        course = CourseFactory.create(mobile_available=True)
        course_overview_1 = CourseOverview.get_from_id(course.id)
        assert course_overview_1.mobile_available

        # Set mobile_available to False and update the course.
        # This fires a course_published signal, which should be caught in signals.py, which should in turn
        # delete the corresponding CourseOverview from the cache.
        course.mobile_available = False
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            self.store.update_item(course, ModuleStoreEnum.UserID.test)

        # Make sure that when we load the CourseOverview again, mobile_available is updated.
        course_overview_2 = CourseOverview.get_from_id(course.id)
        assert not course_overview_2.mobile_available

        # Verify that when the course is deleted, the corresponding CourseOverview is deleted as well.
        with pytest.raises(CourseOverview.DoesNotExist):
            self.store.delete_course(course.id, ModuleStoreEnum.UserID.test)
            CourseOverview.get_from_id(course.id)

    @patch('openedx.core.djangoapps.content.course_overviews.signals.COURSE_START_DATE_CHANGED.send')
    def test_start_changed(self, mock_signal):
        """
        A test that ensures the `COURSE_STATE_DATE_CHANGED` signal is emit when the start date of course run is updated.
        """
        self.assert_changed_signal_sent([Change('start', self.TODAY, self.NEXT_WEEK)], mock_signal)

    @patch('openedx.core.djangoapps.content.course_overviews.signals.COURSE_PACING_CHANGED.send')
    def test_pacing_changed(self, mock_signal):
        """
        A test that ensures the `COURSE_PACING_CHANGED` signal is emit when the pacing type of a course run is updated.
        """
        self.assert_changed_signal_sent([Change('self_paced', True, False)], mock_signal)

    @patch('openedx.core.djangoapps.content.course_overviews.signals.COURSE_CERT_DATE_CHANGE.send_robust')
    def test_cert_date_changed(self, mock_signal):
        changes = [
            Change("certificate_available_date", self.TODAY, self.NEXT_WEEK),
            Change(
                "certificates_display_behavior",
                CertificatesDisplayBehaviors.END,
                CertificatesDisplayBehaviors.END_WITH_DATE
            )
        ]
        self.assert_changed_signal_sent(changes, mock_signal)

    @patch('openedx.core.djangoapps.content.course_overviews.signals.COURSE_CERT_DATE_CHANGE.send_robust')
    def test_cert_end_date_changed(self, mock_signal):
        """
        This test ensures when an instructor-paced course with a certificates display behavior of "END" updates its end
        date that we emit the `COURSE_CERT_DATE_CHANGE` signal.
        """
        course = CourseFactory.create(
            emit_signals=True,
            end=self.TODAY,
            certificates_display_behavior=CertificatesDisplayBehaviors.END
        )

        with self.captureOnCommitCallbacks(execute=True):
            course.end = self.NEXT_WEEK
            self.store.update_item(course, ModuleStoreEnum.UserID.test)

        assert mock_signal.called
