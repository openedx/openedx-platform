"""
Tests for static asset files in openedx_content-based Content Libraries
"""
from uuid import UUID

from opaque_keys.edx.keys import UsageKey

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.tests.base import (
    ContentLibrariesRestApiTest,
)
from openedx.core.djangoapps.xblock.api import get_component_from_usage_key
from openedx.core.djangolib.testing.utils import skip_unless_cms

# Binary data representing an SVG image file
SVG_DATA = """<svg xmlns="http://www.w3.org/2000/svg" height="30" width="100">
  <text x="0" y="15" fill="red">SVG is 🔥</text>
</svg>""".encode()

# part of an .srt transcript file
TRANSCRIPT_DATA = b"""1
00:00:00,260 --> 00:00:01,510
Welcome to edX.

2
00:00:01,510 --> 00:00:04,480
I'm Anant Agarwal, I'm the president of edX,
"""


@skip_unless_cms
class ContentLibrariesStaticAssetsTest(ContentLibrariesRestApiTest):
    """
    Tests for static asset files in openedx_content-based Content Libraries
    """

    def test_asset_filenames(self):
        """
        Test various allowed and disallowed filenames
        """
        library = self._create_library(slug="asset-lib2", title="Static Assets Test Library")
        block = self._add_block_to_library(library["id"], "html", "html1")
        block_id = block["id"]
        file_size = len(SVG_DATA)

        # Unicode names are allowed
        file_name = "🏕.svg"  # (camping).svg
        self._set_library_block_asset(block_id, file_name, SVG_DATA)
        assert self._get_library_block_asset(block_id, file_name)['path'] == file_name
        assert self._get_library_block_asset(block_id, file_name)['size'] == file_size

        # Subfolder names are allowed
        file_name = "transcripts/en.srt"
        self._set_library_block_asset(block_id, file_name, SVG_DATA)
        assert self._get_library_block_asset(block_id, file_name)['path'] == file_name
        assert self._get_library_block_asset(block_id, file_name)['size'] == file_size

        # '../' is definitely not allowed
        file_name = "../definition.xml"
        self._set_library_block_asset(block_id, file_name, SVG_DATA, expect_response=400)

        # 'a////////b' is not allowed
        file_name = "a////////b"
        self._set_library_block_asset(block_id, file_name, SVG_DATA, expect_response=400)

        # Names with spaces are allowed but replaced with underscores
        file_name_with_space = "o w o.svg"
        self._set_library_block_asset(block_id, file_name_with_space, SVG_DATA)
        file_name = "o_w_o.svg"
        assert self._get_library_block_asset(block_id, file_name)['path'] == file_name
        assert self._get_library_block_asset(block_id, file_name)['size'] == file_size

    def test_video_transcripts(self):
        """
        Test that video blocks can read transcript files out of openedx_content.
        """
        library = self._create_library(slug="transcript-test-lib", title="Transcripts Test Library")
        block = self._add_block_to_library(library["id"], "video", "video1")
        block_id = block["id"]
        self._set_library_block_olx(block_id, """
            <video
                youtube_id_1_0="3_yD_cEKoCk"
                display_name="Welcome Video with Transcript"
                download_track="true"
                transcripts='{"en": "3_yD_cEKoCk-en.srt"}'
            />
        """)
        # Upload the transcript file
        self._set_library_block_asset(block_id, "static/3_yD_cEKoCk-en.srt", TRANSCRIPT_DATA)

        transcript_handler_url = self._get_block_handler_url(block_id, "transcript")

        def check_sjson():
            """
            Call the handler endpoint which the video player uses to load the transcript as SJSON
            """
            url = transcript_handler_url + 'translation/en'
            response = self.client.get(url)
            assert response.status_code == 200
            assert 'Welcome to edX' in response.content.decode('utf-8')

        def check_download():
            """
            Call the handler endpoint which the video player uses to download the transcript SRT file
            """
            url = transcript_handler_url + 'download'
            response = self.client.get(url)
            assert response.status_code == 200
            assert response.content == TRANSCRIPT_DATA

        check_sjson()
        check_download()
        # Publish the OLX and the transcript file, since published data gets
        # served differently by openedx_content and we should test that too.
        self._commit_library_changes(library["id"])
        check_sjson()
        check_download()


@skip_unless_cms
class ContentLibrariesComponentVersionAssetTest(ContentLibrariesRestApiTest):
    """
    Tests for the view that actually delivers the Library asset in Studio.
    """

    def setUp(self):
        super().setUp()

        library = self._create_library(slug="asset-lib2", title="Static Assets Test Library")
        block = self._add_block_to_library(library["id"], "html", "html1")
        self._set_library_block_asset(block["id"], "static/test.svg", SVG_DATA)
        usage_key = UsageKey.from_string(block["id"])
        self.usage_key = usage_key
        self.component = get_component_from_usage_key(usage_key)
        self.draft_component_version = self.component.versioning.draft

    def test_good_responses(self):
        get_response = self.client.get(
            f"/library_assets/component_versions/{self.draft_component_version.uuid}/static/test.svg"
        )
        assert get_response.status_code == 200
        content = b''.join(chunk for chunk in get_response.streaming_content)
        assert content == SVG_DATA

        good_head_response = self.client.head(
            f"/library_assets/component_versions/{self.draft_component_version.uuid}/static/test.svg"
        )
        assert good_head_response.headers == get_response.headers

    def test_missing(self):
        """Test asset requests that should 404."""
        # Non-existent version...
        wrong_version_uuid = UUID('11111111-1111-1111-1111-111111111111')
        response = self.client.get(
            f"/library_assets/component_versions/{wrong_version_uuid}/static/test.svg"
        )
        assert response.status_code == 404

        # Non-existent file...
        response = self.client.get(
            f"/library_assets/component_versions/{self.draft_component_version.uuid}/static/missing.svg"
        )
        assert response.status_code == 404

        # File-like ComponentVersionMedia entry that isn't an actually
        # downloadable file...
        response = self.client.get(
            f"/library_assets/component_versions/{self.draft_component_version.uuid}/block.xml"
        )
        assert response.status_code == 404

    def test_anonymous_user(self):
        """Anonymous users shouldn't get access to library assets."""
        self.client.logout()
        response = self.client.get(
            f"/library_assets/component_versions/{self.draft_component_version.uuid}/static/test.svg"
        )
        assert response.status_code == 401

    def test_unauthorized_user(self):
        """User who is not a Content Library staff should not have access."""
        self.client.logout()
        UserFactory.create(
            username="student",
            email="student@example.com",
            password="student-pass",
            is_staff=False,
            is_superuser=False,
        )
        self.client.login(username="student", password="student-pass")
        get_response = self.client.get(
            f"/library_assets/component_versions/{self.draft_component_version.uuid}/static/test.svg"
        )
        assert get_response.status_code == 403

    def test_draft_version(self):
        """Get draft version of asset"""
        get_response = self.client.get(
            f"/library_assets/blocks/{self.usage_key}/static/test.svg"
        )
        assert get_response.status_code == 200
        content = b''.join(chunk for chunk in get_response.streaming_content)
        assert content == SVG_DATA

        good_head_response = self.client.head(
            f"/library_assets/blocks/{self.usage_key}/static/test.svg"
        )
        assert good_head_response.headers == get_response.headers

    def test_draft_version_404(self):
        """Get draft version of asset"""
        get_response = self.client.get(
            f"/library_assets/blocks/{self.usage_key}@/static/test.svg"
        )
        assert get_response.status_code == 404

        get_response = self.client.get(
            f"/library_assets/blocks/{self.usage_key}/static/test2.svg"
        )
        assert get_response.status_code == 404
