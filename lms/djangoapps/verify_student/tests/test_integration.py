"""
Integration tests of the payment flow, including course mode selection.
"""

from django.urls import reverse

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..services import IDVerificationService


class TestProfEdVerification(ModuleStoreTestCase):
    """
    Integration test for professional ed verification, including course mode selection.
    """

    # Choose an uncommon number for the price so we can search for it on the page
    MIN_PRICE = 1438

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create(username="rusty", password="test")
        self.client.login(username="rusty", password="test")
        course = CourseFactory.create(org='Robot', number='999', display_name='Test Course')
        self.course_key = course.id
        CourseModeFactory.create(
            mode_slug="professional",
            course_id=self.course_key,
            min_price=self.MIN_PRICE,
            suggested_prices=''
        )
        self.urls = {
            'course_modes_choose': reverse(
                'course_modes_choose',
                args=[str(self.course_key)]
            ),

            'verify_student_start_flow': IDVerificationService.get_verify_location(self.course_key),
        }

    def test_start_flow(self):
        # Go to the course mode page, expecting a redirect to the intro step of the
        # payment flow (since this is a professional ed course). Otherwise, the student
        # would have the option to choose their track.
        resp = self.client.get(self.urls['course_modes_choose'])
        self.assertRedirects(
            resp,
            self.urls['verify_student_start_flow'],
            fetch_redirect_response=False,
        )

        # For professional ed courses, expect that the student is NOT enrolled
        # automatically in the course.
        assert not CourseEnrollment.is_enrolled(self.user, self.course_key)
