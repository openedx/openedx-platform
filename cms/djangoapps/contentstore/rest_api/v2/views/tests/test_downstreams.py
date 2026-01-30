"""
Unit tests for /api/contentstore/v2/downstreams/* JSON APIs.
"""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import ddt
from django.conf import settings
from django.urls import reverse
from freezegun import freeze_time
from opaque_keys.edx.keys import ContainerKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2, LibraryUsageLocatorV2
from organizations.models import Organization

from cms.djangoapps.contentstore.helpers import StaticFileNotices
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.xblock_storage_handlers import view_handlers as xblock_view_handlers
from cms.djangoapps.contentstore.xblock_storage_handlers.xblock_helpers import get_block_key_string
from cms.lib.xblock.upstream_sync import BadUpstream, UpstreamLink
from common.djangoapps.student.auth import add_users
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api as lib_api
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ImmediateOnCommitMixin, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from .. import downstreams as downstreams_views

MOCK_UPSTREAM_ERROR = "your LibraryGPT subscription has expired"
URL_PREFIX = '/api/libraries/v2/'
URL_LIB_CREATE = URL_PREFIX
URL_LIB_BLOCKS = URL_PREFIX + '{lib_key}/blocks/'
URL_LIB_BLOCK = URL_PREFIX + 'blocks/{block_key}/'
URL_LIB_BLOCK_PUBLISH = URL_PREFIX + 'blocks/{block_key}/publish/'
URL_LIB_BLOCK_OLX = URL_PREFIX + 'blocks/{block_key}/olx/'
URL_LIB_CONTAINER = URL_PREFIX + 'containers/{container_key}/'  # Get a container in this library
URL_LIB_CONTAINERS = URL_PREFIX + '{lib_key}/containers/'  # Create a new container in this library
URL_LIB_CONTAINER_PUBLISH = URL_LIB_CONTAINER + 'publish/'  # Publish changes to the specified container + children


def _get_upstream_link_good_and_syncable(downstream):
    return UpstreamLink(
        upstream_ref=downstream.upstream,
        upstream_key=LibraryUsageLocatorV2.from_string(downstream.upstream),
        downstream_key=str(downstream.usage_key),
        version_synced=downstream.upstream_version,
        version_available=(downstream.upstream_version or 0) + 1,
        version_declined=downstream.upstream_version_declined,
        error_message=None,
        downstream_customized=[],
        top_level_parent_key=None,
        upstream_name=downstream.upstream_display_name,
    )


def _get_upstream_link_bad(_downstream):
    raise BadUpstream(MOCK_UPSTREAM_ERROR)


class _BaseDownstreamViewTestMixin:
    """
    Shared data and error test cases.
    """
    def setUp(self):
        """
        Create a simple course with one unit and two videos, one of which is linked to an "upstream".
        """
        # pylint: disable=too-many-statements
        super().setUp()
        self.now = datetime.now(timezone.utc)
        freezer = freeze_time(self.now)
        self.addCleanup(freezer.stop)
        freezer.start()
        self.maxDiff = 2000

        self.organization, _ = Organization.objects.get_or_create(
            short_name="CL-TEST",
            defaults={"name": "Content Libraries Tachyon Exploration & Survey Team"},
        )
        self.superuser = UserFactory(username="superuser", password="password", is_staff=True, is_superuser=True)
        self.simple_user = UserFactory(username="simple_user", password="password")
        self.course_user = UserFactory(username="course_user", password="password")
        self.lib_user = UserFactory(username="lib_user", password="password")
        self.client.login(username=self.superuser.username, password="password")

        self.library_title = "Test Library 1"
        self.library_id = self._create_library(
            slug="testlib1_preview",
            title=self.library_title,
            description="Testing XBlocks"
        )["id"]
        self.library_key = LibraryLocatorV2.from_string(self.library_id)
        lib_api.set_library_user_permissions(self.library_key, self.lib_user, access_level="read")
        self.html_lib_id = self._add_block_to_library(self.library_id, "html", "html-baz")["id"]
        self.video_lib_id = self._add_block_to_library(self.library_id, "video", "video-baz")["id"]
        self.unit_id = self._create_container(self.library_id, "unit", "unit-1", "Unit 1")["id"]
        self.subsection_id = self._create_container(self.library_id, "subsection", "subsection-1", "Subsection 1")["id"]
        self.section_id = self._create_container(self.library_id, "section", "section-1", "Section 1")["id"]

        # Creating container to test the top-level parent
        self.top_level_unit_id = self._create_container(self.library_id, "unit", "unit-2", "Unit 2")["id"]
        self.top_level_unit_id_2 = self._create_container(self.library_id, "unit", "unit-3", "Unit 3")["id"]
        self.top_level_subsection_id = self._create_container(
            self.library_id,
            "subsection",
            "subsection-2",
            "Subsection 2",
        )["id"]
        self.top_level_section_id = self._create_container(self.library_id, "section", "section-2", "Section 2")["id"]
        self.html_lib_id_2 = self._add_block_to_library(self.library_id, "html", "html-baz-2")["id"]
        self.video_lib_id_2 = self._add_block_to_library(self.library_id, "video", "video-baz-2")["id"]

        self._publish_library_block(self.html_lib_id)
        self._publish_library_block(self.video_lib_id)
        self._publish_library_block(self.html_lib_id_2)
        self._publish_library_block(self.video_lib_id_2)
        self._publish_container(self.unit_id)
        self._publish_container(self.subsection_id)
        self._publish_container(self.section_id)
        self._publish_container(self.top_level_unit_id)
        self._publish_container(self.top_level_unit_id_2)
        self._publish_container(self.top_level_subsection_id)
        self._publish_container(self.top_level_section_id)
        self.mock_upstream_link = f"{settings.COURSE_AUTHORING_MICROFRONTEND_URL}/library/{self.library_id}/components?usageKey={self.video_lib_id}"  # pylint: disable=line-too-long  # noqa: E501
        self.course = CourseFactory.create()
        add_users(self.superuser, CourseStaffRole(self.course.id), self.course_user)
        chapter = BlockFactory.create(category='chapter', parent=self.course)
        sequential = BlockFactory.create(category='sequential', parent=chapter)
        unit = BlockFactory.create(category='vertical', parent=sequential)
        self.regular_video_key = BlockFactory.create(category='video', parent=unit).usage_key
        self.downstream_video_key = BlockFactory.create(
            category='video', parent=unit, upstream=self.video_lib_id, upstream_version=1,
        ).usage_key
        self.downstream_html_key = BlockFactory.create(
            category='html', parent=unit, upstream=self.html_lib_id, upstream_version=1,
        ).usage_key
        self.downstream_chapter_key = BlockFactory.create(
            category='chapter', parent=self.course, upstream=self.section_id, upstream_version=1,
        ).usage_key
        self.downstream_sequential_key = BlockFactory.create(
            category='sequential', parent=chapter, upstream=self.subsection_id, upstream_version=1,
        ).usage_key
        self.downstream_unit_key = BlockFactory.create(
            category='vertical', parent=sequential, upstream=self.unit_id, upstream_version=1,
        ).usage_key

        # Creating Blocks with top-level-parents
        # Unit created as a top-level parent
        self.top_level_downstream_unit = BlockFactory.create(
            category='vertical',
            parent=sequential,
            upstream=self.top_level_unit_id,
            upstream_version=1,
        )
        self.top_level_downstream_html_key = BlockFactory.create(
            category='html',
            parent=self.top_level_downstream_unit,
            upstream=self.html_lib_id_2,
            upstream_version=1,
            top_level_downstream_parent_key=get_block_key_string(
                self.top_level_downstream_unit.usage_key,
            )
        ).usage_key

        # Section created as a top-level parent
        self.top_level_downstream_chapter = BlockFactory.create(
            category='chapter', parent=self.course, upstream=self.top_level_section_id, upstream_version=1,
        )
        self.top_level_downstream_sequential = BlockFactory.create(
            category='sequential',
            parent=self.top_level_downstream_chapter,
            upstream=self.top_level_subsection_id,
            upstream_version=1,
            top_level_downstream_parent_key=get_block_key_string(
                self.top_level_downstream_chapter.usage_key,
            ),
        )
        self.top_level_downstream_unit_2 = BlockFactory.create(
            category='vertical',
            parent=self.top_level_downstream_sequential,
            upstream=self.top_level_unit_id_2,
            upstream_version=1,
            top_level_downstream_parent_key=get_block_key_string(
                self.top_level_downstream_chapter.usage_key,
            ),
        )
        self.top_level_downstream_video_key = BlockFactory.create(
            category='video',
            parent=self.top_level_downstream_unit_2,
            upstream=self.video_lib_id_2,
            upstream_version=1,
            top_level_downstream_parent_key=get_block_key_string(
                self.top_level_downstream_chapter.usage_key,
            )
        ).usage_key

        self.another_course = CourseFactory.create(display_name="Another Course")
        another_chapter = BlockFactory.create(category="chapter", parent=self.another_course)
        another_sequential = BlockFactory.create(category="sequential", parent=another_chapter)
        another_unit = BlockFactory.create(category="vertical", parent=another_sequential)
        self.another_video_keys = []
        for _ in range(3):
            # Adds 3 videos linked to the same upstream
            self.another_video_keys.append(
                BlockFactory.create(
                    category="video",
                    parent=another_unit,
                    upstream=self.video_lib_id,
                    upstream_version=1
                ).usage_key
            )

        self.fake_video_key = self.course.id.make_usage_key("video", "NoSuchVideo")
        self.learner = UserFactory(username="learner", password="password")
        self._update_container(self.unit_id, display_name="Unit 2")
        self._publish_container(self.unit_id)
        self._set_library_block_olx(self.html_lib_id, "<html><b>Hello world!</b></html>")
        self._publish_library_block(self.html_lib_id)
        self._publish_library_block(self.video_lib_id)
        self._publish_library_block(self.html_lib_id)

    def _api(self, method, url, data, expect_response):
        """
        Call a REST API
        """
        response = getattr(self.client, method)(url, data, format="json", content_type="application/json")
        assert response.status_code == expect_response,\
            'Unexpected response code {}:\n{}'.format(response.status_code, getattr(response, 'data', '(no data)'))
        return response.data

    def _create_library(
        self, slug, title, description="", org=None,
        license_type='', expect_response=200,
    ):
        """ Create a library """
        if org is None:
            org = self.organization.short_name
        return self._api('post', URL_LIB_CREATE, {
            "org": org,
            "slug": slug,
            "title": title,
            "description": description,
            "license": license_type,
        }, expect_response)

    def _add_block_to_library(self, lib_key, block_type, slug, parent_block=None, expect_response=200):
        """ Add a new XBlock to the library """
        data = {"block_type": block_type, "definition_id": slug}
        if parent_block:
            data["parent_block"] = parent_block
        return self._api('post', URL_LIB_BLOCKS.format(lib_key=lib_key), data, expect_response)

    def _publish_library_block(self, block_key, expect_response=200):
        """ Publish changes from a specified XBlock """
        return self._api('post', URL_LIB_BLOCK_PUBLISH.format(block_key=block_key), None, expect_response)

    def _publish_container(self, container_key: ContainerKey | str, expect_response=200):
        """ Publish all changes in the specified container + children """
        return self._api('post', URL_LIB_CONTAINER_PUBLISH.format(container_key=container_key), None, expect_response)

    def _update_container(self, container_key: ContainerKey | str, display_name: str, expect_response=200):
        """ Update a container (unit etc.) """
        data = {"display_name": display_name}
        return self._api('patch', URL_LIB_CONTAINER.format(container_key=container_key), data, expect_response)

    def _set_library_block_olx(self, block_key, new_olx, expect_response=200):
        """ Overwrite the OLX of a specific block in the library """
        return self._api('post', URL_LIB_BLOCK_OLX.format(block_key=block_key), {"olx": new_olx}, expect_response)

    def call_api(self, usage_key_string):
        raise NotImplementedError

    def _create_container(self, lib_key, container_type, slug: str | None, display_name: str, expect_response=200):
        """ Create a container (unit etc.) """
        data = {"container_type": container_type, "display_name": display_name}
        if slug:
            data["slug"] = slug
        return self._api('post', URL_LIB_CONTAINERS.format(lib_key=lib_key), data, expect_response)

    def _delete_component(self, block_key, expect_response=200):
        """ Publish all changes in the specified container + children """
        return self._api('delete', URL_LIB_BLOCK.format(block_key=block_key), None, expect_response)


class SharedErrorTestCases(_BaseDownstreamViewTestMixin):
    """
    Shared error test cases.
    """
    def test_404_downstream_not_found(self):
        """
        Do we raise 404 if the specified downstream block could not be loaded?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.fake_video_key)
        assert response.status_code == 404
        assert "not found" in response.data["developer_message"]

    def test_404_downstream_not_accessible(self):
        """
        Do we raise 404 if the user lacks read access on the specified downstream block?
        """
        self.client.login(username="learner", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 404
        assert "not found" in response.data["developer_message"]


class GetComponentDownstreamViewTest(SharedErrorTestCases, SharedModuleStoreTestCase):
    """
    Test that `GET /api/v2/contentstore/downstreams/...` inspects a downstream's link to an upstream.
    """
    def call_api(self, usage_key_string):
        return self.client.get(f"/api/contentstore/v2/downstreams/{usage_key_string}")

    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    def test_200_good_upstream(self):
        """
        Does the happy path work?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 200
        assert response.data['upstream_ref'] == self.video_lib_id
        assert response.data['error_message'] is None
        assert response.data['ready_to_sync'] is True
        assert response.data['upstream_link'] == self.mock_upstream_link

    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_bad)
    def test_200_bad_upstream(self):
        """
        If the upstream link is broken, do we still return 200, but with an error message in body?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 200
        assert response.data['upstream_ref'] == self.video_lib_id
        assert response.data['error_message'] == MOCK_UPSTREAM_ERROR
        assert response.data['ready_to_sync'] is False
        assert response.data['upstream_link'] is None

    def test_200_no_upstream(self):
        """
        If the upstream link is missing, do we still return 200, but with an error message in body?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.regular_video_key)
        assert response.status_code == 200
        assert response.data['upstream_ref'] is None
        assert "is not linked" in response.data['error_message']
        assert response.data['ready_to_sync'] is False
        assert response.data['upstream_link'] is None


class PutDownstreamViewTest(SharedErrorTestCases, SharedModuleStoreTestCase):
    """
    Test that `PUT /api/v2/contentstore/downstreams/...` edits a downstream's link to an upstream.
    """
    def call_api(self, usage_key_string, sync: str | None = None):
        return self.client.put(
            f"/api/contentstore/v2/downstreams/{usage_key_string}",
            data=json.dumps({
                "upstream_ref": str(self.video_lib_id),
                **({"sync": sync} if sync else {}),
            }),
            content_type="application/json",
        )

    @patch.object(downstreams_views, "fetch_customizable_fields_from_block")
    @patch.object(downstreams_views, "sync_library_content")
    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    def test_200_with_sync(self, mock_sync, mock_fetch):
        """
        Does the happy path work (with sync=True)?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(str(self.regular_video_key), sync='true')
        assert response.status_code == 200
        video_after = modulestore().get_item(self.regular_video_key)
        assert mock_sync.call_count == 1
        assert mock_fetch.call_count == 0
        assert video_after.upstream == self.video_lib_id

    @patch.object(downstreams_views, "fetch_customizable_fields_from_block")
    @patch.object(downstreams_views, "sync_library_content")
    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    def test_200_no_sync(self, mock_sync, mock_fetch):
        """
        Does the happy path work (with sync=False)?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.regular_video_key, sync='false')
        assert response.status_code == 200
        video_after = modulestore().get_item(self.regular_video_key)
        assert mock_sync.call_count == 0
        assert mock_fetch.call_count == 1
        assert video_after.upstream == self.video_lib_id

    @patch.object(
        downstreams_views, "fetch_customizable_fields_from_block", side_effect=BadUpstream(MOCK_UPSTREAM_ERROR),
    )
    def test_400(self, sync: str):
        """
        Do we raise a 400 if the provided upstream reference is malformed or not accessible?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 400
        assert MOCK_UPSTREAM_ERROR in response.data['developer_message']['upstream_ref']
        video_after = modulestore().get_item(self.regular_video_key)
        assert video_after.upstream is None


class DeleteDownstreamViewTest(SharedErrorTestCases, ImmediateOnCommitMixin, SharedModuleStoreTestCase):
    """
    Test that `DELETE /api/v2/contentstore/downstreams/...` severs a downstream's link to an upstream.
    """
    def call_api(self, usage_key_string):
        return self.client.delete(f"/api/contentstore/v2/downstreams/{usage_key_string}")

    @patch.object(downstreams_views, "sever_upstream_link")
    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    def test_204(self, mock_sever):
        """
        Does the happy path work?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 204
        assert mock_sever.call_count == 1

    @patch.object(downstreams_views, "sever_upstream_link")
    def test_204_no_upstream(self, mock_sever):
        """
        If there's no upsream, do we still happily return 204?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.regular_video_key)
        assert response.status_code == 204
        assert mock_sever.call_count == 1

    def test_unlink_parent_should_update_children_top_level_parent(self):
        """
        If we unlink a parent block, do all children get the new top-level parent?
        """
        self.client.login(username="superuser", password="password")

        all_downstreams = self.client.get(
            "/api/contentstore/v2/downstreams/",
            data={"course_id": str(self.course.id)},
        )
        assert all_downstreams.data["count"] == 11

        response = self.call_api(self.top_level_downstream_chapter.usage_key)
        assert response.status_code == 204

        # Check that all children have their top_level_downstream_parent_key updated
        subsection = modulestore().get_item(self.top_level_downstream_sequential.usage_key)
        assert subsection.top_level_downstream_parent_key is None

        unit = modulestore().get_item(self.top_level_downstream_unit_2.usage_key)
        # The sequential is the top-level parent for the unit
        sequential_block_key = get_block_key_string(
            self.top_level_downstream_sequential.usage_key
        )
        assert unit.top_level_downstream_parent_key == sequential_block_key

        video = modulestore().get_item(self.top_level_downstream_video_key)
        # The sequential is the top-level parent for the video
        assert video.top_level_downstream_parent_key == sequential_block_key

        all_downstreams = self.client.get(
            "/api/contentstore/v2/downstreams/",
            data={"course_id": str(self.course.id)},
        )
        assert all_downstreams.data["count"] == 10


class _DownstreamSyncViewTestMixin(SharedErrorTestCases):
    """
    Shared tests between the /api/contentstore/v2/downstreams/.../sync endpoints.
    """

    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_bad)
    def test_400_bad_upstream(self):
        """
        If the upstream link is bad, do we get a 400?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 400
        assert MOCK_UPSTREAM_ERROR in response.data["developer_message"][0]

    def test_400_no_upstream(self):
        """
        If the upstream link is missing, do we get a 400?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.regular_video_key)
        assert response.status_code == 400
        assert "is not linked" in response.data["developer_message"][0]


class CreateDownstreamViewTest(CourseTestCase, _BaseDownstreamViewTestMixin, SharedModuleStoreTestCase):
    """
    Tests create new downstream blocks
    """
    def call_api_post(self, library_content_key, category):
        """
        Call the api to create a downstream block using
        `library_content_key` as upstream
        """
        data = {
            "parent_locator": str(self.course.location),
            "display_name": "Test block",
            "library_content_key": library_content_key,
            "category": category,
        }
        return self.client.post(
            reverse("xblock_handler"),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_200(self):
        response = self.call_api_post(self.html_lib_id, "html")

        assert response.status_code == 200
        data = response.json()
        assert data["upstreamRef"] == self.html_lib_id

        usage_key = UsageKey.from_string(data["locator"])
        item = modulestore().get_item(usage_key)
        assert item.upstream == self.html_lib_id

    @patch("cms.djangoapps.contentstore.helpers._insert_static_files_into_downstream_xblock")
    @patch("cms.djangoapps.contentstore.helpers.content_staging_api.stage_xblock_temporarily")
    @patch("cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.sync_from_upstream_block")
    def test_200_video(self, mock_sync, mock_stage, mock_insert):
        mock_lib_block = MagicMock()
        mock_lib_block.runtime.get_block_assets.return_value = ['mocked_asset']
        mock_sync.return_value = mock_lib_block
        mock_stage.return_value = MagicMock()
        mock_insert.return_value = StaticFileNotices()

        response = self.call_api_post(self.video_lib_id, "video")

        assert response.status_code == 200
        data = response.json()
        assert data["upstreamRef"] == self.video_lib_id

        usage_key = UsageKey.from_string(data["locator"])
        item = modulestore().get_item(usage_key)
        assert item.upstream == self.video_lib_id
        assert item.edx_video_id is not None


class PostDownstreamSyncViewTest(_DownstreamSyncViewTestMixin, SharedModuleStoreTestCase):
    """
    Test that `POST /api/v2/contentstore/downstreams/.../sync` initiates a sync from the linked upstream.
    """
    def call_api(self, usage_key_string):
        return self.client.post(f"/api/contentstore/v2/downstreams/{usage_key_string}/sync")

    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    @patch.object(xblock_view_handlers, "import_static_assets_for_library_sync", return_value=StaticFileNotices())
    @patch.object(downstreams_views, "clear_transcripts")
    def test_200(self, mock_import_staged_content, mock_clear_transcripts):
        """
        Does the happy path work?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 200
        assert mock_import_staged_content.call_count == 1
        assert mock_clear_transcripts.call_count == 1


class DeleteDownstreamSyncViewtest(
    _DownstreamSyncViewTestMixin,
    SharedModuleStoreTestCase,
):
    """
    Test that `DELETE /api/v2/contentstore/downstreams/.../sync` declines a sync from the linked upstream.
    """
    def call_api(self, usage_key_string):
        return self.client.delete(f"/api/contentstore/v2/downstreams/{usage_key_string}/sync")

    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    @patch.object(downstreams_views, "decline_sync")
    def test_204(self, mock_decline_sync):
        """
        Does the happy path work?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(self.downstream_video_key)
        assert response.status_code == 204
        assert mock_decline_sync.call_count == 1


@ddt.ddt
class GetUpstreamViewTest(
    _BaseDownstreamViewTestMixin,
    ImmediateOnCommitMixin,
    SharedModuleStoreTestCase,
):
    """
    Test that `GET /api/v2/contentstore/downstreams?...` returns list of links based on the provided filter.
    """

    def call_api(
        self,
        course_id: str | None = None,
        ready_to_sync: bool | None = None,
        upstream_key: str | None = None,
        item_type: str | None = None,
        use_top_level_parents: bool | None = None,
    ):
        data = {}
        if course_id is not None:
            data["course_id"] = str(course_id)
        if ready_to_sync is not None:
            data["ready_to_sync"] = str(ready_to_sync)
        if upstream_key is not None:
            data["upstream_key"] = str(upstream_key)
        if item_type is not None:
            data["item_type"] = str(item_type)
        if use_top_level_parents is not None:
            data["use_top_level_parents"] = str(use_top_level_parents)
        return self.client.get("/api/contentstore/v2/downstreams/", data=data)

    def test_200_single_upstream_container(self):
        """
        Test single upstream container link provides children info as well.
        """
        self.client.login(username="superuser", password="password")
        # Publish components
        self._set_library_block_olx(self.html_lib_id_2, "<html><b>Hello world!</b></html>")
        self._publish_library_block(self.html_lib_id_2)

        response = self.client.get(f"/api/contentstore/v2/downstreams/{self.top_level_downstream_unit.usage_key}")
        assert response.status_code == 200
        data = response.json()
        assert data['upstream_ref'] == self.top_level_unit_id
        assert data['error_message'] is None
        assert data['ready_to_sync'] is True
        assert len(data['ready_to_sync_children']) == 1
        html_block = modulestore().get_item(self.top_level_downstream_html_key)
        self.assertDictEqual(data['ready_to_sync_children'][0], {
            'name': html_block.display_name,
            'upstream': str(self.html_lib_id_2),
            'block_type': 'html',
            'downstream_customized': [],
            'id': str(html_block.usage_key),
        })

    def test_200_all_downstreams_for_a_course(self):
        """
        Returns all links for given course
        """
        self.client.login(username="course_user", password="password")
        response = self.call_api(course_id=self.course.id)
        assert response.status_code == 200
        data = response.json()
        date_format = self.now.isoformat().split("+")[0] + 'Z'
        expected = [
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_video_key),
                'id': 1,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.video_lib_id,
                'upstream_type': 'component',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_html_key),
                'id': 2,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id,
                'upstream_type': 'component',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_html_key),
                'id': 3,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id_2,
                'upstream_type': 'component',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_unit.usage_key),
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_video_key),
                'id': 4,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.video_lib_id_2,
                'upstream_type': 'component',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_chapter_key),
                'id': 1,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.section_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_sequential_key),
                'id': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.subsection_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_unit_key),
                'id': 3,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.unit_id,
                'upstream_type': 'container',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_unit.usage_key),
                'id': 4,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_unit_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'id': 5,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_section_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_sequential.usage_key),
                'id': 6,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_subsection_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_unit_2.usage_key),
                'id': 7,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_unit_id_2,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'downstream_customized': [],
            },
        ]
        self.assertListEqual(data["results"], expected)
        self.assertEqual(data["count"], 11)

    def test_permission_denied_with_course_filter(self):
        self.client.login(username="simple_user", password="password")
        response = self.call_api(course_id=self.course.id)
        assert response.status_code == 403

    def test_200_component_downstreams_for_a_course(self):
        """
        Returns all component links for given course
        """
        self.client.login(username="course_user", password="password")
        response = self.call_api(
            course_id=self.course.id,
            item_type='components',
        )
        assert response.status_code == 200
        data = response.json()
        date_format = self.now.isoformat().split("+")[0] + 'Z'
        expected = [
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_video_key),
                'id': 1,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.video_lib_id,
                'upstream_type': 'component',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_html_key),
                'id': 2,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id,
                'upstream_type': 'component',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_html_key),
                'id': 3,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id_2,
                'upstream_type': 'component',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_unit.usage_key),
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_video_key),
                'id': 4,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.video_lib_id_2,
                'upstream_type': 'component',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'downstream_customized': [],
            },
        ]
        self.assertListEqual(data["results"], expected)
        self.assertEqual(data["count"], 4)

    def test_200_container_downstreams_for_a_course(self):
        """
        Returns all container links for given course
        """
        self.client.login(username="course_user", password="password")
        response = self.call_api(
            course_id=self.course.id,
            item_type='containers',
        )
        assert response.status_code == 200
        data = response.json()
        date_format = self.now.isoformat().split("+")[0] + 'Z'
        expected = [
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_chapter_key),
                'id': 1,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.section_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_sequential_key),
                'id': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.subsection_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_unit_key),
                'id': 3,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.unit_id,
                'upstream_type': 'container',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_unit.usage_key),
                'id': 4,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_unit_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'id': 5,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_section_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_sequential.usage_key),
                'id': 6,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_subsection_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_unit_2.usage_key),
                'id': 7,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_unit_id_2,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'downstream_customized': [],
            },
        ]
        self.assertListEqual(data["results"], expected)
        self.assertEqual(data["count"], 7)

    @ddt.data(
        ('all', 2),
        ('components', 1),
        ('containers', 1),
    )
    @ddt.unpack
    def test_200_downstreams_ready_to_sync(self, item_type, expected_count):
        """
        Returns all links that are syncable
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(
            ready_to_sync=True,
            item_type=item_type,
        )
        assert response.status_code == 200
        data = response.json()
        self.assertTrue(all(o["ready_to_sync"] for o in data["results"]))
        self.assertEqual(data["count"], expected_count)

    def test_permission_denied_without_filter(self):
        self.client.login(username="simple_user", password="password")
        response = self.call_api()
        assert response.status_code == 403

    def test_200_component_downstream_context_list(self):
        """
        Returns all entity downstream links for given component
        """
        self.client.login(username="lib_user", password="password")
        response = self.call_api(upstream_key=self.video_lib_id)
        assert response.status_code == 200
        data = response.json()
        expected = [str(self.downstream_video_key)] + [str(key) for key in self.another_video_keys]
        got = [str(o["downstream_usage_key"]) for o in data["results"]]
        self.assertListEqual(got, expected)
        self.assertEqual(data["count"], 4)

    def test_200_container_downstream_context_list(self):
        """
        Returns all entity downstream links for given container
        """
        self.client.login(username="lib_user", password="password")
        response = self.call_api(upstream_key=self.unit_id)
        assert response.status_code == 200
        data = response.json()
        expected = [str(self.downstream_unit_key)]
        got = [str(o["downstream_usage_key"]) for o in data["results"]]
        self.assertListEqual(got, expected)
        self.assertEqual(data["count"], 1)

    def test_200_get_ready_to_sync_top_level_parents_with_components(self):
        """
        Returns all links that are syncable using the top-level parents of components
        """
        self.client.login(username="superuser", password="password")

        # Publish components
        self._set_library_block_olx(self.html_lib_id_2, "<html><b>Hello world!</b></html>")
        self._publish_library_block(self.html_lib_id_2)
        self._set_library_block_olx(self.video_lib_id_2, "<video><b>Hello world!</b></video>")
        self._publish_library_block(self.video_lib_id_2)

        response = self.call_api(
            ready_to_sync=True,
            item_type="all",
            use_top_level_parents=True,
        )
        assert response.status_code == 200
        data = response.json()
        self.assertEqual(data["count"], 4)
        date_format = self.now.isoformat().split("+")[0] + 'Z'

        # The expected results are
        # * The section that is the top-level parent of `video_lib_id_2`
        # * The unit that is the top-level parent of `html_lib_id_2`
        # * 2 links without top-level parents
        expected = [
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_unit.usage_key),
                'id': 4,
                'ready_to_sync': False,  # <-- It's False because the container doesn't have changes
                'ready_to_sync_from_children': True,  # <-- It's True because a child has changes
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_unit_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'id': 5,
                'ready_to_sync': False,  # <-- It's False because the container doesn't have changes
                'ready_to_sync_from_children': True,  # <-- It's True because a child has changes
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_section_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_html_key),
                'id': 2,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id,
                'upstream_type': 'component',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_unit_key),
                'id': 3,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.unit_id,
                'upstream_type': 'container',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
        ]
        self.assertListEqual(data["results"], expected)

    def test_200_get_ready_to_sync_top_level_parents_with_containers(self):
        """
        Returns all links that are syncable using the top-level parents of containers
        """
        self.client.login(username="superuser", password="password")

        # Publish Subsection
        self._update_container(self.top_level_subsection_id, display_name="Subsection 3")
        self._publish_container(self.top_level_subsection_id)

        response = self.call_api(
            ready_to_sync=True,
            item_type="all",
            use_top_level_parents=True,
        )
        assert response.status_code == 200
        data = response.json()
        self.assertEqual(data["count"], 3)
        date_format = self.now.isoformat().split("+")[0] + 'Z'

        # The expected results are
        # * 2 links without top-level parents
        # * The section that is the top-level parent of `top_level_subsection_id`
        expected = [
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_html_key),
                'id': 2,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id,
                'upstream_type': 'component',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_unit_key),
                'id': 3,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.unit_id,
                'upstream_type': 'container',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'id': 5,
                'ready_to_sync': False,  # <-- It's False because the container doesn't have changes
                'ready_to_sync_from_children': True,  # <-- It's True because a child has changes
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_section_id,
                'upstream_type': 'container',
                'upstream_version': 1,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
        ]
        self.assertListEqual(data["results"], expected)

    def test_200_get_ready_to_sync_duplicated_top_level_parents(self):
        """
        Returns all links that are syncable using the same top-level parents

        According to the requirements, only the top-level parents should be displayed.
        Even if all containers and components within a section are updated, only the top-level parent,
        which is the section, should be displayed.
        This test checks that only the top-level parent is displayed and is not duplicated in the result.
        """
        self.client.login(username="superuser", password="password")

        # Publish Section and component/subsection that has the same section as top-level parent
        self._update_container(self.top_level_section_id, display_name="Section 3")
        self._publish_container(self.top_level_section_id)
        self._set_library_block_olx(self.video_lib_id_2, "<video><b>Hello world!</b></video>")
        self._publish_library_block(self.video_lib_id_2)
        self._update_container(self.top_level_subsection_id, display_name="Subsection 3")
        self._publish_container(self.top_level_subsection_id)

        response = self.call_api(
            ready_to_sync=True,
            item_type="all",
            use_top_level_parents=True,
        )
        assert response.status_code == 200
        data = response.json()
        self.assertEqual(data["count"], 3)
        date_format = self.now.isoformat().split("+")[0] + 'Z'

        # The expected results are
        # * The section that is the top-level parent of `video_lib_id_2` and `top_level_subsection_id`
        # * 2 links without top-level parents
        expected = [
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.top_level_downstream_chapter.usage_key),
                'id': 5,
                'ready_to_sync': True,  # <-- It's True because the section has changes
                'ready_to_sync_from_children': True,  # <-- It's True because a child has changes
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.top_level_section_id,
                'upstream_type': 'container',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_html_key),
                'id': 2,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.html_lib_id,
                'upstream_type': 'component',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
            {
                'created': date_format,
                'downstream_context_key': str(self.course.id),
                'downstream_usage_key': str(self.downstream_unit_key),
                'id': 3,
                'ready_to_sync': True,
                'ready_to_sync_from_children': False,
                'updated': date_format,
                'upstream_context_key': self.library_id,
                'upstream_context_title': self.library_title,
                'upstream_key': self.unit_id,
                'upstream_type': 'container',
                'upstream_version': 2,
                'version_declined': None,
                'version_synced': 1,
                'top_level_parent_usage_key': None,
                'downstream_customized': [],
            },
        ]
        self.assertListEqual(data["results"], expected)


class GetDownstreamSummaryViewTest(
    _BaseDownstreamViewTestMixin,
    ImmediateOnCommitMixin,
    SharedModuleStoreTestCase,
):
    """
    Test that `GET /api/v2/contentstore/downstreams/<course_id>/summary` returns summary of links in course.
    """
    def call_api(self, course_id):
        return self.client.get(f"/api/contentstore/v2/downstreams/{course_id}/summary")

    @patch.object(UpstreamLink, "get_for_block", _get_upstream_link_good_and_syncable)
    def test_200_summary(self):
        """
        Does the happy path work?
        """
        self.client.login(username="superuser", password="password")
        response = self.call_api(str(self.another_course.id))
        assert response.status_code == 200
        data = response.json()
        expected = [{
            'upstream_context_title': 'Test Library 1',
            'upstream_context_key': self.library_id,
            'ready_to_sync_count': 0,
            'total_count': 3,
            'last_published_at': self.now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        }]
        self.assertListEqual(data, expected)
        response = self.call_api(str(self.course.id))
        assert response.status_code == 200
        data = response.json()

        # The `total_count` is 7 because the top-level logic:
        # * The `section-2`, that is the top-level parent of `subsection-2`, `unit-3`, `html-baz-2`
        # * The `unit-2`, that is the top-level parent of `video-baz-2`
        # * The `section-1`
        # * The `subsection-1`
        # * The `unit-1`
        # * The `html-baz-1`
        # * The `video-baz-1`
        expected = [{
            'upstream_context_title': 'Test Library 1',
            'upstream_context_key': self.library_id,
            'ready_to_sync_count': 2,
            'total_count': 7,
            'last_published_at': self.now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        }]
        self.assertListEqual(data, expected)

        # Publish Subsection
        self._update_container(self.top_level_subsection_id, display_name="Subsection 3")
        self._publish_container(self.top_level_subsection_id)

        response = self.call_api(str(self.course.id))
        assert response.status_code == 200
        data = response.json()
        expected = [{
            'upstream_context_title': 'Test Library 1',
            'upstream_context_key': self.library_id,
            'ready_to_sync_count': 3,  # <-- + the section (top-level parent of subsection)
            'total_count': 7,
            'last_published_at': self.now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        }]
        self.assertListEqual(data, expected)

        # Publish Section
        self._update_container(self.top_level_section_id, display_name="Section 3")
        self._publish_container(self.top_level_section_id)

        response = self.call_api(str(self.course.id))
        assert response.status_code == 200
        data = response.json()
        expected = [{
            'upstream_context_title': 'Test Library 1',
            'upstream_context_key': self.library_id,
            'ready_to_sync_count': 3,  # <-- is the same value because the section is the top-level parent
            'total_count': 7,
            'last_published_at': self.now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        }]
        self.assertListEqual(data, expected)


class GetDownstreamDeletedUpstream(
    _BaseDownstreamViewTestMixin,
    ImmediateOnCommitMixin,
    SharedModuleStoreTestCase,
):
    """
    Test that parent container is marked ready_to_sync when even when the only change is a deleted component under it
    """
    def call_api(
        self,
        course_id: str | None = None,
        ready_to_sync: bool | None = None,
        upstream_key: str | None = None,
        item_type: str | None = None,
        use_top_level_parents: bool | None = None,
    ):
        data = {}
        if course_id is not None:
            data["course_id"] = str(course_id)
        if ready_to_sync is not None:
            data["ready_to_sync"] = str(ready_to_sync)
        if upstream_key is not None:
            data["upstream_key"] = str(upstream_key)
        if item_type is not None:
            data["item_type"] = str(item_type)
        if use_top_level_parents is not None:
            data["use_top_level_parents"] = str(use_top_level_parents)
        return self.client.get("/api/contentstore/v2/downstreams/", data=data)

    def test_delete_component_should_be_ready_to_sync(self):
        """
        Test deleting a component from library should mark the entire section container ready to sync
        """
        # Create blocks
        section_id = self._create_container(self.library_id, "section", "section-12", "Section 12")["id"]
        subsection_id = self._create_container(self.library_id, "subsection", "subsection-12", "Subsection 12")["id"]
        unit_id = self._create_container(self.library_id, "unit", "unit-12", "Unit 12")["id"]
        video_id = self._add_block_to_library(self.library_id, "video", "video-bar-13")["id"]
        section_key = ContainerKey.from_string(section_id)
        subsection_key = ContainerKey.from_string(subsection_id)
        unit_key = ContainerKey.from_string(unit_id)
        video_key = LibraryUsageLocatorV2.from_string(video_id)

        # Set children
        lib_api.update_container_children(section_key, [subsection_key], None)
        lib_api.update_container_children(subsection_key, [unit_key], None)
        lib_api.update_container_children(unit_key, [video_key], None)
        self._publish_container(unit_id)
        self._publish_container(subsection_id)
        self._publish_container(section_id)
        self._publish_library_block(video_id)
        course = CourseFactory.create(display_name="Course New")
        add_users(self.superuser, CourseStaffRole(course.id), self.course_user)
        chapter = BlockFactory.create(
            category='chapter', parent=course, upstream=section_id, upstream_version=2,
        )
        sequential = BlockFactory.create(
            category='sequential',
            parent=chapter,
            upstream=subsection_id,
            upstream_version=2,
            top_level_downstream_parent_key=get_block_key_string(chapter.usage_key),
        )
        vertical = BlockFactory.create(
            category='vertical',
            parent=sequential,
            upstream=unit_id,
            upstream_version=2,
            top_level_downstream_parent_key=get_block_key_string(chapter.usage_key),
        )
        BlockFactory.create(
            category='video',
            parent=vertical,
            upstream=video_id,
            upstream_version=1,
            top_level_downstream_parent_key=get_block_key_string(chapter.usage_key),
        )
        self._delete_component(video_id)
        self._publish_container(unit_id)
        response = self.call_api(course_id=course.id, ready_to_sync=True, use_top_level_parents=True)
        assert response.status_code == 200
        data = response.json()['results']
        assert len(data) == 1
        date_format = self.now.isoformat().split("+")[0] + 'Z'
        expected_results = {
            'created': date_format,
            'downstream_context_key': str(course.id),
            'downstream_usage_key': str(chapter.usage_key),
            'downstream_customized': [],
            'id': 8,
            'ready_to_sync': False,
            'ready_to_sync_from_children': True,
            'top_level_parent_usage_key': None,
            'updated': date_format,
            'upstream_context_key': self.library_id,
            'upstream_context_title': self.library_title,
            'upstream_key': section_id,
            'upstream_type': 'container',
            'upstream_version': 2,
            'version_declined': None,
            'version_synced': 2,
        }

        self.assertDictEqual(data[0], expected_results)
