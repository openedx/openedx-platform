# pylint: skip-file


import datetime
import json
import unittest
from unittest import mock
from unittest.mock import Mock, patch

import ddt
import pytest
from django.test import RequestFactory, TestCase
from django.urls import reverse
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey
from pytz import UTC

import lms.djangoapps.discussion.django_comment_client.utils as utils
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import (
    AdminFactory,
    CourseEnrollmentFactory,
    InstructorFactory,
    UserFactory
)
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.discussion.django_comment_client.constants import TYPE_ENTRY, TYPE_SUBCATEGORY
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from lms.djangoapps.discussion.django_comment_client.tests.unicode import UnicodeTestMixin
from lms.djangoapps.discussion.django_comment_client.tests.utils import config_course_discussions, topic_name_to_id
from lms.djangoapps.teams.tests.factories import CourseTeamFactory
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.cohorts import set_course_cohorted
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory, config_course_cohorts
from openedx.core.djangoapps.discussions.utils import (
    available_division_schemes,
    get_accessible_discussion_xblocks,
    get_discussion_categories_ids,
    get_group_names_by_id,
    has_required_keys
)
from openedx.core.djangoapps.django_comment_common.comment_client.utils import CommentClientMaintenanceError
from openedx.core.djangoapps.django_comment_common.models import (
    CourseDiscussionSettings,
    DiscussionsIdMapping,
    assign_role
)
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.djangoapps.util.testing import ContentGroupTestCase
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, ToyCourseFactory


class DictionaryTestCase(TestCase):
    def test_extract(self):
        d = {'cats': 'meow', 'dogs': 'woof'}
        k = ['cats', 'dogs', 'hamsters']
        expected = {'cats': 'meow', 'dogs': 'woof', 'hamsters': None}
        assert utils.extract(d, k) == expected

    def test_strip_none(self):
        d = {'cats': 'meow', 'dogs': 'woof', 'hamsters': None}
        expected = {'cats': 'meow', 'dogs': 'woof'}
        assert utils.strip_none(d) == expected

    def test_strip_blank(self):
        d = {'cats': 'meow', 'dogs': 'woof', 'hamsters': ' ', 'yetis': ''}
        expected = {'cats': 'meow', 'dogs': 'woof'}
        assert utils.strip_blank(d) == expected


class AccessUtilsTestCase(ModuleStoreTestCase):
    """
    Base testcase class for access and roles for the
    comment client service integration
    """
    CREATE_USER = False

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()
        self.course_id = self.course.id
        self.student_role = RoleFactory(name='Student', course_id=self.course_id)
        self.moderator_role = RoleFactory(name='Moderator', course_id=self.course_id)
        self.community_ta_role = RoleFactory(name='Community TA', course_id=self.course_id)
        self.student1 = UserFactory(username='student', email='student@edx.org')
        self.student1_enrollment = CourseEnrollmentFactory(user=self.student1)
        self.student_role.users.add(self.student1)
        self.student2 = UserFactory(username='student2', email='student2@edx.org')
        self.student2_enrollment = CourseEnrollmentFactory(user=self.student2)
        self.moderator = UserFactory(username='moderator', email='staff@edx.org', is_staff=True)
        self.moderator_enrollment = CourseEnrollmentFactory(user=self.moderator)
        self.moderator_role.users.add(self.moderator)
        self.community_ta1 = UserFactory(username='community_ta1', email='community_ta1@edx.org')
        self.community_ta_role.users.add(self.community_ta1)
        self.community_ta2 = UserFactory(username='community_ta2', email='community_ta2@edx.org')
        self.community_ta_role.users.add(self.community_ta2)
        self.course_staff = UserFactory(username='course_staff', email='course_staff@edx.org')
        CourseStaffRole(self.course_id).add_users(self.course_staff)

    def test_get_role_ids(self):
        ret = utils.get_role_ids(self.course_id)
        expected = {'Moderator': [3], 'Community TA': [4, 5]}
        assert ret == expected

    def test_has_discussion_privileges(self):
        assert not utils.has_discussion_privileges(self.student1, self.course_id)
        assert not utils.has_discussion_privileges(self.student2, self.course_id)
        assert not utils.has_discussion_privileges(self.course_staff, self.course_id)
        assert utils.has_discussion_privileges(self.moderator, self.course_id)
        assert utils.has_discussion_privileges(self.community_ta1, self.course_id)
        assert utils.has_discussion_privileges(self.community_ta2, self.course_id)

    def test_has_forum_access(self):
        ret = utils.has_forum_access('student', self.course_id, 'Student')
        assert ret

        ret = utils.has_forum_access('not_a_student', self.course_id, 'Student')
        assert not ret

        ret = utils.has_forum_access('student', self.course_id, 'NotARole')
        assert not ret


@ddt.ddt
class CoursewareContextTestCase(ModuleStoreTestCase):
    """
    Base testcase class for courseware context for the
    comment client service integration
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org="TestX", number="101", display_name="Test Course")
        self.discussion1 = BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id="discussion1",
            discussion_category="Chapter",
            discussion_target="Discussion 1"
        )
        self.discussion2 = BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id="discussion2",
            discussion_category="Chapter / Section / Subsection",
            discussion_target="Discussion 2"
        )

    def test_empty(self):
        utils.add_courseware_context([], self.course, self.user)

    def test_missing_commentable_id(self):
        orig = {"commentable_id": "non-inline"}
        modified = dict(orig)
        utils.add_courseware_context([modified], self.course, self.user)
        assert modified == orig

    def test_basic(self):
        threads = [
            {"commentable_id": self.discussion1.discussion_id},
            {"commentable_id": self.discussion2.discussion_id}
        ]
        utils.add_courseware_context(threads, self.course, self.user)

        def assertThreadCorrect(thread, discussion, expected_title):  # pylint: disable=invalid-name
            """Asserts that the given thread has the expected set of properties"""
            assert set(thread.keys()) == {'commentable_id', 'courseware_url', 'courseware_title'}
            assert thread.get('courseware_url') == reverse('jump_to', kwargs={'course_id': str(self.course.id),
                                                                              'location': str(discussion.location)})
            assert thread.get('courseware_title') == expected_title

        assertThreadCorrect(threads[0], self.discussion1, "Chapter / Discussion 1")
        assertThreadCorrect(threads[1], self.discussion2, "Subsection / Discussion 2")

    def test_empty_discussion_subcategory_title(self):
        """
        Test that for empty subcategory inline discussion blocks,
        the divider " / " is not rendered on a post or inline discussion topic label.
        """
        discussion = BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id="discussion",
            discussion_category="Chapter",
            discussion_target=""  # discussion-subcategory
        )
        thread = {"commentable_id": discussion.discussion_id}
        utils.add_courseware_context([thread], self.course, self.user)
        assert '/' not in thread.get('courseware_title')

    def test_get_accessible_discussion_xblocks(self):
        """
        Tests that the accessible discussion xblocks having no parents do not get fetched for split modulestore.
        """
        course = CourseFactory.create()

        # Create a discussion xblock.
        test_discussion = self.store.create_child(self.user.id, course.location, 'discussion', 'test_discussion')

        # Assert that created discussion xblock is not an orphan.
        assert test_discussion.location not in self.store.get_orphans(course.id)

        # Assert that there is only one discussion xblock in the course at the moment.
        assert len(get_accessible_discussion_xblocks(course, self.user)) == 1

        # The above call is request cached, so we need to clear it for this test.
        RequestCache.clear_all_namespaces()
        # Add an orphan discussion xblock to that course
        orphan = course.id.make_usage_key('discussion', 'orphan_discussion')
        self.store.create_item(self.user.id, orphan.course_key, orphan.block_type, block_id=orphan.block_id)

        # Assert that the discussion xblock is an orphan.
        assert orphan in self.store.get_orphans(course.id)

        assert len(get_accessible_discussion_xblocks(course, self.user)) == 1


class CachedDiscussionIdMapTestCase(ModuleStoreTestCase):
    """
    Tests that using the cache of discussion id mappings has the same behavior as searching through the course.
    """
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(org='TestX', number='101', display_name='Test Course')
        self.discussion = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='test_discussion_id',
            discussion_category='Chapter',
            discussion_target='Discussion 1'
        )
        self.discussion2 = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='test_discussion_id_2',
            discussion_category='Chapter 2',
            discussion_target='Discussion 2'
        )
        self.private_discussion = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='private_discussion_id',
            discussion_category='Chapter 3',
            discussion_target='Beta Testing',
            visible_to_staff_only=True
        )
        RequestCache.clear_all_namespaces()  # clear the cache before the last course publish
        self.bad_discussion = BlockFactory.create(
            parent_location=self.course.location,
            category='discussion',
            discussion_id='bad_discussion_id',
            discussion_category=None,
            discussion_target=None
        )

    def test_cache_returns_correct_key(self):
        usage_key = utils.get_cached_discussion_key(self.course.id, 'test_discussion_id')
        assert usage_key == self.discussion.location

    def test_cache_returns_none_if_id_is_not_present(self):
        usage_key = utils.get_cached_discussion_key(self.course.id, 'bogus_id')
        assert usage_key is None

    def test_cache_raises_exception_if_discussion_id_map_not_cached(self):
        DiscussionsIdMapping.objects.all().delete()
        with pytest.raises(utils.DiscussionIdMapIsNotCached):
            utils.get_cached_discussion_key(self.course.id, 'test_discussion_id')

    def test_cache_raises_exception_if_discussion_id_not_cached(self):
        cache = DiscussionsIdMapping.objects.get(course_id=self.course.id)
        cache.mapping = None
        cache.save()

        with pytest.raises(utils.DiscussionIdMapIsNotCached):
            utils.get_cached_discussion_key(self.course.id, 'test_discussion_id')

    def test_xblock_does_not_have_required_keys(self):
        assert has_required_keys(self.discussion)
        assert not has_required_keys(self.bad_discussion)

    def verify_discussion_metadata(self):
        """Retrieves the metadata for self.discussion and self.discussion2 and verifies that it is correct"""
        metadata = utils.get_cached_discussion_id_map(
            self.course,
            ['test_discussion_id', 'test_discussion_id_2'],
            self.user
        )
        discussion1 = metadata[self.discussion.discussion_id]
        discussion2 = metadata[self.discussion2.discussion_id]
        assert discussion1['location'] == self.discussion.location
        assert discussion1['title'] == 'Chapter / Discussion 1'
        assert discussion2['location'] == self.discussion2.location
        assert discussion2['title'] == 'Chapter 2 / Discussion 2'

    def test_get_discussion_id_map_from_cache(self):
        self.verify_discussion_metadata()

    def test_get_discussion_id_map_without_cache(self):
        DiscussionsIdMapping.objects.all().delete()
        self.verify_discussion_metadata()

    def test_get_missing_discussion_id_map_from_cache(self):
        metadata = utils.get_cached_discussion_id_map(self.course, ['bogus_id'], self.user)
        assert metadata == {}

    def test_get_discussion_id_map_from_cache_without_access(self):
        user = UserFactory.create()

        metadata = utils.get_cached_discussion_id_map(self.course, ['private_discussion_id'], self.user)
        assert metadata['private_discussion_id']['title'] == 'Chapter 3 / Beta Testing'

        metadata = utils.get_cached_discussion_id_map(self.course, ['private_discussion_id'], user)
        assert metadata == {}

    def test_get_bad_discussion_id(self):
        metadata = utils.get_cached_discussion_id_map(self.course, ['bad_discussion_id'], self.user)
        assert metadata == {}

    def test_discussion_id_accessible(self):
        assert utils.discussion_category_id_access(self.course, self.user, 'test_discussion_id')

    def test_bad_discussion_id_not_accessible(self):
        assert not utils.discussion_category_id_access(self.course, self.user, 'bad_discussion_id')

    def test_missing_discussion_id_not_accessible(self):
        assert not utils.discussion_category_id_access(self.course, self.user, 'bogus_id')

    def test_discussion_id_not_accessible_without_access(self):
        user = UserFactory.create()
        assert utils.discussion_category_id_access(self.course, self.user, 'private_discussion_id')
        assert not utils.discussion_category_id_access(self.course, user, 'private_discussion_id')


class CategoryMapTestMixin:
    """
    Provides functionality for classes that test
    `get_discussion_category_map`.
    """

    def assert_category_map_equals(self, expected, requesting_user=None):
        """
        Call `get_discussion_category_map`, and verify that it returns
        what is expected.
        """
        actual = utils.get_discussion_category_map(self.course, requesting_user or self.user)
        actual['subcategories']['Week 1']['children'].sort()
        assert actual == expected


class CategoryMapTestCase(CategoryMapTestMixin, ModuleStoreTestCase):
    """
    Base testcase class for discussion categories for the
    comment client service integration
    """

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(
            org="TestX", number="101", display_name="Test Course",
            # This test needs to use a course that has already started --
            # discussion topics only show up if the course has already started,
            # and the default start date for courses is Jan 1, 2030.
            start=datetime.datetime(2012, 2, 3, tzinfo=UTC)
        )
        # Courses get a default discussion topic on creation, so remove it
        self.course.discussion_topics = {}
        self.discussion_num = 0
        self.instructor = InstructorFactory(course_key=self.course.id)
        self.maxDiff = None  # pylint: disable=invalid-name
        self.later = datetime.datetime(2050, 1, 1, tzinfo=UTC)

    def create_discussion(self, discussion_category, discussion_target, **kwargs):
        self.discussion_num += 1
        return BlockFactory.create(
            parent_location=self.course.location,
            category="discussion",
            discussion_id=f"discussion{self.discussion_num}",
            discussion_category=discussion_category,
            discussion_target=discussion_target,
            **kwargs
        )

    def assert_category_map_equals(self, expected, divided_only_if_explicit=False,
                                   exclude_unstarted=True):  # pylint: disable=arguments-differ
        """
        Asserts the expected map with the map returned by get_discussion_category_map method.
        """
        assert utils.get_discussion_category_map(self.course, self.instructor, divided_only_if_explicit,
                                                 exclude_unstarted) == expected

    def test_empty(self):
        self.assert_category_map_equals({"entries": {}, "subcategories": {}, "children": []})

    def test_configured_topics(self):
        self.course.discussion_topics = {
            "Topic A": {"id": "Topic_A"},
            "Topic B": {"id": "Topic_B"},
            "Topic C": {"id": "Topic_C"}
        }

        def check_cohorted_topics(expected_ids):
            self.assert_category_map_equals(
                {
                    "entries": {
                        "Topic A": {"id": "Topic_A", "sort_key": "Topic A", "is_divided": "Topic_A" in expected_ids},
                        "Topic B": {"id": "Topic_B", "sort_key": "Topic B", "is_divided": "Topic_B" in expected_ids},
                        "Topic C": {"id": "Topic_C", "sort_key": "Topic C", "is_divided": "Topic_C" in expected_ids},
                    },
                    "subcategories": {},
                    "children": [("Topic A", TYPE_ENTRY), ("Topic B", TYPE_ENTRY), ("Topic C", TYPE_ENTRY)]
                }
            )

        check_cohorted_topics([])  # default (empty) cohort config

        set_discussion_division_settings(self.course.id, enable_cohorts=False)
        check_cohorted_topics([])

        set_discussion_division_settings(self.course.id, enable_cohorts=True)
        check_cohorted_topics([])

        set_discussion_division_settings(
            self.course.id,
            enable_cohorts=True,
            divided_discussions=["Topic_B", "Topic_C"]
        )
        check_cohorted_topics(["Topic_B", "Topic_C"])

        set_discussion_division_settings(
            self.course.id,
            enable_cohorts=True,
            divided_discussions=["Topic_A", "Some_Other_Topic"]
        )
        check_cohorted_topics(["Topic_A"])

        # unlikely case, but make sure it works.
        set_discussion_division_settings(
            self.course.id,
            enable_cohorts=False,
            divided_discussions=["Topic_A"]
        )
        check_cohorted_topics([])

    def test_single_inline(self):
        self.create_discussion("Chapter", "Discussion")
        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter": {
                        "entries": {
                            "Discussion": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion", TYPE_ENTRY)]
                    }
                },
                "children": [("Chapter", TYPE_SUBCATEGORY)]
            }
        )

    def test_inline_with_always_divide_inline_discussion_flag(self):
        self.create_discussion("Chapter", "Discussion")
        set_discussion_division_settings(self.course.id, enable_cohorts=True, always_divide_inline_discussions=True)

        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter": {
                        "entries": {
                            "Discussion": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": True,
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion", TYPE_ENTRY)]
                    }
                },
                "children": [("Chapter", TYPE_SUBCATEGORY)]
            }
        )

    def test_inline_without_always_divide_inline_discussion_flag(self):
        self.create_discussion("Chapter", "Discussion")
        set_discussion_division_settings(self.course.id, enable_cohorts=True)

        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter": {
                        "entries": {
                            "Discussion": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion", TYPE_ENTRY)]
                    }
                },
                "children": [("Chapter", TYPE_SUBCATEGORY)]
            },
            divided_only_if_explicit=True
        )

    def test_get_unstarted_discussion_xblocks(self):
        self.create_discussion("Chapter 1", "Discussion 1", start=self.later)

        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter 1": {
                        "entries": {
                            "Discussion 1": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": False,
                                "start_date": self.later
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion 1", TYPE_ENTRY)],
                        "start_date": self.later,
                        "sort_key": "Chapter 1"
                    }
                },
                "children": [("Chapter 1", TYPE_SUBCATEGORY)]
            },
            divided_only_if_explicit=True,
            exclude_unstarted=False
        )

    def test_tree(self):
        self.create_discussion("Chapter 1", "Discussion 1")
        self.create_discussion("Chapter 1", "Discussion 2")
        self.create_discussion("Chapter 2", "Discussion")
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion")
        self.create_discussion("Chapter 2 / Section 1 / Subsection 2", "Discussion")
        self.create_discussion("Chapter 3 / Section 1", "Discussion")

        def check_divided(is_divided):
            self.assert_category_map_equals(
                {
                    "entries": {},
                    "subcategories": {
                        "Chapter 1": {
                            "entries": {
                                "Discussion 1": {
                                    "id": "discussion1",
                                    "sort_key": None,
                                    "is_divided": is_divided,
                                },
                                "Discussion 2": {
                                    "id": "discussion2",
                                    "sort_key": None,
                                    "is_divided": is_divided,
                                }
                            },
                            "subcategories": {},
                            "children": [("Discussion 1", TYPE_ENTRY), ("Discussion 2", TYPE_ENTRY)]
                        },
                        "Chapter 2": {
                            "entries": {
                                "Discussion": {
                                    "id": "discussion3",
                                    "sort_key": None,
                                    "is_divided": is_divided,
                                }
                            },
                            "subcategories": {
                                "Section 1": {
                                    "entries": {},
                                    "subcategories": {
                                        "Subsection 1": {
                                            "entries": {
                                                "Discussion": {
                                                    "id": "discussion4",
                                                    "sort_key": None,
                                                    "is_divided": is_divided,
                                                }
                                            },
                                            "subcategories": {},
                                            "children": [("Discussion", TYPE_ENTRY)]
                                        },
                                        "Subsection 2": {
                                            "entries": {
                                                "Discussion": {
                                                    "id": "discussion5",
                                                    "sort_key": None,
                                                    "is_divided": is_divided,
                                                }
                                            },
                                            "subcategories": {},
                                            "children": [("Discussion", TYPE_ENTRY)]
                                        }
                                    },
                                    "children": [("Subsection 1", TYPE_SUBCATEGORY), ("Subsection 2", TYPE_SUBCATEGORY)]
                                }
                            },
                            "children": [("Discussion", TYPE_ENTRY), ("Section 1", TYPE_SUBCATEGORY)]
                        },
                        "Chapter 3": {
                            "entries": {},
                            "subcategories": {
                                "Section 1": {
                                    "entries": {
                                        "Discussion": {
                                            "id": "discussion6",
                                            "sort_key": None,
                                            "is_divided": is_divided,
                                        }
                                    },
                                    "subcategories": {},
                                    "children": [("Discussion", TYPE_ENTRY)]
                                }
                            },
                            "children": [("Section 1", TYPE_SUBCATEGORY)]
                        }
                    },
                    "children": [("Chapter 1", TYPE_SUBCATEGORY), ("Chapter 2", TYPE_SUBCATEGORY),
                                 ("Chapter 3", TYPE_SUBCATEGORY)]
                }
            )

        # empty / default config
        check_divided(False)

        # explicitly disabled cohorting
        set_discussion_division_settings(self.course.id, enable_cohorts=False)
        check_divided(False)

        # explicitly enable courses divided by Cohort with inline discusssions also divided.
        set_discussion_division_settings(self.course.id, enable_cohorts=True, always_divide_inline_discussions=True)
        check_divided(True)

    def test_tree_with_duplicate_targets(self):
        self.create_discussion("Chapter 1", "Discussion A")
        self.create_discussion("Chapter 1", "Discussion B")
        self.create_discussion("Chapter 1", "Discussion A")  # duplicate
        self.create_discussion("Chapter 1", "Discussion A")  # another duplicate
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion")
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion")  # duplicate

        category_map = utils.get_discussion_category_map(self.course, self.user)

        chapter1 = category_map["subcategories"]["Chapter 1"]
        chapter1_discussions = {"Discussion A", "Discussion B", "Discussion A (1)", "Discussion A (2)"}
        chapter1_discussions_with_types = {("Discussion A", TYPE_ENTRY), ("Discussion B", TYPE_ENTRY),
                                           ("Discussion A (1)", TYPE_ENTRY), ("Discussion A (2)", TYPE_ENTRY)}
        assert set(chapter1['children']) == chapter1_discussions_with_types
        assert set(chapter1['entries'].keys()) == chapter1_discussions

        chapter2 = category_map["subcategories"]["Chapter 2"]
        subsection1 = chapter2["subcategories"]["Section 1"]["subcategories"]["Subsection 1"]
        subsection1_discussions = {"Discussion", "Discussion (1)"}
        subsection1_discussions_with_types = {("Discussion", TYPE_ENTRY), ("Discussion (1)", TYPE_ENTRY)}
        assert set(subsection1['children']) == subsection1_discussions_with_types
        assert set(subsection1['entries'].keys()) == subsection1_discussions

    def test_start_date_filter(self):
        now = datetime.datetime.now()
        self.create_discussion("Chapter 1", "Discussion 1", start=now)
        self.create_discussion("Chapter 1", "Discussion 2 обсуждение", start=self.later)
        self.create_discussion("Chapter 2", "Discussion", start=now)
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion", start=self.later)
        self.create_discussion("Chapter 2 / Section 1 / Subsection 2", "Discussion", start=self.later)
        self.create_discussion("Chapter 3 / Section 1", "Discussion", start=self.later)

        assert not self.course.self_paced
        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter 1": {
                        "entries": {
                            "Discussion 1": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion 1", TYPE_ENTRY)]
                    },
                    "Chapter 2": {
                        "entries": {
                            "Discussion": {
                                "id": "discussion3",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion", TYPE_ENTRY)]
                    }
                },
                "children": [("Chapter 1", TYPE_SUBCATEGORY), ("Chapter 2", TYPE_SUBCATEGORY)]
            }
        )

    def test_self_paced_start_date_filter(self):
        self.course.self_paced = True

        now = datetime.datetime.now()
        self.create_discussion("Chapter 1", "Discussion 1", start=now)
        self.create_discussion("Chapter 1", "Discussion 2", start=self.later)
        self.create_discussion("Chapter 2", "Discussion", start=now)
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion", start=self.later)
        self.create_discussion("Chapter 2 / Section 1 / Subsection 2", "Discussion", start=self.later)
        self.create_discussion("Chapter 3 / Section 1", "Discussion", start=self.later)

        assert self.course.self_paced
        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter 1": {
                        "entries": {
                            "Discussion 1": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": False,
                            },
                            "Discussion 2": {
                                "id": "discussion2",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                        "children": [("Discussion 1", TYPE_ENTRY), ("Discussion 2", TYPE_ENTRY)]
                    },
                    "Chapter 2": {
                        "entries": {
                            "Discussion": {
                                "id": "discussion3",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {
                            "Section 1": {
                                "entries": {},
                                "subcategories": {
                                    "Subsection 1": {
                                        "entries": {
                                            "Discussion": {
                                                "id": "discussion4",
                                                "sort_key": None,
                                                "is_divided": False,
                                            }
                                        },
                                        "subcategories": {},
                                        "children": [("Discussion", TYPE_ENTRY)]
                                    },
                                    "Subsection 2": {
                                        "entries": {
                                            "Discussion": {
                                                "id": "discussion5",
                                                "sort_key": None,
                                                "is_divided": False,
                                            }
                                        },
                                        "subcategories": {},
                                        "children": [("Discussion", TYPE_ENTRY)]
                                    }
                                },
                                "children": [("Subsection 1", TYPE_SUBCATEGORY), ("Subsection 2", TYPE_SUBCATEGORY)]
                            }
                        },
                        "children": [("Discussion", TYPE_ENTRY), ("Section 1", TYPE_SUBCATEGORY)]
                    },
                    "Chapter 3": {
                        "entries": {},
                        "subcategories": {
                            "Section 1": {
                                "entries": {
                                    "Discussion": {
                                        "id": "discussion6",
                                        "sort_key": None,
                                        "is_divided": False,
                                    }
                                },
                                "subcategories": {},
                                "children": [("Discussion", TYPE_ENTRY)]
                            }
                        },
                        "children": [("Section 1", TYPE_SUBCATEGORY)]
                    }
                },
                "children": [("Chapter 1", TYPE_SUBCATEGORY), ("Chapter 2", TYPE_SUBCATEGORY),
                             ("Chapter 3", TYPE_SUBCATEGORY)]
            }
        )

    def test_sort_inline_explicit(self):
        self.create_discussion("Chapter", "Discussion 1", sort_key="D")
        self.create_discussion("Chapter", "Discussion 2", sort_key="A")
        self.create_discussion("Chapter", "Discussion 3", sort_key="E")
        self.create_discussion("Chapter", "Discussion 4", sort_key="C")
        self.create_discussion("Chapter", "Discussion 5", sort_key="B")

        self.assert_category_map_equals(
            {
                "entries": {},
                "subcategories": {
                    "Chapter": {
                        "entries": {
                            "Discussion 1": {
                                "id": "discussion1",
                                "sort_key": "D",
                                "is_divided": False,
                            },
                            "Discussion 2": {
                                "id": "discussion2",
                                "sort_key": "A",
                                "is_divided": False,
                            },
                            "Discussion 3": {
                                "id": "discussion3",
                                "sort_key": "E",
                                "is_divided": False,
                            },
                            "Discussion 4": {
                                "id": "discussion4",
                                "sort_key": "C",
                                "is_divided": False,
                            },
                            "Discussion 5": {
                                "id": "discussion5",
                                "sort_key": "B",
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                        "children": [
                            ("Discussion 2", TYPE_ENTRY),
                            ("Discussion 5", TYPE_ENTRY),
                            ("Discussion 4", TYPE_ENTRY),
                            ("Discussion 1", TYPE_ENTRY),
                            ("Discussion 3", TYPE_ENTRY)
                        ]
                    }
                },
                "children": [("Chapter", TYPE_SUBCATEGORY)]
            }
        )

    def test_sort_configured_topics_explicit(self):
        self.course.discussion_topics = {
            "Topic A": {"id": "Topic_A", "sort_key": "B"},
            "Topic B": {"id": "Topic_B", "sort_key": "C"},
            "Topic C": {"id": "Topic_C", "sort_key": "A"}
        }
        self.assert_category_map_equals(
            {
                "entries": {
                    "Topic A": {"id": "Topic_A", "sort_key": "B", "is_divided": False},
                    "Topic B": {"id": "Topic_B", "sort_key": "C", "is_divided": False},
                    "Topic C": {"id": "Topic_C", "sort_key": "A", "is_divided": False},
                },
                "subcategories": {},
                "children": [("Topic C", TYPE_ENTRY), ("Topic A", TYPE_ENTRY), ("Topic B", TYPE_ENTRY)]
            }
        )

    def test_sort_intermediates(self):
        self.create_discussion("Chapter B", "Discussion 2")
        self.create_discussion("Chapter C", "Discussion")
        self.create_discussion("Chapter A", "Discussion 1")
        self.create_discussion("Chapter B", "Discussion 1")
        self.create_discussion("Chapter A", "Discussion 2")

        self.assert_category_map_equals(
            {
                "children": [("Chapter A", TYPE_SUBCATEGORY), ("Chapter B", TYPE_SUBCATEGORY),
                             ("Chapter C", TYPE_SUBCATEGORY)],
                "entries": {},
                "subcategories": {
                    "Chapter A": {
                        "children": [("Discussion 1", TYPE_ENTRY), ("Discussion 2", TYPE_ENTRY)],
                        "entries": {
                            "Discussion 1": {
                                "id": "discussion3",
                                "sort_key": None,
                                "is_divided": False,
                            },
                            "Discussion 2": {
                                "id": "discussion5",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                    },
                    "Chapter B": {
                        "children": [("Discussion 2", TYPE_ENTRY), ("Discussion 1", TYPE_ENTRY)],
                        "entries": {
                            "Discussion 2": {
                                "id": "discussion1",
                                "sort_key": None,
                                "is_divided": False,
                            },
                            "Discussion 1": {
                                "id": "discussion4",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "subcategories": {},
                    },
                    "Chapter C": {
                        "entries": {
                            "Discussion": {
                                "id": "discussion2",
                                "sort_key": None,
                                "is_divided": False,
                            }
                        },
                        "children": [("Discussion", TYPE_ENTRY)],
                        "subcategories": {},
                    }
                },
            }
        )

    def test_ids_empty(self):
        assert get_discussion_categories_ids(self.course, self.user) == []

    def test_ids_configured_topics(self):
        self.course.discussion_topics = {
            "Topic A": {"id": "Topic_A"},
            "Topic B": {"id": "Topic_B"},
            "Topic C": {"id": "Topic_C"}
        }
        assert len(get_discussion_categories_ids(self.course, self.user)) == len(["Topic_A", "Topic_B", "Topic_C"])

    def test_ids_inline(self):
        self.create_discussion("Chapter 1", "Discussion 1")
        self.create_discussion("Chapter 1", "Discussion 2")
        self.create_discussion("Chapter 2", "Discussion")
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion")
        self.create_discussion("Chapter 2 / Section 1 / Subsection 2", "Discussion")
        self.create_discussion("Chapter 3 / Section 1", "Discussion")
        assert len(get_discussion_categories_ids(self.course, self.user)) == \
               len(["discussion1", "discussion2", "discussion3", "discussion4", "discussion5", "discussion6"])

    def test_ids_mixed(self):
        self.course.discussion_topics = {
            "Topic A": {"id": "Topic_A"},
            "Topic B": {"id": "Topic_B"},
            "Topic C": {"id": "Topic_C"}
        }
        self.create_discussion("Chapter 1", "Discussion 1")
        self.create_discussion("Chapter 2", "Discussion")
        self.create_discussion("Chapter 2 / Section 1 / Subsection 1", "Discussion")
        assert len(get_discussion_categories_ids(self.course, self.user)) == \
               len(["Topic_A", "Topic_B", "Topic_C", "discussion1", "discussion2", "discussion3"])


class ContentGroupCategoryMapTestCase(CategoryMapTestMixin, ContentGroupTestCase):
    """
    Tests `get_discussion_category_map` on discussion xblocks which are
    only visible to some content groups.
    """

    def test_staff_user(self):
        """
        Verify that the staff user can access the alpha, beta, and
        global discussion topics.
        """
        self.assert_category_map_equals(
            {
                'subcategories': {
                    'Week 1': {
                        'subcategories': {},
                        'children': [
                            ('Visible to Alpha', 'entry'),
                            ('Visible to Beta', 'entry'),
                            ('Visible to Everyone', 'entry')
                        ],
                        'entries': {
                            'Visible to Alpha': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'alpha_group_discussion'
                            },
                            'Visible to Beta': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'beta_group_discussion'
                            },
                            'Visible to Everyone': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'global_group_discussion'
                            }
                        }
                    }
                },
                'children': [('General', 'entry'), ('Week 1', 'subcategory')],
                'entries': {
                    'General': {
                        'sort_key': 'General',
                        'is_divided': False,
                        'id': 'course'
                    }
                }
            },
            requesting_user=self.staff_user
        )

    def test_alpha_user(self):
        """
        Verify that the alpha user can access the alpha and global
        discussion topics.
        """
        self.assert_category_map_equals(
            {
                'subcategories': {
                    'Week 1': {
                        'subcategories': {},
                        'children': [
                            ('Visible to Alpha', 'entry'),
                            ('Visible to Everyone', 'entry')
                        ],
                        'entries': {
                            'Visible to Alpha': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'alpha_group_discussion'
                            },
                            'Visible to Everyone': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'global_group_discussion'
                            }
                        }
                    }
                },
                'children': [('General', 'entry'), ('Week 1', 'subcategory')],
                'entries': {
                    'General': {
                        'sort_key': 'General',
                        'is_divided': False,
                        'id': 'course'
                    }
                }
            },
            requesting_user=self.alpha_user
        )

    def test_beta_user(self):
        """
        Verify that the beta user can access the beta and global
        discussion topics.
        """
        self.assert_category_map_equals(
            {
                'subcategories': {
                    'Week 1': {
                        'subcategories': {},
                        'children': [('Visible to Beta', 'entry'), ('Visible to Everyone', 'entry')],
                        'entries': {
                            'Visible to Beta': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'beta_group_discussion'
                            },
                            'Visible to Everyone': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'global_group_discussion'
                            }
                        }
                    }
                },
                'children': [('General', 'entry'), ('Week 1', 'subcategory')],
                'entries': {
                    'General': {
                        'sort_key': 'General',
                        'is_divided': False,
                        'id': 'course'
                    }
                }
            },
            requesting_user=self.beta_user
        )

    def test_non_cohorted_user(self):
        """
        Verify that the non-cohorted user can access the global
        discussion topic.
        """
        self.assert_category_map_equals(
            {
                'subcategories': {
                    'Week 1': {
                        'subcategories': {},
                        'children': [
                            ('Visible to Everyone', 'entry')
                        ],
                        'entries': {
                            'Visible to Everyone': {
                                'sort_key': None,
                                'is_divided': False,
                                'id': 'global_group_discussion'
                            }
                        }
                    }
                },
                'children': [('General', 'entry'), ('Week 1', 'subcategory')],
                'entries': {
                    'General': {
                        'sort_key': 'General',
                        'is_divided': False,
                        'id': 'course'
                    }
                }
            },
            requesting_user=self.non_cohorted_user
        )


class JsonResponseTestCase(TestCase, UnicodeTestMixin):
    def _test_unicode_data(self, text):
        response = utils.JsonResponse(text)
        reparsed = json.loads(response.content.decode('utf-8'))
        assert reparsed == text


@ddt.ddt
class DiscussionTabTestCase(ModuleStoreTestCase):
    """ Test visibility of the discussion tab. """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.enrolled_user = UserFactory.create()
        self.staff_user = AdminFactory.create()
        CourseEnrollmentFactory.create(user=self.enrolled_user, course_id=self.course.id)
        self.unenrolled_user = UserFactory.create()

    def discussion_tab_present(self, user):
        """ Returns true if the user has access to the discussion tab. """
        request = RequestFactory().request()
        all_tabs = get_course_tab_list(user, self.course)
        return any(tab.type == 'discussion' for tab in all_tabs)

    def test_tab_access(self):
        with self.settings(FEATURES={'ENABLE_DISCUSSION_SERVICE': True}):
            assert self.discussion_tab_present(self.staff_user)
            assert self.discussion_tab_present(self.enrolled_user)
            assert not self.discussion_tab_present(self.unenrolled_user)

    @mock.patch('lms.djangoapps.ccx.overrides.get_current_ccx')
    def test_tab_settings(self, mock_get_ccx):
        mock_get_ccx.return_value = True
        with self.settings(FEATURES={'ENABLE_DISCUSSION_SERVICE': False}):
            assert not self.discussion_tab_present(self.enrolled_user)

        with self.settings(FEATURES={'CUSTOM_COURSES_EDX': True}):
            assert not self.discussion_tab_present(self.enrolled_user)


class IsCommentableDividedTestCase(ModuleStoreTestCase):
    """
    Test the is_commentable_divided function.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Make sure that course is reloaded every time--clear out the modulestore.
        """
        super().setUp()
        self.toy_course_key = ToyCourseFactory.create().id

    def test_is_commentable_divided(self):
        course = modulestore().get_course(self.toy_course_key)
        assert not cohorts.is_course_cohorted(course.id)

        def to_id(name):
            """Helper for topic_name_to_id that uses course."""
            return topic_name_to_id(course, name)

        # no topics
        assert not utils.is_commentable_divided(course.id,
                                                to_id('General')), "Course doesn't even have a 'General' topic"

        # not cohorted
        config_course_cohorts(course, is_cohorted=False)
        config_course_discussions(course, discussion_topics=["General", "Feedback"])
        assert not utils.is_commentable_divided(course.id, to_id('General')), "Course isn't cohorted"

        # cohorted, but top level topics aren't
        config_course_cohorts(course, is_cohorted=True)
        config_course_discussions(course, discussion_topics=["General", "Feedback"])

        assert cohorts.is_course_cohorted(course.id)
        assert not utils.is_commentable_divided(course.id, to_id('General')), "Course is cohorted, but 'General' isn't."

        # cohorted, including "Feedback" top-level topics aren't
        config_course_cohorts(
            course,
            is_cohorted=True
        )
        config_course_discussions(course, discussion_topics=["General", "Feedback"], divided_discussions=["Feedback"])

        assert cohorts.is_course_cohorted(course.id)
        assert not utils.is_commentable_divided(course.id, to_id('General')), "Course is cohorted, but 'General' isn't."
        assert utils.is_commentable_divided(course.id,
                                            to_id('Feedback')), 'Feedback was listed as cohorted.  Should be.'

    def test_is_commentable_divided_inline_discussion(self):
        course = modulestore().get_course(self.toy_course_key)
        assert not cohorts.is_course_cohorted(course.id)

        def to_id(name):
            return topic_name_to_id(course, name)

        config_course_cohorts(
            course,
            is_cohorted=True,
        )
        config_course_discussions(
            course,
            discussion_topics=["General", "Feedback"],
            divided_discussions=["Feedback", "random_inline"]
        )

        assert not utils.is_commentable_divided(course.id, to_id(
            'random')), 'By default, Non-top-level discussions are not cohorted in a cohorted courses.'

        # if always_divide_inline_discussions is set to False, non-top-level discussion are always
        # not divided unless they are explicitly set in divided_discussions
        config_course_cohorts(
            course,
            is_cohorted=True,
        )
        config_course_discussions(
            course,
            discussion_topics=["General", "Feedback"],
            divided_discussions=["Feedback", "random_inline"],
            always_divide_inline_discussions=False
        )

        assert not utils.is_commentable_divided(course.id, to_id(
            'random')), 'Non-top-level discussion is not cohorted if always_divide_inline_discussions is False.'
        assert utils.is_commentable_divided(course.id, to_id(
            'random_inline')), 'If always_divide_inline_discussions set to False, Non-top-level discussion is cohorted if explicitly set in cohorted_discussions.'
        assert utils.is_commentable_divided(course.id, to_id(
            'Feedback')), 'If always_divide_inline_discussions set to False, top-level discussion are not affected.'

    def test_is_commentable_divided_team(self):
        course = modulestore().get_course(self.toy_course_key)
        assert not cohorts.is_course_cohorted(course.id)

        config_course_cohorts(course, is_cohorted=True)
        config_course_discussions(course, always_divide_inline_discussions=True)

        team = CourseTeamFactory(course_id=course.id)

        # Verify that team discussions are not cohorted, but other discussions are
        # if "always cohort inline discussions" is set to true.
        assert not utils.is_commentable_divided(course.id, team.discussion_topic_id)
        assert utils.is_commentable_divided(course.id, 'random')

    def test_is_commentable_divided_cohorts(self):
        course = modulestore().get_course(self.toy_course_key)
        set_discussion_division_settings(
            course.id,
            enable_cohorts=True,
            divided_discussions=[],
            always_divide_inline_discussions=True,
            division_scheme=CourseDiscussionSettings.NONE,
        )

        # Although Cohorts are enabled, discussion division is explicitly disabled.
        assert not utils.is_commentable_divided(course.id, 'random')

        # Now set the discussion division scheme.
        set_discussion_division_settings(
            course.id,
            enable_cohorts=True,
            divided_discussions=[],
            always_divide_inline_discussions=True,
            division_scheme=CourseDiscussionSettings.COHORT,
        )
        assert utils.is_commentable_divided(course.id, 'random')

    def test_is_commentable_divided_enrollment_track(self):
        course = modulestore().get_course(self.toy_course_key)
        set_discussion_division_settings(
            course.id,
            divided_discussions=[],
            always_divide_inline_discussions=True,
            division_scheme=CourseDiscussionSettings.ENROLLMENT_TRACK,
        )

        # Although division scheme is set to ENROLLMENT_TRACK, divided returns
        # False because there is only a single enrollment mode.
        assert not utils.is_commentable_divided(course.id, 'random')

        # Now create 2 explicit course modes.
        CourseModeFactory.create(course_id=course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=course.id, mode_slug=CourseMode.VERIFIED)
        assert utils.is_commentable_divided(course.id, 'random')


class GroupIdForUserTestCase(ModuleStoreTestCase):
    """ Test the get_group_id_for_user method. """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)
        self.test_user = UserFactory.create()
        CourseEnrollmentFactory.create(
            mode=CourseMode.VERIFIED, user=self.test_user, course_id=self.course.id
        )
        self.test_cohort = CohortFactory(
            course_id=self.course.id,
            name='Test Cohort',
            users=[self.test_user]
        )

    def test_discussion_division_disabled(self):
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.NONE == course_discussion_settings.division_scheme
        assert utils.get_group_id_for_user(self.test_user, course_discussion_settings) is None

    def test_discussion_division_by_cohort(self):
        set_discussion_division_settings(
            self.course.id, enable_cohorts=True, division_scheme=CourseDiscussionSettings.COHORT
        )
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.COHORT == course_discussion_settings.division_scheme
        assert self.test_cohort.id == utils.get_group_id_for_user(self.test_user, course_discussion_settings)

    def test_discussion_division_by_enrollment_track(self):
        set_discussion_division_settings(
            self.course.id, division_scheme=CourseDiscussionSettings.ENROLLMENT_TRACK
        )
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.ENROLLMENT_TRACK == course_discussion_settings.division_scheme
        assert (- 2) == utils.get_group_id_for_user(self.test_user, course_discussion_settings)


class CourseDiscussionDivisionEnabledTestCase(ModuleStoreTestCase):
    """ Test the course_discussion_division_enabled and available_division_schemes methods. """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        self.test_cohort = CohortFactory(
            course_id=self.course.id,
            name='Test Cohort',
            users=[]
        )

    def test_discussion_division_disabled(self):
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert not utils.course_discussion_division_enabled(course_discussion_settings)
        assert [] == available_division_schemes(self.course.id)

    def test_discussion_division_by_cohort(self):
        set_discussion_division_settings(
            self.course.id, enable_cohorts=False, division_scheme=CourseDiscussionSettings.COHORT
        )
        # Because cohorts are disabled, discussion division is not enabled.
        assert not utils.course_discussion_division_enabled(CourseDiscussionSettings.get(self.course.id))
        assert [] == available_division_schemes(self.course.id)
        # Now enable cohorts, which will cause discussions to be divided.
        set_discussion_division_settings(
            self.course.id, enable_cohorts=True, division_scheme=CourseDiscussionSettings.COHORT
        )
        assert utils.course_discussion_division_enabled(CourseDiscussionSettings.get(self.course.id))
        assert [CourseDiscussionSettings.COHORT] == available_division_schemes(self.course.id)

    def test_discussion_division_by_enrollment_track(self):
        set_discussion_division_settings(
            self.course.id, division_scheme=CourseDiscussionSettings.ENROLLMENT_TRACK
        )
        # Only a single enrollment track exists, so discussion division is not enabled.
        assert not utils.course_discussion_division_enabled(CourseDiscussionSettings.get(self.course.id))
        assert [] == available_division_schemes(self.course.id)

        # Now create a second CourseMode, which will cause discussions to be divided.
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)
        assert utils.course_discussion_division_enabled(CourseDiscussionSettings.get(self.course.id))
        assert [CourseDiscussionSettings.ENROLLMENT_TRACK] == available_division_schemes(self.course.id)


class GroupNameTestCase(ModuleStoreTestCase):
    """ Test the get_group_name and get_group_names_by_id methods. """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory.create(course_id=self.course.id, mode_slug=CourseMode.VERIFIED)
        self.test_cohort_1 = CohortFactory(
            course_id=self.course.id,
            name='Cohort 1',
            users=[]
        )
        self.test_cohort_2 = CohortFactory(
            course_id=self.course.id,
            name='Cohort 2',
            users=[]
        )

    def test_discussion_division_disabled(self):
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert {} == get_group_names_by_id(course_discussion_settings)
        assert utils.get_group_name((- 1000), course_discussion_settings) is None

    def test_discussion_division_by_cohort(self):
        set_discussion_division_settings(
            self.course.id, enable_cohorts=True, division_scheme=CourseDiscussionSettings.COHORT
        )
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert {self.test_cohort_1.id: self.test_cohort_1.name,
                self.test_cohort_2.id: self.test_cohort_2.name} == get_group_names_by_id(course_discussion_settings)
        assert self.test_cohort_2.name == utils.get_group_name(self.test_cohort_2.id, course_discussion_settings)
        # Test also with a group_id that doesn't exist.
        assert utils.get_group_name((- 1000), course_discussion_settings) is None

    def test_discussion_division_by_enrollment_track(self):
        set_discussion_division_settings(
            self.course.id, division_scheme=CourseDiscussionSettings.ENROLLMENT_TRACK
        )
        course_discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert {(- 1): 'audit course', (- 2): 'verified course'} == get_group_names_by_id(course_discussion_settings)

        assert 'verified course' == utils.get_group_name((- 2), course_discussion_settings)
        # Test also with a group_id that doesn't exist.
        assert utils.get_group_name((- 1000), course_discussion_settings) is None


class PermissionsTestCase(ModuleStoreTestCase):
    """Test utils functionality related to forums "abilities" (permissions)"""

    def test_get_ability(self):
        content = {}
        content['user_id'] = '1'
        content['type'] = 'thread'

        user = mock.Mock()
        user.id = 1

        with mock.patch(
            'lms.djangoapps.discussion.django_comment_client.utils.check_permissions_by_view'
        ) as check_perm:
            check_perm.return_value = True
            assert utils.get_ability(None, content, user) == {'editable': True, 'can_reply': True, 'can_delete': True,
                                                              'can_openclose': True, 'can_vote': False,
                                                              'can_report': False}

            content['user_id'] = '2'
            assert utils.get_ability(None, content, user) == {'editable': True, 'can_reply': True, 'can_delete': True,
                                                              'can_openclose': True, 'can_vote': True,
                                                              'can_report': True}

    def test_get_ability_with_global_staff(self):
        """
        Tests that global staff has rights to report other user's post inspite
        of enrolled in the course or not.
        """
        content = {'user_id': '1', 'type': 'thread'}

        with mock.patch(
            'lms.djangoapps.discussion.django_comment_client.utils.check_permissions_by_view'
        ) as check_perm:
            # check_permissions_by_view returns false because user is not enrolled in the course.
            check_perm.return_value = False
            global_staff = UserFactory(username='global_staff', email='global_staff@edx.org', is_staff=True)
            assert utils.get_ability(None, content, global_staff) == {'editable': False, 'can_reply': False,
                                                                      'can_delete': False, 'can_openclose': False,
                                                                      'can_vote': False, 'can_report': True}

    def test_is_content_authored_by(self):
        content = {}
        user = mock.Mock()
        user.id = 1

        # strict equality checking
        content['user_id'] = 1
        assert utils.is_content_authored_by(content, user)

        # cast from string to int
        content['user_id'] = '1'
        assert utils.is_content_authored_by(content, user)

        # strict equality checking, fails
        content['user_id'] = 2
        assert not utils.is_content_authored_by(content, user)

        # cast from string to int, fails
        content['user_id'] = 'string'
        assert not utils.is_content_authored_by(content, user)

        # content has no known author
        del content['user_id']
        assert not utils.is_content_authored_by(content, user)


class GroupModeratorPermissionsTestCase(ModuleStoreTestCase):
    """Test utils functionality related to forums "abilities" (permissions) for group moderators"""

    def _check_condition(user, condition, content):
        """
        Mocks check_condition method because is_open and is_team_member_if_applicable must always be true
        in order to interact with a thread or comment.
        """
        return True if condition == 'is_open' or condition == 'is_team_member_if_applicable' else False

    def setUp(self):
        super().setUp()

        # Create course, seed permissions roles, and create team
        self.course = CourseFactory.create()
        seed_permissions_roles(self.course.id)
        verified_coursemode = CourseMode.VERIFIED
        audit_coursemode = CourseMode.AUDIT

        # Create four users: group_moderator (who is within the verified enrollment track and in the cohort),
        # verified_user (who is in the verified enrollment track but not the cohort),
        # cohorted_user (who is in the cohort but not the verified enrollment track),
        # and plain_user (who is neither in the cohort nor the verified enrollment track)
        self.group_moderator = UserFactory(username='group_moderator', email='group_moderator@edx.org')
        CourseEnrollmentFactory(
            course_id=self.course.id,
            user=self.group_moderator,
            mode=verified_coursemode
        )
        self.verified_user = UserFactory(username='verified', email='verified@edx.org')
        CourseEnrollmentFactory(
            course_id=self.course.id,
            user=self.verified_user,
            mode=verified_coursemode
        )
        self.cohorted_user = UserFactory(username='cohort', email='cohort@edx.org')
        CourseEnrollmentFactory(
            course_id=self.course.id,
            user=self.cohorted_user,
            mode=audit_coursemode
        )
        self.plain_user = UserFactory(username='plain', email='plain@edx.org')
        CourseEnrollmentFactory(
            course_id=self.course.id,
            user=self.plain_user,
            mode=audit_coursemode
        )
        CohortFactory(
            course_id=self.course.id,
            name='Test Cohort',
            users=[self.group_moderator, self.cohorted_user]
        )

        # Give group moderator permissions to group_moderator
        assign_role(self.course.id, self.group_moderator, 'Group Moderator')

    @mock.patch(
        'lms.djangoapps.discussion.django_comment_client.permissions._check_condition',
        side_effect=_check_condition,
    )
    def test_not_divided(self, check_condition_function):
        """
        Group moderator should not have moderator permissions if the discussions are not divided.
        """
        content = {'user_id': self.plain_user.id, 'type': 'thread', 'username': self.plain_user.username}
        assert utils.get_ability(self.course.id, content, self.group_moderator) == {'editable': False,
                                                                                    'can_reply': True,
                                                                                    'can_delete': False,
                                                                                    'can_openclose': False,
                                                                                    'can_vote': True,
                                                                                    'can_report': True}
        content = {'user_id': self.cohorted_user.id, 'type': 'thread'}
        assert utils.get_ability(self.course.id, content, self.group_moderator) == {'editable': False,
                                                                                    'can_reply': True,
                                                                                    'can_delete': False,
                                                                                    'can_openclose': False,
                                                                                    'can_vote': True,
                                                                                    'can_report': True}
        content = {'user_id': self.verified_user.id, 'type': 'thread'}
        assert utils.get_ability(self.course.id, content, self.group_moderator) == {'editable': False,
                                                                                    'can_reply': True,
                                                                                    'can_delete': False,
                                                                                    'can_openclose': False,
                                                                                    'can_vote': True,
                                                                                    'can_report': True}

    @mock.patch(
        'lms.djangoapps.discussion.django_comment_client.permissions._check_condition',
        side_effect=_check_condition,
    )
    def test_divided_within_group(self, check_condition_function):
        """
        Group moderator should have moderator permissions within their group if the discussions are divided.
        """
        set_discussion_division_settings(self.course.id, enable_cohorts=True,
                                         division_scheme=CourseDiscussionSettings.COHORT)
        content = {'user_id': self.cohorted_user.id, 'type': 'thread', 'username': self.cohorted_user.username}
        assert utils.get_ability(self.course.id, content, self.group_moderator) == {'editable': True, 'can_reply': True,
                                                                                    'can_delete': True,
                                                                                    'can_openclose': True,
                                                                                    'can_vote': True,
                                                                                    'can_report': True}

    @mock.patch(
        'lms.djangoapps.discussion.django_comment_client.permissions._check_condition',
        side_effect=_check_condition,
    )
    def test_divided_outside_group(self, check_condition_function):
        """
        Group moderator should not have moderator permissions outside of their group.
        """
        content = {'user_id': self.plain_user.id, 'type': 'thread', 'username': self.plain_user.username}
        set_discussion_division_settings(self.course.id, division_scheme=CourseDiscussionSettings.NONE)

        assert utils.get_ability(self.course.id, content, self.group_moderator) == {'editable': False,
                                                                                    'can_reply': True,
                                                                                    'can_delete': False,
                                                                                    'can_openclose': False,
                                                                                    'can_vote': True,
                                                                                    'can_report': True}


def set_discussion_division_settings(
    course_key, enable_cohorts=False, always_divide_inline_discussions=False,
    divided_discussions=[], division_scheme=CourseDiscussionSettings.COHORT
):
    """
    Convenience method for setting cohort enablement and discussion settings.
    COHORT is the default division_scheme, as no other schemes were supported at
    the time that the unit tests were originally written.
    """
    discussion_settings = CourseDiscussionSettings.get(course_key)
    discussion_settings.update({
        'divided_discussions': divided_discussions,
        'division_scheme': division_scheme,
        'always_divide_inline_discussions': always_divide_inline_discussions,
    })
    set_course_cohorted(course_key, enable_cohorts)


@ddt.ddt
class MiscUtilsTests(TestCase):
    @ddt.data(
        ('course-v1:edX+foo101+bar_t2', '99', '99'),
        ('course-v1:edX+foo101+bar_t2', 99, 99)
    )
    @ddt.unpack
    def test_permalink_does_not_break_for_thread(self, course_id, discussion_id, content_id):
        """
        Tests that the method does not break.

        Test with permalink method for thread type of content data.
        """
        url_kwargs = {'course_id': course_id, 'discussion_id': discussion_id, 'thread_id': content_id}
        thread_data = {'id': content_id, 'course_id': course_id, 'commentable_id': discussion_id, 'type': 'thread'}
        expected_url = reverse('single_thread', kwargs=url_kwargs)

        assert utils.permalink(thread_data) == expected_url

        thread_data['course_id'] = CourseKey.from_string(course_id)
        assert utils.permalink(thread_data) == expected_url

    @ddt.data(
        ('course-v1:edX+foo101+bar_t2', '99', '99'),
        ('course-v1:edX+foo101+bar_t2', 99, 99)
    )
    @ddt.unpack
    def test_permalink_does_not_break_for_non_thread(self, course_id, discussion_id, thread_id):
        """
        Tests that the method does not break.

        Test with permalink method for non thread type of content data.
        """
        url_kwargs = {'course_id': course_id, 'discussion_id': discussion_id, 'thread_id': thread_id}
        thread_data = {
            'id': '101', 'thread_id': thread_id, 'course_id': course_id, 'commentable_id': discussion_id, 'type': 'foo'
        }
        expected_url = reverse('single_thread', kwargs=url_kwargs) + '#' + thread_data['id']

        assert utils.permalink(thread_data) == expected_url

        thread_data['course_id'] = CourseKey.from_string(course_id)
        assert utils.permalink(thread_data) == expected_url


@ddt.ddt
class SanitizeTests(unittest.TestCase):
    """Pure functional tests around sanitizing Markdown"""

    @ddt.data(
        (None, None),
        ("", ""),
        (
            "No substitutions, even if there's data: ",
            "No substitutions, even if there's data: ",
        ),
        (
            """
            [Click here](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==) Some Text

            [This link is fine](https://www.openedx.org)

            More Text [Click here](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0P)
            """,
            """
            [Click here]() Some Text

            [This link is fine](https://www.openedx.org)

            More Text [Click here]()
            """,
        ),
    )
    @ddt.unpack
    def test_input_output(self, input_str, expected_output):
        """Test a range of inputs for cleanup."""
        assert utils.sanitize_body(input_str) == expected_output


class TestConvertHtmlToMarkdown(unittest.TestCase):
    """
    Tests for the convert_html_to_markdown function.
    """
    def test_convert_a_to_markdown(self):
        """
        Tests that the convert_a_to_markdown function converts HTML anchor tags to Markdown.
        """
        input_text = '<a href="https://example.com">Example</a>'
        expected_output = '[Example](https://example.com)'
        self.assertEqual(utils.convert_a_to_markdown(input_text), expected_output)

    def test_convert_img_to_markdown(self):
        """
        Tests that the convert_img_to_markdown function converts HTML image tags to Markdown.
        """
        input_text = '<img src="https://example.com/Full-form-of-URL-1-1024x824.jpg" width="1024" height="824" />'
        expected_output = '![](https://example.com/Full-form-of-URL-1-1024x824.jpg "")'
        self.assertEqual(utils.convert_img_to_markdown(input_text), expected_output)

    def test_convert_p_to_markdown(self):
        """
        Tests that the convert_p_to_markdown function converts HTML paragraph tags to Markdown.
        """
        input_text = '<p>Paragraph text</p>'
        expected_output = '\n\nParagraph text\n\n'
        self.assertEqual(utils.convert_p_to_markdown(input_text), expected_output)

    def test_convert_html_to_markdown(self):
        """
        Tests that the convert_html_to_markdown function converts HTML to Markdown.
        """
        input_text = '<a href="https://example.com">Example</a><p>Paragraph text</p> ' \
                     '<img src="https://example.com/Full-form-of-URL-1-1024x824.jpg" width="1024" height="824" />'
        # pylint: disable=line-too-long
        expected_output = '[Example](https://example.com)\n\nParagraph text\n\n ![](https://example.com/Full-form-of-URL-1-1024x824.jpg "")'
        self.assertEqual(utils.convert_html_to_markdown(input_text), expected_output)
