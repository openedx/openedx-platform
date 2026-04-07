"""
Test cases for forum v2 based tasks.py
"""
from unittest import mock
from unittest.mock import Mock

import ddt
import httpretty
from django.conf import settings
from openedx_events.learning.signals import COURSE_NOTIFICATION_REQUESTED, USER_NOTIFICATION_REQUESTED

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import StaffFactory, UserFactory
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from lms.djangoapps.discussion.rest_api.tasks import (
    send_response_endorsed_notifications,
    send_response_notifications,
    send_thread_created_notification,
)
from lms.djangoapps.discussion.rest_api.tests.utils import ThreadMock, make_minimal_cs_thread
from openedx.core.djangoapps.course_groups.models import CohortMembership, CourseCohortsSettings
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.discussions.models import DiscussionTopicLink
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    CourseDiscussionSettings,
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..discussions_notifications import DiscussionNotificationSender
from .test_views_v2 import DiscussionAPIViewTestMixin


def _get_mfe_url(course_id, post_id):
    """
    get discussions mfe url to specific post.
    """
    return f"{settings.DISCUSSIONS_MICROFRONTEND_URL}/{str(course_id)}/posts/{post_id}"


@ddt.ddt
class TestSendResponseNotifications(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """
    Test for the send_response_notifications function
    """

    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.disable)
        self.addCleanup(httpretty.reset)

        self.course = CourseFactory.create()

        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread",
            return_value=self.course.id
        )
        self.mock_get_course_id_by_thread = patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment",
            return_value=self.course.id
        )
        self.mock_get_course_id_by_comment = patcher.start()
        self.addCleanup(patcher.stop)

        self.user_1 = UserFactory.create()
        CourseEnrollment.enroll(self.user_1, self.course.id)
        self.user_2 = UserFactory.create()
        CourseEnrollment.enroll(self.user_2, self.course.id)
        self.user_3 = UserFactory.create()
        CourseEnrollment.enroll(self.user_3, self.course.id)
        self.thread = ThreadMock(thread_id=1, creator=self.user_1, title='test thread')
        self.thread_2 = ThreadMock(thread_id=2, creator=self.user_2, title='test thread 2')
        self.thread_3 = ThreadMock(thread_id=2, creator=self.user_1, title='test thread 3')
        for thread in [self.thread_3, self.thread_2, self.thread]:
            self.register_get_thread_response({
                'id': thread.id,
                'course_id': str(self.course.id),
                'topic_id': 'abc',
                "user_id": thread.user_id,
                "username": thread.username,
                "thread_type": 'discussion',
                "title": thread.title,
                "commentable_id": thread.commentable_id,

            })

        self._register_subscriptions_endpoint()

        self.comment = ThreadMock(thread_id=4, creator=self.user_2, title='test comment', body='comment body')
        self.register_get_comment_response(
            {
                'id': self.comment.id,
                'thread_id': self.thread.id,
                'parent_id': None,
                'user_id': self.comment.user_id,
                'body': self.comment.body,
            }
        )

    def test_basic(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    def test_not_authenticated(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    @ddt.data(True, False)
    def test_send_notification_to_thread_creator(self, is_subscribed):
        """
        Test that the notification is sent to the thread creator
        """
        handler = mock.Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)
        with mock.patch(
            "lms.djangoapps.discussion.rest_api.discussions_notifications.Subscription.is_user_subscribed_to_thread",
            return_value=is_subscribed
        ):
            # Post the form or do what it takes to send the signal
            send_response_notifications(
                self.thread.id,
                str(self.course.id),
                self.user_2.id,
                self.comment.id,
                parent_id=None
            )
        if is_subscribed:
            self.assertEqual(handler.call_count, 2)
            args = handler.call_args_list[0][1]['notification_data']
            self.assertEqual([int(user_id) for user_id in args.user_ids], [self.user_1.id])
            self.assertEqual(args.notification_type, 'new_response')
            expected_context = {
                'replier_name': self.user_2.username,
                'post_title': 'test thread',
                'email_content': self.comment.body,
                'course_name': self.course.display_name,
                'sender_id': self.user_2.id,
                'response_id': 4,
                'topic_id': None,
                'thread_id': 1,
                'comment_id': None,
                'group_by_id': '1'
            }
            self.assertDictEqual(args.context, expected_context)
            self.assertEqual(
                args.content_url,
                _get_mfe_url(self.course.id, self.thread.id)
            )
            self.assertEqual(args.app_name, 'discussion')
        else:
            self.assertEqual(handler.call_count, 1)

    @ddt.data(True, False)
    def test_no_signal_on_creators_own_thread(self, is_subscribed):
        """
        Makes sure that 1 signal is emitted if user creates response on
        their own thread.
        """
        handler = mock.Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)
        with mock.patch(
            "lms.djangoapps.discussion.rest_api.discussions_notifications.Subscription.is_user_subscribed_to_thread",
            return_value=is_subscribed
        ):
            send_response_notifications(
                self.thread.id,
                str(self.course.id),
                self.user_1.id,
                self.comment.id, parent_id=None
            )
        self.assertEqual(handler.call_count, 1)

    @ddt.data(
        (None, 'response_on_followed_post'), (1, 'comment_on_followed_post')
    )
    @ddt.unpack
    def test_send_notification_to_followers(self, parent_id, notification_type):
        """
        Test that the notification is sent to the followers of the thread
        """
        self.register_get_comment_response({
            'id': self.thread.id,
            'thread_id': self.thread.id,
            'user_id': self.thread.user_id,
            "body": "comment body"
        })
        handler = Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)

        # Post the form or do what it takes to send the signal
        notification_sender = DiscussionNotificationSender(
            self.thread,
            self.course,
            self.user_2,
            parent_id=parent_id,
            comment_id=self.comment.id
        )
        notification_sender.send_response_on_followed_post_notification()
        self.assertEqual(handler.call_count, 1)
        args = handler.call_args[1]['notification_data']
        # only sent to user_3 because user_2 is the one who created the response
        self.assertEqual([self.user_3.id], args.user_ids)
        self.assertEqual(args.notification_type, notification_type)
        expected_context = {
            'replier_name': self.user_2.username,
            'post_title': 'test thread',
            'email_content': self.comment.body,
            'course_name': self.course.display_name,
            'sender_id': self.user_2.id,
            'response_id': 4 if notification_type == 'response_on_followed_post' else parent_id,
            'topic_id': None,
            'thread_id': 1,
            'comment_id': 4 if not notification_type == 'response_on_followed_post' else None,
        }
        if notification_type == 'response_on_followed_post':
            expected_context['group_by_id'] = '1'
        if parent_id:
            expected_context['author_name'] = 'dummy\'s'
            expected_context['author_pronoun'] = 'dummy\'s'
        self.assertDictEqual(args.context, expected_context)
        self.assertEqual(
            args.content_url,
            _get_mfe_url(self.course.id, self.thread.id)
        )
        self.assertEqual(args.app_name, 'discussion')

    @ddt.data(True, False)
    def test_comment_creators_own_response(self, is_subscribed):
        """
        Check incase post author and response auther is same only send
        new comment signal , with your as author_name.
        """
        handler = mock.Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)

        self.register_get_comment_response({
            'id': self.thread_3.id,
            'thread_id': self.thread.id,
            'user_id': self.thread_3.user_id,
            'body': 'comment body',
        })
        with mock.patch(
            "lms.djangoapps.discussion.rest_api.discussions_notifications.Subscription.is_user_subscribed_to_thread",
            return_value=is_subscribed
        ):
            send_response_notifications(
                self.thread.id,
                str(self.course.id),
                self.user_3.id,
                parent_id=self.thread_2.id,
                comment_id=self.comment.id
            )
        if is_subscribed:
            # check if 1 call is made to the handler i.e. for the thread creator
            self.assertEqual(handler.call_count, 2)

            # check if the notification is sent to the thread creator
            args_comment = handler.call_args_list[0][1]['notification_data']
            self.assertEqual(args_comment.user_ids, [self.user_1.id])
            self.assertEqual(args_comment.notification_type, 'new_comment')
            expected_context = {
                'replier_name': self.user_3.username,
                'post_title': self.thread.title,
                'author_name': 'dummy\'s',
                'author_pronoun': 'your',
                'course_name': self.course.display_name,
                'sender_id': self.user_3.id,
                'email_content': self.comment.body,
                'response_id': 2,
                'topic_id': None,
                'thread_id': 1,
                'comment_id': 4,
            }
            self.assertDictEqual(args_comment.context, expected_context)
            self.assertEqual(
                args_comment.content_url,
                _get_mfe_url(self.course.id, self.thread.id)
            )
            self.assertEqual(args_comment.app_name, 'discussion')
        else:
            self.assertEqual(handler.call_count, 1)

    @ddt.data(True, False)
    def test_send_notification_to_parent_threads(self, is_subscribed):
        """
        Test that the notification signal is sent to the parent response creator and
        parent thread creator, it checks signal is sent with correct arguments for both
        types of notifications.
        """
        handler = mock.Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)

        self.register_get_comment_response({
            'id': self.thread_2.id,
            'thread_id': self.thread.id,
            'user_id': self.thread_2.user_id,
            'body': 'comment body'
        })
        with mock.patch(
            "lms.djangoapps.discussion.rest_api.discussions_notifications.Subscription.is_user_subscribed_to_thread",
            return_value=is_subscribed
        ):
            send_response_notifications(
                self.thread.id,
                str(self.course.id),
                self.user_3.id,
                self.comment.id,
                parent_id=self.thread_2.id
            )

        if is_subscribed:
            # check if 2 calls are made to the handler i.e. one for the response creator and one for the thread creator
            self.assertEqual(handler.call_count, 2)
            # check if the notification is sent to the thread creator
            args_comment = handler.call_args_list[0][1]['notification_data']
            args_comment_on_response = handler.call_args_list[1][1]['notification_data']
            self.assertEqual([int(user_id) for user_id in args_comment.user_ids], [self.user_1.id])
            self.assertEqual(args_comment.notification_type, 'new_comment')
            expected_context = {
                'replier_name': self.user_3.username,
                'post_title': self.thread.title,
                'email_content': self.comment.body,
                'author_name': 'dummy\'s',
                'author_pronoun': 'dummy\'s',
                'course_name': self.course.display_name,
                'sender_id': self.user_3.id,
                'response_id': 2,
                'topic_id': None,
                'thread_id': 1,
                'comment_id': 4,
            }
            self.assertDictEqual(args_comment.context, expected_context)
            self.assertEqual(
                args_comment.content_url,
                _get_mfe_url(self.course.id, self.thread.id)
            )
            self.assertEqual(args_comment.app_name, 'discussion')
        else:
            # check if 1 call is made to the handler i.e. for the response creator
            # because thread creator is not subscribed
            args_comment_on_response = handler.call_args_list[0][1]['notification_data']
            self.assertEqual(handler.call_count, 1)
        # check if the notification is sent to the parent response creator
        self.assertEqual([int(user_id) for user_id in args_comment_on_response.user_ids], [self.user_2.id])
        self.assertEqual(args_comment_on_response.notification_type, 'new_comment_on_response')
        expected_context = {
            'replier_name': self.user_3.username,
            'post_title': self.thread.title,
            'email_content': self.comment.body,
            'course_name': self.course.display_name,
            'sender_id': self.user_3.id,
            'response_id': 2,
            'topic_id': None,
            'thread_id': 1,
            'comment_id': 4,
        }
        self.assertDictEqual(args_comment_on_response.context, expected_context)
        self.assertEqual(
            args_comment_on_response.content_url,
            _get_mfe_url(self.course.id, self.thread.id)
        )
        self.assertEqual(args_comment_on_response.app_name, 'discussion')

    def _register_subscriptions_endpoint(self):
        """
        Registers the endpoint for the subscriptions API
        """
        mock_response = {
            'collection': [
                {
                    '_id': 1,
                    'subscriber_id': str(self.user_2.id),
                    "source_id": self.thread.id,
                    "source_type": "thread",
                },
                {
                    '_id': 2,
                    'subscriber_id': str(self.user_3.id),
                    "source_id": self.thread.id,
                    "source_type": "thread",
                },
            ],
            'page': 1,
            'num_pages': 1,
            'subscriptions_count': 2,
            'corrected_text': None

        }
        self.register_get_subscriptions(self.thread.id, mock_response)


@ddt.ddt
class TestSendCommentNotification(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """
    Test case to send new_comment notification
    """

    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()

        self.course = CourseFactory.create()
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread",
            return_value=self.course.id
        )
        self.mock_get_course_id_by_thread = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment",
            return_value=self.course.id
        )
        self.mock_get_course_id_by_comment = patcher.start()
        self.addCleanup(patcher.stop)

        self.user_1 = UserFactory.create()
        CourseEnrollment.enroll(self.user_1, self.course.id)
        self.user_2 = UserFactory.create()
        CourseEnrollment.enroll(self.user_2, self.course.id)

    def test_basic(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    def test_not_authenticated(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    @ddt.data(True, False)
    def test_new_comment_notification(self, is_subscribed):
        """
        Tests new comment notification generation
        """
        handler = mock.Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)

        thread = ThreadMock(thread_id=1, creator=self.user_1, title='test thread')
        response = ThreadMock(thread_id=2, creator=self.user_2, title='test response')
        comment = ThreadMock(thread_id=3, creator=self.user_2, title='test comment', body='comment body')
        self.register_get_thread_response({
            'id': thread.id,
            'course_id': str(self.course.id),
            'topic_id': 'abc',
            "user_id": thread.user_id,
            "username": thread.username,
            "thread_type": 'discussion',
            "title": thread.title,
            "commentable_id": thread.commentable_id,

        })
        self.register_get_comment_response({
            'id': response.id,
            'thread_id': thread.id,
            'user_id': response.user_id
        })
        self.register_get_comment_response({
            'id': comment.id,
            'parent_id': response.id,
            'user_id': comment.user_id,
            'body': comment.body
        })
        self.register_get_subscriptions(1, {})
        with mock.patch(
            "lms.djangoapps.discussion.rest_api.discussions_notifications.Subscription.is_user_subscribed_to_thread",
            return_value=is_subscribed
        ):
            send_response_notifications(thread.id, str(self.course.id), self.user_2.id, parent_id=response.id,
                                        comment_id=comment.id)
        if is_subscribed:
            handler.assert_called_once()
            context = handler.call_args[1]['notification_data'].context
            self.assertEqual(context['author_name'], 'dummy\'s')
            self.assertEqual(context['author_pronoun'], 'their')
            self.assertEqual(context['email_content'], comment.body)
        else:
            handler.assert_not_called()


@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class TestNewThreadCreatedNotification(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """
    Test cases related to new_discussion_post and new_question_post notification types
    """

    def setUp(self):
        """
        Setup test case
        """
        super().setUp()
        # Creating a course
        self.course = CourseFactory.create()
        self.set_mock_return_value('get_course_id_by_thread', str(self.course.id))
        self.set_mock_return_value('get_course_id_by_comment', str(self.course.id))

        # Creating relative discussion and cohort settings
        CourseCohortsSettings.objects.create(course_id=str(self.course.id))
        CourseDiscussionSettings.objects.create(course_id=str(self.course.id), _divided_discussions='[]')
        self.first_cohort = self.second_cohort = None

        # Duplicating roles
        self.student_role = RoleFactory(name=FORUM_ROLE_STUDENT, course_id=self.course.id)
        self.moderator_role = RoleFactory(name=FORUM_ROLE_MODERATOR, course_id=self.course.id)
        self.ta_role = RoleFactory(name=FORUM_ROLE_COMMUNITY_TA, course_id=self.course.id)
        self.group_community_ta_role = RoleFactory(name=FORUM_ROLE_GROUP_MODERATOR, course_id=self.course.id)

        # Creating users for with roles
        self.author = StaffFactory(course_key=self.course.id, username='Author')
        self.staff = StaffFactory(course_key=self.course.id, username='Staff')

        self.moderator = UserFactory(username='Moderator')
        self.moderator_role.users.add(self.moderator)

        self.ta = UserFactory(username='TA')
        self.ta_role.users.add(self.ta)

        self.group_ta_cohort_1 = UserFactory(username='Group TA 1')
        self.group_ta_cohort_2 = UserFactory(username='Group TA 2')
        self.group_community_ta_role.users.add(self.group_ta_cohort_1)
        self.group_community_ta_role.users.add(self.group_ta_cohort_2)

        self.learner_cohort_1 = UserFactory(username='Learner 1')
        self.learner_cohort_2 = UserFactory(username='Learner 2')
        self.student_role.users.add(self.learner_cohort_1)
        self.student_role.users.add(self.learner_cohort_2)

        # Creating a topic
        self.topic_id = 'test_topic'
        usage_key = self.course.id.make_usage_key('vertical', self.topic_id)
        self.topic = DiscussionTopicLink(
            context_key=self.course.id,
            usage_key=usage_key,
            title=f"Discussion on {self.topic_id}",
            external_id=self.topic_id,
            provider_id="openedx",
            ordering=1,
            enabled_in_context=True,
        )
        self.notification_to_all_users = [
            self.learner_cohort_1, self.learner_cohort_2, self.staff,
            self.moderator, self.ta, self.group_ta_cohort_1, self.group_ta_cohort_2
        ]
        self.privileged_users = [
            self.staff, self.moderator, self.ta
        ]
        self.cohort_1_users = [self.learner_cohort_1, self.group_ta_cohort_1] + self.privileged_users
        self.cohort_2_users = [self.learner_cohort_2, self.group_ta_cohort_2] + self.privileged_users
        self.thread = self._create_thread()

    def _configure_cohorts(self):
        """
        Configure cohort for course and assign membership to users
        """
        course_key_str = str(self.course.id)
        cohort_settings = CourseCohortsSettings.objects.get(course_id=course_key_str)
        cohort_settings.is_cohorted = True
        cohort_settings.save()

        discussion_settings = CourseDiscussionSettings.objects.get(course_id=course_key_str)
        discussion_settings.always_divide_inline_discussions = True
        discussion_settings.save()

        self.first_cohort = CohortFactory(course_id=self.course.id, name="FirstCohort")
        self.second_cohort = CohortFactory(course_id=self.course.id, name="SecondCohort")

        CohortMembership.assign(cohort=self.first_cohort, user=self.learner_cohort_1)
        CohortMembership.assign(cohort=self.first_cohort, user=self.group_ta_cohort_1)
        CohortMembership.assign(cohort=self.second_cohort, user=self.learner_cohort_2)
        CohortMembership.assign(cohort=self.second_cohort, user=self.group_ta_cohort_2)

    def _assign_enrollments(self):
        """
        Enrolls all the user in the course
        """
        user_list = [self.author] + self.notification_to_all_users
        for user in user_list:
            CourseEnrollment.enroll(user, self.course.id)

    def _create_thread(self, thread_type="discussion", group_id=None):
        """
        Create a thread
        """
        thread = make_minimal_cs_thread({
            'id': 1,
            'course_id': str(self.course.id),
            "commentable_id": self.topic_id,
            "username": self.author.username,
            "user_id": str(self.author.id),
            "thread_type": thread_type,
            "group_id": group_id,
            "title": "Test Title",
        })
        self.register_get_thread_response(thread)
        return thread

    def test_basic(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    def test_not_authenticated(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    @ddt.data(
        ('new_question_post',),
        ('new_discussion_post',),
    )
    @ddt.unpack
    def test_notification_is_send_to_all_enrollments(self, notification_type):
        """
        Tests notification is sent to all users if course is not cohorted
        """
        self._assign_enrollments()
        thread_type = (
            "discussion"
            if notification_type == "new_discussion_post"
            else ("question" if notification_type == "new_question_post" else "")
        )
        thread = self._create_thread(thread_type=thread_type)
        handler = mock.Mock()
        COURSE_NOTIFICATION_REQUESTED.connect(handler)
        send_thread_created_notification(thread['id'], str(self.course.id), self.author.id)
        self.assertEqual(handler.call_count, 1)
        course_notification_data = handler.call_args[1]['course_notification_data']
        assert notification_type == course_notification_data.notification_type
        notification_audience_filters = {}
        assert notification_audience_filters == course_notification_data.audience_filters

    @ddt.data(
        ('cohort_1', 'new_question_post'),
        ('cohort_1', 'new_discussion_post'),
        ('cohort_2', 'new_question_post'),
        ('cohort_2', 'new_discussion_post'),
    )
    @ddt.unpack
    def test_notification_is_send_to_cohort_ids(self, cohort_text, notification_type):
        """
        Tests if notification is sent only to privileged users and cohort members if the
        course is cohorted
        """
        self._assign_enrollments()
        self._configure_cohorts()
        cohort, audience = (
            (self.first_cohort, self.cohort_1_users)
            if cohort_text == "cohort_1"
            else ((self.second_cohort, self.cohort_2_users) if cohort_text == "cohort_2" else None)
        )

        thread_type = (
            "discussion"
            if notification_type == "new_discussion_post"
            else ("question" if notification_type == "new_question_post" else "")
        )

        cohort_id = cohort.id
        thread = self._create_thread(group_id=cohort_id, thread_type=thread_type)
        handler = mock.Mock()
        COURSE_NOTIFICATION_REQUESTED.connect(handler)
        send_thread_created_notification(thread['id'], str(self.course.id), self.author.id)
        course_notification_data = handler.call_args[1]['course_notification_data']
        assert notification_type == course_notification_data.notification_type
        notification_audience_filters = {
            'cohorts': [cohort_id],
            'course_roles': ['staff', 'instructor'],
            'discussion_roles': ['Administrator', 'Moderator', 'Community TA'],
        }
        assert notification_audience_filters == handler.call_args[1]['course_notification_data'].audience_filters
        self.assertEqual(handler.call_count, 1)


class TestResponseEndorsedNotifications(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """
    Test case to send response endorsed notifications
    """

    def setUp(self):
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.set_mock_return_value('get_course_id_by_thread', str(self.course.id))
        self.set_mock_return_value('get_course_id_by_comment', str(self.course.id))
        self.user_1 = UserFactory.create()
        CourseEnrollment.enroll(self.user_1, self.course.id)
        self.user_2 = UserFactory.create()
        self.user_3 = UserFactory.create()
        CourseEnrollment.enroll(self.user_2, self.course.id)
        CourseEnrollment.enroll(self.user_3, self.course.id)

    def test_basic(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    def test_not_authenticated(self):
        """
        Left empty intentionally. This test case is inherited from DiscussionAPIViewTestMixin
        """

    def test_response_endorsed_notifications(self):
        """
        Tests response endorsed notifications
        """
        thread = ThreadMock(thread_id=1, creator=self.user_1, title='test thread')
        response = ThreadMock(thread_id=2, creator=self.user_2, title='test response')
        self.register_get_thread_response({
            'id': thread.id,
            'course_id': str(self.course.id),
            'topic_id': 'abc',
            "user_id": thread.user_id,
            "username": thread.username,
            "thread_type": 'discussion',
            "title": thread.title,
            "commentable_id": thread.commentable_id,
        })
        self.register_get_comment_response({
            'id': 1,
            'thread_id': thread.id,
            'user_id': response.user_id
        })
        self.register_get_comment_response({
            'id': 2,
            'thread_id': thread.id,
            'user_id': response.user_id
        })
        handler = mock.Mock()
        USER_NOTIFICATION_REQUESTED.connect(handler)
        send_response_endorsed_notifications(thread.id, response.id, str(self.course.id), self.user_3.id)
        self.assertEqual(handler.call_count, 2)

        # Test response endorsed on thread notification
        notification_data = handler.call_args_list[0][1]['notification_data']
        # Target only the thread author
        self.assertEqual([int(user_id) for user_id in notification_data.user_ids], [int(thread.user_id)])
        self.assertEqual(notification_data.notification_type, 'response_endorsed_on_thread')

        expected_context = {
            'replier_name': self.user_2.username,
            'post_title': 'test thread',
            'course_name': self.course.display_name,
            'sender_id': int(self.user_2.id),
            'email_content': 'dummy',
            'response_id': None,
            'topic_id': None,
            'thread_id': 1,
            'comment_id': 2,
        }
        self.assertDictEqual(notification_data.context, expected_context)
        self.assertEqual(notification_data.content_url, _get_mfe_url(self.course.id, thread.id))
        self.assertEqual(notification_data.app_name, 'discussion')
        self.assertEqual('response_endorsed_on_thread', notification_data.notification_type)

        # Test response endorsed notification
        notification_data = handler.call_args_list[1][1]['notification_data']
        # Target only the response author
        self.assertEqual([int(user_id) for user_id in notification_data.user_ids], [int(response.user_id)])
        self.assertEqual(notification_data.notification_type, 'response_endorsed')

        expected_context = {
            'replier_name': response.username,
            'post_title': 'test thread',
            'course_name': self.course.display_name,
            'sender_id': int(response.user_id),
            'email_content': 'dummy',
            'response_id': None,
            'topic_id': None,
            'thread_id': 1,
            'comment_id': 2,
        }
        self.assertDictEqual(notification_data.context, expected_context)
        self.assertEqual(notification_data.content_url, _get_mfe_url(self.course.id, thread.id))
        self.assertEqual(notification_data.app_name, 'discussion')
        self.assertEqual('response_endorsed', notification_data.notification_type)
