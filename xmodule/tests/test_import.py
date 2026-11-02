# lint-amnesty, pylint: disable=missing-module-docstring


import datetime
from tempfile import mkdtemp
from unittest.mock import Mock, patch

import ddt
from django.test import TestCase
from fs.osfs import OSFS
from lxml import etree
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from pytz import UTC
from xblock.core import XBlock
from xblock.fields import Integer, Scope, String
from xblock.runtime import DictKeyValueStore, KvsFieldData

from xmodule.fields import Date
from xmodule.modulestore.inheritance import InheritanceMixin, compute_inherited_metadata
from xmodule.modulestore.xml import XMLImportingModuleStoreRuntime, LibraryXMLModuleStore, XMLModuleStore
from xmodule.tests import DATA_DIR
from xmodule.x_module import XModuleMixin
from xmodule.xml_block import is_pointer_tag

ORG = 'test_org'
COURSE = 'test_course'
RUN = 'test_run'


class DummyModuleStoreRuntime(XMLImportingModuleStoreRuntime):  # pylint: disable=abstract-method, missing-class-docstring
    """
    Minimal modulestore runtime for tests
    """

    @patch('xmodule.modulestore.xml.OSFS', lambda dir: OSFS(mkdtemp()))
    def __init__(self, load_error_blocks, library=False):

        if library:
            xmlstore = LibraryXMLModuleStore("data_dir", source_dirs=[], load_error_blocks=load_error_blocks)
        else:
            xmlstore = XMLModuleStore("data_dir", source_dirs=[], load_error_blocks=load_error_blocks)
        course_id = CourseKey.from_string('/'.join([ORG, COURSE, RUN]))
        course_dir = "test_dir"
        error_tracker = Mock()

        super().__init__(
            xmlstore=xmlstore,
            course_id=course_id,
            course_dir=course_dir,
            error_tracker=error_tracker,
            load_error_blocks=load_error_blocks,
            mixins=(InheritanceMixin, XModuleMixin),
            services={'field-data': KvsFieldData(DictKeyValueStore())},
        )


class BaseCourseTestCase(TestCase):
    '''Make sure block imports work properly, including for malformed inputs'''

    @staticmethod
    def get_system(load_error_blocks=True, library=False):
        '''Get a dummy system'''
        return DummyModuleStoreRuntime(load_error_blocks, library=library)

    def get_course(self, name):
        """Get a test course by directory name.  If there's more than one, error."""
        print(f"Importing {name}")

        modulestore = XMLModuleStore(
            DATA_DIR,
            source_dirs=[name],
            xblock_mixins=(InheritanceMixin,),
        )
        courses = modulestore.get_courses()
        assert len(courses) == 1
        return courses[0]


class GenericXBlock(XBlock):
    """XBlock for testing pure xblock xml import"""
    has_children = True
    field1 = String(default="something", scope=Scope.user_state)
    field2 = Integer(scope=Scope.user_state)


@ddt.ddt
class PureXBlockImportTest(BaseCourseTestCase):
    """
    Tests of import pure XBlocks (not XModules) from xml
    """

    def assert_xblocks_are_good(self, block):
        """Assert a number of conditions that must be true for `block` to be good."""
        scope_ids = block.scope_ids
        assert scope_ids.usage_id is not None
        assert scope_ids.def_id is not None

        for child_id in block.children:
            child = block.runtime.get_block(child_id)
            self.assert_xblocks_are_good(child)

    @XBlock.register_temp_plugin(GenericXBlock)
    @ddt.data(
        "<genericxblock/>",
        "<genericxblock field1='abc' field2='23' />",
        "<genericxblock field1='abc' field2='23'><genericxblock/></genericxblock>",
    )
    @patch('xmodule.x_module.XModuleMixin.location')
    def test_parsing_pure_xblock(self, xml, mock_location):
        system = self.get_system(load_error_blocks=False)
        block = system.process_xml(xml)
        assert isinstance(block, GenericXBlock)
        self.assert_xblocks_are_good(block)
        assert not mock_location.called


class ImportTestCase(BaseCourseTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    date = Date()

    def test_fallback(self):
        '''Check that malformed xml loads as an ErrorBlock.'''

        # Use an exotic character to also flush out Unicode issues.
        bad_xml = '''<sequential display_name="oops\N{SNOWMAN}"><video url="hi"></sequential>'''
        system = self.get_system()

        block = system.process_xml(bad_xml)

        assert block.__class__.__name__ == 'ErrorBlockWithMixins'

    def test_unique_url_names(self):
        '''Check that each error gets its very own url_name'''
        bad_xml = '''<sequential display_name="oops"><video url="hi"></sequential>'''
        bad_xml2 = '''<sequential url_name="oops"><video url="hi"></sequential>'''
        system = self.get_system()

        block1 = system.process_xml(bad_xml)
        block2 = system.process_xml(bad_xml2)

        assert block1.location != block2.location

        # Check that each vertical gets its very own url_name
        bad_xml = '''<vertical display_name="abc"><problem url_name="exam1:2013_Spring:abc"/></vertical>'''
        bad_xml2 = '''<vertical display_name="abc"><problem url_name="exam2:2013_Spring:abc"/></vertical>'''

        block1 = system.process_xml(bad_xml)
        block2 = system.process_xml(bad_xml2)

        assert block1.location != block2.location

    def test_reimport(self):
        '''Make sure an already-exported error xml tag loads properly'''

        self.maxDiff = None
        bad_xml = '''<sequential display_name="oops"><video url="hi"></sequential>'''
        system = self.get_system()
        block = system.process_xml(bad_xml)

        node = etree.Element('unknown')
        block.add_xml_to_node(node)
        re_import_block = system.process_xml(etree.tostring(node))

        assert re_import_block.__class__.__name__ == 'ErrorBlockWithMixins'

        assert block.contents == re_import_block.contents
        assert block.error_msg == re_import_block.error_msg

    def test_fixed_xml_tag(self):
        """Make sure a tag that's been fixed exports as the original tag type"""

        # create a error tag with valid xml contents
        root = etree.Element('error')
        good_xml = '''<sequential display_name="fixed"><video url="hi"/></sequential>'''
        root.text = good_xml

        xml_str_in = etree.tostring(root)

        # load it
        system = self.get_system()
        block = system.process_xml(xml_str_in)

        # export it
        node = etree.Element('unknown')
        block.add_xml_to_node(node)

        # Now make sure the exported xml is a sequential
        assert node.tag == 'sequential'

    def course_block_inheritance_check(self, block, from_date_string, unicorn_color, course_run=RUN):
        """
        Checks to make sure that metadata inheritance on a course block is respected.
        """
        # pylint: disable=protected-access
        print((block, block._field_data))
        assert block.due == ImportTestCase.date.from_json(from_date_string)

        # Check that the child inherits due correctly
        child = block.get_children()[0]
        assert child.due == ImportTestCase.date.from_json(from_date_string)
        # need to convert v to canonical json b4 comparing
        assert ImportTestCase.date.to_json(ImportTestCase.date.from_json(from_date_string)) ==\
               child.xblock_kvs.inherited_settings['due']

        # Now export and check things
        file_system = OSFS(mkdtemp())
        block.runtime.export_fs = file_system.makedir('course', recreate=True)
        node = etree.Element('unknown')
        block.add_xml_to_node(node)

        # Check that the exported xml is just a pointer
        print(("Exported xml:", etree.tostring(node)))
        assert is_pointer_tag(node)
        # but it's a special case course pointer
        assert node.attrib['course'] == COURSE
        assert node.attrib['org'] == ORG

        # Does the course still have unicorns?
        with block.runtime.export_fs.open(f'course/{course_run}.xml') as f:
            course_xml = etree.fromstring(f.read())

        assert course_xml.attrib['unicorn'] == unicorn_color

        # the course and org tags should be _only_ in the pointer
        assert 'course' not in course_xml.attrib
        assert 'org' not in course_xml.attrib

        # did we successfully strip the url_name from the definition contents?
        assert 'url_name' not in course_xml.attrib

        # Does the chapter tag now have a due attribute?
        # hardcoded path to child
        with block.runtime.export_fs.open('chapter/ch.xml') as f:
            chapter_xml = etree.fromstring(f.read())
        assert chapter_xml.tag == 'chapter'
        assert 'due' not in chapter_xml.attrib

    def test_metadata_import_export(self):
        """Two checks:
            - unknown metadata is preserved across import-export
            - inherited metadata doesn't leak to children.
        """
        system = self.get_system()
        from_date_string = 'March 20 17:00'
        url_name = 'test1'
        unicorn_color = 'purple'
        start_xml = '''
        <course org="{org}" course="{course}"
                due="{due}" url_name="{url_name}" unicorn="{unicorn_color}">
            <chapter url="hi" url_name="ch" display_name="CH">
                <html url_name="h" display_name="H">Two houses, ...</html>
            </chapter>
        </course>'''.format(
            due=from_date_string, org=ORG, course=COURSE, url_name=url_name, unicorn_color=unicorn_color
        )
        block = system.process_xml(start_xml)
        compute_inherited_metadata(block)
        self.course_block_inheritance_check(block, from_date_string, unicorn_color)

    def test_library_metadata_import_export(self):
        """Two checks:
            - unknown metadata is preserved across import-export
            - inherited metadata doesn't leak to children.
        """
        system = self.get_system(library=True)
        from_date_string = 'March 26 17:00'
        url_name = 'test2'
        unicorn_color = 'rainbow'
        start_xml = '''
        <library org="TestOrg" library="TestLib" display_name="stuff">
            <course org="{org}" course="{course}"
                due="{due}" url_name="{url_name}" unicorn="{unicorn_color}">
                <chapter url="hi" url_name="ch" display_name="CH">
                    <html url_name="h" display_name="H">Two houses, ...</html>
                </chapter>
            </course>
        </library>'''.format(
            due=from_date_string, org=ORG, course=COURSE, url_name=url_name, unicorn_color=unicorn_color
        )
        block = system.process_xml(start_xml)

        compute_inherited_metadata(block)
        # Check the course block, since it has inheritance
        block = block.get_children()[0]
        self.course_block_inheritance_check(block, from_date_string, unicorn_color)

    def test_metadata_no_inheritance(self):
        """
        Checks that default value of None (for due) does not get marked as inherited when a
        course is the root block.
        """
        system = self.get_system()
        url_name = 'test1'
        start_xml = '''
        <course org="{org}" course="{course}"
                url_name="{url_name}" unicorn="purple">
            <chapter url="hi" url_name="ch" display_name="CH">
                <html url_name="h" display_name="H">Two houses, ...</html>
            </chapter>
        </course>'''.format(org=ORG, course=COURSE, url_name=url_name)
        block = system.process_xml(start_xml)
        compute_inherited_metadata(block)
        self.course_block_no_inheritance_check(block)

    def test_library_metadata_no_inheritance(self):
        """
        Checks that the default value of None (for due) does not get marked as inherited when a
        library is the root block.
        """
        system = self.get_system()
        url_name = 'test1'
        start_xml = '''
        <library org="TestOrg" library="TestLib" display_name="stuff">
            <course org="{org}" course="{course}"
                    url_name="{url_name}" unicorn="purple">
                <chapter url="hi" url_name="ch" display_name="CH">
                    <html url_name="h" display_name="H">Two houses, ...</html>
                </chapter>
            </course>
        </library>'''.format(org=ORG, course=COURSE, url_name=url_name)
        block = system.process_xml(start_xml)
        compute_inherited_metadata(block)
        # Run the checks on the course node instead.
        block = block.get_children()[0]
        self.course_block_no_inheritance_check(block)

    def course_block_no_inheritance_check(self, block):
        """
        Verifies that a default value of None (for due) does not get marked as inherited.
        """
        assert block.due is None

        # Check that the child does not inherit a value for due
        child = block.get_children()[0]
        assert child.due is None

        # Check that the child hasn't started yet
        assert datetime.datetime.now(UTC) <= child.start

    def override_metadata_check(self, block, child, course_due, child_due):
        """
        Verifies that due date can be overriden at child level.
        """
        assert block.due == ImportTestCase.date.from_json(course_due)
        assert child.due == ImportTestCase.date.from_json(child_due)
        # Test inherited metadata. Due does not appear here (because explicitly set on child).
        assert ImportTestCase.date.to_json(ImportTestCase.date.from_json(course_due)) == child.xblock_kvs.inherited_settings['due']  # pylint: disable=line-too-long

    def test_metadata_override_default(self):
        """
        Checks that due date can be overriden at child level when a course is the root.
        """
        system = self.get_system()
        course_due = 'March 20 17:00'
        child_due = 'April 10 00:00'
        url_name = 'test1'
        start_xml = '''
        <course org="{org}" course="{course}"
                due="{due}" url_name="{url_name}" unicorn="purple">
            <chapter url="hi" url_name="ch" display_name="CH">
                <html url_name="h" display_name="H">Two houses, ...</html>
            </chapter>
        </course>'''.format(due=course_due, org=ORG, course=COURSE, url_name=url_name)
        block = system.process_xml(start_xml)
        child = block.get_children()[0]
        # pylint: disable=protected-access
        child._field_data.set(child, 'due', child_due)
        compute_inherited_metadata(block)
        self.override_metadata_check(block, child, course_due, child_due)

    def test_library_metadata_override_default(self):
        """
        Checks that due date can be overriden at child level when a library is the root.
        """
        system = self.get_system()
        course_due = 'March 20 17:00'
        child_due = 'April 10 00:00'
        url_name = 'test1'
        start_xml = '''
        <library org="TestOrg" library="TestLib" display_name="stuff">
            <course org="{org}" course="{course}"
                    due="{due}" url_name="{url_name}" unicorn="purple">
                <chapter url="hi" url_name="ch" display_name="CH">
                    <html url_name="h" display_name="H">Two houses, ...</html>
                </chapter>
            </course>
        </library>'''.format(due=course_due, org=ORG, course=COURSE, url_name=url_name)
        block = system.process_xml(start_xml)
        # Chapter is two levels down here.
        child = block.get_children()[0].get_children()[0]
        # pylint: disable=protected-access
        child._field_data.set(child, 'due', child_due)
        compute_inherited_metadata(block)
        block = block.get_children()[0]
        self.override_metadata_check(block, child, course_due, child_due)

    def test_is_pointer_tag(self):
        """
        Check that is_pointer_tag works properly.
        """

        yes = ["""<html url_name="blah"/>""",
               """<html url_name="blah"></html>""",
               """<html url_name="blah">    </html>""",
               """<problem url_name="blah"/>""",
               """<course org="HogwartsX" course="Mathemagics" url_name="3.14159"/>"""]

        no = ["""<html url_name="blah" also="this"/>""",
              """<html url_name="blah">some text</html>""",
              """<problem url_name="blah"><sub>tree</sub></problem>""",
              """<course org="HogwartsX" course="Mathemagics" url_name="3.14159">
                     <chapter>3</chapter>
                  </course>
              """]

        for xml_str in yes:
            print(f"should be True for {xml_str}")
            assert is_pointer_tag(etree.fromstring(xml_str))

        for xml_str in no:
            print(f"should be False for {xml_str}")
            assert not is_pointer_tag(etree.fromstring(xml_str))

    def test_metadata_inherit(self):
        """Make sure that metadata is inherited properly"""

        print("Starting import")
        course = self.get_course('toy')

        def check_for_key(key, node, value):
            "recursive check for presence of key"
            print(f"Checking {str(node.location)}")
            assert getattr(node, key) == value
            for c in node.get_children():
                check_for_key(key, c, value)

        check_for_key('graceperiod', course, course.graceperiod)

    def test_policy_loading(self):
        """Make sure that when two courses share content with the same
        org and course names, policy applies to the right one."""

        toy = self.get_course('toy')
        two_toys = self.get_course('two_toys')

        assert toy.url_name == '2012_Fall'
        assert two_toys.url_name == 'TT_2012_Fall'

        toy_ch = toy.get_children()[0]
        two_toys_ch = two_toys.get_children()[0]

        assert toy_ch.display_name == 'Overview'
        assert two_toys_ch.display_name == 'Two Toy Overview'

        # Also check that the grading policy loaded
        assert two_toys.grade_cutoffs['C'] == 0.5999

        # Also check that keys from policy are run through the
        # appropriate attribute maps -- 'graded' should be True, not 'true'
        assert toy.graded is True

    def test_static_tabs_import(self):
        """Make sure that the static tabs are imported correctly"""

        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['toy'])

        location_tab_syllabus = BlockUsageLocator(CourseLocator("edX", "toy", "2012_Fall", deprecated=True),
                                                  "static_tab", "syllabus", deprecated=True)
        toy_tab_syllabus = modulestore.get_item(location_tab_syllabus)
        assert toy_tab_syllabus.display_name == 'Syllabus'
        assert toy_tab_syllabus.course_staff_only is False

        location_tab_resources = BlockUsageLocator(CourseLocator("edX", "toy", "2012_Fall", deprecated=True),
                                                   "static_tab", "resources", deprecated=True)
        toy_tab_resources = modulestore.get_item(location_tab_resources)
        assert toy_tab_resources.display_name == 'Resources'
        assert toy_tab_resources.course_staff_only is True

    def test_definition_loading(self):
        """When two courses share the same org and course name and
        both have a block with the same url_name, the definitions shouldn't clash.

        TODO (vshnayder): once we have a CMS, this shouldn't
        happen--locations should uniquely name definitions.  But in
        our imperfect XML world, it can (and likely will) happen."""

        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['toy', 'two_toys'])

        location = BlockUsageLocator(CourseLocator("edX", "toy", "2012_Fall", deprecated=True),
                                     "video", "Welcome", deprecated=True)
        toy_video = modulestore.get_item(location)
        location_two = BlockUsageLocator(CourseLocator("edX", "toy", "TT_2012_Fall", deprecated=True),
                                         "video", "Welcome", deprecated=True)
        two_toy_video = modulestore.get_item(location_two)
        assert toy_video.youtube_id_1_0 == 'p2Q6BrNhdh8'
        assert two_toy_video.youtube_id_1_0 == 'p2Q6BrNhdh9'

    def test_colon_in_url_name(self):
        """Ensure that colons in url_names convert to file paths properly"""

        print("Starting import")
        # Not using get_courses because we need the modulestore object too afterward
        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['toy'])
        courses = modulestore.get_courses()
        assert len(courses) == 1
        course = courses[0]

        print("course errors:")
        for (msg, err) in modulestore.get_course_errors(course.id):
            print(msg)
            print(err)

        chapters = course.get_children()
        assert len(chapters) == 5

        ch2 = chapters[1]
        assert ch2.url_name == 'secret:magic'

        print("Ch2 location: ", ch2.location)

        also_ch2 = modulestore.get_item(ch2.location)
        assert ch2 == also_ch2

        print("making sure html loaded")
        loc = course.id.make_usage_key('html', 'secret:toylab')
        html = modulestore.get_item(loc)
        assert html.display_name == 'Toy lab'

    def test_unicode(self):
        """Check that courses with unicode characters in filenames and in
        org/course/name import properly. Currently, this means: (a) Having
        files with unicode names does not prevent import; (b) if files are not
        loaded because of unicode filenames, there are appropriate
        exceptions/errors to that effect."""

        print("Starting import")
        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['test_unicode'])
        courses = modulestore.get_courses()
        assert len(courses) == 1
        course = courses[0]

        print("course errors:")

        # Expect to find an error/exception about characters in "®esources"
        expect = "InvalidKeyError"
        errors = modulestore.get_course_errors(course.id)

        assert any(((expect in msg) or (expect in err)) for (msg, err) in errors)
        chapters = course.get_children()
        assert len(chapters) == 4

    def test_url_name_mangling(self):
        """
        Make sure that url_names are only mangled once.
        """

        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['toy'])

        toy_id = CourseKey.from_string('edX/toy/2012_Fall')

        course = modulestore.get_course(toy_id)
        chapters = course.get_children()
        ch1 = chapters[0]
        sections = ch1.get_children()

        assert len(sections) == 4

        for i in (2, 3):
            video = sections[i]
            # Name should be 'video_{hash}'
            print(f"video {i} url_name: {video.url_name}")
            assert len(video.url_name) == (len('video_') + 12)

    def test_poll_and_conditional_import(self):
        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['conditional_and_poll'])

        course = modulestore.get_courses()[0]
        chapters = course.get_children()
        ch1 = chapters[0]
        sections = ch1.get_children()

        assert len(sections) == 1

        conditional_location = course.id.make_usage_key('conditional', 'condone')
        block = modulestore.get_item(conditional_location)
        assert len(block.children) == 1

        poll_location = course.id.make_usage_key('poll_question', 'first_poll')
        block = modulestore.get_item(poll_location)
        assert len(block.get_children()) == 0
        assert block.voted is False
        assert block.poll_answer == ''
        assert block.poll_answers == {}
        assert block.answers ==\
               [{'text': 'Yes', 'id': 'Yes'}, {'text': 'No', 'id': 'No'}, {'text': "Don't know", 'id': 'Dont_know'}]

    def test_error_on_import(self):
        '''Check that when load_error_block is false, an exception is raised, rather than returning an ErrorBlock'''

        bad_xml = '''<sequential display_name="oops"><video url="hi"></sequential>'''
        system = self.get_system(False)

        self.assertRaises(etree.XMLSyntaxError, system.process_xml, bad_xml)

    def test_word_cloud_import(self):
        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['word_cloud'])

        course = modulestore.get_courses()[0]
        chapters = course.get_children()
        ch1 = chapters[0]
        sections = ch1.get_children()

        assert len(sections) == 1

        location = course.id.make_usage_key('word_cloud', 'cloud1')
        block = modulestore.get_item(location)
        assert len(block.get_children()) == 0
        assert block.num_inputs == 5
        assert block.num_top_words == 250

    def test_cohort_config(self):
        """
        Check that cohort config parsing works right.

        Note: The cohort config on the CourseBlock is no longer used.
        See openedx.core.djangoapps.course_groups.models.CourseCohortSettings.
        """
        modulestore = XMLModuleStore(DATA_DIR, source_dirs=['toy'])

        toy_id = CourseKey.from_string('edX/toy/2012_Fall')

        course = modulestore.get_course(toy_id)

        # No config -> False
        assert not course.is_cohorted

        # empty config -> False
        course.cohort_config = {}
        assert not course.is_cohorted

        # false config -> False
        course.cohort_config = {'cohorted': False}
        assert not course.is_cohorted

        # and finally...
        course.cohort_config = {'cohorted': True}
        assert course.is_cohorted
