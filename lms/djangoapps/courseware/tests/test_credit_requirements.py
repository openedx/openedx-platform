"""
Tests for credit requirement display on the progress page.
"""


from unittest.mock import patch

import ddt
from django.conf import settings
from django.urls import reverse

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.credit import api as credit_api
from openedx.core.djangoapps.credit.models import CreditCourse
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@patch.dict(settings.FEATURES, {"ENABLE_CREDIT_ELIGIBILITY": True})
@ddt.ddt
class ProgressPageCreditRequirementsTest(SharedModuleStoreTestCase):
    """
    Tests for credit requirement display on the progress page.
    """

    USERNAME = "bob"
    PASSWORD = "test"
    USER_FULL_NAME = "Bob"

    MIN_GRADE_REQ_DISPLAY = "Final Grade Credit Requirement"
    VERIFICATION_REQ_DISPLAY = "Midterm Exam Credit Requirement"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        # Configure course as a credit course
        CreditCourse.objects.create(course_key=self.course.id, enabled=True)

        # Configure credit requirements (passing grade and in-course reverification)
        credit_api.set_credit_requirements(
            self.course.id,
            [
                {
                    "namespace": "grade",
                    "name": "grade",
                    "display_name": self.MIN_GRADE_REQ_DISPLAY,
                    "criteria": {
                        "min_grade": 0.8
                    }
                },
                {
                    "namespace": "reverification",
                    "name": "midterm",
                    "display_name": self.VERIFICATION_REQ_DISPLAY,
                    "criteria": {}
                }
            ]
        )

        # Create a user and log in
        self.user = UserFactory.create(username=self.USERNAME, password=self.PASSWORD)
        self.user.profile.name = self.USER_FULL_NAME
        self.user.profile.save()

        result = self.client.login(username=self.USERNAME, password=self.PASSWORD)
        assert result, 'Could not log in'

        # Enroll the user in the course as "verified"
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,
            mode="verified"
        )

    def test_credit_requirements_maybe_eligible(self):
        # The user hasn't satisfied any of the credit requirements yet, but she
        # also hasn't failed any.
        response = self._get_progress_page()

        # Expect that the requirements are displayed
        self.assertContains(response, self.MIN_GRADE_REQ_DISPLAY)
        self.assertContains(response, self.VERIFICATION_REQ_DISPLAY)
        self.assertContains(response, "Upcoming")
        self.assertContains(
            response,
            f"{self.USER_FULL_NAME}, you have not yet met the requirements for credit"
        )

    def test_credit_requirements_eligible(self):
        """
        Mark the user as eligible for all requirements. Requirements are only displayed
        for credit and verified enrollments.
        """
        credit_api.set_credit_requirement_status(
            self.user, self.course.id,
            "grade", "grade",
            status="satisfied",
            reason={"final_grade": 0.95}
        )

        credit_api.set_credit_requirement_status(
            self.user, self.course.id,
            "reverification", "midterm",
            status="satisfied", reason={}
        )

        # Check the progress page display
        response = self._get_progress_page()
        self.assertContains(response, self.MIN_GRADE_REQ_DISPLAY)
        self.assertContains(response, self.VERIFICATION_REQ_DISPLAY)
        self.assertContains(
            response,
            f"{self.USER_FULL_NAME}, you have met the requirements for credit in this course."
        )
        self.assertContains(response, "Completed by {date}")

        credit_requirements = credit_api.get_credit_requirement_status(self.course.id, self.user.username)
        for requirement in credit_requirements:
            self.assertContains(response, requirement['status_date'].strftime('%Y-%m-%d %H:%M'))
        self.assertNotContains(response, "95%")

    def test_credit_requirements_not_eligible(self):
        """
        Mark the user as having failed both requirements. Requirements are only displayed
        for credit and verified enrollments.
        """
        credit_api.set_credit_requirement_status(
            self.user, self.course.id,
            "reverification", "midterm",
            status="failed", reason={}
        )

        # Check the progress page display
        response = self._get_progress_page()
        self.assertContains(response, self.MIN_GRADE_REQ_DISPLAY)
        self.assertContains(response, self.VERIFICATION_REQ_DISPLAY)
        self.assertContains(
            response,
            f"{self.USER_FULL_NAME}, you are no longer eligible for credit in this course."
        )
        self.assertContains(response, "Verification Failed")

    @ddt.data(
        (CourseMode.VERIFIED, True),
        (CourseMode.CREDIT_MODE, True),
        (CourseMode.HONOR, False),
        (CourseMode.AUDIT, False),
        (CourseMode.PROFESSIONAL, False),
        (CourseMode.NO_ID_PROFESSIONAL_MODE, False)
    )
    @ddt.unpack
    def test_credit_requirements_on_progress_page(self, enrollment_mode, is_requirement_displayed):
        """Test the progress table is only displayed to the verified and credit students."""
        self.enrollment.mode = enrollment_mode
        self.enrollment.save()

        response = self._get_progress_page()
        # Verify the requirements are shown only if the user is in a credit-eligible mode.
        classes = ('credit-eligibility', 'eligibility-heading')
        method = self.assertContains if is_requirement_displayed else self.assertNotContains

        for _class in classes:
            method(response, _class)

    def _get_progress_page(self):
        """Load the progress page for the course the user is enrolled in. """
        url = reverse("progress", kwargs={"course_id": str(self.course.id)})
        return self.client.get(url)
