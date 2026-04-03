"""
Test for export all courses.
"""


import shutil
from tempfile import mkdtemp
from unittest import skip

from cms.djangoapps.contentstore.management.commands.export_all_courses import export_courses_to_output_path
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@skip("OldMongo Deprecation")
# This test fails for split modulestre
# AttributeError: 'MixedModuleStore' object has no attribute 'collection'
# split module store has no 'collection' attribute.
class ExportAllCourses(ModuleStoreTestCase):
    """
    Tests exporting all courses.
    """
    def setUp(self):
        """ Common setup. """
        super().setUp()
        self.store = modulestore()._get_modulestore_by_type(ModuleStoreEnum.Type.mongo)  # lint-amnesty, pylint: disable=protected-access
        self.temp_dir = mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir)
        self.first_course = CourseFactory.create(
            org="test", course="course1", display_name="run1", default_store=ModuleStoreEnum.Type.mongo
        )
        self.second_course = CourseFactory.create(
            org="test", course="course2", display_name="run2", default_store=ModuleStoreEnum.Type.mongo
        )

    def test_export_all_courses(self):
        """
        Test exporting good and faulty courses
        """
        # check that both courses exported successfully
        courses, failed_export_courses = export_courses_to_output_path(self.temp_dir)
        self.assertEqual(len(courses), 2)
        self.assertEqual(len(failed_export_courses), 0)

        # manually make second course faulty and check that it fails on export
        second_course_id = self.second_course.id
        self.store.collection.update(
            {'_id.org': second_course_id.org, '_id.course': second_course_id.course, '_id.name': second_course_id.run},
            {'$set': {'metadata.tags': 'crash'}}
        )
        courses, failed_export_courses = export_courses_to_output_path(self.temp_dir)
        self.assertEqual(len(courses), 2)
        self.assertEqual(len(failed_export_courses), 1)
        self.assertEqual(failed_export_courses[0], str(second_course_id))
