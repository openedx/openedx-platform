"""
Test utilities for course apps.
"""
from typing import Type  # noqa: UP035

from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import AdminFactory, CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.course_apps.plugins import CourseApp
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


def make_test_course_app(
    app_id: str = "test-app",
    name: str = "Test Course App",
    description: str = "Test Course App Description",
    is_available: bool = True,
) -> Type[CourseApp]:  # noqa: UP006
    """
    Creates a test plugin entrypoint based on provided parameters."""

    class TestCourseApp(CourseApp):
        """
        Course App Config for use in tests.
        """

        app_id = ""
        name = ""
        description = ""
        _enabled = {}

        @classmethod
        def is_available(cls, course_key):  # pylint=disable=unused-argument
            """
            Return value provided to function"""
            return is_available

        @classmethod
        def get_allowed_operations(cls, course_key, user=None):  # pylint=disable=unused-argument
            """
            Return dummy values for allowed operations."""
            return {
                "enable": True,
                "configure": True,
            }

        @classmethod
        def set_enabled(cls, course_key: CourseKey, enabled: bool, user: 'User') -> bool:  # noqa: F821
            cls._enabled[course_key] = enabled
            return enabled

        @classmethod
        def is_enabled(cls, course_key: CourseKey) -> bool:
            return cls._enabled.get(course_key, False)

    TestCourseApp.app_id = app_id
    TestCourseApp.name = name
    TestCourseApp.description = description
    return TestCourseApp


@skip_unless_cms
class TabBasedCourseAppTestMixin:
    """Test cases a course app adding/removing tabs CourseApp."""

    tab_type = None
    course_app_class = None

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.instructor = AdminFactory.create()
        self.user = UserFactory()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def reload_course(self):
        self.course = modulestore().get_course(self.course.id)

    def get_app_tab(self, course_key):
        """
        Reload the course and fetch the app tab if present.
        """
        course = self.store.get_course(course_key)
        return next((tab for tab in course.tabs if tab.type == self.tab_type), None)

    def test_app_disabled_by_default(self):
        """
        Test that the app tab is disabled by default.
        """
        assert not self.course_app_class.is_enabled(self.course.id)

    def test_app_enabling(self):
        """
        Test that enabling and disable the app enabled/disables the tab.
        """
        self.course_app_class.set_enabled(self.course.id, True, self.instructor)
        self.reload_course()
        app_tab = self.get_app_tab(self.course.id)
        self._assert_app_enabled(app_tab)
        self.course_app_class.set_enabled(self.course.id, False, self.instructor)
        self.reload_course()
        app_tab = self.get_app_tab(self.course.id)
        self._assert_app_disabled(app_tab)

    def test_app_adds_tab(self):
        """
        Test that enabling the app for a course that doesn't have the app tab adds the tab.
        """
        self.course.tabs = [tab for tab in self.course.tabs if tab.type != self.tab_type]
        self.store.update_item(self.course, self.instructor.id)
        assert self.get_app_tab(self.course.id) is None
        self.course_app_class.set_enabled(self.course.id, True, self.instructor)
        assert self.get_app_tab(self.course.id) is not None
