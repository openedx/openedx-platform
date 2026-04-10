"""
Tests for keyword_substitution.py
"""
from unittest.mock import patch

from ddt import ddt, file_data

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util import keyword_substitution as Ks
from common.djangoapps.util.date_utils import get_default_time_display
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt
class KeywordSubTest(ModuleStoreTestCase):
    """ Tests for the keyword substitution feature """

    CREATE_USER = False

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(
            email="testuser@edx.org",
            username="testuser",
            profile__name="Test User"
        )
        self.course = CourseFactory.create(
            org='edx',
            course='999',
            display_name='test_course'
        )

        self.context = {
            'user_id': self.user.id,
            'course_title': self.course.display_name,
            'name': self.user.profile.name,
            'course_end_date': get_default_time_display(self.course.end),
        }

    @file_data('fixtures/test_keyword_coursename_sub.json')
    def test_course_name_sub(self, test_string, expected):
        """ Tests subbing course name in various scenarios """
        course_name = self.course.display_name
        result = Ks.substitute_keywords_with_data(
            test_string, self.context,
        )

        assert course_name in result
        assert result == expected

    def test_anonymous_id_sub(self):
        """
        Test that anonymous_id is subbed
        """
        test_string = "Turn %%USER_ID%% into anonymous id"
        anonymous_id = Ks.anonymous_id_from_user_id(self.user.id)
        result = Ks.substitute_keywords_with_data(
            test_string, self.context,
        )
        assert '%%USER_ID%%' not in result
        assert anonymous_id in result

    def test_name_sub(self):
        """
        Test that the user's full name is correctly subbed
        """
        test_string = "This is the test string. subthis: %%USER_FULLNAME%% into user name"
        user_name = self.user.profile.name
        result = Ks.substitute_keywords_with_data(
            test_string, self.context,
        )

        assert '%%USER_FULLNAME%%' not in result
        assert user_name in result

    def test_illegal_subtag(self):
        """
        Test that sub-ing doesn't ocurr with illegal tags
        """
        test_string = "%%user_id%%"
        result = Ks.substitute_keywords_with_data(
            test_string, self.context,
        )

        assert test_string == result

    def test_should_not_sub(self):
        """
        Test that sub-ing doesn't work without subtags
        """
        test_string = "this string has no subtags"
        result = Ks.substitute_keywords_with_data(
            test_string, self.context,
        )

        assert test_string == result

    @file_data('fixtures/test_keywordsub_multiple_tags.json')
    def test_sub_multiple_tags(self, test_string, expected):
        """ Test that subbing works with multiple subtags """
        anon_id = '123456789'

        with patch('common.djangoapps.util.keyword_substitution.anonymous_id_from_user_id', lambda user_id: anon_id):
            result = Ks.substitute_keywords_with_data(
                test_string, self.context,
            )
            assert result == expected

    def test_subbing_no_userid_or_courseid(self):
        """
        Tests that no subbing occurs if no user_id or no course_id is given.
        """
        test_string = 'This string should not be subbed here %%USER_ID%%'

        no_course_context = {
            key: value for key, value in self.context.items() if key != 'course_title'
        }
        result = Ks.substitute_keywords_with_data(test_string, no_course_context)
        assert test_string == result

        no_user_id_context = {
            key: value for key, value in self.context.items() if key != 'user_id'
        }
        result = Ks.substitute_keywords_with_data(test_string, no_user_id_context)
        assert test_string == result

    def test_sec_xss_keyword_substitution_regression_html_escape_enabled(self):
        """
        Regression test: with ``html_escape=True``, a malicious full name
        containing HTML/script is escaped before insertion, preventing
        stored XSS in the ACE bulk email body (which renders inside
        ``{% autoescape off %}``).
        """
        self.user.profile.name = '<script>alert("xss")</script>'
        self.user.profile.save()
        self.context['name'] = self.user.profile.name

        test_string = 'Hello %%USER_FULLNAME%%!'
        result = Ks.substitute_keywords_with_data(
            test_string, self.context, html_escape=True,
        )

        assert '<script>' not in result
        assert '</script>' not in result
        assert '&lt;script&gt;' in result
        assert '%%USER_FULLNAME%%' not in result

    def test_sec_xss_keyword_substitution_regression_html_escape_default_off(self):
        """
        Regression test: default ``html_escape=False`` preserves plaintext
        behaviour so the ``text_message`` branch does not emit ``&lt;``
        into plain-text emails and the legacy ``render_htmltext`` path
        (which pre-escapes its context) does not double-escape.
        """
        self.context['name'] = 'Ada <Lovelace>'
        test_string = 'Hi %%USER_FULLNAME%%'
        result = Ks.substitute_keywords_with_data(test_string, self.context)
        assert result == 'Hi Ada <Lovelace>'

    def test_sec_xss_keyword_substitution_regression_course_title_escaped(self):
        """
        Regression test: course titles are user-adjacent (set by course
        staff) and must be escaped when ``html_escape=True``.
        """
        self.context['course_title'] = 'Intro <img src=x onerror=alert(1)>'
        test_string = 'Welcome to %%COURSE_DISPLAY_NAME%%'
        result = Ks.substitute_keywords_with_data(
            test_string, self.context, html_escape=True,
        )
        assert '<img' not in result
        assert '&lt;img src=x onerror=alert(1)&gt;' in result
