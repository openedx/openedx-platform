"""
Unit tests for the course bookmarks feature.
"""


import ddt
from django.test import RequestFactory

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import CourseUserType, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from ..plugins import CourseBookmarksTool


@ddt.ddt
class TestCourseBookmarksTool(SharedModuleStoreTestCase):
    """
    Test the course bookmarks tool.
    """
    @classmethod
    def setUpClass(cls):
        """
        Set up a course to be used for testing.
        """
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            with cls.store.default_store(ModuleStoreEnum.Type.split):
                cls.course = CourseFactory.create()
                with cls.store.bulk_operations(cls.course.id):
                    # Create a basic course structure
                    chapter = BlockFactory.create(category='chapter', parent_location=cls.course.location)
                    section = BlockFactory.create(category='sequential', parent_location=chapter.location)
                    BlockFactory.create(category='vertical', parent_location=section.location)

    @ddt.data(
        [CourseUserType.ANONYMOUS, False],
        [CourseUserType.ENROLLED, True],
        [CourseUserType.UNENROLLED, False],
        [CourseUserType.UNENROLLED_STAFF, True],
    )
    @ddt.unpack
    def test_bookmarks_tool_is_enabled(self, user_type, should_be_enabled):
        request = RequestFactory().request()
        request.user = self.create_user_for_course(self.course, user_type)
        assert CourseBookmarksTool.is_enabled(request, self.course.id) == should_be_enabled
