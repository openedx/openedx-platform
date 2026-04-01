"""
    Test split modulestore w/o using any django stuff.
"""


import datetime
import os
import random
import re
import unittest
from importlib import import_module
from unittest.mock import patch

import pytest
import ddt
from ccx_keys.locator import CCXBlockUsageLocator
from django.core.cache import InvalidCacheBackendError, caches
from opaque_keys.edx.locator import BlockUsageLocator, CourseKey, CourseLocator, LocalId
from xblock.fields import Date, Reference, ReferenceList, ReferenceValueDict, Timedelta

from openedx.core.djangolib.testing.utils import CacheIsolationMixin
from openedx.core.lib import tempdir
from openedx.core.lib.tests import attr
from xmodule.course_block import CourseBlock
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.edit_info import EditInfoMixin
from xmodule.modulestore.exceptions import (
    DuplicateCourseError,
    DuplicateItemError,
    InsufficientSpecificationError,
    ItemNotFoundError,
    VersionConflictError
)
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.modulestore.split_mongo import BlockKey
from xmodule.modulestore.split_mongo.mongo_connection import CourseStructureCache
from xmodule.modulestore.split_mongo.split import SplitMongoModuleStore
from xmodule.modulestore.tests.factories import check_mongo_calls
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM
from xmodule.modulestore.tests.test_modulestore import check_has_course_method
from xmodule.tabs import CourseTab
from xmodule.x_module import XModuleMixin

BRANCH_NAME_DRAFT = ModuleStoreEnum.BranchName.draft
BRANCH_NAME_PUBLISHED = ModuleStoreEnum.BranchName.published

TEST_USER_ID = ModuleStoreEnum.UserID.test
# Other user IDs for use in these tests:
TEST_OTHER_USER_ID = ModuleStoreEnum.UserID.test - 10
TEST_GUEST_USER_ID = ModuleStoreEnum.UserID.test - 11
TEST_ASSISTANT_USER_ID = ModuleStoreEnum.UserID.test - 12


@attr('mongo')
@pytest.mark.django_db
class SplitModuleTest(unittest.TestCase):
    '''
    The base set of tests manually populates a db w/ courses which have
    versions. It creates unique collection names and removes them after all
    tests finish.
    '''
    # Snippets of what would be in the django settings envs file
    DOC_STORE_CONFIG = {
        'host': MONGO_HOST,
        'db': f'test_xmodule_{os.getpid()}',
        'port': MONGO_PORT_NUM,
        'collection': 'modulestore',
    }
    modulestore_options = {
        'default_class': 'xmodule.hidden_block.HiddenBlock',
        'fs_root': tempdir.mkdtemp_clean(),
        'xblock_mixins': (InheritanceMixin, XModuleMixin, EditInfoMixin)
    }

    MODULESTORE = {
        'ENGINE': 'xmodule.modulestore.split_mongo.split.SplitMongoModuleStore',
        'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
        'OPTIONS': modulestore_options
    }

    modulestore = None

    _date_field = Date()
    _time_delta_field = Timedelta()
    COURSE_CONTENT = {
        "testx.GreekHero": {
            "org": "testx",
            "course": "GreekHero",
            "run": "run",
            "root_block_id": "head12345",
            "user_id": TEST_USER_ID,
            "fields": {
                "tabs": [
                    CourseTab.load('courseware'),
                    CourseTab.load('discussion'),
                    CourseTab.load('wiki'),
                ],
                "start": _date_field.from_json("2013-02-14T05:00"),
                "display_name": "The Ancient Greek Hero",
                "grading_policy": {
                    "GRADER": [
                        {
                            "min_count": 5,
                            "weight": 0.15,
                            "type": "Homework",
                            "drop_count": 1,
                            "short_label": "HWa"
                        },
                        {
                            "short_label": "",
                            "min_count": 2,
                            "type": "Lab",
                            "drop_count": 0,
                            "weight": 0.15
                        },
                        {
                            "short_label": "Midterm",
                            "min_count": 1,
                            "type": "Midterm Exam",
                            "drop_count": 0,
                            "weight": 0.3
                        },
                        {
                            "short_label": "Final",
                            "min_count": 1,
                            "type": "Final Exam",
                            "drop_count": 0,
                            "weight": 0.4
                        }
                    ],
                    "GRADE_CUTOFFS": {
                        "Pass": 0.75
                    },
                },
            },
            "revisions": [
                {
                    "user_id": TEST_ASSISTANT_USER_ID,
                    "update": {
                        ("course", "head12345"): {
                            "end": _date_field.from_json("2013-04-13T04:30"),
                            "tabs": [
                                CourseTab.load('courseware'),
                                CourseTab.load('discussion'),
                                CourseTab.load('wiki'),
                                CourseTab.load(
                                    'static_tab', name="Syllabus", url_slug="01356a17b5924b17a04b7fc2426a3798"
                                ),
                                CourseTab.load(
                                    'static_tab', name="Advice for Students", url_slug="57e9991c0d794ff58f7defae3e042e"
                                ),
                            ],
                            "graceperiod": _time_delta_field.from_json("2 hours 0 minutes 0 seconds"),
                            "grading_policy": {
                                "GRADER": [
                                    {
                                        "min_count": 5,
                                        "weight": 0.15,
                                        "type": "Homework",
                                        "drop_count": 1,
                                        "short_label": "HWa"
                                    },
                                    {
                                        "short_label": "",
                                        "min_count": 12,
                                        "type": "Lab",
                                        "drop_count": 2,
                                        "weight": 0.15
                                    },
                                    {
                                        "short_label": "Midterm",
                                        "min_count": 1,
                                        "type": "Midterm Exam",
                                        "drop_count": 0,
                                        "weight": 0.3
                                    },
                                    {
                                        "short_label": "Final",
                                        "min_count": 1,
                                        "type": "Final Exam",
                                        "drop_count": 0,
                                        "weight": 0.4
                                    }
                                ],
                                "GRADE_CUTOFFS": {
                                    "Pass": 0.55
                                }
                            },
                        }
                    }
                },
                {
                    "user_id": TEST_ASSISTANT_USER_ID,
                    "update": {
                        ("course", "head12345"): {
                            "end": _date_field.from_json("2013-06-13T04:30"),
                            "grading_policy": {
                                "GRADER": [
                                    {
                                        "min_count": 4,
                                        "weight": 0.15,
                                        "type": "Homework",
                                        "drop_count": 2,
                                        "short_label": "HWa"
                                    },
                                    {
                                        "short_label": "",
                                        "min_count": 12,
                                        "type": "Lab",
                                        "drop_count": 2,
                                        "weight": 0.15
                                    },
                                    {
                                        "short_label": "Midterm",
                                        "min_count": 1,
                                        "type": "Midterm Exam",
                                        "drop_count": 0,
                                        "weight": 0.3
                                    },
                                    {
                                        "short_label": "Final",
                                        "min_count": 1,
                                        "type": "Final Exam",
                                        "drop_count": 0,
                                        "weight": 0.4
                                    }
                                ],
                                "GRADE_CUTOFFS": {
                                    "Pass": 0.45
                                }
                            },
                            "enrollment_start": _date_field.from_json("2013-01-01T05:00"),
                            "enrollment_end": _date_field.from_json("2013-03-02T05:00"),
                            "advertised_start": "Fall 2013",
                        }
                    },
                    "create": [
                        {
                            "id": "chapter1",
                            "parent": "head12345",
                            "parent_type": "course",
                            "category": "chapter",
                            "fields": {
                                "display_name": "Hercules"
                            },
                        },
                        {
                            "id": "chap",
                            "parent": "head12345",
                            "parent_type": "course",
                            "category": "chapter",
                            "fields": {
                                "display_name": "Buffalo buffalo Buffalo buffalo buffalo buffalo Buffalo buffalo"
                            },
                        },
                        {
                            "id": "chapter2",
                            "parent": "head12345",
                            "parent_type": "course",
                            "category": "chapter",
                            "fields": {
                                "display_name": "Hera heckles Hercules"
                            },
                        },
                        {
                            "id": "chapter3",
                            "parent": "head12345",
                            "parent_type": "course",
                            "category": "chapter",
                            "fields": {
                                "display_name": "Hera cuckolds Zeus"
                            },
                        },
                        {
                            "id": "problem1",
                            "parent": "chapter3",
                            "parent_type": "chapter",
                            "category": "problem",
                            "fields": {
                                "display_name": "Problem 3.1",
                                "graceperiod": _time_delta_field.from_json("4 hours 0 minutes 0 seconds"),
                            },
                        },
                        {
                            "id": "problem3_2",
                            "parent": "chapter3",
                            "parent_type": "chapter",
                            "category": "problem",
                            "fields": {
                                "display_name": "Problem 3.2"
                            },
                        },
                        {
                            "id": "problem32",
                            "parent": "chapter3",
                            "parent_type": "chapter",
                            "category": "problem",
                            "fields": {
                                "display_name": "Problem 3.3",
                                "group_access": {"3": ["33"]},
                            },
                        }
                    ]
                },
            ]
        },
        "testx.wonderful": {
            "org": "testx",
            "course": "wonderful",
            "run": "run",
            "root_block_id": "head23456",
            "user_id": TEST_USER_ID,
            "fields": {
                "tabs": [
                    CourseTab.load('courseware'),
                    CourseTab.load('discussion'),
                    CourseTab.load('wiki'),
                ],
                "start": _date_field.from_json("2013-02-14T05:00"),
                "display_name": "A wonderful course",
                "grading_policy": {
                    "GRADER": [
                        {
                            "min_count": 14,
                            "weight": 0.25,
                            "type": "Homework",
                            "drop_count": 1,
                            "short_label": "HWa"
                        },
                        {
                            "short_label": "",
                            "min_count": 12,
                            "type": "Lab",
                            "drop_count": 2,
                            "weight": 0.25
                        },
                        {
                            "short_label": "Midterm",
                            "min_count": 1,
                            "type": "Midterm Exam",
                            "drop_count": 0,
                            "weight": 0.2
                        },
                        {
                            "short_label": "Final",
                            "min_count": 1,
                            "type": "Final Exam",
                            "drop_count": 0,
                            "weight": 0.3
                        }
                    ],
                    "GRADE_CUTOFFS": {
                        "Pass": 0.95
                    }
                },
            },
            "revisions": [
                {
                    "user_id": TEST_USER_ID,
                    "update": {
                        ("course", "head23456"): {
                            "display_name": "The most wonderful course",
                            "grading_policy": {
                                "GRADER": [
                                    {
                                        "min_count": 14,
                                        "weight": 0.25,
                                        "type": "Homework",
                                        "drop_count": 1,
                                        "short_label": "HWa"
                                    },
                                    {
                                        "short_label": "",
                                        "min_count": 12,
                                        "type": "Lab",
                                        "drop_count": 2,
                                        "weight": 0.25
                                    },
                                    {
                                        "short_label": "Midterm",
                                        "min_count": 1,
                                        "type": "Midterm Exam",
                                        "drop_count": 0,
                                        "weight": 0.2
                                    },
                                    {
                                        "short_label": "Final",
                                        "min_count": 1,
                                        "type": "Final Exam",
                                        "drop_count": 0,
                                        "weight": 0.3
                                    }
                                ],
                                "GRADE_CUTOFFS": {
                                    "Pass": 0.45
                                }
                            },
                        }
                    }
                }
            ]
        },
        "guestx.contender": {
            "org": "guestx",
            "course": "contender",
            "run": "run",
            "root_block_id": "head345679",
            "user_id": TEST_GUEST_USER_ID,
            "fields": {
                "tabs": [
                    CourseTab.load('courseware'),
                    CourseTab.load('discussion'),
                    CourseTab.load('wiki'),
                ],
                "start": _date_field.from_json("2013-03-14T05:00"),
                "display_name": "Yet another contender",
                "grading_policy": {
                    "GRADER": [
                        {
                            "min_count": 4,
                            "weight": 0.25,
                            "type": "Homework",
                            "drop_count": 0,
                            "short_label": "HW"
                        },
                        {
                            "short_label": "Midterm",
                            "min_count": 1,
                            "type": "Midterm Exam",
                            "drop_count": 0,
                            "weight": 0.4
                        },
                        {
                            "short_label": "Final",
                            "min_count": 1,
                            "type": "Final Exam",
                            "drop_count": 0,
                            "weight": 0.35
                        }
                    ],
                    "GRADE_CUTOFFS": {
                        "Pass": 0.25
                    }
                },
            }
        },
    }

    @staticmethod
    def bootstrapDB(split_store):  # pylint: disable=invalid-name
        '''
        Sets up the initial data into the db
        '''
        for _course_id, course_spec in SplitModuleTest.COURSE_CONTENT.items():
            course = split_store.create_course(
                course_spec['org'],
                course_spec['course'],
                course_spec['run'],
                course_spec['user_id'],
                master_branch=BRANCH_NAME_DRAFT,
                fields=course_spec['fields'],
                root_block_id=course_spec['root_block_id']
            )
            for revision in course_spec.get('revisions', []):
                for (block_type, block_id), fields in revision.get('update', {}).items():
                    # cheat since course is most frequent
                    if course.location.block_id == block_id:
                        block = course
                    else:
                        # not easy to figure out the category but get_item won't care
                        block_usage = BlockUsageLocator.make_relative(course.location, block_type, block_id)
                        block = split_store.get_item(block_usage)
                    for key, value in fields.items():
                        setattr(block, key, value)
                # create new blocks into dag: parent must already exist; thus, order is important
                new_ele_dict = {}
                for spec in revision.get('create', []):
                    if spec['parent'] in new_ele_dict:
                        parent = new_ele_dict.get(spec['parent'])
                    elif spec['parent'] == course.location.block_id:
                        parent = course
                    else:
                        block_usage = BlockUsageLocator.make_relative(course.location, spec['parent_type'], spec['parent'])  # lint-amnesty, pylint: disable=line-too-long
                        parent = split_store.get_item(block_usage)
                    block_id = LocalId(spec['id'])
                    child = split_store.create_xblock(
                        course.runtime, course.id, spec['category'], block_id, spec['fields'], parent_xblock=parent
                    )
                    new_ele_dict[spec['id']] = child
                course = split_store.persist_xblock_dag(course, revision['user_id'])
        # publish "testx.wonderful"
        source_course = CourseLocator(org="testx", course="wonderful", run="run", branch=BRANCH_NAME_DRAFT)
        to_publish = BlockUsageLocator(
            source_course,
            block_type='course',
            block_id="head23456"
        )
        destination = CourseLocator(org="testx", course="wonderful", run="run", branch=BRANCH_NAME_PUBLISHED)
        split_store.copy(TEST_USER_ID, source_course, destination, [to_publish], None)

    def setUp(self):
        super().setUp()
        self.user_id = random.getrandbits(32)

    def tearDown(self):
        """
        Clear persistence between each test.
        """
        if SplitModuleTest.modulestore:
            modulestore()._drop_database(database=False, connections=False)  # pylint: disable=protected-access
            # drop the modulestore to force re init
            SplitModuleTest.modulestore = None
        super().tearDown()

    def findByIdInResult(self, collection, _id):  # pylint: disable=invalid-name
        """
        Result is a collection of blocks. Find the one whose block id
        matches the _id.
        """
        for element in collection:
            if element.location.block_id == _id:
                return element


class TestHasChildrenAtDepth(SplitModuleTest):
    """Test the has_children_at_depth method of XModuleMixin. """

    def test_has_children_at_depth(self):
        course_locator = CourseLocator(
            org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT
        )
        block_locator = BlockUsageLocator(
            course_locator, 'course', 'head12345'
        )
        block = modulestore().get_item(block_locator)

        self.assertRaises(
            ValueError, block.has_children_at_depth, -1,
        )
        assert block.has_children_at_depth(0)
        assert block.has_children_at_depth(1)
        assert not block.has_children_at_depth(2)

        ch1 = modulestore().get_item(
            BlockUsageLocator(course_locator, 'chapter', block_id='chapter1')
        )
        assert not ch1.has_children_at_depth(0)

        ch2 = modulestore().get_item(
            BlockUsageLocator(course_locator, 'chapter', block_id='chapter2')
        )
        assert not ch2.has_children_at_depth(0)

        ch3 = modulestore().get_item(
            BlockUsageLocator(course_locator, 'chapter', block_id='chapter3')
        )
        assert ch3.has_children_at_depth(0)
        assert not ch3.has_children_at_depth(1)


@ddt.ddt
class SplitModuleCourseTests(SplitModuleTest):
    '''
    Course CRUD operation tests
    '''

    def test_get_courses(self):
        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT)
        # should have gotten 3 draft courses
        assert len(courses) == 3, 'Wrong number of courses'
        # check metadata -- NOTE no promised order
        course = self.findByIdInResult(courses, "head12345")
        assert course.location.org == 'testx'
        assert course.category == 'course', 'wrong category'
        assert len(course.tabs) == 5, 'wrong number of tabs'
        assert course.display_name == 'The Ancient Greek Hero', 'wrong display name'
        assert course.advertised_start == 'Fall 2013', 'advertised_start'
        assert len(course.children) == 4, 'children'
        # check dates and graders--forces loading of block
        assert course.edited_by == TEST_ASSISTANT_USER_ID
        self.assertDictEqual(course.grade_cutoffs, {"Pass": 0.45})

    def test_get_courses_with_same_course_index(self):
        """
        Test that if two courses point to same course index,
        `get_courses` should return both courses.
        """
        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT)
        # Should have gotten 3 draft courses.
        assert len(courses) == 3

        course_index = modulestore().get_course_index_info(courses[0].id)
        # Creating a new course with same course index of another course.
        new_draft_course = modulestore().create_course(
            'testX', 'rerun_2.0', 'run_q2', 1, BRANCH_NAME_DRAFT, versions_dict=course_index['versions']
        )
        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT)
        # Should have gotten 4 draft courses.
        assert len(courses) == 4
        assert new_draft_course.id.version_agnostic() in [c.id for c in courses]

    def test_get_org_courses(self):
        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT, org='guestx')

        # should have gotten 1 draft courses
        assert len(courses) == 1

        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT, org='testx')

        # should have gotten 2 draft courses
        assert len(courses) == 2

        # although this is already covered in other tests, let's
        # also not pass in org= parameter to make sure we get back
        # 3 courses
        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT)
        assert len(courses) == 3

    def test_branch_requests(self):
        # query w/ branch qualifier (both draft and published)
        def _verify_published_course(courses_published):
            """ Helper function for verifying published course. """
            assert len(courses_published) == 1, len(courses_published)
            course = self.findByIdInResult(courses_published, "head23456")
            assert course is not None, 'published courses'
            assert course.location.course_key.org == 'testx'
            assert course.location.course_key.course == 'wonderful'
            assert course.category == 'course', 'wrong category'
            assert len(course.tabs) == 3, 'wrong number of tabs'
            assert course.display_name == 'The most wonderful course', course.display_name
            assert course.advertised_start is None
            assert len(course.children) == 0, 'children'

        _verify_published_course(modulestore().get_courses(branch=BRANCH_NAME_PUBLISHED))

    def test_has_course(self):
        '''
        Test the various calling forms for has_course
        '''

        check_has_course_method(
            modulestore(),
            CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_DRAFT),
            locator_key_fields=['org', 'course', 'run']
        )

    def test_get_course(self):
        '''
        Test the various calling forms for get_course
        '''
        locator = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        head_course = modulestore().get_course(locator)
        assert head_course.location.version_guid != head_course.previous_version
        locator = CourseLocator(version_guid=head_course.previous_version)
        course = modulestore().get_course(locator)
        assert course.location.course_key.org is None
        assert course.location.version_guid == head_course.previous_version
        assert course.category == 'course'
        assert len(course.tabs) == 5
        assert course.display_name == 'The Ancient Greek Hero'
        assert course.graceperiod == datetime.timedelta(hours=2)
        assert course.advertised_start is None
        assert len(course.children) == 0
        assert course.definition_locator.definition_id != head_course.definition_locator.definition_id
        # check dates and graders--forces loading of block
        assert course.edited_by == TEST_ASSISTANT_USER_ID
        self.assertDictEqual(course.grade_cutoffs, {"Pass": 0.55})

        locator = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        course = modulestore().get_course(locator)
        assert course.location.course_key.org == 'testx'
        assert course.location.course_key.course == 'GreekHero'
        assert course.location.course_key.run == 'run'
        assert course.category == 'course'
        assert len(course.tabs) == 5
        assert course.display_name == 'The Ancient Greek Hero'
        assert course.advertised_start == 'Fall 2013'
        assert len(course.children) == 4
        # check dates and graders--forces loading of block
        assert course.edited_by == TEST_ASSISTANT_USER_ID
        self.assertDictEqual(course.grade_cutoffs, {"Pass": 0.45})

        locator = CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_PUBLISHED)
        course = modulestore().get_course(locator)
        published_version = course.location.version_guid

        locator = CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_DRAFT)
        course = modulestore().get_course(locator)
        assert course.location.version_guid != published_version

    def test_get_course_negative(self):
        # Now negative testing
        with pytest.raises(InsufficientSpecificationError):
            modulestore().get_course(CourseLocator(org='edu', course='meh', run='blah'))
        with pytest.raises(ItemNotFoundError):
            modulestore().get_course(CourseLocator(org='edu', course='nosuchthing', run="run", branch=BRANCH_NAME_DRAFT))  # lint-amnesty, pylint: disable=line-too-long
        with pytest.raises(ItemNotFoundError):
            modulestore().get_course(CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_PUBLISHED))  # lint-amnesty, pylint: disable=line-too-long

    def test_cache(self):
        """
        Test that the mechanics of caching work.
        """
        locator = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        course = modulestore().get_course(locator)
        block_map = modulestore().cache_items(
            course.runtime, [BlockKey.from_usage_key(child) for child in course.children], course.id, depth=3
        )
        assert BlockKey('chapter', 'chapter1') in block_map
        assert BlockKey('problem', 'problem3_2') in block_map

    def test_persist_dag(self):
        """
        try saving temporary xblocks
        """
        test_course = modulestore().create_course(
            course='course', run='2014', org='testx',
            display_name='fun test course', user_id=TEST_OTHER_USER_ID,
            master_branch=ModuleStoreEnum.BranchName.draft
        )
        test_chapter = modulestore().create_xblock(
            test_course.runtime, test_course.id, 'chapter', fields={'display_name': 'chapter n'},
            parent_xblock=test_course
        )
        assert test_chapter.display_name == 'chapter n'
        test_def_content = '<problem>boo</problem>'
        # create child
        new_block = modulestore().create_xblock(
            test_course.runtime, test_course.id,
            'problem',
            fields={
                'data': test_def_content,
                'display_name': 'problem'
            },
            parent_xblock=test_chapter
        )
        assert new_block.definition_locator is not None
        assert isinstance(new_block.definition_locator.definition_id, LocalId)
        # better to pass in persisted parent over the subdag so
        # subdag gets the parent pointer (otherwise 2 ops, persist dag, update parent children,
        # persist parent
        persisted_course = modulestore().persist_xblock_dag(test_course, TEST_OTHER_USER_ID)
        assert len(persisted_course.children) == 1
        persisted_chapter = persisted_course.get_children()[0]
        assert persisted_chapter.category == 'chapter'
        assert persisted_chapter.display_name == 'chapter n'
        assert len(persisted_chapter.children) == 1
        persisted_problem = persisted_chapter.get_children()[0]
        assert persisted_problem.category == 'problem'
        assert persisted_problem.data == test_def_content
        # update it
        persisted_problem.display_name = 'altered problem'
        persisted_problem = modulestore().update_item(persisted_problem, TEST_OTHER_USER_ID)
        assert persisted_problem.display_name == 'altered problem'

    @ddt.data(
        ("course-v1:edx+test_course+test_run", BlockUsageLocator),
        ("ccx-v1:edX+test_course+test_run+ccx@1", CCXBlockUsageLocator),
    )
    @ddt.unpack
    def test_make_course_usage_key(self, course_id, root_block_cls):
        """Test that we get back the appropriate usage key for the root of a course key.

        In particular, we want to make sure that it properly handles CCX courses.
        """
        course_key = CourseKey.from_string(course_id)
        root_block_key = modulestore().make_course_usage_key(course_key)
        assert isinstance(root_block_key, root_block_cls)
        assert root_block_key.block_type == 'course'
        assert root_block_key.block_id == 'course'


class TestCourseStructureCache(CacheIsolationMixin, SplitModuleTest):
    """Tests for the CourseStructureCache"""

    # CacheIsolationMixin will reset the cache between test cases

    # We'll use the "default" cache as a valid cache, and the "course_structure_cache" as a dummy cache
    ENABLED_CACHES = ["default"]

    def setUp(self):
        # make a new course:
        self.user = random.getrandbits(32)
        self.new_course = modulestore().create_course(
            'org', 'course', 'test_run', self.user, BRANCH_NAME_DRAFT,
        )

        super().setUp()

    @patch('xmodule.modulestore.split_mongo.mongo_connection.get_cache')
    def test_course_structure_cache(self, mock_get_cache):
        # force get_cache to return the default cache so we can test
        # its caching behavior
        enabled_cache = caches['default']
        mock_get_cache.return_value = enabled_cache

        with check_mongo_calls(1):
            not_cached_structure = self._get_structure(self.new_course)

        # when cache is warmed, we should have one fewer mongo call
        with check_mongo_calls(0):
            cached_structure = self._get_structure(self.new_course)

        # now make sure that you get the same structure
        assert cached_structure == not_cached_structure

        # If data is corrupted, get it from mongo again.
        cache_key = self.new_course.id.version_guid
        enabled_cache.set(cache_key, b"bad_data")
        with check_mongo_calls(1):
            not_corrupt_structure = self._get_structure(self.new_course)

        # now make sure that you get the same structure
        assert not_corrupt_structure == not_cached_structure

    @patch('xmodule.modulestore.split_mongo.mongo_connection.get_cache')
    def test_course_structure_cache_no_cache_configured(self, mock_get_cache):
        mock_get_cache.side_effect = InvalidCacheBackendError

        with check_mongo_calls(1):
            not_cached_structure = self._get_structure(self.new_course)

        # if the cache isn't configured, we expect to have to make
        # another mongo call here if we want the same course structure
        with check_mongo_calls(1):
            cached_structure = self._get_structure(self.new_course)

        # now make sure that you get the same structure
        assert cached_structure == not_cached_structure

    def test_dummy_cache(self):
        with check_mongo_calls(1):
            not_cached_structure = self._get_structure(self.new_course)

        # Since the test is using the dummy cache, it's not actually caching
        # anything
        with check_mongo_calls(1):
            cached_structure = self._get_structure(self.new_course)

        # now make sure that you get the same structure
        assert cached_structure == not_cached_structure

    @patch('xmodule.modulestore.split_mongo.mongo_connection.monitoring.set_custom_attribute')
    @patch('django.core.cache.cache.set')
    @patch('xmodule.modulestore.split_mongo.mongo_connection.get_cache')
    def test_course_structure_cache_with_data_chunk_greater_than_two_mb(self, mock_get_cache, mock_set_cache,
                                                                        mock_set_custom_attribute):
        enabled_cache = caches['default']
        mock_get_cache.return_value = enabled_cache

        course_cache = CourseStructureCache()

        size = 3000000000
        # this data_chunk will be compressed before being cached
        data_chunk = b'\x00' * size

        course_cache.set('my_data_chunk', data_chunk)
        mock_set_cache.assert_not_called()
        mock_set_custom_attribute.assert_called()

    @patch('xmodule.modulestore.split_mongo.mongo_connection.monitoring.set_custom_attribute')
    @patch('django.core.cache.cache.set')
    @patch('xmodule.modulestore.split_mongo.mongo_connection.get_cache')
    def test_course_structure_cache_with_data_chunk_lesser_than_two_mb(self, mock_get_cache, mock_set_cache,
                                                                       mock_set_custom_attribute):
        enabled_cache = caches['default']
        mock_get_cache.return_value = enabled_cache
        course_cache = CourseStructureCache()
        size = 30000
        data_chunk = b'\x00' * size

        course_cache.set('my_data_chunk', data_chunk)
        mock_set_cache.assert_called()
        mock_set_custom_attribute.assert_not_called()

    def _get_structure(self, course):
        """
        Helper function to get a structure from a course.
        """
        return modulestore().db_connection.get_structure(
            course.location.as_object_id(course.location.version_guid)
        )


class SplitModuleItemTests(SplitModuleTest):
    '''
    Item read tests including inheritance
    '''

    def test_has_item(self):
        '''
        has_item(BlockUsageLocator)
        '''
        org = 'testx'
        course = 'GreekHero'
        run = 'run'
        course_locator = CourseLocator(org=org, course=course, run=run, branch=BRANCH_NAME_DRAFT)
        course = modulestore().get_course(course_locator)
        previous_version = course.previous_version
        # positive tests of various forms
        locator = course.location.map_into_course(CourseLocator(version_guid=previous_version))
        assert modulestore().has_item(locator), ("couldn't find in %s" % previous_version)

        locator = course.location.version_agnostic()
        assert modulestore().has_item(locator)
        assert not modulestore()\
            .has_item(BlockUsageLocator(locator.course_key.for_branch(BRANCH_NAME_PUBLISHED),
                                        block_type=locator.block_type,
                                        block_id=locator.block_id)), 'found in published head'

        # not a course obj
        locator = BlockUsageLocator(course_locator, block_type='chapter', block_id='chapter1')
        assert modulestore().has_item(locator), "couldn't find chapter1"

        # in published course
        locator = BlockUsageLocator(
            CourseLocator(org="testx", course="wonderful", run="run", branch=BRANCH_NAME_DRAFT),
            block_type="course",
            block_id="head23456"
        )
        assert modulestore().has_item(locator.for_branch(BRANCH_NAME_PUBLISHED))

    def test_negative_has_item(self):
        # negative tests--not found
        # no such course or block
        locator = BlockUsageLocator(
            CourseLocator(org="foo", course="doesnotexist", run="run", branch=BRANCH_NAME_DRAFT),
            block_type="course",
            block_id="head23456"
        )
        assert not modulestore().has_item(locator)
        locator = BlockUsageLocator(
            CourseLocator(org="testx", course="wonderful", run="run", branch=BRANCH_NAME_DRAFT),
            block_type="vertical",
            block_id="doesnotexist"
        )
        assert not modulestore().has_item(locator)

    def test_get_item(self):
        '''
        get_item(blocklocator)
        '''
        hero_locator = CourseLocator(org="testx", course="GreekHero", run="run", branch=BRANCH_NAME_DRAFT)
        course = modulestore().get_course(hero_locator)
        previous_version = course.previous_version

        # positive tests of various forms
        locator = course.location.map_into_course(CourseLocator(version_guid=previous_version))
        block = modulestore().get_item(locator)
        assert isinstance(block, CourseBlock)
        assert isinstance(modulestore().get_item(locator), CourseBlock)

        def verify_greek_hero(block):
            """
            Check contents of block
            """
            assert block.location.org == 'testx'
            assert block.location.course == 'GreekHero'
            assert block.location.run == 'run'
            assert len(block.tabs) == 5, 'wrong number of tabs'
            assert block.display_name == 'The Ancient Greek Hero'
            assert block.advertised_start == 'Fall 2013'
            assert len(block.children) == 4
            # check dates and graders--forces loading of block
            assert block.edited_by == TEST_ASSISTANT_USER_ID
            self.assertDictEqual(
                block.grade_cutoffs, {"Pass": 0.45},
            )

        verify_greek_hero(modulestore().get_item(course.location))

        # try to look up other branches
        with pytest.raises(ItemNotFoundError):
            modulestore().get_item(course.location.for_branch(BRANCH_NAME_PUBLISHED))

    def test_get_non_root(self):
        # not a course obj
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'chapter', 'chapter1'
        )
        block = modulestore().get_item(locator)
        assert block.location.org == 'testx'
        assert block.location.course == 'GreekHero'
        assert block.category == 'chapter'
        assert block.display_name == 'Hercules'
        assert block.edited_by == TEST_ASSISTANT_USER_ID

        # in published course
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_PUBLISHED), 'course', 'head23456'  # lint-amnesty, pylint: disable=line-too-long
        )
        assert isinstance(modulestore().get_item(locator), CourseBlock)

        # negative tests--not found
        # no such course or block
        locator = BlockUsageLocator(
            CourseLocator(org='doesnotexist', course='doesnotexist', run="run", branch=BRANCH_NAME_DRAFT), 'course', 'head23456'  # lint-amnesty, pylint: disable=line-too-long
        )
        with pytest.raises(ItemNotFoundError):
            modulestore().get_item(locator)
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_DRAFT), 'html', 'doesnotexist'
        )
        with pytest.raises(ItemNotFoundError):
            modulestore().get_item(locator)

    # pylint: disable=protected-access
    def test_matching(self):
        '''
        test the block and value matches help functions
        '''
        assert modulestore()._value_matches('help', 'help')
        assert not modulestore()._value_matches('help', 'Help')
        assert modulestore()._value_matches(['distract', 'help', 'notme'], 'help')
        assert not modulestore()._value_matches(['distract', 'Help', 'notme'], 'help')
        assert not modulestore()._block_matches({'field': ['distract', 'Help', 'notme']}, {'field': 'help'})
        assert modulestore()._block_matches({'field': ['distract', 'help', 'notme'], 'irrelevant': 2},
                                            {'field': 'help'})
        assert modulestore()._value_matches('I need some help', re.compile('help'))
        assert modulestore()._value_matches(['I need some help', 'today'], re.compile('help'))
        assert not modulestore()._value_matches('I need some help', re.compile('Help'))
        assert modulestore()._value_matches(['I need some help', 'today'], re.compile('Help', re.IGNORECASE))

        assert modulestore()._value_matches('gotcha', {'$in': ['a', 'bunch', 'of', 'gotcha']})
        assert not modulestore()._value_matches('gotcha', {'$in': ['a', 'bunch', 'of', 'gotchas']})
        assert not modulestore()._value_matches('gotcha', {'$nin': ['a', 'bunch', 'of', 'gotcha']})
        assert modulestore()._value_matches('gotcha', {'$nin': ['a', 'bunch', 'of', 'gotchas']})

        assert modulestore()._block_matches({'group_access': {'1': [1]}}, {'group_access': {'$exists': True}})
        assert modulestore()._block_matches({'a': 1, 'b': 2}, {'group_access': {'$exists': False}})
        assert modulestore()._block_matches({'a': 1, 'group_access': {'1': [1]}},
                                            {'a': 1, 'group_access': {'$exists': True}})
        assert not modulestore()._block_matches({'a': 1, 'group_access': {'1': [1]}},
                                                {'a': 111, 'group_access': {'$exists': True}})
        assert modulestore()._block_matches({'a': 1, 'b': 2}, {'a': 1, 'group_access': {'$exists': False}})
        assert not modulestore()._block_matches({'a': 1, 'b': 2}, {'a': 9, 'group_access': {'$exists': False}})

        assert modulestore()._block_matches({'a': 1, 'b': 2}, {'a': 1})
        assert not modulestore()._block_matches({'a': 1, 'b': 2}, {'a': 2})
        assert not modulestore()._block_matches({'a': 1, 'b': 2}, {'c': 1})
        assert not modulestore()._block_matches({'a': 1, 'b': 2}, {'a': 1, 'c': 1})
        assert modulestore()._block_matches({'a': 1, 'b': 2}, {'a': (lambda i: (0 < i < 2))})

    def test_get_items(self):
        '''
        get_items(locator, qualifiers, [branch])
        '''
        locator = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        # get all blocks
        matches = modulestore().get_items(locator)
        assert len(matches) == 8
        matches = modulestore().get_items(locator)
        assert len(matches) == 8
        matches = modulestore().get_items(locator, qualifiers={'category': 'chapter'})
        assert len(matches) == 4
        matches = modulestore().get_items(locator, qualifiers={'category': 'garbage'})
        assert len(matches) == 0
        # Test that we don't accidentally get an item with a similar name.
        matches = modulestore().get_items(locator, qualifiers={'name': 'chapter1'})
        assert len(matches) == 1
        matches = modulestore().get_items(locator, qualifiers={'name': ['chapter1', 'chapter2']})
        assert len(matches) == 2
        matches = modulestore().get_items(
            locator,
            qualifiers={'category': 'chapter'},
            settings={'display_name': re.compile(r'Hera')},
        )
        assert len(matches) == 2
        matches = modulestore().get_items(locator, settings={'group_access': {'$exists': True}})
        assert len(matches) == 1
        matches = modulestore().get_items(locator, settings={'group_access': {'$exists': False}})
        assert len(matches) == 7

    def test_get_parents(self):
        '''
        get_parent_location(locator): BlockUsageLocator
        '''
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT),
            'chapter', block_id='chapter1'
        )
        parent = modulestore().get_parent_location(locator)
        assert parent is not None
        assert parent.block_id == 'head12345'
        assert parent.org == 'testx'
        assert parent.course == 'GreekHero'
        locator = locator.course_key.make_usage_key('chapter', 'chapter2')
        parent = modulestore().get_parent_location(locator)
        assert parent is not None
        assert parent.block_id == 'head12345'
        locator = locator.course_key.make_usage_key('garbage', 'nosuchblock')
        parent = modulestore().get_parent_location(locator)
        assert parent is None

    def test_get_children(self):
        """
        Test the existing get_children method on xblocks
        """
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'course', 'head12345'
        )
        block = modulestore().get_item(locator)
        children = block.get_children()
        expected_ids = [
            "chapter1", "chap", "chapter2", "chapter3"
        ]
        for child in children:
            assert child.category == 'chapter'
            assert child.location.block_id in expected_ids
            expected_ids.remove(child.location.block_id)
        assert len(expected_ids) == 0


def version_agnostic(children):
    """
    children: list of blocks
    Returns the `children` list with each member version-agnostic
    """
    return [child.version_agnostic() for child in children]


class TestItemCrud(SplitModuleTest):
    """
    Test create update and delete of items
    """
    # DHM do I need to test this case which I believe won't work:
    #  1) fetch a course and some of its blocks
    #  2) do a series of CRUD operations on those previously fetched elements
    # The problem here will be that the version_guid of the items will be the version at time of fetch.
    # Each separate save will change the head version; so, the 2nd piecemeal change will flag the version
    # conflict. That is, if versions are v0..vn and start as v0 in initial fetch, the first CRUD op will
    # say it's changing an object from v0, splitMongo will process it and make the current head v1, the next
    # crud op will pass in its v0 element and splitMongo will flag the version conflict.
    # What I don't know is how realistic this test is and whether to wrap the modulestore with a higher level
    # transactional operation which manages the version change or make the threading cache reason out whether or
    # not the changes are independent and additive and thus non-conflicting.
    # A use case I expect is
    # (client) change this metadata
    # (server) done, here's the new info which, btw, updates the course version to v1
    # (client) add these children to this other node (which says it came from v0 or
    #          will the client have refreshed the version before doing the op?)
    # In this case, having a server side transactional model won't help b/c the bug is a long-transaction on the
    # on the client where it would be a mistake for the server to assume anything about client consistency. The best
    # the server could do would be to see if the parent's children changed at all since v0.

    def test_create_minimal_item(self):
        """
        create_item(user, location, category, definition_locator=None, fields): new_desciptor
        """
        # grab link to course to ensure new versioning works
        locator = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        premod_course = modulestore().get_course(locator)
        premod_history = modulestore().get_course_history_info(locator)
        # add minimal one w/o a parent
        category = 'sequential'
        new_block = modulestore().create_item(
            'user123', locator, category,
            fields={'display_name': 'new sequential'}
        )
        # check that course version changed and course's previous is the other one
        assert new_block.location.course == 'GreekHero'
        assert new_block.location.version_guid != premod_course.location.version_guid
        assert locator.version_guid is None,\
            'Version inadvertently filled in'  # lint-amnesty, pylint: disable=no-member
        current_course = modulestore().get_course(locator)
        assert new_block.location.version_guid == current_course.location.version_guid

        history_info = modulestore().get_course_history_info(current_course.location.course_key)
        assert history_info['previous_version'] == premod_course.location.version_guid
        assert history_info['original_version'] == premod_history['original_version']
        assert history_info['edited_by'] == 'user123'
        # check block's info: category, definition_locator, and display_name
        assert new_block.category == 'sequential'
        assert new_block.definition_locator is not None
        assert new_block.display_name == 'new sequential'
        # check that block does not exist in previous version
        locator = new_block.location.map_into_course(
            CourseLocator(version_guid=premod_course.location.version_guid)
        )
        with pytest.raises(ItemNotFoundError):
            modulestore().get_item(locator)

    def test_create_parented_item(self):
        """
        Test create_item w/ specifying the parent of the new item
        """
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT),
            'chapter', block_id='chapter2'
        )
        original = modulestore().get_item(locator)

        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_DRAFT), 'course', 'head23456'
        )
        premod_course = modulestore().get_course(locator.course_key)
        category = 'chapter'
        new_block = modulestore().create_child(
            'user123', locator, category,
            fields={'display_name': 'new chapter'},
            definition_locator=original.definition_locator
        )
        # check that course version changed and course's previous is the other one
        assert new_block.location.version_guid != premod_course.location.version_guid
        parent = modulestore().get_item(locator)
        assert new_block.location.version_agnostic() in version_agnostic(parent.children)
        assert new_block.definition_locator.definition_id == original.definition_locator.definition_id

    def test_unique_naming(self):
        """
        Check that 2 blocks of same type get unique block_ids. Also check that if creation provides
        a definition id and new def data that it branches the definition in the db.
        Actually, this tries to test all create_item features not tested above.
        """
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT),
            'problem', block_id='problem1'
        )
        original = modulestore().get_item(locator)

        locator = BlockUsageLocator(
            CourseLocator(org='guestx', course='contender', run="run", branch=BRANCH_NAME_DRAFT), 'course', 'head345679'
        )
        category = 'problem'
        new_payload = "<problem>empty</problem>"
        new_block = modulestore().create_child(
            'anotheruser', locator, category,
            fields={'display_name': 'problem 1', 'data': new_payload},
        )
        another_payload = "<problem>not empty</problem>"
        another_block = modulestore().create_child(
            'anotheruser', locator, category,
            fields={'display_name': 'problem 2', 'data': another_payload},
            definition_locator=original.definition_locator,
        )
        # check that course version changed and course's previous is the other one
        parent = modulestore().get_item(locator)
        assert new_block.location.block_id != another_block.location.block_id
        assert new_block.location.version_agnostic() in version_agnostic(parent.children)
        assert another_block.location.version_agnostic() in version_agnostic(parent.children)
        assert new_block.data == new_payload
        assert another_block.data == another_payload
        # check definition histories
        new_history = modulestore().get_definition_history_info(new_block.definition_locator)
        assert new_history['previous_version'] is None
        assert new_history['original_version'] == new_block.definition_locator.definition_id
        assert new_history['edited_by'] == 'anotheruser'
        another_history = modulestore().get_definition_history_info(another_block.definition_locator)
        assert another_history['previous_version'] == original.definition_locator.definition_id

    def test_encoded_naming(self):
        """
        Check that using odd characters in block id don't break ability to add and retrieve block.
        """
        course_key = CourseLocator(org='guestx', course='contender', run="run", branch=BRANCH_NAME_DRAFT)
        parent_locator = BlockUsageLocator(course_key, 'course', block_id="head345679")
        chapter_locator = BlockUsageLocator(course_key, 'chapter', block_id="foo.bar_-~:0")
        modulestore().create_child(
            'anotheruser', parent_locator, 'chapter',
            block_id=chapter_locator.block_id,
            fields={'display_name': 'chapter 99'},
        )
        # check that course version changed and course's previous is the other one
        new_block = modulestore().get_item(chapter_locator)
        assert new_block.location.block_id == 'foo.bar_-~:0'
        # hardcode to ensure BUL init didn't change
        # now try making that a parent of something
        new_payload = "<problem>empty</problem>"
        problem_locator = BlockUsageLocator(course_key, 'problem', block_id="prob.bar_-~:99a")
        modulestore().create_child(
            'anotheruser', chapter_locator, 'problem',
            block_id=problem_locator.block_id,
            fields={'display_name': 'chapter 99', 'data': new_payload},
        )
        # check that course version changed and course's previous is the other one
        new_block = modulestore().get_item(problem_locator)
        assert new_block.location.block_id == problem_locator.block_id
        chapter = modulestore().get_item(chapter_locator)
        assert problem_locator in version_agnostic(chapter.children)

    def test_create_bulk_operations(self):
        """
        Test create_item using bulk_operations
        """
        # start transaction w/ simple creation
        user = random.getrandbits(32)
        course_key = CourseLocator('test_org', 'test_transaction', 'test_run')
        with modulestore().bulk_operations(course_key):
            new_course = modulestore().create_course('test_org', 'test_transaction', 'test_run', user, BRANCH_NAME_DRAFT)  # lint-amnesty, pylint: disable=line-too-long
            new_course_locator = new_course.id
            index_history_info = modulestore().get_course_history_info(new_course.location.course_key)
            course_block_prev_version = new_course.previous_version
            course_block_update_version = new_course.update_version
            assert new_course_locator.version_guid is not None, 'Want to test a definite version'
            versionless_course_locator = new_course_locator.version_agnostic()

            # positive simple case: no force, add chapter
            new_ele = modulestore().create_child(
                user, new_course.location, 'chapter',
                fields={'display_name': 'chapter 1'},
            )
            # version info shouldn't change
            assert new_ele.update_version == course_block_update_version
            assert new_ele.update_version == new_ele.location.version_guid
            refetch_course = modulestore().get_course(versionless_course_locator)
            assert refetch_course.location.version_guid == new_course.location.version_guid
            assert refetch_course.previous_version == course_block_prev_version
            assert refetch_course.update_version == course_block_update_version
            refetch_index_history_info = modulestore().get_course_history_info(refetch_course.location.course_key)
            assert refetch_index_history_info == index_history_info
            assert new_ele.location.version_agnostic() in version_agnostic(refetch_course.children)

            # try to create existing item
            with pytest.raises(DuplicateItemError):
                _fail = modulestore().create_child(
                    user, new_course.location, 'chapter',
                    block_id=new_ele.location.block_id,
                    fields={'display_name': 'chapter 2'},
                )

        # start a new transaction
        with modulestore().bulk_operations(course_key):
            new_ele = modulestore().create_child(
                user, new_course.location, 'chapter',
                fields={'display_name': 'chapter 2'},
            )
            transaction_guid = new_ele.location.version_guid
            # ensure force w/ continue gives exception
            with pytest.raises(VersionConflictError):
                _fail = modulestore().create_child(
                    user, new_course.location, 'chapter',
                    fields={'display_name': 'chapter 2'},
                    force=True
                )

            # ensure trying to continue the old one gives exception
            with pytest.raises(VersionConflictError):
                _fail = modulestore().create_child(
                    user, new_course.location, 'chapter',
                    fields={'display_name': 'chapter 3'},
                )

            # add new child to old parent in continued (leave off version_guid)
            course_block_locator = new_course.location.version_agnostic()
            new_ele = modulestore().create_child(
                user, course_block_locator, 'chapter',
                fields={'display_name': 'chapter 4'},
            )
            assert new_ele.update_version != course_block_update_version
            assert new_ele.location.version_guid == transaction_guid

            # check children, previous_version
            refetch_course = modulestore().get_course(versionless_course_locator)
            assert new_ele.location.version_agnostic() in version_agnostic(refetch_course.children)
            assert refetch_course.previous_version == course_block_update_version
            assert refetch_course.update_version == transaction_guid

    def test_bulk_ops_org_filtering(self):
        """
        Make sure of proper filtering when using bulk operations and
        calling get_courses with an 'org' filter
        """

        # start transaction w/ simple creation
        user = random.getrandbits(32)
        course_key = CourseLocator('test_org', 'test_transaction', 'test_run')
        with modulestore().bulk_operations(course_key):
            modulestore().create_course('test_org', 'test_transaction', 'test_run', user, BRANCH_NAME_DRAFT)

            courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT, org='test_org')
            assert len(courses) == 1
            assert courses[0].id.org == course_key.org
            assert courses[0].id.course == course_key.course
            assert courses[0].id.run == course_key.run

            courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT, org='other_org')
            assert len(courses) == 0

        # re-assert after the end of the with scope
        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT, org='test_org')
        assert len(courses) == 1
        assert courses[0].id.org == course_key.org
        assert courses[0].id.course == course_key.course
        assert courses[0].id.run == course_key.run

        courses = modulestore().get_courses(branch=BRANCH_NAME_DRAFT, org='other_org')
        assert len(courses) == 0

    def test_update_metadata(self):
        """
        test updating an items metadata ensuring the definition doesn't version but the course does if it should
        """
        locator = BlockUsageLocator(
            CourseLocator(org="testx", course="GreekHero", run="run", branch=BRANCH_NAME_DRAFT),
            'problem', block_id="problem3_2"
        )
        problem = modulestore().get_item(locator)
        pre_def_id = problem.definition_locator.definition_id
        pre_version_guid = problem.location.version_guid
        assert pre_def_id is not None
        assert pre_version_guid is not None
        assert problem.max_attempts != 4, 'Invalidates rest of test'

        problem.max_attempts = 4
        problem.save()  # decache above setting into the kvs
        updated_problem = modulestore().update_item(problem, self.user_id)
        # check that course version changed and course's previous is the other one
        assert updated_problem.definition_locator.definition_id == pre_def_id
        assert updated_problem.location.version_guid != pre_version_guid
        assert updated_problem.max_attempts == 4
        # refetch to ensure original didn't change
        original_location = problem.location.map_into_course(CourseLocator(version_guid=pre_version_guid))
        problem = modulestore().get_item(original_location)
        assert problem.max_attempts != 4, 'original changed'

        current_course = modulestore().get_course(locator.course_key)
        assert updated_problem.location.version_guid == current_course.location.version_guid

        history_info = modulestore().get_course_history_info(current_course.location.course_key)
        assert history_info['previous_version'] == pre_version_guid
        assert history_info['edited_by'] == self.user_id

    def test_update_children(self):
        """
        test updating an item's children ensuring the definition doesn't version but the course does if it should
        """
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'chapter', 'chapter3'
        )
        block = modulestore().get_item(locator)
        pre_def_id = block.definition_locator.definition_id
        pre_version_guid = block.location.version_guid

        # reorder children
        assert len(block.children) > 0, 'meaningless test'
        moved_child = block.children.pop()
        block.save()  # decache model changes
        updated_problem = modulestore().update_item(block, self.user_id)
        # check that course version changed and course's previous is the other one
        assert updated_problem.definition_locator.definition_id == pre_def_id
        assert updated_problem.location.version_guid != pre_version_guid
        assert version_agnostic(updated_problem.children) == version_agnostic(block.children)
        assert moved_child not in version_agnostic(updated_problem.children)
        locator = locator.course_key.make_usage_key('chapter', "chapter1")
        other_block = modulestore().get_item(locator)
        other_block.children.append(moved_child)
        other_updated = modulestore().update_item(other_block, self.user_id)
        assert moved_child.version_agnostic() in version_agnostic(other_updated.children)

    def test_update_definition(self):
        """
        test updating an item's definition: ensure it gets versioned as well as the course getting versioned
        """
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'course', 'head12345'
        )
        block = modulestore().get_item(locator)
        pre_def_id = block.definition_locator.definition_id
        pre_version_guid = block.location.version_guid

        block.grading_policy['GRADER'][0]['min_count'] = 13
        block.save()  # decache model changes
        updated_block = modulestore().update_item(block, self.user_id)

        assert updated_block.definition_locator.definition_id != pre_def_id
        assert updated_block.location.version_guid != pre_version_guid
        assert updated_block.grading_policy['GRADER'][0]['min_count'] == 13

    def test_update_manifold(self):
        """
        Test updating metadata, children, and definition in a single call ensuring all the versioning occurs
        """
        locator = BlockUsageLocator(
            CourseLocator('testx', 'GreekHero', 'run', branch=BRANCH_NAME_DRAFT),
            'problem', block_id='problem1'
        )
        original = modulestore().get_item(locator)
        # first add 2 children to the course for the update to manipulate
        locator = BlockUsageLocator(
            CourseLocator('guestx', 'contender', 'run', branch=BRANCH_NAME_DRAFT),
            'course', block_id="head345679"
        )
        category = 'problem'
        new_payload = "<problem>empty</problem>"
        modulestore().create_child(
            'test_update_manifold', locator, category,
            fields={'display_name': 'problem 1', 'data': new_payload},
        )
        another_payload = "<problem>not empty</problem>"
        modulestore().create_child(
            'test_update_manifold', locator, category,
            fields={'display_name': 'problem 2', 'data': another_payload},
            definition_locator=original.definition_locator,
        )
        # pylint: disable=protected-access
        modulestore()._clear_cache()

        # now begin the test
        block = modulestore().get_item(locator)
        pre_def_id = block.definition_locator.definition_id
        pre_version_guid = block.location.version_guid

        assert block.grading_policy['GRADER'][0]['min_count'] != 13
        block.grading_policy['GRADER'][0]['min_count'] = 13
        block.children = block.children[1:] + [block.children[0]]
        block.advertised_start = "Soon"

        block.save()  # decache model changes
        updated_block = modulestore().update_item(block, self.user_id)
        assert updated_block.definition_locator.definition_id != pre_def_id
        assert updated_block.location.version_guid != pre_version_guid
        assert updated_block.grading_policy['GRADER'][0]['min_count'] == 13
        assert updated_block.children[0].version_agnostic() == block.children[0].version_agnostic()
        assert updated_block.advertised_start == 'Soon'

    def test_delete_item(self):
        course = self.create_course_for_deletion()
        with pytest.raises(ValueError):
            modulestore().delete_item(course.location, self.user_id)
        reusable_location = course.id.version_agnostic().for_branch(BRANCH_NAME_DRAFT)

        # delete a leaf
        problems = modulestore().get_items(reusable_location, qualifiers={'category': 'problem'})
        locn_to_del = problems[0].location
        new_course_loc = modulestore().delete_item(locn_to_del, self.user_id)
        deleted = locn_to_del.version_agnostic()
        assert not modulestore().has_item(deleted)
        with pytest.raises(VersionConflictError):
            modulestore().has_item(locn_to_del)
        with pytest.raises(ValueError):
            modulestore().delete_item(deleted, self.user_id)

        assert modulestore().has_item(locn_to_del.course_agnostic())
        assert new_course_loc.version_guid != course.location.version_guid

        # delete a subtree
        nodes = modulestore().get_items(reusable_location, qualifiers={'category': 'chapter'})
        new_course_loc = modulestore().delete_item(nodes[0].location, self.user_id)
        # check subtree

        def check_subtree(node):
            """
            Check contents of subtree recursively
            """
            if node:
                node_loc = node.location
                assert not modulestore().has_item(node_loc.version_agnostic())
                assert modulestore().has_item(node_loc.course_agnostic())
                if node.has_children:
                    for sub in node.get_children():
                        check_subtree(sub)
        check_subtree(nodes[0])

    def create_course_for_deletion(self):
        """
        Create a course we can delete
        """
        course = modulestore().create_course('nihilx', 'deletion', 'run', TEST_USER_ID, BRANCH_NAME_DRAFT)
        root = course.location.version_agnostic().for_branch(BRANCH_NAME_DRAFT)
        for _ in range(4):
            self.create_subtree_for_deletion(root, ['chapter', 'vertical', 'problem'])
        return modulestore().get_item(root)

    def create_subtree_for_deletion(self, parent, category_queue):
        """
        Create a subtree in the tb deleted course
        """
        if not category_queue:
            return
        node = modulestore().create_child(
            TEST_USER_ID, parent.version_agnostic(), category_queue[0]
        )
        node_loc = node.location.map_into_course(parent.course_key)
        for _ in range(4):
            self.create_subtree_for_deletion(node_loc, category_queue[1:])

    def test_split_modulestore_create_child_with_position(self):
        """
        This test is designed to hit a specific set of use cases having to do with
        the child positioning logic found in split_mongo/split.py:create_child()
        """
        # Set up the split module store
        store = modulestore()
        user = random.getrandbits(32)
        course_key = CourseLocator('test_org', 'test_transaction', 'test_run')
        with store.bulk_operations(course_key):
            new_course = store.create_course('test_org', 'test_transaction', 'test_run', user, BRANCH_NAME_DRAFT)
            new_course_locator = new_course.id
            versionless_course_locator = new_course_locator.version_agnostic()
            first_child = store.create_child(
                self.user_id,
                new_course.location,
                "chapter"
            )
            refetch_course = store.get_course(versionless_course_locator)
            second_child = store.create_child(
                self.user_id,
                refetch_course.location,
                "chapter",
                position=0
            )

            # First child should have been moved to second position, and better child takes the lead
            refetch_course = store.get_course(versionless_course_locator)
            children = refetch_course.get_children()
            assert str(children[1].location) == str(first_child.location)
            assert str(children[0].location) == str(second_child.location)

            # Clean up the data so we don't break other tests which apparently expect a particular state
            store.delete_course(refetch_course.id, user)


class TestCourseCreation(SplitModuleTest):
    """
    Test create_course
    """
    def test_simple_creation(self):
        """
        The simplest case but probing all expected results from it.
        """
        # Oddly getting differences of 200nsec
        new_course = modulestore().create_course(
            'test_org', 'test_course', 'test_run', TEST_USER_ID, BRANCH_NAME_DRAFT
        )
        new_locator = new_course.location
        # check index entry
        index_info = modulestore().get_course_index_info(new_locator.course_key)
        assert index_info['org'] == 'test_org'
        assert index_info['edited_by'] == TEST_USER_ID
        # check structure info
        structure_info = modulestore().get_course_history_info(new_locator.course_key)
        assert structure_info['original_version'] == index_info['versions'][BRANCH_NAME_DRAFT]
        assert structure_info['previous_version'] is None

        assert structure_info['edited_by'] == TEST_USER_ID
        # check the returned course object
        assert isinstance(new_course, CourseBlock)
        assert new_course.category == 'course'
        assert not new_course.show_calculator
        assert new_course.allow_anonymous
        assert len(new_course.children) == 0
        assert new_course.edited_by == TEST_USER_ID
        assert len(new_course.grading_policy['GRADER']) == 4
        self.assertDictEqual(new_course.grade_cutoffs, {"Pass": 0.5})

    def test_cloned_course(self):
        """
        Test making a course which points to an existing draft and published but not making any changes to either.
        """
        original_locator = CourseLocator(org='testx', course='wonderful', run="run", branch=BRANCH_NAME_DRAFT)
        original_index = modulestore().get_course_index_info(original_locator)
        new_draft = modulestore().create_course(
            'best', 'leech', 'leech_run', TEST_OTHER_USER_ID, BRANCH_NAME_DRAFT,
            versions_dict=original_index['versions'])
        new_draft_locator = new_draft.location
        self.assertRegex(new_draft_locator.org, 'best')
        # the edited_by and other meta fields on the new course will be the original author not this one
        assert new_draft.edited_by == TEST_USER_ID
        assert new_draft_locator.version_guid == original_index['versions'][BRANCH_NAME_DRAFT]
        # however the edited_by and other meta fields on course_index will be this one
        new_index = modulestore().get_course_index_info(new_draft_locator.course_key)
        assert new_index['edited_by'] == TEST_OTHER_USER_ID

        new_published_locator = new_draft_locator.course_key.for_branch(BRANCH_NAME_PUBLISHED)
        new_published = modulestore().get_course(new_published_locator)
        assert new_published.edited_by == TEST_USER_ID
        assert new_published.location.version_guid == original_index['versions'][BRANCH_NAME_PUBLISHED]

        # changing this course will not change the original course
        # using new_draft.location will insert the chapter under the course root
        new_item = modulestore().create_child(
            TEST_OTHER_USER_ID, new_draft.location, 'chapter',
            fields={'display_name': 'new chapter'}
        )
        new_draft_locator = new_draft_locator.course_key.version_agnostic()
        new_index = modulestore().get_course_index_info(new_draft_locator)
        assert new_index['versions'][BRANCH_NAME_DRAFT] != original_index['versions'][BRANCH_NAME_DRAFT]
        new_draft = modulestore().get_course(new_draft_locator)
        assert new_item.edited_by == TEST_OTHER_USER_ID
        assert new_item.location.version_guid != original_index['versions'][BRANCH_NAME_DRAFT]
        assert new_draft.location.version_guid != original_index['versions'][BRANCH_NAME_DRAFT]
        structure_info = modulestore().get_course_history_info(new_draft_locator)
        assert structure_info['edited_by'] == TEST_OTHER_USER_ID

        original_course = modulestore().get_course(original_locator)
        assert original_course.location.version_guid == original_index['versions'][BRANCH_NAME_DRAFT]

    def test_derived_course(self):
        """
        Create a new course which overrides metadata and course_data
        """
        original_locator = CourseLocator(org='guestx', course='contender', run="run", branch=BRANCH_NAME_DRAFT)
        original = modulestore().get_course(original_locator)
        original_index = modulestore().get_course_index_info(original_locator)
        fields = {
            'grading_policy': original.grading_policy,
            'display_name': 'Derivative',
        }
        fields['grading_policy']['GRADE_CUTOFFS'] = {'A': .9, 'B': .8, 'C': .65}
        new_draft = modulestore().create_course(
            'counter', 'leech', 'leech_run', TEST_OTHER_USER_ID, BRANCH_NAME_DRAFT,
            versions_dict={BRANCH_NAME_DRAFT: original_index['versions'][BRANCH_NAME_DRAFT]},
            fields=fields
        )
        new_draft_locator = new_draft.location
        self.assertRegex(new_draft_locator.org, 'counter')
        # the edited_by and other meta fields on the new course will be the original author not this one
        assert new_draft.edited_by == TEST_OTHER_USER_ID
        assert new_draft_locator.version_guid != original_index['versions'][BRANCH_NAME_DRAFT]
        # however the edited_by and other meta fields on course_index will be this one
        new_index = modulestore().get_course_index_info(new_draft_locator.course_key)
        assert new_index['edited_by'] == TEST_OTHER_USER_ID
        assert new_draft.display_name == fields['display_name']
        self.assertDictEqual(
            new_draft.grading_policy['GRADE_CUTOFFS'],
            fields['grading_policy']['GRADE_CUTOFFS']
        )

    def test_update_course_index(self):
        """
        Test the versions pointers. NOTE: you can change the org, course, or other things, but
        it's not clear how you'd find them again or associate them w/ existing student history since
        we use course_key so many places as immutable.
        """
        locator = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        course_info = modulestore().get_course_index_info(locator)

        # an allowed but not necessarily recommended way to revert the draft version
        head_course = modulestore().get_course(locator)
        versions = course_info['versions']
        versions[BRANCH_NAME_DRAFT] = head_course.previous_version
        modulestore().update_course_index(None, course_info)
        course = modulestore().get_course(locator)
        assert course.location.version_guid == versions[BRANCH_NAME_DRAFT]

        # an allowed but not recommended way to publish a course
        versions[BRANCH_NAME_PUBLISHED] = versions[BRANCH_NAME_DRAFT]
        modulestore().update_course_index(None, course_info)
        course = modulestore().get_course(locator.for_branch(BRANCH_NAME_PUBLISHED))
        assert course.location.version_guid == versions[BRANCH_NAME_DRAFT]

    def test_create_with_root(self):
        """
        Test create_course with a specified root id and category
        """
        user = random.getrandbits(32)
        new_course = modulestore().create_course(
            'test_org', 'test_transaction', 'test_run', user, BRANCH_NAME_DRAFT,
            root_block_id='top', root_category='chapter'
        )
        assert new_course.location.block_id == 'top'
        assert new_course.category == 'chapter'
        # look at db to verify
        db_structure = modulestore().db_connection.get_structure(
            new_course.location.as_object_id(new_course.location.version_guid)
        )
        assert db_structure is not None, "Didn't find course"
        assert BlockKey('course', 'course') not in db_structure['blocks']
        assert BlockKey('chapter', 'top') in db_structure['blocks']
        assert db_structure['blocks'][BlockKey('chapter', 'top')].block_type == 'chapter'

    def test_create_id_dupe(self):
        """
        Test create_course rejects duplicate id
        """
        user = random.getrandbits(32)
        courses = modulestore().get_courses(BRANCH_NAME_DRAFT)
        with pytest.raises(DuplicateCourseError):
            dupe_course_key = courses[0].location.course_key
            modulestore().create_course(
                dupe_course_key.org, dupe_course_key.course, dupe_course_key.run, user, BRANCH_NAME_DRAFT
            )

    def test_bulk_ops_get_courses(self):
        """
        Test get_courses when some are created, updated, and deleted w/in a bulk operation
        """
        # create 3 courses before bulk operation
        split_store = modulestore()

        user = random.getrandbits(32)
        to_be_created = split_store.make_course_key('new', 'created', 'course')
        with split_store.bulk_operations(to_be_created):
            split_store.create_course(
                to_be_created.org, to_be_created.course, to_be_created.run, user, master_branch=BRANCH_NAME_DRAFT,
            )

            modified_course_loc = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
            with split_store.bulk_operations(modified_course_loc):
                modified_course = modulestore().get_course(modified_course_loc)
                modified_course.advertised_start = 'coming soon to a theater near you'
                split_store.update_item(modified_course, user)

                to_be_deleted = split_store.make_course_key("guestx", "contender", "run")
                with split_store.bulk_operations(to_be_deleted):
                    split_store.delete_course(to_be_deleted, user)

                    # now get_courses
                    courses = split_store.get_courses(BRANCH_NAME_DRAFT)

                    assert len(courses) == 3
                    course_ids = [course.id.for_branch(None) for course in courses]
                    assert to_be_deleted not in course_ids
                    assert to_be_created in course_ids
                    fetched_modified = [course for course in courses if course.id == modified_course_loc][0]
                    assert fetched_modified.advertised_start == modified_course.advertised_start


class TestInheritance(SplitModuleTest):
    """
    Test the metadata inheritance mechanism.
    """
    def test_inheritance(self):
        """
        The actual test
        """
        # Note, not testing value where defined (course) b/c there's no
        # defined accessor for it on CourseBlock.
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'problem', 'problem3_2'
        )
        node = modulestore().get_item(locator)
        # inherited
        assert node.graceperiod == datetime.timedelta(hours=2)
        locator = BlockUsageLocator(
            CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'problem', 'problem1'
        )
        node = modulestore().get_item(locator)
        # overridden
        assert node.graceperiod == datetime.timedelta(hours=4)

    def test_inheritance_not_saved(self):
        """
        Was saving inherited settings with updated blocks causing inheritance to be sticky
        """
        # set on parent, retrieve child, verify setting
        chapter = modulestore().get_item(
            BlockUsageLocator(
                CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'chapter', 'chapter3'  # lint-amnesty, pylint: disable=line-too-long
            )
        )
        problem = modulestore().get_item(
            BlockUsageLocator(
                CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT), 'problem', 'problem3_2'  # lint-amnesty, pylint: disable=line-too-long
            )
        )
        assert not problem.visible_to_staff_only

        chapter.visible_to_staff_only = True
        modulestore().update_item(chapter, self.user_id)
        problem = modulestore().get_item(problem.location.version_agnostic())
        assert problem.visible_to_staff_only

        # unset on parent, retrieve child, verify unset
        chapter = modulestore().get_item(chapter.location.version_agnostic())
        del chapter.visible_to_staff_only
        modulestore().update_item(chapter, self.user_id)

        problem = modulestore().get_item(problem.location.version_agnostic())
        assert not problem.visible_to_staff_only

    def test_dynamic_inheritance(self):
        """
        Test inheritance for create_item with and without a parent pointer
        """
        course_key = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        chapter = modulestore().get_item(BlockUsageLocator(course_key, 'chapter', 'chapter3'))

        chapter.visible_to_staff_only = True
        orphan_problem = modulestore().create_item(self.user_id, course_key, 'problem')
        assert not orphan_problem.visible_to_staff_only
        parented_problem = modulestore().create_child(self.user_id, chapter.location.version_agnostic(), 'problem')  # lint-amnesty, pylint: disable=unused-variable
        # FIXME LMS-11376
#         self.assertTrue(parented_problem.visible_to_staff_only)

        orphan_problem = modulestore().create_xblock(chapter.runtime, course_key, 'problem')
        assert not orphan_problem.visible_to_staff_only
        parented_problem = modulestore().create_xblock(chapter.runtime, course_key, 'problem', parent_xblock=chapter)
        # FIXME LMS-11376
#         self.assertTrue(parented_problem.visible_to_staff_only)


class TestPublish(SplitModuleTest):
    """
    Test the publishing api
    """
    def test_publish_safe(self):
        """
        Test the standard patterns: publish to new branch, revise and publish
        """
        source_course = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        dest_course = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_PUBLISHED)
        head = source_course.make_usage_key('course', "head12345")
        chapter1 = source_course.make_usage_key('chapter', 'chapter1')
        chapter2 = source_course.make_usage_key('chapter', 'chapter2')
        chapter3 = source_course.make_usage_key('chapter', 'chapter3')
        modulestore().copy(self.user_id, source_course, dest_course, [head], [chapter2, chapter3])
        expected = [BlockKey.from_usage_key(head), BlockKey.from_usage_key(chapter1)]
        unexpected = [
            BlockKey.from_usage_key(chapter2),
            BlockKey.from_usage_key(chapter3),
            BlockKey("problem", "problem1"),
            BlockKey("problem", "problem3_2")
        ]
        self._check_course(source_course, dest_course, expected, unexpected)
        # add a child under chapter1
        new_block = modulestore().create_child(
            self.user_id, chapter1, "sequential",
            fields={'display_name': 'new sequential'},
        )
        # remove chapter1 from expected b/c its pub'd version != the source anymore since source changed
        expected.remove(BlockKey.from_usage_key(chapter1))
        # check that it's not in published course
        with pytest.raises(ItemNotFoundError):
            modulestore().get_item(new_block.location.map_into_course(dest_course))
        # publish it
        modulestore().copy(self.user_id, source_course, dest_course, [new_block.location], None)
        expected.append(BlockKey.from_usage_key(new_block.location))
        # check that it is in the published course and that its parent is the chapter
        pub_block = modulestore().get_item(new_block.location.map_into_course(dest_course))
        assert modulestore().get_parent_location(pub_block.location).block_id == chapter1.block_id
        # ensure intentionally orphaned blocks work (e.g., course_info)
        new_block = modulestore().create_item(
            self.user_id, source_course, "course_info", block_id="handouts"
        )
        # publish it
        modulestore().copy(self.user_id, source_course, dest_course, [new_block.location], None)
        expected.append(BlockKey.from_usage_key(new_block.location))
        # check that it is in the published course (no error means it worked)
        pub_block = modulestore().get_item(new_block.location.map_into_course(dest_course))
        self._check_course(source_course, dest_course, expected, unexpected)

    def test_exceptions(self):
        """
        Test the exceptions which preclude successful publication
        """
        source_course = CourseLocator(org='testx', course='GreekHero', run="run", branch=BRANCH_NAME_DRAFT)
        # destination does not exist
        destination_course = CourseLocator(org='fake', course='Unknown', run="run", branch=BRANCH_NAME_PUBLISHED)
        head = source_course.make_usage_key('course', "head12345")
        chapter3 = source_course.make_usage_key('chapter', 'chapter3')
        problem1 = source_course.make_usage_key('problem', 'problem1')
        with pytest.raises(ItemNotFoundError):
            modulestore().copy(self.user_id, source_course, destination_course, [chapter3], None)
        # publishing into a new branch w/o publishing the root
        destination_course = CourseLocator(org='testx', course='GreekHero', run='run', branch=BRANCH_NAME_PUBLISHED)
        with pytest.raises(ItemNotFoundError):
            modulestore().copy(self.user_id, source_course, destination_course, [chapter3], None)
        # publishing a subdag w/o the parent already in course
        modulestore().copy(self.user_id, source_course, destination_course, [head], [chapter3])
        with pytest.raises(ItemNotFoundError):
            modulestore().copy(self.user_id, source_course, destination_course, [problem1], [])

    def test_move_delete(self):
        """
        Test publishing moves and deletes.
        """
        source_course = CourseLocator(org='testx', course='GreekHero', run='run', branch=BRANCH_NAME_DRAFT)
        dest_course = CourseLocator(org='testx', course='GreekHero', run='run', branch=BRANCH_NAME_PUBLISHED)
        head = source_course.make_usage_key('course', "head12345")
        chapter2 = source_course.make_usage_key('chapter', 'chapter2')
        problem1 = source_course.make_usage_key('problem', 'problem1')
        modulestore().copy(self.user_id, source_course, dest_course, [head], [chapter2])
        expected = [
            BlockKey("course", "head12345"),
            BlockKey("chapter", "chapter1"),
            BlockKey("chapter", "chapter3"),
            BlockKey("problem", "problem1"),
            BlockKey("problem", "problem3_2"),
        ]
        self._check_course(source_course, dest_course, expected, [BlockKey("chapter", "chapter2")])
        # now move problem1 and delete problem3_2
        chapter1 = modulestore().get_item(source_course.make_usage_key("chapter", "chapter1"))
        chapter3 = modulestore().get_item(source_course.make_usage_key("chapter", "chapter3"))
        chapter1.children.append(problem1)
        chapter3.children.remove(problem1.map_into_course(chapter3.location.course_key))
        modulestore().delete_item(source_course.make_usage_key("problem", "problem3_2"), self.user_id)
        modulestore().copy(self.user_id, source_course, dest_course, [head], [chapter2])
        expected = [
            BlockKey("course", "head12345"),
            BlockKey("chapter", "chapter1"),
            BlockKey("chapter", "chapter3"),
            BlockKey("problem", "problem1")
        ]
        self._check_course(source_course, dest_course, expected, [BlockKey("chapter", "chapter2"), BlockKey("problem", "problem3_2")])  # lint-amnesty, pylint: disable=line-too-long

    def _check_course(self, source_course_loc, dest_course_loc, expected_blocks, unexpected_blocks):
        """
        Check that the course has the expected blocks and does not have the unexpected blocks
        """
        history_info = modulestore().get_course_history_info(dest_course_loc)
        assert history_info['edited_by'] == self.user_id
        for expected in expected_blocks:
            source = modulestore().get_item(source_course_loc.make_usage_key(expected.type, expected.id))
            pub_copy = modulestore().get_item(dest_course_loc.make_usage_key(expected.type, expected.id))
            # everything except previous_version & children should be the same
            assert source.category == pub_copy.category
            assert source.update_version == pub_copy.source_version,\
                f"Versions don't match for {expected}: {source.update_version} != {pub_copy.update_version}"
            assert self.user_id == pub_copy.edited_by,\
                f'{pub_copy.location} edited_by {pub_copy.edited_by} not {self.user_id}'
            for field in source.fields.values():
                if field.name == 'children':
                    self._compare_children(field.read_from(source), field.read_from(pub_copy), unexpected_blocks)
                elif isinstance(field, (Reference, ReferenceList, ReferenceValueDict)):
                    self.assertReferenceEqual(field.read_from(source), field.read_from(pub_copy))
                else:
                    assert field.read_from(source) == field.read_from(pub_copy)
        for unexp in unexpected_blocks:
            with pytest.raises(ItemNotFoundError):
                modulestore().get_item(dest_course_loc.make_usage_key(unexp.type, unexp.id))

    def assertReferenceEqual(self, expected, actual):  # lint-amnesty, pylint: disable=missing-function-docstring
        if isinstance(expected, BlockUsageLocator):
            expected = BlockKey.from_usage_key(expected)
            actual = BlockKey.from_usage_key(actual)
        elif isinstance(expected, list):
            expected = [BlockKey.from_usage_key(key) for key in expected]
            actual = [BlockKey.from_usage_key(key) for key in actual]
        elif isinstance(expected, dict):
            expected = {key: BlockKey.from_usage_key(val) for (key, val) in expected}
            actual = {key: BlockKey.from_usage_key(val) for (key, val) in actual}
        assert expected == actual

    def _compare_children(self, source_children, dest_children, unexpected):
        """
        Ensure dest_children == source_children minus unexpected
        """
        source_block_keys = [
            src_key
            for src_key
            in (BlockKey.from_usage_key(src) for src in source_children)
            if src_key not in unexpected
        ]
        dest_block_keys = [BlockKey.from_usage_key(dest) for dest in dest_children]
        for unexp in unexpected:
            assert unexp not in dest_block_keys

        assert source_block_keys == dest_block_keys


class TestSchema(SplitModuleTest):
    """
    Test the db schema (and possibly eventually migrations?)
    """
    def test_schema(self):
        """
        Test that the schema is set in each document
        """
        db_connection = modulestore().db_connection
        for collection in [db_connection.course_index, db_connection.structures, db_connection.definitions]:
            assert collection.count_documents({'schema_version': {'$exists': False}}) == 0, \
                f'{collection.name} has records without schema_version'
            assert collection.count_documents({'schema_version': {'$ne': SplitMongoModuleStore.SCHEMA_VERSION}}) == 0, \
                f'{collection.name} has records with wrong schema_version'


# ===========================================
def modulestore():
    """
    Mock the django dependent global modulestore function to disentangle tests from django
    """
    def load_function(engine_path):
        """
        Load the given engine
        """
        module_path, _, name = engine_path.rpartition('.')
        return getattr(import_module(module_path), name)

    if SplitModuleTest.modulestore is None:
        class_ = load_function(SplitModuleTest.MODULESTORE['ENGINE'])

        options = {}

        options.update(SplitModuleTest.MODULESTORE['OPTIONS'])
        options['render_template'] = render_to_template_mock

        # lint-amnesty, pylint: disable=bad-option-value, star-args
        SplitModuleTest.modulestore = class_(
            None,  # contentstore
            SplitModuleTest.MODULESTORE['DOC_STORE_CONFIG'],
            **options
        )

        SplitModuleTest.bootstrapDB(SplitModuleTest.modulestore)

    return SplitModuleTest.modulestore


# pylint: disable=unused-argument
def render_to_template_mock(*args):
    pass
