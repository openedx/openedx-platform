"""
Tests for the course updates utility methods.
"""

from django.test.client import RequestFactory

from openedx.core.djangoapps.user_api.course_tag.api import get_course_tag, set_course_tag
from openedx.features.course_experience.course_updates import (
    dismiss_current_update_for_user,
    get_current_update_for_user,
    get_ordered_updates,
)
from openedx.features.course_experience.tests import BaseCourseUpdatesTestCase


class TestCourseUpdatesUtils(BaseCourseUpdatesTestCase):
    """Tests for the course update utility methods."""

    UPDATES_TAG = 'view-welcome-message'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.request = RequestFactory().get('/')
        cls.request.user = cls.user

    def test_update_structure(self):
        """Test that returned item dictionary is as we expect."""
        content = '<em>HTML Content</em>'
        date = 'January 1, 2000'
        self.create_course_update(content, date=date)
        updates = get_ordered_updates(self.request, self.course)
        self.assertListEqual(updates, [{
            'id': 1,
            'content': content,
            'date': date,
            'status': 'visible',
        }])

    def test_ordered_updates(self):
        """Test that order of returned items follows our rules."""
        first = self.create_course_update('2000', date='January 1, 2000')
        second = self.create_course_update('2017', date='January 1, 2017')
        third = self.create_course_update('Also 2017', date='January 1, 2017')
        injected = self.create_course_update('Injected out of order', date='January 1, 2010')
        ill_formed = self.create_course_update('Ill-formed date is parsed as now()', date='foobar')
        self.create_course_update('Deleted is ignored', deleted=True)
        updates = get_ordered_updates(self.request, self.course)
        self.assertListEqual(updates, [ill_formed, third, second, injected, first])

    def test_replace_urls(self):
        """We should be replacing static URLs with course specific ones."""
        self.create_course_update("<img src='/static/img.png'>")
        updates = get_ordered_updates(self.request, self.course)
        expected = "<img src='/asset-v1:{org}+{course}+{run}+type@asset+block/img.png'>".format(
            org=self.course.id.org,
            course=self.course.id.course,
            run=self.course.id.run,
        )
        assert updates[0]['content'] == expected

    def test_ordered_update_includes_dismissed_updates(self):
        """Ordered update list should still have dismissed updates."""
        self.create_course_update('Dismissed')
        dismiss_current_update_for_user(self.request, self.course)
        updates = get_ordered_updates(self.request, self.course)
        assert len(updates) == 1

    def test_get_current_update_is_newest(self):
        """Tests that the current update is also the newest."""
        self.create_course_update('Oldest', date='January 1, 1900')
        self.create_course_update('New', date='January 1, 2017')
        self.create_course_update('Oldish', date='January 1, 2000')
        assert get_current_update_for_user(self.request, self.course) == 'New'

    def test_get_current_update_when_dismissed(self):
        """Tests that a dismissed update is not returned."""
        self.create_course_update('Dismissed')
        dismiss_current_update_for_user(self.request, self.course)
        assert get_current_update_for_user(self.request, self.course) is None

    def test_get_current_update_when_dismissed_but_edited(self):
        """Tests that a dismissed but edited update is returned."""
        self.create_course_update('Original')
        dismiss_current_update_for_user(self.request, self.course)
        assert get_current_update_for_user(self.request, self.course) is None
        self.edit_course_update(1, content='Edited')
        assert get_current_update_for_user(self.request, self.course) is not None

    def test_get_current_update_remembers_dismissals(self):
        """Tests that older dismissed updates are remembered."""
        self.create_course_update('First')
        self.create_course_update('Second')
        dismiss_current_update_for_user(self.request, self.course)
        self.create_course_update('Third')
        dismiss_current_update_for_user(self.request, self.course)
        self.create_course_update('Fourth')

        assert get_current_update_for_user(self.request, self.course) == 'Fourth'
        self.edit_course_update(4, deleted=True)
        assert get_current_update_for_user(self.request, self.course) is None
        self.edit_course_update(3, deleted=True)
        assert get_current_update_for_user(self.request, self.course) is None
        self.edit_course_update(2, deleted=True)
        assert get_current_update_for_user(self.request, self.course) == 'First'

    def test_legacy_ignore_all_support(self):
        """Storing 'False' as the dismissal ignores all updates."""
        self.create_course_update('First')
        assert get_current_update_for_user(self.request, self.course) == 'First'

        set_course_tag(self.user, self.course.id, self.UPDATES_TAG, 'False')
        assert get_current_update_for_user(self.request, self.course) is None

    def test_dismissal_hashing(self):
        """Confirm that the stored dismissal values are what we expect, to catch accidentally changing formats."""
        self.create_course_update('First')
        dismiss_current_update_for_user(self.request, self.course)
        self.create_course_update('Second')
        dismiss_current_update_for_user(self.request, self.course)

        tag = get_course_tag(self.user, self.course.id, self.UPDATES_TAG)
        assert tag == '7fb55ed0b7a30342ba6da306428cae04,c22cf8376b1893dcfcef0649fe1a7d87'
