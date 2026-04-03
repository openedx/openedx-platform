"""
Tests for toggles, where there is logic beyond enable/disable.
"""

from unittest.mock import patch

import ddt

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.learner_home.waffle import learner_home_mfe_enabled
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase


@ddt.ddt
class TestLearnerHomeWaffle(SharedModuleStoreTestCase):
    """
    Tests for learner_home_mfe_enabled
    """

    def setUp(self):
        super().setUp()

        # Set up a user for testing
        self.user = UserFactory

    @ddt.data(True, False)
    @patch("lms.djangoapps.learner_home.waffle.ENABLE_LEARNER_HOME_MFE")
    def test_learner_home_mfe_enabled(
        self, is_waffle_enabled, mock_enable_learner_home
    ):
        # Given Learner Home MFE feature is / not enabled
        mock_enable_learner_home.is_enabled.return_value = is_waffle_enabled

        # When I check if the feature is enabled
        is_learner_home_enabled = learner_home_mfe_enabled()

        # Then I respects waffle setting.
        self.assertEqual(is_learner_home_enabled, is_waffle_enabled)
