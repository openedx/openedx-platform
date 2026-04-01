"""
Tests for the course modes Django admin interface.
"""

from datetime import datetime, timedelta

import ddt
from django.conf import settings
from django.urls import reverse
from pytz import timezone
from zoneinfo import ZoneInfo

from common.djangoapps.course_modes.admin import CourseModeForm
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
# Technically, we shouldn't be importing verify_student, since it's
# defined in the LMS and course_modes is in common.  However, the benefits
# of putting all this configuration in one place outweigh the downsides.
# Once the course admin tool is deployed, we can remove this dependency.
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.date_utils import get_time_display
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


# We can only test this in the LMS because the course modes admin relies
# on verify student, which is not an installed app in Studio, so the verification
# deadline table will not be created.
@skip_unless_lms
class AdminCourseModePageTest(ModuleStoreTestCase):
    """
    Test the course modes Django admin interface.
    """

    def test_expiration_timezone(self):
        # Test that expiration datetimes are saved and retrieved with the timezone set to UTC.
        # This verifies the fix for a bug in which the date displayed to users was different
        # than the date in Django admin.
        user = UserFactory.create(is_staff=True, is_superuser=True)
        user.save()
        course = CourseFactory.create()
        expiration = datetime(2015, 10, 20, 1, 10, 23, tzinfo=timezone(settings.TIME_ZONE))
        CourseOverview.load_from_module_store(course.id)

        data = {
            'course': str(course.id),
            'mode_slug': 'verified',
            'mode_display_name': 'verified',
            'min_price': 10,
            'currency': 'usd',
            '_expiration_datetime_0': expiration.date(),  # due to django admin datetime widget passing as separate vals
            '_expiration_datetime_1': expiration.time(),
        }

        self.client.login(username=user.username, password=self.TEST_PASSWORD)

        # Create a new course mode from django admin page
        response = self.client.post(reverse('admin:course_modes_coursemode_add'), data=data)
        self.assertRedirects(response, reverse('admin:course_modes_coursemode_changelist'))

        course_mode = CourseMode.objects.get(course_id=str(course.id), mode_slug='verified')

        # Verify that datetime is appears on list page
        response = self.client.get(reverse('admin:course_modes_coursemode_changelist'))
        self.assertContains(response, get_time_display(expiration, '%B %d, %Y, %H:%M  %p'))

        # Verify that on the edit page the datetime value appears as UTC.
        resp = self.client.get(reverse('admin:course_modes_coursemode_change', args=(course_mode.id,)))
        self.assertContains(resp, expiration.date())
        self.assertContains(resp, expiration.time())

        # Verify that the expiration datetime is the same as what we set
        # (hasn't changed because of a timezone translation).
        course_mode.refresh_from_db()
        assert course_mode.expiration_datetime.replace(tzinfo=None) == expiration.replace(tzinfo=None)


@skip_unless_lms
@ddt.ddt
class AdminCourseModeFormTest(ModuleStoreTestCase):
    """
    Test the course modes Django admin form validation and saving.
    """

    UPGRADE_DEADLINE = datetime.now(ZoneInfo("UTC"))
    VERIFICATION_DEADLINE = UPGRADE_DEADLINE + timedelta(days=5)

    def setUp(self):
        """
        Create a test course.
        """
        super().setUp()
        self.course = CourseFactory.create()
        CourseOverview.load_from_module_store(self.course.id)

    @ddt.data(
        ("honor", False),
        ("verified", True),
        ("professional", True),
        ("no-id-professional", False),
        ("credit", False),
    )
    @ddt.unpack
    def test_load_verification_deadline(self, mode, expect_deadline):
        # Configure a verification deadline for the course
        VerificationDeadline.set_deadline(self.course.id, self.VERIFICATION_DEADLINE)

        # Configure a course mode with both an upgrade and verification deadline
        # and load the form to edit it.
        deadline = self.UPGRADE_DEADLINE if mode == "verified" else None
        form = self._admin_form(mode, upgrade_deadline=deadline)

        # Check that the verification deadline is loaded,
        # but ONLY for verified modes.
        loaded_deadline = form.initial.get("verification_deadline")
        if expect_deadline:
            assert loaded_deadline.replace(tzinfo=None) == self.VERIFICATION_DEADLINE.replace(tzinfo=None)
        else:
            assert loaded_deadline is None

    @ddt.data("verified", "professional")
    def test_set_verification_deadline(self, course_mode):
        # Configure a verification deadline for the course
        VerificationDeadline.set_deadline(self.course.id, self.VERIFICATION_DEADLINE)

        # Create the course mode Django admin form
        form = self._admin_form(course_mode)

        # Update the verification deadline form data
        # We need to set the date and time fields separately, since they're
        # displayed as separate widgets in the form.
        new_deadline = (self.VERIFICATION_DEADLINE + timedelta(days=1)).replace(microsecond=0)
        self._set_form_verification_deadline(form, new_deadline)
        form.save()

        # Check that the deadline was updated
        updated_deadline = VerificationDeadline.deadline_for_course(self.course.id)
        assert updated_deadline == new_deadline

    def test_disable_verification_deadline(self):
        # Configure a verification deadline for the course
        VerificationDeadline.set_deadline(self.course.id, self.VERIFICATION_DEADLINE)

        # Create the course mode Django admin form
        form = self._admin_form("verified", upgrade_deadline=self.UPGRADE_DEADLINE)

        # Use the form to disable the verification deadline
        self._set_form_verification_deadline(form, None)
        form.save()

        # Check that the deadline was disabled
        assert VerificationDeadline.deadline_for_course(self.course.id) is None

    @ddt.data("honor", "professional", "no-id-professional", "credit")
    def test_validate_upgrade_deadline_only_for_verified(self, course_mode):
        # Only the verified mode should have an upgrade deadline, so any other course
        # mode that has an upgrade deadline set should cause a validation error
        form = self._admin_form(course_mode, upgrade_deadline=self.UPGRADE_DEADLINE)
        self._assert_form_has_error(form, (
            'Only the "verified" mode can have an upgrade deadline.  '
            'For other modes, please set the enrollment end date in Studio.'
        ))

    def test_validate_expiration_datetime_is_explicit_only_with_upgrade_deadline(self):
        # Only allow the expiration_datetime_is_explicit to be True if the upgrade_deadline is
        # defined with a date, otherwise cause a validation error.
        form = self._admin_form("verified", expiration_datetime_is_explicit=True)
        self._assert_form_has_error(form, (
            "An upgrade deadline must be specified when setting Lock upgrade deadline date to True."
        ))

    @ddt.data("honor", "no-id-professional", "credit")
    def test_validate_verification_deadline_only_for_verified(self, course_mode):
        # Only the verified mode should have a verification deadline set.
        # Any other course mode should raise a validation error if a deadline is set.
        form = self._admin_form(course_mode)
        self._set_form_verification_deadline(form, self.VERIFICATION_DEADLINE)
        self._assert_form_has_error(form, "Verification deadline can be set only for verified modes.")

    def test_verification_deadline_after_upgrade_deadline(self):
        form = self._admin_form("verified", upgrade_deadline=self.UPGRADE_DEADLINE)
        before_upgrade = self.UPGRADE_DEADLINE - timedelta(days=1)
        self._set_form_verification_deadline(form, before_upgrade)
        self._assert_form_has_error(form, "Verification deadline must be after the upgrade deadline.")

    def _configure(self, mode, upgrade_deadline=None, verification_deadline=None):
        """Configure course modes and deadlines. """
        course_mode = CourseModeFactory.create(
            mode_slug=mode,
            mode_display_name=mode,
        )

        if upgrade_deadline is not None:
            course_mode.upgrade_deadline = upgrade_deadline
            course_mode.save()

        VerificationDeadline.set_deadline(self.course.id, verification_deadline)

        return CourseModeForm(instance=course_mode)

    def _admin_form(self, mode, upgrade_deadline=None, expiration_datetime_is_explicit=False):
        """Load the course mode admin form. """
        course_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=mode,
        )
        return CourseModeForm({
            "course": str(self.course.id),
            "mode_slug": mode,
            "mode_display_name": mode,
            "_expiration_datetime": upgrade_deadline,
            "expiration_datetime_is_explicit": expiration_datetime_is_explicit,
            "currency": "usd",
            "min_price": 10,
        }, instance=course_mode)

    def _set_form_verification_deadline(self, form, deadline):
        """Set the verification deadline on the course mode admin form. """
        date_str = deadline.strftime("%Y-%m-%d") if deadline else None
        time_str = deadline.strftime("%H:%M:%S") if deadline else None

        form.data["verification_deadline_0"] = date_str
        form.data["verification_deadline_1"] = time_str

    def _assert_form_has_error(self, form, error):
        """Check that a form has a validation error. """
        validation_errors = form.errors.get("__all__", [])
        assert error in validation_errors
