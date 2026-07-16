"""
Tests for wiki course app.
"""
from unittest.mock import patch

from django.conf import settings

from lms.djangoapps.edxnotes.plugins import EdxNotesCourseApp
from openedx.core.djangoapps.course_apps.tests.utils import TabBasedCourseAppTestMixin
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order


@skip_unless_cms
@patch.dict(settings.FEATURES, {'ENABLE_EDXNOTES': True})
class NotesCourseAppTestCase(TabBasedCourseAppTestMixin, ModuleStoreTestCase):
    """Test cases for Notes CourseApp."""

    tab_type = 'edxnotes'
    course_app_class = EdxNotesCourseApp

    def test_app_disabled_by_default(self):
        """
        Override base mixin behavior: Notes is enabled by default.
        """
        assert self.course_app_class.is_enabled(self.course.id)

    def _assert_app_enabled(self, app_tab):
        assert app_tab.is_enabled(self.course, self.user)

    def _assert_app_disabled(self, app_tab):
        assert not app_tab.is_enabled(self.course, self.user)
