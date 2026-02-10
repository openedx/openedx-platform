"""Tests for problem weight metadata fix."""
from unittest.mock import Mock
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory
from xmodule.modulestore.inheritance import own_metadata
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import get_block_info


class ProblemWeightMetadataTestCase(ModuleStoreTestCase):
    """Test problem weight population from max_score."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def test_weight_from_max_score(self):
        """Weight should be populated from max_score when not explicitly set."""
        problem_xml = """
        <problem>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="true">A</choice>
              </choicegroup>
            </multiplechoiceresponse>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="true">B</choice>
              </choicegroup>
            </multiplechoiceresponse>
            <multiplechoiceresponse>
              <choicegroup type="MultipleChoice">
                <choice correct="true">C</choice>
              </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """

        problem = BlockFactory.create(
            parent_location=self.course.location,
            category="problem",
            data=problem_xml
        )

        self.assertNotIn('weight', own_metadata(problem))
        self.assertEqual(problem.max_score(), 3.0)

        block_info = get_block_info(problem)

        self.assertEqual(block_info['metadata']['weight'], 3.0)

    def test_explicit_weight_preserved(self):
        """Explicit weight should not be overridden."""
        problem = BlockFactory.create(
            parent_location=self.course.location,
            category="problem",
            data="<problem><multiplechoiceresponse><choicegroup type='MultipleChoice'><choice correct='true'>A</choice></choicegroup></multiplechoiceresponse></problem>",
            weight=5.0
        )

        block_info = get_block_info(problem)

        self.assertEqual(block_info['metadata']['weight'], 5.0)

    def test_zero_max_score(self):
        """Weight should not be set for zero max_score."""
        problem = BlockFactory.create(
            parent_location=self.course.location,
            category="problem",
            data="<problem><p>No questions</p></problem>"
        )

        self.assertEqual(problem.max_score(), 0)

        block_info = get_block_info(problem)

        self.assertNotIn('weight', block_info['metadata'])

    def test_non_problem_unaffected(self):
        """Non-problem blocks should not have weight added."""
        html_block = BlockFactory.create(
            parent_location=self.course.location,
            category="html",
            data="<p>Content</p>"
        )

        block_info = get_block_info(html_block)

        self.assertNotIn('weight', block_info['metadata'])

    def test_max_score_exception_handled(self):
        """Exceptions from max_score should be handled gracefully."""
        problem = BlockFactory.create(
            parent_location=self.course.location,
            category="problem",
            data="<problem><multiplechoiceresponse><choicegroup type='MultipleChoice'><choice correct='true'>A</choice></choicegroup></multiplechoiceresponse></problem>"
        )

        problem.max_score = Mock(side_effect=Exception("Test"))

        block_info = get_block_info(problem)

        self.assertIn('metadata', block_info)
