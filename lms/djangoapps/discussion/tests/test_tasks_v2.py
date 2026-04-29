"""
Tests the execution of forum notification tasks.
"""

import math
from datetime import datetime, timedelta
from unittest import mock

import ddt
from django.contrib.sites.models import Site
from edx_ace.channel import ChannelType, get_channel_for_message
from edx_ace.recipient import Recipient
from edx_ace.renderers import EmailRenderer
from edx_ace.utils import date

import openedx.core.djangoapps.django_comment_common.comment_client as cc
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.discussion.django_comment_client.tests.mixins import MockForumApiMixin
from lms.djangoapps.discussion.signals.handlers import ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY
from lms.djangoapps.discussion.tasks import _is_first_comment, _should_send_message, _track_notification_sent
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.django_comment_common.signals import comment_created
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from openedx.core.lib.celery.task_utils import emulate_http_request
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

NOW = datetime.utcnow()
ONE_HOUR_AGO = NOW - timedelta(hours=1)
TWO_HOURS_AGO = NOW - timedelta(hours=2)


def make_subscribed_threads_callback(subscribed_thread_ids, per_page=1):
    """
    Creates a callback function for simulating user data.
    """

    def callback(*args, **kwargs):  # pylint: disable=unused-argument
        subscribed_thread_collection = [
            {"id": thread_id} for thread_id in subscribed_thread_ids
        ]
        page = kwargs.get("page", 1)
        start_index = per_page * (page - 1)
        end_index = per_page * page
        data = {
            "collection": subscribed_thread_collection[start_index:end_index],
            "page": page,
            "num_pages": int(
                math.ceil(len(subscribed_thread_collection) / float(per_page))
            ),
            "thread_count": len(subscribed_thread_collection),
        }
        return data

    return callback


@ddt.ddt
class TaskTestCase(
    ModuleStoreTestCase, MockForumApiMixin
):  # pylint: disable=missing-class-docstring

    @classmethod
    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()
        cls.discussion_id = "dummy_discussion_id"
        cls.question_id = "dummy_question_id"
        cls.course = CourseOverviewFactory.create(language="fr")

        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with mock.patch("common.djangoapps.student.models.user.cc.User.save"):
            cls.thread_author = UserFactory(
                username="thread_author", password="password", email="email"
            )
            cls.comment_author = UserFactory(
                username="comment_author", password="password", email="email"
            )

            CourseEnrollmentFactory(user=cls.thread_author, course_id=cls.course.id)
            CourseEnrollmentFactory(user=cls.comment_author, course_id=cls.course.id)

        cls.create_threads_and_comments()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    def _set_forum_mocks(
        self,
        subscribed_thread_ids=None,
        thread_data=None,
        comment_data=None,
        per_page=1,
    ):
        """mock threads and comments"""
        if subscribed_thread_ids:
            self.set_mock_side_effect(
                "get_user_subscriptions",
                make_subscribed_threads_callback(subscribed_thread_ids, per_page),
            )
        if thread_data:
            self.set_mock_return_value("get_thread", thread_data)
        if comment_data:
            self.set_mock_return_value("get_parent_comment", comment_data)

    @classmethod
    def create_threads_and_comments(
        cls,
    ):  # pylint: disable=missing-function-docstring
        # Regular discussion threads and comments.
        cls.discussion_thread = {
            "id": cls.discussion_id,
            "course_id": str(cls.course.id),
            "created_at": date.serialize(TWO_HOURS_AGO),
            "title": "discussion-thread-title",
            "user_id": cls.thread_author.id,
            "username": cls.thread_author.username,
            "commentable_id": "discussion-thread-commentable-id",
            "thread_type": "discussion",
        }
        cls.discussion_comment = {
            "id": "discussion-comment",
            "body": "discussion-comment-body",
            "created_at": date.serialize(ONE_HOUR_AGO),
            "thread_id": cls.discussion_thread["id"],
            "parent_id": None,
            "user_id": cls.comment_author.id,
            "username": cls.comment_author.username,
            "course_id": str(cls.course.id),
        }
        cls.discussion_comment2 = {
            "id": "discussion-comment2",
            "body": "discussion-comment2-body",
            "created_at": date.serialize(NOW),
            "thread_id": cls.discussion_thread["id"],
            "parent_id": None,
            "user_id": cls.comment_author.id,
            "username": cls.comment_author.username,
            "course_id": str(cls.course.id),
        }
        cls.discussion_subcomment = {
            "id": "discussion-subcomment",
            "body": "discussion-subcomment-body",
            "created_at": date.serialize(NOW),
            "thread_id": cls.discussion_thread["id"],
            "parent_id": cls.discussion_comment["id"],
            "user_id": cls.comment_author.id,
            "username": cls.comment_author.username,
            "course_id": str(cls.course.id),
        }
        cls.discussion_thread["children"] = [
            cls.discussion_comment,
            cls.discussion_comment2,
        ]
        cls.discussion_comment["child_count"] = 1
        cls.discussion_thread2 = {
            "id": cls.discussion_id,
            "course_id": str(cls.course.id),
            "created_at": date.serialize(TWO_HOURS_AGO),
            "title": "discussion-thread-2-title",
            "user_id": cls.thread_author.id,
            "username": cls.thread_author.username,
            "commentable_id": "discussion-thread-commentable-id-2",
            "thread_type": "discussion",
        }
        # Qeustion threads and comments.
        cls.question_thread = {
            "id": cls.question_id,
            "course_id": str(cls.course.id),
            "created_at": date.serialize(TWO_HOURS_AGO),
            "title": "question-thread-title",
            "user_id": cls.thread_author.id,
            "username": cls.thread_author.username,
            "commentable_id": "question-thread-commentable-id-3",
            "thread_type": "question",
        }
        cls.question_comment = {
            "id": "question-comment",
            "body": "question-comment-body",
            "created_at": date.serialize(ONE_HOUR_AGO),
            "thread_id": cls.question_thread["id"],
            "parent_id": None,
            "user_id": cls.comment_author.id,
            "username": cls.comment_author.username,
            "course_id": str(cls.course.id),
        }
        cls.question_comment2 = {
            "id": "question-comment2",
            "body": "question-comment2-body",
            "created_at": date.serialize(NOW),
            "thread_id": cls.question_thread["id"],
            "parent_id": None,
            "user_id": cls.comment_author.id,
            "username": cls.comment_author.username,
            "course_id": str(cls.course.id),
        }
        cls.question_subcomment = {
            "id": "question-subcomment",
            "body": "question-subcomment-body",
            "created_at": date.serialize(NOW),
            "thread_id": cls.question_thread["id"],
            "parent_id": cls.question_comment["id"],
            "user_id": cls.comment_author.id,
            "username": cls.comment_author.username,
            "course_id": str(cls.course.id),
        }
        cls.question_thread["endorsed_responses"] = [cls.question_comment]
        cls.question_thread["non_endorsed_responses"] = [cls.question_comment2]
        cls.question_comment["child_count"] = 1
        cls.question_thread2 = {
            "id": cls.question_id,
            "course_id": str(cls.course.id),
            "created_at": date.serialize(TWO_HOURS_AGO),
            "title": "question-thread-2-title",
            "user_id": cls.thread_author.id,
            "username": cls.thread_author.username,
            "commentable_id": "question-thread-commentable-id-2",
            "thread_type": "question",
        }

    def setUp(self):
        super().setUp()

        self.ace_send_patcher = mock.patch("edx_ace.ace.send")
        self.mock_ace_send = self.ace_send_patcher.start()
        self.mock_message_patcher = mock.patch(
            "lms.djangoapps.discussion.tasks.ResponseNotification"
        )
        self.mock_message = self.mock_message_patcher.start()

        thread_permalink = "/courses/discussion/dummy_discussion_id"
        self.permalink_patcher = mock.patch(
            "lms.djangoapps.discussion.tasks.permalink", return_value=thread_permalink
        )
        self.mock_permalink = self.permalink_patcher.start()
        self.set_mock_return_value("get_course_id_by_thread", str(self.course.id))
        self.set_mock_return_value("get_course_id_by_comment", str(self.course.id))

    def tearDown(self):
        super().tearDown()
        self.ace_send_patcher.stop()
        self.mock_message_patcher.stop()
        self.permalink_patcher.stop()

    @ddt.data(True)
    def test_send_discussion_email_notification(self, user_subscribed):
        self.mock_message_patcher.stop()
        if user_subscribed:
            non_matching_id = "not-a-match"
            # with per_page left with a default value of 1, this ensures
            # that we test a multiple page result when calling
            # comment_client.User.subscribed_threads()
            subscribed_thread_ids = [
                non_matching_id,
                self.discussion_id,
                self.question_id,
            ]
        else:
            subscribed_thread_ids = []

        site = Site.objects.get_current()
        site_config = SiteConfigurationFactory.create(site=site)
        site_config.site_values[ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY] = True
        site_config.save()
        examples = [
            (self.discussion_thread, self.discussion_comment),
            (self.question_thread, self.question_comment),
        ]
        for thread, comment in examples:
            self.mock_ace_send.reset_mock()
            self._set_forum_mocks(
                subscribed_thread_ids=subscribed_thread_ids,
                comment_data=comment,
                thread_data=thread,
            )
            user = mock.Mock()
            comment = cc.Comment.find(id=comment["id"]).retrieve()
            with mock.patch(
                "lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async"
            ):
                with mock.patch(
                    "lms.djangoapps.discussion.signals.handlers.get_current_site",
                    return_value=site,
                ):
                    comment_created.send(sender=None, user=user, post=comment)
            if user_subscribed:
                expected_message_context = get_base_template_context(site)
                expected_message_context.update(
                    {
                        "comment_author_id": self.comment_author.id,
                        "comment_body": comment["body"],
                        "comment_body_text": comment.body_text,
                        "comment_created_at": ONE_HOUR_AGO,
                        "comment_id": comment["id"],
                        "comment_parent_id": comment["parent_id"],
                        "comment_username": self.comment_author.username,
                        "course_id": self.course.id,
                        "thread_author_id": self.thread_author.id,
                        "thread_created_at": TWO_HOURS_AGO,
                        "thread_id": thread["id"],
                        "thread_title": thread["title"],
                        "thread_username": self.thread_author.username,
                        "thread_commentable_id": thread["commentable_id"],
                        "post_link": f"https://{site.domain}{self.mock_permalink.return_value}",
                        "site": site,
                        "site_id": site.id,
                        "push_notification_extra_context": {
                            "notification_type": "forum_response",
                            "topic_id": thread["commentable_id"],
                            "course_id": comment["course_id"],
                            "parent_id": str(comment["parent_id"]),
                            "thread_id": thread["id"],
                            "comment_id": comment["id"],
                        },
                    }
                )
                expected_recipient = Recipient(
                    self.thread_author.id, self.thread_author.email
                )
                actual_message = self.mock_ace_send.call_args_list[0][0][0]
                assert expected_message_context == actual_message.context
                assert expected_recipient == actual_message.recipient
                assert self.course.language == actual_message.language
                self._assert_rendered_email(actual_message, comment)

            else:
                assert not self.mock_ace_send.called

    def _assert_rendered_email(
        self, message, comment
    ):  # pylint: disable=missing-function-docstring
        # check that we can actually render the message
        with emulate_http_request(
            site=message.context["site"], user=self.thread_author
        ):
            rendered_email = EmailRenderer().render(
                get_channel_for_message(ChannelType.EMAIL, message), message
            )
            assert comment["body"] in rendered_email.body_html
            assert self.comment_author.username in rendered_email.body_html
            assert self.mock_permalink.return_value in rendered_email.body_html
            assert message.context["site"].domain in rendered_email.body_html

    def run_should_not_send_email_test(self, thread, comment_dict):
        """
        assert email is not sent
        """
        self._set_forum_mocks(
            subscribed_thread_ids=[self.discussion_id, self.question_id],
            comment_data=comment_dict,
            thread_data=thread,
        )
        user = mock.Mock()
        comment = cc.Comment.find(id=comment_dict["id"]).retrieve()
        with mock.patch(
            "lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async"
        ):
            comment_created.send(sender=None, user=user, post=comment)

        actual_result = _should_send_message(  # noqa: F841
            {
                "thread_author_id": self.thread_author.id,
                "course_id": self.course.id,
                "comment_id": comment_dict["id"],
                "thread_id": thread["id"],
            }
        )

        should_email_send = _is_first_comment(comment_dict["id"], thread["id"])
        assert not should_email_send
        assert not self.mock_ace_send.called

    def test_subcomment_should_not_send_email(self):
        self.run_should_not_send_email_test(
            self.discussion_thread, self.discussion_subcomment
        )
        self.run_should_not_send_email_test(
            self.question_subcomment, self.question_subcomment
        )

    def test_second_comment_should_not_send_email(self):
        self.run_should_not_send_email_test(
            self.discussion_thread, self.discussion_comment2
        )
        self.run_should_not_send_email_test(
            self.question_thread, self.question_comment2
        )

    def test_thread_without_children_should_not_send_email(self):
        """
        test that email notification will not be sent for the thread
        that doesn't have attribute 'children'
        """
        self.run_should_not_send_email_test(
            self.discussion_thread2, self.discussion_comment
        )
        self.run_should_not_send_email_test(
            self.question_thread2, self.question_comment
        )

    @ddt.data(
        (
            {
                "thread_id": "dummy_discussion_id",
                "thread_title": "thread-title",
                "thread_created_at": date.serialize(datetime(2000, 1, 1, 0, 0, 0)),
                "course_id": "fake_course_edx",
                "thread_author_id": "a_fake_dude",
            },
            {
                "app_label": "discussion",
                "name": "responsenotification",
                "language": "en",
                "uuid": "uuid1",
                "send_uuid": "uuid2",
                "thread_id": "dummy_discussion_id",
                "course_id": "fake_course_edx",
                "thread_created_at": datetime(2000, 1, 1, 0, 0, 0),
            },
        ),
        (
            {
                "thread_id": "dummy_discussion_id2",
                "thread_title": "thread-title2",
                "thread_created_at": date.serialize(datetime(2000, 1, 1, 0, 0, 0)),
                "course_id": "fake_course_edx2",
                "thread_author_id": "a_fake_dude2",
            },
            {
                "app_label": "discussion",
                "name": "responsenotification",
                "language": "en",
                "uuid": "uuid3",
                "send_uuid": "uuid4",
                "thread_id": "dummy_discussion_id2",
                "course_id": "fake_course_edx2",
                "thread_created_at": datetime(2000, 1, 1, 0, 0, 0),
            },
        ),
    )
    @ddt.unpack
    def test_track_notification_sent(self, context, test_props):
        with mock.patch("edx_ace.ace.send").start() as message:
            # Populate mock message (
            # There are some cruft attrs, but they're harmless.
            for key, entry in test_props.items():
                setattr(message, key, entry)

        test_props["nonInteraction"] = True
        # Also augment context with site object, for setting segment context.
        site = Site.objects.get_current()
        context["site"] = site
        with mock.patch(
            "lms.djangoapps.discussion.tasks.segment.track"
        ) as mock_segment_track:
            _track_notification_sent(message, context)
            mock_segment_track.assert_called_once_with(
                user_id=context["thread_author_id"],
                event_name="edx.bi.email.sent",
                properties=test_props,
            )
