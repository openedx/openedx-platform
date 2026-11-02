""" Mixins for setting up particular course structures (such as split tests or cohorted content) """


from datetime import datetime

from zoneinfo import ZoneInfo

from openedx.core.djangoapps.course_groups.models import CourseUserGroupPartitionGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.djangoapps.user_api.tests.factories import UserCourseTagFactory
from openedx.core.lib.teams_config import TeamsConfig
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import Group, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order


class ContentGroupTestCase(ModuleStoreTestCase):
    """
    Sets up discussion blocks visible to content groups 'Alpha' and
    'Beta', as well as a block visible to all students.  Creates a
    staff user, users with access to Alpha/Beta (by way of cohorts),
    and a non-cohorted user with no special access.
    """
    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(
            org='org', number='number', run='run',
            # This test needs to use a course that has already started --
            # discussion topics only show up if the course has already started,
            # and the default start date for courses is Jan 1, 2030.
            start=datetime(2012, 2, 3, tzinfo=ZoneInfo("UTC")),
            user_partitions=[
                UserPartition(
                    0,
                    'Content Group Configuration',
                    '',
                    [Group(1, 'Alpha'), Group(2, 'Beta')],
                    scheme_id='cohort'
                )
            ],
            grading_policy={
                "GRADER": [{
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "HW",
                    "weight": 1.0
                }]
            },
            cohort_config={'cohorted': True},
            discussion_topics={},
            teams_configuration=TeamsConfig({
                'topics': [{
                    'id': 'topic_id',
                    'name': 'topic_name',
                    'description': 'topic_desc',
                }]
            })
        )

        seed_permissions_roles(self.course.id)

        self.staff_user = UserFactory.create(is_staff=True)
        self.alpha_user = UserFactory.create()
        self.beta_user = UserFactory.create()
        self.non_cohorted_user = UserFactory.create()
        self.community_ta = UserFactory.create(username="community_ta")
        self.community_ta.roles.add(Role.objects.get(name="Community TA", course_id=self.course.id))
        for user in [
            self.staff_user,
            self.alpha_user,
            self.beta_user,
            self.non_cohorted_user,
            self.community_ta
        ]:
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        alpha_cohort = CohortFactory(
            course_id=self.course.id,
            name='Cohort Alpha',
            users=[self.alpha_user, self.community_ta, self.staff_user]
        )
        beta_cohort = CohortFactory(
            course_id=self.course.id,
            name='Cohort Beta',
            users=[self.beta_user]
        )
        CourseUserGroupPartitionGroup.objects.create(
            course_user_group=alpha_cohort,
            partition_id=self.course.user_partitions[0].id,
            group_id=self.course.user_partitions[0].groups[0].id
        )
        CourseUserGroupPartitionGroup.objects.create(
            course_user_group=beta_cohort,
            partition_id=self.course.user_partitions[0].id,
            group_id=self.course.user_partitions[0].groups[1].id
        )
        self.alpha_block = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='alpha_group_discussion',
            discussion_target='Visible to Alpha',
            group_access={self.course.user_partitions[0].id: [self.course.user_partitions[0].groups[0].id]}
        )
        self.beta_block = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='beta_group_discussion',
            discussion_target='Visible to Beta',
            group_access={self.course.user_partitions[0].id: [self.course.user_partitions[0].groups[1].id]}
        )
        self.global_block = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='global_group_discussion',
            discussion_target='Visible to Everyone'
        )
        self.course = self.store.get_item(self.course.location)


class TestConditionalContent(ModuleStoreTestCase):
    """
    Construct a course with graded problems that exist within a split test.
    """
    TEST_SECTION_NAME = 'Subsection'

    def setUp(self):
        """
        Set up a course with graded problems within a split test.

        Course hierarchy is as follows (modeled after how split tests
        are created in studio):
        -> course
            -> chapter
                -> sequential (graded)
                    -> vertical
                        -> split_test
                            -> vertical (Group A)
                                -> problem
                            -> vertical (Group B)
                                -> problem
        """
        super().setUp()

        # Create user partitions
        self.user_partition_group_a = 0
        self.user_partition_group_b = 1
        self.partition = UserPartition(
            0,
            'first_partition',
            'First Partition',
            [
                Group(self.user_partition_group_a, 'Group A'),
                Group(self.user_partition_group_b, 'Group B')
            ]
        )

        # Create course with group configurations and grading policy
        self.course = CourseFactory.create(
            user_partitions=[self.partition],
            grading_policy={
                "GRADER": [{
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "HW",
                    "weight": 1.0
                }]
            }
        )
        chapter = BlockFactory.create(parent_location=self.course.location,
                                      display_name='Chapter')

        # add a sequence to the course to which the problems can be added
        self.problem_section = BlockFactory.create(parent_location=chapter.location,
                                                   category='sequential',
                                                   metadata={'graded': True, 'format': 'Homework'},
                                                   display_name=self.TEST_SECTION_NAME)

        # Create users and partition them
        self.student_a = UserFactory.create(username='student_a', email='student_a@example.com')
        CourseEnrollmentFactory.create(user=self.student_a, course_id=self.course.id)
        self.student_b = UserFactory.create(username='student_b', email='student_b@example.com')
        CourseEnrollmentFactory.create(user=self.student_b, course_id=self.course.id)

        UserCourseTagFactory(
            user=self.student_a,
            course_id=self.course.id,
            key=f'xblock.partition_service.partition_{self.partition.id}',
            value=str(self.user_partition_group_a)
        )
        UserCourseTagFactory(
            user=self.student_b,
            course_id=self.course.id,
            key=f'xblock.partition_service.partition_{self.partition.id}',
            value=str(self.user_partition_group_b)
        )

        # Create a vertical to contain our split test
        problem_vertical = BlockFactory.create(
            parent_location=self.problem_section.location,
            category='vertical',
            display_name='Problem Unit'
        )

        # Create the split test and child vertical containers
        vertical_a_url = self.course.id.make_usage_key('vertical', 'split_test_vertical_a')
        vertical_b_url = self.course.id.make_usage_key('vertical', 'split_test_vertical_b')
        self.split_test = BlockFactory.create(
            parent_location=problem_vertical.location,
            category='split_test',
            display_name='Split Test',
            user_partition_id=self.partition.id,
            group_id_to_child={str(index): url for index, url in enumerate([vertical_a_url, vertical_b_url])}
        )
        self.vertical_a = BlockFactory.create(
            parent_location=self.split_test.location,
            category='vertical',
            display_name='Group A problem container',
            location=vertical_a_url
        )
        self.vertical_b = BlockFactory.create(
            parent_location=self.split_test.location,
            category='vertical',
            display_name='Group B problem container',
            location=vertical_b_url
        )
