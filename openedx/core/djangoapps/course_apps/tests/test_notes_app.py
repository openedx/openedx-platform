"""
Tests for wiki course app.
"""
from django.test.utils import override_settings

from lms.djangoapps.edxnotes.plugins import EdxNotesCourseApp
from openedx.core.djangoapps.course_apps.tests.utils import TabBasedCourseAppTestMixin
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


@skip_unless_cms
@override_settings(ENABLE_EDXNOTES=True)
class NotesCourseAppTestCase(TabBasedCourseAppTestMixin, ModuleStoreTestCase):
    """Test cases for Notes CourseApp."""

    tab_type = 'edxnotes'
    course_app_class = EdxNotesCourseApp

    def _assert_app_enabled(self, app_tab):
        assert app_tab.is_enabled(self.course, self.user)

    def _assert_app_disabled(self, app_tab):
        assert not app_tab.is_enabled(self.course, self.user)
