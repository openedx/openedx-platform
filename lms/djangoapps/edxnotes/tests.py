"""
Tests for the EdxNotes app.
"""

import json
from contextlib import contextmanager
from datetime import datetime
from unittest import skipUnless
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import ddt
import jwt
import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from oauth2_provider.models import Application

from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, SuperuserFactory, UserFactory
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from lms.djangoapps.courseware.tabs import get_course_tab_list
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import ApplicationFactory
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementStatus
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tabs import CourseTab  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tests.helpers import StubUserService  # lint-amnesty, pylint: disable=wrong-import-order

from . import helpers
from .decorators import edxnotes
from .exceptions import EdxNotesParseError, EdxNotesServiceUnavailable
from .plugins import EdxNotesTab

FEATURES = settings.FEATURES.copy()

NOTES_API_EMPTY_RESPONSE = {
    "total": 0,
    "rows": [],
    "current_page": 1,
    "start": 0,
    "next": None,
    "previous": None,
    "num_pages": 0,
}

NOTES_VIEW_EMPTY_RESPONSE = {
    "count": 0,
    "results": [],
    "current_page": 1,
    "start": 0,
    "next": None,
    "previous": None,
    "num_pages": 0,
}


def enable_edxnotes_for_the_course(course, user_id):
    """
    Enable EdxNotes for the course.
    """
    course.tabs.append(CourseTab.load("edxnotes"))
    modulestore().update_item(course, user_id)


@edxnotes
class TestProblem:
    """
    Test class (fake problem) decorated by edxnotes decorator.

    The purpose of this class is to imitate any problem.
    """
    def __init__(self, course, user=None):
        self.scope_ids = MagicMock(usage_id=course.id.make_usage_key('test_problem', 'test_usage_id'))
        user = user or UserFactory()
        user_service = StubUserService(user)
        self.runtime = MagicMock(service=lambda _a, _b: user_service, is_author_mode=False)
        self.block = MagicMock()
        self.block.runtime.modulestore.get_course.return_value = course

    def get_html(self):
        """
        Imitate get_html in block.
        """
        return "original_get_html"


@skipUnless(settings.FEATURES["ENABLE_EDXNOTES"], "EdxNotes feature needs to be enabled.")
class EdxNotesDecoratorTest(ModuleStoreTestCase):
    """
    Tests for edxnotes decorator.
    """

    def setUp(self):
        super().setUp()

        ApplicationFactory(name="edx-notes")
        self.course = CourseFactory(edxnotes=True, default_store=ModuleStoreEnum.Type.split)
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=UserFactory._DEFAULT_PASSWORD)  # lint-amnesty, pylint: disable=protected-access
        self.problem = TestProblem(self.course, self.user)

    @patch.dict("django.conf.settings.FEATURES", {'ENABLE_EDXNOTES': True})
    @patch("lms.djangoapps.edxnotes.helpers.get_public_endpoint", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.get_token_url", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.get_edxnotes_id_token", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.generate_uid", autospec=True)
    def test_edxnotes_enabled(self, mock_generate_uid, mock_get_id_token, mock_get_token_url, mock_get_endpoint):
        """
        Tests if get_html is wrapped when feature flag is on and edxnotes are
        enabled for the course.
        """
        course = CourseFactory(edxnotes=True)
        enrollment = CourseEnrollmentFactory(course_id=course.id)
        user = enrollment.user
        problem = TestProblem(course, user)

        mock_generate_uid.return_value = "uid"
        mock_get_id_token.return_value = "token"
        mock_get_token_url.return_value = "/tokenUrl"
        mock_get_endpoint.return_value = "/endpoint"
        enable_edxnotes_for_the_course(course, user.id)
        expected_context = {
            "content": "original_get_html",
            "uid": "uid",
            "edxnotes_visibility": "true",
            "params": {
                "usageId": problem.scope_ids.usage_id,
                "courseId": course.id,
                "token": "token",
                "tokenUrl": "/tokenUrl",
                "endpoint": "/endpoint",
                "debug": settings.DEBUG,
                "eventStringLimit": settings.TRACK_MAX_EVENT / 6,
            },
        }
        assert problem.get_html() == render_to_string('edxnotes_wrapper.html', expected_context)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    def test_edxnotes_disabled_if_edxnotes_flag_is_false(self):
        """
        Tests that get_html is wrapped when feature flag is on, but edxnotes are
        disabled for the course.
        """
        self.course.edxnotes = False
        assert 'original_get_html' == self.problem.get_html()

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": False})
    def test_edxnotes_disabled(self):
        """
        Tests that get_html is not wrapped when feature flag is off.
        """
        assert 'original_get_html' == self.problem.get_html()

    def test_edxnotes_studio(self):
        """
        Tests that get_html is not wrapped when problem is rendered in Studio.
        """
        self.problem.runtime.is_author_mode = True
        assert 'original_get_html' == self.problem.get_html()

    def test_edxnotes_openedx_content_runtime(self):
        """
        Tests that get_html is not wrapped when problem is rendered by the openedx_content runtime.
        """
        del self.problem.block.runtime.modulestore
        assert 'original_get_html' == self.problem.get_html()

    def test_edxnotes_harvard_notes_enabled(self):
        """
        Tests that get_html is not wrapped when Harvard Annotation Tool is enabled.
        """
        self.course.advanced_modules = ["videoannotation", "imageannotation", "textannotation"]
        enable_edxnotes_for_the_course(self.course, self.user.id)
        assert 'original_get_html' == self.problem.get_html()

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    def test_anonymous_user(self):
        user = AnonymousUser()
        problem = TestProblem(self.course, user)
        enable_edxnotes_for_the_course(self.course, None)
        assert problem.get_html() == "original_get_html"


@skipUnless(settings.FEATURES["ENABLE_EDXNOTES"], "EdxNotes feature needs to be enabled.")
@ddt.ddt
class EdxNotesHelpersTest(ModuleStoreTestCase):
    """
    Tests for EdxNotes helpers.
    """
    def setUp(self):
        """
        Setup a dummy course content.
        """
        super().setUp()

        with self.store.default_store(ModuleStoreEnum.Type.split):
            ApplicationFactory(name="edx-notes")
            self.course = CourseFactory.create()
            self.chapter = BlockFactory.create(category="chapter", parent_location=self.course.location)
            self.chapter_2 = BlockFactory.create(category="chapter", parent_location=self.course.location)
            self.sequential = BlockFactory.create(category="sequential", parent_location=self.chapter.location)
            self.vertical = BlockFactory.create(category="vertical", parent_location=self.sequential.location)
            self.html_block_1 = BlockFactory.create(category="html", parent_location=self.vertical.location)
            self.html_block_2 = BlockFactory.create(category="html", parent_location=self.vertical.location)
            self.vertical_with_container = BlockFactory.create(
                category='vertical', parent_location=self.sequential.location
            )
            self.child_container = BlockFactory.create(
                category='split_test', parent_location=self.vertical_with_container.location)
            self.child_vertical = BlockFactory.create(
                category='vertical', parent_location=self.child_container.location)
            self.child_html_block = BlockFactory.create(category="html", parent_location=self.child_vertical.location)

            # Read again so that children lists are accurate
            self.course = self.store.get_item(self.course.location)
            self.chapter = self.store.get_item(self.chapter.location)
            self.chapter_2 = self.store.get_item(self.chapter_2.location)
            self.sequential = self.store.get_item(self.sequential.location)
            self.vertical = self.store.get_item(self.vertical.location)

            self.vertical_with_container = self.store.get_item(self.vertical_with_container.location)
            self.child_container = self.store.get_item(self.child_container.location)
            self.child_vertical = self.store.get_item(self.child_vertical.location)
            self.child_html_block = self.store.get_item(self.child_html_block.location)

            self.user = UserFactory()
            self.client.login(username=self.user.username, password=UserFactory._DEFAULT_PASSWORD)  # lint-amnesty, pylint: disable=protected-access

        self.request = RequestFactory().request()
        self.request.user = self.user

    def _get_unit_url(self, course, chapter, section, position=1):
        """
        Returns `jump_to_id` url for the `vertical`.
        """
        return reverse('courseware_position', kwargs={
            'course_id': course.id,
            'section': chapter.url_name,
            'subsection': section.url_name,
            'position': position,
        })

    def test_edxnotes_harvard_notes_enabled(self):
        """
        Tests that edxnotes are disabled when Harvard Annotation Tool is enabled.
        """
        self.course.advanced_modules = ['imageannotation', 'textannotation', 'videoannotation']
        assert not helpers.is_feature_enabled(self.course, self.user)

    @ddt.data(True, False)
    def test_is_feature_enabled(self, enabled):
        """
        Tests that is_feature_enabled shows correct behavior.
        """
        course = CourseFactory(edxnotes=enabled)
        enrollment = CourseEnrollmentFactory(course_id=course.id)
        assert helpers.is_feature_enabled(course, enrollment.user) == enabled

    @ddt.data(
        helpers.get_public_endpoint,
        helpers.get_internal_endpoint,
    )
    def test_get_endpoints(self, get_endpoint_function):
        """
        Test that the get_public_endpoint and get_internal_endpoint functions
        return appropriate values.
        """
        @contextmanager
        def patch_edxnotes_api_settings(url):
            """
            Convenience function for patching both EDXNOTES_PUBLIC_API and
            EDXNOTES_INTERNAL_API.
            """
            with override_settings(EDXNOTES_PUBLIC_API=url):
                with override_settings(EDXNOTES_INTERNAL_API=url):
                    yield

        # url ends with "/"
        with patch_edxnotes_api_settings("http://example.com/"):
            assert 'http://example.com/' == get_endpoint_function()

        # url doesn't have "/" at the end
        with patch_edxnotes_api_settings("http://example.com"):
            assert 'http://example.com/' == get_endpoint_function()

        # url with path that starts with "/"
        with patch_edxnotes_api_settings("http://example.com"):
            assert 'http://example.com/some_path/' == get_endpoint_function('/some_path')

        # url with path without "/"
        with patch_edxnotes_api_settings("http://example.com"):
            assert 'http://example.com/some_path/' == get_endpoint_function('some_path/')

        # url is not configured
        with patch_edxnotes_api_settings(None):
            pytest.raises(ImproperlyConfigured, get_endpoint_function)

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_get_notes_correct_data(self, mock_get):
        """
        Tests the result if correct data is received.
        """
        mock_get.return_value.content = json.dumps(
            {
                "total": 2,
                "current_page": 1,
                "start": 0,
                "next": None,
                "previous": None,
                "num_pages": 1,
                "rows": [
                    {
                        "quote": "quote text",
                        "text": "text",
                        "usage_id": str(self.html_block_1.location),
                        "updated": datetime(2014, 11, 19, 8, 5, 16, 00000).isoformat(),
                    },
                    {
                        "quote": "quote text",
                        "text": "text",
                        "usage_id": str(self.html_block_2.location),
                        "updated": datetime(2014, 11, 19, 8, 6, 16, 00000).isoformat(),
                    }
                ]
            }
        ).encode('utf-8')

        assert len(
            {
                "count": 2,
                "current_page": 1,
                "start": 0,
                "next": None,
                "previous": None,
                "num_pages": 1,
                "results": [
                    {
                        "quote": "quote text",
                        "text": "text",
                        "chapter": {
                            "display_name": self.chapter.display_name_with_default,
                            "index": 0,
                            "location": str(self.chapter.location),
                            "children": [str(self.sequential.location)]
                        },
                        "section": {
                            "display_name": self.sequential.display_name_with_default,
                            "location": str(self.sequential.location),
                            "children": [
                                str(self.vertical.location), str(self.vertical_with_container.location)
                            ]
                        },
                        "unit": {
                            "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                            "display_name": self.vertical.display_name_with_default,
                            "location": str(self.vertical.location),
                        },
                        "usage_id": str(self.html_block_2.location),
                        "updated": "Nov 19, 2014 at 08:06 UTC",
                    },
                    {
                        "quote": "quote text",
                        "text": "text",
                        "chapter": {
                            "display_name": self.chapter.display_name_with_default,
                            "index": 0,
                            "location": str(self.chapter.location),
                            "children": [str(self.sequential.location)]
                        },
                        "section": {
                            "display_name": self.sequential.display_name_with_default,
                            "location": str(self.sequential.location),
                            "children": [
                                str(self.vertical.location),
                                str(self.vertical_with_container.location)]
                        },
                        "unit": {
                            "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                            "display_name": self.vertical.display_name_with_default,
                            "location": str(self.vertical.location),
                        },
                        "usage_id": str(self.html_block_1.location),
                        "updated": "Nov 19, 2014 at 08:05 UTC",
                    },
                ]
            }) == len(helpers.get_notes(self.request, self.course))

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_get_notes_json_error(self, mock_get):
        """
        Tests the result if incorrect json is received.
        """
        mock_get.return_value.content = b"Error"
        self.assertRaises(EdxNotesParseError, helpers.get_notes, self.request, self.course)

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_get_notes_empty_collection(self, mock_get):
        """
        Tests the result if an empty response is received.
        """
        mock_get.return_value.content = json.dumps({}).encode('utf-8')
        self.assertRaises(EdxNotesParseError, helpers.get_notes, self.request, self.course)

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_search_correct_data(self, mock_get):
        """
        Tests the result if correct data is received.
        """
        mock_get.return_value.content = json.dumps({
            "total": 2,
            "current_page": 1,
            "start": 0,
            "next": None,
            "previous": None,
            "num_pages": 1,
            "rows": [
                {
                    "quote": "quote text",
                    "text": "text",
                    "usage_id": str(self.html_block_1.location),
                    "updated": datetime(2014, 11, 19, 8, 5, 16, 00000).isoformat(),
                },
                {
                    "quote": "quote text",
                    "text": "text",
                    "usage_id": str(self.html_block_2.location),
                    "updated": datetime(2014, 11, 19, 8, 6, 16, 00000).isoformat(),
                }
            ]
        }).encode('utf-8')

        assert len(
            {
                "count": 2,
                "current_page": 1,
                "start": 0,
                "next": None,
                "previous": None,
                "num_pages": 1,
                "results": [
                    {
                        "quote": "quote text",
                        "text": "text",
                        "chapter": {
                            "display_name": self.chapter.display_name_with_default,
                            "index": 0,
                            "location": str(self.chapter.location),
                            "children": [str(self.sequential.location)]
                        },
                        "section": {
                            "display_name": self.sequential.display_name_with_default,
                            "location": str(self.sequential.location),
                            "children": [
                                str(self.vertical.location),
                                str(self.vertical_with_container.location)]
                        },
                        "unit": {
                            "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                            "display_name": self.vertical.display_name_with_default,
                            "location": str(self.vertical.location),
                        },
                        "usage_id": str(self.html_block_2.location),
                        "updated": "Nov 19, 2014 at 08:06 UTC",
                    },
                    {
                        "quote": "quote text",
                        "text": "text",
                        "chapter": {
                            "display_name": self.chapter.display_name_with_default,
                            "index": 0,
                            "location": str(self.chapter.location),
                            "children": [str(self.sequential.location)]
                        },
                        "section": {
                            "display_name": self.sequential.display_name_with_default,
                            "location": str(self.sequential.location),
                            "children": [
                                str(self.vertical.location),
                                str(self.vertical_with_container.location)]
                        },
                        "unit": {
                            "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                            "display_name": self.vertical.display_name_with_default,
                            "location": str(self.vertical.location),
                        },
                        "usage_id": str(self.html_block_1.location),
                        "updated": "Nov 19, 2014 at 08:05 UTC",
                    },
                ]
            }) == len(helpers.get_notes(self.request, self.course))

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_search_json_error(self, mock_get):
        """
        Tests the result if incorrect json is received.
        """
        mock_get.return_value.content = b"Error"
        self.assertRaises(EdxNotesParseError, helpers.get_notes, self.request, self.course)

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_search_wrong_data_format(self, mock_get):
        """
        Tests the result if incorrect data structure is received.
        """
        mock_get.return_value.content = json.dumps({"1": 2}).encode('utf-8')
        self.assertRaises(EdxNotesParseError, helpers.get_notes, self.request, self.course)

    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_search_empty_collection(self, mock_get):
        """
        Tests no results.
        """
        mock_get.return_value.content = json.dumps(NOTES_API_EMPTY_RESPONSE).encode('utf-8')
        assert len(NOTES_VIEW_EMPTY_RESPONSE) == len(helpers.get_notes(self.request, self.course))

    @override_settings(EDXNOTES_PUBLIC_API="http://example.com")
    @override_settings(EDXNOTES_INTERNAL_API="http://example.com")
    @patch("lms.djangoapps.edxnotes.helpers.anonymous_id_for_user", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.get_edxnotes_id_token", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.requests.post")
    def test_delete_all_notes_for_user(self, mock_post, mock_get_id_token, mock_anonymous_id_for_user):
        """
        Test GDPR data deletion for Notes user_id
        """
        mock_anonymous_id_for_user.return_value = "anonymous_id"
        mock_get_id_token.return_value = "test_token"
        helpers.delete_all_notes_for_user(self.user)
        mock_post.assert_called_with(
            url='http://example.com/retire_annotations/',
            headers={
                'x-annotator-auth-token': 'test_token'
            },
            data={
                'user': 'anonymous_id'
            },
            timeout=(settings.EDXNOTES_CONNECT_TIMEOUT, settings.EDXNOTES_READ_TIMEOUT)
        )

    def test_preprocess_collection_no_item(self):
        """
        Tests the result if appropriate block is not found.
        """
        initial_collection = [
            {
                "quote": "quote text",
                "text": "text",
                "usage_id": str(self.html_block_1.location),
                "updated": datetime(2014, 11, 19, 8, 5, 16, 00000).isoformat()
            },
            {
                "quote": "quote text",
                "text": "text",
                "usage_id": str(self.course.id.make_usage_key("html", "test_item")),
                "updated": datetime(2014, 11, 19, 8, 6, 16, 00000).isoformat()
            },
        ]

        assert len(
            [{
                "quote": "quote text",
                "text": "text",
                "chapter": {
                    "display_name": self.chapter.display_name_with_default,
                    "index": 0,
                    "location": str(self.chapter.location),
                    "children": [str(self.sequential.location)]
                },
                "section": {
                    "display_name": self.sequential.display_name_with_default,
                    "location": str(self.sequential.location),
                    "children": [str(self.vertical.location), str(self.vertical_with_container.location)]
                },
                "unit": {
                    "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                    "display_name": self.vertical.display_name_with_default,
                    "location": str(self.vertical.location),
                },
                "usage_id": str(self.html_block_1.location),
                "updated": datetime(2014, 11, 19, 8, 5, 16, 00000),
            }]) == len(helpers.preprocess_collection(self.user, self.course, initial_collection))

    def test_preprocess_collection_has_access(self):
        """
        Tests the result if the user does not have access to some of the blocks.
        """
        initial_collection = [
            {
                "quote": "quote text",
                "text": "text",
                "usage_id": str(self.html_block_1.location),
                "updated": datetime(2014, 11, 19, 8, 5, 16, 00000).isoformat(),
            },
            {
                "quote": "quote text",
                "text": "text",
                "usage_id": str(self.html_block_2.location),
                "updated": datetime(2014, 11, 19, 8, 6, 16, 00000).isoformat(),
            },
        ]
        self.html_block_2.visible_to_staff_only = True
        self.store.update_item(self.html_block_2, self.user.id)
        assert len(
            [{
                "quote": "quote text",
                "text": "text",
                "chapter": {
                    "display_name": self.chapter.display_name_with_default,
                    "index": 0,
                    "location": str(self.chapter.location),
                    "children": [str(self.sequential.location)]
                },
                "section": {
                    "display_name": self.sequential.display_name_with_default,
                    "location": str(self.sequential.location),
                    "children": [str(self.vertical.location), str(self.vertical_with_container.location)]
                },
                "unit": {
                    "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                    "display_name": self.vertical.display_name_with_default,
                    "location": str(self.vertical.location),
                },
                "usage_id": str(self.html_block_1.location),
                "updated": datetime(2014, 11, 19, 8, 5, 16, 00000),
            }]) == len(helpers.preprocess_collection(self.user, self.course, initial_collection))

    @patch("lms.djangoapps.edxnotes.helpers.has_access", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.modulestore", autospec=True)
    def test_preprocess_collection_no_unit(self, mock_modulestore, mock_has_access):
        """
        Tests the result if the unit does not exist.
        """
        store = MagicMock()
        store.get_item().get_parent.return_value = None
        mock_modulestore.return_value = store
        mock_has_access.return_value = True
        initial_collection = [{
            "quote": "quote text",
            "text": "text",
            "usage_id": str(self.html_block_1.location),
            "updated": datetime(2014, 11, 19, 8, 5, 16, 00000).isoformat(),
        }]

        assert not helpers.preprocess_collection(self.user, self.course, initial_collection)

    @override_settings(NOTES_DISABLED_TABS=['course_structure', 'tags'])
    def test_preprocess_collection_with_disabled_tabs(self, ):
        """
        Tests that preprocess collection returns correct data if `course_structure` and `tags` are disabled.
        """
        initial_collection = [
            {
                "quote": "quote text1",
                "text": "text1",
                "usage_id": str(self.html_block_1.location),
                "updated": datetime(2016, 1, 26, 8, 5, 16, 00000).isoformat(),
            },
            {
                "quote": "quote text2",
                "text": "text2",
                "usage_id": str(self.html_block_2.location),
                "updated": datetime(2016, 1, 26, 9, 6, 17, 00000).isoformat(),
            },
        ]

        assert len(
            [
                {

                    'section': {},
                    'chapter': {},
                    "unit": {
                        "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                        "display_name": self.vertical.display_name_with_default,
                        "location": str(self.vertical.location),
                    },
                    'text': 'text1',
                    'quote': 'quote text1',
                    'usage_id': str(self.html_block_1.location),
                    'updated': datetime(2016, 1, 26, 8, 5, 16)
                },
                {
                    'section': {},
                    'chapter': {},
                    "unit": {
                        "url": self._get_unit_url(self.course, self.chapter, self.sequential),
                        "display_name": self.vertical.display_name_with_default,
                        "location": str(self.vertical.location),
                    },
                    'text': 'text2',
                    'quote': 'quote text2',
                    'usage_id': str(self.html_block_2.location),
                    'updated': datetime(2016, 1, 26, 9, 6, 17)
                }
            ]) == len(helpers.preprocess_collection(self.user, self.course, initial_collection))

    def test_get_block_context_sequential(self):
        """
        Tests `get_block_context` method for the sequential.
        """
        self.assertDictEqual(
            {
                "display_name": self.sequential.display_name_with_default,
                "location": str(self.sequential.location),
                "children": [str(self.vertical.location), str(self.vertical_with_container.location)],
            },
            helpers.get_block_context(self.course, self.sequential)
        )

    def test_get_block_context_html_component(self):
        """
        Tests `get_block_context` method for the components.
        """
        self.assertDictEqual(
            {
                "display_name": self.html_block_1.display_name_with_default,
                "location": str(self.html_block_1.location),
            },
            helpers.get_block_context(self.course, self.html_block_1)
        )

    def test_get_block_context_chapter(self):
        """
        Tests `get_block_context` method for the chapters.
        """
        self.assertDictEqual(
            {
                "display_name": self.chapter.display_name_with_default,
                "index": 0,
                "location": str(self.chapter.location),
                "children": [str(self.sequential.location)],
            },
            helpers.get_block_context(self.course, self.chapter)
        )
        self.assertDictEqual(
            {
                "display_name": self.chapter_2.display_name_with_default,
                "index": 1,
                "location": str(self.chapter_2.location),
                "children": [],
            },
            helpers.get_block_context(self.course, self.chapter_2)
        )

    @override_settings(EDXNOTES_PUBLIC_API="http://example.com")
    @override_settings(EDXNOTES_INTERNAL_API="http://example.com")
    @patch("lms.djangoapps.edxnotes.helpers.anonymous_id_for_user", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.get_edxnotes_id_token", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_send_request_with_text_param(self, mock_get, mock_get_id_token, mock_anonymous_id_for_user):
        """
        Tests that requests are send with correct information.
        """
        mock_get_id_token.return_value = "test_token"
        mock_anonymous_id_for_user.return_value = "anonymous_id"
        helpers.send_request(
            self.user,
            self.course.id,
            path="test",
            text="text",
            page=helpers.DEFAULT_PAGE,
            page_size=helpers.DEFAULT_PAGE_SIZE
        )
        mock_get.assert_called_with(
            "http://example.com/test/",
            headers={
                "x-annotator-auth-token": "test_token"
            },
            params={
                "user": "anonymous_id",
                "course_id": str(self.course.id),
                "text": "text",
                "highlight": True,
                'page': 1,
                'page_size': 25,
            },
            timeout=(settings.EDXNOTES_CONNECT_TIMEOUT, settings.EDXNOTES_READ_TIMEOUT)
        )

    @override_settings(EDXNOTES_PUBLIC_API="http://example.com")
    @override_settings(EDXNOTES_INTERNAL_API="http://example.com")
    @patch("lms.djangoapps.edxnotes.helpers.anonymous_id_for_user", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.get_edxnotes_id_token", autospec=True)
    @patch("lms.djangoapps.edxnotes.helpers.requests.get", autospec=True)
    def test_send_request_without_text_param(self, mock_get, mock_get_id_token, mock_anonymous_id_for_user):
        """
        Tests that requests are send with correct information.
        """
        mock_get_id_token.return_value = "test_token"
        mock_anonymous_id_for_user.return_value = "anonymous_id"
        helpers.send_request(
            self.user, self.course.id, path="test", page=1, page_size=25
        )
        mock_get.assert_called_with(
            "http://example.com/test/",
            headers={
                "x-annotator-auth-token": "test_token"
            },
            params={
                "user": "anonymous_id",
                "course_id": str(self.course.id),
                'page': helpers.DEFAULT_PAGE,
                'page_size': helpers.DEFAULT_PAGE_SIZE,
            },
            timeout=(settings.EDXNOTES_CONNECT_TIMEOUT, settings.EDXNOTES_READ_TIMEOUT)
        )

    def test_get_course_position_no_section(self):
        """
        Returns `None` if no section found.
        """
        mock_course_block = MagicMock()
        mock_course_block.position = 3
        mock_course_block.get_children.return_value = []
        assert helpers.get_course_position(mock_course_block) is None

    def test_get_course_position_to_section(self):
        """
        Returns a position that leads to COURSE/SECTION if this isn't the users's
        first time.
        """
        mock_course_block = MagicMock(id=self.course.id, position=3)

        mock_section = MagicMock()
        mock_section.url_name = 'section_url_name'
        mock_section.display_name_with_default = 'Test Chapter Display Name'

        mock_course_block.get_children.return_value = [mock_section]

        assert helpers.get_course_position(mock_course_block) == {
            'display_name': 'Test Chapter Display Name',
            'url': f'/courses/{self.course.id}/courseware/section_url_name/',
        }

    def test_get_course_position_no_subsection(self):
        """
        Returns `None` if no section found.
        """
        mock_course_block = MagicMock(id=self.course.id, position=None)
        mock_course_block.get_children.return_value = [MagicMock()]
        assert helpers.get_course_position(mock_course_block) is None

    def test_get_course_position_to_subsection(self):
        """
        Returns a position that leads to COURSE/SECTION/SUBSECTION if this is the
        user's first time.
        """
        mock_course_block = MagicMock(id=self.course.id, position=None)

        mock_section = MagicMock()
        mock_section.url_name = 'section_url_name'
        mock_course_block.get_children.return_value = [mock_section]

        mock_subsection = MagicMock()
        mock_subsection.url_name = 'subsection_url_name'
        mock_subsection.display_name_with_default = 'Test Section Display Name'

        mock_section.get_children.return_value = [mock_subsection]
        mock_subsection.get_children.return_value = [MagicMock()]

        assert helpers.get_course_position(mock_course_block) == {
            'display_name': 'Test Section Display Name',
            'url': f'/courses/{self.course.id}/courseware/section_url_name/subsection_url_name/',
        }

    def test_get_index(self):
        """
        Tests `get_index` method returns unit url.
        """
        children = self.sequential.children
        assert 0 == helpers.get_index(str(self.vertical.location), children)
        assert 1 == helpers.get_index(str(self.vertical_with_container.location), children)

    @ddt.unpack
    @ddt.data(
        {'previous_api_url': None, 'next_api_url': None},
        {'previous_api_url': None, 'next_api_url': 'edxnotes/?course_id=abc&page=2&page_size=10&user=123'},
        {'previous_api_url': 'edxnotes.org/?course_id=abc&page=2&page_size=10&user=123', 'next_api_url': None},
        {
            'previous_api_url': 'edxnotes.org/?course_id=abc&page_size=10&user=123',
            'next_api_url': 'edxnotes.org/?course_id=abc&page=3&page_size=10&user=123'
        },
        {
            'previous_api_url': 'edxnotes.org/?course_id=abc&page=2&page_size=10&text=wow&user=123',
            'next_api_url': 'edxnotes.org/?course_id=abc&page=4&page_size=10&text=wow&user=123'
        },
    )
    def test_construct_url(self, previous_api_url, next_api_url):
        """
        Verify that `construct_url` works correctly.
        """
        # make absolute url
        if self.request.is_secure():
            host = 'https://' + self.request.get_host()
        else:
            host = 'http://' + self.request.get_host()
        notes_url = host + reverse("notes", args=[str(self.course.id)])

        def verify_url(constructed, expected):
            """
            Verify that constructed url is correct.
            """
            # if api url is None then constructed url should also be None
            if expected is None:
                assert expected == constructed
            else:
                # constructed url should startswith notes view url instead of api view url
                assert constructed.startswith(notes_url)

                # constructed url should not contain extra params
                assert 'user' not in constructed

                # constructed url should only has these params if present in api url
                allowed_params = ('page', 'page_size', 'text')

                # extract query params from constructed url
                parsed = urlparse(constructed)
                params = parse_qs(parsed.query)

                # verify that constructed url has only correct params and params have correct values
                for param, value in params.items():
                    assert param in allowed_params
                    assert f'{param}={value[0]}' in expected

        next_url, previous_url = helpers.construct_pagination_urls(
            self.request,
            self.course.id,
            next_api_url, previous_api_url
        )
        verify_url(next_url, next_api_url)
        verify_url(previous_url, previous_api_url)


@skipUnless(settings.FEATURES["ENABLE_EDXNOTES"], "EdxNotes feature needs to be enabled.")
@ddt.ddt
class EdxNotesViewsTest(ModuleStoreTestCase):
    """
    Tests for EdxNotes views.
    """
    def setUp(self):
        ApplicationFactory(name="edx-notes")
        super().setUp()
        self.course = CourseFactory(edxnotes=True)
        self.user = UserFactory()
        CourseEnrollmentFactory(user=self.user, course_id=self.course.id)  # lint-amnesty, pylint: disable=no-member
        self.client.login(username=self.user.username, password=UserFactory._DEFAULT_PASSWORD)  # lint-amnesty, pylint: disable=protected-access
        self.notes_page_url = reverse("edxnotes", args=[str(self.course.id)])  # lint-amnesty, pylint: disable=no-member
        self.notes_url = reverse("notes", args=[str(self.course.id)])  # lint-amnesty, pylint: disable=no-member
        self.get_token_url = reverse("get_token", args=[str(self.course.id)])  # lint-amnesty, pylint: disable=no-member
        self.visibility_url = reverse("edxnotes_visibility", args=[str(self.course.id)])  # lint-amnesty, pylint: disable=no-member

    def _get_course_block(self):
        """
        Returns the course block.
        """
        field_data_cache = FieldDataCache([self.course], self.course.id, self.user)  # lint-amnesty, pylint: disable=no-member
        return get_block_for_descriptor(
            self.user, MagicMock(), self.course, field_data_cache, self.course.id, course=self.course  # lint-amnesty, pylint: disable=no-member
        )

    def test_edxnotes_tab(self):
        """
        Tests that edxnotes tab is shown only when the feature is enabled.
        """
        def has_notes_tab(user, course):
            """Returns true if the "Notes" tab is shown."""
            tabs = get_course_tab_list(user, course)
            return len([tab for tab in tabs if tab.type == 'edxnotes']) == 1

        assert not has_notes_tab(self.user, self.course)
        enable_edxnotes_for_the_course(self.course, self.user.id)
        # disable course.edxnotes
        self.course.edxnotes = False
        assert not has_notes_tab(self.user, self.course)

        # reenable course.edxnotes
        self.course.edxnotes = True
        assert has_notes_tab(self.user, self.course)

    # pylint: disable=unused-argument
    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    @patch("lms.djangoapps.edxnotes.views.get_notes", return_value={'results': []})
    def test_edxnotes_view_is_enabled(self, mock_get_notes):
        """
        Tests that appropriate view is received if EdxNotes feature is enabled.
        """
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.get(self.notes_page_url)
        self.assertContains(response, 'Highlights and notes you&#39;ve made in course content')

    # pylint: disable=unused-argument
    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    @patch("lms.djangoapps.edxnotes.views.get_notes", return_value={'results': []})
    @patch("lms.djangoapps.edxnotes.views.get_course_position", return_value={
        'display_name': 'Section 1',
        'url': 'test_url'
    })
    def test_edxnotes_html_tags_should_not_be_escaped(self, mock_get_notes, mock_position):
        """
        Tests that explicit html tags rendered correctly.
        """
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.get(self.notes_page_url)
        self.assertContains(
            response,
            'Get started by making a note in something you just read, like <a href="test_url">Section 1</a>'
        )

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": False})
    def test_edxnotes_view_is_disabled(self):
        """
        Tests that 404 status code is received if EdxNotes feature is disabled.
        """
        response = self.client.get(self.notes_page_url)
        assert response.status_code == 404

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    @patch("lms.djangoapps.edxnotes.views.get_notes", autospec=True)
    def test_search_notes_successfully_respond(self, mock_search):
        """
        Tests that search notes successfully respond if EdxNotes feature is enabled.
        """
        mock_search.return_value = NOTES_VIEW_EMPTY_RESPONSE
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.get(self.notes_url, {"text": "test"})
        assert json.loads(response.content.decode('utf-8')) == NOTES_VIEW_EMPTY_RESPONSE
        assert response.status_code == 200

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": False})
    def test_search_notes_is_disabled(self):
        """
        Tests that 404 status code is received if EdxNotes feature is disabled.
        """
        response = self.client.get(self.notes_url, {"text": "test"})
        assert response.status_code == 404

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    @patch("lms.djangoapps.edxnotes.views.get_notes", autospec=True)
    def test_search_500_service_unavailable(self, mock_search):
        """
        Tests that 500 status code is received if EdxNotes service is unavailable.
        """
        mock_search.side_effect = EdxNotesServiceUnavailable
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.get(self.notes_url, {"text": "test"})
        self.assertContains(response, "error", status_code=500)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    @patch("lms.djangoapps.edxnotes.views.get_notes", autospec=True)
    def test_search_notes_exception(self, mock_search):
        """
        Tests that 500 status code is received if invalid data was received from
        EdXNotes service.
        """
        mock_search.side_effect = EdxNotesParseError
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.get(self.notes_url, {"text": "test"})
        self.assertContains(response, "error", status_code=500)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    def test_get_id_token(self):
        """
        Test generation of ID Token.
        """
        response = self.client.get(self.get_token_url)
        assert response.status_code == 200
        client = Application.objects.get(name='edx-notes')
        jwt.decode(
            response.content,
            client.client_secret,
            audience=client.client_id,
            algorithms=[settings.JWT_AUTH['JWT_ALGORITHM']]
        )

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    def test_get_id_token_anonymous(self):
        """
        Test that generation of ID Token does not work for anonymous user.
        """
        self.client.logout()
        response = self.client.get(self.get_token_url)
        assert response.status_code == 302

    def test_edxnotes_visibility(self):
        """
        Can update edxnotes_visibility value successfully.
        """
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.post(
            self.visibility_url,
            data=json.dumps({"visibility": False}),
            content_type="application/json",
        )
        assert response.status_code == 200
        course_block = self._get_course_block()
        assert not course_block.edxnotes_visibility

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": False})
    def test_edxnotes_visibility_if_feature_is_disabled(self):
        """
        Tests that 404 response is received if EdxNotes feature is disabled.
        """
        response = self.client.post(self.visibility_url)
        assert response.status_code == 404

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    def test_edxnotes_visibility_invalid_json(self):
        """
        Tests that 400 response is received if invalid JSON is sent.
        """
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.post(
            self.visibility_url,
            data="string",
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_EDXNOTES": True})
    def test_edxnotes_visibility_key_error(self):
        """
        Tests that 400 response is received if invalid data structure is sent.
        """
        enable_edxnotes_for_the_course(self.course, self.user.id)
        response = self.client.post(
            self.visibility_url,
            data=json.dumps({'test_key': 1}),
            content_type="application/json",
        )
        assert response.status_code == 400


class EdxNotesRetireAPITest(ModuleStoreTestCase):
    """
    Tests for EdxNotes retirement API.
    """
    def setUp(self):
        ApplicationFactory(name="edx-notes")
        super().setUp()

        # setup relevant states
        RetirementState.objects.create(state_name='PENDING', state_execution_order=1)
        self.retire_notes_state = RetirementState.objects.create(state_name='RETIRING_NOTES', state_execution_order=11)
        self.something_complete_state = RetirementState.objects.create(
            state_name='SOMETHING_COMPLETE',
            state_execution_order=22,
        )

        # setup retired user with retirement status
        self.retired_user = UserFactory()
        self.retirement = UserRetirementStatus.create_retirement(self.retired_user)
        self.retirement.current_state = self.retire_notes_state
        self.retirement.save()

        # setup another normal user which should not be allowed to retire any notes
        self.normal_user = UserFactory()

        # setup superuser for making API calls
        self.superuser = SuperuserFactory()

        self.retire_user_url = reverse("edxnotes_retire_user")

    def _build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {'HTTP_AUTHORIZATION': 'JWT ' + token}
        return headers

    @patch("lms.djangoapps.edxnotes.helpers.requests.post", autospec=True)
    def test_retire_user_success(self, mock_post):
        """
        Tests that 204 response is received on success.
        """
        mock_post.return_value.content = b''
        mock_post.return_value.status_code = 204
        headers = self._build_jwt_headers(self.superuser)
        response = self.client.post(
            self.retire_user_url,
            data=json.dumps({'username': self.retired_user.username}),
            content_type='application/json',
            **headers
        )
        assert response.status_code == 204

    def test_retire_user_normal_user_not_allowed(self):
        """
        Tests that 403 response is received when the requester is not allowed to call the retirement endpoint.
        """
        headers = self._build_jwt_headers(self.normal_user)
        response = self.client.post(
            self.retire_user_url,
            data=json.dumps({'username': self.retired_user.username}),
            content_type='application/json',
            **headers
        )
        assert response.status_code == 403

    def test_retire_user_status_not_found(self):
        """
        Tests that 404 response is received if the retirement user status is not found.
        """
        headers = self._build_jwt_headers(self.superuser)
        response = self.client.post(
            self.retire_user_url,
            data=json.dumps({'username': 'username_does_not_exist'}),
            content_type='application/json',
            **headers
        )
        assert response.status_code == 404

    def test_retire_user_wrong_state(self):
        """
        Tests that 405 response is received if the retirement user status is currently in a state which cannot be acted
        on.
        """
        # Set state to the _COMPLETE version of an arbitrary "SOMETHING" state.
        self.retirement.current_state = self.something_complete_state
        self.retirement.save()
        headers = self._build_jwt_headers(self.superuser)
        response = self.client.post(
            self.retire_user_url,
            data=json.dumps({'username': self.retired_user.username}),
            content_type='application/json',
            **headers
        )
        assert response.status_code == 405

    @patch("lms.djangoapps.edxnotes.helpers.delete_all_notes_for_user", autospec=True)
    def test_retire_user_downstream_unavailable(self, mock_delete_all_notes_for_user):
        """
        Tests that 500 response is received if the downstream (i.e. the EdxNotes IDA) is unavailable.
        """
        mock_delete_all_notes_for_user.side_effect = EdxNotesServiceUnavailable
        headers = self._build_jwt_headers(self.superuser)
        response = self.client.post(
            self.retire_user_url,
            data=json.dumps({'username': self.retired_user.username}),
            content_type='application/json',
            **headers
        )
        assert response.status_code == 500


@skipUnless(settings.FEATURES["ENABLE_EDXNOTES"], "EdxNotes feature needs to be enabled.")
@ddt.ddt
class EdxNotesPluginTest(ModuleStoreTestCase):
    """
    EdxNotesTab tests.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(edxnotes=True)
        self.user = UserFactory()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def test_edxnotes_tab_with_unenrolled_user(self):
        user = UserFactory()
        assert not EdxNotesTab.is_enabled(self.course, user=user)

    @ddt.data(True, False)
    def test_edxnotes_tab_with_feature_flag(self, enabled):
        """
        Verify EdxNotesTab visibility when ENABLE_EDXNOTES feature flag is enabled/disabled.
        """
        FEATURES['ENABLE_EDXNOTES'] = enabled
        with override_settings(FEATURES=FEATURES):
            assert EdxNotesTab.is_enabled(self.course, self.user) == enabled
