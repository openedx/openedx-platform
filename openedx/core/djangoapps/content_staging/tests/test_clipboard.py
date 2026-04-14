# pylint: skip-file
"""
Tests for the clipboard functionality
"""
from textwrap import dedent
from typing import cast
from xml.etree import ElementTree

from rest_framework.test import APIClient

from openedx.core.djangoapps.content_staging import api as python_api
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, upload_file_to_course
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, ToyCourseFactory

CLIPBOARD_ENDPOINT = "/api/content-staging/v1/clipboard/"

# OLX of the video in the toy course using course_key.make_usage_key("video", "sample_video")
SAMPLE_VIDEO_OLX = """
    <video
        copied_from_block="block-v1:edX+toy+2012_Fall+type@video+block@sample_video"
        url_name="sample_video"
        display_name="default"
        youtube="0.75:JMD_ifUUfsU,1.00:OEoXaMPEzfM,1.25:AKqURZnYqpk,1.50:DYpADpL7jAY"
        youtube_id_0_75="JMD_ifUUfsU"
        youtube_id_1_0="OEoXaMPEzfM"
        youtube_id_1_25="AKqURZnYqpk"
        youtube_id_1_5="DYpADpL7jAY"
    />
"""


class ClipboardTestCase(ModuleStoreTestCase):
    """
    Test Clipboard functionality
    """

    def test_empty_clipboard(self) -> None:
        """
        When a user has no content on their clipboard, we get an empty 200 response
        """
        ## Test the REST API:
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)
        response = client.get(CLIPBOARD_ENDPOINT)
        # We don't consider this a 404 error, it's a 200 with an empty response
        assert response.status_code == 200
        assert response.json() == {
            "content": None,
            "source_usage_key": "",
            "source_context_title": "",
            "source_edit_url": "",
        }
        ## The Python method for getting the API response should be identical:
        assert response.json() == python_api.get_user_clipboard_json(self.user.id, response.wsgi_request)
        # And the pure python API should return None
        assert python_api.get_user_clipboard(self.user.id) is None

    def _setup_course(self):
        """ Set up the "Toy Course" and an APIClient for testing clipboard functionality. """
        # Setup:
        course_key = ToyCourseFactory.create().id  # See xmodule/modulestore/tests/sample_courses.py
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)

        # Initial conditions: clipboard is empty:
        response = client.get(CLIPBOARD_ENDPOINT)
        assert response.status_code == 200
        assert response.json()["content"] is None

        return (course_key, client)

    def test_copy_video(self) -> None:
        """
        Test copying a video from the course, and retrieve it using the REST API
        """
        course_key, client = self._setup_course()

        # Copy the video
        video_key = course_key.make_usage_key("video", "sample_video")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(video_key)}, format="json")

        # Validate the response:
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["source_usage_key"] == str(video_key)
        assert response_data["source_context_title"] == "Toy Course"
        assert response_data["content"] == {**response_data["content"], **{
            "block_type": "video",
            "block_type_display": "Video",
            # To ensure API stability, we are hard-coding these expected values:
            "purpose": "clipboard",
            "status": "ready",
            "display_name": "default",  # Weird name but that's what defined in the toy course
        }}
        # Test the actual OLX in the clipboard:
        olx_url = response_data["content"]["olx_url"]
        olx_response = client.get(olx_url)
        assert olx_response.status_code == 200
        assert olx_response.get("Content-Type") == "application/vnd.openedx.xblock.v1.video+xml"
        self.assertXmlEqual(olx_response.content.decode(), SAMPLE_VIDEO_OLX)

        # Now if we GET the clipboard again, the GET response should exactly equal the last POST response:
        assert client.get(CLIPBOARD_ENDPOINT).json() == response_data

    def test_copy_video_python_get(self) -> None:
        """
        Test copying a video from the course, and retrieve it using the python API
        """
        course_key, client = self._setup_course()

        # Copy the video
        video_key = course_key.make_usage_key("video", "sample_video")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(video_key)}, format="json")
        assert response.status_code == 200

        # Get the clipboard status using python:
        clipboard_data = python_api.get_user_clipboard(self.user.id)
        assert clipboard_data is not None
        assert clipboard_data.source_usage_key == video_key
        # source_context_title is not in the python API because it's easy to retrieve a course's name from python code.
        assert clipboard_data.content.block_type == "video"
        # To ensure API stability, we are hard-coding these expected values:
        assert clipboard_data.content.purpose == "clipboard"
        assert clipboard_data.content.status == "ready"
        assert clipboard_data.content.display_name == "default"
        # Test the actual OLX in the clipboard:
        olx_data = python_api.get_staged_content_olx(clipboard_data.content.id)
        assert olx_data is not None
        self.assertXmlEqual(olx_data, SAMPLE_VIDEO_OLX)

    def test_copy_html(self) -> None:
        """
        Test copying an HTML XBlock from the course
        """
        course_key, client = self._setup_course()

        # Copy the HTML
        html_key = course_key.make_usage_key("html", "toyhtml")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(html_key)}, format="json")

        # Validate the response:
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["source_usage_key"] == str(html_key)
        assert response_data["source_context_title"] == "Toy Course"
        assert response_data["content"] == {**response_data["content"], **{
            "block_type": "html",
            # To ensure API stability, we are hard-coding these expected values:
            "purpose": "clipboard",
            "status": "ready",
            "display_name": "Text",  # Has no display_name set so we fallback to this default
        }}
        # Test the actual OLX in the clipboard:
        olx_url = response_data["content"]["olx_url"]
        olx_response = client.get(olx_url)
        assert olx_response.status_code == 200
        assert olx_response.get("Content-Type") == "application/vnd.openedx.xblock.v1.html+xml"
        # For HTML, we really want to be sure that the OLX is serialized in this exact format (using CDATA), so we check
        # the actual string directly rather than using assertXmlEqual():
        assert olx_response.content.decode() == dedent("""
            <html url_name="toyhtml" display_name="Text" copied_from_block="block-v1:edX+toy+2012_Fall+type@html+block@toyhtml"><![CDATA[
            <a href='/static/handouts/sample_handout.txt'>Sample</a>
            ]]></html>
        """).replace("\n", "") + "\n"  # No newlines, expect one trailing newline.

        # Now if we GET the clipboard again, the GET response should exactly equal the last POST response:
        assert client.get(CLIPBOARD_ENDPOINT).json() == response_data

    def test_copy_unit(self) -> None:
        """
        Test copying a unit (vertical block) from the course
        """
        course_key, client = self._setup_course()

        # Copy the HTML
        unit_key = course_key.make_usage_key("vertical", "vertical_test")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(unit_key)}, format="json")

        # Validate the response:
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["source_usage_key"] == str(unit_key)
        assert response_data["source_context_title"] == "Toy Course"
        assert response_data["content"] == {**response_data["content"], **{
            "block_type": "vertical",
            # To ensure API stability, we are hard-coding these expected values:
            "purpose": "clipboard",
            "status": "ready",
            "display_name": "vertical test",  # Has no display_name set so display_name_with_default falls back to this
        }}
        # Test the actual OLX in the clipboard:
        olx_url = response_data["content"]["olx_url"]
        olx_response = client.get(olx_url)
        assert olx_response.status_code == 200
        assert olx_response.get("Content-Type") == "application/vnd.openedx.xblock.v1.vertical+xml"
        self.assertXmlEqual(olx_response.content.decode(), """
            <vertical url_name="vertical_test" copied_from_block="block-v1:edX+toy+2012_Fall+type@vertical+block@vertical_test">
                <video
                    copied_from_block="block-v1:edX+toy+2012_Fall+type@video+block@sample_video"
                    url_name="sample_video"
                    display_name="default"
                    youtube="0.75:JMD_ifUUfsU,1.00:OEoXaMPEzfM,1.25:AKqURZnYqpk,1.50:DYpADpL7jAY"
                    youtube_id_0_75="JMD_ifUUfsU"
                    youtube_id_1_0="OEoXaMPEzfM"
                    youtube_id_1_25="AKqURZnYqpk"
                    youtube_id_1_5="DYpADpL7jAY"
                />
                <video
                    copied_from_block="block-v1:edX+toy+2012_Fall+type@video+block@separate_file_video"
                    url_name="separate_file_video"
                    display_name="default"
                    youtube="0.75:JMD_ifUUfsU,1.00:OEoXaMPEzfM,1.25:AKqURZnYqpk,1.50:DYpADpL7jAY"
                    youtube_id_0_75="JMD_ifUUfsU"
                    youtube_id_1_0="OEoXaMPEzfM"
                    youtube_id_1_25="AKqURZnYqpk"
                    youtube_id_1_5="DYpADpL7jAY"
                />
                <video
                    copied_from_block="block-v1:edX+toy+2012_Fall+type@video+block@video_with_end_time"
                    url_name="video_with_end_time"
                    display_name="default"
                    youtube="0.75:JMD_ifUUfsU,1.00:OEoXaMPEzfM,1.25:AKqURZnYqpk,1.50:DYpADpL7jAY"
                    end_time="00:00:10"
                    youtube_id_0_75="JMD_ifUUfsU"
                    youtube_id_1_0="OEoXaMPEzfM"
                    youtube_id_1_25="AKqURZnYqpk"
                    youtube_id_1_5="DYpADpL7jAY"
                />
                <poll_question
                    copied_from_block="block-v1:edX+toy+2012_Fall+type@poll_question+block@T1_changemind_poll_foo_2"
                    url_name="T1_changemind_poll_foo_2"
                    display_name="Change your answer"
                    reset="false"
                >
                    &lt;p&gt;Have you changed your mind?&lt;/p&gt;
                    <answer id="yes">Yes</answer>
                    <answer id="no">No</answer>
                </poll_question>
            </vertical>
        """)

        # Now if we GET the clipboard again, the GET response should exactly equal the last POST response:
        assert client.get(CLIPBOARD_ENDPOINT).json() == response_data

    def test_copy_several_things(self) -> None:
        """
        Test that the clipboard only holds one thing at a time.
        """
        course_key, client = self._setup_course()

        # Copy the video and validate the response:
        video_key = course_key.make_usage_key("video", "sample_video")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(video_key)}, format="json")
        assert response.status_code == 200
        video_clip_data = response.json()
        assert video_clip_data["source_usage_key"] == str(video_key)
        assert video_clip_data["content"]["block_type"] == "video"
        old_olx_url = video_clip_data["content"]["olx_url"]
        assert client.get(old_olx_url).status_code == 200

        # Now copy some HTML:
        html_key = course_key.make_usage_key("html", "toyhtml")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(html_key)}, format="json")
        assert response.status_code == 200

        # Now check the clipboard:
        response = client.get(CLIPBOARD_ENDPOINT)
        html_clip_data = response.json()
        assert html_clip_data["source_usage_key"] == str(html_key)
        assert html_clip_data["content"]["block_type"] == "html"
        assert html_clip_data["content"]["block_type_display"] == "Text"
        ## The Python method for getting the API response should be identical:
        assert html_clip_data == python_api.get_user_clipboard_json(self.user.id, response.wsgi_request)

        # The OLX link from the video will no longer work:
        assert client.get(old_olx_url).status_code == 404

    def test_copy_static_assets(self) -> None:
        """
        Test copying an HTML from the course that references a static asset file.
        """
        course_key, client = self._setup_course()
        # The toy course references static files that don't actually exist yet, so we have to upload one now:
        upload_file_to_course(
            course_key=course_key,
            contentstore=contentstore(),
            source_file='./common/test/data/toy/static/just_a_test.jpg',
            target_filename="foo_bar.jpg",
        )

        # Copy the HTML XBlock to the clipboard:
        html_key = course_key.make_usage_key("html", "just_img")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(html_key)}, format="json")

        # Validate the response:
        assert response.status_code == 200
        response_data = response.json()
        staged_content_id = cast(python_api.StagedContentID, response_data["content"]["id"])
        olx_str = python_api.get_staged_content_olx(staged_content_id)
        assert olx_str is not None
        assert '<img src="/static/foo_bar.jpg" />' in olx_str
        static_assets = python_api.get_staged_content_static_files(staged_content_id)

        assert static_assets == [python_api.StagedContentFileData(
            filename="foo_bar.jpg",
            source_key=course_key.make_asset_key("asset", "foo_bar.jpg"),
            md5_hash="addd3c218c0c0c41e7e1ae73f5969810",
            data=None,
        )]

    def test_copy_static_assets_nonexistent(self) -> None:
        """
        Test copying a HTML block which references non-existent static assets.
        """
        _other_course_key, client = self._setup_course()
        course = CourseFactory.create()
        html_block = BlockFactory.create(
            parent_location=course.location,
            category="html",
            display_name="Some HTML",
            data="""
            <p>
                <a href="/static/nonexistent1.jpg">Picture 1</a>
                <a href="/static/nonexistent2.jpg">Picture 2</a>
            </p>
            """,
        )
        # Copy the HTML
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(html_block.location)}, format="json")

        # Validate the response:
        assert response.status_code == 200
        response_data = response.json()
        staged_content_id = response_data["content"]["id"]
        olx_str = python_api.get_staged_content_olx(staged_content_id)
        assert olx_str is not None
        assert '<a href="/static/nonexistent1.jpg">' in olx_str
        static_assets = python_api.get_staged_content_static_files(staged_content_id)
        assert static_assets == []

    def test_no_course_permission(self) -> None:
        """
        Test that a user without read access cannot copy items in a course
        """
        course_key = ToyCourseFactory.create().id
        nonstaff_client = APIClient()
        nonstaff_username, nonstaff_password = self.create_non_staff_user()
        nonstaff_client.login(username=nonstaff_username, password=nonstaff_password)

        # Try copying the video as a non-staff user:
        html_key = course_key.make_usage_key("html", "toyhtml")
        with self.allow_transaction_exception():
            response = nonstaff_client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(html_key)}, format="json")
            assert response.status_code == 403
        response = nonstaff_client.get(CLIPBOARD_ENDPOINT)
        assert response.json()["content"] is None

    def test_no_stealing_clipboard_content(self) -> None:
        """
        Test that a user cannot see another user's clipboard
        """
        course_key, client = self._setup_course()
        nonstaff_client = APIClient()
        nonstaff_username, nonstaff_password = self.create_non_staff_user()
        nonstaff_client.login(username=nonstaff_username, password=nonstaff_password)

        # The regular user copies something to their clipboard:
        html_key = course_key.make_usage_key("html", "toyhtml")
        response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(html_key)}, format="json")
        # Then another user tries to get the OLX:
        olx_url = response.json()["content"]["olx_url"]
        response = nonstaff_client.get(olx_url)
        assert response.status_code == 403

    def assertXmlEqual(self, xml_str_a: str, xml_str_b: str) -> None:
        """ Assert that the given XML strings are equal, ignoring attribute order and some whitespace variations. """
        a = ElementTree.canonicalize(xml_str_a, strip_text=True)
        b = ElementTree.canonicalize(xml_str_b, strip_text=True)
        assert a == b
