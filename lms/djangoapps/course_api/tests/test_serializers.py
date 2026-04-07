"""
Test data created by CourseSerializer and CourseDetailSerializer
"""

from datetime import datetime
from unittest import TestCase, mock

import ddt
from opaque_keys.edx.locator import CourseLocator
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from xblock.core import XBlock

from lms.djangoapps.certificates.api import can_show_certificate_available_date_field
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.models.course_details import CourseDetails
from xmodule.course_block import DEFAULT_START_DATE
from xmodule.modulestore.tests.django_utils import TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import check_mongo_calls

from ..serializers import CourseDetailSerializer, CourseKeySerializer, CourseSerializer
from .mixins import CourseApiFactoryMixin


@ddt.ddt
class TestCourseSerializer(CourseApiFactoryMixin, ModuleStoreTestCase):
    """
    Test CourseSerializer
    """
    expected_mongo_calls = 0
    maxDiff = 5000  # long enough to show mismatched dicts, in case of error
    serializer_class = CourseSerializer

    MODULESTORE = TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        self.staff_user = self.create_user('staff', is_staff=True)
        self.honor_user = self.create_user('honor', is_staff=False)
        self.request_factory = APIRequestFactory()

        course_id = 'course-v1:edX+toy+2012_Fall'
        banner_image_uri = '/asset-v1:edX+toy+2012_Fall+type@asset+block@images_course_image.jpg'
        banner_image_absolute_uri = 'http://testserver' + banner_image_uri
        image_path = '/asset-v1:edX+toy+2012_Fall+type@asset+block@just_a_test.jpg'
        image_url = 'http://testserver' + image_path
        self.expected_data = {
            'id': course_id,
            'name': 'Toy Course',
            'number': 'toy',
            'org': 'edX',
            'short_description': 'A course about toys.',
            'media': {
                'banner_image': {
                    'uri': banner_image_uri,
                    'uri_absolute': banner_image_absolute_uri
                },
                'course_image': {'uri': image_path},
                'course_video': {'uri': 'http://www.youtube.com/watch?v=test_youtube_id'},
                'image': {
                    'raw': image_url,
                    'small': image_url,
                    'large': image_url
                }
            },
            'start': '2015-07-17T12:00:00Z',
            'start_type': 'timestamp',
            'start_display': 'July 17, 2015',
            'end': '2015-09-19T18:00:00Z',
            'enrollment_start': '2015-06-15T00:00:00Z',
            'enrollment_end': '2015-07-15T00:00:00Z',
            'blocks_url': 'http://testserver/api/courses/v2/blocks/?course_id=course-v1%3AedX%2Btoy%2B2012_Fall',
            'effort': '6 hours',
            'pacing': 'instructor',
            'mobile_available': True,
            'hidden': False,
            'invitation_only': False,

            # 'course_id' is a deprecated field, please use 'id' instead.
            'course_id': course_id,
        }

    def _get_request(self, user=None):
        """
        Build a Request object for the specified user.
        """
        if user is None:
            user = self.honor_user
        request = Request(self.request_factory.get('/'))
        request.user = user
        return request

    def _get_result(self, course):
        """
        Return the CourseSerializer for the specified course.
        """
        course_overview = CourseOverview.get_from_id(course.id)
        return self.serializer_class(course_overview, context={'request': self._get_request()}).data

    def test_basic(self):
        course = self.create_course()
        CourseDetails.update_about_video(course, 'test_youtube_id', self.staff_user.id)
        with check_mongo_calls(self.expected_mongo_calls):
            result = self._get_result(course)
        self.assertDictEqual(result, self.expected_data)

    def test_hidden(self):
        course = self.create_course(
            course='custom',
            start=datetime(2015, 3, 15),
            catalog_visibility='none'
        )
        result = self._get_result(course)
        assert result['hidden'] is True

    def test_advertised_start(self):
        course = self.create_course(
            course='custom',
            start=datetime(2015, 3, 15),
            advertised_start='The Ides of March'
        )
        result = self._get_result(course)
        assert result['course_id'] == 'course-v1:edX+custom+2012_Fall'
        assert result['start_type'] == 'string'
        assert result['start_display'] == 'The Ides of March'

    def test_empty_start(self):
        course = self.create_course(start=DEFAULT_START_DATE, course='custom')
        result = self._get_result(course)
        assert result['course_id'] == 'course-v1:edX+custom+2012_Fall'
        assert result['start_type'] == 'empty'
        assert result['start_display'] is None

    @ddt.unpack
    @ddt.data(
        (True, 'self'),
        (False, 'instructor'),
    )
    def test_pacing(self, self_paced, expected_pacing):
        course = self.create_course(self_paced=self_paced)
        result = self._get_result(course)
        assert result['pacing'] == expected_pacing


class TestCourseDetailSerializer(TestCourseSerializer):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test CourseDetailSerializer by rerunning all the tests
    in TestCourseSerializer, but with the
    CourseDetailSerializer serializer class.

    """
    # 1 mongo call is made to get the course About overview text.
    expected_mongo_calls = 2
    serializer_class = CourseDetailSerializer

    def setUp(self):
        super().setUp()

        # update the expected_data to include the 'overview' data.
        about_block = XBlock.load_class('about')
        overview_template = about_block.get_template('overview.yaml')
        self.expected_data['overview'] = overview_template.get('data')

    # override test case
    @mock.patch(
        "lms.djangoapps.certificates.api.can_show_certificate_available_date_field",
        mock.Mock(return_value=[True, False])
    )
    def test_basic(self):
        """
        Overridden from Test CourseDetailSerializer to
        test certificate_available_date according to waffle
        switch `certificates.auto_certificate_generation`
        from CourseDetailSerializer serializer class.
        """
        course = self.create_course()
        CourseDetails.update_about_video(course, 'test_youtube_id', self.staff_user.id)
        course_overview = CourseOverview.get_from_id(course.id)
        with check_mongo_calls(self.expected_mongo_calls):
            result = self._get_result(course)
        if can_show_certificate_available_date_field(course_overview):
            self.expected_data['certificate_available_date'] = '2015-08-14T00:00:00Z'
            result['certificate_available_date'] = (
                result['certificate_available_date'].strftime('%Y-%m-%dT%H:%M:%SZ')
                if isinstance(result['certificate_available_date'], datetime)
                else None
            )
        self.assertDictEqual(result, self.expected_data)

    @mock.patch('lms.djangoapps.course_api.serializers.CourseEnrollment.is_enrolled', return_value=True)
    def test_is_enrolled_field_true(self, mock_is_enrolled):
        course = self.create_course()
        result = self._get_result_with_query_param(course)
        assert result['is_enrolled'] is True
        mock_is_enrolled.assert_called_once()

    @mock.patch('lms.djangoapps.course_api.serializers.CourseEnrollment.is_enrolled', return_value=False)
    def test_is_enrolled_field_false(self, mock_is_enrolled):
        course = self.create_course()
        result = self._get_result_with_query_param(course)
        assert result['is_enrolled'] is False
        mock_is_enrolled.assert_called_once()

    def test_is_enrolled_field_anonymous_user(self):
        course = self.create_course()
        result = self._get_anonymous_result(course)
        self.assertNotIn('is_enrolled', result)

    def _get_anonymous_request(self):
        return Request(self.request_factory.get('/'))

    def _get_anonymous_result(self, course):
        course_overview = CourseOverview.get_from_id(course.id)
        return self.serializer_class(course_overview, context={'request': self._get_anonymous_request()}).data

    def _get_result_with_query_param(self, course):
        """
        Return the CourseSerializer for the specified course with 'username' in query params.
        """
        course_overview = CourseOverview.get_from_id(course.id)
        return self.serializer_class(course_overview, context={'request': self._get_request_with_query_param()}).data

    def _get_request_with_query_param(self, user=None):
        """
        Build a Request object for the specified user with 'username' in query params.
        """
        if user is None:
            user = self.honor_user
        request = Request(self.request_factory.get('/', {'username': user.username}))
        request.user = user
        return request


class TestCourseKeySerializer(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def test_course_key_serializer(self):
        course_key = CourseLocator(org='org', course='course', run='2020_Q3')
        serializer = CourseKeySerializer(course_key)
        assert serializer.data == str(course_key)
