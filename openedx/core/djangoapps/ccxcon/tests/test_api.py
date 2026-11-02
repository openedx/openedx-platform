"""
Unit tests for the API module
"""
import datetime
from unittest import mock
from urllib import parse
from zoneinfo import ZoneInfo

import pytest
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory

from openedx.core.djangoapps.ccxcon import api as ccxconapi
from common.djangoapps.student.tests.factories import AdminFactory

from .factories import CcxConFactory


def flatten(seq):
    """
    For [[1, 2], [3, 4]] returns [1, 2, 3, 4].  Does not recurse.
    """
    return [x for sub in seq for x in sub]


def fetch_token_mock(*args, **kwargs):  # pylint: disable=unused-argument
    """
    Mock function used to bypass the oauth fetch token
    """
    return


class APIsTestCase(SharedModuleStoreTestCase):
    """
    Unit tests for the API module functions
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = course = CourseFactory.create()
        cls.course_key = cls.course.location.course_key

        # Create a course outline
        start = datetime.datetime(
            2010, 5, 12, 2, 42, tzinfo=ZoneInfo("UTC")
        )
        due = datetime.datetime(
            2010, 7, 7, 0, 0, tzinfo=ZoneInfo("UTC")
        )

        cls.chapters = [
            BlockFactory.create(start=start, parent=course) for _ in range(2)
        ]
        cls.sequentials = flatten([
            [
                BlockFactory.create(parent=chapter) for _ in range(2)
            ] for chapter in cls.chapters
        ])
        cls.verticals = flatten([
            [
                BlockFactory.create(
                    start=start, due=due, parent=sequential, graded=True, format='Homework', category='vertical'
                ) for _ in range(2)
            ] for sequential in cls.sequentials
        ])

        # Trying to wrap the whole thing in a bulk operation fails because it
        # doesn't find the parents. But we can at least wrap this part...
        with cls.store.bulk_operations(course.id, emit_signals=False):
            blocks = flatten([  # pylint: disable=unused-variable
                [
                    BlockFactory.create(parent=vertical) for _ in range(2)
                ] for vertical in cls.verticals
            ])

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        # Create instructor account
        self.instructor = AdminFactory.create()
        # create an instance of modulestore
        self.mstore = modulestore()
        # enable ccx
        self.course.enable_ccx = True
        # setup CCX connector
        self.course.ccx_connector = 'https://url.to.cxx.connector.mit.edu'
        # save the changes
        self.mstore.update_item(self.course, self.instructor.id)
        # create a configuration for the ccx connector: this must match the one in the course
        self.ccxcon_conf = CcxConFactory(url=self.course.ccx_connector)

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_no_valid_course_key(self, mock_post):
        """
        Test for an invalid course key
        """
        missing_course_key = CourseKey.from_string('course-v1:FakeOrganization+CN999+CR-FALL99')
        assert ccxconapi.course_info_to_ccxcon(missing_course_key) is None
        assert mock_post.call_count == 0

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_no_ccx_enabled(self, mock_post):
        """
        Test for a course without CCX enabled
        """
        self.course.enable_ccx = False
        self.mstore.update_item(self.course, self.instructor.id)
        assert ccxconapi.course_info_to_ccxcon(self.course_key) is None
        assert mock_post.call_count == 0

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_invalid_ccx_connector(self, mock_post):
        """
        Test for a course with invalid CCX connector URL
        """
        # no connector at all
        self.course.ccx_connector = ""
        self.mstore.update_item(self.course, self.instructor.id)
        assert ccxconapi.course_info_to_ccxcon(self.course_key) is None
        assert mock_post.call_count == 0
        # invalid url
        self.course.ccx_connector = "www.foo"
        self.mstore.update_item(self.course, self.instructor.id)
        assert ccxconapi.course_info_to_ccxcon(self.course_key) is None
        assert mock_post.call_count == 0

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_no_config(self, mock_post):
        """
        Test for course with ccx connector credentials not configured
        """
        self.course.ccx_connector = "https://www.foo.com"
        self.mstore.update_item(self.course, self.instructor.id)
        assert ccxconapi.course_info_to_ccxcon(self.course_key) is None
        assert mock_post.call_count == 0

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_ok(self, mock_post):
        """
        Test for happy path
        """
        mock_response = mock.Mock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        ccxconapi.course_info_to_ccxcon(self.course_key)

        assert mock_post.call_count == 1
        k_args, k_kwargs = mock_post.call_args
        # no args used for the call
        assert k_args == tuple()
        assert k_kwargs.get('url') ==\
               parse.urljoin(self.course.ccx_connector, ccxconapi.CCXCON_COURSEXS_URL)

        # second call with different status code
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        ccxconapi.course_info_to_ccxcon(self.course_key)

        assert mock_post.call_count == 2
        k_args, k_kwargs = mock_post.call_args
        # no args used for the call
        assert k_args == tuple()
        assert k_kwargs.get('url') ==\
               parse.urljoin(self.course.ccx_connector, ccxconapi.CCXCON_COURSEXS_URL)

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_500_error(self, mock_post):
        """
        Test for 500 error: a CCXConnServerError exception is raised
        """
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        with pytest.raises(ccxconapi.CCXConnServerError):
            ccxconapi.course_info_to_ccxcon(self.course_key)

    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.fetch_token', fetch_token_mock)
    @mock.patch('requests_oauthlib.oauth2_session.OAuth2Session.post')
    def test_course_info_to_ccxcon_other_status_codes(self, mock_post):
        """
        Test for status codes different from >= 500 and 201:
        The called function doesn't raise any exception and simply returns None.
        """
        mock_response = mock.Mock()
        for status_code in (204, 300, 304, 400, 404):
            mock_response.status_code = status_code
            mock_post.return_value = mock_response
            assert ccxconapi.course_info_to_ccxcon(self.course_key) is None
