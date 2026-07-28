"""
Tests for exporting course metadata for all courses.
"""

from unittest.mock import patch

from edx_toggles.toggles.testutils import override_waffle_flag

from cms.djangoapps.export_course_metadata.toggles import EXPORT_COURSE_METADATA_FLAG
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..export_course_metadata_for_all_courses import export_course_metadata_for_all_courses


@override_waffle_flag(EXPORT_COURSE_METADATA_FLAG, True)
class ExportAllCourses(ModuleStoreTestCase):
    """
    Tests for exporting course metadata for all courses.
    """
    def setUp(self):
        super().setUp()
        CourseFactory.create()
        CourseFactory.create()

    @patch('cms.djangoapps.export_course_metadata.tasks.course_metadata_export_storage.save')
    def test_exporting_all_courses(self, patched_storage):
        """
        Test for exporting course metadata for all courses.
        """
        export_course_metadata_for_all_courses()
        assert len(patched_storage.mock_calls) == 2
