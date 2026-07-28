"""
Test audit user's access to various content based on content-gating features.
"""
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ddt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test.client import Client, RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from pyquery import PyQuery as pq

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment, FBEEnrollmentExclusion
from common.djangoapps.student.roles import CourseInstructorRole
from common.djangoapps.student.tests.factories import (
    TEST_PASSWORD,
    BetaTesterFactory,
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    InstructorFactory,
    OrgInstructorFactory,
    OrgStaffFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.course_api.blocks.api import get_blocks
from lms.djangoapps.courseware.block_render import load_single_xblock
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
)
from openedx.core.djangoapps.user_api.tests.factories import UserCourseTagFactory
from openedx.core.djangoapps.util.testing import TestConditionalContent
from openedx.core.djangolib.markup import HTML
from openedx.core.lib.url_utils import quote_slashes
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID, CONTENT_TYPE_GATE_GROUP_IDS
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.content_type_gating.partitions import ContentTypeGatingPartition
from openedx.features.content_type_gating.services import ContentTypeGatingService
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.partitions.partitions import ENROLLMENT_TRACK_PARTITION_ID

METADATA = {
    'group_access': {
        CONTENT_GATING_PARTITION_ID: [CONTENT_TYPE_GATE_GROUP_IDS['full_access']]
    }
}
User = get_user_model()


@patch("crum.get_current_request")
def _get_content_from_fragment(_store, block, user_id, course, request_factory, mock_get_current_request):
    """
    Returns the content from the rendered fragment of a block
    Arguments:
        block: some sort of XBlock, must implement .scope_ids.usage_id
        user_id (int): id of user
        course_id (CourseLocator): id of course
    """
    fake_request = request_factory.get('')
    mock_get_current_request.return_value = fake_request

    # Load a block we know will pass access control checks
    block = load_single_xblock(
        request=fake_request,
        user_id=user_id,
        course_id=str(course.id),
        usage_key_string=str(block.scope_ids.usage_id),
        course=course,
        will_recheck_access=True,
    )
    # Attempt to render the block, this should return different fragments if the content is gated or not.
    frag = block.render('student_view')
    return frag.content


def _get_content_from_lms_index(store, block, user_id, _course, _request_factory):
    """
    Returns the content from the lms index view of the block
    Arguments:
        block: some sort of XBlock, must implement .scope_ids.usage_id
        user_id (int): id of user
    """
    client = Client()
    client.login(username=User.objects.get(id=user_id).username, password=TEST_PASSWORD)

    # Re-load the block from the store to ensure that its `parent` field is filled out correctly
    block = store.get_item(block.location)

    page_content = client.get(
        reverse('render_xblock', args=[str(block.parent)]) + '?recheck_access=1',
        follow=True,
    )

    page = pq(page_content.content)
    block_contents = page(f'[data-id="{block.scope_ids.usage_id}"]')
    return block_contents.html()


def _assert_block_is_gated(store, block, is_gated, user, course, request_factory, has_upgrade_link=True):
    """
    Asserts that a block in a specific course is gated for a specific user
    Arguments:
        block: some sort of XBlock, must implement .scope_ids.usage_id
        is_gated (bool): if True, this user is expected to be gated from this block
        user (int): user
        course_id (CourseLocator): id of course
    """
    checkout_link = '#' if has_upgrade_link else None
    for content_getter in (_get_content_from_fragment, _get_content_from_lms_index):
        with patch.object(ContentTypeGatingPartition, '_get_checkout_link', return_value=checkout_link):
            content = content_getter(store, block, user.id, course, request_factory)  # pylint: disable=no-value-for-parameter
        if is_gated:
            assert 'content-paywall' in content
            if has_upgrade_link:
                assert 'certA_1' in content
            else:
                assert 'certA_1' not in content
        else:
            assert 'content-paywall' not in content

    fake_request = request_factory.get('')
    requested_fields = ['block_id', 'contains_gated_content', 'display_name', 'student_view_data', 'student_view_url']
    blocks = get_blocks(fake_request, course.location, user=user, requested_fields=requested_fields,
                        student_view_data=['html'])
    course_api_block = blocks['blocks'][str(block.location)]

    assert course_api_block.get('contains_gated_content', False) == is_gated

    if is_gated:
        assert 'authorization_denial_reason' in course_api_block
        assert 'block_id' in course_api_block
        assert 'display_name' in course_api_block
        assert 'student_view_data' not in course_api_block
        assert 'student_view_url' in course_api_block
    else:
        assert 'authorization_denial_reason' not in course_api_block
        if block.category == 'html':
            assert 'student_view_data' in course_api_block


def _assert_block_is_empty(store, block, user_id, course, request_factory):
    """
    Asserts that a block in a specific course is empty for a specific user
    Arguments:
        block: some sort of XBlock, must implement .scope_ids.usage_id
        is_gated (bool): if True, this user is expected to be gated from this block
        user_id (int): id of user
        course_id (CourseLocator): id of course
    """
    content = _get_content_from_lms_index(store, block, user_id, course, request_factory)
    assert content is None


@ddt.ddt
@override_settings(FIELD_OVERRIDE_PROVIDERS=(
    'openedx.features.content_type_gating.field_override.ContentTypeGatingFieldOverride',
))
class TestProblemTypeAccess(SharedModuleStoreTestCase, MasqueradeMixin):  # pylint: disable=missing-class-docstring

    PROBLEM_TYPES = ['problem', 'openassessment', 'drag-and-drop-v2', 'done', 'edx_sga']
    # 'html' is a component that just displays html, in these tests it is used to test that users who do not have access
    # to graded problems still have access to non-problems
    COMPONENT_TYPES = PROBLEM_TYPES + ['html']
    MODE_TYPES = ['credit', 'honor', 'audit', 'verified', 'professional', 'no-id-professional']

    GRADED_SCORE_WEIGHT_TEST_CASES = [
        # graded, has_score, weight, is_gated
        (False, False, 0, False),
        (False, True, 0, False),
        (False, False, 1, False),
        (False, True, 1, False),
        (True, False, 0, False),
        (True, True, 0, False),
        (True, False, 1, False),
        (True, True, 1, True)
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.factory = RequestFactory()

        cls.courses = {}

        # default course is used for most tests, it includes an audit and verified track and all the problem types
        # defined in 'PROBLEM_TYPES' and 'GRADED_SCORE_WEIGHT_TEST_CASES'
        cls.courses['default'] = cls._create_course(
            run='testcourse1',
            display_name='Test Course Title',
            modes=['audit', 'verified'],
            component_types=cls.COMPONENT_TYPES
        )
        # because default course is used for most tests self.course and self.problem_dict are set for ease of reference
        cls.course = cls.courses['default']['course']
        cls.blocks_dict = cls.courses['default']['blocks']

        # Create components with the cartesian product of possible values of
        # graded/has_score/weight for the test_graded_score_weight_values test.
        cls.graded_score_weight_blocks = {}
        for graded, has_score, weight, gated in cls.GRADED_SCORE_WEIGHT_TEST_CASES:  # noqa: B007
            case_name = ' Graded: ' + str(graded) + ' Has Score: ' + str(has_score) + ' Weight: ' + str(weight)  # noqa: F841  # pylint: disable=line-too-long
            block_args = {
                'parent': cls.blocks_dict['vertical'],
                # has_score is determined by XBlock type. It is not a value set on an instance of an XBlock.
                # Therefore, we create a problem component when has_score is True
                # and an html component when has_score is False.
                'category': 'problem' if has_score else 'html',
                'graded': graded,
            }
            if has_score:
                block_args['weight'] = weight
            if graded and has_score and weight:
                block_args['metadata'] = METADATA
            block = BlockFactory.create(**block_args)
            # Intersperse HTML so that the content-gating renders in all blocks
            BlockFactory.create(
                parent=cls.blocks_dict['vertical'],
                category='html',
                graded=False,
            )
            cls.graded_score_weight_blocks[(graded, has_score, weight)] = block
        host = '127.0.0.1'
        metadata_lti_xblock = {
            'lti_id': 'correct_lti_id',
            'launch_url': 'http://{}:{}/{}'.format(host, '8765', 'correct_lti_endpoint'),
        }

        scored_lti_metadata = {}
        scored_lti_metadata.update(metadata_lti_xblock)
        scored_lti_metadata.update(METADATA)

        # add LTI blocks to default course
        cls.blocks_dict['lti_block'] = BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='lti_consumer',
            has_score=True,
            graded=True,
            metadata=scored_lti_metadata,
        )
        # Intersperse HTML so that the content-gating renders in all blocks
        BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='html',
            graded=False,
        )
        cls.blocks_dict['lti_block_not_scored'] = BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='lti_consumer',
            has_score=False,
            metadata=metadata_lti_xblock,
        )

        # Intersperse HTML so that the content-gating renders in all blocks
        BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='html',
            graded=False,
        )

        # add ungraded problem for xblock_handler test
        cls.blocks_dict['graded_problem'] = BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )

        # Intersperse HTML so that the content-gating renders in all blocks
        BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='html',
            graded=False,
        )

        cls.blocks_dict['ungraded_problem'] = BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='problem',
            graded=False,
        )

        # Intersperse HTML so that the content-gating renders in all blocks
        BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='html',
            graded=False,
        )

        cls.blocks_dict['audit_visible_graded_problem'] = BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='problem',
            graded=True,
            group_access={
                CONTENT_GATING_PARTITION_ID: [
                    CONTENT_TYPE_GATE_GROUP_IDS['limited_access'],
                    CONTENT_TYPE_GATE_GROUP_IDS['full_access']
                ]
            },
        )

        # Intersperse HTML so that the content-gating renders in all blocks
        BlockFactory.create(
            parent=cls.blocks_dict['vertical'],
            category='html',
            graded=False,
        )

        # audit_only course only has an audit track available
        cls.courses['audit_only'] = cls._create_course(
            run='audit_only_course_run_1',
            display_name='Audit Only Test Course Title',
            modes=['audit'],
            component_types=['problem', 'html']
        )

        # all_track_types course has all track types defined in MODE_TYPES
        cls.courses['all_track_types'] = cls._create_course(
            run='all_track_types_run_1',
            display_name='All Track/Mode Types Test Course Title',
            modes=cls.MODE_TYPES,
            component_types=['problem', 'html']
        )

        cls.courses['expired_upgrade_deadline'] = cls._create_course(
            run='expired_upgrade_deadline_run_1',
            display_name='Expired Upgrade Deadline Course Title',
            modes=['audit', 'verified'],
            component_types=['problem', 'html'],
            expired_upgrade_deadline=True
        )

    def setUp(self):
        super().setUp()

        # enroll all users into the all track types course
        self.users = {}
        for mode_type in self.MODE_TYPES:
            self.users[mode_type] = UserFactory.create(username=mode_type)
            CourseEnrollmentFactory.create(
                user=self.users[mode_type],
                course_id=self.courses['all_track_types']['course'].id,
                mode=mode_type
            )

        # create audit_user for ease of reference
        self.audit_user = self.users['audit']

        # enroll audit and verified users into default course
        for mode_type in ['audit', 'verified']:
            CourseEnrollmentFactory.create(
                user=self.users[mode_type],
                course_id=self.course.id,
                mode=mode_type
            )

        # enroll audit user into the audit_only course
        CourseEnrollmentFactory.create(
            user=self.audit_user,
            course_id=self.courses['audit_only']['course'].id,
            mode='audit'
        )
        # enroll audit user into the upgrade expired course
        CourseEnrollmentFactory.create(
            user=self.audit_user,
            course_id=self.courses['expired_upgrade_deadline']['course'].id,
            mode='audit'
        )
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

    @classmethod
    def _create_course(cls, run, display_name, modes, component_types, expired_upgrade_deadline=False):
        """
        Helper method to create a course
        Arguments:
            run (str): name of course run
            display_name (str): display name of course
            modes (list of str): list of modes/tracks this course should have
            component_types (list of str): list of problem types this course should have
        Returns:
             (dict): {
                'course': (CourseBlockWithMixins): course definition
                'blocks': (dict) {
                    'block_category_1': XBlock representing that block,
                    'block_category_2': XBlock representing that block,
                    ....
             }
        """
        start_date = timezone.now() - timedelta(weeks=1)
        course = CourseFactory.create(run=run, display_name=display_name, start=start_date)

        for mode in modes:
            if expired_upgrade_deadline and mode == 'verified':
                CourseModeFactory.create(
                    course_id=course.id,
                    mode_slug=mode,
                    expiration_datetime=start_date + timedelta(days=365),
                )
            else:
                CourseModeFactory.create(course_id=course.id, mode_slug=mode)

        with cls.store.bulk_operations(course.id):
            blocks_dict = {}
            chapter = BlockFactory.create(
                parent=course,
                display_name='Overview',
            )
            blocks_dict['chapter'] = BlockFactory.create(
                parent=course,
                category='chapter',
                display_name='Week 1',
            )
            blocks_dict['sequential'] = BlockFactory.create(
                parent=chapter,
                category='sequential',
                display_name='Lesson 1',
            )
            blocks_dict['vertical'] = BlockFactory.create(
                parent=blocks_dict['sequential'],
                category='vertical',
                display_name='Lesson 1 Vertical - Unit 1',
            )

            for component_type in component_types:
                block = BlockFactory.create(
                    parent=blocks_dict['vertical'],
                    category=component_type,
                    graded=True,
                    metadata={} if (component_type == 'html' or len(modes) == 1) else METADATA
                )
                blocks_dict[component_type] = block
                # Intersperse HTML so that the content-gating renders in all blocks
                BlockFactory.create(
                    parent=blocks_dict['vertical'],
                    category='html',
                    graded=False,
                )

            return {
                'course': course,
                'blocks': blocks_dict,
            }

    @ddt.data(
        ('problem', True),
        ('openassessment', True),
        ('drag-and-drop-v2', True),
        ('done', True),
        ('edx_sga', True),
        ('lti_block', True),
        ('ungraded_problem', False),
        ('lti_block_not_scored', False),
        ('audit_visible_graded_problem', False),
    )
    @ddt.unpack
    def test_access_to_problems(self, prob_type, is_gated):
        _assert_block_is_gated(
            self.store,
            block=self.blocks_dict[prob_type],
            user=self.users['audit'],
            course=self.course,
            is_gated=is_gated,
            request_factory=self.factory,
        )
        _assert_block_is_gated(
            self.store,
            block=self.blocks_dict[prob_type],
            user=self.users['verified'],
            course=self.course,
            is_gated=False,
            request_factory=self.factory,
        )

    @ddt.data(
        *GRADED_SCORE_WEIGHT_TEST_CASES
    )
    @ddt.unpack
    def test_graded_score_weight_values(self, graded, has_score, weight, is_gated):
        # Verify that graded, has_score and weight must all be true for a component to be gated
        block = self.graded_score_weight_blocks[(graded, has_score, weight)]
        _assert_block_is_gated(
            self.store,
            block=block,
            user=self.audit_user,
            course=self.course,
            is_gated=is_gated,
            request_factory=self.factory,
        )

    @ddt.data(
        ('audit', 'problem', 'default', True),
        ('verified', 'problem', 'default', False),
        ('audit', 'html', 'default', False),
        ('verified', 'html', 'default', False),
        ('audit', 'problem', 'audit_only', False),
        ('audit', 'html', 'audit_only', False),
        ('credit', 'problem', 'all_track_types', False),
        ('credit', 'html', 'all_track_types', False),
        ('honor', 'problem', 'all_track_types', False),
        ('honor', 'html', 'all_track_types', False),
        ('audit', 'problem', 'all_track_types', True),
        ('audit', 'html', 'all_track_types', False),
        ('verified', 'problem', 'all_track_types', False),
        ('verified', 'html', 'all_track_types', False),
        ('professional', 'problem', 'all_track_types', False),
        ('professional', 'html', 'all_track_types', False),
        ('no-id-professional', 'problem', 'all_track_types', False),
        ('no-id-professional', 'html', 'all_track_types', False),
    )
    @ddt.unpack
    def test_access_based_on_track(self, user_track, component_type, course, is_gated):
        """
         If a user is enrolled as an audit user they should not have access to graded problems, unless there is no paid
         track option.  All paid type tracks should have access to all types of content.
         All users should have access to non-problem component types, the 'html' components test that.
         """
        _assert_block_is_gated(
            self.store,
            block=self.courses[course]['blocks'][component_type],
            user=self.users[user_track],
            course=self.courses[course]['course'],
            is_gated=is_gated,
            request_factory=self.factory,
        )

    def test_access_expired_upgrade_deadline(self):
        """
        If a user is enrolled as an audit user and the upgrade deadline has passed
        the user will continue to see gated content, but the upgrade messaging will be removed.
        """
        _assert_block_is_gated(
            self.store,
            block=self.courses['expired_upgrade_deadline']['blocks']['problem'],
            user=self.users['audit'],
            course=self.courses['expired_upgrade_deadline']['course'],
            is_gated=True,
            request_factory=self.factory,
            has_upgrade_link=False
        )

    @ddt.data(
        ('problem', 'graded_problem', 'audit', 404),
        ('problem', 'graded_problem', 'verified', 200),
        ('problem', 'ungraded_problem', 'audit', 200),
        ('problem', 'ungraded_problem', 'verified', 200),
    )
    @ddt.unpack
    def test_xblock_handlers(self, xblock_type, xblock_name, user, status_code):
        """
        Test the ajax calls to the problem xblock to ensure the LMS is sending back
        the expected response codes on requests when content is gated for audit users
        (404) and when it is available to audit users (200). Content is always available
        to verified users.
        """
        problem_location = self.blocks_dict[xblock_name].scope_ids.usage_id
        url = reverse(
            'xblock_handler',
            kwargs={
                'course_id': str(self.course.id),
                'usage_id': quote_slashes(str(problem_location)),
                'handler': 'xmodule_handler',
                'suffix': 'problem_show',
            }
        )
        self.client.login(username=self.users[user].username, password=TEST_PASSWORD)
        response = self.client.post(url)
        assert response.status_code == status_code

    @ddt.data(
        InstructorFactory,
        StaffFactory,
        BetaTesterFactory,
        OrgStaffFactory,
        OrgInstructorFactory,
        GlobalStaffFactory,
    )
    def test_access_course_team_users(self, role_factory):
        """
        Test that members of the course team do not lose access to graded content
        """
        # There are two types of course team members: instructor and staff
        # they have different privileges, but for the purpose of this test the important thing is that they should both
        # have access to all graded content
        if role_factory == GlobalStaffFactory:
            user = role_factory.create()
        else:
            user = role_factory.create(course_key=self.course.id)

        CourseEnrollmentFactory.create(
            user=user,
            course_id=self.course.id,
            mode='audit',
        )

        # assert that course team members have access to graded content
        _assert_block_is_gated(
            self.store,
            block=self.blocks_dict['problem'],
            user=user,
            course=self.course,
            is_gated=False,
            request_factory=self.factory,
        )

    @ddt.data(
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_GROUP_MODERATOR
    )
    def test_access_user_with_forum_role(self, role_name):
        """
        Test that users with a given forum role do not lose access to graded content
        """
        user = UserFactory.create()
        role = RoleFactory(name=role_name, course_id=self.course.id)
        role.users.add(user)

        CourseEnrollmentFactory.create(
            user=user,
            course_id=self.course.id,
            mode='audit',
        )
        _assert_block_is_gated(
            self.store,
            block=self.blocks_dict['problem'],
            user=user,
            course=self.course,
            is_gated=False,
            request_factory=self.factory,
        )

    @ddt.data(
        (False, True),
        (True, False),
    )
    @ddt.unpack
    def test_content_gating_holdback(self, put_user_in_holdback, is_gated):
        """
        Test that putting a user in the content gating holdback disables content gating.
        """
        user = UserFactory.create()
        enrollment = CourseEnrollment.enroll(user, self.course.id)
        if put_user_in_holdback:
            FBEEnrollmentExclusion.objects.create(enrollment=enrollment)

        graded, has_score, weight = True, True, 1
        block = self.graded_score_weight_blocks[(graded, has_score, weight)]
        _assert_block_is_gated(
            self.store,
            block=block,
            user=user,
            course=self.course,
            is_gated=is_gated,
            request_factory=self.factory,
        )

    @ddt.data(
        ({'user_partition_id': CONTENT_GATING_PARTITION_ID,
          'group_id': CONTENT_TYPE_GATE_GROUP_IDS['limited_access']}, True),
        ({'user_partition_id': CONTENT_GATING_PARTITION_ID,
          'group_id': CONTENT_TYPE_GATE_GROUP_IDS['full_access']}, False),
        ({'user_partition_id': ENROLLMENT_TRACK_PARTITION_ID,
          'group_id': settings.COURSE_ENROLLMENT_MODES['audit']['id']}, True),
        ({'user_partition_id': ENROLLMENT_TRACK_PARTITION_ID,
          'group_id': settings.COURSE_ENROLLMENT_MODES['verified']['id']}, False),
        ({'role': 'staff'}, False),
        ({'role': 'student'}, True),
        ({'username': 'audit'}, True),
        ({'username': 'verified'}, False),
    )
    @ddt.unpack
    def test_masquerade(self, masquerade_config, is_gated):
        instructor = UserFactory.create()
        CourseEnrollmentFactory.create(
            user=instructor,
            course_id=self.course.id,
            mode='audit'
        )
        CourseInstructorRole(self.course.id).add_users(instructor)
        self.client.login(username=instructor.username, password=TEST_PASSWORD)

        self.update_masquerade(**masquerade_config)

        block = self.blocks_dict['problem']
        block_view_url = reverse('render_xblock', kwargs={'usage_key_string': str(block.scope_ids.usage_id)})
        response = self.client.get(block_view_url)
        if is_gated:
            assert response.status_code == 404
        else:
            assert response.status_code == 200

    @ddt.data(
        InstructorFactory,
        StaffFactory,
        BetaTesterFactory,
        OrgStaffFactory,
        OrgInstructorFactory,
        GlobalStaffFactory,
    )
    def test_access_masquerade_as_course_team_users(self, role_factory):
        """
        Test that when masquerading as members of the course team you do not lose access to graded content
        """
        # There are two types of course team members: instructor and staff
        # they have different privileges, but for the purpose of this test the important thing is that they should both
        # have access to all graded content
        staff_user = StaffFactory.create(password=TEST_PASSWORD, course_key=self.course.id)
        CourseEnrollmentFactory.create(
            user=staff_user,
            course_id=self.course.id,
            mode='audit'
        )
        self.client.login(username=staff_user.username, password=TEST_PASSWORD)

        if role_factory == GlobalStaffFactory:
            user = role_factory.create()
        else:
            user = role_factory.create(course_key=self.course.id)
        CourseEnrollment.enroll(user, self.course.id)
        self.update_masquerade(username=user.username)

        block = self.blocks_dict['problem']
        block_view_url = reverse('render_xblock', kwargs={'usage_key_string': str(block.scope_ids.usage_id)})
        response = self.client.get(block_view_url)
        assert response.status_code == 200

    @ddt.data(
        FORUM_ROLE_COMMUNITY_TA,
        FORUM_ROLE_ADMINISTRATOR,
        FORUM_ROLE_MODERATOR,
        FORUM_ROLE_GROUP_MODERATOR
    )
    def test_access_masquerade_as_user_with_forum_role(self, role_name):
        """
        Test that when masquerading as a user with a given forum role you do not lose access to graded content
        """
        staff_user = StaffFactory.create(password=TEST_PASSWORD, course_key=self.course.id)
        CourseEnrollmentFactory.create(
            user=staff_user,
            course_id=self.course.id,
            mode='audit'
        )
        self.client.login(username=staff_user.username, password=TEST_PASSWORD)

        user = UserFactory.create()
        role = RoleFactory(name=role_name, course_id=self.course.id)
        role.users.add(user)

        CourseEnrollmentFactory.create(
            user=user,
            course_id=self.course.id,
            mode='audit'
        )

        self.update_masquerade(username=user.username)

        _assert_block_is_gated(
            self.store,
            block=self.blocks_dict['problem'],
            user=user,
            course=self.course,
            is_gated=False,
            request_factory=self.factory,
        )

    @patch(
        'openedx.features.content_type_gating.partitions.format_strikeout_price',
        Mock(return_value=(HTML("<span>DISCOUNT_PRICE</span>"), True))
    )
    def test_discount_display(self):

        with patch.object(ContentTypeGatingPartition, '_get_checkout_link', return_value='#'):
            block_content = _get_content_from_lms_index(self.store, self.blocks_dict['problem'], self.audit_user.id,
                                                        self.course, self.factory)

        assert '<span>DISCOUNT_PRICE</span>' in block_content


@override_settings(FIELD_OVERRIDE_PROVIDERS=(
    'openedx.features.content_type_gating.field_override.ContentTypeGatingFieldOverride',
))
class TestConditionalContentAccess(TestConditionalContent):
    """
    Conditional Content allows course authors to run a/b tests on course content.  We want to make sure that
    even if one of these a/b tests are being run, the student still has the correct access to the content.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.factory = RequestFactory()
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

    def setUp(self):
        super().setUp()

        # Add a verified mode to the course
        CourseModeFactory.create(course_id=self.course.id, mode_slug='audit')
        CourseModeFactory.create(course_id=self.course.id, mode_slug='verified')

        # Create variables that more accurately describe the student's function
        self.student_audit_a = self.student_a
        self.student_audit_b = self.student_b

        # Create verified students
        self.student_verified_a = UserFactory.create(
            username='student_verified_a', email='student_verified_a@example.com'
        )
        CourseEnrollmentFactory.create(user=self.student_verified_a, course_id=self.course.id, mode='verified')
        self.student_verified_b = UserFactory.create(
            username='student_verified_b', email='student_verified_b@example.com'
        )
        CourseEnrollmentFactory.create(user=self.student_verified_b, course_id=self.course.id, mode='verified')

        # Put students into content gating groups
        UserCourseTagFactory(
            user=self.student_verified_a,
            course_id=self.course.id,
            key=f'xblock.partition_service.partition_{self.partition.id}',
            value='0',
        )
        UserCourseTagFactory(
            user=self.student_verified_b,
            course_id=self.course.id,
            key=f'xblock.partition_service.partition_{self.partition.id}',
            value='1',
        )
        # Create blocks to go into the verticals
        self.block_a = BlockFactory.create(
            category='problem',
            parent=self.vertical_a,
            metadata=METADATA,
        )
        self.block_b = BlockFactory.create(
            category='problem',
            parent=self.vertical_b,
            metadata=METADATA,
        )

    def test_access_based_on_conditional_content(self):
        """
        If a user is enrolled as an audit user they should not have access to graded problems,
        including conditional content.
        All paid type tracks should have access graded problems including conditional content.
        """

        # Make sure that all audit enrollments are gated regardless of if they see vertical a or vertical b
        _assert_block_is_gated(
            self.store,
            block=self.block_a,
            user=self.student_audit_a,
            course=self.course,
            is_gated=True,
            request_factory=self.factory,
        )
        _assert_block_is_gated(
            self.store,
            block=self.block_b,
            user=self.student_audit_b,
            course=self.course,
            is_gated=True,
            request_factory=self.factory,
        )

        # Make sure that all verified enrollments are not gated regardless of if they see vertical a or vertical b
        _assert_block_is_gated(
            self.store,
            block=self.block_a,
            user=self.student_verified_a,
            course=self.course,
            is_gated=False,
            request_factory=self.factory,
        )
        _assert_block_is_gated(
            self.store,
            block=self.block_b,
            user=self.student_verified_b,
            course=self.course,
            is_gated=False,
            request_factory=self.factory,
        )


@override_settings(FIELD_OVERRIDE_PROVIDERS=(
    'openedx.features.content_type_gating.field_override.ContentTypeGatingFieldOverride',
))
class TestMessageDeduplication(ModuleStoreTestCase):
    """
    Tests to verify that access denied messages isn't shown if multiple items in a row are denied.
    Expected results:
        - 0 items denied => No access denied messages
        - 1+ items in a row denied => 1 access denied message
        - If page has accessible content between access denied blocks, show both blocks.

    NOTE: This uses `_assert_block_is_gated` to verify that the message is being shown (as that's
    how it's currently tested). If that method changes to use something other than the template
    message, this method's checks will need to be updated.
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.request_factory = RequestFactory()
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

    def _create_course(self):  # pylint: disable=missing-function-docstring
        course = CourseFactory.create(run='test', display_name='test')
        CourseModeFactory.create(course_id=course.id, mode_slug='audit')
        CourseModeFactory.create(course_id=course.id, mode_slug='verified')
        blocks_dict = {}
        with self.store.bulk_operations(course.id):
            blocks_dict['chapter'] = BlockFactory.create(
                parent=course,
                category='chapter',
                display_name='Week 1'
            )
            blocks_dict['sequential'] = BlockFactory.create(
                parent=blocks_dict['chapter'],
                category='sequential',
                display_name='Lesson 1'
            )
            blocks_dict['vertical'] = BlockFactory.create(
                parent=blocks_dict['sequential'],
                category='vertical',
                display_name='Lesson 1 Vertical - Unit 1'
            )
        return {
            'course': course,
            'blocks': blocks_dict,
        }

    def test_single_denied(self):
        ''' Single graded problem should show error '''
        course = self._create_course()
        blocks_dict = course['blocks']
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course['course'].id,
            mode='audit'
        )
        blocks_dict['graded_1'] = BlockFactory.create(
            parent_location=blocks_dict['vertical'].location,
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        _assert_block_is_gated(
            self.store,
            block=blocks_dict['graded_1'],
            user=self.user,
            course=course['course'],
            is_gated=True,
            request_factory=self.request_factory,
        )

    def test_double_denied(self):
        ''' First graded problem should show message, second shouldn't '''
        course = self._create_course()
        blocks_dict = course['blocks']
        blocks_dict['graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        blocks_dict['graded_2'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course['course'].id,
            mode='audit'
        )
        _assert_block_is_gated(
            self.store,
            block=blocks_dict['graded_1'],
            user=self.user,
            course=course['course'],
            is_gated=True,
            request_factory=self.request_factory,
        )
        _assert_block_is_empty(
            self.store,
            block=blocks_dict['graded_2'],
            user_id=self.user.id,
            course=course['course'],
            request_factory=self.request_factory,
        )

    def test_many_denied(self):
        ''' First graded problem should show message, all that follow shouldn't '''
        course = self._create_course()
        blocks_dict = course['blocks']
        blocks_dict['graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        blocks_dict['graded_2'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        blocks_dict['graded_3'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        blocks_dict['graded_4'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course['course'].id,
            mode='audit'
        )
        _assert_block_is_gated(
            self.store,
            block=blocks_dict['graded_1'],
            user=self.user,
            course=course['course'],
            is_gated=True,
            request_factory=self.request_factory,
        )
        _assert_block_is_empty(
            self.store,
            block=blocks_dict['graded_2'],
            user_id=self.user.id,
            course=course['course'],
            request_factory=self.request_factory,
        )
        _assert_block_is_empty(
            self.store,
            block=blocks_dict['graded_3'],
            user_id=self.user.id,
            course=course['course'],
            request_factory=self.request_factory,
        )
        _assert_block_is_empty(
            self.store,
            block=blocks_dict['graded_4'],
            user_id=self.user.id,
            course=course['course'],
            request_factory=self.request_factory,
        )

    def test_alternate_denied(self):
        ''' Multiple graded content with ungraded between it should show message on either end '''
        course = self._create_course()
        blocks_dict = course['blocks']
        blocks_dict['graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        blocks_dict['ungraded_2'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=False,
        )
        blocks_dict['graded_3'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course['course'].id,
            mode='audit'
        )
        _assert_block_is_gated(
            self.store,
            block=blocks_dict['graded_1'],
            user=self.user,
            course=course['course'],
            is_gated=True,
            request_factory=self.request_factory,
        )
        _assert_block_is_gated(
            self.store,
            block=blocks_dict['ungraded_2'],
            user=self.user,
            course=course['course'],
            is_gated=False,
            request_factory=self.request_factory,
        )
        _assert_block_is_gated(
            self.store,
            block=blocks_dict['graded_3'],
            user=self.user,
            course=course['course'],
            is_gated=True,
            request_factory=self.request_factory,
        )


@override_settings(FIELD_OVERRIDE_PROVIDERS=(
    'openedx.features.content_type_gating.field_override.ContentTypeGatingFieldOverride',
))
class TestContentTypeGatingService(ModuleStoreTestCase):
    """
    The ContentTypeGatingService was originally created as a helper class for timed exams
    to check whether a sequence contains content type gated blocks
    The content_type_gate_for_block can be used to return the content type gate for a given block
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.request_factory = RequestFactory()
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

    def _create_course(self):  # pylint: disable=missing-function-docstring
        course = CourseFactory.create(run='test', display_name='test')
        CourseModeFactory.create(course_id=course.id, mode_slug='audit')
        CourseModeFactory.create(course_id=course.id, mode_slug='verified')
        blocks_dict = {}
        with self.store.bulk_operations(course.id):
            blocks_dict['chapter'] = BlockFactory.create(
                parent=course,
                category='chapter',
                display_name='Week 1'
            )
            blocks_dict['sequential'] = BlockFactory.create(
                parent=blocks_dict['chapter'],
                category='sequential',
                display_name='Lesson 1'
            )
            blocks_dict['vertical'] = BlockFactory.create(
                parent=blocks_dict['sequential'],
                category='vertical',
                display_name='Lesson 1 Vertical - Unit 1'
            )
        # get updated course
        course = self.store.get_item(course.location)
        return {
            'course': course,
            'blocks': blocks_dict,
        }

    def test_content_type_gate_for_block(self):
        ''' Verify that the method returns a content type gate when appropriate '''
        course = self._create_course()
        blocks_dict = course['blocks']
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course['course'].id,
            mode='audit'
        )
        blocks_dict['graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        blocks_dict['not_graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=False,
            metadata=METADATA,
        )

        # The method returns a content type gate for blocks that should be gated
        assert 'content-paywall' in ContentTypeGatingService()._content_type_gate_for_block(  # pylint: disable=protected-access
            self.user, blocks_dict['graded_1'], course['course'].id
        ).content

        # The method returns None for blocks that should not be gated
        assert ContentTypeGatingService()._content_type_gate_for_block(  # pylint: disable=protected-access
            self.user, blocks_dict['not_graded_1'], course['course'].id
        ) is None

    @patch.object(ContentTypeGatingService, "_get_user")
    def test_check_children_for_content_type_gating_paywall(self, mocked_get_user):
        ''' Verify that the method returns a content type gate when appropriate '''
        mocked_get_user.return_value = UserFactory.create()  # saved user object with id
        course = self._create_course()
        blocks_dict = course['blocks']
        CourseEnrollmentFactory.create(
            user=self.user,
            course_id=course['course'].id,
            mode='audit'
        )
        blocks_dict['not_graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=False,
            metadata=METADATA,
        )
        # get updated course
        course['course'] = self.store.get_item(course['course'].location)
        blocks_dict['vertical'] = self.store.get_item(blocks_dict['vertical'].location)

        # The method returns a content type gate for blocks that should be gated
        assert ContentTypeGatingService().check_children_for_content_type_gating_paywall(
            blocks_dict['vertical'], course['course'].id
        ) is None

        blocks_dict['graded_1'] = BlockFactory.create(
            parent=blocks_dict['vertical'],
            category='problem',
            graded=True,
            metadata=METADATA,
        )
        # get updated course
        course['course'] = self.store.get_item(course['course'].location)
        blocks_dict['vertical'] = self.store.get_item(blocks_dict['vertical'].location)

        # The method returns None for blocks that should not be gated
        assert 'content-paywall' in ContentTypeGatingService().check_children_for_content_type_gating_paywall(
            blocks_dict['vertical'], course['course'].id
        )
