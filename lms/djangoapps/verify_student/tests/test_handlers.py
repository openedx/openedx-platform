"""
Unit tests for the VerificationDeadline signals
"""


from datetime import timedelta
from unittest.mock import patch  # pylint: disable=wrong-import-order

from django.utils.timezone import now

from common.djangoapps.student.models_api import do_name_change_request
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import (
    SoftwareSecurePhotoVerification,
    VerificationAttempt,
    VerificationDeadline,
)
from lms.djangoapps.verify_student.signals.handlers import (
    _listen_for_course_publish,
    _listen_for_lms_retire,
    _listen_for_lms_retire_verification_attempts,
)
from lms.djangoapps.verify_student.tests.factories import (
    SoftwareSecurePhotoVerificationFactory,
    VerificationAttemptFactory,
)
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import fake_completed_retirement
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


class VerificationDeadlineHandlerTest(ModuleStoreTestCase):
    """
    Tests for the VerificationDeadline handler
    """

    def setUp(self):
        super().setUp()
        self.end = now().replace(microsecond=0) + timedelta(days=7)
        self.course = CourseFactory.create(end=self.end)
        VerificationDeadline.objects.all().delete()

    def test_no_deadline(self):
        """ Verify the handler sets deadline to course end when no deadline exists."""
        _listen_for_course_publish('store', self.course.id)

        assert VerificationDeadline.deadline_for_course(self.course.id) == self.course.end

    def test_deadline(self):
        """ Verify deadline is set to course end date by handler when changed. """
        deadline = now() - timedelta(days=7)
        VerificationDeadline.set_deadline(self.course.id, deadline)

        _listen_for_course_publish('store', self.course.id)
        assert VerificationDeadline.deadline_for_course(self.course.id) == self.course.end

    def test_deadline_explicit(self):
        """ Verify deadline is unchanged by handler when explicitly set. """
        deadline = now() - timedelta(days=7)
        VerificationDeadline.set_deadline(self.course.id, deadline, is_explicit=True)

        _listen_for_course_publish('store', self.course.id)

        actual_deadline = VerificationDeadline.deadline_for_course(self.course.id)
        assert actual_deadline != self.course.end
        assert actual_deadline == deadline


class RetirementHandlerTest(ModuleStoreTestCase):
    """
    Tests for the VerificationDeadline handler
    """

    def _create_entry(self):
        """
        Helper method to create and return a SoftwareSecurePhotoVerification with appropriate data
        """
        name = 'Test Name'
        face_url = 'https://test.invalid'
        id_url = 'https://test2.invalid'
        key = 'test+key'
        user = UserFactory()
        return SoftwareSecurePhotoVerificationFactory(
            user=user,
            name=name,
            face_image_url=face_url,
            photo_id_image_url=id_url,
            photo_id_key=key
        )

    def test_retire_success(self):
        verification = self._create_entry()
        _listen_for_lms_retire(sender=self.__class__, user=verification.user)

        ver_obj = SoftwareSecurePhotoVerification.objects.get(user=verification.user)

        # All values for this user should now be empty string
        for field in ('name', 'face_image_url', 'photo_id_image_url', 'photo_id_key'):
            assert '' == getattr(ver_obj, field)

    def test_retire_success_no_entries(self):
        user = UserFactory()
        _listen_for_lms_retire(sender=self.__class__, user=user)

    def test_idempotent(self):
        verification = self._create_entry()

        # Run this twice to make sure there are no errors raised 2nd time through
        _listen_for_lms_retire(sender=self.__class__, user=verification.user)
        fake_completed_retirement(verification.user)
        _listen_for_lms_retire(sender=self.__class__, user=verification.user)

        ver_obj = SoftwareSecurePhotoVerification.objects.get(user=verification.user)

        # All values for this user should now be empty string
        for field in ('name', 'face_image_url', 'photo_id_image_url', 'photo_id_key'):
            assert '' == getattr(ver_obj, field)


class PostSavePhotoVerificationTest(ModuleStoreTestCase):
    """
    Tests for the post_save handler on the SoftwareSecurePhotoVerification model.
    This receiver should emit another handler that contains limited data about
    the verification attempt that was updated.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.photo_id_name = 'Bob Doe'
        self.face_image_url = 'https://test.face'
        self.photo_id_image_url = 'https://test.photo'
        self.photo_id_key = 'test+key'

    @patch('lms.djangoapps.verify_student.signals.signals.idv_update_signal.send')
    def test_post_save_signal(self, mock_signal):
        # create new softwaresecureverification
        attempt = SoftwareSecurePhotoVerification.objects.create(
            user=self.user,
            name=self.photo_id_name,
            face_image_url=self.face_image_url,
            photo_id_image_url=self.photo_id_image_url,
            photo_id_key=self.photo_id_key
        )
        self.assertTrue(mock_signal.called)  # noqa: PT009
        mock_signal.assert_called_with(
            sender='idv_update',
            attempt_id=attempt.id,
            user_id=attempt.user.id,
            status=attempt.status,
            photo_id_name=attempt.name,
            full_name=attempt.user.profile.name
        )
        mock_signal.reset_mock()

        attempt.mark_ready()

        self.assertTrue(mock_signal.called)  # noqa: PT009
        mock_signal.assert_called_with(
            sender='idv_update',
            attempt_id=attempt.id,
            user_id=attempt.user.id,
            status=attempt.status,
            photo_id_name=attempt.name,
            full_name=attempt.user.profile.name
        )

    @patch('lms.djangoapps.verify_student.signals.signals.idv_update_signal.send')
    def test_post_save_signal_pending_name(self, mock_signal):
        pending_name_change = do_name_change_request(self.user, 'Pending Name', 'test')[0]

        attempt = SoftwareSecurePhotoVerification.objects.create(
            user=self.user,
            name=self.photo_id_name,
            face_image_url=self.face_image_url,
            photo_id_image_url=self.photo_id_image_url,
            photo_id_key=self.photo_id_key
        )

        mock_signal.assert_called_with(
            sender='idv_update',
            attempt_id=attempt.id,
            user_id=attempt.user.id,
            status=attempt.status,
            photo_id_name=attempt.name,
            full_name=pending_name_change.new_name
        )


class RetirementHandlerVerificationAttemptsTest(ModuleStoreTestCase):
    """
    Tests for the LMS User Retirement signal for Verification Attempts
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.other_user = UserFactory.create()
        VerificationAttemptFactory.create(user=self.user)
        VerificationAttemptFactory.create(user=self.other_user)

    def test_retirement_signal(self):
        _listen_for_lms_retire_verification_attempts(sender=self.__class__, user=self.user)
        self.assertEqual(len(VerificationAttempt.objects.filter(user=self.user)), 0)  # noqa: PT009
        self.assertEqual(len(VerificationAttempt.objects.filter(user=self.other_user)), 1)  # noqa: PT009

    def test_retirement_signal_no_attempts(self):
        no_attempt_user = UserFactory.create()
        _listen_for_lms_retire_verification_attempts(sender=self.__class__, user=no_attempt_user)
        self.assertEqual(len(VerificationAttempt.objects.all()), 2)  # noqa: PT009
