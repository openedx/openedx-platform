"""
Test the behavior of the GradesTransformer
"""


import datetime
import random
from copy import deepcopy

import ddt
import pytz

import openedx.core.djangoapps.content.block_structure.api as bs_api
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.course_blocks.transformers.tests.helpers import CourseStructureTestCase
from openedx.core.djangoapps.content.block_structure.api import clear_course_from_cache
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import check_mongo_calls_range

from ..transformer import GradesTransformer


@ddt.ddt
class GradesTransformerTestCase(CourseStructureTestCase):
    """
    Verify behavior of the GradesTransformer
    """

    TRANSFORMER_CLASS_TO_TEST = GradesTransformer

    ENABLED_SIGNALS = ['course_published']

    problem_metadata = {
        'graded': True,
        'weight': 1,
        'due': datetime.datetime(2099, 3, 15, 12, 30, 0, tzinfo=pytz.utc),
    }

    def setUp(self):
        super().setUp()
        password = 'test'
        self.student = UserFactory.create(is_staff=False, username='test_student', password=password)
        self.client.login(username=self.student.username, password=password)

    def _update_course_grading_policy(self, course, grading_policy):
        """
        Helper to update a course's grading policy in the modulestore.
        """
        course.set_grading_policy(grading_policy)
        self.update_course(course, self.user.id)

    def _validate_grading_policy_hash(self, course_location, grading_policy_hash):
        """
        Helper to retrieve the course at the given course_location and
        assert that its hashed grading policy (from the grades transformer)
        is as expected.
        """
        block_structure = get_course_blocks(self.student, course_location, self.transformers)
        self.assert_collected_transformer_block_fields(
            block_structure,
            course_location,
            self.TRANSFORMER_CLASS_TO_TEST,
            grading_policy_hash=grading_policy_hash,
        )

    def assert_collected_xblock_fields(self, block_structure, usage_key, **expectations):
        """
        Given a block structure, a block usage key, and a list of keyword
        arguments representing XBlock fields, verify that the block structure
        has the specified values for each XBlock field.
        """
        assert len(expectations) > 0
        for field in expectations:
            # Append our custom message to the default assertEqual error message
            self.longMessage = True  # pylint: disable=invalid-name
            assert expectations[field] == block_structure.get_xblock_field(usage_key, field),\
                f'in field {repr(field)},'
        assert block_structure.get_xblock_field(usage_key, 'subtree_edited_on') is not None

    def assert_collected_transformer_block_fields(self, block_structure, usage_key, transformer_class, **expectations):
        """
        Given a block structure, a block usage key, a transformer, and a list
        of keyword arguments representing transformer block fields, verify that
        the block structure has the specified values for each transformer block
        field.
        """
        assert len(expectations) > 0
        # Append our custom message to the default assertEqual error message
        self.longMessage = True
        for field in expectations:
            assert expectations[field] == block_structure.get_transformer_block_field(
                usage_key, transformer_class, field
            ), f'in {transformer_class} and field {repr(field)}'

    def build_course_with_problems(self, data='<problem></problem>', metadata=None):
        """
        Create a test course with the requested problem `data` and `metadata` values.

        Appropriate defaults are provided when either argument is omitted.
        """
        metadata = metadata or self.problem_metadata

        # Special structure-related keys start with '#'.  The rest get passed as
        # kwargs to Factory.create.  See docstring at
        # `CourseStructureTestCase.build_course` for details.
        return self.build_course([
            {
                'org': 'GradesTestOrg',
                'course': 'GB101',
                'run': 'cannonball',
                'metadata': {'format': 'homework'},
                '#type': 'course',
                '#ref': 'course',
                '#children': [
                    {
                        '#type': 'problem',
                        '#ref': 'problem',
                        'data': data,
                        **metadata,
                    }
                ]
            }
        ])

    def build_complicated_hypothetical_course(self):
        """
        Create a test course with a very odd structure as a stress-test for various methods.

        Current design is to test containing_subsection logic in collect_unioned_set_field.
        I can't reasonably draw this in ascii art (due to intentional complexities), so here's an overview:
            We have 1 course, containing 1 chapter, containing 2 subsections.

            From here, it starts to get hairy. Call our subsections A and B.
            Subsection A contains 3 verticals (call them 1, 2, and 3), and another subsection (C)
            Subsection B contains vertical 3 and subsection C
            Subsection C contains 1 problem (b)
            Vertical 1 contains 1 vertical (11)
            Vertical 2 contains no children
            Vertical 3 contains no children
            Vertical 11 contains 1 problem (aa) and vertical 2
            Problem b contains no children
        """
        return self.build_course([
            {
                'org': 'GradesTestOrg',
                'course': 'GB101',
                'run': 'cannonball',
                'metadata': {'format': 'homework'},
                '#type': 'course',
                '#ref': 'course',
                '#children': [
                    {
                        '#type': 'chapter',
                        '#ref': 'chapter',
                        '#children': [
                            {
                                '#type': 'sequential',
                                '#ref': 'sub_A',
                                '#children': [
                                    {
                                        '#type': 'vertical',
                                        '#ref': 'vert_1',
                                        '#children': [
                                            {
                                                '#type': 'vertical',
                                                '#ref': 'vert_A11',
                                                '#children': [{'#type': 'problem', '#ref': 'prob_A1aa'}]
                                            },
                                        ]
                                    },
                                    {'#type': 'vertical', '#ref': 'vert_2', '#parents': ['vert_A11']},
                                ]
                            },
                            {
                                '#type': 'sequential',
                                '#ref': 'sub_B',
                                '#children': [
                                    {'#type': 'vertical', '#ref': 'vert_3', '#parents': ['sub_A']},
                                    {
                                        '#type': 'sequential',
                                        '#ref': 'sub_C',
                                        '#parents': ['sub_A'],
                                        '#children': [{'#type': 'problem', '#ref': 'prob_BCb'}]
                                    },
                                ]
                            },
                        ]
                    }
                ]
            }
        ])

    def test_collect_containing_subsection(self):
        expected_subsections = {
            'course': set(),
            'chapter': set(),
            'sub_A': {'sub_A'},
            'sub_B': {'sub_B'},
            'sub_C': {'sub_A', 'sub_B', 'sub_C'},
            'vert_1': {'sub_A'},
            'vert_2': {'sub_A'},
            'vert_3': {'sub_A', 'sub_B'},
            'vert_A11': {'sub_A'},
            'prob_A1aa': {'sub_A'},
            'prob_BCb': {'sub_A', 'sub_B', 'sub_C'},
        }
        blocks = self.build_complicated_hypothetical_course()
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)
        for block_ref, expected_subsections in expected_subsections.items():  # noqa: B020
            actual_subsections = block_structure.get_transformer_block_field(
                blocks[block_ref].location,
                self.TRANSFORMER_CLASS_TO_TEST,
                'subsections',
            )
            assert actual_subsections == {blocks[sub].location for sub in expected_subsections}

    def test_unscored_block_collection(self):
        blocks = self.build_course_with_problems()
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)
        self.assert_collected_xblock_fields(
            block_structure,
            blocks['course'].location,
            weight=None,
            graded=False,
            has_score=False,
            due=None,
            format='homework',
        )
        self.assert_collected_transformer_block_fields(
            block_structure,
            blocks['course'].location,
            self.TRANSFORMER_CLASS_TO_TEST,
            max_score=None,
            explicit_graded=None,
        )

    def test_grades_collected_basic(self):

        blocks = self.build_course_with_problems()
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)

        self.assert_collected_xblock_fields(
            block_structure,
            blocks['problem'].location,
            weight=self.problem_metadata['weight'],
            graded=self.problem_metadata['graded'],
            has_score=True,
            due=self.problem_metadata['due'],
            format=None,
        )
        self.assert_collected_transformer_block_fields(
            block_structure,
            blocks['problem'].location,
            self.TRANSFORMER_CLASS_TO_TEST,
            max_score=0,
            explicit_graded=True,
        )

    @ddt.data(True, False, None)
    def test_graded_at_problem(self, graded):
        problem_metadata = {
            'has_score': True,
        }
        if graded is not None:
            problem_metadata['graded'] = graded
        blocks = self.build_course_with_problems(metadata=problem_metadata)
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)
        self.assert_collected_transformer_block_fields(
            block_structure,
            blocks['problem'].location,
            self.TRANSFORMER_CLASS_TO_TEST,
            explicit_graded=graded,
        )

    def test_collecting_staff_only_problem(self):
        # Demonstrate that the problem data can by collected by the SystemUser
        # even if the block has access restrictions placed on it.
        problem_metadata = {
            'graded': True,
            'weight': 1,
            'due': datetime.datetime(2016, 10, 16, 0, 4, 0, tzinfo=pytz.utc),
            'visible_to_staff_only': True,
        }

        blocks = self.build_course_with_problems(metadata=problem_metadata)
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)

        self.assert_collected_xblock_fields(
            block_structure,
            blocks['problem'].location,
            weight=problem_metadata['weight'],
            graded=problem_metadata['graded'],
            has_score=True,
            due=problem_metadata['due'],
            format=None,
        )

    def test_max_score_collection(self):
        problem_data = '''
            <problem>
                <numericalresponse answer="2">
                    <textline label="1+1" trailing_text="%" />
                </numericalresponse>
            </problem>
        '''

        blocks = self.build_course_with_problems(data=problem_data)
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)

        self.assert_collected_transformer_block_fields(
            block_structure,
            blocks['problem'].location,
            self.TRANSFORMER_CLASS_TO_TEST,
            max_score=1,
        )

    def test_max_score_for_multiresponse_problem(self):
        problem_data = '''
            <problem>
                <numericalresponse answer="27">
                    <textline label="3^3" />
                </numericalresponse>
                <numericalresponse answer="13.5">
                    <textline label="and then half of that?" />
                </numericalresponse>
            </problem>
        '''

        blocks = self.build_course_with_problems(problem_data)
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)

        self.assert_collected_transformer_block_fields(
            block_structure,
            blocks['problem'].location,
            self.TRANSFORMER_CLASS_TO_TEST,
            max_score=2,
        )

    def test_max_score_for_invalid_dropdown_problem(self):
        """
        Verify that for an invalid dropdown problem, the max score is set to zero.
        """
        problem_data = '''
        <problem>
            <optionresponse>
              <p>You can use this template as a guide to the simple editor markdown and OLX markup to use for dropdown
               problems. Edit this component to replace this template with your own assessment.</p>
            <label>Add the question text, or prompt, here. This text is required.</label>
            <description>You can add an optional tip or note related to the prompt like this. </description>
            <optioninput>
                <option correct="False">an incorrect answer</option>
                <option correct="True">the correct answer</option>
                <option correct="True">an incorrect answer</option>
              </optioninput>
            </optionresponse>
        </problem>
        '''

        blocks = self.build_course_with_problems(problem_data)
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)

        self.assert_collected_transformer_block_fields(
            block_structure,
            blocks['problem'].location,
            self.TRANSFORMER_CLASS_TO_TEST,
            max_score=0,
        )

    def test_course_version_collected_in_split(self):
        blocks = self.build_course_with_problems()
        block_structure = get_course_blocks(self.student, blocks['course'].location, self.transformers)
        assert block_structure.get_xblock_field(blocks['course'].location, 'course_version') is not None
        assert block_structure.get_xblock_field(
            blocks['problem'].location, 'course_version'
        ) == block_structure.get_xblock_field(blocks['course'].location, 'course_version')

    def test_grading_policy_collected(self):
        # the calculated hash of the original and updated grading policies of the test course
        original_grading_policy_hash = 'ChVp0lHGQGCevD0t4njna/C44zQ='
        updated_grading_policy_hash = 'TsbX04qWOy1WRnC0NHy+94upPd4='

        blocks = self.build_course_with_problems()
        course_block = blocks['course']
        self._validate_grading_policy_hash(
            course_block.location,
            original_grading_policy_hash
        )

        # make sure the hash changes when the course grading policy is edited
        grading_policy_with_updates = course_block.grading_policy
        original_grading_policy = deepcopy(grading_policy_with_updates)
        for section in grading_policy_with_updates['GRADER']:
            assert section['weight'] != 0.25
            section['weight'] = 0.25

        self._update_course_grading_policy(course_block, grading_policy_with_updates)
        self._validate_grading_policy_hash(
            course_block.location,
            updated_grading_policy_hash
        )

        # reset the grading policy and ensure the hash matches the original
        self._update_course_grading_policy(course_block, original_grading_policy)
        self._validate_grading_policy_hash(
            course_block.location,
            original_grading_policy_hash
        )


@ddt.ddt
class MultiProblemModulestoreAccessTestCase(CourseStructureTestCase, SharedModuleStoreTestCase):
    """
    Test mongo usage in GradesTransformer.
    """

    TRANSFORMER_CLASS_TO_TEST = GradesTransformer

    def setUp(self):
        super().setUp()
        password = 'test'
        self.student = UserFactory.create(is_staff=False, username='test_student', password=password)
        self.client.login(username=self.student.username, password=password)

    @ddt.data(
        (ModuleStoreEnum.Type.split, 2, 2),
    )
    @ddt.unpack
    def test_modulestore_performance(self, store_type, max_mongo_calls, min_mongo_calls):
        """
        Test that a constant number of mongo calls are made regardless of how
        many grade-related blocks are in the course.
        """
        course = [
            {
                'org': 'GradesTestOrg',
                'course': 'GB101',
                'run': 'cannonball',
                'metadata': {'format': 'homework'},
                '#type': 'course',
                '#ref': 'course',
                '#children': [],
            },
        ]
        for problem_number in range(random.randrange(10, 20)):
            course[0]['#children'].append(
                {
                    'metadata': {
                        'graded': True,
                        'weight': 1,
                        'due': datetime.datetime(2099, 3, 15, 12, 30, 0, tzinfo=pytz.utc),
                    },
                    '#type': 'problem',
                    '#ref': f'problem_{problem_number}',
                    'data': '''
                        <problem>
                            <numericalresponse answer="{number}">
                                <textline label="1*{number}" />
                            </numericalresponse>
                        </problem>'''.format(number=problem_number),  # noqa: UP032
                }
            )
        with self.store.default_store(store_type):
            blocks = self.build_course(course)
        bs_api.update_course_in_cache(blocks['course'].id)
        clear_course_from_cache(blocks['course'].id)
        with check_mongo_calls_range(max_mongo_calls, min_mongo_calls):
            get_course_blocks(self.student, blocks['course'].location, self.transformers)
