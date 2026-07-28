"""Tests for user API middleware"""


from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import TestCase
from django.test.client import RequestFactory
from django.urls import reverse

from common.djangoapps.student.tests.factories import AnonymousUserFactory, UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

from ..middleware import UserTagsEventContextMiddleware
from ..tests.factories import UserCourseTagFactory


# This middleware only gets installed in the LMS so no need to test
# it in the CMS context.
@skip_unless_lms
class TagsMiddlewareTest(TestCase):
    """
    Test the UserTagsEventContextMiddleware
    """
    def setUp(self):
        super().setUp()
        self.middleware = UserTagsEventContextMiddleware(get_response=lambda request: None)
        self.user = UserFactory.create()
        self.other_user = UserFactory.create()

        self.course_id = 'mock/course/id'
        self.request_factory = RequestFactory()

        self.request = RequestFactory().get(reverse('progress', kwargs={'course_id': self.course_id}))
        self.request.user = self.user

        self.response = Mock(spec=HttpResponse)

        patcher = patch('openedx.core.djangoapps.user_api.middleware.tracker')
        self.tracker = patcher.start()
        self.addCleanup(patcher.stop)

    def process_request(self):
        """
        Execute process request using the request, and verify that it returns None
        so that the request continues.
        """
        # Middleware should pass request through
        assert self.middleware.process_request(self.request) is None

    def assertContextSetTo(self, context):
        """Asserts UserTagsEventContextMiddleware.CONTEXT_NAME matches ``context``"""
        self.tracker.get_tracker.return_value.enter_context.assert_called_with(
            UserTagsEventContextMiddleware.CONTEXT_NAME,
            context
        )

    def test_tag_context(self):
        for key, value in (('int_value', 1), ('str_value', "two")):
            UserCourseTagFactory.create(
                course_id=self.course_id,
                user=self.user,
                key=key,
                value=value,
            )

        UserCourseTagFactory.create(
            course_id=self.course_id,
            user=self.other_user,
            key="other_user",
            value="other_user_value"
        )

        UserCourseTagFactory.create(
            course_id='other/course/id',
            user=self.user,
            key="other_course",
            value="other_course_value"
        )

        self.process_request()
        self.assertContextSetTo({
            'course_id': self.course_id,
            'course_user_tags': {
                'int_value': '1',
                'str_value': 'two',
            }
        })

    def test_no_tags(self):
        self.process_request()
        self.assertContextSetTo({'course_id': self.course_id, 'course_user_tags': {}})

    def test_not_course_url(self):
        self.request = self.request_factory.get('/not/a/course/url')
        self.request.user = self.user

        self.process_request()

        self.assertContextSetTo({})

    def test_invalid_course_id(self):
        self.request = self.request_factory.get('/courses/edX/101/')
        self.request.user = self.user

        self.process_request()
        self.assertContextSetTo({})

    def test_anonymous_user(self):
        self.request.user = AnonymousUserFactory()

        self.process_request()

        self.assertContextSetTo({'course_id': self.course_id, 'course_user_tags': {}})

    def test_remove_context(self):
        get_tracker = self.tracker.get_tracker
        exit_context = get_tracker.return_value.exit_context

        # The middleware should clean up the context when the request is done
        assert self.middleware.process_response(self.request, self.response) == self.response
        exit_context.assert_called_with(UserTagsEventContextMiddleware.CONTEXT_NAME)
        exit_context.reset_mock()

        # Even if the tracker blows up, the middleware should still return the response
        get_tracker.side_effect = Exception
        assert self.middleware.process_response(self.request, self.response) == self.response
