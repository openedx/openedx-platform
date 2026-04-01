"""
Test the import_staged_content_from_user_clipboard() method, which is used to
allow users to paste XBlocks that were copied using the staged_content/clipboard
APIs.
"""
import ddt
from opaque_keys.edx.keys import UsageKey
from rest_framework.test import APIClient
from openedx_events.content_authoring.signals import (
    LIBRARY_BLOCK_DELETED,
    XBLOCK_CREATED,
    XBLOCK_DELETED,
    XBLOCK_UPDATED,
)
from openedx_events.tests.utils import OpenEdxEventsTestMixin
from openedx_tagging.models import Tag
from organizations.models import Organization
from xmodule.modulestore.django import contentstore, modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, upload_file_to_course, ImmediateOnCommitMixin
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, ToyCourseFactory, LibraryFactory

from cms.djangoapps.contentstore.utils import reverse_usage_url
from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangoapps.content_tagging import api as tagging_api

CLIPBOARD_ENDPOINT = "/api/content-staging/v1/clipboard/"
XBLOCK_ENDPOINT = "/xblock/"


@ddt.ddt
class ClipboardPasteTestCase(ModuleStoreTestCase):
    """
    Test Clipboard Paste functionality
    """

    def _setup_course(self):
        """ Set up the "Toy Course" and an APIClient for testing clipboard functionality. """
        # Setup:
        course_key = ToyCourseFactory.create().id  # See xmodule/modulestore/tests/sample_courses.py
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)
        return (course_key, client)

    def test_copy_and_paste_video(self):
        """
        Test copying a video from the course, and pasting it into the same unit
        """
        course_key, client = self._setup_course()

        # Check how many blocks are in the vertical currently
        parent_key = course_key.make_usage_key("vertical", "vertical_test")  # This is the vertical that holds the video
        orig_vertical = self.store.get_item(parent_key)
        assert len(orig_vertical.children) == 4

        # Copy the video
        video_key = course_key.make_usage_key("video", "sample_video")
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(video_key)}, format="json")
        assert copy_response.status_code == 200

        # Paste the video
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(parent_key),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        new_block_key = UsageKey.from_string(paste_response.json()["locator"])

        # Now there should be an extra block in the vertical:
        updated_vertical = self.store.get_item(parent_key)
        assert len(updated_vertical.children) == 5
        assert updated_vertical.children[-1] == new_block_key
        # And it should match the original:
        orig_video = self.store.get_item(video_key)
        new_video = self.store.get_item(new_block_key)
        assert new_video.youtube_id_1_0 == orig_video.youtube_id_1_0
        # The new block should store a reference to where it was copied from
        assert new_video.copied_from_block == str(video_key)

    def test_copy_and_paste_unit(self):
        """
        Test copying a unit (vertical) from one course into another
        """
        course_key, client = self._setup_course()
        dest_course = CourseFactory.create(display_name='Destination Course')
        with self.store.bulk_operations(dest_course.id):
            dest_chapter = BlockFactory.create(parent=dest_course, category='chapter', display_name='Section')
            dest_sequential = BlockFactory.create(parent=dest_chapter, category='sequential', display_name='Subsection')

        # Copy the unit
        unit_key = course_key.make_usage_key("vertical", "vertical_test")
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(unit_key)}, format="json")
        assert copy_response.status_code == 200

        # Paste the unit
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_sequential.location),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        dest_unit_key = UsageKey.from_string(paste_response.json()["locator"])

        # Now there should be a one unit/vertical as a child of the destination sequential/subsection:
        updated_sequential = self.store.get_item(dest_sequential.location)
        assert updated_sequential.children == [dest_unit_key]
        # And it should match the original:
        orig_unit = self.store.get_item(unit_key)
        dest_unit = self.store.get_item(dest_unit_key)
        assert len(orig_unit.children) == len(dest_unit.children)
        # Check details of the fourth child (a poll)
        orig_poll = self.store.get_item(orig_unit.children[3])
        dest_poll = self.store.get_item(dest_unit.children[3])
        assert dest_poll.display_name == orig_poll.display_name
        assert dest_poll.question == orig_poll.question
        # The new block should store a reference to where it was copied from
        assert dest_unit.copied_from_block == str(unit_key)

    @ddt.data(
        # A problem with absolutely no fields set. A previous version of copy-paste had an error when pasting this.
        {"category": "problem", "display_name": None, "data": ""},
        {"category": "problem", "display_name": "Emoji Land 😎", "data": "<problem>emoji in the body 😎</problem>"},
    )
    def test_copy_and_paste_component(self, block_args):
        """
        Test copying a component (XBlock) from one course into another
        """
        source_course = CourseFactory.create(display_name='Source Course')
        source_block = BlockFactory.create(parent_location=source_course.location, **block_args)

        dest_course = CourseFactory.create(display_name='Destination Course')
        with self.store.bulk_operations(dest_course.id):
            dest_chapter = BlockFactory.create(parent=dest_course, category='chapter', display_name='Section')
            dest_sequential = BlockFactory.create(parent=dest_chapter, category='sequential', display_name='Subsection')

        # Copy the block
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(source_block.location)}, format="json")
        assert copy_response.status_code == 200

        # Paste the unit
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_sequential.location),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        dest_block_key = UsageKey.from_string(paste_response.json()["locator"])

        dest_block = self.store.get_item(dest_block_key)
        assert dest_block.display_name == source_block.display_name
        # The new block should store a reference to where it was copied from
        assert dest_block.copied_from_block == str(source_block.location)

    def _setup_tagged_content(self, course_key) -> dict:
        """
        Create and tag content to use in copy/paste tests.
        """
        # Add a couple more blocks to test tagging different types of blocks.
        # We add these here instead of in sample_courses.py to avoid breaking modulestore tests.
        unit_key = course_key.make_usage_key("vertical", "vertical_test")
        with self.store.bulk_operations(course_key):
            discussion_block_key = BlockFactory.create(
                parent=self.store.get_item(unit_key),
                category='discussion',
                display_name='Toy_forum',
                publish_item=True,
            ).location
        with self.store.bulk_operations(course_key):
            html_block_key = BlockFactory.create(
                parent=self.store.get_item(unit_key),
                category="html",
                display_name="Toy_text",
                publish_item=True,
            ).location

        library = ClipboardPasteFromV1LibraryTestCase.setup_library()
        with self.store.bulk_operations(course_key):
            library_content_block_key = BlockFactory.create(
                parent=self.store.get_item(unit_key),
                category="library_content",
                source_library_id=str(library.context_key),
                display_name="LC Block",
                publish_item=True,
            ).location

        # Add tags to the unit
        taxonomy_all_org = tagging_api.create_taxonomy(
            "test_taxonomy",
            "Test Taxonomy",
            export_id="ALL_ORGS",
        )

        tagging_api.set_taxonomy_orgs(taxonomy_all_org, all_orgs=True)
        for tag_value in ('tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7'):
            tagging_api.add_tag_to_taxonomy(taxonomy_all_org, tag_value)
        tagging_api.tag_object(
            object_id=str(unit_key),
            taxonomy=taxonomy_all_org,
            tags=["tag_1", "tag_2"],
        )

        # Tag some sub-blocks with different tags
        video_block_key = course_key.make_usage_key("video", "sample_video")
        tagging_api.tag_object(
            object_id=str(video_block_key),
            taxonomy=taxonomy_all_org,
            tags=["tag_3"],
        )
        poll_block_key = course_key.make_usage_key("poll_question", "T1_changemind_poll_foo_2")
        tagging_api.tag_object(
            object_id=str(poll_block_key),
            taxonomy=taxonomy_all_org,
            tags=["tag_4"],
        )
        tagging_api.tag_object(
            object_id=str(discussion_block_key),
            taxonomy=taxonomy_all_org,
            tags=["tag_5"],
        )
        tagging_api.tag_object(
            object_id=str(html_block_key),
            taxonomy=taxonomy_all_org,
            tags=["tag_6"],
        )
        tagging_api.tag_object(
            object_id=str(library_content_block_key),
            taxonomy=taxonomy_all_org,
            tags=["tag_7"],
        )

        # Tag our blocks using a taxonomy we'll remove before pasting -- so these tags won't be pasted
        all_block_keys = {
            key.block_type: key
            for key in (
                unit_key,
                video_block_key,
                poll_block_key,
                discussion_block_key,
                html_block_key,
                library_content_block_key,
            )
        }

        taxonomy_all_org_removed = tagging_api.create_taxonomy(
            "test_taxonomy_removed",
            "Test Taxonomy Removed",
            export_id="REMOVE_ME",
        )
        tagging_api.set_taxonomy_orgs(taxonomy_all_org_removed, all_orgs=True)
        Tag.objects.create(taxonomy=taxonomy_all_org_removed, value="tag_1")
        Tag.objects.create(taxonomy=taxonomy_all_org_removed, value="tag_2")
        for object_key in all_block_keys.values():
            tagging_api.tag_object(
                object_id=str(object_key),
                taxonomy=taxonomy_all_org_removed,
                tags=["tag_1", "tag_2"],
            )

        # Tag our blocks using a taxonomy that isn't enabled for any orgs -- these tags won't be pasted
        taxonomy_no_org = tagging_api.create_taxonomy("test_taxonomy_no_org", "Test Taxonomy No Org")
        Tag.objects.create(taxonomy=taxonomy_no_org, value="tag_1")
        Tag.objects.create(taxonomy=taxonomy_no_org, value="tag_2")
        for object_key in all_block_keys.values():
            tagging_api.tag_object(
                object_id=str(object_key),
                taxonomy=taxonomy_no_org,
                tags=["tag_1", "tag_2"],
            )

        return all_block_keys

    def test_copy_and_paste_unit_with_tags(self):
        """
        Test copying a unit (vertical) with tags from one course into another
        """
        course_key, client = self._setup_course()
        source_block_keys = self._setup_tagged_content(course_key)

        dest_course = CourseFactory.create(display_name='Destination Course')
        with self.store.bulk_operations(dest_course.id):
            dest_chapter = BlockFactory.create(parent=dest_course, category='chapter', display_name='Section')
            dest_sequential = BlockFactory.create(parent=dest_chapter, category='sequential', display_name='Subsection')

        # Copy the unit
        unit_key = source_block_keys['vertical']
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(unit_key)}, format="json")
        assert copy_response.status_code == 200

        # Delete one of the taxonomies used, to test that their tags aren't pasted.
        tagging_api.get_taxonomy_by_export_id('REMOVE_ME').delete()

        # Paste the unit
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_sequential.location),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        dest_unit_key = UsageKey.from_string(paste_response.json()["locator"])

        # Only tags from the taxonomy that is associated with the dest org should be copied
        tags = list(tagging_api.get_object_tags(str(dest_unit_key)))
        assert len(tags) == 2
        assert str(tags[0]) == f'<ObjectTag> {dest_unit_key}: test_taxonomy=tag_1'
        assert str(tags[1]) == f'<ObjectTag> {dest_unit_key}: test_taxonomy=tag_2'

        # Ensure that the pasted child blocks were tagged too.
        dest_tags, _ = tagging_api.get_all_object_tags(dest_course.id)
        dest_block_ids = {
            UsageKey.from_string(block_id).block_type: block_id
            for block_id in dest_tags
        }
        taxonomy_all_org = tagging_api.get_taxonomy_by_export_id('ALL_ORGS')

        dest_video_id = dest_block_ids.get('video')
        assert dest_video_id, f"No tags pasted from {source_block_keys['video']}?"
        assert dest_tags.get(dest_video_id, {}).get(taxonomy_all_org.id) == ["tag_3"]

        dest_poll_id = dest_block_ids.get('poll_question')
        assert dest_poll_id, f"No tags pasted from {source_block_keys['poll_question']}?"
        assert dest_tags.get(dest_poll_id, {}).get(taxonomy_all_org.id) == ["tag_4"]

        dest_discussion_id = dest_block_ids.get('discussion')
        assert dest_discussion_id, f"No tags pasted from {source_block_keys['discussion']}?"
        assert dest_tags.get(dest_discussion_id, {}).get(taxonomy_all_org.id) == ["tag_5"]

        dest_html_id = dest_block_ids.get('html')
        assert dest_html_id, f"No tags pasted from {source_block_keys['html']}?"
        assert dest_tags.get(dest_html_id, {}).get(taxonomy_all_org.id) == ["tag_6"]

        dest_library_id = dest_block_ids.get('library_content')
        assert dest_library_id, f"No tags pasted from {source_block_keys['library_content']}?"
        assert dest_tags.get(dest_library_id, {}).get(taxonomy_all_org.id) == ["tag_7"]

    def test_paste_with_assets(self):
        """
        When pasting into a different course, any required static assets should
        be pasted too, unless they already exist in the destination course.
        """
        dest_course_key, client = self._setup_course()
        # Make sure some files exist in the source course to be copied:
        source_course = CourseFactory.create()
        upload_file_to_course(
            course_key=source_course.id,
            contentstore=contentstore(),
            source_file='./common/test/data/static/picture1.jpg',
            target_filename="picture1.jpg",
        )
        upload_file_to_course(
            course_key=source_course.id,
            contentstore=contentstore(),
            source_file='./common/test/data/static/picture2.jpg',
            target_filename="picture2.jpg",
        )
        source_html = BlockFactory.create(
            parent_location=source_course.location,
            category="html",
            display_name="Some HTML",
            data="""
            <p>
                <a href="/static/picture1.jpg">Picture 1</a>
                <a href="/static/picture2.jpg">Picture 2</a>
            </p>
            """,
        )

        # Now, to test conflict handling, we also upload a CONFLICTING image to
        # the destination course under the same filename.
        upload_file_to_course(
            course_key=dest_course_key,
            contentstore=contentstore(),
            # Note this is picture 3, not picture 2, but we save it as picture 2:
            source_file='./common/test/data/static/picture3.jpg',
            target_filename="picture2.jpg",
        )

        # Now copy the HTML block from the source cost and paste it into the destination:
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(source_html.location)}, format="json")
        assert copy_response.status_code == 200

        # Paste the video
        dest_parent_key = dest_course_key.make_usage_key("vertical", "vertical_test")
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_parent_key),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        static_file_notices = paste_response.json()["static_file_notices"]
        assert static_file_notices == {
            "error_files": [],
            "new_files": ["picture1.jpg"],
            # The new course already had a file named "picture2.jpg" with different md5 hash, so it's a conflict:
            "conflicting_files": ["picture2.jpg"],
        }

        # Check that the files are as we expect:
        source_pic1_hash = contentstore().find(source_course.id.make_asset_key("asset", "picture1.jpg")).content_digest
        dest_pic1_hash = contentstore().find(dest_course_key.make_asset_key("asset", "picture1.jpg")).content_digest
        assert source_pic1_hash == dest_pic1_hash
        source_pic2_hash = contentstore().find(source_course.id.make_asset_key("asset", "picture2.jpg")).content_digest
        dest_pic2_hash = contentstore().find(dest_course_key.make_asset_key("asset", "picture2.jpg")).content_digest
        assert source_pic2_hash != dest_pic2_hash  # Because there was a conflict, this file was unchanged.


class ClipboardPasteFromV2LibraryTestCase(OpenEdxEventsTestMixin, ImmediateOnCommitMixin, ModuleStoreTestCase):
    """
    Test Clipboard Paste functionality with a "new" (as of Sumac) library
    """
    ENABLED_OPENEDX_EVENTS = [
        LIBRARY_BLOCK_DELETED.event_type,
        XBLOCK_CREATED.event_type,
        XBLOCK_DELETED.event_type,
        XBLOCK_UPDATED.event_type,
    ]

    def setUp(self):
        """
        Set up a v2 Content Library and a library content block
        """
        super().setUp()
        self.client = APIClient()
        self.client.login(username=self.user.username, password=self.user_password)
        self.store = modulestore()

        self.library = library_api.create_library(
            org=Organization.objects.create(name="Test Org", short_name="CL-TEST"),
            slug="lib",
            title="Library",
        )

        self.lib_block_key = library_api.create_library_block(self.library.key, "problem", "p1").usage_key  # v==1
        library_api.set_library_block_olx(self.lib_block_key, """
        <problem display_name="MCQ-published" max_attempts="1">
            <multiplechoiceresponse>
                <label>Q</label>
                <choicegroup type="MultipleChoice">
                    <choice correct="false">Wrong</choice>
                    <choice correct="true">Right</choice>
                </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """)  # v==2
        library_api.publish_changes(self.library.key)
        library_api.set_library_block_olx(self.lib_block_key, """
        <problem display_name="MCQ-draft" max_attempts="5">
            <multiplechoiceresponse>
                <label>Q</label>
                <choicegroup type="MultipleChoice">
                    <choice correct="false">Wrong</choice>
                    <choice correct="true">Right</choice>
                </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """)  # v==3
        lib_block_meta = library_api.get_library_block(self.lib_block_key)
        assert lib_block_meta.published_version_num == 2
        assert lib_block_meta.draft_version_num == 3

        self.course = CourseFactory.create(display_name='Course')

        taxonomy_all_org = tagging_api.create_taxonomy(
            "test_taxonomy",
            "Test Taxonomy",
            export_id="ALL_ORGS",
        )
        tagging_api.set_taxonomy_orgs(taxonomy_all_org, all_orgs=True)
        for tag_value in ('tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7'):
            tagging_api.add_tag_to_taxonomy(taxonomy_all_org, tag_value)

        self.lib_block_tags = ['tag_1', 'tag_5']
        tagging_api.tag_object(str(self.lib_block_key), taxonomy_all_org, self.lib_block_tags)

    def test_paste_from_library_read_only_tags(self):
        """
        When we copy a v2 lib block into a course, the dest block should have read-only copied tags.
        """

        copy_response = self.client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(self.lib_block_key)}, format="json")
        assert copy_response.status_code == 200

        paste_response = self.client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(self.course.usage_key),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200

        new_block_key = paste_response.json()["locator"]

        object_tags = tagging_api.get_object_tags(new_block_key)
        assert len(object_tags) == len(self.lib_block_tags)
        for object_tag in object_tags:
            assert object_tag.value in self.lib_block_tags
            assert object_tag.is_copied

        # If we delete the upstream library block...
        library_api.delete_library_block(self.lib_block_key)

        # ...the copied tags remain, but should no longer be marked as "copied"
        object_tags = tagging_api.get_object_tags(new_block_key)
        assert len(object_tags) == len(self.lib_block_tags)
        for object_tag in object_tags:
            assert object_tag.value in self.lib_block_tags
            assert not object_tag.is_copied

    def test_paste_from_library_copies_asset(self):
        """
        Assets from a library component copied into a subdir of Files & Uploads.
        """
        # This is the binary for a real, 1px webp file – we need actual image
        # data because contentstore will try to make a thumbnail and grab
        # metadata.
        webp_raw_data = b'RIFF\x16\x00\x00\x00WEBPVP8L\n\x00\x00\x00/\x00\x00\x00\x00E\xff#\xfa\x1f'

        # First add the asset.
        library_api.add_library_block_static_asset_file(
            self.lib_block_key,
            "static/1px.webp",
            webp_raw_data,
        )  # v==4

        # Now add the reference to the asset
        library_api.set_library_block_olx(self.lib_block_key, """
        <problem display_name="MCQ-draft" max_attempts="5">
            <p>Including this totally real image: <img src="/static/1px.webp" /></p>
            <multiplechoiceresponse>
                <label>Q</label>
                <choicegroup type="MultipleChoice">
                    <choice correct="false">Wrong</choice>
                    <choice correct="true">Right</choice>
                </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """)  # v==5

        copy_response = self.client.post(
            CLIPBOARD_ENDPOINT,
            {"usage_key": str(self.lib_block_key)},
            format="json"
        )
        assert copy_response.status_code == 200

        paste_response = self.client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(self.course.usage_key),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200

        new_block_key = UsageKey.from_string(paste_response.json()["locator"])
        new_block = modulestore().get_item(new_block_key)

        # Check that the substitution worked.
        expected_import_path = f"components/{new_block_key.block_type}/{new_block_key.block_id}/1px.webp"
        assert f"/static/{expected_import_path}" in new_block.data

        # Check that the asset was copied over properly
        image_asset = contentstore().find(
            self.course.id.make_asset_key("asset", expected_import_path.replace('/', '_'))
        )
        assert image_asset.import_path == expected_import_path
        assert image_asset.name == "1px.webp"
        assert image_asset.length == len(webp_raw_data)

    def test_paste_from_course_block_imported_from_library_creates_link(self):
        """
        When we copy a course xblock which was imported or copied from v2 lib block into a course,
        the dest block should be linked up to the original lib block.
        """
        def _copy_paste_and_assert_link(key_to_copy):
            copy_response = self.client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(key_to_copy)}, format="json")
            assert copy_response.status_code == 200

            paste_response = self.client.post(XBLOCK_ENDPOINT, {
                "parent_locator": str(self.course.usage_key),
                "staged_content": "clipboard",
            }, format="json")
            assert paste_response.status_code == 200

            new_block_key = UsageKey.from_string(paste_response.json()["locator"])
            new_block = modulestore().get_item(new_block_key)
            assert new_block.upstream == str(self.lib_block_key)
            assert new_block.upstream_version == 3
            assert new_block.upstream_display_name == "MCQ-draft"
            return new_block_key

        # first verify link for copied block from library
        new_block_key = _copy_paste_and_assert_link(self.lib_block_key)
        # next verify link for copied block from the pasted block
        _copy_paste_and_assert_link(new_block_key)


class ClipboardPasteFromV1LibraryTestCase(ModuleStoreTestCase):
    """
    Test Clipboard Paste functionality with legacy (v1) library content
    """

    def setUp(self):
        """
        Set up a v1 Content Library and a library content block
        """
        super().setUp()
        self.client = APIClient()
        self.client.login(username=self.user.username, password=self.user_password)
        self.store = modulestore()
        self.library = self.setup_library()

        # Create a library content block (lc), point it out our library, and sync it.
        self.course = CourseFactory.create(display_name='Course')
        self.orig_lc_block = BlockFactory.create(
            parent=self.course,
            category="library_content",
            source_library_id=str(self.library.context_key),
            display_name="LC Block",
            publish_item=False,
        )
        self.dest_lc_block = None

        self._sync_lc_block_from_library('orig_lc_block')
        orig_child = self.store.get_item(self.orig_lc_block.children[0])
        assert orig_child.display_name == "MCQ"

    @classmethod
    def setup_library(cls):
        """
        Creates and returns a legacy content library with 1 problem
        """
        library = LibraryFactory.create(display_name='Library')
        lib_block = BlockFactory.create(
            parent_location=library.usage_key,
            category="problem",
            display_name="MCQ",
            max_attempts=1,
            data="""
            <multiplechoiceresponse>
                <label>Q</label>
                <choicegroup type="MultipleChoice">
                    <choice correct="false">Wrong</choice>
                    <choice correct="true">Right</choice>
                </choicegroup>
            </multiplechoiceresponse>
            """,
            publish_item=False,
        )
        return library

    def test_paste_library_content_block(self):
        """
        Test the special handling of copying and pasting library content
        """
        # Copy a library content block that has children:
        copy_response = self.client.post(CLIPBOARD_ENDPOINT, {
            "usage_key": str(self.orig_lc_block.location)
        }, format="json")
        assert copy_response.status_code == 200

        # Paste the Library content block:
        paste_response = self.client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(self.course.location),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        dest_lc_block_key = UsageKey.from_string(paste_response.json()["locator"])

        # Get the ID of the new child:
        self.dest_lc_block = self.store.get_item(dest_lc_block_key)
        dest_child = self.store.get_item(self.dest_lc_block.children[0])
        assert dest_child.display_name == "MCQ"

        # Importantly, the ID of the child must not changed when the library content is synced.
        # Otherwise, user state saved against this child will be lost when it syncs.
        self._sync_lc_block_from_library('dest_lc_block')
        updated_dest_child = self.store.get_item(self.dest_lc_block.children[0])
        assert dest_child.location == updated_dest_child.location

    def _sync_lc_block_from_library(self, attr_name):
        """
        Helper method to "sync" a Library Content Block by [re-]fetching its
        children from the library.
        """
        usage_key = getattr(self, attr_name).location
        # It's easiest to do this via the REST API:
        handler_url = reverse_usage_url('preview_handler', usage_key, kwargs={'handler': 'upgrade_and_sync'})
        response = self.client.post(handler_url)
        assert response.status_code == 200
        # Now reload the block and make sure the child is in place
        setattr(self, attr_name, self.store.get_item(usage_key))  # we must reload after upgrade_and_sync
