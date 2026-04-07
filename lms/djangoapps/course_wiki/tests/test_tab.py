"""
Tests for wiki views.
"""


from django.conf import settings

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from lms.djangoapps.courseware.tabs import get_course_tab_list
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class WikiTabTestCase(ModuleStoreTestCase):
    """Test cases for Wiki Tab."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.instructor = AdminFactory.create()
        self.user = UserFactory()

    def _enable_wiki_tab(self):
        """
        Enables the wiki tab globally and unhides it for the course.
        """
        settings.WIKI_ENABLED = True
        # Enable the wiki tab for these tests
        for tab in self.course.tabs:
            if tab.type == 'wiki':
                tab.is_hidden = False
        self.course.save()

    def get_wiki_tab(self, user, course):
        """Returns wiki tab if it is shown."""
        all_tabs = get_course_tab_list(user, course)
        return next((tab for tab in all_tabs if tab.type == 'wiki'), None)

    def test_wiki_enabled_and_public(self):
        """
        Test wiki tab when Enabled setting is True and the wiki is open to
        the public.
        """
        self._enable_wiki_tab()
        self.course.allow_public_wiki_access = True
        assert self.get_wiki_tab(self.user, self.course) is not None

    def test_wiki_enabled_and_not_public(self):
        """
        Test wiki when it is enabled but not open to the public
        """
        self._enable_wiki_tab()
        self.course.allow_public_wiki_access = False
        assert self.get_wiki_tab(self.user, self.course) is None
        assert self.get_wiki_tab(self.instructor, self.course) is not None

    def test_wiki_enabled_false(self):
        """Test wiki tab when Enabled setting is False"""
        assert self.get_wiki_tab(self.user, self.course) is None
        assert self.get_wiki_tab(self.instructor, self.course) is None

    def test_wiki_visibility(self):
        """Test toggling of visibility of wiki tab"""
        self._enable_wiki_tab()
        self.course.allow_public_wiki_access = True
        wiki_tab = self.get_wiki_tab(self.user, self.course)
        assert wiki_tab is not None
        assert wiki_tab.is_hideable
        wiki_tab.is_hidden = True
        assert wiki_tab['is_hidden']
        wiki_tab['is_hidden'] = False
        assert not wiki_tab.is_hidden

    def test_wiki_hidden_by_default(self):
        """
        Test that the wiki tab is hidden by default
        """
        assert self.get_wiki_tab(self.user, self.course) is None
