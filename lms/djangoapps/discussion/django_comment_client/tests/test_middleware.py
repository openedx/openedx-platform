# pylint: disable=missing-module-docstring
import json

import django.http
from django.test import TestCase

import lms.djangoapps.discussion.django_comment_client.middleware as middleware
import openedx.core.djangoapps.django_comment_common.comment_client as comment_client


class AjaxExceptionTestCase(TestCase):  # pylint: disable=missing-class-docstring

    def setUp(self):
        super().setUp()
        self.a = middleware.AjaxExceptionMiddleware(get_response=lambda request: None)
        self.request1 = django.http.HttpRequest()
        self.request0 = django.http.HttpRequest()
        self.exception1 = comment_client.CommentClientRequestError('{}', 401)
        self.exception2 = comment_client.CommentClientRequestError('Foo!', 404)
        self.exception0 = comment_client.CommentClient500Error("Holy crap the server broke!")
        self.request1.META['HTTP_X_REQUESTED_WITH'] = "XMLHttpRequest"
        self.request0.META['HTTP_X_REQUESTED_WITH'] = "SHADOWFAX"

    def test_process_exception(self):
        response1 = self.a.process_exception(self.request1, self.exception1)
        assert isinstance(response1, middleware.JsonError)
        assert self.exception1.status_code == response1.status_code
        assert {'errors': json.loads(str(self.exception1))} == json.loads(response1.content.decode('utf-8'))

        response2 = self.a.process_exception(self.request1, self.exception2)
        assert isinstance(response2, middleware.JsonError)
        assert self.exception2.status_code == response2.status_code
        assert {'errors': [str(self.exception2)]} == json.loads(response2.content.decode('utf-8'))

        assert self.a.process_exception(self.request1, self.exception0) is None
        assert self.a.process_exception(self.request0, self.exception1) is None
        assert self.a.process_exception(self.request0, self.exception0) is None
