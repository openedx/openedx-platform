'''
Utilities for contentstore tests
'''


import json

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.test.client import Client
from opaque_keys.edx.keys import AssetKey

from cms.djangoapps.contentstore.utils import reverse_url
from common.djangoapps.student.models import Registration
from openedx.core.djangoapps.video_config.tests.test_transcripts_utils import YoutubeVideoHTMLResponse
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.inheritance import own_metadata
from xmodule.modulestore.split_mongo.split import SplitMongoModuleStore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.utils import ProceduralCourseTestMixin

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


def parse_json(response):
    """Parse response, which is assumed to be json"""
    return json.loads(response.content.decode('utf-8'))


def user(email):
    """look up a user by email"""
    return User.objects.get(email=email)


def registration(email):
    """look up registration object by email"""
    return Registration.objects.get(user__email=email)


class AjaxEnabledTestClient(Client):
    """
    Convenience class to make testing easier.
    """

    def ajax_post(self, path, data=None, content_type="application/json", **kwargs):
        """
        Convenience method for client post which serializes the data into json and sets the accept type
        to json
        """
        if not isinstance(data, str):
            data = json.dumps(data or {})
        kwargs.setdefault("HTTP_X_REQUESTED_WITH", "XMLHttpRequest")
        kwargs.setdefault("HTTP_ACCEPT", "application/json")
        return self.post(path=path, data=data, content_type=content_type, **kwargs)

    def get_html(self, path, data=None, follow=False, **extra):
        """
        Convenience method for client.get which sets the accept type to html
        """
        return self.get(path, data or {}, follow, HTTP_ACCEPT="text/html", **extra)

    def get_json(self, path, data=None, follow=False, **extra):
        """
        Convenience method for client.get which sets the accept type to json
        """
        return self.get(path, data or {}, follow, HTTP_ACCEPT="application/json", **extra)


class CourseTestCase(ProceduralCourseTestMixin, ModuleStoreTestCase):
    """
    Base class for Studio tests that require a logged in user and a course.
    Also provides helper methods for manipulating and verifying the course.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        These tests need a user in the DB so that the django Test Client can log them in.
        The test user is created in the ModuleStoreTestCase setUp method.
        They inherit from the ModuleStoreTestCase class so that the mongodb collection
        will be cleared out before each test case execution and deleted
        afterwards.
        """

        super().setUp()

        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.user_password)

        self.course = CourseFactory.create()

    def create_non_staff_authed_user_client(self, authenticate=True):
        """
        Create a non-staff user, log them in (if authenticate=True), and return the client, user to use for testing.
        """
        nonstaff, password = self.create_non_staff_user()

        client = AjaxEnabledTestClient()
        if authenticate:
            client.login(username=nonstaff.username, password=password)
        return client, nonstaff

    def reload_course(self):
        """
        Reloads the course object from the database
        """
        self.course = self.store.get_course(self.course.id)

    def save_course(self):
        """
        Updates the course object in the database
        """
        self.course.save()
        self.store.update_item(self.course, self.user.id)

    TEST_VERTICAL = 'vertical_test'
    ORPHAN_DRAFT_VERTICAL = 'orphan_draft_vertical'
    ORPHAN_DRAFT_HTML = 'orphan_draft_html'
    PRIVATE_VERTICAL = 'a_private_vertical'
    PUBLISHED_VERTICAL = 'a_published_vertical'
    SEQUENTIAL = 'vertical_sequential'
    DRAFT_HTML = 'draft_html'
    DRAFT_VIDEO = 'draft_video'
    LOCKED_ASSET_KEY = AssetKey.from_string('asset-v1:edX+toy+2012_Fall+type@asset+block@sample_static.html')

    def assertCoursesEqual(self, course1_id, course2_id):
        """
        Verifies the content of the two given courses are equal
        """
        course1_items = self.store.get_items(course1_id)
        course2_items = self.store.get_items(course2_id)
        self.assertGreater(len(course1_items), 0)  # ensure it found content instead of [] == []  # noqa: PT009
        if len(course1_items) != len(course2_items):
            course1_block_ids = {item.location.block_id for item in course1_items}
            course2_block_ids = {item.location.block_id for item in course2_items}
            raise AssertionError(
                "Course1 extra blocks: {}; course2 extra blocks: {}".format(  # noqa: UP032
                    course1_block_ids - course2_block_ids, course2_block_ids - course1_block_ids
                )
            )

        for course1_item in course1_items:
            course1_item_loc = course1_item.location
            course2_item_loc = course2_id.make_usage_key(course1_item_loc.block_type, course1_item_loc.block_id)
            if course1_item_loc.block_type == 'course':
                # mongo uses the run as the name, split uses 'course'
                store = self.store._get_modulestore_for_courselike(course2_id)  # pylint: disable=protected-access
                new_name = 'course' if isinstance(store, SplitMongoModuleStore) else course2_item_loc.run
                course2_item_loc = course2_item_loc.replace(name=new_name)
            course2_item = self.store.get_item(course2_item_loc)

            # compare published state
            self.assertEqual(  # noqa: PT009
                self.store.has_published_version(course1_item),
                self.store.has_published_version(course2_item)
            )

            # compare data
            self.assertEqual(hasattr(course1_item, 'data'), hasattr(course2_item, 'data'))  # noqa: PT009
            if hasattr(course1_item, 'data'):
                self.assertEqual(course1_item.data, course2_item.data)  # noqa: PT009

            # compare meta-data
            course1_metadata = own_metadata(course1_item)
            course2_metadata = own_metadata(course2_item)
            # Omit edx_video_id as it can be different in case of extrnal video imports.
            course1_metadata.pop('edx_video_id', None)
            course2_metadata.pop('edx_video_id', None)
            self.assertEqual(course1_metadata, course2_metadata)  # noqa: PT009

            # compare children
            self.assertEqual(course1_item.has_children, course2_item.has_children)  # noqa: PT009
            if course1_item.has_children:
                expected_children = []
                for course1_item_child in course1_item.children:
                    expected_children.append(
                        course2_id.make_usage_key(course1_item_child.block_type, course1_item_child.block_id)
                    )
                self.assertEqual(expected_children, course2_item.children)  # noqa: PT009

        # compare assets
        content_store = self.store.contentstore
        course1_assets, count_course1_assets = content_store.get_all_content_for_course(course1_id)
        _, count_course2_assets = content_store.get_all_content_for_course(course2_id)
        self.assertEqual(count_course1_assets, count_course2_assets)  # noqa: PT009
        for asset in course1_assets:
            asset_son = asset.get('content_son', asset['_id'])
            self.assertAssetsEqual(asset_son, course1_id, course2_id)

    def check_verticals(self, items):
        """ Test getting the editing HTML for each vertical. """
        # assert is here to make sure that the course being tested actually has verticals (units) to check.
        self.assertGreater(len(items), 0, "Course has no verticals (units) to check")  # noqa: PT009
        for block in items:
            resp = self.client.get_html(get_url('container_handler', block.location))
            self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def assertAssetsEqual(self, asset_son, course1_id, course2_id):
        """Verifies the asset of the given key has the same attributes in both given courses."""
        content_store = contentstore()
        category = asset_son.block_type if hasattr(asset_son, 'block_type') else asset_son['category']
        filename = asset_son.block_id if hasattr(asset_son, 'block_id') else asset_son['name']
        course1_asset_attrs = content_store.get_attrs(course1_id.make_asset_key(category, filename))
        course2_asset_attrs = content_store.get_attrs(course2_id.make_asset_key(category, filename))
        self.assertEqual(len(course1_asset_attrs), len(course2_asset_attrs))  # noqa: PT009
        for key, value in course1_asset_attrs.items():
            if key in ['_id', 'filename', 'uploadDate', 'content_son', 'thumbnail_location']:
                pass
            else:
                self.assertEqual(value, course2_asset_attrs[key])  # noqa: PT009


class HTTPGetResponse:
    """
    Generic object used to return results from a mock patch to an HTTP GET request
    """

    def __init__(self, status_code, response_string):
        self.status_code = status_code
        self.text = response_string
        self.content = response_string.encode('utf-8')


def setup_caption_responses(mock_get, language_code, caption_response_string, track_status_code=200):
    """
    When fetching youtube captions, two calls to requests.get() are required. The first fetches a
    captions URL (link) from the video page, applicable to the selected language track. The second
    fetches caption timing information from that track's captions URL.

    This helper method assumes that the two operations are performed in order, and is used in conjunction
    with mock patch() operations to return appropriate results for each of the two get operations.
    """
    caption_link_response = YoutubeVideoHTMLResponse.with_caption_track(language_code)
    caption_track_response = HTTPGetResponse(track_status_code, caption_response_string)
    mock_get.side_effect = [
        caption_link_response,
        caption_track_response,
    ]


def get_url(handler_name, key_value, key_name='usage_key_string', kwargs=None):
    """
    Helper function for getting HTML for a page in Studio and checking that it does not error.
    """
    return reverse_url(handler_name, key_name, key_value, kwargs)
