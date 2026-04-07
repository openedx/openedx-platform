"""
Tests for experimentation feature flags
"""

from unittest.mock import patch

import ddt
import pytz
from crum import set_current_request
from dateutil import parser
from django.test.client import RequestFactory
from edx_django_utils.cache import RequestCache
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.experiments.factories import ExperimentKeyValueFactory
from lms.djangoapps.experiments.flags import ExperimentWaffleFlag
from lms.djangoapps.experiments.testutils import override_experiment_waffle_flag
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)


@ddt.ddt
class ExperimentWaffleFlagTests(SharedModuleStoreTestCase):
    """ Tests for the ExperimentWaffleFlag class """
    def setUp(self):
        super().setUp()

        self.user = UserFactory()

        self.request = RequestFactory().request()
        self.request.session = {}
        self.request.site = SiteFactory()
        self.request.user = self.user
        self.addCleanup(set_current_request, None)
        set_current_request(self.request)

        self.flag = ExperimentWaffleFlag('experiments.test', __name__, num_buckets=2, experiment_id=0)  # lint-amnesty, pylint: disable=toggle-missing-annotation
        self.key = CourseKey.from_string('a/b/c')

        bucket_patch = patch('lms.djangoapps.experiments.flags.stable_bucketing_hash_group', return_value=1)
        self.addCleanup(bucket_patch.stop)
        bucket_patch.start()

        self.addCleanup(RequestCache.clear_all_namespaces)

    def get_bucket(self, track=False, active=True):
        # Does not use override_experiment_waffle_flag, since that shortcuts get_bucket and we want to test internals
        with override_waffle_flag(self.flag, active):
            with override_waffle_flag(self.flag.bucket_flags[1], True):
                return self.flag.get_bucket(course_key=self.key, track=track)

    def test_basic_happy_path(self):
        assert self.get_bucket() == 1

    def test_no_request(self):
        set_current_request(None)
        assert self.get_bucket() == 0

    def test_not_enabled(self):
        assert self.get_bucket(active=False) == 0

    @ddt.data(
        ('2012-01-06', None, 1),  # no enrollment, but start is in past (we allow normal bucketing in this case)
        ('9999-01-06', None, 0),  # no enrollment, but start is in future (we give bucket 0 in that case)
        ('2012-01-06', '2012-01-05', 0),  # enrolled before experiment start
        ('2012-01-06', '2012-01-07', 1),  # enrolled after experiment start
        (None, '2012-01-07', 1),  # no experiment date
        ('not-a-date', '2012-01-07', 0),  # bad experiment date
    )
    @ddt.unpack
    def test_enrollment_start(self, experiment_start, enrollment_created, expected_bucket):
        if enrollment_created:
            enrollment = CourseEnrollmentFactory(user=self.user, course_id='a/b/c')
            enrollment.created = parser.parse(enrollment_created).replace(tzinfo=pytz.UTC)
            enrollment.save()
        if experiment_start:
            ExperimentKeyValueFactory(experiment_id=0, key='enrollment_start', value=experiment_start)
        assert self.get_bucket() == expected_bucket

    @ddt.data(
        ('2012-01-06', None, 0),  # no enrollment, but end is in past (we give bucket 0 in that case)
        ('9999-01-06', None, 1),  # no enrollment, but end is in future (we allow normal bucketing in this case)
        ('2012-01-06', '2012-01-05', 1),  # enrolled before experiment end
        ('2012-01-06', '2012-01-07', 0),  # enrolled after experiment end
        (None, '2012-01-07', 1),  # no experiment date
        ('not-a-date', '2012-01-07', 0),  # bad experiment date
    )
    @ddt.unpack
    def test_enrollment_end(self, experiment_end, enrollment_created, expected_bucket):
        if enrollment_created:
            enrollment = CourseEnrollmentFactory(user=self.user, course_id='a/b/c')
            enrollment.created = parser.parse(enrollment_created).replace(tzinfo=pytz.UTC)
            enrollment.save()
        if experiment_end:
            ExperimentKeyValueFactory(experiment_id=0, key='enrollment_end', value=experiment_end)
        assert self.get_bucket() == expected_bucket

    @ddt.data(
        (True, 0),
        (False, 1),
    )
    @ddt.unpack
    def test_forcing_bucket(self, active, expected_bucket):
        bucket_flag = CourseWaffleFlag('experiments.test.0', __name__)  # lint-amnesty, pylint: disable=toggle-missing-annotation
        with override_waffle_flag(bucket_flag, active=active):
            assert self.get_bucket() == expected_bucket

    def test_tracking(self):
        # Run twice, with same request
        with patch('lms.djangoapps.experiments.flags.segment') as segment_mock:
            assert self.get_bucket(track=True) == 1
            RequestCache.clear_all_namespaces()  # we want to force get_bucket to check session, not early exit
            assert self.get_bucket(track=True) == 1

        # Now test that we only sent the signal once, and with the correct properties
        assert segment_mock.track.call_count == 1
        assert segment_mock.track.call_args == ((), {
            'user_id': self.user.id,
            'event_name': 'edx.bi.experiment.user.bucketed',
            'properties': {
                'site': self.request.site.domain,
                'app_label': 'experiments',
                'experiment': 'test',
                'bucket': 1,
                'course_id': 'a/b/c',
                'is_staff': self.user.is_staff,
                'nonInteraction': 1
            }
        })

    def test_caching(self):
        assert self.get_bucket(active=True) == 1
        assert self.get_bucket(active=False) == 1
        # still returns 1!

    def test_is_enabled(self):
        with patch('lms.djangoapps.experiments.flags.ExperimentWaffleFlag.get_bucket', return_value=1):
            assert self.flag.is_enabled(self.key) is True
            assert self.flag.is_enabled() is True
        with patch('lms.djangoapps.experiments.flags.ExperimentWaffleFlag.get_bucket', return_value=0):
            assert self.flag.is_enabled(self.key) is False
            assert self.flag.is_enabled() is False

    @ddt.data(
        (True, 1, 1),
        (True, 0, 0),
        (False, 1, 0),  # bucket is always 0 if the experiment is off
        (False, 0, 0),
    )
    @ddt.unpack
    # Test the override method
    def test_override_method(self, active, bucket_override, expected_bucket):
        with override_experiment_waffle_flag(self.flag, active=active, bucket=bucket_override):
            assert self.flag.get_bucket() == expected_bucket
            assert self.flag.is_experiment_on() == active

    def test_app_label_experiment_name(self):
        # pylint: disable=protected-access
        assert 'experiments' == self.flag._app_label
        assert 'test' == self.flag._experiment_name

        flag = ExperimentWaffleFlag("namespace.flag.name", __name__)  # lint-amnesty, pylint: disable=toggle-missing-annotation
        assert 'namespace' == flag._app_label
        assert 'flag.name' == flag._experiment_name


class ExperimentWaffleFlagCourseAwarenessTest(SharedModuleStoreTestCase):
    """
    Tests for how course context awareness/unawareness interacts with the
    ExperimentWaffleFlag class.
    """
    course_aware_flag = ExperimentWaffleFlag(  # lint-amnesty, pylint: disable=toggle-missing-annotation
        'exp.aware', __name__, num_buckets=20, use_course_aware_bucketing=True,
    )
    course_aware_subflag = CourseWaffleFlag('exp.aware.1', __name__)  # lint-amnesty, pylint: disable=toggle-missing-annotation

    course_unaware_flag = ExperimentWaffleFlag(  # lint-amnesty, pylint: disable=toggle-missing-annotation
        'exp.unaware', __name__, num_buckets=20, use_course_aware_bucketing=False,
    )
    course_unaware_subflag = CourseWaffleFlag('exp.unaware.1', __name__)  # lint-amnesty, pylint: disable=toggle-missing-annotation

    course_key_1 = CourseKey.from_string("x/y/1")
    course_key_2 = CourseKey.from_string("x/y/22")
    course_key_3 = CourseKey.from_string("x/y/333")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Force all users into Bucket 1 for course at `course_key_1`.
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag="exp.aware.1", course_id=cls.course_key_1, enabled=True
        )
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag="exp.unaware.1", course_id=cls.course_key_1, enabled=True
        )
        cls.user = UserFactory()

    def setUp(self):
        super().setUp()
        self.request = RequestFactory().request()
        self.request.session = {}
        self.request.site = SiteFactory()
        self.request.user = self.user
        self.addCleanup(set_current_request, None)
        set_current_request(self.request)
        self.addCleanup(RequestCache.clear_all_namespaces)

        # Enable all experiment waffle flags.
        experiment_waffle_flag_patcher = patch.object(
            ExperimentWaffleFlag, 'is_experiment_on', return_value=True
        )
        experiment_waffle_flag_patcher.start()
        self.addCleanup(experiment_waffle_flag_patcher.stop)

        # Use our custom fake `stable_bucketing_hash_group` implementation.
        stable_bucket_patcher = patch(
            'lms.djangoapps.experiments.flags.stable_bucketing_hash_group', self._mock_stable_bucket
        )
        stable_bucket_patcher.start()
        self.addCleanup(stable_bucket_patcher.stop)

    @staticmethod
    def _mock_stable_bucket(group_name, *_args, **_kwargs):
        """
        A fake version of `stable_bucketing_hash_group` that just returns
        the length of `group_name`.
        """
        return len(group_name)

    def test_course_aware_bucketing(self):
        """
        Test behavior of an experiment flag configured wtih course-aware bucket hashing.
        """

        # Expect queries for Course 1 to be forced into Bucket 1
        # due to `course_aware_subflag`.
        assert self.course_aware_flag.get_bucket(self.course_key_1) == 1

        # Because we are using course-aware bucket hashing, different
        # courses may default to different buckets.
        # In the case of Courses 2 and 3 here, we expect two different buckets.
        assert self.course_aware_flag.get_bucket(self.course_key_2) == 16
        assert self.course_aware_flag.get_bucket(self.course_key_3) == 17

        # We can still query a course-aware flag outside of course context,
        # which has its own default bucket.
        assert self.course_aware_flag.get_bucket() == 9

    def test_course_unaware_bucketing(self):
        """
        Test behavior of an experiment flag configured wtih course-unaware bucket hashing.
        """

        # Expect queries for Course 1 to be forced into Bucket 1
        # due to `course_unaware_subflag`.
        # This should happen in spite of the fact that *default* bucketing
        # is unaware of courses.
        assert self.course_unaware_flag.get_bucket(self.course_key_1) == 1

        # Expect queries for Course 2, queries for Course 3, and queries outside
        # the context of the course to all be hashed into the same default bucket.
        assert self.course_unaware_flag.get_bucket(self.course_key_2) == 11
        assert self.course_unaware_flag.get_bucket(self.course_key_3) == 11
        assert self.course_unaware_flag.get_bucket() == 11
