"""
Tests for signal handlers in the contentstore.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from openedx_events.content_authoring.data import CourseCatalogData, CourseScheduleData

import cms.djangoapps.contentstore.signals.handlers as sh
from xmodule.modulestore.django import SignalHandler
from xmodule.modulestore.edit_info import EditInfoMixin
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import SampleCourseFactory


class TestCatalogInfoSignal(ModuleStoreTestCase):
    """
    Test functionality of triggering catalog info signals (and events) from course_published signal.
    """

    def setUp(self):
        super().setUp()
        self.course = SampleCourseFactory.create(
            org='TestU',
            number='sig101',
            display_name='Signals 101',
            run='Summer2022',
        )
        self.course_key = self.course.id

        self.expected_data = CourseCatalogData(
            course_key=CourseLocator(org='TestU', course='sig101', run='Summer2022', branch=None, version_guid=None),
            name='Signals 101',
            schedule_data=CourseScheduleData(
                start=datetime.fromisoformat('2030-01-01T00:00+00:00'),
                pacing='instructor',
                end=None,
                enrollment_start=None,
                enrollment_end=None),
            hidden=False,
            invitation_only=False
        )

    @patch(
        'cms.djangoapps.contentstore.signals.handlers.transaction.on_commit',
        autospec=True, side_effect=lambda func: func(),  # run right away
    )
    @patch('cms.djangoapps.contentstore.signals.handlers.emit_catalog_info_changed_signal', autospec=True)
    def test_signal_chain(self, mock_emit, _mock_on_commit):
        """
        Test that the course_published signal handler invokes the catalog info signal emitter.

        I tested this in a bit of a weird way because I couldn't get the transaction on-commit
        to run during the test, so instead I capture it and call the callbacks right away.
        """
        with SignalHandler.course_published.for_state(is_enabled=True):
            SignalHandler.course_published.send(TestCatalogInfoSignal, course_key=self.course_key)
        mock_emit.assert_called_once_with(self.course_key)

    @patch('cms.djangoapps.contentstore.signals.handlers.COURSE_CATALOG_INFO_CHANGED', autospec=True)
    def test_emit_regular_course(self, mock_signal):
        """On a normal course publish, send an event."""
        now = datetime.now()
        with patch.object(EditInfoMixin, 'subtree_edited_on', now):
            sh.emit_catalog_info_changed_signal(self.course_key)
        mock_signal.send_event.assert_called_once_with(
            time=now.replace(tzinfo=timezone.utc),
            catalog_info=self.expected_data)

    @patch('cms.djangoapps.contentstore.signals.handlers.COURSE_CATALOG_INFO_CHANGED', autospec=True)
    def test_ignore_library(self, mock_signal):
        """When course key is actually a library, don't send."""
        sh.emit_catalog_info_changed_signal(LibraryLocator(org='SomeOrg', library='stuff'))
        mock_signal.send_event.assert_not_called()
