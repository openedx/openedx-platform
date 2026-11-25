"""
Tests for signal handlers in the contentstore.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import ddt
from django.test import TestCase, TransactionTestCase
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from openedx_events.content_authoring.data import CourseCatalogData, CourseScheduleData, DuplicatedXBlockData
from openedx_events.content_authoring.signals import XBLOCK_DUPLICATED

import cms.djangoapps.contentstore.signals.handlers as sh
from cms.djangoapps.contentstore.tests.utils import create_taxonomy_with_tag, get_tag_pairs
from openedx.core.djangoapps.content_tagging import api as tagging_api
from xmodule.course_metadata_utils import DEFAULT_START_DATE
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
                start=DEFAULT_START_DATE,
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
    def test_signal_chain(self, mock_emit, _mock_on_commit):  # noqa: PT019
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
            time=now.replace(tzinfo=timezone.utc),  # noqa: UP017
            catalog_info=self.expected_data)

    @patch('cms.djangoapps.contentstore.signals.handlers.COURSE_CATALOG_INFO_CHANGED', autospec=True)
    def test_ignore_library(self, mock_signal):
        """When course key is actually a library, don't send."""
        sh.emit_catalog_info_changed_signal(LibraryLocator(org='SomeOrg', library='stuff'))
        mock_signal.send_event.assert_not_called()


def _send_duplicated_event(block_type):
    """Send XBLOCK_DUPLICATED for a block of the given type; return the source and duplicate keys."""
    source = UsageKey.from_string(f"block-v1:orgA+c+r+type@{block_type}+block@source")
    duplicate = UsageKey.from_string(f"block-v1:orgA+c+r+type@{block_type}+block@duplicate")
    XBLOCK_DUPLICATED.send_event(
        xblock_info=DuplicatedXBlockData(usage_key=duplicate, block_type=block_type, source_usage_key=source),
    )
    return source, duplicate


@ddt.ddt
class TestStripCompetencyTagsOnDuplicate(TestCase):
    """
    Tests for the receiver that queues the removal of competency tags from a duplicate.
    """

    @ddt.data("chapter", "sequential")
    @patch("cms.djangoapps.contentstore.signals.handlers.strip_competency_tags_from_duplicate")
    def test_section_or_subsection_queues_the_task_on_commit(self, block_type, mock_task):
        with self.captureOnCommitCallbacks() as callbacks:
            _, duplicate = _send_duplicated_event(block_type)
        mock_task.delay.assert_not_called()

        for callback in callbacks:
            callback()

        mock_task.delay.assert_called_once_with(str(duplicate))

    @patch("cms.djangoapps.contentstore.signals.handlers.strip_competency_tags_from_duplicate")
    def test_failure_to_queue_the_task_does_not_fail_the_commit(self, mock_task):
        mock_task.delay.side_effect = ConnectionError("broker is down")

        with self.captureOnCommitCallbacks(execute=True):
            _send_duplicated_event("sequential")

        mock_task.delay.assert_called_once()

    @ddt.data("vertical", "html")
    @patch("cms.djangoapps.contentstore.signals.handlers.strip_competency_tags_from_duplicate")
    def test_unit_or_component_queues_nothing(self, block_type, mock_task):
        with self.captureOnCommitCallbacks(execute=True):
            _send_duplicated_event(block_type)

        mock_task.delay.assert_not_called()


class TestStripCompetencyTagsRunsAfterTagCopy(TransactionTestCase):
    """
    Tests the order of the duplicate receivers when no transaction is open, as in Studio's duplicate request.
    """

    def test_duplicate_has_no_competency_tag_without_a_transaction(self):
        source = UsageKey.from_string("block-v1:orgA+c+r+type@sequential+block@source")
        for name, competency in (("regular", False), ("comp", True)):
            taxonomy = create_taxonomy_with_tag(name, competency=competency)
            tagging_api.tag_object(str(source), taxonomy, [f"{name}-tag"])

        _, duplicate = _send_duplicated_event("sequential")

        assert get_tag_pairs(duplicate) == {("regular", "regular-tag")}
