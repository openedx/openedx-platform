"""
Unit tests for user messages.
"""


import warnings

import ddt
from django.contrib.messages.middleware import MessageMiddleware
from django.test import RequestFactory, TestCase

from common.djangoapps.student.tests.factories import UserFactory
from common.test.utils import normalize_repr
from openedx.core.djangolib.markup import HTML, Text

from ..user_messages import PageLevelMessages, UserMessageType

TEST_MESSAGE = 'Test message'


@ddt.ddt
class UserMessagesTestCase(TestCase):
    """
    Unit tests for page level user messages.
    """
    def setUp(self):
        super().setUp()
        self.student = UserFactory.create()
        self.request = RequestFactory().request()
        self.request.session = {}
        self.request.user = self.student
        MessageMiddleware(get_response=lambda request: None).process_request(self.request)

    @ddt.data(
        ('Rock & Roll', '<div class="message-content">Rock &amp; Roll</div>'),
        (Text('Rock & Roll'), '<div class="message-content">Rock &amp; Roll</div>'),
        (HTML('<p>Hello, world!</p>'), '<div class="message-content"><p>Hello, world!</p></div>')
    )
    @ddt.unpack
    def test_message_escaping(self, message, expected_message_html):
        """
        Verifies that a user message is escaped correctly.
        """
        PageLevelMessages.register_user_message(self.request, UserMessageType.INFO, message)
        messages = list(PageLevelMessages.user_messages(self.request))
        assert len(messages) == 1
        assert messages[0].message_html == expected_message_html

    @ddt.data(
        (UserMessageType.ERROR, 'alert-danger', 'fa fa-warning'),
        (UserMessageType.INFO, 'alert-info', 'fa fa-bullhorn'),
        (UserMessageType.SUCCESS, 'alert-success', 'fa fa-check-circle'),
        (UserMessageType.WARNING, 'alert-warning', 'fa fa-warning'),
    )
    @ddt.unpack
    def test_message_icon(self, message_type, expected_css_class, expected_icon_class):
        """
        Verifies that a user message returns the correct CSS and icon classes.
        """
        PageLevelMessages.register_user_message(self.request, message_type, TEST_MESSAGE)
        messages = list(PageLevelMessages.user_messages(self.request))
        assert len(messages) == 1
        assert messages[0].css_class == expected_css_class
        assert messages[0].icon_class == expected_icon_class

    @ddt.data(
        (normalize_repr(PageLevelMessages.register_error_message), UserMessageType.ERROR),
        (normalize_repr(PageLevelMessages.register_info_message), UserMessageType.INFO),
        (normalize_repr(PageLevelMessages.register_success_message), UserMessageType.SUCCESS),
        (normalize_repr(PageLevelMessages.register_warning_message), UserMessageType.WARNING),
    )
    @ddt.unpack
    def test_message_type(self, register_message_function, expected_message_type):
        """
        Verifies that each user message function returns the correct type.
        """
        register_message_function(self.request, TEST_MESSAGE)
        messages = list(PageLevelMessages.user_messages(self.request))
        assert len(messages) == 1
        assert messages[0].type == expected_message_type

    def global_message_count(self):
        """
        Count the number of times the global message appears in the user messages.
        """
        expected_html = """<div class="message-content">I &lt;3 HTML-escaping</div>"""
        messages = list(PageLevelMessages.user_messages(self.request))
        return len(list(msg for msg in messages if expected_html in msg.message_html))

    def test_global_message_off_by_default(self):
        """Verifies feature toggle."""
        with self.settings(
            GLOBAL_NOTICE_ENABLED=False,
            GLOBAL_NOTICE_MESSAGE="I <3 HTML-escaping",
            GLOBAL_NOTICE_TYPE='WARNING'
        ):
            # Missing when feature disabled
            assert self.global_message_count() == 0

    def test_global_message_persistent(self):
        """Verifies global message is always included, when enabled."""
        with self.settings(
            GLOBAL_NOTICE_ENABLED=True,
            GLOBAL_NOTICE_MESSAGE="I <3 HTML-escaping",
            GLOBAL_NOTICE_TYPE='WARNING'
        ):
            # Present with no other setup
            assert self.global_message_count() == 1

            # Present when other messages are present
            PageLevelMessages.register_user_message(self.request, UserMessageType.INFO, "something else")
            assert self.global_message_count() == 1

    def test_global_message_error_isolation(self):
        """Verifies that any setting errors don't break the page, or other messages."""
        with self.settings(
            GLOBAL_NOTICE_ENABLED=True,
            GLOBAL_NOTICE_MESSAGE=ThrowingMarkup(),  # force an error
            GLOBAL_NOTICE_TYPE='invalid'
        ):
            PageLevelMessages.register_user_message(self.request, UserMessageType.WARNING, "something else")
            # Doesn't throw, or even interfere with other messages,
            # when given invalid settings
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter('always')
                messages = list(PageLevelMessages.user_messages(self.request))
                assert len(w) == 1
                assert str(w[0].message) == "Could not register global notice: Exception('Some random error')"
            assert len(messages) == 1
            assert "something else" in messages[0].message_html


class ThrowingMarkup:
    """Class that raises an exception if markupsafe tries to get HTML from it."""
    def __html__(self):
        raise Exception("Some random error")
