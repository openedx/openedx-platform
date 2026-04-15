"""
Tests that check that we ignore the appropriate files when importing courses.
"""


import unittest
from unittest.mock import Mock

from opaque_keys.edx.locator import CourseLocator

from xmodule.modulestore.tests.utils import (
    DOT_FILES_DICT,
    TILDA_FILES_DICT,
    add_temp_files_from_dict,
    remove_temp_files_from_list,
)
from xmodule.modulestore.xml_importer import StaticContentImporter
from xmodule.tests import DATA_DIR


class IgnoredFilesTestCase(unittest.TestCase):
    """
    Tests for ignored files
    """
    course_dir = DATA_DIR / "course_ignore"
    dict_list = [DOT_FILES_DICT, TILDA_FILES_DICT]

    def setUp(self):
        super().setUp()
        for dictionary in self.dict_list:
            self.addCleanup(remove_temp_files_from_list, list(dictionary.keys()), self.course_dir / "static")
            add_temp_files_from_dict(dictionary, self.course_dir / "static")

    def test_sample_static_files(self):
        """
        Test for to ensure Mac OS metadata files (filename starts with "._") as well
        as files ending with "~" get ignored, while files starting with "." are not.
        """
        course_id = CourseLocator("edX", "course_ignore", "2014_Fall")
        content_store = Mock()
        content_store.generate_thumbnail.return_value = ("content", "location")
        static_content_importer = StaticContentImporter(
            static_content_store=content_store,
            course_data_path=self.course_dir,
            target_id=course_id
        )
        static_content_importer.import_static_content_directory()
        saved_static_content = [call[0][0] for call in content_store.save.call_args_list]
        name_val = {sc.name: sc.data for sc in saved_static_content}
        assert 'example.txt' in name_val
        assert '.example.txt' in name_val
        assert b'GREEN' in name_val['example.txt']
        assert b'BLUE' in name_val['.example.txt']
        assert '._example.txt' not in name_val
        assert '.DS_Store' not in name_val
        assert 'example.txt~' not in name_val
