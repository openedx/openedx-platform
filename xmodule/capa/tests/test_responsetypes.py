# -*- coding: utf-8 -*-
"""
Tests of responsetypes
"""
import io
import json
import os
import textwrap
import unittest
import zipfile
from datetime import datetime
from unittest import mock

import pytest
import calc
import pyparsing
import random2 as random
import requests
from pytz import UTC

from xmodule.capa.correctmap import CorrectMap
from xmodule.capa.responsetypes import LoncapaProblemError, ResponseError, StudentInputError
from xmodule.capa.tests.helpers import load_fixture, new_loncapa_problem, mock_capa_system
from xmodule.capa.tests.response_xml_factory import (
    AnnotationResponseXMLFactory,
    ChoiceResponseXMLFactory,
    ChoiceTextResponseXMLFactory,
    CodeResponseXMLFactory,
    CustomResponseXMLFactory,
    FormulaResponseXMLFactory,
    ImageResponseXMLFactory,
    MultipleChoiceResponseXMLFactory,
    NumericalResponseXMLFactory,
    OptionResponseXMLFactory,
    SchematicResponseXMLFactory,
    StringResponseXMLFactory,
    SymbolicResponseXMLFactory,
    TrueFalseResponseXMLFactory
)
from xmodule.capa.tests.test_util import use_unsafe_codejail
from xmodule.capa.util import convert_files_to_filenames
from xmodule.capa.xqueue_interface import dateformat


class ResponseTest(unittest.TestCase):
    """Base class for tests of capa responses."""

    xml_factory_class = None

    # If something is wrong, show it to us.
    maxDiff = None

    def setUp(self):
        super(ResponseTest, self).setUp()  # lint-amnesty, pylint: disable=super-with-arguments
        if self.xml_factory_class:
            self.xml_factory = self.xml_factory_class()  # lint-amnesty, pylint: disable=not-callable

    def build_problem(self, capa_system=None, **kwargs):
        xml = self.xml_factory.build_xml(**kwargs)
        return new_loncapa_problem(xml, capa_system=capa_system)

    # pylint: disable=missing-function-docstring
    def assert_grade(self, problem, submission, expected_correctness, msg=None):
        input_dict = {'1_2_1': submission}
        correct_map = problem.grade_answers(input_dict)
        if msg is None:
            assert correct_map.get_correctness('1_2_1') == expected_correctness
        else:
            assert correct_map.get_correctness('1_2_1') == expected_correctness, msg

    def assert_answer_format(self, problem):
        answers = problem.get_question_answers()
        assert answers['1_2_1'] is not None

    # pylint: disable=missing-function-docstring
    def assert_multiple_grade(self, problem, correct_answers, incorrect_answers):
        for input_str in correct_answers:
            result = problem.grade_answers({'1_2_1': input_str}).get_correctness('1_2_1')
            assert result == 'correct'

        for input_str in incorrect_answers:
            result = problem.grade_answers({'1_2_1': input_str}).get_correctness('1_2_1')
            assert result == 'incorrect'

    def assert_multiple_partial(self, problem, correct_answers, incorrect_answers, partial_answers):
        """
        Runs multiple asserts for varying correct, incorrect,
        and partially correct answers, all passed as lists.
        """
        for input_str in correct_answers:
            result = problem.grade_answers({'1_2_1': input_str}).get_correctness('1_2_1')
            assert result == 'correct'

        for input_str in incorrect_answers:
            result = problem.grade_answers({'1_2_1': input_str}).get_correctness('1_2_1')
            assert result == 'incorrect'

        for input_str in partial_answers:
            result = problem.grade_answers({'1_2_1': input_str}).get_correctness('1_2_1')
            assert result == 'partially-correct'

    def _get_random_number_code(self):
        """Returns code to be used to generate a random result."""
        return "str(random.randint(0, 1e9))"

    def _get_random_number_result(self, seed_value):
        """Returns a result that should be generated using the random_number_code."""
        rand = random.Random(seed_value)
        return str(rand.randint(0, 1e9))


@use_unsafe_codejail()
class MultiChoiceResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = MultipleChoiceResponseXMLFactory

    def test_multiple_choice_grade(self):
        problem = self.build_problem(choices=[False, True, False])

        # Ensure that we get the expected grades
        self.assert_grade(problem, 'choice_0', 'incorrect')
        self.assert_grade(problem, 'choice_1', 'correct')
        self.assert_grade(problem, 'choice_2', 'incorrect')

    def test_partial_multiple_choice_grade(self):
        problem = self.build_problem(choices=[False, True, 'partial'], credit_type='points')

        # Ensure that we get the expected grades
        self.assert_grade(problem, 'choice_0', 'incorrect')
        self.assert_grade(problem, 'choice_1', 'correct')
        self.assert_grade(problem, 'choice_2', 'partially-correct')

    def test_named_multiple_choice_grade(self):
        problem = self.build_problem(choices=[False, True, False],
                                     choice_names=["foil_1", "foil_2", "foil_3"])

        # Ensure that we get the expected grades
        self.assert_grade(problem, 'choice_foil_1', 'incorrect')
        self.assert_grade(problem, 'choice_foil_2', 'correct')
        self.assert_grade(problem, 'choice_foil_3', 'incorrect')

    def test_multiple_choice_valid_grading_schemes(self):
        # Multiple Choice problems only allow one partial credit scheme.
        # Change this test if that changes.
        problem = self.build_problem(choices=[False, True, 'partial'], credit_type='points,points')
        with pytest.raises(LoncapaProblemError):
            input_dict = {'1_2_1': 'choice_1'}
            problem.grade_answers(input_dict)

        # 'bongo' is not a valid grading scheme.
        problem = self.build_problem(choices=[False, True, 'partial'], credit_type='bongo')
        with pytest.raises(LoncapaProblemError):
            input_dict = {'1_2_1': 'choice_1'}
            problem.grade_answers(input_dict)

    def test_partial_points_multiple_choice_grade(self):
        problem = self.build_problem(
            choices=['partial', 'partial', 'partial'],
            credit_type='points',
            points=['1', '0.6', '0']
        )

        # Ensure that we get the expected number of points
        # Using assertAlmostEqual to avoid floating point issues
        correct_map = problem.grade_answers({'1_2_1': 'choice_0'})
        assert round(correct_map.get_npoints('1_2_1') - 1, 7) >= 0

        correct_map = problem.grade_answers({'1_2_1': 'choice_1'})
        assert round(correct_map.get_npoints('1_2_1') - 0.6, 7) >= 0

        correct_map = problem.grade_answers({'1_2_1': 'choice_2'})
        assert round(correct_map.get_npoints('1_2_1') - 0, 7) >= 0

    def test_contextualized_choices(self):
        script = textwrap.dedent("""
            a = 2
            b = 9
            c = a + b

            ok0 = c % 2 == 0 # check remainder modulo 2
            text0 = "$a + $b is even"

            ok1 = c % 2 == 1 # check remainder modulo 2
            text1 = "$a + $b is odd"

            ok2 = "partial"
            text2 = "infinity may be both"
        """)
        choices = ["$ok0", "$ok1", "$ok2"]
        choice_names = ["$text0 ... (should be $ok0)",
                        "$text1 ... (should be $ok1)",
                        "$text2 ... (should be $ok2)"]
        problem = self.build_problem(script=script,
                                     choices=choices,
                                     choice_names=choice_names,
                                     credit_type='points')

        # Ensure the expected correctness and choice names
        self.assert_grade(problem, 'choice_2 + 9 is even ... (should be False)', 'incorrect')
        self.assert_grade(problem, 'choice_2 + 9 is odd ... (should be True)', 'correct')
        self.assert_grade(problem, 'choice_infinity may be both ... (should be partial)', 'partially-correct')


class TrueFalseResponseTest(ResponseTest):   # pylint: disable=missing-class-docstring
    xml_factory_class = TrueFalseResponseXMLFactory

    def test_true_false_grade(self):
        problem = self.build_problem(choices=[False, True, True])

        # Check the results
        # Mark correct if and only if ALL (and only) correct choices selected
        self.assert_grade(problem, 'choice_0', 'incorrect')
        self.assert_grade(problem, 'choice_1', 'incorrect')
        self.assert_grade(problem, 'choice_2', 'incorrect')
        self.assert_grade(problem, ['choice_0', 'choice_1', 'choice_2'], 'incorrect')
        self.assert_grade(problem, ['choice_0', 'choice_2'], 'incorrect')
        self.assert_grade(problem, ['choice_0', 'choice_1'], 'incorrect')
        self.assert_grade(problem, ['choice_1', 'choice_2'], 'correct')

        # Invalid choices should be marked incorrect (we have no choice 3)
        self.assert_grade(problem, 'choice_3', 'incorrect')
        self.assert_grade(problem, 'not_a_choice', 'incorrect')

    def test_named_true_false_grade(self):
        problem = self.build_problem(choices=[False, True, True],
                                     choice_names=['foil_1', 'foil_2', 'foil_3'])

        # Check the results
        # Mark correct if and only if ALL (and only) correct chocies selected
        self.assert_grade(problem, 'choice_foil_1', 'incorrect')
        self.assert_grade(problem, 'choice_foil_2', 'incorrect')
        self.assert_grade(problem, 'choice_foil_3', 'incorrect')
        self.assert_grade(problem, ['choice_foil_1', 'choice_foil_2', 'choice_foil_3'], 'incorrect')
        self.assert_grade(problem, ['choice_foil_1', 'choice_foil_3'], 'incorrect')
        self.assert_grade(problem, ['choice_foil_1', 'choice_foil_2'], 'incorrect')
        self.assert_grade(problem, ['choice_foil_2', 'choice_foil_3'], 'correct')

        # Invalid choices should be marked incorrect
        self.assert_grade(problem, 'choice_foil_4', 'incorrect')
        self.assert_grade(problem, 'not_a_choice', 'incorrect')

    def test_single_correct_response(self):
        problem = self.build_problem(choices=[True, False])
        self.assert_grade(problem, 'choice_0', 'correct')
        self.assert_grade(problem, ['choice_0'], 'correct')


class ImageResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = ImageResponseXMLFactory

    def test_rectangle_grade(self):
        # Define a rectangle with corners (10,10) and (20,20)
        problem = self.build_problem(rectangle="(10,10)-(20,20)")

        # Anything inside the rectangle (and along the borders) is correct
        # Everything else is incorrect
        correct_inputs = ["[12,19]", "[10,10]", "[20,20]",
                          "[10,15]", "[20,15]", "[15,10]", "[15,20]"]
        incorrect_inputs = ["[4,6]", "[25,15]", "[15,40]", "[15,4]"]
        self.assert_multiple_grade(problem, correct_inputs, incorrect_inputs)

    def test_multiple_rectangles_grade(self):
        # Define two rectangles
        rectangle_str = "(10,10)-(20,20);(100,100)-(200,200)"

        # Expect that only points inside the rectangles are marked correct
        problem = self.build_problem(rectangle=rectangle_str)
        correct_inputs = ["[12,19]", "[120, 130]"]
        incorrect_inputs = ["[4,6]", "[25,15]", "[15,40]", "[15,4]",
                            "[50,55]", "[300, 14]", "[120, 400]"]
        self.assert_multiple_grade(problem, correct_inputs, incorrect_inputs)

    def test_region_grade(self):
        # Define a triangular region with corners (0,0), (5,10), and (0, 10)
        region_str = "[ [1,1], [5,10], [0,10] ]"

        # Expect that only points inside the triangle are marked correct
        problem = self.build_problem(regions=region_str)
        correct_inputs = ["[2,4]", "[1,3]"]
        incorrect_inputs = ["[0,0]", "[3,5]", "[5,15]", "[30, 12]"]
        self.assert_multiple_grade(problem, correct_inputs, incorrect_inputs)

    def test_multiple_regions_grade(self):
        # Define multiple regions that the user can select
        region_str = "[[[10,10], [20,10], [20, 30]], [[100,100], [120,100], [120,150]]]"

        # Expect that only points inside the regions are marked correct
        problem = self.build_problem(regions=region_str)
        correct_inputs = ["[15,12]", "[110,112]"]
        incorrect_inputs = ["[0,0]", "[600,300]"]
        self.assert_multiple_grade(problem, correct_inputs, incorrect_inputs)

    def test_region_and_rectangle_grade(self):
        rectangle_str = "(100,100)-(200,200)"
        region_str = "[[10,10], [20,10], [20, 30]]"

        # Expect that only points inside the rectangle or region are marked correct
        problem = self.build_problem(regions=region_str, rectangle=rectangle_str)
        correct_inputs = ["[13,12]", "[110,112]"]
        incorrect_inputs = ["[0,0]", "[600,300]"]
        self.assert_multiple_grade(problem, correct_inputs, incorrect_inputs)

    def test_show_answer(self):
        rectangle_str = "(100,100)-(200,200)"
        region_str = "[[10,10], [20,10], [20, 30]]"

        problem = self.build_problem(regions=region_str, rectangle=rectangle_str)
        self.assert_answer_format(problem)


@use_unsafe_codejail()
class SymbolicResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = SymbolicResponseXMLFactory

    def test_grade_single_input_incorrect(self):
        problem = self.build_problem(math_display=True, expect="2*x+3*y")

        # Incorrect answers
        incorrect_inputs = [
            ('0', ''),
            ('4x+3y', textwrap.dedent("""
                <math xmlns="http://www.w3.org/1998/Math/MathML">
                    <mstyle displaystyle="true">
                    <mn>4</mn><mo>*</mo><mi>x</mi><mo>+</mo><mn>3</mn><mo>*</mo><mi>y</mi>
                    </mstyle></math>""")),
        ]

        for (input_str, input_mathml) in incorrect_inputs:
            self._assert_symbolic_grade(problem, input_str, input_mathml, 'incorrect')

    def test_complex_number_grade_incorrect(self):

        problem = self.build_problem(math_display=True,
                                     expect="[[cos(theta),i*sin(theta)],[i*sin(theta),cos(theta)]]",
                                     options=["matrix", "imaginary"])

        wrong_snuggletex = load_fixture('snuggletex_wrong.html')
        dynamath_input = textwrap.dedent("""
            <math xmlns="http://www.w3.org/1998/Math/MathML">
              <mstyle displaystyle="true"><mn>2</mn></mstyle>
            </math>
        """)

        self._assert_symbolic_grade(
            problem, "2", dynamath_input,
            'incorrect',
            snuggletex_resp=wrong_snuggletex,
        )

    def test_multiple_inputs_exception(self):

        # Should not allow multiple inputs, since we specify
        # only one "expect" value
        with pytest.raises(Exception):
            self.build_problem(math_display=True, expect="2*x+3*y", num_inputs=3)

    def test_check_runs_in_sandbox(self):
        """The learner's answer and the author's expect reach symmath only inside safe_exec."""
        problem = self.build_problem(expect="0")
        payload = "__import__('os').getpid()+0"

        def fake_safe_exec(_code, globals_dict, **_kwargs):
            globals_dict['cfn_return'] = {'ok': False, 'msg': '<p>graded in the sandbox</p>'}

        with mock.patch(
            'xmodule.capa.responsetypes.safe_exec.safe_exec', side_effect=fake_safe_exec
        ) as mock_exec:
            correct_map = problem.grade_answers({'1_2_1': payload, '1_2_1_dynamath': ''})

        mock_exec.assert_called_once()
        code, globals_dict = mock_exec.call_args.args
        assert 'symmath_check(expect, ans' in code
        assert globals_dict['ans'] == payload
        assert globals_dict['expect'] == '0'
        assert mock_exec.call_args.kwargs['slug'] == '1_1'
        assert correct_map.get_correctness('1_2_1') == 'incorrect'
        assert 'graded in the sandbox' in correct_map.get_msg('1_2_1')

    def test_sandbox_failure_is_a_response_error(self):
        """A sandbox failure surfaces as a ResponseError, like CustomResponse, not a bare Exception."""
        problem = self.build_problem(expect="0")

        with mock.patch('xmodule.capa.responsetypes.safe_exec.safe_exec', side_effect=RuntimeError('no sandbox')):
            with pytest.raises(ResponseError, match='no sandbox'):
                problem.grade_answers({'1_2_1': 'x', '1_2_1_dynamath': ''})

    def _assert_symbolic_grade(
        self, problem, student_input, dynamath_input, expected_correctness,
        snuggletex_resp=""
    ):
        """
        Assert that the symbolic response has a certain grade.

        `problem` is the capa problem containing the symbolic response.
        `student_input` is the text the student entered.
        `dynamath_input` is the JavaScript rendered MathML from the page.
        `expected_correctness` is either "correct" or "incorrect"
        `snuggletex_resp` is the simulated response from the Snuggletex server
        """
        input_dict = {'1_2_1': str(student_input),
                      '1_2_1_dynamath': str(dynamath_input)}

        # Simulate what the Snuggletex server would respond
        with mock.patch.object(requests, 'post') as mock_post:
            mock_post.return_value.text = snuggletex_resp

            correct_map = problem.grade_answers(input_dict)

            assert correct_map.get_correctness('1_2_1') == expected_correctness


@use_unsafe_codejail()
class OptionResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = OptionResponseXMLFactory

    def test_grade(self):
        problem = self.build_problem(options=["first", "second", "third"],
                                     correct_option="second")

        # Assert that we get the expected grades
        self.assert_grade(problem, "first", "incorrect")
        self.assert_grade(problem, "second", "correct")
        self.assert_grade(problem, "third", "incorrect")

        # Options not in the list should be marked incorrect
        self.assert_grade(problem, "invalid_option", "incorrect")

    def test_quote_option(self):
        # Test that option response properly escapes quotes inside options strings
        problem = self.build_problem(options=["hasnot", "hasn't", "has'nt"],
                                     correct_option="hasn't")

        # Assert that correct option with a quote inside is marked correctly
        self.assert_grade(problem, "hasnot", "incorrect")
        self.assert_grade(problem, "hasn't", "correct")
        self.assert_grade(problem, "hasn\'t", "correct")
        self.assert_grade(problem, "has'nt", "incorrect")

    def test_variable_options(self):
        """
        Test that if variable are given in option response then correct map must contain answervariable value.
        """
        script = textwrap.dedent("""\
        a = 1000
        b = a*2
        c = a*3
        """)
        problem = self.build_problem(
            options=['$a', '$b', '$c'],
            correct_option='$a',
            script=script
        )

        input_dict = {'1_2_1': '1000'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_correctness('1_2_1') == 'correct'
        assert correct_map.get_property('1_2_1', 'answervariable') == '$a'


@use_unsafe_codejail()
class FormulaResponseTest(ResponseTest):
    """
    Test the FormulaResponse class
    """
    xml_factory_class = FormulaResponseXMLFactory

    def test_grade(self):
        """
        Test basic functionality of FormulaResponse

        Specifically, if it can understand equivalence of formulae
        """
        # Sample variables x and y in the range [-10, 10]
        sample_dict = {'x': (-10, 10), 'y': (-10, 10)}

        # The expected solution is numerically equivalent to x+2y
        problem = self.build_problem(sample_dict=sample_dict,
                                     num_samples=10,
                                     tolerance=0.01,
                                     answer="x+2*y")

        # Expect an equivalent formula to be marked correct
        # 2x - x + y + y = x + 2y
        input_formula = "2*x - x + y + y"
        self.assert_grade(problem, input_formula, "correct")

        # Expect an incorrect formula to be marked incorrect
        # x + y != x + 2y
        input_formula = "x + y"
        self.assert_grade(problem, input_formula, "incorrect")

    def test_hint(self):
        """
        Test the hint-giving functionality of FormulaResponse
        """
        # Sample variables x and y in the range [-10, 10]
        sample_dict = {'x': (-10, 10), 'y': (-10, 10)}

        # Give a hint if the user leaves off the coefficient
        # or leaves out x
        hints = [('x + 3*y', 'y_coefficient', 'Check the coefficient of y'),
                 ('2*y', 'missing_x', 'Try including the variable x')]

        # The expected solution is numerically equivalent to x+2y
        problem = self.build_problem(sample_dict=sample_dict,
                                     num_samples=10,
                                     tolerance=0.01,
                                     answer="x+2*y",
                                     hints=hints)

        # Expect to receive a hint  if we add an extra y
        input_dict = {'1_2_1': "x + 2*y + y"}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'Check the coefficient of y'

        # Expect to receive a hint if we leave out x
        input_dict = {'1_2_1': "2*y"}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'Try including the variable x'

    def test_script(self):
        """
        Test if python script can be used to generate answers
        """

        # Calculate the answer using a script
        script = "calculated_ans = 'x+x'"

        # Sample x in the range [-10,10]
        sample_dict = {'x': (-10, 10)}

        # The expected solution is numerically equivalent to 2*x
        problem = self.build_problem(sample_dict=sample_dict,
                                     num_samples=10,
                                     tolerance=0.01,
                                     answer="$calculated_ans",
                                     script=script)

        # Expect that the inputs are graded correctly
        self.assert_grade(problem, '2*x', 'correct')
        self.assert_grade(problem, '3*x', 'incorrect')

    def test_grade_infinity(self):
        """
        Test that a large input on a problem with relative tolerance isn't
        erroneously marked as correct.
        """

        sample_dict = {'x': (1, 2)}

        # Test problem
        problem = self.build_problem(sample_dict=sample_dict,
                                     num_samples=10,
                                     tolerance="1%",
                                     answer="x")
        # Expect such a large answer to be marked incorrect
        input_formula = "x*1e999"
        self.assert_grade(problem, input_formula, "incorrect")
        # Expect such a large negative answer to be marked incorrect
        input_formula = "-x*1e999"
        self.assert_grade(problem, input_formula, "incorrect")

    def test_grade_nan(self):
        """
        Test that expressions that evaluate to NaN are not marked as correct.
        """

        sample_dict = {'x': (1, 2)}

        # Test problem
        problem = self.build_problem(sample_dict=sample_dict,
                                     num_samples=10,
                                     tolerance="1%",
                                     answer="x")
        # Expect an incorrect answer (+ nan) to be marked incorrect
        # Right now this evaluates to 'nan' for a given x (Python implementation-dependent)
        input_formula = "10*x + 0*1e999"
        self.assert_grade(problem, input_formula, "incorrect")
        # Expect an correct answer (+ nan) to be marked incorrect
        input_formula = "x + 0*1e999"
        self.assert_grade(problem, input_formula, "incorrect")

    def test_raises_zero_division_err(self):
        """
        See if division by zero raises an error.
        """
        sample_dict = {'x': (1, 2)}
        problem = self.build_problem(sample_dict=sample_dict,
                                     num_samples=10,
                                     tolerance="1%",
                                     answer="x")  # Answer doesn't matter
        input_dict = {'1_2_1': '1/0'}
        self.assertRaises(StudentInputError, problem.grade_answers, input_dict)

    def test_validate_answer(self):
        """
        Makes sure that validate_answer works.
        """
        sample_dict = {'x': (1, 2)}
        problem = self.build_problem(
            sample_dict=sample_dict,
            num_samples=10,
            tolerance="1%",
            answer="x"
        )
        assert list(problem.responders.values())[0].validate_answer('14*x')
        assert not list(problem.responders.values())[0].validate_answer('3*y+2*x')


@use_unsafe_codejail()
class StringResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = StringResponseXMLFactory

    def test_backward_compatibility_for_multiple_answers(self):
        """
        Remove this test, once support for _or_ separator will be removed.
        """

        answers = ["Second", "Third", "Fourth"]
        problem = self.build_problem(answer="_or_".join(answers), case_sensitive=True)

        for answer in answers:
            # Exact string should be correct
            self.assert_grade(problem, answer, "correct")
        # Other strings and the lowercase version of the string are incorrect
        self.assert_grade(problem, "Other String", "incorrect")

        problem = self.build_problem(answer="_or_".join(answers), case_sensitive=False)
        for answer in answers:
            # Exact string should be correct
            self.assert_grade(problem, answer, "correct")
            self.assert_grade(problem, answer.lower(), "correct")
        self.assert_grade(problem, "Other String", "incorrect")

    def test_regexp(self):
        problem = self.build_problem(answer="Second", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Second", "correct")

        problem = self.build_problem(answer="sec", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Second", "incorrect")

        problem = self.build_problem(answer="sec.*", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Second", "correct")

        problem = self.build_problem(answer="sec.*", case_sensitive=True, regexp=True)
        self.assert_grade(problem, "Second", "incorrect")

        problem = self.build_problem(answer="Sec.*$", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Second", "correct")

        problem = self.build_problem(answer="^sec$", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Second", "incorrect")

        problem = self.build_problem(answer="^Sec(ond)?$", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Second", "correct")

        problem = self.build_problem(answer="^Sec(ond)?$", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "Sec", "correct")

        problem = self.build_problem(answer="tre+", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "There is a tree", "incorrect")

        problem = self.build_problem(answer=".*tre+", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "There is a tree", "correct")

        # test with case_sensitive not specified
        problem = self.build_problem(answer=".*tre+", regexp=True)
        self.assert_grade(problem, "There is a tree", "correct")

        answers = [
            "Martin Luther King Junior",
            "Doctor Martin Luther King Junior",
            "Dr. Martin Luther King Jr.",
            "Martin Luther King"
        ]

        problem = self.build_problem(answer=r"\w*\.?.*Luther King\s*.*", case_sensitive=True, regexp=True)

        for answer in answers:
            self.assert_grade(problem, answer, "correct")

        problem = self.build_problem(answer=r"^(-\|){2,5}$", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "-|-|-|", "correct")
        self.assert_grade(problem, "-|", "incorrect")
        self.assert_grade(problem, "-|-|-|-|-|-|", "incorrect")

        regexps = [
            "^One$",
            "two",
            "^thre+",
            "^4|Four$",
        ]
        problem = self.build_problem(
            answer="just_sample",
            case_sensitive=False,
            regexp=True,
            additional_answers=regexps
        )

        self.assert_grade(problem, "One", "correct")
        self.assert_grade(problem, "two", "correct")
        self.assert_grade(problem, "!!two!!", "correct")
        self.assert_grade(problem, "threeeee", "correct")
        self.assert_grade(problem, "three", "correct")
        self.assert_grade(problem, "4", "correct")
        self.assert_grade(problem, "Four", "correct")
        self.assert_grade(problem, "Five", "incorrect")
        self.assert_grade(problem, "|", "incorrect")

        # test unicode
        problem = self.build_problem(answer="æ", case_sensitive=False, regexp=True, additional_answers=['ö'])
        self.assert_grade(problem, "æ", "correct")
        self.assert_grade(problem, "ö", "correct")
        self.assert_grade(problem, "î", "incorrect")
        self.assert_grade(problem, "o", "incorrect")

    def test_backslash_and_unicode_regexps(self):
        r"""
        Test some special cases of [unicode] regexps.

        One needs to use either r'' strings or write real `repr` of unicode strings, because of the following
        (from python docs, http://docs.python.org/2/library/re.html):

        'for example, to match a literal backslash, one might have to write '\\\\' as the pattern string,
        because the regular expression must be \\,
        and each backslash must be expressed as \\ inside a regular Python string literal.'

        Example of real use case in Studio:
            a) user inputs regexp in usual regexp language,
            b) regexp is saved to xml and is read in python as repr of that string
            So  a\d in front-end editor will become a\\\\d in xml,  so it will match a1 as student answer.
        """
        problem = self.build_problem(answer="5\\\\æ", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "5\\æ", "correct")

        problem = self.build_problem(answer="5\\\\æ", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "5\\æ", "correct")

    def test_backslash(self):
        problem = self.build_problem(answer="a\\\\c1", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "a\\c1", "correct")

    def test_special_chars(self):
        problem = self.build_problem(answer="a \\s1", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "a  1", "correct")

    def test_case_sensitive(self):
        # Test single answer
        problem_specified = self.build_problem(answer="Second", case_sensitive=True)

        # should also be case_sensitive if case sensitivity is not specified
        problem_not_specified = self.build_problem(answer="Second")
        problems = [problem_specified, problem_not_specified]

        for problem in problems:
            # Exact string should be correct
            self.assert_grade(problem, "Second", "correct")

            # Other strings and the lowercase version of the string are incorrect
            self.assert_grade(problem, "Other String", "incorrect")
            self.assert_grade(problem, "second", "incorrect")

        # Test multiple answers
        answers = ["Second", "Third", "Fourth"]

        # set up problems
        problem_specified = self.build_problem(
            answer="sample_answer", case_sensitive=True, additional_answers=answers
        )
        problem_not_specified = self.build_problem(
            answer="sample_answer", additional_answers=answers
        )
        problems = [problem_specified, problem_not_specified]
        for problem in problems:
            for answer in answers:
                # Exact string should be correct
                self.assert_grade(problem, answer, "correct")

            # Other strings and the lowercase version of the string are incorrect
            self.assert_grade(problem, "Other String", "incorrect")
            self.assert_grade(problem, "second", "incorrect")

    def test_bogus_escape_not_raised(self):
        """
        We now adding ^ and $ around regexp, so no bogus escape error will be raised.
        """
        problem = self.build_problem(answer="\\", case_sensitive=False, regexp=True)

        self.assert_grade(problem, "\\", "incorrect")

        # right way to search for \
        problem = self.build_problem(answer="\\\\", case_sensitive=False, regexp=True)
        self.assert_grade(problem, "\\", "correct")

    def test_case_insensitive(self):
        # Test single answer
        problem = self.build_problem(answer="Second", case_sensitive=False)

        # Both versions of the string should be allowed, regardless
        # of capitalization
        self.assert_grade(problem, "Second", "correct")
        self.assert_grade(problem, "second", "correct")

        # Other strings are not allowed
        self.assert_grade(problem, "Other String", "incorrect")

        # Test multiple answers
        answers = ["Second", "Third", "Fourth"]
        problem = self.build_problem(answer="sample_answer", case_sensitive=False, additional_answers=answers)

        for answer in answers:
            # Exact string should be correct
            self.assert_grade(problem, answer, "correct")
            self.assert_grade(problem, answer.lower(), "correct")

        # Other strings and the lowercase version of the string are incorrect
        self.assert_grade(problem, "Other String", "incorrect")

    def test_compatible_non_attribute_additional_answer_xml(self):
        problem = self.build_problem(answer="Donut", non_attribute_answers=["Sprinkles"])
        self.assert_grade(problem, "Donut", "correct")
        self.assert_grade(problem, "Sprinkles", "correct")
        self.assert_grade(problem, "Meh", "incorrect")

    def test_partial_matching(self):
        problem = self.build_problem(answer="a2", case_sensitive=False, regexp=True, additional_answers=['.?\\d.?'])
        self.assert_grade(problem, "a3", "correct")
        self.assert_grade(problem, "3a", "correct")

    def test_exception(self):
        problem = self.build_problem(answer="a2", case_sensitive=False, regexp=True, additional_answers=['?\\d?'])
        with pytest.raises(Exception) as cm:
            self.assert_grade(problem, "a3", "correct")
        exception_message = str(cm.value)
        assert 'nothing to repeat' in exception_message

    def test_hints(self):

        hints = [
            ("wisconsin", "wisc", "The state capital of Wisconsin is Madison"),
            ("minnesota", "minn", "The state capital of Minnesota is St. Paul"),
        ]
        problem = self.build_problem(
            answer="Michigan",
            case_sensitive=False,
            hints=hints,
        )
        # We should get a hint for Wisconsin
        input_dict = {'1_2_1': 'Wisconsin'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'The state capital of Wisconsin is Madison'

        # We should get a hint for Minnesota
        input_dict = {'1_2_1': 'Minnesota'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'The state capital of Minnesota is St. Paul'

        # We should NOT get a hint for Michigan (the correct answer)
        input_dict = {'1_2_1': 'Michigan'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == ''

        # We should NOT get a hint for any other string
        input_dict = {'1_2_1': 'California'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == ''

    def test_hints_regexp_and_answer_regexp(self):
        different_student_answers = [
            "May be it is Boston",
            "Boston, really?",
            "Boston",
            "OK, I see, this is Boston",
        ]

        # if problem has regexp = true, it will accept hints written in regexp
        hints = [
            ("wisconsin", "wisc", "The state capital of Wisconsin is Madison"),
            ("minnesota", "minn", "The state capital of Minnesota is St. Paul"),
            (".*Boston.*", "bst", "First letter of correct answer is M."),
            ('^\\d9$', "numbers", "Should not end with 9."),
        ]

        additional_answers = [
            '^\\d[0-8]$',
        ]
        problem = self.build_problem(
            answer="Michigan",
            case_sensitive=False,
            hints=hints,
            additional_answers=additional_answers,
            regexp=True
        )

        # We should get a hint for Wisconsin
        input_dict = {'1_2_1': 'Wisconsin'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'The state capital of Wisconsin is Madison'

        # We should get a hint for Minnesota
        input_dict = {'1_2_1': 'Minnesota'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'The state capital of Minnesota is St. Paul'

        # We should NOT get a hint for Michigan (the correct answer)
        input_dict = {'1_2_1': 'Michigan'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == ''

        # We should NOT get a hint for any other string
        input_dict = {'1_2_1': 'California'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == ''

        # We should get the same hint for each answer
        for answer in different_student_answers:
            input_dict = {'1_2_1': answer}
            correct_map = problem.grade_answers(input_dict)
            assert correct_map.get_hint('1_2_1') == 'First letter of correct answer is M.'

        input_dict = {'1_2_1': '59'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'Should not end with 9.'

        input_dict = {'1_2_1': '57'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == ''

    def test_computed_hints(self):
        problem = self.build_problem(
            answer="Michigan",
            hintfn="gimme_a_hint",
            script=textwrap.dedent("""
                def gimme_a_hint(answer_ids, student_answers, new_cmap, old_cmap):
                    aid = answer_ids[0]
                    answer = student_answers[aid]
                    new_cmap.set_hint_and_mode(aid, answer+"??", "always")
            """)
        )

        input_dict = {'1_2_1': 'Hello'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_hint('1_2_1') == 'Hello??'

    def test_hint_function_randomization(self):
        # The hint function should get the seed from the problem.
        problem = self.build_problem(
            answer="1",
            hintfn="gimme_a_random_hint",
            script=textwrap.dedent("""
                def gimme_a_random_hint(answer_ids, student_answers, new_cmap, old_cmap):
                    answer = {code}
                    new_cmap.set_hint_and_mode(answer_ids[0], answer, "always")

            """.format(code=self._get_random_number_code()))
        )
        correct_map = problem.grade_answers({'1_2_1': '2'})
        hint = correct_map.get_hint('1_2_1')
        assert hint == self._get_random_number_result(problem.seed)

    def test_empty_answer_graded_as_incorrect(self):
        """
        Tests that problem should be graded incorrect if blank space is chosen as answer
        """
        problem = self.build_problem(answer=" ", case_sensitive=False, regexp=True)
        self.assert_grade(problem, " ", "incorrect")


class CodeResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = CodeResponseXMLFactory

    def setUp(self):
        super(CodeResponseTest, self).setUp()  # lint-amnesty, pylint: disable=super-with-arguments

        grader_payload = json.dumps({"grader": "ps04/grade_square.py"})
        self.problem = self.build_problem(initial_display="def square(x):",
                                          answer_display="answer",
                                          grader_payload=grader_payload,
                                          num_responses=2)

    @staticmethod
    def make_queuestate(key, time):
        """Create queuestate dict"""
        timestr = datetime.strftime(time, dateformat)
        return {'key': key, 'time': timestr}

    def test_is_queued(self):
        """
        Simple test of whether LoncapaProblem knows when it's been queued
        """

        answer_ids = sorted(self.problem.get_question_answers())

        # CodeResponse requires internal CorrectMap state. Build it now in the unqueued state
        cmap = CorrectMap()
        for answer_id in answer_ids:
            cmap.update(CorrectMap(answer_id=answer_id, queuestate=None))
        self.problem.correct_map.update(cmap)

        assert self.problem.is_queued() is False

        # Now we queue the LCP
        cmap = CorrectMap()
        for i, answer_id in enumerate(answer_ids):
            queuestate = CodeResponseTest.make_queuestate(i, datetime.now(UTC))
            cmap.update(CorrectMap(answer_id=answer_id, queuestate=queuestate))
        self.problem.correct_map.update(cmap)

        assert self.problem.is_queued() is True

    def test_update_score(self):
        '''
        Test whether LoncapaProblem.update_score can deliver queued result to the right subproblem
        '''
        answer_ids = sorted(self.problem.get_question_answers())

        # CodeResponse requires internal CorrectMap state. Build it now in the queued state
        old_cmap = CorrectMap()
        for i, answer_id in enumerate(answer_ids):
            queuekey = 1000 + i
            queuestate = CodeResponseTest.make_queuestate(queuekey, datetime.now(UTC))
            old_cmap.update(CorrectMap(answer_id=answer_id, queuestate=queuestate))

        # Message format common to external graders
        grader_msg = '<span>MESSAGE</span>'   # Must be valid XML
        correct_score_msg = json.dumps({'correct': True, 'score': 1, 'msg': grader_msg})
        incorrect_score_msg = json.dumps({'correct': False, 'score': 0, 'msg': grader_msg})

        xserver_msgs = {'correct': correct_score_msg,
                        'incorrect': incorrect_score_msg, }

        # Incorrect queuekey, state should not be updated
        for correctness in ['correct', 'incorrect']:
            self.problem.correct_map = CorrectMap()
            self.problem.correct_map.update(old_cmap)  # Deep copy

            self.problem.update_score(xserver_msgs[correctness], queuekey=0)
            assert self.problem.correct_map.get_dict() == old_cmap.get_dict()
            # Deep comparison

            for answer_id in answer_ids:
                assert self.problem.correct_map.is_queued(answer_id)
                # Should be still queued, since message undelivered  # lint-amnesty, pylint: disable=line-too-long

        # Correct queuekey, state should be updated
        for correctness in ['correct', 'incorrect']:
            for i, answer_id in enumerate(answer_ids):
                self.problem.correct_map = CorrectMap()
                self.problem.correct_map.update(old_cmap)

                new_cmap = CorrectMap()
                new_cmap.update(old_cmap)
                npoints = 1 if correctness == 'correct' else 0
                new_cmap.set(answer_id=answer_id, npoints=npoints, correctness=correctness, msg=grader_msg, queuestate=None)  # lint-amnesty, pylint: disable=line-too-long

                self.problem.update_score(xserver_msgs[correctness], queuekey=1000 + i)
                assert self.problem.correct_map.get_dict() == new_cmap.get_dict()

                for j, test_id in enumerate(answer_ids):
                    if j == i:
                        assert not self.problem.correct_map.is_queued(test_id)
                        # Should be dequeued, message delivered  # lint-amnesty, pylint: disable=line-too-long
                    else:
                        assert self.problem.correct_map.is_queued(test_id)
                        # Should be queued, message undelivered  # lint-amnesty, pylint: disable=line-too-long

    def test_recentmost_queuetime(self):
        '''
        Test whether the LoncapaProblem knows about the time of queue requests
        '''
        answer_ids = sorted(self.problem.get_question_answers())

        # CodeResponse requires internal CorrectMap state. Build it now in the unqueued state
        cmap = CorrectMap()
        for answer_id in answer_ids:
            cmap.update(CorrectMap(answer_id=answer_id, queuestate=None))
        self.problem.correct_map.update(cmap)

        assert self.problem.get_recentmost_queuetime() is None

        # CodeResponse requires internal CorrectMap state. Build it now in the queued state
        cmap = CorrectMap()
        for i, answer_id in enumerate(answer_ids):
            queuekey = 1000 + i
            latest_timestamp = datetime.now(UTC)
            queuestate = CodeResponseTest.make_queuestate(queuekey, latest_timestamp)
            cmap.update(CorrectMap(answer_id=answer_id, queuestate=queuestate))
        self.problem.correct_map.update(cmap)

        # Queue state only tracks up to second
        latest_timestamp = datetime.strptime(
            datetime.strftime(latest_timestamp, dateformat), dateformat
        ).replace(tzinfo=UTC)

        assert self.problem.get_recentmost_queuetime() == latest_timestamp

    def test_convert_files_to_filenames(self):
        '''
        Test whether file objects are converted to filenames without altering other structures
        '''
        problem_file = os.path.join(os.path.dirname(__file__), "test_files/filename_convert_test.txt")
        with open(problem_file) as fp:
            answers_with_file = {'1_2_1': 'String-based answer',
                                 '1_3_1': ['answer1', 'answer2', 'answer3'],
                                 '1_4_1': [fp, fp]}
            answers_converted = convert_files_to_filenames(answers_with_file)
            assert answers_converted['1_2_1'] == 'String-based answer'
            assert answers_converted['1_3_1'] == ['answer1', 'answer2', 'answer3']
            assert answers_converted['1_4_1'] == [fp.name, fp.name]

    def test_parse_score_msg_of_responder(self):
        """
        Test whether LoncapaProblem._parse_score_msg correcly parses valid HTML5 html.
        """
        valid_grader_msgs = [
            '<span>MESSAGE</span>',  # Valid XML
            textwrap.dedent("""
                <div class='matlabResponse'><div id='mwAudioPlaceHolder'>
                <audio controls autobuffer autoplay src='data:audio/wav;base64='>Audio is not supported on this browser.</audio>
                <div>Right click <a href=https://endpoint.mss-mathworks.com/media/filename.wav>here</a> and click \"Save As\" to download the file</div></div>
                <div style='white-space:pre' class='commandWindowOutput'></div><ul></ul></div>
            """).replace('\n', ''),  # Valid HTML5 real case Matlab response, invalid XML
            '<aaa></bbb>'  # Invalid XML, but will be parsed by html5lib to <aaa/>
        ]

        invalid_grader_msgs = [
            '<audio',  # invalid XML and HTML5
            '<p>\b</p>',  # invalid special character
        ]

        answer_ids = sorted(self.problem.get_question_answers())

        # CodeResponse requires internal CorrectMap state. Build it now in the queued state
        old_cmap = CorrectMap()
        for i, answer_id in enumerate(answer_ids):
            queuekey = 1000 + i
            queuestate = CodeResponseTest.make_queuestate(queuekey, datetime.now(UTC))
            old_cmap.update(CorrectMap(answer_id=answer_id, queuestate=queuestate))

        for grader_msg in valid_grader_msgs:
            correct_score_msg = json.dumps({'correct': True, 'score': 1, 'msg': grader_msg})
            incorrect_score_msg = json.dumps({'correct': False, 'score': 0, 'msg': grader_msg})
            xserver_msgs = {'correct': correct_score_msg, 'incorrect': incorrect_score_msg, }

            for i, answer_id in enumerate(answer_ids):
                self.problem.correct_map = CorrectMap()
                self.problem.correct_map.update(old_cmap)
                output = self.problem.update_score(xserver_msgs['correct'], queuekey=1000 + i)
                assert output[answer_id]['msg'] == grader_msg

        for grader_msg in invalid_grader_msgs:
            correct_score_msg = json.dumps({'correct': True, 'score': 1, 'msg': grader_msg})
            incorrect_score_msg = json.dumps({'correct': False, 'score': 0, 'msg': grader_msg})
            xserver_msgs = {'correct': correct_score_msg, 'incorrect': incorrect_score_msg, }

            for i, answer_id in enumerate(answer_ids):
                self.problem.correct_map = CorrectMap()
                self.problem.correct_map.update(old_cmap)

                output = self.problem.update_score(xserver_msgs['correct'], queuekey=1000 + i)
                assert output[answer_id]['msg'] == 'Invalid grader reply. Please contact the course staff.'


@use_unsafe_codejail()
class ChoiceResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = ChoiceResponseXMLFactory

    def test_radio_group_grade(self):
        problem = self.build_problem(choice_type='radio',
                                     choices=[False, True, False])

        # Check that we get the expected results
        self.assert_grade(problem, 'choice_0', 'incorrect')
        self.assert_grade(problem, 'choice_1', 'correct')
        self.assert_grade(problem, 'choice_2', 'incorrect')

        # No choice 3 exists --> mark incorrect
        self.assert_grade(problem, 'choice_3', 'incorrect')

    def test_checkbox_group_grade(self):
        problem = self.build_problem(choice_type='checkbox',
                                     choices=[False, True, True])

        # Check that we get the expected results
        # (correct if and only if BOTH correct choices chosen)
        self.assert_grade(problem, ['choice_1', 'choice_2'], 'correct')
        self.assert_grade(problem, 'choice_1', 'incorrect')
        self.assert_grade(problem, 'choice_2', 'incorrect')
        self.assert_grade(problem, ['choice_0', 'choice_1'], 'incorrect')
        self.assert_grade(problem, ['choice_0', 'choice_2'], 'incorrect')

        # No choice 3 exists --> mark incorrect
        self.assert_grade(problem, 'choice_3', 'incorrect')

    def test_checkbox_group_valid_grading_schemes(self):
        # Checkbox-type problems only allow one partial credit scheme.
        # Change this test if that changes.
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True],
            credit_type='edc,halves,bongo'
        )
        with pytest.raises(LoncapaProblemError):
            input_dict = {'1_2_1': 'choice_1'}
            problem.grade_answers(input_dict)

        # 'bongo' is not a valid grading scheme.
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True],
            credit_type='bongo'
        )
        with pytest.raises(LoncapaProblemError):
            input_dict = {'1_2_1': 'choice_1'}
            problem.grade_answers(input_dict)

    def test_checkbox_group_partial_credit_grade(self):
        # First: Every Decision Counts grading style
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True],
            credit_type='edc'
        )

        # Check that we get the expected results
        # (correct if and only if BOTH correct choices chosen)
        # (partially correct if at least one choice is right)
        # (incorrect if totally wrong)
        self.assert_grade(problem, ['choice_0', 'choice_1'], 'incorrect')
        self.assert_grade(problem, ['choice_2', 'choice_3'], 'correct')
        self.assert_grade(problem, 'choice_0', 'partially-correct')
        self.assert_grade(problem, 'choice_2', 'partially-correct')
        self.assert_grade(problem, ['choice_0', 'choice_1', 'choice_2', 'choice_3'], 'partially-correct')

        # Second: Halves grading style
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True],
            credit_type='halves'
        )

        # Check that we get the expected results
        # (correct if and only if BOTH correct choices chosen)
        # (partially correct on one error)
        # (incorrect for more errors, at least with this # of choices.)
        self.assert_grade(problem, ['choice_0', 'choice_1'], 'incorrect')
        self.assert_grade(problem, ['choice_2', 'choice_3'], 'correct')
        self.assert_grade(problem, 'choice_2', 'partially-correct')
        self.assert_grade(problem, ['choice_1', 'choice_2', 'choice_3'], 'partially-correct')
        self.assert_grade(problem, ['choice_0', 'choice_1', 'choice_2', 'choice_3'], 'incorrect')

        # Third: Halves grading style with more options
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True, False],
            credit_type='halves'
        )

        # Check that we get the expected results
        # (2 errors allowed with 5+ choices)
        self.assert_grade(problem, ['choice_0', 'choice_1', 'choice_4'], 'incorrect')
        self.assert_grade(problem, ['choice_2', 'choice_3'], 'correct')
        self.assert_grade(problem, 'choice_2', 'partially-correct')
        self.assert_grade(problem, ['choice_1', 'choice_2', 'choice_3'], 'partially-correct')
        self.assert_grade(problem, ['choice_0', 'choice_1', 'choice_2', 'choice_3'], 'partially-correct')
        self.assert_grade(problem, ['choice_0', 'choice_1', 'choice_2', 'choice_3', 'choice_4'], 'incorrect')

    def test_checkbox_group_partial_points_grade(self):
        # Ensure that we get the expected number of points
        # Using assertAlmostEqual to avoid floating point issues
        # First: Every Decision Counts grading style
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True],
            credit_type='edc'
        )

        correct_map = problem.grade_answers({'1_2_1': 'choice_2'})
        assert round(correct_map.get_npoints('1_2_1') - 0.75, 7) >= 0

        # Second: Halves grading style
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True],
            credit_type='halves'
        )

        correct_map = problem.grade_answers({'1_2_1': 'choice_2'})
        assert round(correct_map.get_npoints('1_2_1') - 0.5, 7) >= 0

        # Third: Halves grading style with more options
        problem = self.build_problem(
            choice_type='checkbox',
            choices=[False, False, True, True, False],
            credit_type='halves'
        )

        correct_map = problem.grade_answers({'1_2_1': 'choice_2,choice4'})
        assert round(correct_map.get_npoints('1_2_1') - 0.25, 7) >= 0

    def test_grade_with_no_checkbox_selected(self):
        """
        Test that answer marked as incorrect if no checkbox selected.
        """
        problem = self.build_problem(
            choice_type='checkbox', choices=[False, False, False]
        )

        correct_map = problem.grade_answers({})
        assert correct_map.get_correctness('1_2_1') == 'incorrect'

    def test_contextualized_choices(self):
        script = textwrap.dedent("""
            a = 6
            b = 4
            c = a + b

            ok0 = c % 2 == 0 # check remainder modulo 2
            ok1 = c % 3 == 0 # check remainder modulo 3
            ok2 = c % 5 == 0 # check remainder modulo 5
            ok3 = not any((ok0, ok1, ok2))
        """)
        choices = ["$ok0", "$ok1", "$ok2", "$ok3"]
        problem = self.build_problem(script=script,
                                     choice_type='checkbox',
                                     choices=choices)

        # Ensure the expected correctness
        self.assert_grade(problem, ['choice_0', 'choice_2'], 'correct')
        self.assert_grade(problem, ['choice_1', 'choice_3'], 'incorrect')


@use_unsafe_codejail()
class NumericalResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = NumericalResponseXMLFactory

    # We blend the line between integration (using evaluator) and exclusively
    # unit testing the NumericalResponse (mocking out the evaluator)
    # For simple things its not worth the effort.
    def test_grade_range_tolerance(self):
        problem_setup = [
            # [given_answer, [list of correct responses], [list of incorrect responses]]
            ['[5, 7)', ['5', '6', '6.999'], ['4.999', '7']],
            ['[1.6e-5, 1.9e24)', ['0.000016', '1.6*10^-5', '1.59e24'], ['1.59e-5', '1.9e24', '1.9*10^24']],
            ['[0, 1.6e-5]', ['1.6*10^-5'], ["2"]],
            ['(1.6e-5, 10]', ["2"], ['1.6*10^-5']],
        ]
        for given_answer, correct_responses, incorrect_responses in problem_setup:
            problem = self.build_problem(answer=given_answer)
            self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_additional_answer_grading(self):
        """
        Test additional answers are graded correct with their associated correcthint.
        """
        primary_answer = '100'
        primary_correcthint = 'primary feedback'
        additional_answers = {
            '1': '1. additional feedback',
            '2': '2. additional feedback',
            '4': '4. additional feedback',
            '5': ''
        }
        problem = self.build_problem(
            answer=primary_answer,
            additional_answers=additional_answers,
            correcthint=primary_correcthint
        )

        # Assert primary answer is graded correctly.
        correct_map = problem.grade_answers({'1_2_1': primary_answer})
        assert correct_map.get_correctness('1_2_1') == 'correct'
        assert primary_correcthint in correct_map.get_msg('1_2_1')

        # Assert additional answers are graded correct
        for answer, correcthint in additional_answers.items():
            correct_map = problem.grade_answers({'1_2_1': answer})
            assert correct_map.get_correctness('1_2_1') == 'correct'
            assert correcthint in correct_map.get_msg('1_2_1')

    def test_additional_answer_get_score(self):
        """
        Test `get_score` is working for additional answers.
        """
        problem = self.build_problem(answer='100', additional_answers={'1': ''})
        responder = list(problem.responders.values())[0]

        # Check primary answer.
        new_cmap = responder.get_score({'1_2_1': '100'})
        assert new_cmap.get_correctness('1_2_1') == 'correct'

        # Check additional answer.
        new_cmap = responder.get_score({'1_2_1': '1'})
        assert new_cmap.get_correctness('1_2_1') == 'correct'

        # Check any wrong answer.
        new_cmap = responder.get_score({'1_2_1': '2'})
        assert new_cmap.get_correctness('1_2_1') == 'incorrect'

    def test_grade_range_tolerance_partial_credit(self):
        problem_setup = [
            # [given_answer,
            #   [list of correct responses],
            #   [list of incorrect responses],
            #   [list of partially correct responses]]
            [
                '[5, 7)',
                ['5', '6', '6.999'],
                ['0', '100'],
                ['4', '8']
            ],
            [
                '[1.6e-5, 1.9e24)',
                ['0.000016', '1.6*10^-5', '1.59e24'],
                ['-1e26', '1.9e26', '1.9*10^26'],
                ['0', '2e24']
            ],
            [
                '[0, 1.6e-5]',
                ['1.6*10^-5'],
                ['2'],
                ['1.9e-5', '-1e-6']
            ],
            [
                '(1.6e-5, 10]',
                ['2'],
                ['-20', '30'],
                ['-1', '12']
            ],
        ]
        for given_answer, correct_responses, incorrect_responses, partial_responses in problem_setup:
            problem = self.build_problem(answer=given_answer, credit_type='close')
            self.assert_multiple_partial(problem, correct_responses, incorrect_responses, partial_responses)

    def test_grade_range_tolerance_exceptions(self):
        # no complex number in range tolerance staff answer
        problem = self.build_problem(answer='[1j, 5]')
        input_dict = {'1_2_1': '3'}
        with pytest.raises(StudentInputError):
            problem.grade_answers(input_dict)

        # no complex numbers in student ansers to range tolerance problems
        problem = self.build_problem(answer='(1, 5)')
        input_dict = {'1_2_1': '1*J'}
        with pytest.raises(StudentInputError):
            problem.grade_answers(input_dict)

        # test isnan student input: no exception,
        # but problem should be graded as incorrect
        problem = self.build_problem(answer='(1, 5)')
        input_dict = {'1_2_1': ''}
        correct_map = problem.grade_answers(input_dict)
        correctness = correct_map.get_correctness('1_2_1')
        assert correctness == 'incorrect'

        # test invalid range tolerance answer
        with pytest.raises(StudentInputError):
            problem = self.build_problem(answer='(1 5)')

        # test empty boundaries
        problem = self.build_problem(answer='(1, ]')
        input_dict = {'1_2_1': '3'}
        with pytest.raises(StudentInputError):
            problem.grade_answers(input_dict)

    def test_grade_exact(self):
        problem = self.build_problem(answer=4)
        correct_responses = ["4", "4.0", "4.00"]
        incorrect_responses = ["", "3.9", "4.1", "0"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_grade_partial(self):
        # First: "list"-style grading scheme.
        problem = self.build_problem(
            answer=4,
            credit_type='list',
            partial_answers='2,8,-4'
        )
        correct_responses = ["4", "4.0"]
        incorrect_responses = ["1", "3", "4.1", "0", "-2"]
        partial_responses = ["2", "2.0", "-4", "-4.0", "8", "8.0"]
        self.assert_multiple_partial(problem, correct_responses, incorrect_responses, partial_responses)

        # Second: "close"-style grading scheme. Default range is twice tolerance.
        problem = self.build_problem(
            answer=4,
            tolerance=0.2,
            credit_type='close'
        )
        correct_responses = ["4", "4.1", "3.9"]
        incorrect_responses = ["1", "3", "4.5", "0", "-2"]
        partial_responses = ["4.3", "3.7"]
        self.assert_multiple_partial(problem, correct_responses, incorrect_responses, partial_responses)

        # Third: "close"-style grading scheme with partial_range set.
        problem = self.build_problem(
            answer=4,
            tolerance=0.2,
            partial_range=3,
            credit_type='close'
        )
        correct_responses = ["4", "4.1"]
        incorrect_responses = ["1", "3", "0", "-2"]
        partial_responses = ["4.5", "3.5"]
        self.assert_multiple_partial(problem, correct_responses, incorrect_responses, partial_responses)

        # Fourth: both "list"- and "close"-style grading schemes at once.
        problem = self.build_problem(
            answer=4,
            tolerance=0.2,
            partial_range=3,
            credit_type='close,list',
            partial_answers='2,8,-4'
        )
        correct_responses = ["4", "4.0"]
        incorrect_responses = ["1", "3", "0", "-2"]
        partial_responses = ["2", "2.1", "1.5", "8", "7.5", "8.1", "-4", "-4.15", "-3.5", "4.5", "3.5"]
        self.assert_multiple_partial(problem, correct_responses, incorrect_responses, partial_responses)

    def test_numerical_valid_grading_schemes(self):
        # 'bongo' is not a valid grading scheme.
        problem = self.build_problem(answer=4, tolerance=0.1, credit_type='bongo')
        input_dict = {'1_2_1': '4'}
        with pytest.raises(LoncapaProblemError):
            problem.grade_answers(input_dict)

    def test_grade_decimal_tolerance(self):
        problem = self.build_problem(answer=4, tolerance=0.1)
        correct_responses = ["4.0", "4.00", "4.09", "3.91"]
        incorrect_responses = ["", "4.11", "3.89", "0"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_grade_percent_tolerance(self):
        # Positive only range
        problem = self.build_problem(answer=4, tolerance="10%")
        correct_responses = ["4.0", "4.00", "4.39", "3.61"]
        incorrect_responses = ["", "4.41", "3.59", "0"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)
        # Negative only range
        problem = self.build_problem(answer=-4, tolerance="10%")
        correct_responses = ["-4.0", "-4.00", "-4.39", "-3.61"]
        incorrect_responses = ["", "-4.41", "-3.59", "0"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)
        # Mixed negative/positive range
        problem = self.build_problem(answer=1, tolerance="200%")
        correct_responses = ["1", "1.00", "2.99", "0.99"]
        incorrect_responses = ["", "3.01", "-1.01"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_grade_percent_tolerance_with_spaces(self):
        """
        Tests that system does not throw an error when tolerance data contains spaces before or after.
        """
        problem = self.build_problem(answer=4, tolerance="10% ")
        correct_responses = ["4.0", "4.00", "4.39", "3.61"]
        incorrect_responses = ["", "4.41", "3.59", "0"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_floats(self):
        """
        Default tolerance for all responsetypes is 1e-3%.
        """
        problem_setup = [
            # [given_answer, [list of correct responses], [list of incorrect responses]]
            [1, ["1"], ["1.1"]],
            [2.0, ["2.0"], ["1.0"]],
            [4, ["4.0", "4.00004"], ["4.00005"]],
            [0.00016, ["1.6*10^-4"], [""]],
            [0.000016, ["1.6*10^-5"], ["0.000165"]],
            [1.9e24, ["1.9*10^24"], ["1.9001*10^24"]],
            [2e-15, ["2*10^-15"], [""]],
            [3141592653589793238., ["3141592653589793115."], [""]],
            [0.1234567, ["0.123456", "0.1234561"], ["0.123451"]],
            [1e-5, ["1e-5", "1.0e-5"], ["-1e-5", "2*1e-5"]],
        ]
        for given_answer, correct_responses, incorrect_responses in problem_setup:
            problem = self.build_problem(answer=given_answer)
            self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_percentage(self):
        """
        Test percentage
        """
        problem_setup = [
            # [given_answer, [list of correct responses], [list of incorrect responses]]
            ["1%", ["1%", "1.0%", "1.00%", "0.01"], [""]],
            ["2.0%", ["2%", "2.0%", "2.00%", "0.02"], [""]],
            ["4.00%", ["4%", "4.0%", "4.00%", "0.04"], [""]],
        ]
        for given_answer, correct_responses, incorrect_responses in problem_setup:
            problem = self.build_problem(answer=given_answer)
            self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_grade_with_script(self):
        script_text = "computed_response = math.sqrt(4)"
        problem = self.build_problem(answer="$computed_response", script=script_text)
        correct_responses = ["2", "2.0"]
        incorrect_responses = ["", "2.01", "1.99", "0"]
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_raises_zero_division_err(self):
        """See if division by zero is handled correctly."""
        problem = self.build_problem(answer="1")  # Answer doesn't matter
        input_dict = {'1_2_1': '1/0'}
        with pytest.raises(StudentInputError):
            problem.grade_answers(input_dict)

    def test_staff_inputs_expressions(self):
        """Test that staff may enter in an expression as the answer."""
        problem = self.build_problem(answer="1/3", tolerance=1e-3)
        correct_responses = ["1/3", "0.333333"]
        incorrect_responses = []
        self.assert_multiple_grade(problem, correct_responses, incorrect_responses)

    def test_staff_inputs_expressions_legacy(self):
        """Test that staff may enter in a complex number as the answer."""
        problem = self.build_problem(answer="1+1j", tolerance=1e-3)
        self.assert_grade(problem, '1+j', 'correct')

    @mock.patch('xmodule.capa.responsetypes.log')
    def test_staff_inputs_bad_syntax(self, mock_log):
        """Test that staff may enter in a complex number as the answer."""
        staff_ans = "clearly bad syntax )[+1e"
        problem = self.build_problem(answer=staff_ans, tolerance=1e-3)

        msg = "There was a problem with the staff answer to this problem"
        with self.assertRaisesRegex(StudentInputError, msg):
            self.assert_grade(problem, '1+j', 'correct')

        mock_log.debug.assert_called_once_with(
            "Content error--answer '%s' is not a valid number", staff_ans
        )

    @mock.patch('xmodule.capa.responsetypes.log')
    def test_responsetype_i18n(self, mock_log):  # lint-amnesty, pylint: disable=unused-argument
        """Test that LoncapaSystem has an i18n that works."""
        staff_ans = "clearly bad syntax )[+1e"
        problem = self.build_problem(answer=staff_ans, tolerance=1e-3)

        class FakeTranslations(object):
            """A fake gettext.Translations object."""
            def ugettext(self, text):
                """Return the 'translation' of `text`."""
                if text == "There was a problem with the staff answer to this problem.":
                    text = "TRANSLATED!"
                return text
            gettext = ugettext

        problem.capa_system.i18n = FakeTranslations()

        with self.assertRaisesRegex(StudentInputError, "TRANSLATED!"):
            self.assert_grade(problem, '1+j', 'correct')

    def test_grade_infinity(self):
        """
        Check that infinity doesn't automatically get marked correct.

        This resolves a bug where a problem with relative tolerance would
        pass with any arbitrarily large student answer.
        """
        mapping = {
            'some big input': float('inf'),
            'some neg input': -float('inf'),
            'weird NaN input': float('nan'),
            '4': 4
        }

        def evaluator_side_effect(_, __, math_string):
            """Look up the given response for `math_string`."""
            return mapping[math_string]

        problem = self.build_problem(answer=4, tolerance='10%')

        with mock.patch('xmodule.capa.responsetypes.evaluator') as mock_eval:
            mock_eval.side_effect = evaluator_side_effect
            self.assert_grade(problem, 'some big input', 'incorrect')
            self.assert_grade(problem, 'some neg input', 'incorrect')
            self.assert_grade(problem, 'weird NaN input', 'incorrect')

    def test_err_handling(self):
        """
        See that `StudentInputError`s are raised when things go wrong.
        """
        problem = self.build_problem(answer=4)

        errors = [  # (exception raised, message to student)
            (calc.UndefinedVariable("Invalid Input: x not permitted in answer as a variable"),
             r"Invalid Input: x not permitted in answer as a variable"),
            (ValueError("factorial() mess-up"), "Factorial function evaluated outside its domain"),
            (ValueError(), "Could not interpret '.*' as a number"),
            (pyparsing.ParseException("oopsie"), "Invalid math syntax"),
            (ZeroDivisionError(), "Could not interpret '.*' as a number")
        ]

        with mock.patch('xmodule.capa.responsetypes.evaluator') as mock_eval:
            for err, msg_regex in errors:

                def evaluator_side_effect(_, __, math_string):
                    """Raise an error only for the student input."""
                    if math_string != '4':
                        raise err  # lint-amnesty, pylint: disable=cell-var-from-loop
                mock_eval.side_effect = evaluator_side_effect

                with self.assertRaisesRegex(StudentInputError, msg_regex):
                    problem.grade_answers({'1_2_1': 'foobar'})

    def test_compare_answer(self):
        """Tests the answer compare function."""
        problem = self.build_problem(answer="42")
        responder = list(problem.responders.values())[0]
        assert responder.compare_answer('48', '8*6')
        assert not responder.compare_answer('48', '9*5')

    def test_validate_answer(self):
        """Tests the answer validation function."""
        problem = self.build_problem(answer="42")
        responder = list(problem.responders.values())[0]
        assert responder.validate_answer('23.5')
        assert not responder.validate_answer('fish')


@use_unsafe_codejail()
class CustomResponseTest(ResponseTest):  # pylint: disable=missing-class-docstring
    xml_factory_class = CustomResponseXMLFactory

    def test_inline_code(self):
        # For inline code, we directly modify global context variables
        # 'answers' is a list of answers provided to us
        # 'correct' is a list we fill in with True/False
        # 'expect' is given to us (if provided in the XML)
        inline_script = """correct[0] = 'correct' if (answers['1_2_1'] == expect) else 'incorrect'"""
        problem = self.build_problem(answer=inline_script, expect="42")

        # Check results
        self.assert_grade(problem, '42', 'correct')
        self.assert_grade(problem, '0', 'incorrect')

    def test_inline_message(self):
        # Inline code can update the global messages list
        # to pass messages to the CorrectMap for a particular input
        # The code can also set the global overall_message (str)
        # to pass a message that applies to the whole response
        inline_script = textwrap.dedent("""
            messages[0] = "Test Message"
            overall_message = "Overall message"
            """)
        problem = self.build_problem(answer=inline_script)

        input_dict = {'1_2_1': '0'}
        correctmap = problem.grade_answers(input_dict)

        # Check that the message for the particular input was received
        input_msg = correctmap.get_msg('1_2_1')
        assert input_msg == 'Test Message'

        # Check that the overall message (for the whole response) was received
        overall_msg = correctmap.get_overall_message()
        assert overall_msg == 'Overall message'

    def test_inline_randomization(self):
        # Make sure the seed from the problem gets fed into the script execution.
        inline_script = "messages[0] = {code}".format(code=self._get_random_number_code())
        problem = self.build_problem(answer=inline_script)

        input_dict = {'1_2_1': '0'}
        correctmap = problem.grade_answers(input_dict)

        input_msg = correctmap.get_msg('1_2_1')
        assert input_msg == self._get_random_number_result(problem.seed)

    def test_function_code_single_input(self):
        # For function code, we pass in these arguments:
        #
        #   'expect' is the expect attribute of the <customresponse>
        #
        #   'answer_given' is the answer the student gave (if there is just one input)
        #       or an ordered list of answers (if there are multiple inputs)
        #
        # The function should return a dict of the form
        # { 'ok': BOOL or STRING, 'msg': STRING } (no 'grade_decimal' key to test that it's optional)
        #
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                partial_credit = '21'
                if answer_given == expect:
                    retval = True
                elif answer_given == partial_credit:
                    retval = 'partial'
                else:
                    retval = False
                return {'ok': retval, 'msg': 'Message text'}
        """)

        problem = self.build_problem(script=script, cfn="check_func", expect="42")

        # Correct answer
        input_dict = {'1_2_1': '42'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')
        npoints = correct_map.get_npoints('1_2_1')

        assert correctness == 'correct'
        assert msg == 'Message text'
        assert npoints == 1

        # Partially Credit answer
        input_dict = {'1_2_1': '21'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')
        npoints = correct_map.get_npoints('1_2_1')

        assert correctness == 'partially-correct'
        assert msg == 'Message text'
        assert 0 <= npoints <= 1

        # Incorrect answer
        input_dict = {'1_2_1': '0'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')
        npoints = correct_map.get_npoints('1_2_1')

        assert correctness == 'incorrect'
        assert msg == 'Message text'
        assert npoints == 0

    def test_function_code_single_input_decimal_score(self):
        # For function code, we pass in these arguments:
        #
        #   'expect' is the expect attribute of the <customresponse>
        #
        #   'answer_given' is the answer the student gave (if there is just one input)
        #       or an ordered list of answers (if there are multiple inputs)
        #
        # The function should return a dict of the form
        # { 'ok': BOOL or STRING, 'msg': STRING, 'grade_decimal': FLOAT }
        #
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                partial_credit = '21'
                if answer_given == expect:
                    retval = True
                    score = 0.9
                elif answer_given == partial_credit:
                    retval = 'partial'
                    score = 0.5
                else:
                    retval = False
                    score = 0.1
                return {
                    'ok': retval,
                    'msg': 'Message text',
                    'grade_decimal': score,
                }
        """)

        problem = self.build_problem(script=script, cfn="check_func", expect="42")

        # Correct answer
        input_dict = {'1_2_1': '42'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_npoints('1_2_1') == 0.9
        assert correct_map.get_correctness('1_2_1') == 'correct'

        # Incorrect answer
        input_dict = {'1_2_1': '43'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_npoints('1_2_1') == 0.1
        assert correct_map.get_correctness('1_2_1') == 'incorrect'

        # Partially Correct answer
        input_dict = {'1_2_1': '21'}
        correct_map = problem.grade_answers(input_dict)
        assert correct_map.get_npoints('1_2_1') == 0.5
        assert correct_map.get_correctness('1_2_1') == 'partially-correct'

    def test_script_context(self):
        # Ensure that python script variables can be used in the "expect" and "answer" fields,

        script = script = textwrap.dedent("""
            expected_ans = 42

            def check_func(expect, answer_given):
                return answer_given == expect
        """)

        problems = (
            self.build_problem(script=script, cfn="check_func", expect="$expected_ans"),
            self.build_problem(script=script, cfn="check_func", answer_attr="$expected_ans")
        )

        input_dict = {'1_2_1': '42'}

        for problem in problems:
            correctmap = problem.grade_answers(input_dict)

            # CustomResponse also adds 'expect' to the problem context; check that directly first:
            assert problem.context['expect'] == '42'

            # Also make sure the problem was graded correctly:
            correctness = correctmap.get_correctness('1_2_1')
            assert correctness == 'correct'

    def test_function_code_multiple_input_no_msg(self):

        # Check functions also have the option of returning
        # a single boolean or string value
        # If true, mark all the inputs correct
        # If one is true but not the other, mark all partially correct
        # If false, mark all the inputs incorrect
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                if answer_given[0] == expect and answer_given[1] == expect:
                    retval = True
                elif answer_given[0] == expect or answer_given[1] == expect:
                    retval = 'partial'
                else:
                    retval = False
                return retval
        """)

        problem = self.build_problem(script=script, cfn="check_func",
                                     expect="42", num_inputs=2)

        # Correct answer -- expect both inputs marked correct
        input_dict = {'1_2_1': '42', '1_2_2': '42'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        assert correctness == 'correct'

        correctness = correct_map.get_correctness('1_2_2')
        assert correctness == 'correct'

        # One answer incorrect -- expect both inputs marked partially correct
        input_dict = {'1_2_1': '0', '1_2_2': '42'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        assert correctness == 'partially-correct'
        assert 0 <= correct_map.get_npoints('1_2_1') <= 1

        correctness = correct_map.get_correctness('1_2_2')
        assert correctness == 'partially-correct'
        assert 0 <= correct_map.get_npoints('1_2_2') <= 1

        # Both answers incorrect -- expect both inputs marked incorrect
        input_dict = {'1_2_1': '0', '1_2_2': '0'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        assert correctness == 'incorrect'

        correctness = correct_map.get_correctness('1_2_2')
        assert correctness == 'incorrect'

    def test_function_code_multiple_inputs(self):

        # If the <customresponse> has multiple inputs associated with it,
        # the check function can return a dict of the form:
        #
        # {'overall_message': STRING,
        #  'input_list': [{'ok': BOOL or STRING, 'msg': STRING}, ...] }
        # (no grade_decimal to test it's optional)
        #
        # 'overall_message' is displayed at the end of the response
        #
        # 'input_list' contains dictionaries representing the correctness
        #           and message for each input.
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                check1 = (int(answer_given[0]) == 1)
                check2 = (int(answer_given[1]) == 2)
                check3 = (int(answer_given[2]) == 3)
                check4 = 'partial' if answer_given[3] == 'four' else False
                return {'overall_message': 'Overall message',
                        'input_list': [
                            {'ok': check1,  'msg': 'Feedback 1'},
                            {'ok': check2,  'msg': 'Feedback 2'},
                            {'ok': check3,  'msg': 'Feedback 3'},
                            {'ok': check4,  'msg': 'Feedback 4'} ] }
            """)

        problem = self.build_problem(
            script=script,
            cfn="check_func",
            num_inputs=4
        )

        # Grade the inputs (one input incorrect)
        input_dict = {'1_2_1': '-999', '1_2_2': '2', '1_2_3': '3', '1_2_4': 'four'}
        correct_map = problem.grade_answers(input_dict)

        # Expect that we receive the overall message (for the whole response)
        assert correct_map.get_overall_message() == 'Overall message'

        # Expect that the inputs were graded individually
        assert correct_map.get_correctness('1_2_1') == 'incorrect'
        assert correct_map.get_correctness('1_2_2') == 'correct'
        assert correct_map.get_correctness('1_2_3') == 'correct'
        assert correct_map.get_correctness('1_2_4') == 'partially-correct'

        # Expect that the inputs were given correct npoints
        assert correct_map.get_npoints('1_2_1') == 0
        assert correct_map.get_npoints('1_2_2') == 1
        assert correct_map.get_npoints('1_2_3') == 1
        assert 0 <= correct_map.get_npoints('1_2_4') <= 1

        # Expect that we received messages for each individual input
        assert correct_map.get_msg('1_2_1') == 'Feedback 1'
        assert correct_map.get_msg('1_2_2') == 'Feedback 2'
        assert correct_map.get_msg('1_2_3') == 'Feedback 3'
        assert correct_map.get_msg('1_2_4') == 'Feedback 4'

    def test_function_code_multiple_inputs_decimal_score(self):

        # If the <customresponse> has multiple inputs associated with it,
        # the check function can return a dict of the form:
        #
        # {'overall_message': STRING,
        #  'input_list': [{'ok': BOOL or STRING,
        #                  'msg': STRING, 'grade_decimal': FLOAT}, ...] }
        #        #
        # 'input_list' contains dictionaries representing the correctness
        #           and message for each input.
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                check1 = (int(answer_given[0]) == 1)
                check2 = (int(answer_given[1]) == 2)
                check3 = (int(answer_given[2]) == 3)
                check4 = 'partial' if answer_given[3] == 'four' else False
                score1 = 0.9 if check1 else 0.1
                score2 = 0.9 if check2 else 0.1
                score3 = 0.9 if check3 else 0.1
                score4 = 0.7 if check4 == 'partial' else 0.1
                return {
                    'input_list': [
                        {'ok': check1, 'grade_decimal': score1, 'msg': 'Feedback 1'},
                        {'ok': check2, 'grade_decimal': score2, 'msg': 'Feedback 2'},
                        {'ok': check3, 'grade_decimal': score3, 'msg': 'Feedback 3'},
                        {'ok': check4, 'grade_decimal': score4, 'msg': 'Feedback 4'},
                    ]
                }
            """)

        problem = self.build_problem(script=script, cfn="check_func", num_inputs=4)

        # Grade the inputs (one input incorrect)
        input_dict = {'1_2_1': '-999', '1_2_2': '2', '1_2_3': '3', '1_2_4': 'four'}
        correct_map = problem.grade_answers(input_dict)

        # Expect that the inputs were graded individually
        assert correct_map.get_correctness('1_2_1') == 'incorrect'
        assert correct_map.get_correctness('1_2_2') == 'correct'
        assert correct_map.get_correctness('1_2_3') == 'correct'
        assert correct_map.get_correctness('1_2_4') == 'partially-correct'

        # Expect that the inputs were given correct npoints
        assert correct_map.get_npoints('1_2_1') == 0.1
        assert correct_map.get_npoints('1_2_2') == 0.9
        assert correct_map.get_npoints('1_2_3') == 0.9
        assert correct_map.get_npoints('1_2_4') == 0.7

    def test_function_code_with_extra_args(self):
        script = textwrap.dedent("""\
                    def check_func(expect, answer_given, options, dynamath):
                        assert options == "xyzzy", "Options was %r" % options
                        partial_credit = '21'
                        if answer_given == expect:
                            retval = True
                        elif answer_given == partial_credit:
                            retval = 'partial'
                        else:
                            retval = False
                        return {'ok': retval, 'msg': 'Message text'}
                    """)

        problem = self.build_problem(
            script=script,
            cfn="check_func",
            expect="42",
            options="xyzzy",
            cfn_extra_args="options dynamath"
        )

        # Correct answer
        input_dict = {'1_2_1': '42'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')

        assert correctness == 'correct'
        assert msg == 'Message text'

        # Partially Correct answer
        input_dict = {'1_2_1': '21'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')

        assert correctness == 'partially-correct'
        assert msg == 'Message text'

        # Incorrect answer
        input_dict = {'1_2_1': '0'}
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')

        assert correctness == 'incorrect'
        assert msg == 'Message text'

    def test_function_code_with_attempt_number(self):
        script = textwrap.dedent("""\
                    def gradeit(expect, ans, **kwargs):
                        attempt = kwargs["attempt"]
                        message = "This is attempt number {}".format(str(attempt))
                        return {
                            'input_list': [
                                { 'ok': True, 'msg': message},
                            ]
                        }
                    """)

        problem = self.build_problem(
            script=script,
            cfn="gradeit",
            expect="42",
            cfn_extra_args="attempt"
        )

        # first attempt
        input_dict = {'1_2_1': '42'}
        problem.context['attempt'] = 1
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')

        assert correctness == 'correct'
        assert msg == 'This is attempt number 1'

        # second attempt
        problem.context['attempt'] = 2
        correct_map = problem.grade_answers(input_dict)

        correctness = correct_map.get_correctness('1_2_1')
        msg = correct_map.get_msg('1_2_1')

        assert correctness == 'correct'
        assert msg == 'This is attempt number 2'

    def test_multiple_inputs_return_one_status(self):
        # When given multiple inputs, the 'answer_given' argument
        # to the check_func() is a list of inputs
        #
        # The sample script below marks the problem as correct
        # if and only if it receives answer_given=[1,2,3]
        # (or string values ['1','2','3'])
        #
        # Since we return a dict describing the status of one input,
        # we expect that the same 'ok' value is applied to each
        # of the inputs.
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                check1 = (int(answer_given[0]) == 1)
                check2 = (int(answer_given[1]) == 2)
                check3 = (int(answer_given[2]) == 3)
                if (int(answer_given[0]) == -1) and check2 and check3:
                    return {'ok': 'partial',
                            'msg': 'Message text'}
                else:
                    return {'ok': (check1 and check2 and check3),
                            'msg': 'Message text'}
            """)

        problem = self.build_problem(script=script,
                                     cfn="check_func", num_inputs=3)

        # Grade the inputs (one input incorrect)
        input_dict = {'1_2_1': '-999', '1_2_2': '2', '1_2_3': '3'}
        correct_map = problem.grade_answers(input_dict)

        # Everything marked incorrect
        assert correct_map.get_correctness('1_2_1') == 'incorrect'
        assert correct_map.get_correctness('1_2_2') == 'incorrect'
        assert correct_map.get_correctness('1_2_3') == 'incorrect'

        # Grade the inputs (one input partially correct)
        input_dict = {'1_2_1': '-1', '1_2_2': '2', '1_2_3': '3'}
        correct_map = problem.grade_answers(input_dict)

        # Everything marked partially correct
        assert correct_map.get_correctness('1_2_1') == 'partially-correct'
        assert correct_map.get_correctness('1_2_2') == 'partially-correct'
        assert correct_map.get_correctness('1_2_3') == 'partially-correct'

        # Grade the inputs (everything correct)
        input_dict = {'1_2_1': '1', '1_2_2': '2', '1_2_3': '3'}
        correct_map = problem.grade_answers(input_dict)

        # Everything marked incorrect
        assert correct_map.get_correctness('1_2_1') == 'correct'
        assert correct_map.get_correctness('1_2_2') == 'correct'
        assert correct_map.get_correctness('1_2_3') == 'correct'

        # Message is interpreted as an "overall message"
        assert correct_map.get_overall_message() == 'Message text'

    def test_script_exception_function(self):

        # Construct a script that will raise an exception
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                raise Exception("Test")
            """)

        problem = self.build_problem(script=script, cfn="check_func")

        # Expect that an exception gets raised when we check the answer
        with pytest.raises(ResponseError):
            problem.grade_answers({'1_2_1': '42'})

    def test_script_exception_inline(self):

        # Construct a script that will raise an exception
        script = 'raise Exception("Test")'
        problem = self.build_problem(answer=script)

        # Expect that an exception gets raised when we check the answer
        with pytest.raises(ResponseError):
            problem.grade_answers({'1_2_1': '42'})

    def test_invalid_dict_exception(self):

        # Construct a script that passes back an invalid dict format
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                return {'invalid': 'test'}
            """)

        problem = self.build_problem(script=script, cfn="check_func")

        # Expect that an exception gets raised when we check the answer
        with pytest.raises(ResponseError):
            problem.grade_answers({'1_2_1': '42'})

    def test_setup_randomization(self):
        # Ensure that the problem setup script gets the random seed from the problem.
        script = textwrap.dedent("""
            num = {code}
            """.format(code=self._get_random_number_code()))
        problem = self.build_problem(script=script)
        assert problem.context['num'] == self._get_random_number_result(problem.seed)

    def test_check_function_randomization(self):
        # The check function should get random-seeded from the problem.
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                return {{'ok': True, 'msg': {code} }}
        """.format(code=self._get_random_number_code()))

        problem = self.build_problem(script=script, cfn="check_func", expect="42")
        input_dict = {'1_2_1': '42'}
        correct_map = problem.grade_answers(input_dict)
        msg = correct_map.get_msg('1_2_1')
        assert msg == self._get_random_number_result(problem.seed)

    def test_random_isnt_none(self):
        # Bug LMS-500 says random.seed(10) fails with:
        #     File "<string>", line 61, in <module>
        #     File "/usr/lib/python2.7/random.py", line 116, in seed
        #       super(Random, self).seed(a)
        #   TypeError: must be type, not None

        r = random.Random()
        r.seed(10)
        num = r.randint(0, 1e9)

        script = textwrap.dedent("""
            random.seed(10)
            num = random.randint(0, 1e9)
            """)
        problem = self.build_problem(script=script)
        assert problem.context['num'] == num

    def test_module_imports_inline(self):
        '''
        Check that the correct modules are available to custom
        response scripts
        '''

        for module_name in ['random', 'numpy', 'math', 'scipy',
                            'calc', 'eia', 'chemcalc', 'chemtools',
                            'miller', 'draganddrop']:

            # Create a script that checks that the name is defined
            # If the name is not defined, then the script
            # will raise an exception
            script = textwrap.dedent('''
            correct[0] = 'correct'
            assert('%s' in globals())''' % module_name)

            # Create the problem
            problem = self.build_problem(answer=script)

            # Expect that we can grade an answer without
            # getting an exception
            try:
                problem.grade_answers({'1_2_1': '42'})

            except ResponseError:
                self.fail("Could not use name '{0}s' in custom response".format(module_name))

    def test_module_imports_function(self):
        '''
        Check that the correct modules are available to custom
        response scripts
        '''

        for module_name in ['random', 'numpy', 'math', 'scipy',
                            'calc', 'eia', 'chemcalc', 'chemtools',
                            'miller', 'draganddrop']:

            # Create a script that checks that the name is defined
            # If the name is not defined, then the script
            # will raise an exception
            script = textwrap.dedent('''
            def check_func(expect, answer_given):
                assert('%s' in globals())
                return True''' % module_name)

            # Create the problem
            problem = self.build_problem(script=script, cfn="check_func")

            # Expect that we can grade an answer without
            # getting an exception
            try:
                problem.grade_answers({'1_2_1': '42'})

            except ResponseError:
                self.fail("Could not use name '{0}s' in custom response".format(module_name))

    def test_python_lib_zip_is_available(self):
        # Prove that we can import code from a zipfile passed down to us.

        # Make a zipfile with one module in it with one function.
        zipstring = io.BytesIO()
        zipf = zipfile.ZipFile(zipstring, "w")  # lint-amnesty, pylint: disable=consider-using-with
        zipf.writestr("my_helper.py", textwrap.dedent("""\
            def seventeen():
                return 17
            """))
        zipf.close()

        # Use that module in our Python script.
        script = textwrap.dedent("""
            import my_helper
            num = my_helper.seventeen()
            """)
        capa_system = mock_capa_system()
        capa_system.get_python_lib_zip = lambda: zipstring.getvalue()  # lint-amnesty, pylint: disable=unnecessary-lambda
        problem = self.build_problem(script=script, capa_system=capa_system)
        assert problem.context['num'] == 17

    def test_function_code_multiple_inputs_order(self):
        # Ensure that order must be correct according to sub-problem position
        script = textwrap.dedent("""
            def check_func(expect, answer_given):
                check1 = (int(answer_given[0]) == 1)
                check2 = (int(answer_given[1]) == 2)
                check3 = (int(answer_given[2]) == 3)
                check4 = (int(answer_given[3]) == 4)
                check5 = (int(answer_given[4]) == 5)
                check6 = (int(answer_given[5]) == 6)
                check7 = (int(answer_given[6]) == 7)
                check8 = (int(answer_given[7]) == 8)
                check9 = (int(answer_given[8]) == 9)
                check10 = (int(answer_given[9]) == 10)
                check11 = (int(answer_given[10]) == 11)
                return {'overall_message': 'Overall message',
                            'input_list': [
                                { 'ok': check1, 'msg': '1'},
                                { 'ok': check2, 'msg': '2'},
                                { 'ok': check3, 'msg': '3'},
                                { 'ok': check4, 'msg': '4'},
                                { 'ok': check5, 'msg': '5'},
                                { 'ok': check6, 'msg': '6'},
                                { 'ok': check7, 'msg': '7'},
                                { 'ok': check8, 'msg': '8'},
                                { 'ok': check9, 'msg': '9'},
                                { 'ok': check10, 'msg': '10'},
                                { 'ok': check11, 'msg': '11'},
                ]}
            """)

        problem = self.build_problem(script=script, cfn="check_func", num_inputs=11)

        # Grade the inputs showing out of order
        input_dict = {
            '1_2_1': '1',
            '1_2_2': '2',
            '1_2_3': '3',
            '1_2_4': '4',
            '1_2_5': '5',
            '1_2_6': '6',
            '1_2_10': '10',
            '1_2_11': '16',
            '1_2_7': '7',
            '1_2_8': '8',
            '1_2_9': '9'
        }

        correct_order = [
            '1_2_1', '1_2_2', '1_2_3', '1_2_4', '1_2_5', '1_2_6', '1_2_7', '1_2_8', '1_2_9', '1_2_10', '1_2_11'
        ]

        correct_map = problem.grade_answers(input_dict)

        assert list(problem.student_answers.keys()) != correct_order

        # euqal to correct order after sorting at get_score
        self.assertListEqual(list(problem.responders.values())[0].context['idset'], correct_order)

        assert correct_map.get_correctness('1_2_1') == 'correct'
        assert correct_map.get_correctness('1_2_9') == 'correct'
        assert correct_map.get_correctness('1_2_11') == 'incorrect'

        assert correct_map.get_msg('1_2_1') == '1'
        assert correct_map.get_msg('1_2_9') == '9'
        assert correct_map.get_msg('1_2_11') == '11'


@use_unsafe_codejail()
class SchematicResponseTest(ResponseTest):
    """
    Class containing setup and tests for Schematic responsetype.
    """
    xml_factory_class = SchematicResponseXMLFactory

    def test_grade(self):
        # Most of the schematic-specific work is handled elsewhere
        # (in client-side JavaScript)
        # The <schematicresponse> is responsible only for executing the
        # Python code in <answer> with *submission* (list)
        # in the global context.

        # To test that the context is set up correctly,
        # we create a script that sets *correct* to true
        # if and only if we find the *submission* (list)
        script = "correct = ['correct' if 'test' in submission[0] else 'incorrect']"
        problem = self.build_problem(answer=script)

        # The actual dictionary would contain schematic information
        # sent from the JavaScript simulation
        submission_dict = {'test': 'the_answer'}
        input_dict = {'1_2_1': json.dumps(submission_dict)}
        correct_map = problem.grade_answers(input_dict)

        # Expect that the problem is graded as true
        # (That is, our script verifies that the context
        # is what we expect)
        assert correct_map.get_correctness('1_2_1') == 'correct'

    def test_check_function_randomization(self):
        # The check function should get a random seed from the problem.
        script = "correct = ['correct' if (submission[0]['num'] == {code}) else 'incorrect']".format(code=self._get_random_number_code())  # lint-amnesty, pylint: disable=line-too-long
        problem = self.build_problem(answer=script)

        submission_dict = {'num': self._get_random_number_result(problem.seed)}
        input_dict = {'1_2_1': json.dumps(submission_dict)}
        correct_map = problem.grade_answers(input_dict)

        assert correct_map.get_correctness('1_2_1') == 'correct'

    def test_script_exception(self):
        # Construct a script that will raise an exception
        script = "raise Exception('test')"
        problem = self.build_problem(answer=script)

        # Expect that an exception gets raised when we check the answer
        with pytest.raises(ResponseError):
            submission_dict = {'test': 'test'}
            input_dict = {'1_2_1': json.dumps(submission_dict)}
            problem.grade_answers(input_dict)


class AnnotationResponseTest(ResponseTest):  # lint-amnesty, pylint: disable=missing-class-docstring
    xml_factory_class = AnnotationResponseXMLFactory

    def test_grade(self):
        (correct, partially, incorrect) = ('correct', 'partially-correct', 'incorrect')

        answer_id = '1_2_1'
        options = (('x', correct), ('y', partially), ('z', incorrect))
        make_answer = lambda option_ids: {answer_id: json.dumps({'options': option_ids})}

        tests = [
            {'correctness': correct, 'points': 2, 'answers': make_answer([0])},
            {'correctness': partially, 'points': 1, 'answers': make_answer([1])},
            {'correctness': incorrect, 'points': 0, 'answers': make_answer([2])},
            {'correctness': incorrect, 'points': 0, 'answers': make_answer([0, 1, 2])},
            {'correctness': incorrect, 'points': 0, 'answers': make_answer([])},
            {'correctness': incorrect, 'points': 0, 'answers': make_answer('')},
            {'correctness': incorrect, 'points': 0, 'answers': make_answer(None)},
            {'correctness': incorrect, 'points': 0, 'answers': {answer_id: 'null'}},
        ]

        for test in tests:
            expected_correctness = test['correctness']
            expected_points = test['points']
            answers = test['answers']

            problem = self.build_problem(options=options)
            correct_map = problem.grade_answers(answers)
            actual_correctness = correct_map.get_correctness(answer_id)
            actual_points = correct_map.get_npoints(answer_id)

            assert expected_correctness == actual_correctness,\
                ('%s should be marked %s' % (answer_id, expected_correctness))
            assert expected_points == actual_points, ('%s should have %d points' % (answer_id, expected_points))


@use_unsafe_codejail()
class ChoiceTextResponseTest(ResponseTest):
    """
    Class containing setup and tests for ChoiceText responsetype.
    """

    xml_factory_class = ChoiceTextResponseXMLFactory

    # `TEST_INPUTS` is a dictionary mapping from
    # test_name to a representation of inputs for a test problem.
    TEST_INPUTS = {
        "1_choice_0_input_correct": [(True, [])],
        "1_choice_0_input_incorrect": [(False, [])],
        "1_choice_0_input_invalid_choice": [(False, []), (True, [])],
        "1_choice_1_input_correct": [(True, ["123"])],
        "1_input_script_correct": [(True, ["2"])],
        "1_input_script_incorrect": [(True, ["3.25"])],
        "1_choice_2_inputs_correct": [(True, ["123", "456"])],
        "1_choice_2_inputs_tolerance": [(True, ["123 + .5", "456 + 9"])],
        "1_choice_2_inputs_1_wrong": [(True, ["0", "456"])],
        "1_choice_2_inputs_both_wrong": [(True, ["0", "0"])],
        "1_choice_2_inputs_inputs_blank": [(True, ["", ""])],
        "1_choice_2_inputs_empty": [(False, [])],
        "1_choice_2_inputs_fail_tolerance": [(True, ["123 + 1.5", "456 + 9"])],
        "1_choice_1_input_within_tolerance": [(True, ["122.5"])],
        "1_choice_1_input_answer_incorrect": [(True, ["345"])],
        "1_choice_1_input_choice_incorrect": [(False, ["123"])],
        "2_choices_0_inputs_correct": [(False, []), (True, [])],
        "2_choices_0_inputs_incorrect": [(True, []), (False, [])],
        "2_choices_0_inputs_blank": [(False, []), (False, [])],
        "2_choices_1_input_1_correct": [(False, []), (True, ["123"])],
        "2_choices_1_input_1_incorrect": [(True, []), (False, ["123"])],
        "2_choices_1_input_input_wrong": [(False, []), (True, ["321"])],
        "2_choices_1_input_1_blank": [(False, []), (False, [])],
        "2_choices_1_input_2_correct": [(True, []), (False, ["123"])],
        "2_choices_1_input_2_incorrect": [(False, []), (True, ["123"])],
        "2_choices_2_inputs_correct": [(True, ["123"]), (False, [])],
        "2_choices_2_inputs_wrong_choice": [(False, ["123"]), (True, [])],
        "2_choices_2_inputs_wrong_input": [(True, ["321"]), (False, [])]
    }

    # `TEST_SCENARIOS` is a dictionary of the form
    # {Test_Name" : (Test_Problem_name, correctness)}
    # correctness represents whether the problem should be graded as
    # correct or incorrect when the test is run.
    TEST_SCENARIOS = {
        "1_choice_0_input_correct": ("1_choice_0_input", "correct"),
        "1_choice_0_input_incorrect": ("1_choice_0_input", "incorrect"),
        "1_choice_0_input_invalid_choice": ("1_choice_0_input", "incorrect"),
        "1_input_script_correct": ("1_input_script", "correct"),
        "1_input_script_incorrect": ("1_input_script", "incorrect"),
        "1_choice_2_inputs_correct": ("1_choice_2_inputs", "correct"),
        "1_choice_2_inputs_tolerance": ("1_choice_2_inputs", "correct"),
        "1_choice_2_inputs_1_wrong": ("1_choice_2_inputs", "incorrect"),
        "1_choice_2_inputs_both_wrong": ("1_choice_2_inputs", "incorrect"),
        "1_choice_2_inputs_inputs_blank": ("1_choice_2_inputs", "incorrect"),
        "1_choice_2_inputs_empty": ("1_choice_2_inputs", "incorrect"),
        "1_choice_2_inputs_fail_tolerance": ("1_choice_2_inputs", "incorrect"),
        "1_choice_1_input_correct": ("1_choice_1_input", "correct"),
        "1_choice_1_input_within_tolerance": ("1_choice_1_input", "correct"),
        "1_choice_1_input_answer_incorrect": ("1_choice_1_input", "incorrect"),
        "1_choice_1_input_choice_incorrect": ("1_choice_1_input", "incorrect"),
        "2_choices_0_inputs_correct": ("2_choices_0_inputs", "correct"),
        "2_choices_0_inputs_incorrect": ("2_choices_0_inputs", "incorrect"),
        "2_choices_0_inputs_blank": ("2_choices_0_inputs", "incorrect"),
        "2_choices_1_input_1_correct": ("2_choices_1_input_1", "correct"),
        "2_choices_1_input_1_incorrect": ("2_choices_1_input_1", "incorrect"),
        "2_choices_1_input_input_wrong": ("2_choices_1_input_1", "incorrect"),
        "2_choices_1_input_1_blank": ("2_choices_1_input_1", "incorrect"),
        "2_choices_1_input_2_correct": ("2_choices_1_input_2", "correct"),
        "2_choices_1_input_2_incorrect": ("2_choices_1_input_2", "incorrect"),
        "2_choices_2_inputs_correct": ("2_choices_2_inputs", "correct"),
        "2_choices_2_inputs_wrong_choice": ("2_choices_2_inputs", "incorrect"),
        "2_choices_2_inputs_wrong_input": ("2_choices_2_inputs", "incorrect")
    }

    # Dictionary that maps from problem_name to arguments for
    # _make_problem, that will create the problem.
    TEST_PROBLEM_ARGS = {
        "1_choice_0_input": {"choices": ("true", {}), "script": ''},
        "1_choice_1_input": {
            "choices": ("true", {"answer": "123", "tolerance": "1"}),
            "script": ''
        },

        "1_input_script": {
            "choices": ("true", {"answer": "$computed_response", "tolerance": "1"}),
            "script": "computed_response = math.sqrt(4)"
        },

        "1_choice_2_inputs": {
            "choices": [
                (
                    "true", (
                        {"answer": "123", "tolerance": "1"},
                        {"answer": "456", "tolerance": "10"}
                    )
                )
            ],
            "script": ''
        },
        "2_choices_0_inputs": {
            "choices": [("false", {}), ("true", {})],
            "script": ''

        },
        "2_choices_1_input_1": {
            "choices": [
                ("false", {}), ("true", {"answer": "123", "tolerance": "0"})
            ],
            "script": ''
        },
        "2_choices_1_input_2": {
            "choices": [("true", {}), ("false", {"answer": "123", "tolerance": "0"})],
            "script": ''
        },
        "2_choices_2_inputs": {
            "choices": [
                ("true", {"answer": "123", "tolerance": "0"}),
                ("false", {"answer": "999", "tolerance": "0"})
            ],
            "script": ''
        }
    }

    def _make_problem(self, choices, in_type='radiotextgroup', script=''):
        """
        Convenience method to fill in default values for script and
        type if needed, then call self.build_problem
        """
        return self.build_problem(
            choices=choices,
            type=in_type,
            script=script
        )

    def _make_answer_dict(self, choice_list):
        """
        Convenience method to make generation of answers less tedious,
        pass in an iterable argument with elements of the form: [bool, [ans,]]
        Will generate an answer dict for those options
        """

        answer_dict = {}
        for index, choice_answers_pair in enumerate(choice_list):
            # Choice is whether this choice is correct
            # Answers contains a list of answers to textinpts for the choice
            choice, answers = choice_answers_pair

            if choice:
                # Radio/Checkbox inputs in choicetext problems follow
                # a naming convention that gives them names ending with "bc"
                choice_id = "1_2_1_choiceinput_{index}bc".format(index=index)
                choice_value = "choiceinput_{index}".format(index=index)
                answer_dict[choice_id] = choice_value
            # Build the names for the numtolerance_inputs and add their answers
            # to `answer_dict`.
            for ind, answer in enumerate(answers):
                # In `answer_id` `index` represents the ordinality of the
                # choice and `ind` represents the ordinality of the
                # numtolerance_input inside the parent choice.
                answer_id = "1_2_1_choiceinput_{index}_numtolerance_input_{ind}".format(
                    index=index,
                    ind=ind
                )
                answer_dict[answer_id] = answer

        return answer_dict

    def test_invalid_xml(self):
        """
        Test that build problem raises errors for invalid options
        """
        with pytest.raises(Exception):
            self.build_problem(type="invalidtextgroup")

    def test_unchecked_input_not_validated(self):
        """
        Test that a student can have a non numeric answer in an unselected
        choice without causing an error to be raised when the problem is
        checked.
        """

        two_choice_two_input = self._make_problem(
            [
                ("true", {"answer": "123", "tolerance": "1"}),
                ("false", {})
            ],
            "checkboxtextgroup"
        )

        self.assert_grade(
            two_choice_two_input,
            self._make_answer_dict([(True, ["1"]), (False, ["Platypus"])]),
            "incorrect"
        )

    def test_interpret_error(self):
        """
        Test that student answers that cannot be interpeted as numbers
        cause the response type to raise an error.
        """
        two_choice_two_input = self._make_problem(
            [
                ("true", {"answer": "123", "tolerance": "1"}),
                ("false", {})
            ],
            "checkboxtextgroup"
        )

        with self.assertRaisesRegex(StudentInputError, "Could not interpret"):
            # Test that error is raised for input in selected correct choice.
            self.assert_grade(
                two_choice_two_input,
                self._make_answer_dict([(True, ["Platypus"])]),
                "correct"
            )

        with self.assertRaisesRegex(StudentInputError, "Could not interpret"):
            # Test that error is raised for input in selected incorrect choice.
            self.assert_grade(
                two_choice_two_input,
                self._make_answer_dict([(True, ["1"]), (True, ["Platypus"])]),
                "correct"
            )

    def test_staff_answer_error(self):
        broken_problem = self._make_problem(
            [("true", {"answer": "Platypus", "tolerance": "0"}),
             ("true", {"answer": "edX", "tolerance": "0"})
             ],
            "checkboxtextgroup"
        )
        with self.assertRaisesRegex(
            StudentInputError,
            "The Staff answer could not be interpreted as a number."
        ):
            self.assert_grade(
                broken_problem,
                self._make_answer_dict(
                    [(True, ["1"]), (True, ["1"])]
                ),
                "correct"
            )

    def test_radio_grades(self):
        """
        Test that confirms correct operation of grading when the inputtag is
        radiotextgroup.
        """

        for name, inputs in self.TEST_INPUTS.items():
            # Turn submission into the form expected when grading this problem.
            submission = self._make_answer_dict(inputs)
            # Lookup the problem_name, and the whether this test problem
            # and inputs should be graded as correct or incorrect.
            problem_name, correctness = self.TEST_SCENARIOS[name]
            # Load the args needed to build the problem for this test.
            problem_args = self.TEST_PROBLEM_ARGS[problem_name]
            test_choices = problem_args["choices"]
            test_script = problem_args["script"]
            # Build the actual problem for the test.
            test_problem = self._make_problem(test_choices, 'radiotextgroup', test_script)
            # Make sure the actual grade matches the expected grade.
            self.assert_grade(
                test_problem,
                submission,
                correctness,
                msg="{0} should be {1}".format(
                    name,
                    correctness
                )
            )

    def test_checkbox_grades(self):
        """
        Test that confirms correct operation of grading when the inputtag is
        checkboxtextgroup.
        """
        # Dictionary from name of test_scenario to (problem_name, correctness)
        # Correctness is used to test whether the problem was graded properly
        scenarios = {
            "2_choices_correct": ("checkbox_two_choices", "correct"),
            "2_choices_incorrect": ("checkbox_two_choices", "incorrect"),

            "2_choices_2_inputs_correct": (
                "checkbox_2_choices_2_inputs",
                "correct"
            ),

            "2_choices_2_inputs_missing_choice": (
                "checkbox_2_choices_2_inputs",
                "incorrect"
            ),

            "2_choices_2_inputs_wrong_input": (
                "checkbox_2_choices_2_inputs",
                "incorrect"
            )
        }
        # Dictionary scenario_name: test_inputs
        inputs = {
            "2_choices_correct": [(True, []), (True, [])],
            "2_choices_incorrect": [(True, []), (False, [])],
            "2_choices_2_inputs_correct": [(True, ["123"]), (True, ["456"])],
            "2_choices_2_inputs_missing_choice": [
                (True, ["123"]), (False, ["456"])
            ],
            "2_choices_2_inputs_wrong_input": [
                (True, ["123"]), (True, ["654"])
            ]
        }

        # Two choice zero input problem with both choices being correct.
        checkbox_two_choices = self._make_problem(
            [("true", {}), ("true", {})], "checkboxtextgroup"
        )
        # Two choice two input problem with both choices correct.
        checkbox_two_choices_two_inputs = self._make_problem(
            [("true", {"answer": "123", "tolerance": "0"}),
             ("true", {"answer": "456", "tolerance": "0"})
             ],
            "checkboxtextgroup"
        )

        # Dictionary problem_name: problem
        problems = {
            "checkbox_two_choices": checkbox_two_choices,
            "checkbox_2_choices_2_inputs": checkbox_two_choices_two_inputs
        }

        for name, inputs in inputs.items():
            submission = self._make_answer_dict(inputs)
            # Load the test problem's name and desired correctness
            problem_name, correctness = scenarios[name]
            # Load the problem
            problem = problems[problem_name]

            # Make sure the actual grade matches the expected grade
            self.assert_grade(
                problem,
                submission,
                correctness,
                msg="{0} should be {1}".format(name, correctness)
            )
