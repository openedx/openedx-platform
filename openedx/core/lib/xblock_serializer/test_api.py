"""
Test for the XBlock serialization lib's API
"""
from xml.etree import ElementTree

from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.django import contentstore, modulestore
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase, upload_file_to_course
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, ToyCourseFactory, LibraryFactory
from xmodule.util.sandboxing import DEFAULT_PYTHON_LIB_FILENAME
from openedx_tagging.models import Tag
from openedx.core.djangoapps.content_tagging.models import TaxonomyOrg
from openedx.core.djangoapps.content_tagging import api as tagging_api

from . import api
from .block_serializer import XBlockSerializer


# The expected OLX string for the 'Toy_Videos' sequential in the toy course
EXPECTED_SEQUENTIAL_OLX = """
<sequential display_name="Toy Videos" format="Lecture Sequence" url_name="Toy_Videos" copied_from_block="block-v1:edX+toy+2012_Fall+type@sequential+block@Toy_Videos">
  <html url_name="secret:toylab" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@secret:toylab" display_name="Toy lab"><![CDATA[
<b>Lab 2A: Superposition Experiment</b>


<p>Isn't the toy course great?</p>

<p>Let's add some markup that uses non-ascii characters.
'For example, we should be able to write words like encyclop&aelig;dia, or foreign words like fran&ccedil;ais.
Looking beyond latin-1, we should handle math symbols:  &pi;r&sup2 &le; &#8734.
And it shouldn't matter if we use entities or numeric codes &mdash; &Omega; &ne; &pi; &equiv; &#937; &#8800; &#960;.
</p>


]]></html>
  <html url_name="toyjumpto" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@toyjumpto" display_name="Text"><![CDATA[
<a href="/jump_to_id/vertical_test">This is a link to another page and some Chinese 四節比分和七年前</a> <p>Some more Chinese 四節比分和七年前</p>

]]></html>
  <html url_name="toyhtml" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@toyhtml" display_name="Text"><![CDATA[
<a href='/static/handouts/sample_handout.txt'>Sample</a>
]]></html>
  <html url_name="nonportable" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@nonportable" display_name="Text"><![CDATA[
<a href="/static/foo.jpg">link</a>

]]></html>
  <html url_name="nonportable_link" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@nonportable_link" display_name="Text"><![CDATA[
<a href="/jump_to_id/nonportable_link">link</a>


]]></html>
  <html url_name="badlink" display_name="Text" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@badlink"><![CDATA[
<img src="/static//file.jpg" />

]]></html>
  <html url_name="with_styling" display_name="Text" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@with_styling"><![CDATA[
<p style="font:italic bold 72px/30px Georgia, serif; color: red; ">Red text here</p>
]]></html>
  <html url_name="just_img" display_name="Text" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@just_img"><![CDATA[
<img src="/static/foo_bar.jpg" />
]]></html>
  <video
    copied_from_block="block-v1:edX+toy+2012_Fall+type@video+block@Video_Resources"
    display_name="Video Resources"
    url_name="Video_Resources"
    youtube="1.00:1bK-WdDi6Qw"
    youtube_id_1_0="1bK-WdDi6Qw"
  />
</sequential>
"""


@skip_unless_cms
class XBlockSerializationTestCase(SharedModuleStoreTestCase):
    """
    Test for the XBlock serialization library's python API
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up a course for use in these tests
        """
        super().setUpClass()
        cls.course = ToyCourseFactory.create()

        # Create taxonomies and tags for testing
        cls.taxonomy1 = tagging_api.create_taxonomy(name="t1", enabled=True, export_id="t1-export-id")
        TaxonomyOrg.objects.create(
            taxonomy=cls.taxonomy1,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        cls.taxonomy2 = tagging_api.create_taxonomy(name="t2", enabled=True, export_id="t2-export-id")
        TaxonomyOrg.objects.create(
            taxonomy=cls.taxonomy2,
            rel_type=TaxonomyOrg.RelType.OWNER,
        )
        root1 = Tag.objects.create(taxonomy=cls.taxonomy1, value="ROOT1")
        root2 = Tag.objects.create(taxonomy=cls.taxonomy2, value="ROOT2")
        Tag.objects.create(taxonomy=cls.taxonomy1, value="normal tag", parent=root1)
        Tag.objects.create(taxonomy=cls.taxonomy1, value="<special \"'-=,. |= chars > tag", parent=root1)
        Tag.objects.create(taxonomy=cls.taxonomy1, value="anotherTag", parent=root1)
        Tag.objects.create(taxonomy=cls.taxonomy2, value="tag", parent=root2)
        Tag.objects.create(taxonomy=cls.taxonomy2, value="other tag", parent=root2)

    def assertXmlEqual(self, xml_str_a: str, xml_str_b: str) -> None:
        """ Assert that the given XML strings are equal, ignoring attribute order and some whitespace variations. """
        self.assertEqual(
            ElementTree.canonicalize(xml_str_a, strip_text=True),
            ElementTree.canonicalize(xml_str_b, strip_text=True),
        )

    def test_html_with_static_asset(self):
        """
        Test that HTML gets converted to use CDATA and static assets are
        handled.
        """
        block_id = self.course.id.make_usage_key('html', 'just_img')  # see sample_courses.py
        html_block = modulestore().get_item(block_id)
        serialized = api.serialize_xblock_to_olx(html_block)

        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <html
                copied_from_block="{str(block_id)}"
                display_name="Text"
                url_name="just_img"
            ><![CDATA[
                <img src="/static/foo_bar.jpg" />
            ]]></html>
            """
        )
        self.assertIn("CDATA", serialized.olx_str)
        self.assertEqual(serialized.static_files, [
            api.StaticFile(
                name="foo_bar.jpg",
                url="/asset-v1:edX+toy+2012_Fall+type@asset+block@foo_bar.jpg",
                data=None,
            ),
        ])

    def test_html_with_static_asset_learning_core(self):
        """
        Test the learning-core-specific serialization of an HTML block
        """
        block_id = self.course.id.make_usage_key('html', 'just_img')  # see sample_courses.py
        html_block = modulestore().get_item(block_id)
        serialized = api.serialize_xblock_to_olx(html_block)
        serialized_learning_core = api.serialize_modulestore_block_for_learning_core(html_block)
        self.assertXmlEqual(
            serialized_learning_core.olx_str,
            # For learning core, OLX should never contain "url_name" as that ID
            # is specified by the Component key:
            """
            <html display_name="Text"><![CDATA[
                <img src="/static/foo_bar.jpg" />
            ]]></html>
            """
        )
        self.assertIn("CDATA", serialized.olx_str)
        # Static files should be identical:
        self.assertEqual(serialized.static_files, serialized_learning_core.static_files)

    def test_html_with_fields(self):
        """ Test an HTML Block with non-default fields like editor='raw' """
        course = CourseFactory.create(display_name='test course', run="Testing_course")
        html_block = BlockFactory.create(
            parent_location=course.location,
            category="html",
            display_name="Non-default HTML Block",
            editor="raw",
            use_latex_compiler=True,
            data="🍔",
        )
        serialized = api.serialize_xblock_to_olx(html_block)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <html
                copied_from_block="{str(html_block.location)}"
                url_name="Non-default_HTML_Block"
                display_name="Non-default HTML Block"
                editor="raw"
                use_latex_compiler="true"
            ><![CDATA[
                🍔
            ]]></html>
            """
        )

    def test_export_sequential(self):
        """
        Export a sequential from the toy course, including all of its children.
        """
        sequential_id = self.course.id.make_usage_key('sequential', 'Toy_Videos')  # see sample_courses.py
        sequential = modulestore().get_item(sequential_id)
        serialized = api.serialize_xblock_to_olx(sequential)

        self.assertXmlEqual(serialized.olx_str, EXPECTED_SEQUENTIAL_OLX)

    def test_capa_python_lib(self):
        """ Test capa problem blocks with and without python_lib.zip """
        course = CourseFactory.create(display_name='Python Testing course', run="PY")
        upload_file_to_course(
            course_key=course.id,
            contentstore=contentstore(),
            source_file='./common/test/data/uploads/python_lib.zip',
            target_filename=DEFAULT_PYTHON_LIB_FILENAME,
        )

        regular_problem = BlockFactory.create(
            parent_location=course.location,
            category="problem",
            display_name="Problem No Python",
            max_attempts=3,
            data="<problem><optionresponse></optionresponse></problem>",
        )

        python_problem = BlockFactory.create(
            parent_location=course.location,
            category="problem",
            display_name="Python Problem",
            data='<problem>This uses python: <script type="text/python">...</script>...</problem>',
        )

        # The regular problem doesn't use python so shouldn't contain python_lib.zip:

        serialized = api.serialize_xblock_to_olx(regular_problem)
        assert not serialized.static_files
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <problem
                copied_from_block="{regular_problem.location}"
                display_name="Problem No Python"
                url_name="Problem_No_Python"
                max_attempts="3"
            >
                <optionresponse></optionresponse>
            </problem>
            """
        )

        # The python problem should contain python_lib.zip:

        serialized = api.serialize_xblock_to_olx(python_problem)
        assert len(serialized.static_files) == 1
        assert serialized.static_files[0].name == "python_lib.zip"
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <problem
                copied_from_block="{python_problem.location}"
                display_name="Python Problem"
                url_name="Python_Problem"
            >
                This uses python: <script type="text/python">...</script>...
            </problem>
            """
        )

    def test_jsinput_extra_files(self):
        """
        Test JSInput problems with extra static files.
        """
        course = CourseFactory.create(display_name='JSInput Testing course', run="JSI")
        jsinput_files = [
            ("simple-question.html", "./common/test/data/uploads/simple-question.html"),
            ("simple-question.js", "./common/test/data/uploads/simple-question.js"),
            ("simple-question.css", "./common/test/data/uploads/simple-question.css"),
            ("image.jpg", "./common/test/data/uploads/image.jpg"),
            ("jschannel.js", "./common/static/js/capa/src/jschannel.js"),
        ]
        for filename, full_path in jsinput_files:
            upload_file_to_course(
                course_key=course.id,
                contentstore=contentstore(),
                source_file=full_path,
                target_filename=filename,
            )

        jsinput_problem = BlockFactory.create(
            parent_location=course.location,
            category="problem",
            display_name="JSInput Problem",
            data="<problem><jsinput html_file='/static/simple-question.html' /></problem>",
        )

        # The jsinput problem should contain the html_file along with extra static files:

        serialized = api.serialize_xblock_to_olx(jsinput_problem)
        assert len(serialized.static_files) == 5
        for file in serialized.static_files:
            self.assertIn(file.name, list(map(lambda f: f[0], jsinput_files)))

        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <problem
                copied_from_block="{jsinput_problem.location}"
                display_name="JSInput Problem"
                url_name="JSInput_Problem"
            >
                <jsinput html_file='/static/simple-question.html' />
            </problem>
            """
        )

    def test_tagged_units(self):
        """
        Test units (vertical blocks) that have applied tags
        """
        course = CourseFactory.create(display_name='Tagged Unit Course', run="TUC")
        unit = BlockFactory(
            parent_location=course.location,
            category="vertical",
            display_name="Tagged Unit",
        )

        # Add a bunch of tags
        tagging_api.tag_object(
            object_id=str(unit.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )
        tagging_api.tag_object(
            object_id=str(unit.location),
            taxonomy=self.taxonomy2,
            tags=["tag", "other tag"]
        )

        # Check that the tags data is serialized and omitted from the OLX
        serialized = api.serialize_xblock_to_olx(unit)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <vertical
                copied_from_block="{str(unit.location)}"
                display_name="Tagged Unit"
                url_name="Tagged_Unit"
            />
            """
        )
        self.assertEqual(
            serialized.tags, {
                str(unit.location): {
                    self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
                    self.taxonomy2.id: ["tag", "other tag"],
                }
            }
        )

    def test_tagged_html_block(self):
        """
        Test html blocks that have applied tags
        """
        course = CourseFactory.create(display_name='Tagged HTML Block Test Course', run="THBTC")

        # Create html block
        html_block = BlockFactory.create(
            parent_location=course.location,
            category="html",
            display_name="Tagged Non-default HTML Block",
            editor="raw",
            use_latex_compiler=True,
            data="🍔",
        )

        # Add a bunch of tags
        tagging_api.tag_object(
            object_id=str(html_block.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )
        tagging_api.tag_object(
            object_id=str(html_block.location),
            taxonomy=self.taxonomy2,
            tags=["tag", "other tag"]
        )

        # Check that the tags data is serialized and omitted from the OLX
        serialized = api.serialize_xblock_to_olx(html_block)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <html
                copied_from_block="{str(html_block.location)}"
                url_name="Tagged_Non-default_HTML_Block"
                display_name="Tagged Non-default HTML Block"
                editor="raw"
                use_latex_compiler="true"
            ><![CDATA[
                🍔
            ]]></html>
            """
        )
        self.assertEqual(
            serialized.tags, {
                str(html_block.location): {
                    self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
                    self.taxonomy2.id: ["tag", "other tag"],
                }
            }
        )

    def test_tagged_problem_blocks(self):
        """
        Test regular problem block + problem block with dependancy that
        have applied tags
        """
        course = CourseFactory.create(display_name='Tagged Python Testing course', run="TPY")
        upload_file_to_course(
            course_key=course.id,
            contentstore=contentstore(),
            source_file='./common/test/data/uploads/python_lib.zip',
            target_filename=DEFAULT_PYTHON_LIB_FILENAME,
        )

        regular_problem = BlockFactory.create(
            parent_location=course.location,
            category="problem",
            display_name="Tagged Problem No Python",
            max_attempts=3,
            data="<problem><optionresponse></optionresponse></problem>",
        )

        python_problem = BlockFactory.create(
            parent_location=course.location,
            category="problem",
            display_name="Tagged Python Problem",
            data='<problem>This uses python: <script type="text/python">...</script>...</problem>',
        )

        # Add a bunch of tags to the problem blocks
        tagging_api.tag_object(
            object_id=str(regular_problem.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )
        tagging_api.tag_object(
            object_id=str(regular_problem.location),
            taxonomy=self.taxonomy2,
            tags=["tag", "other tag"]
        )
        tagging_api.tag_object(
            object_id=str(python_problem.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )
        tagging_api.tag_object(
            object_id=str(python_problem.location),
            taxonomy=self.taxonomy2,
            tags=["tag", "other tag"]
        )

        # Check that the tags data is serialized and omitted from the OLX.
        serialized = api.serialize_xblock_to_olx(regular_problem)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <problem
                copied_from_block="{str(regular_problem.location)}"
                display_name="Tagged Problem No Python"
                url_name="Tagged_Problem_No_Python"
                max_attempts="3"
            >
                <optionresponse></optionresponse>
            </problem>
            """
        )
        self.assertEqual(
            serialized.tags, {
                str(regular_problem.location): {
                    self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
                    self.taxonomy2.id: ["tag", "other tag"],
                }
            }
        )

        serialized = api.serialize_xblock_to_olx(python_problem)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <problem
                copied_from_block="{str(python_problem.location)}"
                display_name="Tagged Python Problem"
                url_name="Tagged_Python_Problem"
            >
                This uses python: <script type="text/python">...</script>...
            </problem>
            """
        )
        self.assertEqual(
            serialized.tags, {
                str(python_problem.location): {
                    self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
                    self.taxonomy2.id: ["tag", "other tag"],
                }
            }
        )

    def test_tagged_library_content_blocks(self):
        """
        Test library content blocks that have applied tags
        """
        course = CourseFactory.create(display_name='Tagged Library Content course', run="TLCC")
        lib = LibraryFactory()
        lc_block = BlockFactory(
            parent_location=course.location,
            category="library_content",
            source_library_id=str(lib.location.library_key),
            display_name="Tagged LC Block",
            max_count=1,
        )

        # Add a bunch of tags to the library content block
        tagging_api.tag_object(
            object_id=str(lc_block.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )

        # Check that the tags data is serialized, omitted from the OLX, and properly escaped
        serialized = api.serialize_xblock_to_olx(lc_block)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <library_content
                copied_from_block="{str(lc_block.location)}"
                display_name="Tagged LC Block"
                max_count="1"
                source_library_id="{str(lib.location.library_key)}"
                url_name="Tagged_LC_Block"
            />
            """
        )
        self.assertEqual(serialized.tags, {
            str(lc_block.location): {
                self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
            }
        })

    def test_tagged_video_block(self):
        """
        Test video blocks that have applied tags
        """
        course = CourseFactory.create(display_name='Tagged Video Test course', run="TVTC")
        video_block = BlockFactory.create(
            parent_location=course.location,
            category="video",
            display_name="Tagged Video Block",
        )

        # Add tags to video block
        tagging_api.tag_object(
            object_id=str(video_block.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )

        # Check that the tags data is serialized and omitted from the OLX.
        serialized = api.serialize_xblock_to_olx(video_block)
        self.assertXmlEqual(
            serialized.olx_str,
            f"""
            <video
                copied_from_block="{str(video_block.location)}"
                youtube="1.00:3_yD_cEKoCk"
                url_name="Tagged_Video_Block"
                display_name="Tagged Video Block"
            />
            """
        )
        self.assertEqual(serialized.tags, {
            str(video_block.location): {
                self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
            }
        })

    def test_tagged_openassessment_block(self):
        """
        Test openassessment blocks that have applied tags
        """
        course = CourseFactory.create(display_name='Tagged OpenAssessment Test course', run="TOTC")
        openassessment_block = BlockFactory.create(
            parent_location=course.location,
            category="openassessment",
            display_name="Tagged OpenAssessment Block",
        )

        # Add a tags to openassessment block
        tagging_api.tag_object(
            object_id=str(openassessment_block.location),
            taxonomy=self.taxonomy1,
            tags=["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"]
        )

        # Check that the tags data is serialized and omitted from the OLX
        serialized = api.serialize_xblock_to_olx(openassessment_block)

        self.assertNotIn("normal tag", serialized.olx_str)
        self.assertNotIn("<special \"'-=,. |= chars > tag", serialized.olx_str)
        self.assertNotIn("anotherTag", serialized.olx_str)

        self.assertEqual(serialized.tags, {
            str(openassessment_block.location): {
                self.taxonomy1.id: ["normal tag", "<special \"'-=,. |= chars > tag", "anotherTag"],
            }
        })

    def test_write_copied_from_false(self):
        """
        Test that using `write_copied_from=False` in XBlockSerializer
        does not write copied_from_block or copied_from_version
        """
        course = CourseFactory.create(display_name='Copied From False Test course', run="CFTC")
        video_block = BlockFactory.create(
            parent_location=course.location,
            category="video",
            display_name="Video Block",
        )

        serialized = XBlockSerializer(
            video_block,
            write_copied_from=False,  # Disable copied_from_block and copied_from_version
        )

        self.assertXmlEqual(
            serialized.olx_str,
            """
            <video
                youtube="1.00:3_yD_cEKoCk"
                url_name="Video_Block"
                display_name="Video Block"
            />
            """
        )
