"""
Tests around our XML modulestore, including importing
well-formed and not-well-formed XML.
"""


import os.path
from glob import glob
from unittest.mock import Mock, patch

import pytest
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.test_modulestore import check_has_course_method
from xmodule.modulestore.tests.utils import TILDA_FILES_DICT, add_temp_files_from_dict, remove_temp_files_from_list
from xmodule.modulestore.xml import XMLModuleStore
from xmodule.tests import DATA_DIR
from xmodule.x_module import XModuleMixin


def glob_tildes_at_end(path):
    """
    A wrapper for the `glob.glob` function, but it always returns
    files that end in a tilde (~) at the end of the list of results.
    """
    result = glob(path)
    with_tildes = [f for f in result if f.endswith("~")]
    no_tildes = [f for f in result if not f.endswith("~")]
    return no_tildes + with_tildes


class TestXMLModuleStore(TestCase):
    """
    Test around the XML modulestore
    """

    @patch('xmodule.tabs.CourseTabList.initialize_default', Mock())
    def test_unicode_chars_in_xml_content(self):
        # edX/full/6.002_Spring_2012 has non-ASCII chars, and during
        # uniquification of names, would raise a UnicodeError. It no longer does.

        # Ensure that there really is a non-ASCII character in the course.
        with open(os.path.join(DATA_DIR, "toy/sequential/vertical_sequential.xml"), 'rb') as xmlf:
            xml = xmlf.read()
            with pytest.raises(UnicodeDecodeError):
                xml.decode('ascii')

        # Load the course, but don't make error blocks.  This will succeed,
        # but will record the errors.
        modulestore = XMLModuleStore(
            DATA_DIR,
            source_dirs=['toy'],
            xblock_mixins=(XModuleMixin,),
            load_error_blocks=False)

        # Look up the errors during load. There should be none.
        errors = modulestore.get_course_errors(CourseKey.from_string("edX/toy/2012_Fall"))
        assert errors == []

    def test_get_courses_for_wiki(self):
        """
        Test the get_courses_for_wiki method
        """
        store = XMLModuleStore(DATA_DIR, source_dirs=['toy', 'simple'], xblock_mixins=(XModuleMixin,))
        for course in store.get_courses():
            course_locations = store.get_courses_for_wiki(course.wiki_slug)
            assert len(course_locations) == 1
            assert course.location.course_key in course_locations

        course_locations = store.get_courses_for_wiki('no_such_wiki')
        assert len(course_locations) == 0

        # now set toy course to share the wiki with simple course
        toy_course = store.get_course(CourseKey.from_string('edX/toy/2012_Fall'))
        toy_course.wiki_slug = 'simple'

        course_locations = store.get_courses_for_wiki('toy')
        assert len(course_locations) == 0

        course_locations = store.get_courses_for_wiki('simple')
        assert len(course_locations) == 2
        for course_number in ['toy', 'simple']:
            assert CourseKey.from_string('/'.join(['edX', course_number, '2012_Fall'])) in course_locations

    def test_has_course(self):
        """
        Test the has_course method
        """
        check_has_course_method(
            XMLModuleStore(DATA_DIR, source_dirs=['toy', 'simple'], xblock_mixins=(XModuleMixin,)),
            CourseKey.from_string('edX/toy/2012_Fall'),
            locator_key_fields=CourseLocator.KEY_FIELDS
        )

    def test_branch_setting(self):
        """
        Test the branch setting context manager
        """
        store = XMLModuleStore(DATA_DIR, source_dirs=['toy'], xblock_mixins=(XModuleMixin,))
        course = store.get_courses()[0]

        # XML store allows published_only branch setting
        with store.branch_setting(ModuleStoreEnum.Branch.published_only, course.id):
            store.get_item(course.location)

        # XML store does NOT allow draft_preferred branch setting
        with pytest.raises(ValueError):  # noqa: PT011
            with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course.id):
                # verify that the above context manager raises a ValueError
                pass  # pragma: no cover

    @patch('xmodule.modulestore.xml.log')
    def test_dag_course(self, mock_logging):
        """
        Test a course whose structure is not a tree.
        """
        store = XMLModuleStore(
            DATA_DIR,
            source_dirs=['xml_dag'],
            xblock_mixins=(XModuleMixin,),
        )
        course_key = store.get_courses()[0].id

        mock_logging.warning.assert_called_with(
            "%s has more than one definition", course_key.make_usage_key('discussion', 'duplicate_def')
        )

        shared_item_loc = course_key.make_usage_key('html', 'toyhtml')
        shared_item = store.get_item(shared_item_loc)
        parent = shared_item.get_parent()
        assert parent is not None, 'get_parent failed to return a value'
        parent_loc = course_key.make_usage_key('vertical', 'vertical_test')
        assert parent.location == parent_loc
        assert shared_item.location in [x.location for x in parent.get_children()]
        # ensure it's still a child of the other parent even tho it doesn't claim the other parent as its parent
        other_parent_loc = course_key.make_usage_key('vertical', 'zeta')
        other_parent = store.get_item(other_parent_loc)
        # children rather than get_children b/c the instance returned by get_children != shared_item
        assert shared_item_loc in other_parent.children


class TestModuleStoreIgnore(TestXMLModuleStore):  # pylint: disable=missing-class-docstring, test-inherits-tests
    course_dir = DATA_DIR / "course_ignore"

    def setUp(self):
        super().setUp()
        self.addCleanup(remove_temp_files_from_list, list(TILDA_FILES_DICT.keys()), self.course_dir / "static")
        add_temp_files_from_dict(TILDA_FILES_DICT, self.course_dir / "static")

    @patch("xmodule.modulestore.xml.glob.glob", side_effect=glob_tildes_at_end)
    def test_tilde_files_ignored(self, _fake_glob):  # noqa: PT019
        modulestore = XMLModuleStore(
            DATA_DIR, source_dirs=["course_ignore"], xblock_mixins=(XModuleMixin,), load_error_blocks=False
        )
        about_location = CourseKey.from_string('edX/course_ignore/2014_Fall').make_usage_key(
            'about', 'index',
        )
        about_block = modulestore.get_item(about_location)
        assert 'GREEN' in about_block.data
        assert 'RED' not in about_block.data
