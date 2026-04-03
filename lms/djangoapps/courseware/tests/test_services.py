"""
Tests for courseware services.
"""


import itertools
import json

import ddt

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.services import UserStateService
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


@ddt.ddt
class TestUserStateService(ModuleStoreTestCase):
    """
    Test suite for user state service.
    """

    def setUp(self):
        """
        Creating pre-requisites for the test cases.
        """
        super().setUp()
        self.user = UserFactory.create()
        self.course = CourseFactory.create()
        chapter = BlockFactory.create(
            category='chapter',
            parent=self.course,
            display_name='Test Chapter'
        )
        sequential = BlockFactory.create(
            category='sequential',
            parent=chapter,
            display_name='Test Sequential'
        )
        vertical = BlockFactory.create(
            category='vertical',
            parent=sequential,
            display_name='Test Vertical'
        )
        self.problem = BlockFactory.create(
            category='problem',
            parent=vertical,
            display_name='Test Problem'
        )

    def _create_student_module(self, state):
        StudentModuleFactory.create(
            student=self.user,
            module_state_key=self.problem.location,
            course_id=self.course.id,
            state=json.dumps(state)
        )

    def _get_email_or_username(self, should_use_email):
        """
        Helper function that returns either username or email of the user based on given criteria.

        Arguments:
            should_use_email(bool): Flag to identify if the email is to be returned
        Returns:
            username/email depending upon the value of provided flag
        """
        return self.user.email if should_use_email else self.user.username

    @ddt.data(
        *itertools.product(
            [
                ({'key_1a': 'value_1a', 'key_2a': 'value_2a'}),
                ({'key_1b': 'value_1b', 'key_2b': 'value_2b', 'key_3b': 'value_3b'})
            ], [
                True, False
            ]
        ))
    @ddt.unpack
    def test_student_state(self, expected_state, should_use_email):
        """
        Verify the service gets the correct state from the student module.

        Scenario:
            Given a user and a problem or block
            Then create a student module entry for the user
            If the state is obtained from common.djangoapps.student module service
            Then the state is equal to previously created student module state
        """
        self._create_student_module(expected_state)
        state = UserStateService().get_state_as_dict(
            self._get_email_or_username(should_use_email), self.problem.location
        )
        self.assertDictEqual(state, expected_state)

    @ddt.data(
        *itertools.product(
            [
                ({'username_or_email': 'no_user', 'block_id':
                    'block-v1:myOrg+123+2030_T2+type@openassessment+block@hash'}),
                ({'username_or_email': 'no_user'}),
                ({'block_id': 'block-v1:myOrg+1234+2030_T2+type@openassessment+block@hash'})
            ], [
                True, False
            ]
        ))
    @ddt.unpack
    def test_nonexistent_student_module_state(self, state_params, should_use_email):
        """
        Verify the user state service returns empty dict for non-existent student module entry.

        Scenario:
            Given a user and a problem/block
            Then create a student module entry for the user
            If the state is obtained with incorrect parameters
            Then an empty dict is returned
        """
        params = {
            'username_or_email': self._get_email_or_username(should_use_email),
            'block_id': self.problem.location
        }
        params.update(state_params)
        self._create_student_module({'key_1': 'value_1'})
        state = UserStateService().get_state_as_dict(**params)
        assert not state
