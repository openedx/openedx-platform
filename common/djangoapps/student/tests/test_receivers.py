""" Tests for student signal receivers. """

from unittest import skipUnless
from unittest.mock import patch

from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.student.models import CourseEnrollmentCelebration, PendingNameChange, UserProfile
from common.djangoapps.student.signals.signals import USER_EMAIL_CHANGED
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory, UserProfileFactory
from lms.djangoapps.courseware.toggles import COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES
from openedx.core.djangolib.testing.utils import get_mock_request, skip_unless_lms
from openedx.features.name_affirmation_api.utils import is_name_affirmation_installed
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)

name_affirmation_installed = is_name_affirmation_installed()
if name_affirmation_installed:
    # pylint: disable=import-error
    from edx_name_affirmation.signals import VERIFIED_NAME_APPROVED


class ReceiversTest(SharedModuleStoreTestCase):
    """
    Tests for dashboard utility functions
    """
    @override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES, active=True)
    def test_celebration_created(self):
        """ Test that we make celebration objects when enrollments are created """
        assert CourseEnrollmentCelebration.objects.count() == 0

        # Test initial creation upon an enrollment being made
        enrollment = CourseEnrollmentFactory()
        assert CourseEnrollmentCelebration.objects.count() == 1
        celebration = CourseEnrollmentCelebration.objects.get(
            enrollment=enrollment, celebrate_first_section=True, celebrate_weekly_goal=True
        )

        # Test nothing changes if we update that enrollment
        celebration.celebrate_first_section = False
        celebration.save()
        enrollment.mode = 'test-mode'
        enrollment.save()
        assert CourseEnrollmentCelebration.objects.count() == 1
        CourseEnrollmentCelebration.objects.get(
            enrollment=enrollment, celebrate_first_section=False, celebrate_weekly_goal=True
        )

    def test_celebration_gated_by_waffle(self):
        """ Test we don't make a celebration if the MFE redirect waffle flag is off """
        CourseEnrollmentFactory()
        assert CourseEnrollmentCelebration.objects.count() == 0

    @skipUnless(name_affirmation_installed, "Requires Name Affirmation")
    def test_listen_for_verified_name_approved(self):
        """
        Test that profile name is updated when a pending name change is approved
        """
        user = UserFactory(email='email@test.com', username='jdoe')
        UserProfileFactory(user=user)

        new_name = 'John Doe'
        PendingNameChange.objects.create(user=user, new_name=new_name)
        assert PendingNameChange.objects.count() == 1

        # Send a VERIFIED_NAME_APPROVED signal where the profile name matches the name
        # change request
        VERIFIED_NAME_APPROVED.send(sender=None, user_id=user.id, profile_name=new_name)

        # Assert that the pending name change was deleted and the profile name was updated
        assert PendingNameChange.objects.count() == 0
        profile = UserProfile.objects.get(user=user)
        assert profile.name == new_name

    @skip_unless_lms
    @patch('common.djangoapps.student.signals.receivers.get_email_client')
    def test_listen_for_user_email_changed(self, mock_get_email_client):
        """
        Ensure that USER_EMAIL_CHANGED signal triggers correct calls to
        get_email_client and update email in session.
        """
        user = UserFactory(email='email@test.com', username='jdoe')
        request = get_mock_request(user=user)
        request.session = self.client.session

        # simulating email change
        user.email = 'new_email@test.com'
        user.save()

        USER_EMAIL_CHANGED.send(sender=None, user=user, request=request)

        assert mock_get_email_client.called
        assert request.session.get('email', None) == user.email
