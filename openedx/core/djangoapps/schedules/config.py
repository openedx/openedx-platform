"""
Contains configuration for schedules app
"""

from crum import get_current_request
from edx_toggles.toggles import WaffleFlag, WaffleSwitch

from lms.djangoapps.experiments.flags import ExperimentWaffleFlag
from lms.djangoapps.experiments.models import ExperimentData

WAFFLE_NAMESPACE = 'schedules'

# .. toggle_name: schedules.enable_debugging
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enable debug level of logging for schedules messages.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-09-17
DEBUG_MESSAGE_WAFFLE_FLAG = WaffleFlag(f'{WAFFLE_NAMESPACE}.enable_debugging', __name__)

COURSE_UPDATE_SHOW_UNSUBSCRIBE_WAFFLE_SWITCH = WaffleSwitch(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.course_update_show_unsubscribe', __name__
)

# This experiment waffle is supporting an A/B test we are running on sending course updates from an external service,
# rather than through platform and ACE. See ticket AA-661 for more information.
# Don't use this flag directly, instead use the `set_up_external_updates_for_enrollment` and `query_external_updates`
# methods below. We save this flag decision at enrollment time and don't change it even if the flag changes. So you
# can't just directly look at flag result.
_EXTERNAL_COURSE_UPDATES_EXPERIMENT_ID = 18
_EXTERNAL_COURSE_UPDATES_FLAG = ExperimentWaffleFlag(  # pylint: disable=toggle-missing-annotation
    f'{WAFFLE_NAMESPACE}.external_updates', __name__,
    experiment_id=_EXTERNAL_COURSE_UPDATES_EXPERIMENT_ID,
    use_course_aware_bucketing=False
)


def set_up_external_updates_for_enrollment(user, course_key):
    """
    Returns and stores whether a user should be getting the "external course updates" experience.

    See the description of this experiment with the waffle flag definition above. But basically, if a user is getting
    external course updates for a course, edx-platform just stops sending any updates, trustingn that the user is
    receiving them elsewhere.

    This is basically just a wrapper around our experiment waffle flag, but only buckets users that directly enrolled
    (rather than users enrolled by staff), for technical "waffle-flags-can-only-get-the-user-from-the-request" reasons.

    This saves the decision in experiment data tables. It is also idempotent and will not change after the first
    call for a given user/course, regardless of how the waffle answer changes.
    """
    request = get_current_request()
    user_is_valid = request and hasattr(request, 'user') and request.user.id and request.user.id == user.id
    experiment_on = _EXTERNAL_COURSE_UPDATES_FLAG.is_experiment_on(course_key)
    if user_is_valid and experiment_on:
        # Don't send tracking info as it might differ from our saved value, and we already send the bucket in
        # enrollment segment events.
        bucket = _EXTERNAL_COURSE_UPDATES_FLAG.get_bucket(course_key, track=False)
    else:
        bucket = -1  # a special value meaning to ignore this enrollment for analytics purposes

    data, _created = ExperimentData.objects.get_or_create(experiment_id=_EXTERNAL_COURSE_UPDATES_EXPERIMENT_ID,
                                                          user_id=user.id, key=str(course_key),
                                                          defaults={'value': str(bucket)})
    return int(data.value)


def query_external_updates(user_id, course_id):
    """
    Returns a queryset indicating whether the user get the "external course updates" experience for the given course.

    This is designed for use as a subquery in a larger queryset, which is why it returns a queryset, rather than a
    boolean. But it can also be used to spot-check whether a user is in the external experience for a given course by
    casting the returned queryset to a bool.

    This looks up the experiment data, saved at enrollment time.
    """
    return ExperimentData.objects.filter(experiment_id=_EXTERNAL_COURSE_UPDATES_EXPERIMENT_ID,
                                         user_id=user_id, key=course_id, value='1')
