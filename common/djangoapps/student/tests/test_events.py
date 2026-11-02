"""
Test that various events are fired for models in the student app.
"""


from unittest import mock

import ddt
import pytest
from django.db.utils import IntegrityError
from django.test import TestCase
from django_countries.fields import Country
from opaque_keys.edx.keys import CourseKey
from openedx_events.learning.data import (  # lint-amnesty, pylint: disable=wrong-import-order
    CourseAccessRoleData,
    CourseData,
    CourseEnrollmentData,
    UserData,
    UserPersonalData
)
from openedx_events.learning.signals import (  # lint-amnesty, pylint: disable=wrong-import-order
    COURSE_ACCESS_ROLE_ADDED,
    COURSE_ACCESS_ROLE_REMOVED,
    COURSE_ENROLLMENT_CHANGED,
    COURSE_ENROLLMENT_CREATED,
    COURSE_UNENROLLMENT_COMPLETED
)
from openedx_events.tests.utils import OpenEdxEventsTestMixin  # lint-amnesty, pylint: disable=wrong-import-order

from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentAllowedFactory, UserFactory, UserProfileFactory
from common.djangoapps.student.tests.tests import UserSettingsEventTestMixin
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import \
    SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order
from common.test.utils import assert_dict_contains_subset


class TestUserProfileEvents(UserSettingsEventTestMixin, TestCase):
    """
    Test that we emit field change events when UserProfile models are changed.
    """
    def setUp(self):
        super().setUp()
        self.table = 'auth_userprofile'
        self.user = UserFactory.create()
        self.profile = self.user.profile
        self.reset_tracker()

    def test_change_one_field(self):
        """
        Verify that we emit an event when a single field changes on the user
        profile.
        """
        self.profile.year_of_birth = 1900
        self.profile.save()
        self.assert_user_setting_event_emitted(setting='year_of_birth', old=None, new=self.profile.year_of_birth)

        # Verify that we remove the temporary `_changed_fields` property from
        # the model after we're done emitting events.
        with pytest.raises(AttributeError):
            self.profile._changed_fields    # pylint: disable=pointless-statement, protected-access

    def test_change_many_fields(self):
        """
        Verify that we emit one event per field when many fields change on the
        user profile in one transaction.
        """
        self.profile.gender = 'o'
        self.profile.bio = 'test bio'
        self.profile.save()
        self.assert_user_setting_event_emitted(setting='bio', old=None, new=self.profile.bio)
        self.assert_user_setting_event_emitted(setting='gender', old='m', new='o')

    def test_unicode(self):
        """
        Verify that the events we emit can handle unicode characters.
        """
        old_name = self.profile.name
        self.profile.name = 'Dånîél'
        self.profile.save()
        self.assert_user_setting_event_emitted(setting='name', old=old_name, new=self.profile.name)

    def test_country(self):
        """
        Verify that we properly serialize the JSON-unfriendly Country field.
        """
        self.profile.country = Country('AL', 'dummy_flag_url')
        self.profile.save()
        self.assert_user_setting_event_emitted(setting='country', old=None, new=self.profile.country)

    def test_excluded_field(self):
        """
        Verify that we don't emit events for ignored fields.
        """
        self.profile.meta = {'foo': 'bar'}
        self.profile.save()
        self.assert_no_events_were_emitted()

    @mock.patch('common.djangoapps.student.models.user.UserProfile.save', side_effect=IntegrityError)
    def test_no_event_if_save_failed(self, _save_mock):
        """
        Verify no event is triggered if the save does not complete. Note that the pre_save
        signal is not called in this case either, but the intent is to make it clear that this model
        should never emit an event if save fails.
        """
        self.profile.gender = "unknown"
        with pytest.raises(IntegrityError):
            self.profile.save()
        self.assert_no_events_were_emitted()


class TestUserEvents(UserSettingsEventTestMixin, TestCase):
    """
    Test that we emit field change events when User models are changed.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.reset_tracker()
        self.table = 'auth_user'

    def test_change_one_field(self):
        """
        Verify that we emit an event when a single field changes on the user.
        """
        old_username = self.user.username
        self.user.username = 'new username'
        self.user.save()
        self.assert_user_setting_event_emitted(setting='username', old=old_username, new=self.user.username)

    def test_change_many_fields(self):
        """
        Verify that we emit one event per field when many fields change on the
        user in one transaction.
        """
        old_email = self.user.email
        old_is_staff = self.user.is_staff
        self.user.email = 'foo@bar.com'
        self.user.is_staff = True
        self.user.save()
        self.assert_user_setting_event_emitted(setting='email', old=old_email, new=self.user.email)
        self.assert_user_setting_event_emitted(setting='is_staff', old=old_is_staff, new=self.user.is_staff)

    def test_password(self):
        """
        Verify that password values are not included in the event payload.
        """
        self.user.password = 'new password'
        self.user.save()
        self.assert_user_setting_event_emitted(setting='password', old=None, new=None)

    def test_related_fields_ignored(self):
        """
        Verify that we don't emit events for related fields.
        """
        self.user.loginfailures_set.create()
        self.user.save()
        self.assert_no_events_were_emitted()

    @mock.patch('django.contrib.auth.models.User.save', side_effect=IntegrityError)
    def test_no_event_if_save_failed(self, _save_mock):
        """
        Verify no event is triggered if the save does not complete. Note that the pre_save
        signal is not called in this case either, but the intent is to make it clear that this model
        should never emit an event if save fails.
        """
        self.user.password = 'new password'
        with pytest.raises(IntegrityError):
            self.user.save()
        self.assert_no_events_were_emitted()

    def test_no_first_and_last_name_events(self):
        """
        Verify that first_name and last_name events are not emitted.
        """
        self.user.first_name = "Donald"
        self.user.last_name = "Duck"
        self.user.save()
        self.assert_no_events_were_emitted()

    def test_enrolled_after_email_change(self):
        """
        Test that when a user's email changes, the user is enrolled in pending courses.
        """
        pending_enrollment = CourseEnrollmentAllowedFactory(auto_enroll=True)  # lint-amnesty, pylint: disable=unused-variable

        # Create a CourseOverview for the enrollment course
        course_overview = CourseOverviewFactory.create(id=pending_enrollment.course_id)
        course_overview.save()

        # the e-mail will change to test@edx.org (from something else)
        assert self.user.email != 'test@edx.org'

        # there's a CEA for the new e-mail
        assert CourseEnrollmentAllowed.objects.count() == 1
        assert CourseEnrollmentAllowed.objects.filter(email='test@edx.org').count() == 1

        # Changing the e-mail to the enrollment-allowed e-mail should enroll
        self.user.email = 'test@edx.org'
        self.user.save()
        self.assert_user_enrollment_occurred('edX/toy/2012_Fall')

        # CEAs shouldn't have been affected
        assert CourseEnrollmentAllowed.objects.count() == 1
        assert CourseEnrollmentAllowed.objects.filter(email='test@edx.org').count() == 1


@skip_unless_lms
class EnrollmentEventsTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Tests for the Open edX Events associated with the enrollment process through the enroll method.

    This class guarantees that the following events are sent during the user's enrollment, with
    the exact Data Attributes as the event definition stated:

        - COURSE_ENROLLMENT_CREATED: sent after the user's enrollment.
        - COURSE_ENROLLMENT_CHANGED: sent after the enrollment update.
        - COURSE_UNENROLLMENT_COMPLETED: sent after the user's unenrollment.
    """

    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.enrollment.created.v1",
        "org.openedx.learning.course.enrollment.changed.v1",
        "org.openedx.learning.course.unenrollment.completed.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create(
            username="test",
            email="test@example.com",
            password="password",
        )
        self.user_profile = UserProfileFactory.create(user=self.user, name="Test Example")
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_enrollment_created_event_emitted(self):
        """
        Test whether the student enrollment event is sent after the user's
        enrollment process.

        Expected result:
            - COURSE_ENROLLMENT_CREATED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        COURSE_ENROLLMENT_CREATED.connect(event_receiver)

        enrollment = CourseEnrollment.enroll(self.user, self.course.id)

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_ENROLLMENT_CREATED,
                "sender": None,
                "enrollment": CourseEnrollmentData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=CourseData(
                        course_key=self.course.id,
                        display_name=self.course.display_name,
                    ),
                    mode=enrollment.mode,
                    is_active=enrollment.is_active,
                    creation_date=enrollment.created,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    def test_enrollment_changed_event_emitted(self):
        """
        Test whether the student enrollment changed event is sent after the enrollment
        update process ends.

        Expected result:
            - COURSE_ENROLLMENT_CHANGED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        COURSE_ENROLLMENT_CHANGED.connect(event_receiver)

        enrollment.update_enrollment(mode="verified")

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_ENROLLMENT_CHANGED,
                "sender": None,
                "enrollment": CourseEnrollmentData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=CourseData(
                        course_key=self.course.id,
                        display_name=self.course.display_name,
                    ),
                    mode=enrollment.mode,
                    is_active=enrollment.is_active,
                    creation_date=enrollment.created,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    def test_unenrollment_completed_event_emitted(self):
        """
        Test whether the student un-enrollment completed event is sent after the
        user's unenrollment process.

        Expected result:
            - COURSE_UNENROLLMENT_COMPLETED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        COURSE_UNENROLLMENT_COMPLETED.connect(event_receiver)

        CourseEnrollment.unenroll(self.user, self.course.id)

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_UNENROLLMENT_COMPLETED,
                "sender": None,
                "enrollment": CourseEnrollmentData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=CourseData(
                        course_key=self.course.id,
                        display_name=self.course.display_name,
                    ),
                    mode=enrollment.mode,
                    is_active=False,
                    creation_date=enrollment.created,
                ),
            },
            event_receiver.call_args.kwargs,
        )


@skip_unless_lms
@ddt.ddt
class TestCourseAccessRoleEvents(TestCase, OpenEdxEventsTestMixin):
    """
    Tests for the events associated with the CourseAccessRole model.
    """
    ENABLED_OPENEDX_EVENTS = [
        'org.openedx.learning.user.course_access_role.added.v1',
        'org.openedx.learning.user.course_access_role.removed.v1',
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        self.course_key = CourseKey.from_string("course-v1:test+blah+blah")
        self.user = UserFactory.create(
            username="test",
            email="test@example.com",
            password="password",
        )
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    @ddt.data(
        CourseStaffRole,
        CourseInstructorRole,
    )
    def test_access_role_created_event_emitted(self, AccessRole):
        """
        Event is emitted with the correct data when a CourseAccessRole is created.
        """
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        COURSE_ACCESS_ROLE_ADDED.connect(event_receiver)

        role = AccessRole(self.course_key)
        role.add_users(self.user)

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_ACCESS_ROLE_ADDED,
                "sender": None,
                "course_access_role_data": CourseAccessRoleData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course_key=self.course_key,
                    org_key=self.course_key.org,
                    role=role._role_name,  # pylint: disable=protected-access
                ),
            },
            event_receiver.call_args.kwargs,
        )

    @ddt.data(
        CourseStaffRole,
        CourseInstructorRole,
    )
    def test_access_role_removed_event_emitted(self, AccessRole):
        """
        Event is emitted with the correct data when a CourseAccessRole is deleted.
        """
        role = AccessRole(self.course_key)
        role.add_users(self.user)

        # connect mock only after initial role is added
        event_receiver = mock.Mock(side_effect=self._event_receiver_side_effect)
        COURSE_ACCESS_ROLE_REMOVED.connect(event_receiver)
        role.remove_users(self.user)

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_ACCESS_ROLE_REMOVED,
                "sender": None,
                "course_access_role_data": CourseAccessRoleData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course_key=self.course_key,
                    org_key=self.course_key.org,
                    role=role._role_name,  # pylint: disable=protected-access
                ),
            },
            event_receiver.call_args.kwargs,
        )
