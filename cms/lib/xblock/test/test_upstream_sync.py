"""
Test CMS's upstream->downstream syncing system
"""
import datetime

import ddt
from organizations.api import ensure_organization
from organizations.models import Organization

from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import save_xblock_with_callback
from cms.lib.xblock.upstream_sync import (
    BadDownstream,
    BadUpstream,
    NoUpstream,
    UpstreamLink,
    decline_sync,
    sever_upstream_link,
)
from cms.lib.xblock.upstream_sync_block import fetch_customizable_fields_from_block, sync_from_upstream_block
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api as libs
from openedx.core.djangoapps.content_tagging import api as tagging_api
from openedx.core.djangoapps.xblock import api as xblock
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class UpstreamTestCase(ModuleStoreTestCase):
    """
    Tests the upstream_sync mixin, data object, and Python APIs.
    """

    def setUp(self):
        """
        Create a simple course with one unit, and simple V2 library with two blocks.
        """
        super().setUp()
        course = CourseFactory.create()
        chapter = BlockFactory.create(category='chapter', parent=course)
        sequential = BlockFactory.create(category='sequential', parent=chapter)
        self.unit = BlockFactory.create(category='vertical', parent=sequential)

        ensure_organization("TestX")
        self.library = libs.create_library(
            org=Organization.objects.get(short_name="TestX"),
            slug="TestLib",
            title="Test Upstream Library",
        )
        self.upstream_key = libs.create_library_block(self.library.key, "html", "test-upstream").usage_key

        upstream = xblock.load_block(self.upstream_key, self.user)
        upstream.display_name = "Upstream Title V2"
        upstream.data = "<html><body>Upstream content V2</body></html>"
        upstream.save()

        self.upstream_problem_key = libs.create_library_block(self.library.key, "problem", "problem-upstream").usage_key
        libs.set_library_block_olx(self.upstream_problem_key, (
            '<problem'
            ' attempts_before_showanswer_button="1"'
            ' display_name="Upstream Problem Title V2"'
            ' due="2024-01-01T00:00:00Z"'
            ' force_save_button="false"'
            ' graceperiod="1d"'
            ' grading_method="last_attempt"'
            ' matlab_api_key="abc"'
            ' max_attempts="10"'
            ' rerandomize="&quot;always&quot;"'
            ' show_correctness="never"'
            ' show_reset_button="false"'
            ' showanswer="on_correct"'
            ' submission_wait_seconds="10"'
            ' use_latex_compiler="false"'
            ' weight="1"'
            '/>\n'
        ))

        self.upstream_video_key = libs.create_library_block(self.library.key, "video", "video-upstream").usage_key
        libs.set_library_block_olx(self.upstream_video_key, (
            '<video'
            ' display_name="Video Test"'
            ' edx_video_id=""'
            ' end_time="00:00:00"'
            ' html5_sources="[&quot;https://www.sample-videos.com/video321/mp4/720/big_buck_bunny_720p_2mb.mp4&quot;]"'
            ' start_time="00:00:00"'
            ' track=""'
            ' youtube_id_1_0=""'
            '>'
            ' <source src="https://www.sample-videos.com/video321/mp4/720/big_buck_bunny_720p_2mb.mp4"/>'
            '</video>'
        ))

        libs.publish_changes(self.library.key, self.user.id)

        self.taxonomy_all_org = tagging_api.create_taxonomy(
            "test_taxonomy",
            "Test Taxonomy",
            export_id="ALL_ORGS",
        )
        tagging_api.set_taxonomy_orgs(self.taxonomy_all_org, all_orgs=True)
        for tag_value in ('tag_1', 'tag_2', 'tag_3', 'tag_4', 'tag_5', 'tag_6', 'tag_7'):
            tagging_api.add_tag_to_taxonomy(self.taxonomy_all_org, tag_value)

        self.upstream_tags = ['tag_1', 'tag_5']
        tagging_api.tag_object(str(self.upstream_key), self.taxonomy_all_org, self.upstream_tags)

    def test_sync_bad_downstream(self):
        """
        Syncing into an unsupported downstream (such as a another Content Library block) raises BadDownstream, but
        doesn't affect the block.
        """
        downstream_lib_block_key = libs.create_library_block(self.library.key, "html", "bad-downstream").usage_key
        downstream_lib_block = xblock.load_block(downstream_lib_block_key, self.user)
        downstream_lib_block.display_name = "Another lib block"
        downstream_lib_block.data = "<html>another lib block</html>"
        downstream_lib_block.upstream = str(self.upstream_key)
        downstream_lib_block.save()

        with self.assertRaises(BadDownstream):  # noqa: PT027
            sync_from_upstream_block(downstream_lib_block, self.user)

        assert downstream_lib_block.display_name == "Another lib block"
        assert downstream_lib_block.data == "<html>another lib block</html>"

    def test_sync_no_upstream(self):
        """
        Trivial case: Syncing a block with no upstream is a no-op
        """
        block = BlockFactory.create(category='html', parent=self.unit)
        block.display_name = "Block Title"
        block.data = "Block content"

        with self.assertRaises(NoUpstream):  # noqa: PT027
            sync_from_upstream_block(block, self.user)

        assert block.display_name == "Block Title"
        assert block.data == "Block content"
        assert not block.upstream_display_name

    @ddt.data(
        ("not-a-key-at-all", ".*is malformed.*"),
        ("course-v1:Oops+ItsA+CourseKey", ".*is malformed.*"),
        ("block-v1:The+Wrong+KindOfUsageKey+type@html+block@nope", ".*is malformed.*"),
        ("lb:TestX:NoSuchLib:html:block-id", ".*not found in the system.*"),
        ("lb:TestX:TestLib:html:no-such-html", ".*not found in the system.*"),
    )
    @ddt.unpack
    def test_sync_bad_upstream(self, upstream, message_regex):
        """
        Syncing with a bad upstream raises BadUpstream, but doesn't affect the block
        """
        block = BlockFactory.create(category='html', parent=self.unit, upstream=upstream)
        block.display_name = "Block Title"
        block.data = "Block content"

        with self.assertRaisesRegex(BadUpstream, message_regex):  # noqa: PT027
            sync_from_upstream_block(block, self.user)

        assert block.display_name == "Block Title"
        assert block.data == "Block content"
        assert not block.upstream_display_name

    def test_sync_incompatible_upstream(self):
        """
        Syncing with a bad upstream raises BadUpstream, but doesn't affect the block
        """
        downstream_block = BlockFactory.create(
            category='html', parent=self.unit, upstream=str(self.upstream_problem_key),
        )
        downstream_block.display_name = "Block Title"
        downstream_block.data = "Block content"

        with self.assertRaisesRegex(BadUpstream, "Content type mismatch.*"):  # noqa: PT027
            sync_from_upstream_block(downstream_block, self.user)

        assert downstream_block.display_name == "Block Title"
        assert downstream_block.data == "Block content"
        assert not downstream_block.upstream_display_name

    def test_sync_not_accessible(self):
        """
        Syncing with an block that exists, but is inaccessible, raises BadUpstream
        """
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))
        user_who_cannot_read_upstream = UserFactory.create(username="rando", is_staff=False, is_superuser=False)
        with self.assertRaisesRegex(BadUpstream, ".*could not be loaded.*") as exc:  # noqa: F841, PT027
            sync_from_upstream_block(downstream, user_who_cannot_read_upstream)

    def test_sync_updates_happy_path(self):
        """
        Can we sync updates from a content library block to a linked out-of-date course block?
        """
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))

        # Initial sync
        sync_from_upstream_block(downstream, self.user)
        assert downstream.upstream_version == 2  # Library blocks start at version 2 (v1 is the empty new block)
        assert downstream.upstream_display_name == "Upstream Title V2"
        assert downstream.display_name == "Upstream Title V2"
        assert downstream.data == "<html><body>Upstream content V2</body></html>"

        # Verify tags
        object_tags = tagging_api.get_object_tags(str(downstream.location))
        assert len(object_tags) == len(self.upstream_tags)
        for object_tag in object_tags:
            assert object_tag.value in self.upstream_tags

        # Upstream updates
        upstream = xblock.load_block(self.upstream_key, self.user)
        upstream.display_name = "Upstream Title V3"
        upstream.data = "<html><body>Upstream content V3</body></html>"
        upstream.save()
        new_upstream_tags = self.upstream_tags + ['tag_2', 'tag_3']
        tagging_api.tag_object(str(self.upstream_key), self.taxonomy_all_org, new_upstream_tags)

        # Assert that un-published updates are not yet pulled into downstream
        sync_from_upstream_block(downstream, self.user)
        assert downstream.upstream_version == 2  # Library blocks start at version 2 (v1 is the empty new block)
        assert downstream.upstream_display_name == "Upstream Title V2"
        assert downstream.display_name == "Upstream Title V2"
        assert downstream.data == "<html><body>Upstream content V2</body></html>"

        # Publish changes
        libs.publish_changes(self.library.key, self.user.id)

        # Follow-up sync. Assert that updates are pulled into downstream.
        sync_from_upstream_block(downstream, self.user)
        assert downstream.upstream_version == 3
        assert downstream.upstream_display_name == "Upstream Title V3"
        assert downstream.display_name == "Upstream Title V3"
        assert downstream.data == "<html><body>Upstream content V3</body></html>"

        # Verify tags
        object_tags = tagging_api.get_object_tags(str(downstream.location))
        assert len(object_tags) == len(new_upstream_tags)
        for object_tag in object_tags:
            assert object_tag.value in new_upstream_tags

    # pylint: disable=too-many-statements
    def test_sync_updates_to_downstream_only_fields(self):
        """
        If we sync to modified content, will it preserve downstream-only fields, and overwrite the rest?
        """
        downstream = BlockFactory.create(category='problem', parent=self.unit, upstream=str(self.upstream_problem_key))

        # Initial sync
        sync_from_upstream_block(downstream, self.user)

        # These fields are copied from upstream
        assert downstream.upstream_display_name == "Upstream Problem Title V2"
        assert downstream.display_name == "Upstream Problem Title V2"
        assert downstream.rerandomize == '"always"'
        assert downstream.matlab_api_key == 'abc'
        assert not downstream.use_latex_compiler

        # These fields are "downstream only", so field defaults are preserved, and values are NOT copied from upstream
        assert downstream.attempts_before_showanswer_button == 0
        assert downstream.due is None
        assert not downstream.force_save_button
        assert downstream.graceperiod is None
        assert downstream.grading_method == 'last_score'
        assert downstream.max_attempts is None
        assert downstream.show_correctness == 'always'
        assert not downstream.show_reset_button
        assert downstream.showanswer == 'finished'
        assert downstream.submission_wait_seconds == 0
        assert downstream.weight is None

        # Upstream updates
        libs.set_library_block_olx(self.upstream_problem_key, (
            '<problem'
            ' attempts_before_showanswer_button="10"'
            ' display_name="Upstream Problem Title V3"'
            ' due="2024-02-02T00:00:00Z"'
            ' force_save_button="false"'
            ' graceperiod=""'
            ' grading_method="final_attempt"'
            ' matlab_api_key="def"'
            ' max_attempts="11"'
            ' rerandomize="&quot;per_student&quot;"'
            ' show_correctness="past_due"'
            ' show_reset_button="false"'
            ' showanswer="attempted"'
            ' submission_wait_seconds="11"'
            ' use_latex_compiler="true"'
            ' weight="2"'
            '/>\n'
        ))
        libs.publish_changes(self.library.key, self.user.id)

        # Modifing downstream-only fields are "safe" customizations
        downstream.display_name = "Downstream Title Override"
        downstream.attempts_before_showanswer_button = 2
        downstream.due = datetime.datetime(2025, 2, 2, tzinfo=datetime.timezone.utc)  # noqa: UP017
        downstream.force_save_button = True
        downstream.graceperiod = '2d'
        downstream.grading_method = 'last_score'
        downstream.max_attempts = 100
        downstream.show_correctness = 'always'
        downstream.show_reset_button = True
        downstream.showanswer = 'on_expired'
        downstream.submission_wait_seconds = 100
        downstream.weight = 3

        # Modifying synchronized fields are "unsafe" customizations
        downstream.rerandomize = '"onreset"'
        downstream.matlab_api_key = 'hij'
        save_xblock_with_callback(downstream, self.user)

        # Follow-up sync.
        sync_from_upstream_block(downstream, self.user)

        # "unsafe" customizations are overridden by upstream
        assert downstream.upstream_display_name == "Upstream Problem Title V3"
        assert downstream.rerandomize == '"per_student"'
        assert downstream.matlab_api_key == 'def'
        assert downstream.use_latex_compiler

        # but "safe" customizations survive
        assert downstream.display_name == "Downstream Title Override"
        assert downstream.attempts_before_showanswer_button == 2
        assert downstream.due == datetime.datetime(2025, 2, 2, tzinfo=datetime.timezone.utc)  # noqa: UP017
        assert downstream.force_save_button
        assert downstream.graceperiod == '2d'
        assert downstream.grading_method == 'last_score'
        assert downstream.max_attempts == 100
        assert downstream.show_correctness == 'always'
        assert downstream.show_reset_button
        assert downstream.showanswer == 'on_expired'
        assert downstream.submission_wait_seconds == 100
        assert downstream.weight == 3

    def test_sync_updates_to_modified_content(self):
        """
        If we sync to modified content, will it preserve customizable fields, but overwrite the rest?
        """
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))

        # Initial sync
        sync_from_upstream_block(downstream, self.user)
        assert downstream.upstream_display_name == "Upstream Title V2"
        assert downstream.display_name == "Upstream Title V2"
        assert downstream.data == "<html><body>Upstream content V2</body></html>"

        # Upstream updates
        upstream = xblock.load_block(self.upstream_key, self.user)
        upstream.display_name = "Upstream Title V3"
        upstream.data = "<html><body>Upstream content V3</body></html>"
        upstream.save()
        libs.publish_changes(self.library.key, self.user.id)

        # Downstream modifications, currently all fields are overridden on individual sync
        downstream.display_name = "Downstream Title Override"
        downstream.data = "Downstream content override"
        save_xblock_with_callback(downstream, self.user)

        # Follow-up sync. Assert that updates are pulled into downstream, but customizations are saved.
        sync_from_upstream_block(downstream, self.user)
        assert downstream.display_name == "Downstream Title Override"  # "safe" customization survives
        assert downstream.data == "Downstream content override"  # "safe" customization survives
        # Verify hidden field has latest upstream value
        assert downstream.upstream_data == "<html><body>Upstream content V3</body></html>"
        assert downstream.upstream_display_name == "Upstream Title V3"

    def test_sync_to_downstream_with_subtle_customization(self):
        """
        Edge case: If our downstream customizes a field, but then the upstream is changed to match the
                   customization do we still remember that the downstream field is customized? That is,
                   if the upstream later changes again, do we retain the downstream customization (rather than
                   following the upstream update?)
        """
        # Start with an uncustomized downstream block.
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))
        sync_from_upstream_block(downstream, self.user)
        assert downstream.downstream_customized == []
        assert downstream.display_name == downstream.upstream_display_name == "Upstream Title V2"

        # Then, customize our downstream title.
        downstream.display_name = "Title V3"
        save_xblock_with_callback(downstream, self.user)
        assert downstream.downstream_customized == ["display_name"]

        # Syncing should retain the customization if we allow display name customization.
        sync_from_upstream_block(
            downstream,
            self.user,
            override_customizations=True,
            keep_custom_fields=["display_name"]
        )
        assert downstream.upstream_version == 2
        assert downstream.upstream_display_name == "Upstream Title V2"
        assert downstream.display_name == "Title V3"

        # Whoa, look at that, the upstream has updated itself to the exact same title...
        upstream = xblock.load_block(self.upstream_key, self.user)
        upstream.display_name = "Title V3"
        upstream.save()
        libs.publish_changes(self.library.key, self.user.id)

        # ...which is reflected when we sync.
        sync_from_upstream_block(
            downstream,
            self.user,
            override_customizations=True,
            keep_custom_fields=["display_name"]
        )
        assert downstream.upstream_version == 3
        assert downstream.upstream_display_name == downstream.display_name == "Title V3"

        # But! Our downstream knows that its title is still customized.
        assert downstream.downstream_customized == ["display_name"]
        # So, if the upstream title changes again...
        upstream.display_name = "Title V4"
        upstream.save()
        libs.publish_changes(self.library.key, self.user.id)

        # ...then the downstream title should remain put.
        sync_from_upstream_block(
            downstream,
            self.user,
            override_customizations=True,
            keep_custom_fields=["display_name"]
        )
        assert downstream.upstream_version == 4
        assert downstream.upstream_display_name == "Title V4"
        assert downstream.display_name == "Title V3"

        # Finally, if we don't allow keeping any customizations
        upstream.display_name = "Title V5"
        upstream.save()
        libs.publish_changes(self.library.key, self.user.id)
        sync_from_upstream_block(
            downstream,
            self.user,
            override_customizations=True,
            keep_custom_fields=[]
        )  # No customizations!
        assert downstream.upstream_version == 5
        assert downstream.upstream_display_name == downstream.display_name == "Title V5"
        # Clears downstream_customized field as well
        assert downstream.downstream_customized == []

    @ddt.data(None, "Title From Some Other Upstream Version")
    def test_update_customizable_fields(self, initial_upstream_display_name):
        """
        Can we fetch a block's upstream field values without syncing it?

        Test both with and without a pre-"fetched" upstrema values on the downstream.
        """
        downstream = BlockFactory.create(category='html', parent=self.unit)
        downstream.upstream_display_name = initial_upstream_display_name
        downstream.display_name = "Some Title"
        downstream.data = "<html><data>Some content</data></html>"

        # Note that we're not linked to any upstream. fetch_customizable_fields_from_block shouldn't care.
        assert not downstream.upstream
        assert not downstream.upstream_version

        # fetch!
        upstream = xblock.load_block(self.upstream_key, self.user)
        fetch_customizable_fields_from_block(upstream=upstream, downstream=downstream, user=self.user)

        # Ensure: fetching doesn't affect the upstream link (or lack thereof).
        assert not downstream.upstream
        assert not downstream.upstream_version

        # Ensure: fetching doesn't affect actual content or settings.
        assert downstream.display_name == "Some Title"
        assert downstream.data == "<html><data>Some content</data></html>"

        # Ensure: fetching DOES set the upstream_* fields.
        assert downstream.upstream_display_name == "Upstream Title V2"

    def test_prompt_and_decline_sync(self):
        """
        Is the user prompted for sync when it's available? Does declining remove the prompt until a new sync is ready?
        """
        # Initial conditions (pre-sync)
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))
        link = UpstreamLink.get_for_block(downstream)
        assert link.version_synced is None
        assert link.version_declined is None
        assert link.version_available == 2  # Library block with content starts at version 2
        assert link.ready_to_sync is True

        # Initial sync to V2
        sync_from_upstream_block(downstream, self.user)
        link = UpstreamLink.get_for_block(downstream)
        assert link.version_synced == 2
        assert link.version_declined is None
        assert link.version_available == 2
        assert link.ready_to_sync is False

        # Upstream updated to V3, but not yet published
        upstream = xblock.load_block(self.upstream_key, self.user)
        upstream.data = "<html><body>Upstream content V3</body></html>"
        upstream.save()
        link = UpstreamLink.get_for_block(downstream)
        assert link.version_synced == 2
        assert link.version_declined is None
        assert link.version_available == 2
        assert link.ready_to_sync is False

        # Publish changes
        libs.publish_changes(self.library.key, self.user.id)
        link = UpstreamLink.get_for_block(downstream)
        assert link.version_synced == 2
        assert link.version_declined is None
        assert link.version_available == 3
        assert link.ready_to_sync is True

        # Decline to sync to V3 -- ready_to_sync becomes False.
        decline_sync(downstream)
        link = UpstreamLink.get_for_block(downstream)
        assert link.version_synced == 2
        assert link.version_declined == 3
        assert link.version_available == 3
        assert link.ready_to_sync is False

        # Upstream updated to V4 -- ready_to_sync becomes True again.
        upstream = xblock.load_block(self.upstream_key, self.user)
        upstream.data = "<html><body>Upstream content V4</body></html>"
        upstream.save()
        libs.publish_changes(self.library.key, self.user.id)
        link = UpstreamLink.get_for_block(downstream)
        assert link.version_synced == 2
        assert link.version_declined == 3
        assert link.version_available == 4
        assert link.ready_to_sync is True

    def test_sever_upstream_link(self):
        """
        Does sever_upstream_link correctly disconnect a block from its upstream?
        """
        # Start with a course block that is linked+synced to a content library block
        # and has a customizred title.
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))
        sync_from_upstream_block(downstream, self.user)
        downstream.display_name = "Downstream Title"
        save_xblock_with_callback(downstream, self.user)

        # (sanity checks)
        assert downstream.upstream == str(self.upstream_key)
        assert downstream.upstream_version == 2
        assert downstream.upstream_display_name == "Upstream Title V2"
        assert downstream.display_name == "Downstream Title"
        assert downstream.downstream_customized == ["display_name"]
        assert downstream.data == "<html><body>Upstream content V2</body></html>"
        assert downstream.copied_from_block is None

        # Now, disconnect the course block.
        sever_upstream_link(downstream)

        # All upstream metadata has been wiped out.
        assert downstream.upstream is None
        assert downstream.upstream_version is None
        assert downstream.upstream_display_name is None
        assert downstream.downstream_customized == []

        # BUT, the content remains.
        assert downstream.display_name == "Downstream Title"
        assert downstream.data == "<html><body>Upstream content V2</body></html>"

        # AND, we have recorded the old upstream as our copied_from_block.
        assert downstream.copied_from_block == str(self.upstream_key)

        # Finally... unlike an upstream-linked block, our unlinked block should not
        # have its downstream_customized updated when the title changes.
        downstream.display_name = "Downstream Title II"
        save_xblock_with_callback(downstream, self.user)
        assert downstream.downstream_customized == []

    def test_sync_library_block_tags(self):
        upstream_lib_block_key = libs.create_library_block(self.library.key, "html", "upstream").usage_key
        upstream_lib_block = xblock.load_block(upstream_lib_block_key, self.user)
        upstream_lib_block.display_name = "Another lib block"
        upstream_lib_block.data = "<html>another lib block</html>"
        upstream_lib_block.save()

        libs.publish_changes(self.library.key, self.user.id)

        expected_tags = self.upstream_tags
        tagging_api.tag_object(str(upstream_lib_block_key), self.taxonomy_all_org, expected_tags)

        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(upstream_lib_block_key))

        # Initial sync
        sync_from_upstream_block(downstream, self.user)

        # Verify tags
        object_tags = tagging_api.get_object_tags(str(downstream.location))
        assert len(object_tags) == len(expected_tags)
        for object_tag in object_tags:
            assert object_tag.value in expected_tags

        # Upstream updates
        upstream_lib_block.display_name = "Upstream Title V3"
        upstream_lib_block.data = "<html><body>Upstream content V3</body></html>"
        upstream_lib_block.save()
        new_upstream_tags = self.upstream_tags + ['tag_2', 'tag_3']
        tagging_api.tag_object(str(upstream_lib_block_key), self.taxonomy_all_org, new_upstream_tags)

        # Follow-up sync.
        sync_from_upstream_block(downstream, self.user)

        #Verify tags
        object_tags = tagging_api.get_object_tags(str(downstream.location))
        assert len(object_tags) == len(new_upstream_tags)
        for object_tag in object_tags:
            assert object_tag.value in new_upstream_tags

    def test_sync_video_block(self):
        downstream = BlockFactory.create(category='video', parent=self.unit, upstream=str(self.upstream_video_key))
        downstream.edx_video_id = "test_video_id"

        # Sync
        sync_from_upstream_block(downstream, self.user)
        assert downstream.upstream_version == 2
        assert downstream.upstream_display_name == "Video Test"
        assert downstream.display_name == "Video Test"

        # `edx_video_id` doesn't change
        assert downstream.edx_video_id == "test_video_id"

    def test_sync_keep_customizaton_option(self):
        """
        Test that when an upstream block has a customized downstream block, we keep
        the customized options when syncing based on keep_custom_fields option.
        """
        # Start with an uncustomized downstream block.
        downstream = BlockFactory.create(category='html', parent=self.unit, upstream=str(self.upstream_key))
        sync_from_upstream_block(downstream, self.user)
        assert downstream.downstream_customized == []
        assert downstream.display_name == downstream.upstream_display_name == "Upstream Title V2"

        # Then, customize our downstream title and content
        downstream.display_name = "Title V3"
        downstream.data = "<html><data>Some content</data></html>"
        save_xblock_with_callback(downstream, self.user)
        assert downstream.downstream_customized == ["display_name", "data"]

        # Now, sync the upstream block with `keep_custom_fields=["display_name"] only`.
        # And let data be overridden
        sync_from_upstream_block(
            downstream,
            self.user,
            override_customizations=True,
            keep_custom_fields=['display_name']
        )
        assert downstream.display_name == "Title V3"
        # data is overridden
        assert downstream.data == "<html><body>Upstream content V2</body></html>"
        assert downstream.downstream_customized == ["display_name"]
