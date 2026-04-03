"""
Test models, managers, and validators.
"""


import ddt
from completion.test_utils import CompletionWaffleTestMixin
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_switch
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


@ddt.ddt
@skip_unless_lms
class CompletionBatchTestCase(CompletionWaffleTestMixin, ModuleStoreTestCase):
    """
    Test that BlockCompletion.objects.submit_batch_completion has the desired
    semantics.
    """
    ENROLLED_USERNAME = 'test_user'
    UNENROLLED_USERNAME = 'unenrolled_user'
    COURSE_KEY = 'course-v1:TestX+101+Test'
    BLOCK_KEY = 'block-v1:TestX+101+Test+type@problem+block@Test_Problem'

    def setUp(self):
        """
        Create the test data.
        """
        super().setUp()
        self.url = reverse('completion:v1:completion-batch')

        # Enable the waffle flag for all tests
        self.override_waffle_switch(True)

        # Create course
        self.course = CourseFactory.create(
            org='TestX', number='101', display_name='Test',
            default_store=ModuleStoreEnum.Type.split,
        )
        assert str(self.course.id) == self.COURSE_KEY
        self.problem = BlockFactory.create(
            parent=self.course, category="problem", display_name="Test Problem", publish_item=False,
        )
        assert str(self.problem.location) == self.BLOCK_KEY

        # Create users
        self.staff_user = UserFactory(is_staff=True)
        self.enrolled_user = UserFactory(username=self.ENROLLED_USERNAME)
        self.unenrolled_user = UserFactory(username=self.UNENROLLED_USERNAME)

        # Enrol one user in the course
        CourseEnrollmentFactory.create(user=self.enrolled_user, course_id=self.course.id)

        # Login the enrolled user by for all tests
        self.client = APIClient()
        self.client.force_authenticate(user=self.enrolled_user)

    def test_enable_completion_tracking(self):
        """
        Test response when the waffle switch is disabled (default).
        """
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, False):
            response = self.client.post(self.url, {'username': self.ENROLLED_USERNAME}, format='json')
        assert response.data == \
               {
                   'detail': 'BlockCompletion.objects.submit_batch_completion'
                             ' should not be called when the feature is disabled.'
               }
        assert response.status_code == 400

    @ddt.data(
        # Valid submission
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 200, {'detail': 'ok'}
        ),
        # Blocks list can be empty, though it's a no-op
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': [],
            }, 200, {"detail": "ok"}
        ),
        # Course must be a valid key
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': "not:a:course:key",
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 400, {"detail": "Invalid learning context key: not:a:course:key"}
        ),
        # Block must be a valid key
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': {
                    'not:a:block:key': 1.0,
                }
            }, 400, {"detail": "Invalid block key: not:a:block:key"}
        ),
        # Block not in course
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': {
                    'block-v1:TestX+101+OtherCourse+type@problem+block@other': 1.0,
                }
            },
            400,
            {
                "detail": (
                    "Block with key: 'block-v1:TestX+101+OtherCourse+type@problem+block@other' "
                    "is not in context {}".format(COURSE_KEY)
                )
            }
        ),
        # Course key is required
        (
            {
                'username': ENROLLED_USERNAME,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 400, {"detail": "Key 'course_key' not found."}
        ),
        # Blocks is required
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': COURSE_KEY,
            }, 400, {"detail": "Key 'blocks' not found."}
        ),
        # Ordinary users can only update their own completions
        (
            {
                'username': UNENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 403, {"detail": "You do not have permission to perform this action."}
        ),
        # Username is required
        (
            {
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 403, {"detail": 'You do not have permission to perform this action.'}
        ),
        # Course does not exist
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': 'course-v1:TestX+101+Test2',
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 400, {"detail": "User is not enrolled in course."}
        ),
    )
    @ddt.unpack
    def test_batch_submit(self, payload, expected_status, expected_data):
        """
        Test the batch submission response for student users.
        """
        response = self.client.post(self.url, payload, format='json')
        assert response.data == expected_data
        assert response.status_code == expected_status

    @ddt.data(
        # Staff can submit completion on behalf of other users
        (
            {
                'username': ENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 200, {'detail': 'ok'}
        ),
        # User must be enrolled in the course
        (
            {
                'username': UNENROLLED_USERNAME,
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 400, {"detail": "User is not enrolled in course."}
        ),
        # Username is required
        (
            {
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 400, {"detail": "Key 'username' not found."}
        ),
        # User must not exist
        (
            {
                'username': 'doesntexist',
                'course_key': COURSE_KEY,
                'blocks': {
                    BLOCK_KEY: 1.0,
                }
            }, 404, {"detail": 'User matching query does not exist.'}
        ),
    )
    @ddt.unpack
    def test_batch_submit_staff(self, payload, expected_status, expected_data):
        """
        Test the batch submission response when logged in as a staff user.
        """
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.post(self.url, payload, format='json')
        assert response.data == expected_data
        assert response.status_code == expected_status
