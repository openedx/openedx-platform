"""
Feature flag support for experiments
"""

import datetime
import logging

import dateutil
import pytz
from crum import get_current_request
from edx_django_utils.cache import RequestCache

from common.djangoapps.track import segment
from lms.djangoapps.experiments.stable_bucketing import stable_bucketing_hash_group
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

log = logging.getLogger(__name__)


class ExperimentWaffleFlag(CourseWaffleFlag):
    """
    ExperimentWaffleFlag handles logic around experimental bucketing and whitelisting.

    You'll have one main flag that gates the experiment. This allows you to control the scope
    of your experiment and always provides a quick kill switch.

    But you'll also have smaller related flags that can force bucketing certain users into
    specific buckets of your experiment. Those can be set using a waffle named like
    "main_flag.BUCKET_NUM" (e.g. "course_experience.animated_exy.0") to force
    users that pass the first main waffle check into a specific bucket experience.

    If a user is not forced into a specific bucket by one of the aforementioned smaller flags,
    then they will be randomly assigned a default bucket based on a consistent hash of:
      * (flag_name, course_key, username) if use_course_aware_bucketing=True, or
      * (flag_name, username)             if use_course_aware_bucketing=False.

    Note that you may call `.get_bucket` and `.is_enabled` without a course_key, in which case:
    * the smaller flags will be evaluated without course context, and
    * the default bucket will be evaluated as if use_course_aware_bucketing=False.

    You can also control whether the experiment only affects future enrollments by setting
    an ExperimentKeyValue model object with a key of 'enrollment_start' to the date of the
    first enrollments that should be bucketed.

    Bucket 0 is assumed to be the control bucket.

    See a HOWTO here: https://openedx.atlassian.net/wiki/spaces/AC/pages/1250623700/Bucketing+users+for+an+experiment

    When writing tests involving an ExperimentWaffleFlag you must not use the
    override_waffle_flag utility. That will only turn the experiment on or off and won't
    override bucketing. Instead use override_experiment_waffle_flag function which
    will do both. Example:

        from lms.djangoapps.experiments.testutils import override_experiment_waffle_flag
        with @override_experiment_waffle_flag(MY_EXPERIMENT_WAFFLE_FLAG, active=True, bucket=1):
            ...

    or as a decorator:

        @override_experiment_waffle_flag(MY_EXPERIMENT_WAFFLE_FLAG, active=True, bucket=1)
        def test_my_experiment(self):
            ...
    """

    def __init__(
            self,
            flag_name,
            module_name,
            num_buckets=2,
            experiment_id=None,
            use_course_aware_bucketing=True,
            **kwargs
    ):
        super().__init__(flag_name, module_name, **kwargs)
        self.num_buckets = num_buckets
        self.experiment_id = experiment_id
        self.bucket_flags = [
            CourseWaffleFlag(f'{flag_name}.{bucket}', module_name)  # pylint: disable=toggle-missing-annotation
            for bucket in range(num_buckets)
        ]
        self.use_course_aware_bucketing = use_course_aware_bucketing

    @property
    def _app_label(self):
        """
        By convention, the app label associated to an experiment waffle flag is the dotted prefix of the flag name. For
        example: if the flag name is "grades.my.experiment.waffle.flag", then the `_app_label` will be "grades".
        This app label replaces what was formerly known as the waffle flag namespace.
        """
        return self._split_name[0]

    @property
    def _experiment_name(self):
        """
        By convention, the app label associated to an experiment waffle flag is the first dotted suffix of the flag
        name. For example: if the flag name name is "grades.my.experiment.waffle.flag", then the `_experiment_name`
        will be "my.experiment.waffle.flag".
        """
        return self._split_name[1]

    @property
    def _split_name(self):
        """
        Return the flag name prefix (before the first dot) and suffix. This raises a ValueError if the flag does not
        contain a dot ".".
        """
        prefix, suffix = self.name.split(".", maxsplit=1)
        return prefix, suffix

    def _cache_bucket(self, key, value):
        request_cache = RequestCache('experiments')
        request_cache.set(key, value)
        return value

    def _is_enrollment_inside_date_bounds(self, experiment_values, user, course_key):
        """ Returns True if the user's enrollment (if any) is valid for the configured experiment date range """
        from common.djangoapps.student.models import CourseEnrollment

        enrollment_start = experiment_values.get('enrollment_start')
        enrollment_end = experiment_values.get('enrollment_end')
        if not enrollment_start and not enrollment_end:
            return True  # early exit just to avoid any further lookups

        now = datetime.datetime.now(pytz.utc)
        enrollment = CourseEnrollment.get_enrollment(user, course_key)

        # If the user isn't enrolled, act like they would enroll right now (this keeps the pre-enroll and post-enroll
        # experiences the same, if they decide to enroll right now)
        enrollment_creation_date = enrollment.created if enrollment else now

        # Enrollment must be after any enrollment_start date, if specified
        if enrollment_start:
            try:
                start_date = dateutil.parser.parse(enrollment_start).replace(tzinfo=pytz.UTC)
            except ValueError:
                log.exception('Could not parse enrollment start date for experiment %d', self.experiment_id)
                return False
            if enrollment_creation_date < start_date:
                return False

        # Enrollment must be before any enrollment_end date, if specified
        if enrollment_end:
            try:
                end_date = dateutil.parser.parse(enrollment_end).replace(tzinfo=pytz.UTC)
            except ValueError:
                log.exception('Could not parse enrollment end date for experiment %d', self.experiment_id)
                return False
            if enrollment_creation_date >= end_date:
                return False

        # All good! Either because the key was not set or because the enrollment was valid
        return True

    def get_bucket(self, course_key=None, track=True):
        """
        Return which bucket number the specified user is in.

        The user may be force-bucketed if matching subordinate flags of the form
        "main_flag.BUCKET_NUM" exist. Otherwise, they will be hashed into a default
        bucket based on their username, the experiment name, and the course-run key.

        If `self.use_course_aware_bucketing` is False, the course-run key will
        be omitted from the hashing formula, thus making it so a given user
        has the same default bucket across all course runs; however, subordinate
        flags that match the course-run key will still apply.

        If `course_key` argument is omitted altogether, then subordinate flags
        will be evaluated outside of the course-run context, and the default bucket
        will be calculated as if `self.use_course_aware_bucketing` is False.

        Finally, Bucket 0 is assumed to be the control bucket and will be returned if the
        experiment is not enabled for this user and course.

        Arguments:
            course_key (Optional[CourseKey])
                This argument should always be passed in a course-aware context even if
                course aware bucketing is False.
            track (bool):
                Whether an analytics event should be generated if the user is
                bucketed for the first time.

        Returns: int
        """
        # Keep some imports in here, because this class is commonly used at a module level, and we want to avoid
        # circular imports for any models.
        from lms.djangoapps.courseware.masquerade import get_specific_masquerading_user
        from lms.djangoapps.experiments.models import ExperimentKeyValue

        request = get_current_request()
        if not request:
            return 0

        if hasattr(request, 'user'):
            user = get_specific_masquerading_user(request.user, course_key)

            if user is None:
                user = request.user
                masquerading_as_specific_student = False
            else:
                masquerading_as_specific_student = True

        # If a course key is passed in, include it in the experiment name
        # in order to separate caches and analytics calls per course-run.
        # If we are using course-aware bucketing, then also append that course key
        # to `bucketing_group_name`, such that users can be hashed into different
        # buckets for different course-runs.
        experiment_name = bucketing_group_name = self.name
        if course_key:
            experiment_name += f".{course_key}"
        if course_key and self.use_course_aware_bucketing:
            bucketing_group_name += f".{course_key}"

        # Check if we have a cache for this request already
        request_cache = RequestCache('experiments')
        cache_response = request_cache.get_cached_response(experiment_name)
        if cache_response.is_found:
            return cache_response.value

        # Check if the main flag is even enabled for this user and course.
        if not self.is_experiment_on(course_key):  # grabs user from the current request, if any
            return self._cache_bucket(experiment_name, 0)

        # Check if the enrollment should even be considered (if it started before the experiment wants, we ignore)
        if course_key and self.experiment_id is not None:
            values = ExperimentKeyValue.objects.filter(experiment_id=self.experiment_id).values('key', 'value')
            values = {pair['key']: pair['value'] for pair in values}

            if not self._is_enrollment_inside_date_bounds(values, user, course_key):
                return self._cache_bucket(experiment_name, 0)

        # Determine the user's bucket.
        # First check if forced into a particular bucket, using our subordinate bucket flags.
        # If not, calculate their default bucket using a consistent hash function.
        for i, bucket_flag in enumerate(self.bucket_flags):
            if bucket_flag.is_enabled(course_key):
                bucket = i
                break
        else:
            bucket = stable_bucketing_hash_group(
                bucketing_group_name, self.num_buckets, user
            )

        session_key = f'tracked.{experiment_name}'
        anonymous = not hasattr(request, 'user') or not request.user.id
        if (
                track and hasattr(request, 'session') and
                session_key not in request.session and
                not masquerading_as_specific_student and not anonymous  # pylint: disable=used-before-assignment
        ):
            segment.track(
                user_id=user.id,
                event_name='edx.bi.experiment.user.bucketed',
                properties={
                    'site': request.site.domain,
                    'app_label': self._app_label,
                    'experiment': self._experiment_name,
                    'course_id': str(course_key) if course_key else None,
                    'bucket': bucket,
                    'is_staff': user.is_staff,
                    'nonInteraction': 1,
                }
            )

            # Mark that we've recorded this bucketing, so that we don't do it again this session
            request.session[session_key] = True

        return self._cache_bucket(experiment_name, bucket)

    def is_enabled(self, course_key=None):
        """
        Return whether the requesting user is in a nonzero bucket for the given course.

        See the docstring of `.get_bucket` for more details.

        Arguments:
            course_key (Optional[CourseKey])

        Returns: bool
        """
        return self.get_bucket(course_key) != 0

    def is_experiment_on(self, course_key=None):
        """
        Return whether the overall experiment flag is enabled for this user.

        This disregards `.bucket_flags`.
        """
        return super().is_enabled(course_key)
