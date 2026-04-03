"""
Unit tests for course optimizer
"""
from django.test import TestCase
from django.urls import reverse

from cms.djangoapps.contentstore.tests.test_utils import AuthorizeStaffTestCase
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class TestGetLinkCheckStatus(AuthorizeStaffTestCase, ModuleStoreTestCase, TestCase):
    '''
    Authentication and Authorization Tests for CourseOptimizer.
    For concrete tests that are run, check `AuthorizeStaffTestCase`.
    '''
    def make_request(self, course_id=None, data=None, **kwargs):
        url = self.get_url(self.course.id)
        response = self.client.get(url, data)
        return response

    def get_url(self, course_key):
        url = reverse(
            'cms.djangoapps.contentstore:v0:link_check_status',
            kwargs={'course_id': self.course.id}
        )
        return url

    def test_produces_4xx_when_invalid_course_id(self):
        '''
        Test course_id validation
        '''
        response = self.make_request(course_id='invalid_course_id')
        self.assertIn(response.status_code, range(400, 500))

    def test_produces_4xx_when_additional_kwargs(self):
        '''
        Test additional kwargs validation
        '''
        response = self.make_request(course_id=self.course.id, malicious_kwarg='malicious_kwarg')
        self.assertIn(response.status_code, range(400, 500))


class TestPostLinkCheck(AuthorizeStaffTestCase, ModuleStoreTestCase, TestCase):
    '''
    Authentication and Authorization Tests for CourseOptimizer.
    For concrete tests that are run, check `AuthorizeStaffTestCase`.
    '''
    def make_request(self, course_id=None, data=None, **kwargs):
        url = self.get_url(self.course.id)
        response = self.client.post(url, data)
        return response

    def get_url(self, course_key):
        url = reverse(
            'cms.djangoapps.contentstore:v0:link_check',
            kwargs={'course_id': self.course.id}
        )
        return url

    def test_produces_4xx_when_invalid_course_id(self):
        '''
        Test course_id validation
        '''
        response = self.make_request(course_id='invalid_course_id')
        self.assertIn(response.status_code, range(400, 500))

    def test_produces_4xx_when_additional_kwargs(self):
        '''
        Test additional kwargs validation
        '''
        response = self.make_request(course_id=self.course.id, malicious_kwarg='malicious_kwarg')
        self.assertIn(response.status_code, range(400, 500))

    def test_produces_4xx_when_unexpected_data(self):
        '''
        Test validation when request contains unexpected data
        '''
        response = self.make_request(course_id=self.course.id, data={'unexpected_data': 'unexpected_data'})
        self.assertIn(response.status_code, range(400, 500))
