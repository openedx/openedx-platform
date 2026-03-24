"""
Tests for the Studio content search documents (what gets stored in the index)
"""
import ddt
from dataclasses import replace
from datetime import datetime, timezone

from freezegun import freeze_time
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator
from openedx_content import api as content_api
from organizations.models import Organization

from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, ToyCourseFactory

try:
    # This import errors in the lms because content.search is not an installed app there.
    from ..documents import (
        searchable_doc_collections,
        searchable_doc_for_collection,
        searchable_doc_for_container,
        searchable_doc_for_course_block,
        searchable_doc_for_library_block,
        searchable_doc_tags,
    )
    from ..models import SearchAccess
except RuntimeError:
    searchable_doc_for_course_block = lambda x: x
    searchable_doc_tags = lambda x: x
    searchable_doc_for_collection = lambda x: x
    searchable_doc_for_container = lambda x: x
    searchable_doc_for_library_block = lambda x: x
    SearchAccess = {}


STUDIO_SEARCH_ENDPOINT_URL = "/api/content_search/v2/studio/"


@skip_unless_cms
@ddt.ddt
class StudioDocumentsTest(SharedModuleStoreTestCase):
    """
    Tests for the Studio content search documents (what gets stored in the
    search index)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = modulestore()
        # Create a library and collection with a block
        cls.created_date = datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
        with freeze_time(cls.created_date):
            # Get references to some blocks in the toy course
            cls.org = Organization.objects.create(name="edX", short_name="edX")
            cls.toy_course = ToyCourseFactory.create()  # See xmodule/modulestore/tests/sample_courses.py
            cls.toy_course_key = cls.toy_course.id

            cls.html_block_key = cls.toy_course_key.make_usage_key("html", "toyjumpto")
            # Create a problem in course
            cls.problem_block = BlockFactory.create(
                category="problem",
                parent_location=cls.toy_course_key.make_usage_key("vertical", "vertical_test"),
                display_name='Test Problem',
                data="<problem>What is a test?<multiplechoiceresponse></multiplechoiceresponse></problem>",
            )

            cls.library = library_api.create_library(
                org=cls.org,
                slug="2012_Fall",
                title="some content_library",
                description="some description",
            )
            cls.collection = library_api.create_library_collection(
                cls.library.key,
                collection_key="TOY_COLLECTION",
                title="Toy Collection",
                created_by=None,
                description="my toy collection description"
            )
            cls.collection_key = LibraryCollectionLocator.from_string(
                "lib-collection:edX:2012_Fall:TOY_COLLECTION",
            )
            cls.library_block = library_api.create_library_block(
                cls.library.key,
                "html",
                "text2",
            )
            cls.container = library_api.create_container(
                cls.library.key,
                container_type=library_api.ContainerType.Unit,
                slug="unit1",
                title="A Unit in the Search Index",
                user_id=None,
            )
            cls.container_key = LibraryContainerLocator.from_string(
                "lct:edX:2012_Fall:unit:unit1",
            )
            cls.subsection = library_api.create_container(
                cls.library.key,
                container_type=library_api.ContainerType.Subsection,
                slug="subsection1",
                title="A Subsection in the Search Index",
                user_id=None,
            )
            cls.subsection_key = LibraryContainerLocator.from_string(
                "lct:edX:2012_Fall:subsection:subsection1",
            )
            cls.section = library_api.create_container(
                cls.library.key,
                container_type=library_api.ContainerType.Section,
                slug="section1",
                title="A Section in the Search Index",
                user_id=None,
            )
            cls.section_key = LibraryContainerLocator.from_string(
                "lct:edX:2012_Fall:section:section1",
            )

            # Add the problem block to the collection
            library_api.update_library_collection_items(
                cls.library.key,
                collection_key="TOY_COLLECTION",
                opaque_keys=[
                    cls.library_block.usage_key,
                ]
            )

        # Create a couple taxonomies and some tags
        cls.difficulty_tags = tagging_api.create_taxonomy(name="Difficulty", orgs=[cls.org], allow_multiple=False)
        tagging_api.add_tag_to_taxonomy(cls.difficulty_tags, tag="Easy")
        tagging_api.add_tag_to_taxonomy(cls.difficulty_tags, tag="Normal")
        tagging_api.add_tag_to_taxonomy(cls.difficulty_tags, tag="Difficult")

        cls.subject_tags = tagging_api.create_taxonomy(name="Subject", orgs=[cls.org], allow_multiple=True)
        tagging_api.add_tag_to_taxonomy(cls.subject_tags, tag="Linguistics")
        tagging_api.add_tag_to_taxonomy(cls.subject_tags, tag="Asian Languages", parent_tag_value="Linguistics")
        tagging_api.add_tag_to_taxonomy(cls.subject_tags, tag="Chinese", parent_tag_value="Asian Languages")
        tagging_api.add_tag_to_taxonomy(cls.subject_tags, tag="Hypertext")
        tagging_api.add_tag_to_taxonomy(cls.subject_tags, tag="Jump Links", parent_tag_value="Hypertext")

        # Tag stuff:
        tagging_api.tag_object(str(cls.problem_block.usage_key), cls.difficulty_tags, tags=["Easy"])
        tagging_api.tag_object(str(cls.html_block_key), cls.subject_tags, tags=["Chinese", "Jump Links"])
        tagging_api.tag_object(str(cls.html_block_key), cls.difficulty_tags, tags=["Normal"])
        tagging_api.tag_object(str(cls.library_block.usage_key), cls.difficulty_tags, tags=["Normal"])
        tagging_api.tag_object(str(cls.collection_key), cls.difficulty_tags, tags=["Normal"])
        tagging_api.tag_object(str(cls.container_key), cls.difficulty_tags, tags=["Normal"])
        tagging_api.tag_object(str(cls.subsection_key), cls.difficulty_tags, tags=["Normal"])
        tagging_api.tag_object(str(cls.section_key), cls.difficulty_tags, tags=["Normal"])

    @property
    def toy_course_access_id(self):
        """
        Returns the SearchAccess.id created for the toy course.

        This SearchAccess object is created when documents are added to the search index, so this method must be called
        after this step, or risk a DoesNotExist error.
        """
        return SearchAccess.objects.get(context_key=self.toy_course_key).id

    @property
    def library_access_id(self):
        """
        Returns the SearchAccess.id created for the library.

        This SearchAccess object is created when documents are added to the search index, so this method must be called
        after this step, or risk a DoesNotExist error.
        """
        return SearchAccess.objects.get(context_key=self.library.key).id

    def test_problem_block(self):
        """
        Test how a problem block gets represented in the search index
        """
        block = self.store.get_item(self.problem_block.usage_key)
        doc = {}
        doc.update(searchable_doc_for_course_block(block))
        doc.update(searchable_doc_tags(block.usage_key))

        assert doc == {
            # Note the 'id' has been stripped of special characters to meet Meilisearch requirements.
            # The '-8516ed8' suffix is deterministic based on the original usage key.
            "id": "block-v1edxtoy2012_falltypeproblemblocktest_problem-f46b6f1e",
            "type": "course_block",
            "block_type": "problem",
            "usage_key": "block-v1:edX+toy+2012_Fall+type@problem+block@Test_Problem",
            "block_id": "Test_Problem",
            "context_key": "course-v1:edX+toy+2012_Fall",
            "org": "edX",
            "access_id": self.toy_course_access_id,
            "display_name": "Test Problem",
            "description": "What is a test?",
            "breadcrumbs": [
                {
                    'display_name': 'Toy Course',
                },
                {
                    'display_name': 'chapter',
                    'usage_key': 'block-v1:edX+toy+2012_Fall+type@chapter+block@vertical_container',
                },
                {
                    'display_name': 'sequential',
                    'usage_key': 'block-v1:edX+toy+2012_Fall+type@sequential+block@vertical_sequential',
                },
                {
                    'display_name': 'vertical',
                    'usage_key': 'block-v1:edX+toy+2012_Fall+type@vertical+block@vertical_test',
                },
            ],
            "modified": self.created_date.timestamp(),
            "content": {
                "capa_content": "What is a test?",
                "problem_types": ["multiplechoiceresponse"],
            },
            # See https://blog.meilisearch.com/nested-hierarchical-facets-guide/
            # and https://www.algolia.com/doc/api-reference/widgets/hierarchical-menu/js/
            # For details on why the hierarchical tag data is in this format.
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Easy"],
            },
        }

    def test_html_block(self):
        """
        Test how an HTML block gets represented in the search index
        """
        block = self.store.get_item(self.html_block_key)
        doc = {}
        doc.update(searchable_doc_for_course_block(block))
        doc.update(searchable_doc_tags(block.usage_key))
        assert doc == {
            "id": "block-v1edxtoy2012_falltypehtmlblocktoyjumpto-efb9c601",
            "type": "course_block",
            "block_type": "html",
            "usage_key": "block-v1:edX+toy+2012_Fall+type@html+block@toyjumpto",
            "block_id": "toyjumpto",
            "context_key": "course-v1:edX+toy+2012_Fall",
            "org": "edX",
            "access_id": self.toy_course_access_id,
            "display_name": "Text",
            "description": "This is a link to another page and some Chinese 四節比分和七年前 Some "
                         "more Chinese 四節比分和七年前 ",
            "modified": self.created_date.timestamp(),
            "breadcrumbs": [
                {
                    'display_name': 'Toy Course',
                },
                {
                    'display_name': 'Overview',
                    'usage_key': 'block-v1:edX+toy+2012_Fall+type@chapter+block@Overview',
                },
                {
                    "display_name": "Toy Videos",
                    "usage_key": "block-v1:edX+toy+2012_Fall+type@sequential+block@Toy_Videos",
                },
            ],
            "content": {
                "html_content": (
                    "This is a link to another page and some Chinese 四節比分和七年前 Some more Chinese 四節比分和七年前 "
                ),
            },
            "tags": {
                "taxonomy": ["Difficulty", "Subject"],
                "level0": ["Difficulty > Normal", "Subject > Hypertext", "Subject > Linguistics"],
                "level1": ["Subject > Hypertext > Jump Links", "Subject > Linguistics > Asian Languages"],
                "level2": ["Subject > Linguistics > Asian Languages > Chinese"],
            },
        }

    def test_video_block_untagged(self):
        """
        Test how a video block gets represented in the search index.
        """
        block_usage_key = self.toy_course_key.make_usage_key("video", "Welcome")
        block = self.store.get_item(block_usage_key)
        doc = searchable_doc_for_course_block(block)
        assert doc == {
            "id": "block-v1edxtoy2012_falltypevideoblockwelcome-0c9fd626",
            "type": "course_block",
            "block_type": "video",
            "usage_key": "block-v1:edX+toy+2012_Fall+type@video+block@Welcome",
            "block_id": "Welcome",
            "context_key": "course-v1:edX+toy+2012_Fall",
            "org": "edX",
            "access_id": self.toy_course_access_id,
            "display_name": "Welcome",
            "breadcrumbs": [
                {
                    'display_name': 'Toy Course',
                },
                {
                    'display_name': 'Overview',
                    'usage_key': 'block-v1:edX+toy+2012_Fall+type@chapter+block@Overview',
                },
            ],
            "content": {},
            "modified": self.created_date.timestamp(),
            # This video has no tags.
        }

    def test_html_library_block(self):
        """
        Test how a library block gets represented in the search index
        """
        doc = {}
        doc.update(searchable_doc_for_library_block(self.library_block))
        doc.update(searchable_doc_tags(self.library_block.usage_key))
        doc.update(searchable_doc_collections(self.library_block.usage_key))
        assert doc == {
            "id": "lbedx2012_fallhtmltext2-4bb47d67",
            "type": "library_block",
            "block_type": "html",
            "usage_key": "lb:edX:2012_Fall:html:text2",
            "block_id": "text2",
            "context_key": "lib:edX:2012_Fall",
            "org": "edX",
            "access_id": self.library_access_id,
            "display_name": "Text",
            "breadcrumbs": [
                {
                    "display_name": "some content_library",
                },
            ],
            "last_published": None,
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "content": {
                "html_content": "",
            },
            "collections": {
                "key": ["TOY_COLLECTION"],
                "display_name": ["Toy Collection"],
            },
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"],
            },
            "publish_status": "never",
        }

    def test_html_published_library_block(self):
        library_api.publish_changes(self.library.key)

        doc = searchable_doc_for_library_block(self.library_block)
        doc.update(searchable_doc_tags(self.library_block.usage_key))
        doc.update(searchable_doc_collections(self.library_block.usage_key))

        assert doc == {
            "id": "lbedx2012_fallhtmltext2-4bb47d67",
            "type": "library_block",
            "block_type": "html",
            "usage_key": "lb:edX:2012_Fall:html:text2",
            "block_id": "text2",
            "context_key": "lib:edX:2012_Fall",
            "org": "edX",
            "access_id": self.library_access_id,
            "display_name": "Text",
            "breadcrumbs": [
                {
                    "display_name": "some content_library",
                },
            ],
            "last_published": None,
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "content": {
                "html_content": "",
            },
            "collections": {
                "key": ["TOY_COLLECTION"],
                "display_name": ["Toy Collection"],
            },
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"],
            },
            'published': {'display_name': 'Text'},
            "publish_status": "published",
        }

        # Update library block to create a draft
        olx_str = '<html display_name="Text 2"><![CDATA[<p>This is a Test</p>]]></html>'
        library_api.set_library_block_olx(self.library_block.usage_key, olx_str)

        doc = searchable_doc_for_library_block(self.library_block)
        doc.update(searchable_doc_tags(self.library_block.usage_key))
        doc.update(searchable_doc_collections(self.library_block.usage_key))

        assert doc == {
            "id": "lbedx2012_fallhtmltext2-4bb47d67",
            "type": "library_block",
            "block_type": "html",
            "usage_key": "lb:edX:2012_Fall:html:text2",
            "block_id": "text2",
            "context_key": "lib:edX:2012_Fall",
            "org": "edX",
            "access_id": self.library_access_id,
            "display_name": "Text 2",
            "description": "This is a Test",
            "breadcrumbs": [
                {
                    "display_name": "some content_library",
                },
            ],
            "last_published": None,
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "content": {
                "html_content": "This is a Test",
            },
            "collections": {
                "key": ["TOY_COLLECTION"],
                "display_name": ["Toy Collection"],
            },
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"],
            },
            "published": {"display_name": "Text"},
            "publish_status": "published",
        }

        # Publish new changes
        library_api.publish_changes(self.library.key)
        doc = searchable_doc_for_library_block(self.library_block)
        doc.update(searchable_doc_tags(self.library_block.usage_key))
        doc.update(searchable_doc_collections(self.library_block.usage_key))

        assert doc == {
            "id": "lbedx2012_fallhtmltext2-4bb47d67",
            "type": "library_block",
            "block_type": "html",
            "usage_key": "lb:edX:2012_Fall:html:text2",
            "block_id": "text2",
            "context_key": "lib:edX:2012_Fall",
            "org": "edX",
            "access_id": self.library_access_id,
            "display_name": "Text 2",
            "description": "This is a Test",
            "breadcrumbs": [
                {
                    "display_name": "some content_library",
                },
            ],
            "last_published": None,
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "content": {
                "html_content": "This is a Test",
            },
            "collections": {
                "key": ["TOY_COLLECTION"],
                "display_name": ["Toy Collection"],
            },
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"],
            },
            "published": {
                "display_name": "Text 2",
                "description": "This is a Test",
            },
            "publish_status": "published",
        }

        # Verify publish status is set to modified
        library_block_modified = replace(
            self.library_block,
            modified=datetime(2024, 4, 5, 6, 7, 8, tzinfo=timezone.utc),
            last_published=datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc),
        )
        doc = searchable_doc_for_library_block(library_block_modified)
        doc.update(searchable_doc_tags(library_block_modified.usage_key))
        doc.update(searchable_doc_collections(library_block_modified.usage_key))
        assert doc["publish_status"] == "modified"

    def test_collection_with_library(self):
        doc = searchable_doc_for_collection(self.collection_key)
        doc.update(searchable_doc_tags(self.collection_key))

        assert doc == {
            "id": "lib-collectionedx2012_falltoy_collection-d1d907a4",
            "block_id": self.collection.key,
            "usage_key": str(self.collection_key),
            "type": "collection",
            "org": "edX",
            "display_name": "Toy Collection",
            "description": "my toy collection description",
            "num_children": 1,
            "context_key": "lib:edX:2012_Fall",
            "access_id": self.library_access_id,
            "breadcrumbs": [{"display_name": "some content_library"}],
            "created": 1680674828.0,
            "modified": 1680674828.0,
            'tags': {
                'taxonomy': ['Difficulty'],
                'level0': ['Difficulty > Normal']
            },
            "published": {
                "num_children": 0
            }
        }

    def test_collection_with_published_library(self):
        library_api.publish_changes(self.library.key)

        doc = searchable_doc_for_collection(self.collection_key)
        doc.update(searchable_doc_tags(self.collection_key))

        assert doc == {
            "id": "lib-collectionedx2012_falltoy_collection-d1d907a4",
            "block_id": self.collection.key,
            "usage_key": str(self.collection_key),
            "type": "collection",
            "org": "edX",
            "display_name": "Toy Collection",
            "description": "my toy collection description",
            "num_children": 1,
            "context_key": "lib:edX:2012_Fall",
            "access_id": self.library_access_id,
            "breadcrumbs": [{"display_name": "some content_library"}],
            "created": 1680674828.0,
            "modified": 1680674828.0,
            'tags': {
                'taxonomy': ['Difficulty'],
                'level0': ['Difficulty > Normal']
            },
            "published": {
                "num_children": 1
            }
        }

    @ddt.data(
        ("container", "unit1", "unit", "edd13a0c"),
        ("subsection", "subsection1", "subsection", "c6c172be"),
        ("section", "section1", "section", "79ee8fa2"),
    )
    @ddt.unpack
    def test_draft_container(self, container_name, container_slug, container_type, doc_id):
        """
        Test creating a search document for a draft-only container
        """
        container = getattr(self, container_name)
        doc = searchable_doc_for_container(container.container_key)
        doc.update(searchable_doc_tags(container.container_key))

        assert doc == {
            "id": f"lctedx2012_fall{container_type}{container_slug}-{doc_id}",
            "block_id": container_slug,
            "block_type": container_type,
            "usage_key": f"lct:edX:2012_Fall:{container_type}:{container_slug}",
            "type": "library_container",
            "org": "edX",
            "display_name": container.display_name,
            # description is not set for containers
            "num_children": 0,
            "content": {
                "child_usage_keys": [],
                "child_display_names": [],
            },
            "publish_status": "never",
            "context_key": "lib:edX:2012_Fall",
            "access_id": self.library_access_id,
            "breadcrumbs": [{"display_name": "some content_library"}],
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "last_published": None,
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"]
            },
            # "published" is not set since we haven't published it yet
        }

    def test_published_container(self):
        """
        Test creating a search document for a published container
        """
        with freeze_time(self.container.created):
            # Create a container with a block in it
            library_api.update_container_children(
                self.container.container_key,
                [self.library_block.usage_key],
                user_id=None,
            )
            library_api.publish_changes(self.library.key)

        doc = searchable_doc_for_container(self.container.container_key)
        doc.update(searchable_doc_tags(self.container.container_key))

        assert doc == {
            "id": "lctedx2012_fallunitunit1-edd13a0c",
            "block_id": "unit1",
            "block_type": "unit",
            "usage_key": "lct:edX:2012_Fall:unit:unit1",
            "type": "library_container",
            "org": "edX",
            "display_name": "A Unit in the Search Index",
            # description is not set for containers
            "num_children": 1,
            "content": {
                "child_usage_keys": [
                    "lb:edX:2012_Fall:html:text2",
                ],
                "child_display_names": [
                    "Text",
                ],
            },
            "publish_status": "published",
            "context_key": "lib:edX:2012_Fall",
            "access_id": self.library_access_id,
            "breadcrumbs": [{"display_name": "some content_library"}],
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "last_published": 1680674828.0,
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"]
            },
            "published": {
                "num_children": 1,
                "display_name": "A Unit in the Search Index",
                "content": {
                    "child_usage_keys": [
                        "lb:edX:2012_Fall:html:text2",
                    ],
                    "child_display_names": [
                        "Text",
                    ],
                },
            },
        }

    def test_published_container_with_changes(self):
        """
        Test creating a search document for a published container
        """
        library_api.update_container_children(
            self.container.container_key,
            [self.library_block.usage_key],
            user_id=None,
        )
        with freeze_time(self.container.created):
            library_api.publish_changes(self.library.key)
        block_2 = library_api.create_library_block(
            self.library.key,
            "html",
            "text3",
        )

        # Add another component after publish
        with freeze_time(self.container.created):
            library_api.update_container_children(
                self.container.container_key,
                [block_2.usage_key],
                user_id=None,
                entities_action=content_api.ChildrenEntitiesAction.APPEND,
            )

        doc = searchable_doc_for_container(self.container.container_key)
        doc.update(searchable_doc_tags(self.container.container_key))

        assert doc == {
            "id": "lctedx2012_fallunitunit1-edd13a0c",
            "block_id": "unit1",
            "block_type": "unit",
            "usage_key": "lct:edX:2012_Fall:unit:unit1",
            "type": "library_container",
            "org": "edX",
            "display_name": "A Unit in the Search Index",
            # description is not set for containers
            "num_children": 2,
            "content": {
                "child_usage_keys": [
                    "lb:edX:2012_Fall:html:text2",
                    "lb:edX:2012_Fall:html:text3",
                ],
                "child_display_names": [
                    "Text",
                    "Text",
                ],
            },
            "publish_status": "modified",
            "context_key": "lib:edX:2012_Fall",
            "access_id": self.library_access_id,
            "breadcrumbs": [{"display_name": "some content_library"}],
            "created": 1680674828.0,
            "modified": 1680674828.0,
            "last_published": 1680674828.0,
            "tags": {
                "taxonomy": ["Difficulty"],
                "level0": ["Difficulty > Normal"]
            },
            "published": {
                "num_children": 1,
                "display_name": "A Unit in the Search Index",
                "content": {
                    "child_usage_keys": [
                        "lb:edX:2012_Fall:html:text2",
                    ],
                    "child_display_names": [
                        "Text",
                    ],
                },
            },
        }

    def test_mathjax_plain_text_conversion_for_search(self):
        """
        Test how an HTML block with mathjax equations gets converted to plain text in search description.
        """
        # pylint: disable=line-too-long
        eqns = [
            # (input, expected output)
            ('Simple addition: \\( 2 + 3 \\)', 'Simple addition:  2 + 3'),
            ('Simple subtraction: \\( 5 - 2 \\)', 'Simple subtraction:  5 − 2'),
            ('Simple multiplication: \\( 4 * 6 \\)', 'Simple multiplication:  4 * 6'),
            ('Simple division: \\( 8 / 2 \\)', 'Simple division:  8 / 2'),
            ('Mixed arithmetic: \\( 2 + 3  4 \\)', 'Mixed arithmetic:  2 + 3 4'),
            ('Simple exponentiation: \\[ 2^3 \\]', 'Simple exponentiation:  2³'),
            ('Root extraction: \\[ 16^{1/2} \\]', 'Root extraction:  16¹^/²'),
            ('Exponent with multiple terms: \\[ (2 + 3)^2 \\]', 'Exponent with multiple terms:  (2 + 3)²'),
            ('Nested exponents: \\[ 2^(3^2) \\]', 'Nested exponents:  2⁽3²)'),
            ('Mixed roots: \\[ 8^{1/2}  3^2 \\]', 'Mixed roots:  8¹^/² 3²'),
            ('Simple fraction: [mathjaxinline] 3/4 [/mathjaxinline]', 'Simple fraction:  3/4'),
            (
                'Decimal to fraction conversion: [mathjaxinline] 0.75 = 3/4 [/mathjaxinline]',
                'Decimal to fraction conversion:  0.75 = 3/4',
            ),
            ('Mixed fractions: [mathjaxinline] 1 1/2 = 3/2 [/mathjaxinline]', 'Mixed fractions:  1 1/2 = 3/2'),
            (
                'Converting decimals to mixed fractions: [mathjaxinline] 2.5 = 5/2 [/mathjaxinline]',
                'Converting decimals to mixed fractions:  2.5 = 5/2',
            ),
            (
                'Trig identities: [mathjaxinline] \\sin(x + y) = \\sin(x)  \\cos(y) + \\cos(x)  \\sin(y) [/mathjaxinline]',
                'Trig identities:  sin(x + y) = sin(x) cos(y) + cos(x) sin(y)',
            ),
            (
                'Sine, cosine, and tangent: [mathjaxinline] \\sin(x) [/mathjaxinline] [mathjaxinline] \\cos(x) [/mathjaxinline] [mathjaxinline] \\tan(x) [/mathjaxinline]',
                'Sine, cosine, and tangent:  sin(x)   cos(x)   tan(x)',
            ),
            (
                'Hyperbolic trig functions: [mathjaxinline] \\sinh(x) [/mathjaxinline] [mathjaxinline] \\cosh(x) [/mathjaxinline]',
                'Hyperbolic trig functions:  sinh(x)   cosh(x)',
            ),
            (
                "Simple derivative: [mathjax] f(x) = x^2, f'(x) = 2x [/mathjax]",
                "Simple derivative:  f(x) = x², f'(x) = 2x",
            ),
            ('Double integral: [mathjax] int\\int (x + y) dxdy [/mathjax]', 'Double integral:  int∫ (x + y) dxdy'),
            (
                'Partial derivatives: [mathjax] f(x,y) = xy, \\frac{\\partial f}{\\partial x} = y [/mathjax] [mathjax] \\frac{\\partial f}{\\partial y} = x [/mathjax]',
                'Partial derivatives:  f(x,y) = xy, (∂ f/∂ x) = y   (∂ f/∂ y) = x',
            ),
            (
                'Mean and standard deviation: [mathjax] mu = 2, \\sigma = 1 [/mathjax]',
                'Mean and standard deviation:  mu = 2, σ = 1',
            ),
            (
                'Binomial probability: [mathjax] P(X = k) = (\\binom{n}{k} p^k (1-p)^{n-k}) [/mathjax]',
                'Binomial probability:  P(X = k) = (\\binom{n}{k} pᵏ (1−p)ⁿ⁻ᵏ)',
            ),
            ('Gaussian distribution: [mathjax] N(\\mu, \\sigma^2) [/mathjax]', 'Gaussian distribution:  N(μ, σ²)'),
            (
                'Greek letters: [mathjaxinline] \\alpha [/mathjaxinline] [mathjaxinline] \\beta [/mathjaxinline] [mathjaxinline] \\gamma [/mathjaxinline]',
                'Greek letters:  α   β   γ',
            ),
            (
                'Subscripted variables: [mathjaxinline] x_i [/mathjaxinline] [mathjaxinline] y_j [/mathjaxinline]',
                'Subscripted variables:  xᵢ   yⱼ',
            ),
            ('Superscripted variables: [mathjaxinline] x^{i} [/mathjaxinline]', 'Superscripted variables:  xⁱ'),
            (
                'Not supported: \\( \\begin{bmatrix} 1 & 0 \\ 0 & 1 \\end{bmatrix} = I \\)',
                'Not supported:  \\begin{bmatrix} 1 & 0 \\ 0 & 1 \\end{bmatrix} = I',
            ),
            (
                'Bold text: \\( {\\bf a} \\cdot {\\bf b} = |{\\bf a}| |{\\bf b}| \\cos(\\theta) \\)',
                'Bold text:  a ⋅ b = |a| |b| cos(θ)',
            ),
            ('Bold text: \\( \\frac{\\sqrt{\\mathbf{2}+3}}{\\sqrt{4}} \\)', 'Bold text:  (√{2+3}/√{4})'),
            ('Nested Bold text 1: \\( \\mathbf{ \\frac{1}{2} } \\)', 'Nested Bold text 1:   (1/2)'),
            (
                'Nested Bold text 2: \\( \\mathbf{a \\cdot (a \\mathbf{\\times} b)} \\)',
                'Nested Bold text 2:  a ⋅ (a × b)'
            ),
            (
                'Nested Bold text 3: \\( \\mathbf{a \\cdot (a \\bm{\\times} b)} \\)',
                'Nested Bold text 3:  a ⋅ (a × b)'
            ),
            ('Sqrt test 1: \\(\\sqrt\\)', 'Sqrt test 1: √'),
            ('Sqrt test 2: \\(x^2 + \\sqrt(y)\\)', 'Sqrt test 2: x² + √(y)'),
            ('Sqrt test 3: [mathjaxinline]x^2 + \\sqrt(y)[/mathjaxinline]', 'Sqrt test 3: x² + √(y)'),
            ('Fraction test 1: \\( \\frac{2} {3} \\)', 'Fraction test 1:  (2/3)'),
            ('Fraction test 2: \\( \\frac{2}{3} \\)', 'Fraction test 2:  (2/3)'),
            ('Fraction test 3: \\( \\frac{\\frac{2}{3}}{4} \\)', 'Fraction test 3:  ((2/3)/4)'),
            ('Fraction test 4: \\( \\frac{\\frac{2} {3}}{4} \\)', 'Fraction test 4:  ((2/3)/4)'),
            ('Fraction test 5: \\( \\frac{\\frac{2} {3}}{\\frac{4}{3}} \\)', 'Fraction test 5:  ((2/3)/(4/3))'),
            # Invalid equations.
            ('Fraction error: \\( \\frac{2} \\)', 'Fraction error:  \\frac{2}'),
            ('Fraction error 2: \\( \\frac{\\frac{2}{3}{4} \\)', 'Fraction error 2:  \\frac{\\frac{2}{3}{4}'),
            ('Unclosed: [mathjaxinline]x^2', 'Unclosed: [mathjaxinline]x^2'),
            (
                'Missing closing bracket: \\( \\frac{\\frac{2} {3}{\\frac{4}{3}} \\)',
                'Missing closing bracket:  \\frac{\\frac{2} {3}{\\frac{4}{3}}'
            ),
            ('No equation: normal text', 'No equation: normal text'),
        ]
        # pylint: enable=line-too-long
        block = BlockFactory.create(
            parent_location=self.toy_course.location,
            category="html",
            display_name="Non-default HTML Block",
            editor="raw",
            use_latex_compiler=True,
            data="|||".join(e[0] for e in eqns),
        )
        doc = {}
        doc.update(searchable_doc_for_course_block(block))
        doc.update(searchable_doc_tags(block.usage_key))
        result = doc['description'].split('|||')
        for i, eqn in enumerate(result):
            assert eqn.strip() == eqns[i][1]
