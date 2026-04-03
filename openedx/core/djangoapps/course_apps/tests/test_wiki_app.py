"""
Tests for wiki course app.
"""

from lms.djangoapps.course_wiki.plugins.course_app import WikiCourseApp
from openedx.core.djangoapps.course_apps.tests.utils import TabBasedCourseAppTestMixin
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)


@skip_unless_cms
class WikiCourseAppTestCase(TabBasedCourseAppTestMixin, ModuleStoreTestCase):
    """Test cases for Wiki CourseApp."""

    tab_type = 'wiki'
    course_app_class = WikiCourseApp

    def _assert_app_enabled(self, app_tab):
        assert not app_tab.is_hidden

    def _assert_app_disabled(self, app_tab):
        assert app_tab.is_hidden
