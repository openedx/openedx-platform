""" Tests for v1 User Tour views. """

import ddt
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework import status

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.user_tours.handlers import init_user_tour
from lms.djangoapps.user_tours.models import UserDiscussionsTours, UserTour
from lms.djangoapps.user_tours.toggles import USER_TOURS_DISABLED
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user

User = get_user_model()


def build_jwt_headers(user):
    """ Helper function for creating headers for the JWT authentication. """
    token = create_jwt_for_user(user)
    headers = {'HTTP_AUTHORIZATION': f'JWT {token}'}
    return headers


@ddt.ddt
class TestUserTourView(TestCase):
    """ Tests for the v1 User Tour views. """

    def setUp(self):
        """ Test set up. """
        super().setUp()
        self.user = UserFactory()
        self.existing_user_tour = self.user.tour
        self.existing_user_tour.course_home_tour_status = UserTour.CourseHomeChoices.EXISTING_USER_TOUR
        self.existing_user_tour.show_courseware_tour = False
        self.existing_user_tour.save()

        self.staff_user = UserFactory(is_staff=True)
        self.new_user_tour = self.staff_user.tour

    def send_request(self, jwt_user, request_user, method, data=None):
        """ Helper function to call API. """
        headers = build_jwt_headers(jwt_user)
        url = reverse('user-tours', args=[request_user.username])
        if method == 'GET':
            return self.client.get(url, **headers)
        elif method == 'PATCH':
            return self.client.patch(url, data, content_type='application/json', **headers)

    @ddt.data('GET', 'PATCH')
    @override_waffle_flag(USER_TOURS_DISABLED, active=True)
    def test_tours_disabled(self, method):
        """ Test that the tours can be turned off with a waffle flag. """
        response = self.send_request(self.staff_user, self.user, method)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data('GET', 'PATCH')
    def test_unauthorized_user(self, method):
        """ Test all endpoints if request does not have jwt auth. """
        url = reverse('user-tours', args=[self.user.username])
        if method == 'GET':
            response = self.client.get(url)
        elif method == 'PATCH':
            response = self.client.patch(url, data={})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_success(self):
        """ Test GET request for a user. """
        response = self.send_request(self.staff_user, self.staff_user, 'GET')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'course_home_tour_status': self.new_user_tour.course_home_tour_status,
            'show_courseware_tour': self.new_user_tour.show_courseware_tour
        }

    def test_get_staff_user_for_other_user(self):
        """ Test GET request for a staff user requesting info for another user. """
        response = self.send_request(self.staff_user, self.user, 'GET')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'course_home_tour_status': self.existing_user_tour.course_home_tour_status,
            'show_courseware_tour': self.existing_user_tour.show_courseware_tour
        }

    def test_get_user_for_other_user(self):
        """ Test GET request for a regular user requesting info for another user. """
        response = self.send_request(self.user, self.staff_user, 'GET')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_nonexistent_user_tour(self):
        """
        Test GET request for a non-existing user tour.

        Note: In reality, this should never happen, but better safe than sorry
        """
        # We need to disable the UserTour handler for this test since it will be automatically
        # created otherwise.
        post_save.disconnect(init_user_tour, sender=User)
        response = self.send_request(self.staff_user, UserFactory(), 'GET')
        assert response.status_code == status.HTTP_404_NOT_FOUND
        post_save.connect(init_user_tour, sender=User)

    def test_patch_success(self):
        """ Test PATCH request for a user. """
        tour = UserTour.objects.get(user=self.user)
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.EXISTING_USER_TOUR
        data = {'course_home_tour_status': UserTour.CourseHomeChoices.NO_TOUR}
        response = self.send_request(self.user, self.user, 'PATCH', data=data)
        assert response.status_code == status.HTTP_200_OK
        tour.refresh_from_db()
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.NO_TOUR

    def test_patch_update_multiple_fields(self):
        """ Test PATCH request for a user changing multiple fields. """
        tour = UserTour.objects.get(user=self.staff_user)
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.NEW_USER_TOUR
        assert tour.show_courseware_tour is True
        data = {
            'course_home_tour_status': UserTour.CourseHomeChoices.NO_TOUR,
            'show_courseware_tour': False
        }
        response = self.send_request(self.staff_user, self.staff_user, 'PATCH', data=data)
        assert response.status_code == status.HTTP_200_OK
        tour.refresh_from_db()
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.NO_TOUR
        assert tour.show_courseware_tour is False

    def test_patch_user_for_other_user(self):
        """ Test PATCH request for a user trying to change UserTour status for another user. """
        data = {'course_home_tour_status': UserTour.CourseHomeChoices.NO_TOUR}
        response = self.send_request(self.staff_user, self.user, 'PATCH', data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_patch_bad_data(self):
        """ Test PATCH request for a request with bad data. """
        # Invalid value
        data = {'course_home_tour_status': 'blah'}
        response = self.send_request(self.user, self.user, 'PATCH', data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['course_home_tour_status'][0] == '"blah" is not a valid choice.'

        # Invalid param, dropped from validated data so no update happens
        data = {'user': 7}
        response = self.send_request(self.user, self.user, 'PATCH', data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Param that doesn't even exist on model, dropped from validated data so no update happens
        data = {'foo': 'bar'}
        response = self.send_request(self.user, self.user, 'PATCH', data=data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_not_supported(self):
        """ Test PUT request returns method not supported. """
        headers = build_jwt_headers(self.staff_user)
        url = reverse('user-tours', args=[self.staff_user.username])
        response = self.client.put(url, data={}, content_type='application/json', **headers)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@override_settings(AVAILABLE_DISCUSSION_TOURS=['not_responded_filter'])
class UserDiscussionsToursViewTestCase(TestCase):
    """
    Tests for the UserDiscussionsToursView view.
    """
    def setUp(self):
        # Create a user and a tour for testing
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass'
        )
        # create a UserDiscussionsTour for the user
        self.tour = UserDiscussionsTours.objects.create(
            tour_name='Test Tour',
            show_tour=False,
            user=self.user
        )
        self.url = reverse('discussion-tours')

    def test_get_tours(self):
        """
        Test GET request for a user's discussion tours.
        """
        # create a header with our user's credentials
        headers = build_jwt_headers(self.user)
        # Send a GET request to the view
        response = self.client.get(self.url, **headers)
        # Check that the response status code is correct
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

        # Check that the returned data is correct
        self.assertEqual(len(response.data), 2)  # noqa: PT009
        self.assertEqual(response.data[0]['tour_name'], 'Test Tour')  # noqa: PT009
        self.assertFalse(response.data[0]['show_tour'])  # noqa: PT009

        self.assertEqual(response.data[1]['tour_name'], 'not_responded_filter')  # noqa: PT009
        self.assertTrue(response.data[1]['show_tour'])  # noqa: PT009

        # Test that the view can be disabled by a waffle flag.
        with override_waffle_flag(USER_TOURS_DISABLED, active=True):
            response = self.client.get(self.url, **headers)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_get_tours_unauthenticated(self):
        """
        Test that an unauthenticated user cannot access the discussion tours endpoint.
        """
        # Send a GET request to the view without logging in
        response = self.client.get(self.url)

        # Check that the response status code is correct
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_update_tour(self):
        """
        Test that a user can update their discussion tour status.
        """
        headers = build_jwt_headers(self.user)

        # Send a PUT request to the view with the updated tour data
        updated_data = {'show_tour': False}
        url = reverse('discussion-tours', args=[self.tour.id])
        response = self.client.put(url, updated_data, content_type='application/json', **headers)

        # Check that the response status code is correct
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

        # Check that the tour was updated in the database
        updated_tour = UserDiscussionsTours.objects.get(id=self.tour.id)
        self.assertEqual(updated_tour.show_tour, False)  # noqa: PT009

        # Test that the view can be disabled by a waffle flag.
        with override_waffle_flag(USER_TOURS_DISABLED, active=True):
            response = self.client.put(url, updated_data, content_type='application/json', **headers)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
