"""
Test the objecttag_export_helpers module
"""
import time
from unittest.mock import patch

from openedx_tagging.models import ObjectTag
from organizations.models import Organization

from openedx.core.djangoapps.content_libraries import api as library_api
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from .. import api
from ..helpers.objecttag_export_helpers import TaggedContent, build_object_tree_with_objecttags, iterate_with_level


class TestGetAllObjectTagsMixin:
    """
    Set up data to test get_all_object_tags functions
    """

    def setUp(self):
        super().setUp()

        self.taxonomy_1 = api.create_taxonomy(name="Taxonomy 1")
        api.set_taxonomy_orgs(self.taxonomy_1, all_orgs=True)
        self.tag_1_1 = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_1,
            tag="Tag 1.1",
        )
        self.tag_1_2 = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_1,
            tag="Tag 1.2",
        )

        self.taxonomy_2 = api.create_taxonomy(name="Taxonomy 2")
        api.set_taxonomy_orgs(self.taxonomy_2, all_orgs=True)

        self.tag_2_1 = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_2,
            tag="Tag 2.1",
        )
        self.tag_2_2 = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_2,
            tag="Tag 2.2",
        )

        api.tag_object(
            object_id="course-v1:orgA+test_course+test_run",
            taxonomy=self.taxonomy_1,
            tags=['Tag 1.1'],
        )
        self.course_tags = api.get_object_tags("course-v1:orgA+test_course+test_run")

        self.orgA = Organization.objects.create(name="Organization A", short_name="orgA")
        self.orgB = Organization.objects.create(name="Organization B", short_name="orgB")
        self.taxonomy_3 = api.create_taxonomy(name="Taxonomy 3", orgs=[self.orgA])
        api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_3,
            tag="Tag 3.1",
        )

        # Tag blocks
        api.tag_object(
            object_id="block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            taxonomy=self.taxonomy_1,
            tags=['Tag 1.1', 'Tag 1.2'],
        )
        self.sequential_tags1 = api.get_object_tags(
            "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            taxonomy_id=self.taxonomy_1.id,

        )
        api.tag_object(
            object_id="block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            taxonomy=self.taxonomy_2,
            tags=['Tag 2.1'],
        )
        self.sequential_tags2 = api.get_object_tags(
            "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            taxonomy_id=self.taxonomy_2.id,
        )
        api.tag_object(
            object_id="block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            taxonomy=self.taxonomy_2,
            tags=['Tag 2.2'],
        )
        self.vertical1_tags = api.get_object_tags(
            "block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1"
        )
        api.tag_object(
            object_id="block-v1:orgA+test_course+test_run+type@html+block@test_html",
            taxonomy=self.taxonomy_2,
            tags=['Tag 2.1'],
        )
        self.html_tags = api.get_object_tags("block-v1:orgA+test_course+test_run+type@html+block@test_html")

        # Create "deleted" object tags, which will be omitted from the results.
        for object_id in (
            "course-v1:orgA+test_course+test_run",
            "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            "block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            "block-v1:orgA+test_course+test_run+type@html+block@test_html",
        ):
            ObjectTag.objects.create(
                object_id=str(object_id),
                taxonomy=None,
                tag=None,
                _value="deleted tag",
                _export_id="deleted_taxonomy",
            )

        self.expected_course_objecttags = {
            "course-v1:orgA+test_course+test_run": {
                self.taxonomy_1.id: [tag.value for tag in self.course_tags],
            },
            "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential": {
                self.taxonomy_1.id: [tag.value for tag in self.sequential_tags1],
                self.taxonomy_2.id: [tag.value for tag in self.sequential_tags2],
            },
            "block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1": {
                self.taxonomy_2.id: [tag.value for tag in self.vertical1_tags],
            },
            "block-v1:orgA+test_course+test_run+type@html+block@test_html": {
                self.taxonomy_2.id: [tag.value for tag in self.html_tags],
            },
        }

        # Library tags and library contents need a unique block_id that is persisted along test runs
        self.block_suffix = str(round(time.time() * 1000))

        api.tag_object(
            object_id=f"lib:orgA:lib_{self.block_suffix}",
            taxonomy=self.taxonomy_2,
            tags=['Tag 2.1'],
        )
        self.library_tags = api.get_object_tags(f"lib:orgA:lib_{self.block_suffix}")

        api.tag_object(
            object_id=f"lb:orgA:lib_{self.block_suffix}:problem:problem1_{self.block_suffix}",
            taxonomy=self.taxonomy_1,
            tags=['Tag 1.1'],
        )
        self.problem1_tags = api.get_object_tags(
            f"lb:orgA:lib_{self.block_suffix}:problem:problem1_{self.block_suffix}"
        )

        api.tag_object(
            object_id=f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}",
            taxonomy=self.taxonomy_1,
            tags=['Tag 1.2'],
        )
        self.library_html_tags1 = api.get_object_tags(
            object_id=f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}",
            taxonomy_id=self.taxonomy_1.id,
        )

        api.tag_object(
            object_id=f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}",
            taxonomy=self.taxonomy_2,
            tags=['Tag 2.2'],
        )
        self.library_html_tags2 = api.get_object_tags(
            object_id=f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}",
            taxonomy_id=self.taxonomy_2.id,
        )

        # Create "deleted" object tags, which will be omitted from the results.
        for object_id in (
            f"lib:orgA:lib_{self.block_suffix}",
            f"lb:orgA:lib_{self.block_suffix}:problem:problem1_{self.block_suffix}",
            f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}",
        ):
            ObjectTag.objects.create(
                object_id=object_id,
                taxonomy=None,
                tag=None,
                _value="deleted tag",
                _export_id="deleted_taxonomy",
            )

        self.expected_library_objecttags = {
            f"lib:orgA:lib_{self.block_suffix}": {
                self.taxonomy_2.id: [tag.value for tag in self.library_tags],
            },
            f"lb:orgA:lib_{self.block_suffix}:problem:problem1_{self.block_suffix}": {
                self.taxonomy_1.id: [tag.value for tag in self.problem1_tags],
            },
            f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}": {
                self.taxonomy_1.id: [tag.value for tag in self.library_html_tags1],
                self.taxonomy_2.id: [tag.value for tag in self.library_html_tags2],
            },
        }


class TaggedCourseMixin(TestGetAllObjectTagsMixin, ModuleStoreTestCase):  # type: ignore[misc]
    """
    Mixin with a course structure and taxonomies
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    CREATE_USER = False

    def setUp(self):
        super().setUp()

        # Patch modulestore
        self.patcher = patch("openedx.core.djangoapps.content_tagging.tasks.modulestore", return_value=self.store)
        self.addCleanup(self.patcher.stop)
        self.patcher.start()

        # Create course
        self.course = CourseFactory.create(
            org=self.orgA.short_name,
            number="test_course",
            run="test_run",
            display_name="Test Course",
        )

        self.expected_course_tagged_xblock = TaggedContent(
            display_name="Test Course",
            block_id="course-v1:orgA+test_course+test_run",
            category="course",
            children=[],
            object_tags={
                self.taxonomy_1.id: [tag.value for tag in self.course_tags],
            },
        )

        # Create XBlocks
        self.sequential = BlockFactory.create(
            parent=self.course,
            category="sequential",
            display_name="test sequential",
        )
        # Tag blocks
        tagged_sequential = TaggedContent(
            display_name="test sequential",
            block_id="block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential",
            category="sequential",
            children=[],
            object_tags={
                self.taxonomy_1.id: [tag.value for tag in self.sequential_tags1],
                self.taxonomy_2.id: [tag.value for tag in self.sequential_tags2],
            },
        )

        assert self.expected_course_tagged_xblock.children is not None  # type guard
        self.expected_course_tagged_xblock.children.append(tagged_sequential)

        # Untagged blocks
        sequential2 = BlockFactory.create(
            parent=self.course,
            category="sequential",
            display_name="untagged sequential",
        )
        untagged_sequential = TaggedContent(
            display_name="untagged sequential",
            block_id="block-v1:orgA+test_course+test_run+type@sequential+block@untagged_sequential",
            category="sequential",
            children=[],
            object_tags={},
        )
        assert self.expected_course_tagged_xblock.children is not None  # type guard
        self.expected_course_tagged_xblock.children.append(untagged_sequential)
        BlockFactory.create(
            parent=sequential2,
            category="vertical",
            display_name="untagged vertical",
        )
        untagged_vertical = TaggedContent(
            display_name="untagged vertical",
            block_id="block-v1:orgA+test_course+test_run+type@vertical+block@untagged_vertical",
            category="vertical",
            children=[],
            object_tags={},
        )
        assert untagged_sequential.children is not None  # type guard
        untagged_sequential.children.append(untagged_vertical)
        # /Untagged blocks

        vertical = BlockFactory.create(
            parent=self.sequential,
            category="vertical",
            display_name="test vertical1",
        )
        tagged_vertical = TaggedContent(
            display_name="test vertical1",
            block_id="block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            category="vertical",
            children=[],
            object_tags={
                self.taxonomy_2.id: [tag.value for tag in self.vertical1_tags],
            },
        )
        assert tagged_sequential.children is not None  # type guard
        tagged_sequential.children.append(tagged_vertical)

        vertical2 = BlockFactory.create(
            parent=self.sequential,
            category="vertical",
            display_name="test vertical2",
        )
        untagged_vertical2 = TaggedContent(
            display_name="test vertical2",
            block_id="block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical2",
            category="vertical",
            children=[],
            object_tags={},
        )
        assert tagged_sequential.children is not None  # type guard
        tagged_sequential.children.append(untagged_vertical2)

        BlockFactory.create(
            parent=vertical2,
            category="html",
            display_name="test html",
        )
        tagged_html = TaggedContent(
            display_name="test html",
            block_id="block-v1:orgA+test_course+test_run+type@html+block@test_html",
            category="html",
            children=[],
            object_tags={
                self.taxonomy_2.id: [tag.value for tag in self.html_tags],
            },
        )
        assert untagged_vertical2.children is not None  # type guard
        untagged_vertical2.children.append(tagged_html)

        self.all_course_object_tags, _ = api.get_all_object_tags(self.course.id)
        self.expected_course_tagged_content_list = [
            (self.expected_course_tagged_xblock, 0),
            (tagged_sequential, 1),
            (tagged_vertical, 2),
            (untagged_vertical2, 2),
            (tagged_html, 3),
            (untagged_sequential, 1),
            (untagged_vertical, 2),
        ]

        # Create a library
        self.library = library_api.create_library(
            self.orgA,
            f"lib_{self.block_suffix}",
            "Test Library",
        )
        self.expected_library_tagged_xblock = TaggedContent(
            display_name="Test Library",
            block_id=f"lib:orgA:lib_{self.block_suffix}",
            category="library",
            children=[],
            object_tags={
                self.taxonomy_2.id: [tag.value for tag in self.library_tags],
            },
        )

        library_api.create_library_block(
            self.library.key,
            "problem",
            f"problem1_{self.block_suffix}",
        )
        tagged_problem = TaggedContent(
            display_name="Blank Problem",
            block_id=f"lb:orgA:lib_{self.block_suffix}:problem:problem1_{self.block_suffix}",
            category="problem",
            children=[],
            object_tags={
                self.taxonomy_1.id: [tag.value for tag in self.problem1_tags],
            },
        )

        library_api.create_library_block(
            self.library.key,
            "problem",
            f"problem2_{self.block_suffix}",
        )
        untagged_problem = TaggedContent(
            display_name="Blank Problem",
            block_id=f"lb:orgA:lib_{self.block_suffix}:problem:problem2_{self.block_suffix}",
            category="problem",
            children=[],
            object_tags={},
        )

        library_api.create_library_block(
            self.library.key,
            "html",
            f"html_{self.block_suffix}",
        )
        tagged_library_html = TaggedContent(
            display_name="Text",
            block_id=f"lb:orgA:lib_{self.block_suffix}:html:html_{self.block_suffix}",
            category="html",
            children=[],
            object_tags={
                self.taxonomy_1.id: [tag.value for tag in self.library_html_tags1],
                self.taxonomy_2.id: [tag.value for tag in self.library_html_tags2],
            },
        )

        assert self.expected_library_tagged_xblock.children is not None  # type guard
        # The children are sorted by add order
        self.expected_library_tagged_xblock.children.append(tagged_problem)
        self.expected_library_tagged_xblock.children.append(untagged_problem)
        self.expected_library_tagged_xblock.children.append(tagged_library_html)

        self.all_library_object_tags, _ = api.get_all_object_tags(self.library.key)
        self.expected_library_tagged_content_list = [
            (self.expected_library_tagged_xblock, 0),
            (tagged_problem, 1),
            (untagged_problem, 1),
            (tagged_library_html, 1),
        ]

        self.expected_csv = (
            '"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'
            '"Test Course","course","course-v1:orgA+test_course+test_run","Tag 1.1",""\r\n'
            '"  test sequential","sequential","test_sequential","Tag 1.1; Tag 1.2","Tag 2.1"\r\n'
            '"    test vertical1","vertical","test_vertical1","","Tag 2.2"\r\n'
            '"    test vertical2","vertical","test_vertical2","",""\r\n'
            '"      test html","html","test_html","","Tag 2.1"\r\n'
            '"  untagged sequential","sequential","untagged_sequential","",""\r\n'
            '"    untagged vertical","vertical","untagged_vertical","",""\r\n'
        )


class TestContentTagChildrenExport(TaggedCourseMixin):  # type: ignore[misc]
    """
    Test helper functions for exporting tagged content
    """
    def test_build_course_object_tree(self) -> None:
        """
        Test if we can export a course
        """
        with self.assertNumQueries(3):
            tagged_course = build_object_tree_with_objecttags(self.course.id, self.all_course_object_tags)

        assert tagged_course == self.expected_course_tagged_xblock

    def test_build_library_object_tree(self) -> None:
        """
        Test if we can export a library
        """
        with self.assertNumQueries(11):
            tagged_library = build_object_tree_with_objecttags(self.library.key, self.all_library_object_tags)

        assert tagged_library == self.expected_library_tagged_xblock

    def test_course_iterate_with_level(self) -> None:
        """
        Test if we can iterate over the tagged course in the correct order
        """
        tagged_content_list = list(iterate_with_level(self.expected_course_tagged_xblock))
        assert tagged_content_list == self.expected_course_tagged_content_list

    def test_library_iterate_with_level(self) -> None:
        """
        Test if we can iterate over the tagged library in the correct order
        """
        tagged_content_list = list(iterate_with_level(self.expected_library_tagged_xblock))
        assert tagged_content_list == self.expected_library_tagged_content_list
