"""
Tests the forum notification views.
"""
import json
import logging
from datetime import datetime
from unittest import mock
from unittest.mock import ANY, Mock, call, patch

import pytest
from django.test.client import Client, RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import translation
from edx_django_utils.cache import RequestCache

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.discussion import views
from lms.djangoapps.discussion.django_comment_client.constants import TYPE_ENTRY, TYPE_SUBCATEGORY
from lms.djangoapps.discussion.django_comment_client.permissions import get_team
from lms.djangoapps.discussion.django_comment_client.tests.utils import config_course_discussions, topic_name_to_id
from lms.djangoapps.discussion.views import _get_discussion_default_topic_id, course_discussions_settings_handler
from openedx.core.djangoapps.course_groups.tests.helpers import config_course_cohorts
from openedx.core.djangoapps.course_groups.tests.test_views import CohortViewsTestCase
from openedx.core.djangoapps.django_comment_common.comment_client.utils import CommentClientPaginatedResult
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings
from openedx.core.djangoapps.django_comment_common.utils import ThreadContext
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

log = logging.getLogger(__name__)

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES


class ViewsExceptionTestCase(UrlResetMixin, ModuleStoreTestCase):  # pylint: disable=missing-class-docstring

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):

        # Patching the ENABLE_DISCUSSION_SERVICE value affects the contents of urls.py,
        # so we need to call super.setUp() which reloads urls.py (because
        # of the UrlResetMixin)
        super().setUp()

        # create a course
        self.course = CourseFactory.create(org='MITx', course='999',
                                           display_name='Robot Super Course')

        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch('common.djangoapps.student.models.user.cc.User.save'):
            uname = 'student'
            email = 'student@edx.org'
            password = 'test'

            # Create the student
            self.student = UserFactory(username=uname, password=password, email=email)

            # Enroll the student in the course
            CourseEnrollmentFactory(user=self.student, course_id=self.course.id)

            # Log the student in
            self.client = Client()
            assert self.client.login(username=uname, password=password)

        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        )
        self.mock_get_course_id_by_thread = patcher.start()
        self.addCleanup(patcher.stop)

    @patch('common.djangoapps.student.models.user.cc.User.from_django_user')
    @patch('openedx.core.djangoapps.django_comment_common.comment_client.user.User.active_threads')
    def test_user_profile_exception(self, mock_threads, mock_from_django_user):

        # Mock the code that makes the HTTP requests to the cs_comment_service app
        # for the profiled user's active threads
        mock_threads.return_value = [], 1, 1

        # Mock the code that makes the HTTP request to the cs_comment_service app
        # that gets the current user's info
        mock_from_django_user.return_value = Mock()

        url = reverse('user_profile',
                      kwargs={'course_id': str(self.course.id), 'user_id': '12345'})  # There is no user 12345
        response = self.client.get(url)
        assert response.status_code == 404

    @patch('common.djangoapps.student.models.user.cc.User.from_django_user')
    @patch('common.djangoapps.student.models.user.cc.User.subscribed_threads')
    def test_user_followed_threads_exception(self, mock_threads, mock_from_django_user):

        # Mock the code that makes the HTTP requests to the cs_comment_service app
        # for the profiled user's active threads
        mock_threads.return_value = CommentClientPaginatedResult(collection=[], page=1, num_pages=1)

        # Mock the code that makes the HTTP request to the cs_comment_service app
        # that gets the current user's info
        mock_from_django_user.return_value = Mock()

        url = reverse('followed_threads',
                      kwargs={'course_id': str(self.course.id), 'user_id': '12345'})  # There is no user 12345
        response = self.client.get(url)
        assert response.status_code == 404


def make_mock_thread_data(  # pylint: disable=missing-function-docstring
        course,
        text,
        thread_id,
        num_children,
        group_id=None,
        group_name=None,
        commentable_id=None,
        is_commentable_divided=None,
        anonymous=False,
        anonymous_to_peers=False,
):
    data_commentable_id = (
        commentable_id or course.discussion_topics.get('General', {}).get('id') or "dummy_commentable_id"
    )
    thread_data = {
        "id": thread_id,
        "type": "thread",
        "title": text,
        "body": text,
        "commentable_id": data_commentable_id,
        "resp_total": 42,
        "resp_skip": 25,
        "resp_limit": 5,
        "group_id": group_id,
        "anonymous": anonymous,
        "anonymous_to_peers": anonymous_to_peers,
        "context": (
            ThreadContext.COURSE if get_team(data_commentable_id) is None else ThreadContext.STANDALONE
        )
    }
    if group_id is not None:
        thread_data['group_name'] = group_name
    if is_commentable_divided is not None:
        thread_data['is_commentable_divided'] = is_commentable_divided
    if num_children is not None:
        thread_data["children"] = [{
            "id": f"dummy_comment_id_{i}",
            "type": "comment",
            "body": text,
        } for i in range(num_children)]
    return thread_data


def make_mock_collection_data(  # pylint: disable=missing-function-docstring
    course,
    text,
    thread_id,
    num_children=None,
    group_id=None,
    commentable_id=None,
    thread_list=None
):
    if thread_list:
        return [
            make_mock_thread_data(course=course, text=text, num_children=num_children, **thread)
            for thread in thread_list
        ]
    else:
        return [
            make_mock_thread_data(
                course=course,
                text=text,
                thread_id=thread_id,
                num_children=num_children,
                group_id=group_id,
                commentable_id=commentable_id,
            )
        ]


def make_mock_perform_request_impl(  # pylint: disable=missing-function-docstring
        course,
        text,
        thread_id="dummy_thread_id",
        group_id=None,
        commentable_id=None,
        num_thread_responses=1,
        thread_list=None,
        anonymous=False,
        anonymous_to_peers=False,
):
    def mock_perform_request_impl(*args, **kwargs):
        url = args[1]
        if url.endswith("threads") or url.endswith("user_profile"):
            return {
                "collection": make_mock_collection_data(
                    course, text, thread_id, None, group_id, commentable_id, thread_list
                )
            }
        elif thread_id and url.endswith(thread_id):
            return make_mock_thread_data(
                course=course,
                text=text,
                thread_id=thread_id,
                num_children=num_thread_responses,
                group_id=group_id,
                commentable_id=commentable_id,
                anonymous=anonymous,
                anonymous_to_peers=anonymous_to_peers,
            )
        elif "/users/" in url:
            res = {
                "default_sort_key": "date",
                "upvoted_ids": [],
                "downvoted_ids": [],
                "subscribed_thread_ids": [],
            }
            # comments service adds these attributes when course_id param is present
            if kwargs.get('params', {}).get('course_id'):
                res.update({
                    "threads_count": 1,
                    "comments_count": 2
                })
            return res
        else:
            return None
    return mock_perform_request_impl


def make_mock_request_impl(  # pylint: disable=missing-function-docstring
        course,
        text,
        thread_id="dummy_thread_id",
        group_id=None,
        commentable_id=None,
        num_thread_responses=1,
        thread_list=None,
        anonymous=False,
        anonymous_to_peers=False,
):
    impl = make_mock_perform_request_impl(
        course,
        text,
        thread_id=thread_id,
        group_id=group_id,
        commentable_id=commentable_id,
        num_thread_responses=num_thread_responses,
        thread_list=thread_list,
        anonymous=anonymous,
        anonymous_to_peers=anonymous_to_peers,
    )

    def mock_request_impl(*args, **kwargs):
        data = impl(*args, **kwargs)
        if data:
            return Mock(status_code=200, text=json.dumps(data), json=Mock(return_value=data))
        else:
            return Mock(status_code=404)
    return mock_request_impl


class StringEndsWithMatcher:  # pylint: disable=missing-class-docstring
    def __init__(self, suffix):
        self.suffix = suffix

    def __eq__(self, other):
        return other.endswith(self.suffix)


class PartialDictMatcher:  # pylint: disable=missing-class-docstring
    def __init__(self, expected_values):
        self.expected_values = expected_values

    def __eq__(self, other):
        return all(
            key in other and other[key] == value
            for key, value in self.expected_values.items()
        )


class AllowPlusOrMinusOneInt(int):
    """
    A workaround for the fact that assertNumQueries doesn't let you
    specify a range or any tolerance. An 'int' that is 'equal to' its value,
    but also its value +/- 1
    """

    def __init__(self, value):
        super().__init__()
        self.value = value
        self.values = (value, value - 1, value + 1)

    def __eq__(self, other):
        return other in self.values

    def __repr__(self):
        return f"({self.value} +/- 1)"


@patch('requests.request', autospec=True)
class CommentsServiceRequestHeadersTestCase(UrlResetMixin, ModuleStoreTestCase):  # pylint: disable=missing-class-docstring

    CREATE_USER = False

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment"
        )
        self.mock_get_course_id_by_comment = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        )
        self.mock_get_course_id_by_thread = patcher.start()
        self.addCleanup(patcher.stop)

        username = "foo"
        password = "bar"

        # Invoke UrlResetMixin
        super().setUp()
        self.course = CourseFactory.create(discussion_topics={'dummy discussion': {'id': 'dummy_discussion_id'}})
        self.student = UserFactory.create(username=username, password=password)
        CourseEnrollmentFactory.create(user=self.student, course_id=self.course.id)
        assert self.client.login(username=username, password=password)

        self.addCleanup(translation.deactivate)

    def assert_all_calls_have_header(self, mock_request, key, value):  # pylint: disable=missing-function-docstring
        expected = call(
            ANY,  # method
            ANY,  # url
            data=ANY,
            params=ANY,
            headers=PartialDictMatcher({key: value}),
            timeout=ANY
        )
        for actual in mock_request.call_args_list:
            assert expected == actual

    def test_accept_language(self, mock_request):
        lang = "eo"
        text = "dummy content"
        thread_id = "test_thread_id"
        mock_request.side_effect = make_mock_request_impl(course=self.course, text=text, thread_id=thread_id)

        self.client.get(
            reverse(
                "single_thread",
                kwargs={
                    "course_id": str(self.course.id),
                    "discussion_id": "dummy_discussion_id",
                    "thread_id": thread_id,
                }
            ),
            HTTP_ACCEPT_LANGUAGE=lang,
        )
        self.assert_all_calls_have_header(mock_request, "Accept-Language", lang)

    @override_settings(COMMENTS_SERVICE_KEY="test_api_key")
    def test_api_key(self, mock_request):
        mock_request.side_effect = make_mock_request_impl(course=self.course, text="dummy", thread_id="dummy")

        self.client.get(
            reverse(
                "forum_form_discussion",
                kwargs={"course_id": str(self.course.id)}
            ),
        )
        self.assert_all_calls_have_header(mock_request, "X-Edx-Api-Key", "test_api_key")


class EnrollmentTestCase(ModuleStoreTestCase):
    """
    Tests for the behavior of views depending on if the student is enrolled
    in the course
    """

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.student = UserFactory.create()

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    @patch('openedx.core.djangoapps.django_comment_common.comment_client.utils.requests.request', autospec=True)
    def test_unenrolled(self, mock_request):
        mock_request.side_effect = make_mock_request_impl(course=self.course, text='dummy')
        request = RequestFactory().get('dummy_url')
        request.user = self.student
        with pytest.raises(CourseAccessRedirect):
            views.forum_form_discussion(request, course_id=str(self.course.id))  # pylint: disable=no-value-for-parameter, unexpected-keyword-arg


class DividedDiscussionsTestCase(CohortViewsTestCase):  # pylint: disable=missing-class-docstring

    def create_divided_discussions(self):
        """
        Set up a divided discussion in the system, complete with all the fixings
        """
        divided_inline_discussions = ['Topic A']
        divided_course_wide_discussions = ["Topic B"]
        divided_discussions = divided_inline_discussions + divided_course_wide_discussions

        # inline discussion
        BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id=topic_name_to_id(self.course, "Topic A"),
            discussion_category="Chapter",
            discussion_target="Discussion",
            start=datetime.now()
        )
        # get updated course
        self.course = self.store.get_item(self.course.location)
        # course-wide discussion
        discussion_topics = {
            "Topic B": {"id": "Topic B"},
        }

        config_course_cohorts(
            self.course,
            is_cohorted=True,
        )

        config_course_discussions(
            self.course,
            discussion_topics=discussion_topics,
            divided_discussions=divided_discussions
        )
        return divided_inline_discussions, divided_course_wide_discussions


class CourseDiscussionTopicsTestCase(DividedDiscussionsTestCase):
    """
    Tests the `divide_discussion_topics` view.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def test_non_staff(self):
        """
        Verify that we cannot access divide_discussion_topics if we're a non-staff user.
        """
        self._verify_non_staff_cannot_access(views.discussion_topics, "GET", [str(self.course.id)])

    def test_get_discussion_topics(self):
        """
        Verify that discussion_topics is working for HTTP GET.
        """
        # create inline & course-wide discussion to verify the different map.
        self.create_divided_discussions()

        response = self.get_handler(self.course, handler=views.discussion_topics)
        start_date = response['inline_discussions']['subcategories']['Chapter']['start_date']
        expected_response = {
            "course_wide_discussions": {
                'children': [['Topic B', TYPE_ENTRY]],
                'entries': {
                    'Topic B': {
                        'sort_key': 'A',
                        'is_divided': True,
                        'id': topic_name_to_id(self.course, "Topic B"),
                        'start_date': response['course_wide_discussions']['entries']['Topic B']['start_date']
                    }
                }
            },
            "inline_discussions": {
                'subcategories': {
                    'Chapter': {
                        'subcategories': {},
                        'children': [['Discussion', TYPE_ENTRY]],
                        'entries': {
                            'Discussion': {
                                'sort_key': None,
                                'is_divided': True,
                                'id': topic_name_to_id(self.course, "Topic A"),
                                'start_date': start_date
                            }
                        },
                        'sort_key': 'Chapter',
                        'start_date': start_date
                    }
                },
                'children': [['Chapter', TYPE_SUBCATEGORY]]
            }
        }
        assert response == expected_response


class CourseDiscussionsHandlerTestCase(DividedDiscussionsTestCase):
    """
    Tests the course_discussion_settings_handler
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def get_expected_response(self):
        """
        Returns the static response dict.
        """
        return {
            'always_divide_inline_discussions': False,
            'divided_inline_discussions': [],
            'divided_course_wide_discussions': [],
            'id': 1,
            'division_scheme': 'cohort',
            'available_division_schemes': ['cohort']
        }

    def test_non_staff(self):
        """
        Verify that we cannot access course_discussions_settings_handler if we're a non-staff user.
        """
        self._verify_non_staff_cannot_access(
            course_discussions_settings_handler, "GET", [str(self.course.id)]
        )
        self._verify_non_staff_cannot_access(
            course_discussions_settings_handler, "PATCH", [str(self.course.id)]
        )

    def test_update_always_divide_inline_discussion_settings(self):
        """
        Verify that course_discussions_settings_handler is working for always_divide_inline_discussions via HTTP PATCH.
        """
        config_course_cohorts(self.course, is_cohorted=True)

        response = self.get_handler(self.course, handler=course_discussions_settings_handler)

        expected_response = self.get_expected_response()

        assert response == expected_response

        expected_response['always_divide_inline_discussions'] = True
        response = self.patch_handler(
            self.course, data=expected_response, handler=course_discussions_settings_handler
        )

        assert response == expected_response

    def test_update_course_wide_discussion_settings(self):
        """
        Verify that course_discussions_settings_handler is working for divided_course_wide_discussions via HTTP PATCH.
        """
        # course-wide discussion
        discussion_topics = {
            "Topic B": {"id": "Topic B"},
        }

        config_course_cohorts(self.course, is_cohorted=True)
        config_course_discussions(self.course, discussion_topics=discussion_topics)

        response = self.get_handler(self.course, handler=views.course_discussions_settings_handler)

        expected_response = self.get_expected_response()
        assert response == expected_response

        expected_response['divided_course_wide_discussions'] = [topic_name_to_id(self.course, "Topic B")]
        response = self.patch_handler(
            self.course, data=expected_response, handler=views.course_discussions_settings_handler
        )

        assert response == expected_response

    def test_update_inline_discussion_settings(self):
        """
        Verify that course_discussions_settings_handler is working for divided_inline_discussions via HTTP PATCH.
        """
        config_course_cohorts(self.course, is_cohorted=True)

        response = self.get_handler(self.course, handler=views.course_discussions_settings_handler)

        expected_response = self.get_expected_response()
        assert response == expected_response

        RequestCache.clear_all_namespaces()
        now = datetime.now()
        # inline discussion
        BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id="Topic_A",
            discussion_category="Chapter",
            discussion_target="Discussion",
            start=now
        )

        expected_response['divided_inline_discussions'] = ["Topic_A"]
        response = self.patch_handler(
            self.course, data=expected_response, handler=views.course_discussions_settings_handler
        )

        assert response == expected_response

    def test_get_settings(self):
        """
        Verify that course_discussions_settings_handler is working for HTTP GET.
        """
        divided_inline_discussions, divided_course_wide_discussions = self.create_divided_discussions()

        response = self.get_handler(self.course, handler=views.course_discussions_settings_handler)
        expected_response = self.get_expected_response()

        expected_response['divided_inline_discussions'] = [topic_name_to_id(self.course, name)
                                                           for name in divided_inline_discussions]
        expected_response['divided_course_wide_discussions'] = [topic_name_to_id(self.course, name)
                                                                for name in divided_course_wide_discussions]

        assert response == expected_response

    def test_update_settings_with_invalid_field_data_type(self):
        """
        Verify that course_discussions_settings_handler return HTTP 400 if field data type is incorrect.
        """
        config_course_cohorts(self.course, is_cohorted=True)

        response = self.patch_handler(
            self.course,
            data={'always_divide_inline_discussions': ''},
            expected_response_code=400,
            handler=views.course_discussions_settings_handler
        )
        assert 'Incorrect field type for `{}`. Type must be `{}`'.format(
            'always_divide_inline_discussions',
            bool.__name__
        ) == response.get('error')

    def test_available_schemes(self):
        # Cohorts disabled, single enrollment mode.
        config_course_cohorts(self.course, is_cohorted=False)
        response = self.get_handler(self.course, handler=views.course_discussions_settings_handler)
        expected_response = self.get_expected_response()
        expected_response['available_division_schemes'] = []
        assert response == expected_response

        # Add 2 enrollment modes
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)
        response = self.get_handler(self.course, handler=views.course_discussions_settings_handler)
        expected_response['available_division_schemes'] = [CourseDiscussionSettings.ENROLLMENT_TRACK]
        assert response == expected_response

        # Enable cohorts
        config_course_cohorts(self.course, is_cohorted=True)
        response = self.get_handler(self.course, handler=views.course_discussions_settings_handler)
        expected_response['available_division_schemes'] = [
            CourseDiscussionSettings.COHORT, CourseDiscussionSettings.ENROLLMENT_TRACK
        ]
        assert response == expected_response


class DefaultTopicIdGetterTestCase(ModuleStoreTestCase):
    """
    Tests the `_get_discussion_default_topic_id` helper.
    """

    def test_no_default_topic(self):
        discussion_topics = {
            'dummy discussion': {
                'id': 'dummy_discussion_id',
            },
        }
        course = CourseFactory.create(discussion_topics=discussion_topics)
        expected_id = None
        result = _get_discussion_default_topic_id(course)
        assert expected_id == result

    def test_default_topic_id(self):
        discussion_topics = {
            'dummy discussion': {
                'id': 'dummy_discussion_id',
            },
            'another discussion': {
                'id': 'another_discussion_id',
                'default': True,
            },
        }
        course = CourseFactory.create(discussion_topics=discussion_topics)
        expected_id = 'another_discussion_id'
        result = _get_discussion_default_topic_id(course)
        assert expected_id == result
