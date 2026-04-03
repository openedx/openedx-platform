"""
Unit tests for the `user_api` app's public Python interface.
"""


from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.api import get_retired_user_ids
from openedx.core.djangoapps.user_api.models import RetirementState, UserRetirementRequest, UserRetirementStatus
from openedx.core.djangoapps.user_api.tests.factories import (
    RetirementStateFactory,
    UserRetirementRequestFactory,
    UserRetirementStatusFactory
)


class UserApiRetirementTests(TestCase):
    """
    Tests for utility functions exposed by the `user_api` app's public Python interface that are related to the user
    retirement pipeline.
    """

    @classmethod
    def setUpClass(cls):
        """
        The retirement pipeline is not fully enabled by default. We must ensure that the required RetirementState's
        exist before executing any of our unit tests.
        """
        super().setUpClass()
        cls.pending = RetirementStateFactory(state_name="PENDING")
        cls.complete = RetirementStateFactory(state_name="COMPLETE")

    @classmethod
    def tearDownClass(cls):
        # Remove any retirement state objects that we created during this test suite run.
        RetirementState.objects.all().delete()
        super().tearDownClass()

    def tearDown(self):
        # clear retirement requests and related data between each test
        UserRetirementRequest.objects.all().delete()
        UserRetirementStatus.objects.all().delete()
        super().tearDown()

    def test_get_retired_user_ids(self):
        """
        A unit test to verify that the only user id's returned from the `get_retired_user_ids` function are learners who
        aren't in the "PENDING" state.
        """
        user_pending = UserFactory()
        # create a retirement request and status entry for a learner in the PENDING state
        UserRetirementRequestFactory(user=user_pending)
        UserRetirementStatusFactory(user=user_pending, current_state=self.pending, last_state=self.pending)
        user_complete = UserFactory()
        # create a retirement request and status entry for a learner in the COMPLETE state
        UserRetirementRequestFactory(user=user_complete)
        UserRetirementStatusFactory(user=user_complete, current_state=self.complete, last_state=self.complete)

        results = get_retired_user_ids()
        assert len(results) == 1
        assert results == [user_complete.id]

    def test_get_retired_user_ids_no_results(self):
        """
        A unit test to verify that if the only retirement requests pending are in the "PENDING" state, we don't return
        any learners' user_ids when calling the `get_retired_user_ids` function.
        """
        user_pending_1 = UserFactory()
        # create a retirement request and status entry for a learner in the PENDING state
        UserRetirementRequestFactory(user=user_pending_1)
        UserRetirementStatusFactory(
            user=user_pending_1,
            current_state=self.pending,
            last_state=self.pending,
        )
        user_pending_2 = UserFactory()
        # create a retirement request and status entry for a learner in the PENDING state
        UserRetirementRequestFactory(user=user_pending_2)
        UserRetirementStatusFactory(
            user=user_pending_2,
            current_state=self.pending,
            last_state=self.pending,
        )
        results = get_retired_user_ids()
        assert len(results) == 0
        assert not results
