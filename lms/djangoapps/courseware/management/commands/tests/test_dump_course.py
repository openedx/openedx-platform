"""
Tests for Django management commands
"""

import datetime
import json
from io import StringIO

import factory
import pytz
from django.conf import settings
from django.core.management import call_command

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    SharedModuleStoreTestCase
)
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory
from xmodule.modulestore.xml_importer import import_course_from_xml

DATA_DIR = settings.COMMON_TEST_DATA_ROOT
XML_COURSE_DIRS = ['simple']
TEST_COURSE_START = datetime.datetime(2012, 7, 1, tzinfo=pytz.UTC)
TEST_COURSE_END = datetime.datetime(2012, 12, 31, tzinfo=pytz.UTC)


class CommandsTestBase(SharedModuleStoreTestCase):
    """
    Base class for testing different django commands.

    Must be subclassed using override_settings set to the modulestore
    to be tested.

    """
    __test__ = False
    url_name = '2012_Fall'
    ENABLED_SIGNALS = ['course_published']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_course_key = modulestore().make_course_key("edX", "simple", "2012_Fall")
        cls.loaded_courses = cls.load_courses()

    @classmethod
    def load_courses(cls):
        """
        Load test courses and return list of ids
        """
        store = modulestore()

        unique_org = factory.Sequence(lambda n: 'edX.%d' % n)
        cls.course = CourseFactory.create(
            emit_signals=True,
            org=unique_org,
            course='simple',
            run="run",
            display_name='2012_Fáĺĺ',
            modulestore=store,
            start=TEST_COURSE_START,
            end=TEST_COURSE_END,
        )

        cls.discussion = BlockFactory.create(
            category='discussion', parent_location=cls.course.location
        )

        courses = store.get_courses()
        # NOTE: if xml store owns these, it won't import them into mongo
        if cls.test_course_key not in [c.id for c in courses]:
            import_course_from_xml(
                store, ModuleStoreEnum.UserID.mgmt_command, DATA_DIR, XML_COURSE_DIRS, create_if_not_present=True
            )

        return [course.id for course in store.get_courses()]

    def call_command(self, name, *args, **kwargs):
        """
        Call management command and return output
        """
        out = StringIO()  # To Capture the output of the command
        call_command(name, *args, stdout=out, **kwargs)
        out.seek(0)
        return out.read()

    def test_dump_course_ids_with_filter(self):
        """
        Test that `dump_course_ids_with_filter` works correctly by
        only returning courses that have not ended before the provided `end` data.

        `load_courses` method creates two courses first by calling CourseFactory.create
        which creates a course with end=2012-12-31. Then it creates a second course
        by calling import_course_from_xml which creates a course with end=None.

        This test makes sure that only the second course is returned when
        `end`=2013-01-01 is passed to `dump_course_ids_with_filter`.
        """
        args = []
        kwargs = {'end': '2013-01-01'}  # exclude any courses which have ended before 2013-01-01
        output = self.call_command('dump_course_ids_with_filter', *args, **kwargs)
        dumped_courses = output.strip().split('\n')
        dumped_ids = set(dumped_courses)
        assert {str(self.test_course_key)} == dumped_ids

    def test_dump_course_ids(self):
        output = self.call_command('dump_course_ids')
        dumped_courses = (output.strip() or []) and output.strip().split('\n')
        course_ids = {str(course_id) for course_id in self.loaded_courses}
        dumped_ids = set(dumped_courses)
        assert course_ids == dumped_ids

    def test_correct_course_structure_metadata(self):
        course_id = str(self.test_course_key)
        args = [course_id]
        kwargs = {'modulestore': 'default'}

        try:
            output = self.call_command('dump_course_structure', *args, **kwargs)
        except TypeError as exception:
            self.fail(exception)

        dump = json.loads(output)
        assert len(list(dump.values())) > 0

    def test_dump_course_structure(self):
        args = [str(self.test_course_key)]
        kwargs = {'modulestore': 'default'}
        output = self.call_command('dump_course_structure', *args, **kwargs)

        dump = json.loads(output)

        # check that all elements in the course structure have metadata,
        # but not inherited metadata:
        for element in dump.values():
            assert 'metadata' in element
            assert 'children' in element
            assert 'category' in element
            assert 'inherited_metadata' not in element

        # Check a few elements in the course dump
        test_course_key = self.test_course_key
        parent_id = str(test_course_key.make_usage_key('chapter', 'Overview'))
        assert dump[parent_id]['category'] == 'chapter'
        assert len(dump[parent_id]['children']) == 3

        child_id = dump[parent_id]['children'][1]
        assert dump[child_id]['category'] == 'sequential'
        assert len(dump[child_id]['children']) == 2

        video_id = str(test_course_key.make_usage_key('video', 'Welcome'))
        assert dump[video_id]['category'] == 'video'
        video_metadata = dump[video_id]['metadata']
        video_metadata.pop('edx_video_id', None)
        self.assertCountEqual(
            list(video_metadata.keys()),
            ['youtube_id_1_0']
        )
        assert 'youtube_id_1_0' in dump[video_id]['metadata']

        # Check if there are the right number of elements

        assert len(dump) == 17

    def test_dump_inherited_course_structure(self):
        args = [str(self.test_course_key)]
        kwargs = {'modulestore': 'default', 'inherited': True}
        output = self.call_command('dump_course_structure', *args, **kwargs)
        dump = json.loads(output)
        # check that all elements in the course structure have inherited metadata,
        # and that it contains a particular value as well:
        for element in dump.values():
            assert 'metadata' in element
            assert 'children' in element
            assert 'category' in element
            assert 'inherited_metadata' in element
            # ... but does not contain inherited metadata containing a default value:
            assert 'due' not in element['inherited_metadata']

    def test_dump_inherited_course_structure_with_defaults(self):
        args = [str(self.test_course_key)]
        kwargs = {'modulestore': 'default', 'inherited': True, 'inherited_defaults': True}
        output = self.call_command('dump_course_structure', *args, **kwargs)
        dump = json.loads(output)
        # check that all elements in the course structure have inherited metadata,
        # and that it contains a particular value as well:
        for element in dump.values():
            assert 'metadata' in element
            assert 'children' in element
            assert 'category' in element
            assert 'inherited_metadata' in element
            # ... and contains inherited metadata containing a default value:
            assert element['inherited_metadata']['due'] is None

    def test_export_discussion_ids(self):
        output = self.call_command('dump_course_structure', str(self.course.id))
        dump = json.loads(output)
        dumped_id = dump[str(self.discussion.location)]['metadata']['discussion_id']
        assert dumped_id == self.discussion.discussion_id

    def test_export_discussion_id_custom_id(self):
        output = self.call_command('dump_course_structure', str(self.test_course_key))
        dump = json.loads(output)
        discussion_key = str(self.test_course_key.make_usage_key('discussion', 'custom_id'))
        dumped_id = dump[str(discussion_key)]['metadata']['discussion_id']
        assert dumped_id == 'custom'

    def check_export_file(self, tar_file):  # pylint: disable=missing-function-docstring
        names = tar_file.getnames()

        # Check if some of the files are present.

        # The rest is of the code should be covered by the tests for
        # xmodule.modulestore.xml_exporter, used by the dump_course command

        assert_in = self.assertIn
        assert_in('edX-simple-2012_Fall', names)
        assert_in(f'edX-simple-2012_Fall/policies/{self.url_name}/policy.json', names)
        assert_in('edX-simple-2012_Fall/html/toylab.html', names)
        assert_in('edX-simple-2012_Fall/sequential/A_simple_sequence.xml', names)
        assert_in('edX-simple-2012_Fall/sequential/Lecture_2.xml', names)


class CommandSplitMongoTestCase(CommandsTestBase):
    """
    Test case for management commands using the mixed mongo modulestore with split as the default.

    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    __test__ = True
    url_name = 'course'
