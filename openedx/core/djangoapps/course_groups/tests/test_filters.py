"""
Test that various filters are executed for models in the course_groups app.
"""
from django.test import override_settings
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CohortAssignmentRequested, CohortChangeRequested

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.course_groups.models import (
    CohortAssignmentNotAllowed,
    CohortChangeNotAllowed,
    CohortMembership,
)
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase


class TestCohortChangeStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, current_membership, target_cohort):  # pylint: disable=arguments-differ
        """Pipeline step that adds cohort info to the users profile."""
        user = current_membership.user
        user.profile.set_meta(
            {
                "cohort_info":
                f"Changed from Cohort {str(current_membership.course_user_group)} to Cohort {str(target_cohort)}",
            }
        )
        user.profile.save()
        return {}


class TestCohortAssignmentStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user, target_cohort):  # pylint: disable=arguments-differ
        """Pipeline step that adds cohort info to the users profile."""
        user.profile.set_meta(
            {
                "cohort_info":
                f"User assigned to Cohort {str(target_cohort)}",
            }
        )
        user.profile.save()
        return {}


class TestStopCohortChangeStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, current_membership, target_cohort, *args, **kwargs):  # pylint: disable=arguments-differ
        """Pipeline step that stops the cohort change process."""
        raise CohortChangeRequested.PreventCohortChange("You can't change cohorts.")


class TestStopAssignmentChangeStep(PipelineStep):
    """
    Utility function used when getting steps for pipeline.
    """

    def run_filter(self, user, target_cohort, *args, **kwargs):  # pylint: disable=arguments-differ
        """Pipeline step that stops the cohort change process."""
        raise CohortAssignmentRequested.PreventCohortAssignment("You can't be assign to this cohort.")


@skip_unless_lms
class CohortFiltersTest(SharedModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the cohort change process.

    This class guarantees that the following filters are triggered during the user's cohort change:

    - CohortChangeRequested
    """

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
        self.first_cohort = CohortFactory(course_id=self.course.id, name="FirstCohort")
        self.second_cohort = CohortFactory(course_id=self.course.id, name="SecondCohort")

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.cohort.change.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.course_groups.tests.test_filters.TestCohortChangeStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_cohort_change_filter_executed(self):
        """
        Test whether the student cohort change filter is triggered before the user's
        changes cohort.

        Expected result:
            - CohortChangeRequested is triggered and executes TestCohortChangeStep.
            - The user's profile meta contains cohort_info.
        """
        CohortMembership.assign(cohort=self.first_cohort, user=self.user)

        cohort_membership, _ = CohortMembership.assign(cohort=self.second_cohort, user=self.user)

        self.assertEqual(
            {"cohort_info": "Changed from Cohort FirstCohort to Cohort SecondCohort"},
            cohort_membership.user.profile.get_meta(),
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.cohort.assignment.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.course_groups.tests.test_filters.TestCohortAssignmentStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_cohort_assignment_filter_executed(self):
        """
        Test whether the student cohort assignment filter is triggered before the user's
        assignment.

        Expected result:
            - CohortAssignmentRequested is triggered and executes TestCohortAssignmentStep.
            - The user's profile meta contains cohort_info.
        """

        cohort_membership, _ = CohortMembership.assign(user=self.user, cohort=self.second_cohort, )

        self.assertEqual(
            {"cohort_info": "User assigned to Cohort SecondCohort"},
            cohort_membership.user.profile.get_meta(),
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.cohort.change.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.course_groups.tests.test_filters.TestStopCohortChangeStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_cohort_change_filter_prevent_move(self):
        """
        Test prevent the user's cohort change through a pipeline step.

        Expected result:
            - CohortChangeRequested is triggered and executes TestStopCohortChangeStep.
            - The user can't change cohorts.
        """
        CohortMembership.assign(cohort=self.first_cohort, user=self.user)

        with self.assertRaises(CohortChangeNotAllowed):
            CohortMembership.assign(cohort=self.second_cohort, user=self.user)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.cohort.assignment.requested.v1": {
                "pipeline": [
                    "openedx.core.djangoapps.course_groups.tests.test_filters.TestStopAssignmentChangeStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_cohort_assignment_filter_prevent_move(self):
        """
        Test prevent the user's cohort assignment through a pipeline step.

        Expected result:
            - CohortAssignmentRequested is triggered and executes TestStopAssignmentChangeStep.
            - The user can't be assigned to the cohort.
        """
        with self.assertRaises(CohortAssignmentNotAllowed):
            CohortMembership.assign(cohort=self.second_cohort, user=self.user)

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_cohort_change_without_filter_configuration(self):
        """
        Test usual cohort change process, without filter's intervention.

        Expected result:
            - CohortChangeRequested does not have any effect on the cohort change process.
            - The cohort assignment process ends successfully.
        """
        CohortMembership.assign(cohort=self.first_cohort, user=self.user)

        cohort_membership, _ = CohortMembership.assign(cohort=self.second_cohort, user=self.user)

        self.assertEqual({}, cohort_membership.user.profile.get_meta())

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_cohort_assignment_without_filter_configuration(self):
        """
        Test usual cohort assignment process, without filter's intervention.

        Expected result:
            - CohortAssignmentRequested does not have any effect on the cohort change process.
            - The cohort assignment process ends successfully.
        """
        cohort_membership, _ = CohortMembership.assign(cohort=self.second_cohort, user=self.user)

        self.assertEqual({}, cohort_membership.user.profile.get_meta())
