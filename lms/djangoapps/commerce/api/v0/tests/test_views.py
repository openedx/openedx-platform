""" Commerce API v0 view tests. """


import itertools
import json
from datetime import datetime, timedelta
from unittest import mock
from uuid import uuid4

import ddt
import pytz
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse, reverse_lazy

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.tests import (
    EnrollmentEventTestMixin,  # lint-amnesty, pylint: disable=unused-import
)
from openedx.core.djangoapps.embargo.test_utils import restrict_course
from openedx.core.djangoapps.enrollments.api import get_enrollment
from openedx.core.lib.django_test_client_utils import get_absolute_url
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ....constants import Messages
from ....tests.mocks import mock_basket_order
from ....tests.test_views import UserMixin

UTM_COOKIE_NAME = 'edx.test.utm'
UTM_COOKIE_CONTENTS = {
    'utm_source': 'test-source'
}


@ddt.ddt
class BasketsViewTests(UserMixin, ModuleStoreTestCase):
    """
    Tests for the commerce Baskets view.
    """

    def _post_to_view(self, course_id=None, marketing_email_opt_in=False, include_utm_cookie=False):
        """
        POST to the view being tested.

        Arguments
            course_id (str) --  ID of course for which a seat should be ordered.

        :return: Response
        """
        payload = {
            "course_id": str(course_id or self.course.id)
        }
        if marketing_email_opt_in:
            payload["email_opt_in"] = True

        if include_utm_cookie:
            self.client.cookies[UTM_COOKIE_NAME] = json.dumps(UTM_COOKIE_CONTENTS)
        return self.client.post(self.url, payload)

    def assertResponseMessage(self, response, expected_msg):
        """ Asserts the detail field in the response's JSON body equals the expected message. """
        actual = json.loads(response.content.decode('utf-8'))['detail']
        assert actual == expected_msg

    def setUp(self):
        super().setUp()
        self.url = reverse('commerce_api:v0:baskets:create')
        self._login()

        self.course = CourseFactory.create()

        # TODO Verify this is the best method to create CourseMode objects.
        # TODO Find/create constants for the modes.
        for mode in [CourseMode.HONOR, CourseMode.VERIFIED, CourseMode.AUDIT]:
            sku_string = str(uuid4().hex)
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode,
                mode_display_name=mode,
                sku=sku_string,
                bulk_sku=f'BULK-{sku_string}'
            )

    @mock.patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_restriction(self):
        """
        The view should return HTTP 403 status if the course is embargoed.
        """
        with restrict_course(self.course.id) as redirect_url:
            response = self._post_to_view()
            assert 403 == response.status_code
            body = json.loads(response.content.decode('utf-8'))
            assert get_absolute_url(redirect_url) == body['user_message_url']

    def test_login_required(self):
        """
        The view should return HTTP 401 status if the user is not logged in.
        """
        self.client.logout()
        assert 401 == self._post_to_view().status_code

    @ddt.data('delete', 'get', 'put')
    def test_post_required(self, method):
        """
        Verify that the view only responds to POST operations.
        """
        response = getattr(self.client, method)(self.url)
        assert 405 == response.status_code

    def test_invalid_course(self):
        """
        If the course does not exist, the view should return HTTP 406.
        """
        # TODO Test inactive courses, and those not open for enrollment.
        assert 406 == self._post_to_view('aaa/bbb/ccc').status_code

    def test_invalid_request_data(self):
        """
        If invalid data is supplied with the request, the view should return HTTP 406.
        """
        assert 406 == self.client.post(self.url, {}).status_code
        assert 406 == self.client.post(self.url, {'not_course_id': ''}).status_code

    @ddt.data(True, False)
    def test_course_for_active_and_inactive_user(self, user_is_active):
        """
        Test course enrollment for active and inactive user.
        """
        # Set user's active flag
        self.user.is_active = user_is_active
        self.user.save()
        response = self._post_to_view()

        # Validate the response content
        assert response.status_code == 200
        msg = Messages.ENROLL_DIRECTLY.format(
            course_id=self.course.id,
            username=self.user.username
        )
        self.assertResponseMessage(response, msg)

    def _test_course_without_sku(self, enrollment_mode=CourseMode.DEFAULT_MODE_SLUG):
        """
        Validates the view when course has no CourseModes with SKUs.
        """
        response = self._post_to_view()

        # Validate the response content
        assert response.status_code == 200
        msg = Messages.NO_SKU_ENROLLED.format(
            enrollment_mode=enrollment_mode,
            course_id=self.course.id,
            course_name=self.course.display_name,
            username=self.user.username,
            announcement=self.course.announcement
        )
        self.assertResponseMessage(response, msg)

    def test_course_without_sku_default(self):
        """
        If the course does NOT have a SKU, the user should be enrolled in the course (under the default mode) and
        redirected to the user dashboard.
        """
        # Remove SKU from all course modes
        for course_mode in CourseMode.objects.filter(course_id=self.course.id):
            course_mode.sku = None
            course_mode.save()

        self._test_course_without_sku()

    def test_course_without_sku_honor(self):
        """
        If the course does not have an SKU and has an honor mode, the user
        should be enrolled as honor. This ensures backwards
        compatibility with courses existing before the removal of
        honor certificates.
        """
        # Remove all existing course modes
        CourseMode.objects.filter(course_id=self.course.id).delete()
        # Ensure that honor mode exists
        CourseMode(
            mode_slug=CourseMode.HONOR,
            mode_display_name="Honor Cert",
            course_id=self.course.id
        ).save()
        # We should be enrolled in honor mode
        self._test_course_without_sku(enrollment_mode=CourseMode.HONOR)

    def assertProfessionalModeBypassed(self):
        """ Verifies that the view returns HTTP 406 when a course with no honor or audit mode is encountered. """

        CourseMode.objects.filter(course_id=self.course.id).delete()
        mode = CourseMode.NO_ID_PROFESSIONAL_MODE
        sku_string = str(uuid4().hex)
        CourseModeFactory.create(course_id=self.course.id, mode_slug=mode, mode_display_name=mode,
                                 sku=sku_string, bulk_sku=f'BULK-{sku_string}')
        response = self._post_to_view()

        # The view should return an error status code
        assert response.status_code == 406
        msg = Messages.NO_DEFAULT_ENROLLMENT_MODE.format(course_id=self.course.id)
        self.assertResponseMessage(response, msg)

    def test_course_with_professional_mode_only(self):
        """ Verifies that the view behaves appropriately when the course only has a professional mode. """
        self.assertProfessionalModeBypassed()

    @override_settings(ECOMMERCE_API_URL=None)
    def test_professional_mode_only_and_ecommerce_service_not_configured(self):
        """
        Verifies that the view behaves appropriately when the course only has a professional mode and
        the E-Commerce Service is not configured.
        """
        self.assertProfessionalModeBypassed()

    def test_empty_sku(self):
        """ If the CourseMode has an empty string for a SKU, the API should not be used. """
        # Set SKU to empty string for all modes.
        for course_mode in CourseMode.objects.filter(course_id=self.course.id):
            course_mode.sku = ''
            course_mode.save()

        self._test_course_without_sku()

    def test_existing_active_enrollment(self):
        """ The view should respond with HTTP 409 if the user has an existing active enrollment for the course. """

        # Enroll user in the course
        CourseEnrollment.enroll(self.user, self.course.id)
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)

        response = self._post_to_view()
        assert response.status_code == 409
        msg = Messages.ENROLLMENT_EXISTS.format(username=self.user.username, course_id=self.course.id)
        self.assertResponseMessage(response, msg)

    def test_existing_inactive_enrollment(self):
        """
        If the user has an inactive enrollment for the course, the view should behave as if the
        user has no enrollment.
        """
        # Create an inactive enrollment
        CourseEnrollment.enroll(self.user, self.course.id)
        CourseEnrollment.unenroll(self.user, self.course.id, True)
        assert not CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert get_enrollment(self.user.username, str(self.course.id)) is not None

    @mock.patch('lms.djangoapps.commerce.api.v0.views.update_email_opt_in')
    @ddt.data(*itertools.product((False, True), (False, True), (False, True)))
    @ddt.unpack
    def test_marketing_email_opt_in(self, is_opt_in, has_sku, is_exception, mock_update):
        """
        Ensures the email opt-in flag is handled, if present, and that problems handling the
        flag don't cause the rest of the enrollment transaction to fail.
        """
        if not has_sku:
            for course_mode in CourseMode.objects.filter(course_id=self.course.id):
                course_mode.sku = None
                course_mode.save()

        if is_exception:
            mock_update.side_effect = Exception("boink")

        response = self._post_to_view(marketing_email_opt_in=is_opt_in)
        assert mock_update.called == is_opt_in
        assert response.status_code == 200

    def test_closed_course(self):
        """
        Verifies that the view returns HTTP 406 when a course is closed.
        """
        self.course.enrollment_end = datetime.now(pytz.UTC) - timedelta(days=1)
        modulestore().update_item(self.course, self.user.id)
        assert self._post_to_view().status_code == 406


class BasketOrderViewTests(UserMixin, TestCase):
    """ Tests for the basket order view. """
    view_name = 'commerce_api:v0:baskets:retrieve_order'
    MOCK_ORDER = {'number': 1}
    path = reverse_lazy(view_name, kwargs={'basket_id': 1})

    def setUp(self):
        super().setUp()
        self._login()

    def test_order_found(self):
        """ If the order is located, the view should pass the data from the API. """

        with mock_basket_order(basket_id=1, response=self.MOCK_ORDER):
            response = self.client.get(self.path)

        assert response.status_code == 200
        actual = json.loads(response.content.decode('utf-8'))
        assert actual == self.MOCK_ORDER

    def test_order_not_found(self):
        """ If the order is not found, the view should return a 404. """
        with mock_basket_order(basket_id=1, status=404):
            response = self.client.get(self.path)
        assert response.status_code == 404

    def test_login_required(self):
        """ The view should return 403 if the user is not logged in. """
        self.client.logout()
        response = self.client.get(self.path)
        assert response.status_code == 401
