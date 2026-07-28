"""
Test classes for the events sent in the cohort assignment process.

Classes:
    CohortEventTest: Test event sent after cohort membership changes.
"""
from unittest.mock import Mock  # pylint: disable=wrong-import-order

from openedx_events.learning.data import (  # pylint: disable=wrong-import-order
    CohortData,
    CourseData,
    UserData,
    UserPersonalData,
)
from openedx_events.learning.signals import (
    COHORT_MEMBERSHIP_CHANGED,  # pylint: disable=wrong-import-order
)
from openedx_events.testing import OpenEdxEventsTestMixin  # pylint: disable=wrong-import-order

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import assert_dict_contains_subset
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.course_groups.models import CohortMembership
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


@skip_unless_lms
class CohortEventTest(OpenEdxEventsTestMixin, SharedModuleStoreTestCase):
    """
    Tests for the Open edX Events associated with the cohort update process.

    This class guarantees that the following events are sent during the user's
    certification process, with the exact Data Attributes as the event definition stated:

        - COHORT_MEMBERSHIP_CHANGED: when a cohort membership update ends.
    """

    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.cohort_membership.changed.v1",
    ]

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseOverviewFactory()
        self.user = UserFactory.create(
            username="somestudent",
            first_name="Student",
            last_name="Person",
            email="robot@robot.org",
            is_active=True
        )
        self.cohort = CohortFactory(course_id=self.course.id, name="FirstCohort")
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_send_cohort_membership_changed_event(self):
        """
        Test whether the COHORT_MEMBERSHIP_CHANGED event is sent when a cohort
        membership update ends.

        Expected result:
            - COHORT_MEMBERSHIP_CHANGED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = Mock(side_effect=self._event_receiver_side_effect)
        COHORT_MEMBERSHIP_CHANGED.connect(event_receiver)

        cohort_membership, _ = CohortMembership.assign(
            cohort=self.cohort,
            user=self.user,
        )

        self.assertTrue(self.receiver_called)  # noqa: PT009
        assert_dict_contains_subset(
            self,
            {
                "signal": COHORT_MEMBERSHIP_CHANGED,
                "sender": None,
                "cohort": CohortData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=cohort_membership.user.username,
                            email=cohort_membership.user.email,
                            name=cohort_membership.user.profile.name,
                        ),
                        id=cohort_membership.user.id,
                        is_active=cohort_membership.user.is_active,
                    ),
                    course=CourseData(
                        course_key=cohort_membership.course_id,
                    ),
                    name=cohort_membership.course_user_group.name,
                ),
            },
            event_receiver.call_args.kwargs,
        )
