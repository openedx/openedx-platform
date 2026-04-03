"""
Unit tests for the localization of emails sent by instructor.api methods.
"""


from django.core import mail
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import InstructorFactory, UserFactory
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.user_api.preferences.api import delete_user_preference, set_user_preference
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestInstructorAPIEnrollmentEmailLocalization(SharedModuleStoreTestCase):
    """
    Test whether the enroll, unenroll and beta role emails are sent in the
    proper language, i.e: the student's language.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        # Platform language is English, instructor's language is Chinese,
        # student's language is Esperanto, so the emails should all be sent in
        # Esperanto.
        self.instructor = InstructorFactory(course_key=self.course.id)
        set_user_preference(self.instructor, LANGUAGE_KEY, 'zh-cn')
        self.client.login(username=self.instructor.username, password=self.TEST_PASSWORD)

        self.student = UserFactory.create()
        set_user_preference(self.student, LANGUAGE_KEY, 'eo')

    def update_enrollement(self, action, student_email):
        """
        Update the current student enrollment status.
        """
        url = reverse('students_update_enrollment', kwargs={'course_id': str(self.course.id)})
        args = {'identifiers': student_email, 'email_students': 'true', 'action': action, 'reason': 'testing'}
        response = self.client.post(url, args)
        return response

    def check_outbox_is_esperanto(self):
        """
        Check that the email outbox contains exactly one message for which both
        the message subject and body contain a certain Esperanto string.
        """
        return self.check_outbox("Ýöü hävé ßéén")

    def check_outbox(self, expected_message):
        """
        Check that the email outbox contains exactly one message for which both
        the message subject and body contain a certain string.
        """
        assert 1 == len(mail.outbox)
        assert expected_message in mail.outbox[0].subject
        assert expected_message in mail.outbox[0].body

    def test_enroll(self):
        self.update_enrollement("enroll", self.student.email)

        self.check_outbox_is_esperanto()

    def test_unenroll(self):
        CourseEnrollment.enroll(
            self.student,
            self.course.id
        )
        self.update_enrollement("unenroll", self.student.email)

        self.check_outbox_is_esperanto()

    def test_set_beta_role(self):
        url = reverse('bulk_beta_modify_access', kwargs={'course_id': str(self.course.id)})
        self.client.post(url, {'identifiers': self.student.email, 'action': 'add', 'email_students': 'true'})

        self.check_outbox_is_esperanto()

    def test_enroll_unsubscribed_student(self):
        # Student is unknown, so the platform language should be used
        self.update_enrollement("enroll", "newuser@hotmail.com")
        self.check_outbox("You have been")

    @override_settings(LANGUAGE_CODE="eo")
    def test_user_without_preference_receives_email_in_esperanto(self):
        delete_user_preference(self.student, LANGUAGE_KEY)
        self.update_enrollement("enroll", self.student.email)

        self.check_outbox_is_esperanto()
