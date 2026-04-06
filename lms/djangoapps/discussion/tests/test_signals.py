"""
Tests the forum notification signals.
"""
from unittest import mock

from django.test import TestCase
from edx_django_utils.cache import RequestCache

from lms.djangoapps.discussion.signals.handlers import ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY
from openedx.core.djangoapps.django_comment_common import models, signals
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class SendMessageHandlerTestCase(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        self.sender = mock.Mock()
        self.user = mock.Mock()
        self.post = mock.MagicMock()
        self.course_key_str = 'course-v1:edX+DemoX+Demo_Course'
        self.post.thread.course_id = self.course_key_str
        self.post.thread.attributes = {
            'thread_id': 'thread-id',
            'course_id': self.course_key_str,
            'parent_id': None
        }

        self.site = SiteFactory.create()

    @mock.patch('lms.djangoapps.discussion.signals.handlers.get_current_site')
    @mock.patch('lms.djangoapps.discussion.signals.handlers.send_message')
    def test_comment_created_signal_sends_message(self, mock_send_message, mock_get_current_site):
        site_config = SiteConfigurationFactory.create(site=self.site)
        enable_notifications_cfg = {ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY: True}
        site_config.site_values = enable_notifications_cfg
        site_config.save()
        mock_get_current_site.return_value = self.site
        with mock.patch('lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async'):
            signals.comment_created.send(sender=self.sender, user=self.user, post=self.post)

        mock_send_message.assert_called_once_with(self.post, mock_get_current_site.return_value)

    @mock.patch('lms.djangoapps.discussion.signals.handlers.get_current_site', return_value=None)
    @mock.patch('lms.djangoapps.discussion.signals.handlers.send_message')
    def test_comment_created_signal_message_not_sent_without_site(self, mock_send_message, mock_get_current_site):  # lint-amnesty, pylint: disable=unused-argument
        with mock.patch('lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async'):
            signals.comment_created.send(sender=self.sender, user=self.user, post=self.post)

        assert not mock_send_message.called

    @mock.patch('lms.djangoapps.discussion.signals.handlers.get_current_site')
    @mock.patch('lms.djangoapps.discussion.signals.handlers.send_message')
    def test_comment_created_signal_msg_not_sent_without_site_config(self, mock_send_message, mock_get_current_site):
        mock_get_current_site.return_value = self.site
        with mock.patch('lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async'):
            signals.comment_created.send(sender=self.sender, user=self.user, post=self.post)

        assert not mock_send_message.called

    @mock.patch('lms.djangoapps.discussion.signals.handlers.get_current_site')
    @mock.patch('lms.djangoapps.discussion.signals.handlers.send_message')
    def test_comment_created_signal_msg_not_sent_with_site_config_disabled(
            self, mock_send_message, mock_get_current_site
    ):
        site_config = SiteConfigurationFactory.create(site=self.site)
        enable_notifications_cfg = {ENABLE_FORUM_NOTIFICATIONS_FOR_SITE_KEY: False}
        site_config.site_values = enable_notifications_cfg
        site_config.save()
        mock_get_current_site.return_value = self.site
        with mock.patch('lms.djangoapps.discussion.rest_api.tasks.send_response_notifications.apply_async'):
            signals.comment_created.send(sender=self.sender, user=self.user, post=self.post)

        assert not mock_send_message.called


class CoursePublishHandlerTestCase(ModuleStoreTestCase):
    """
    Tests for discussion updates on course publish.
    """
    ENABLED_SIGNALS = ['course_published']

    def test_discussion_id_map_updates_on_publish(self):
        course_key_args = dict(org='org', course='number', run='run')
        course_key = self.store.make_course_key(**course_key_args)

        # create course
        course = CourseFactory(emit_signals=True, **course_key_args)
        assert course.id == course_key
        self._assert_discussion_id_map(course_key, {})

        # create discussion block
        RequestCache().clear()
        discussion_id = 'discussion1'
        discussion_block = BlockFactory.create(
            parent_location=course.location,
            category="discussion",
            discussion_id=discussion_id,
        )
        self._assert_discussion_id_map(course_key, {discussion_id: str(discussion_block.location)})

    def _assert_discussion_id_map(self, course_key, expected_map):
        """
        Verifies the discussion ID map for the given course matches the expected value.
        """
        mapping_entry = models.DiscussionsIdMapping.objects.get(course_id=course_key)
        self.assertDictEqual(mapping_entry.mapping, expected_map)
