"""
Unit tests for the gating feature in Studio
"""


from unittest.mock import patch

from milestones.tests.utils import MilestonesTestCaseMixin

from cms.djangoapps.contentstore.signals.handlers import handle_item_deleted
from openedx.core.lib.gating import api as gating_api
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class TestHandleItemDeleted(ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Test case for handle_score_changed django signal handler
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        """
        Initial data setup
        """
        super().setUp()

        self.course = CourseFactory.create()
        self.course.enable_subsection_gating = True
        self.course.save()
        self.chapter = BlockFactory.create(
            parent=self.course,
            category="chapter",
            display_name="Chapter"
        )
        self.open_seq = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name="Open Sequential"
        )
        self.gated_seq = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name="Gated Sequential"
        )
        gating_api.add_prerequisite(self.course.id, self.open_seq.location)
        gating_api.set_required_content(self.course.id, self.gated_seq.location, self.open_seq.location, 100, 100)

    @patch('cms.djangoapps.contentstore.signals.handlers.gating_api.set_required_content')
    @patch('cms.djangoapps.contentstore.signals.handlers.gating_api.remove_prerequisite')
    def test_chapter_deleted(self, mock_remove_prereq, mock_set_required):
        """ Test gating milestone data is cleanup up when course content item is deleted """
        handle_item_deleted(usage_key=self.chapter.location, user_id=0)
        mock_remove_prereq.assert_called_with(self.open_seq.location)
        mock_set_required.assert_called_with(
            self.open_seq.location.course_key, self.open_seq.location, None, None, None
        )

    @patch('cms.djangoapps.contentstore.signals.handlers.gating_api.set_required_content')
    @patch('cms.djangoapps.contentstore.signals.handlers.gating_api.remove_prerequisite')
    def test_sequential_deleted(self, mock_remove_prereq, mock_set_required):
        """ Test gating milestone data is cleanup up when course content item is deleted """
        handle_item_deleted(usage_key=self.open_seq.location, user_id=0)
        mock_remove_prereq.assert_called_with(self.open_seq.location)
        mock_set_required.assert_called_with(
            self.open_seq.location.course_key, self.open_seq.location, None, None, None
        )
