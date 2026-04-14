""" Tests for the Calendar Sync models """


from common.djangoapps.student.tests.factories import UserFactory
from openedx.features.calendar_sync.models import UserCalendarSyncConfig
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

TEST_PASSWORD = 'test'


class TestUserCalendarSyncConfig(SharedModuleStoreTestCase):
    """ Tests for the UserCalendarSyncConfig model """
    @classmethod
    def setUpClass(cls):
        """ Set up any course data """
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.user = UserFactory(password=TEST_PASSWORD)

    def test_is_enabled_for_course(self):
        # Calendar Sync Config does not exist and returns False
        assert not UserCalendarSyncConfig.is_enabled_for_course(self.user, self.course_key)

        # Default value for enabled is False
        UserCalendarSyncConfig.objects.create(user=self.user, course_key=self.course_key)
        assert not UserCalendarSyncConfig.is_enabled_for_course(self.user, self.course_key)

        UserCalendarSyncConfig.objects.filter(user=self.user, course_key=self.course_key).update(enabled=True)
        assert UserCalendarSyncConfig.is_enabled_for_course(self.user, self.course_key)
