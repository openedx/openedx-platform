"""
Tests for schedules config flag code
"""

import crum
import ddt
from django.test import TestCase
from django.test.client import RequestFactory
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.experiments.models import ExperimentData
from lms.djangoapps.experiments.testutils import override_experiment_waffle_flag
from openedx.core.djangoapps.schedules.config import (
    _EXTERNAL_COURSE_UPDATES_FLAG,
    query_external_updates,
    set_up_external_updates_for_enrollment,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms


@ddt.ddt
@skip_unless_lms
class ScheduleConfigExternalUpdatesTests(TestCase):
    """Tests for the 'external course updates' experiment code"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory()
        cls.course_key = CourseKey.from_string('A/B/C')

    def set_request(self, with_user=True, user=None):
        """Configures a current request, as required by the experiment code"""
        request = RequestFactory()
        if with_user:
            request.user = user or self.user

        self.addCleanup(crum.set_current_request, None)
        crum.set_current_request(request)

    def set_up_updates(self, active=True, bucket=1):
        """Sets up the external updates experiment data, with the given bucket"""
        with override_experiment_waffle_flag(_EXTERNAL_COURSE_UPDATES_FLAG, active=active, bucket=bucket):
            return set_up_external_updates_for_enrollment(self.user, self.course_key)

    def test_set_up_fails_with_no_request(self):
        """No request fails"""
        assert self.set_up_updates() == -1

    def test_set_up_fails_with_no_user(self):
        """No user fails"""
        self.set_request(with_user=False)
        assert self.set_up_updates() == -1

    def test_set_up_fails_with_anon_user(self):
        """Anon user fails"""
        self.set_request(user=UserFactory(id=0))
        assert self.set_up_updates() == -1

    def test_set_up_fails_with_different_user(self):
        """Different user fails"""
        self.set_request(user=UserFactory())
        assert self.set_up_updates() == -1

    def test_set_up_happy_path(self):
        """Sanity check above tests by just needing a request and confirming we're good"""
        self.set_request()
        assert self.set_up_updates() == 1  # bucket 1 is default for set_up_updates

    @ddt.data(
        (True, 0, 0),  # bucket zero
        (True, 1, 1),  # bucket one
        (False, 0, -1),  # experiment off
    )
    @ddt.unpack
    def test_set_up_returns_and_saves_result(self, active, bucket, expected):
        """Confirm that the setup call works and saves the result in the database"""
        self.set_request()

        assert self.set_up_updates(active=active, bucket=bucket) == expected

        stored = ExperimentData.objects.get(experiment_id=18, user_id=self.user.id, key=str(self.course_key))
        assert stored.value == str(expected)

    def test_set_up_does_not_change_results(self):
        """Confirm that the setup call will not change its answer as flag changes"""
        self.set_request()

        assert self.set_up_updates() == 1
        assert self.set_up_updates(active=False) == 1

        # Sanity check that if we wipe saved data, we do get -1 for that last call again
        ExperimentData.objects.all().delete()
        assert self.set_up_updates(active=False) == -1

    def test_query_external_updates(self):
        """Check that the query method hits ExperimentData correctly (and not any waffle code)"""
        user2 = UserFactory()
        user3 = UserFactory()
        user4 = UserFactory()
        ExperimentData.objects.create(experiment_id=18, user_id=self.user.id, key='A/B/C', value='1')
        ExperimentData.objects.create(experiment_id=18, user_id=user2.id, key='A/B/C', value='0')
        ExperimentData.objects.create(experiment_id=18, user_id=user3.id, key='A/B/C', value='-1')
        ExperimentData.objects.create(experiment_id=18, user_id=user4.id, key='A/B/C', value='1')
        ExperimentData.objects.create(experiment_id=18, user_id=self.user.id, key='A/B/D', value='1')
        ExperimentData.objects.create(experiment_id=18, user_id=self.user.id, key='A/B/E', value='0')
        ExperimentData.objects.create(experiment_id=18, user_id=self.user.id, key='A/B/F', value='-1')

        assert query_external_updates(self.user.id, 'A/B/C')
        assert query_external_updates(self.user.id, 'A/B/D')
        assert not query_external_updates(self.user.id, 'A/B/E')
        assert not query_external_updates(self.user.id, 'A/B/F')

        assert not query_external_updates(user2.id, 'A/B/C')
        assert not query_external_updates(user3.id, 'A/B/C')
        assert query_external_updates(user4.id, 'A/B/C')
