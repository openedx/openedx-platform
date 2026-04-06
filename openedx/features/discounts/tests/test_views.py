"""Tests of openedx.features.discounts.views"""


import jwt
from django.test.client import Client
from django.urls import reverse

from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestCourseUserDiscount(ModuleStoreTestCase):
    """
    CourseUserDiscount should return a jwt with the information if this combination of user and
    course can receive a discount, and how much that discount should be.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.course = CourseFactory.create(run='test', display_name='test')
        self.client = Client()
        self.url = reverse(
            'api_discounts:course_user_discount',
            kwargs={'course_key_string': str(self.course.id)}
        )

    def test_url(self):
        """
        Test that the url hasn't changed
        """
        assert self.url == ('/api/discounts/course/' + str(self.course.id))

    def test_course_user_discount(self):
        """
        Test that the api returns a jwt with the discount information
        """
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        # the endpoint should return a 200 if all goes well
        response = self.client.get(self.url)
        assert response.status_code == 200

        # for now, it should always return false
        expected_payload = {'discount_applicable': False, 'discount_percent': 15}
        assert expected_payload['discount_applicable'] == response.data['discount_applicable']

        # make sure that the response matches the expected response
        response_payload = jwt.decode(response.data['jwt'], options={"verify_signature": False})
        assert all(item in list(response_payload.items()) for item in expected_payload.items())

    def test_course_user_discount_no_user(self):
        """
        Test that the endpoint returns a 401 if there is no user signed in
        """
        # the endpoint should return a 401 because the user is not logged in
        response = self.client.get(self.url)
        assert response.status_code == 401
