"""
Test capa problem.
"""

import textwrap
import unittest
from unittest.mock import MagicMock, patch

import ddt
import pytest
from lxml import etree
from markupsafe import Markup

from openedx.core.djangolib.markup import HTML
from xmodule.capa.correctmap import CorrectMap
from xmodule.capa.responsetypes import LoncapaProblemError
from xmodule.capa.tests.helpers import new_loncapa_problem
from xmodule.capa.tests.test_util import UseUnsafeCodejail


@ddt.ddt
@UseUnsafeCodejail()
class CAPAProblemTest(unittest.TestCase):
    """CAPA problem related tests"""

    @ddt.unpack
    @ddt.data(
        {"question": "Select the correct synonym of paranoid?"},
        {"question": "Select the correct <em>synonym</em> of <strong>paranoid</strong>?"},
    )
    def test_label_and_description_inside_responsetype(self, question):
        """
        Verify that
        * label is extracted
        * <label> tag is removed to avoid duplication

        This is the case when we have a problem with single question or
        problem with multiple-questions separated as per the new format.
        """
        xml = f"""
        <problem>
            <choiceresponse>
                <label>{question}</label>
                <description>Only the paranoid survive.</description>
                <checkboxgroup>
                    <choice correct="true">over-suspicious</choice>
                    <choice correct="false">sad</choice>
                </checkboxgroup>
            </choiceresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {
            "1_2_1": {"label": question, "descriptions": {"description_1_1_1": "Only the paranoid survive."}}
        }
        assert len(problem.tree.xpath("//label")) == 0

    @ddt.unpack
    @ddt.data(
        {
            "question": "Once we become predictable, we become ______?",
            "label_attr": "Once we become predictable, we become ______?",
        },
        {
            "question": 'Once we become predictable, we become ______?<img src="img/src"/>',
            "label_attr": "Once we become predictable, we become ______?",
        },
    )
    def test_legacy_problem(self, question, label_attr):
        """
        Verify that legacy problem is handled correctly.
        """
        xml = f"""
        <problem>
            <p>Be sure to check your spelling.</p>
            <p>{question}</p>
            <stringresponse answer="vulnerable" type="ci">
                <textline label="{label_attr}" size="40"/>
            </stringresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {"1_2_1": {"label": question, "descriptions": {}}}
        assert len(problem.tree.xpath(f"//*[normalize-space(text())='{question}']")) == 0

    @ddt.unpack
    @ddt.data(
        {
            "question1": "People who say they have nothing to ____ almost always do?",
            "question2": "Select the correct synonym of paranoid?",
        },
        {
            "question1": "<b>People</b> who say they have <mark>nothing</mark> to ____ almost always do?",
            "question2": "Select the <sup>correct</sup> synonym of <mark>paranoid</mark>?",
        },
    )
    def test_neither_label_tag_nor_attribute(self, question1, question2):
        """
        Verify that label is extracted correctly.

        This is the case when we have a markdown problem with multiple-questions.
        In this case when markdown is converted to xml, there will be no label
        tag and label attribute inside responsetype. But we have a label tag
        before the responsetype.
        """
        xml = f"""
        <problem>
            <p>Be sure to check your spelling.</p>
            <label>{question1}</label>
            <stringresponse answer="hide" type="ci">
                <textline size="40"/>
            </stringresponse>
            <choiceresponse>
                <label>{question2}</label>
                <checkboxgroup>
                    <choice correct="true">over-suspicious</choice>
                    <choice correct="false">shy</choice>
                </checkboxgroup>
            </choiceresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {
            "1_2_1": {"label": question1, "descriptions": {}},
            "1_3_1": {"label": question2, "descriptions": {}},
        }
        for question in (question1, question2):
            assert len(problem.tree.xpath(f'//label[text()="{question}"]')) == 0

    def test_multiple_descriptions(self):
        """
        Verify that multiple descriptions are handled correctly.
        """
        desc1 = "The problem with trying to be the <em>bad guy</em>, there's always someone <strong>worse</strong>."
        desc2 = "Anyone who looks the world as if it was a game of chess deserves to lose."
        xml = f"""
        <problem>
            <p>Be sure to check your spelling.</p>
            <stringresponse answer="War" type="ci">
                <label>___ requires sacrifices.</label>
                <description>{desc1}</description>
                <description>{desc2}</description>
                <textline size="40"/>
            </stringresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {
            "1_2_1": {
                "label": "___ requires sacrifices.",
                "descriptions": {"description_1_1_1": desc1, "description_1_1_2": desc2},
            }
        }

    def test_additional_answer_is_skipped_from_resulting_html(self):
        """Tests that additional_answer element is not present in transformed HTML"""
        xml = """
        <problem>
            <p>Be sure to check your spelling.</p>
            <stringresponse answer="War" type="ci">
                <label>___ requires sacrifices.</label>
                <description>Anyone who looks the world as if it was a game of chess deserves to lose.</description>
                <additional_answer answer="optional acceptable variant of the correct answer"/>
                <textline size="40"/>
            </stringresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert len(problem.extracted_tree.xpath("//additional_answer")) == 0
        assert "additional_answer" not in problem.get_html()

    def test_non_accessible_inputtype(self):
        """
        Verify that tag with question text is not removed when inputtype is not fully accessible.
        """
        question = "Click the country which is home to the Pyramids."

        xml = """
        <problem>
            <p>{question}</p>
            <imageresponse>
                <imageinput label="{question}"
                src="/static/Africa.png" width="600" height="638" rectangle="(338,98)-(412,168)"/>
            </imageresponse>
        </problem>
        """.format(
            question=question
        )
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {"1_2_1": {"label": question, "descriptions": {}}}
        # <p> tag with question text should not be deleted
        assert problem.tree.xpath(f"string(p[text()='{question}'])") == question

    def test_label_is_empty_if_no_label_attribute(self):
        """
        Verify that label in response_data is empty string when label
        attribute is missing and responsetype is not fully accessible.
        """
        question = "Click the country which is home to the Pyramids."
        xml = f"""
        <problem>
            <p>{question}</p>
            <imageresponse>
                <imageinput
                src="/static/Africa.png" width="600" height="638" rectangle="(338,98)-(412,168)"/>
            </imageresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {"1_2_1": {"label": "", "descriptions": {}}}

    def test_multiple_questions_problem(self):
        """
        For a problem with multiple questions verify that for each question
        * label is extracted
        * descriptions info is constructed
        * <label> tag is removed to avoid duplication
        """
        xml = """
        <problem>
            <choiceresponse>
                <label>Select the correct synonym of paranoid?</label>
                <description>Only the paranoid survive.</description>
                <checkboxgroup>
                    <choice correct="true">over-suspicious</choice>
                    <choice correct="false">happy</choice>
                </checkboxgroup>
            </choiceresponse>
            <multiplechoiceresponse>
                <p>one more question</p>
                <label>What Apple device competed with the portable CD player?</label>
                <description>Device looks like an egg plant.</description>
                <choicegroup type="MultipleChoice">
                    <choice correct="false">The iPad</choice>
                    <choice correct="false">Napster</choice>
                    <choice correct="true">The iPod</choice>
                    <choice correct="false">The vegetable peeler</choice>
                </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {
            "1_2_1": {
                "label": "Select the correct synonym of paranoid?",
                "descriptions": {"description_1_1_1": "Only the paranoid survive."},
            },
            "1_3_1": {
                "label": "What Apple device competed with the portable CD player?",
                "descriptions": {"description_1_2_1": "Device looks like an egg plant."},
            },
        }
        assert len(problem.tree.xpath("//label")) == 0

    def test_question_title_not_removed_got_children(self):
        """
        Verify that <p> question text before responsetype not deleted when
        it contains other children and label is picked from label attribute of inputtype

        This is the case when author updated the <p> immediately before
        responsetype to contain other elements. We do not want to delete information in that case.
        """
        question = "Is egg plant a fruit?"
        xml = f"""
        <problem>
            <p>Choose wisely.</p>
            <p>Select the correct synonym of paranoid?</p>
            <p><img src="" /></p>
            <choiceresponse>
                <checkboxgroup label="{question}">
                    <choice correct="true">over-suspicious</choice>
                    <choice correct="false">funny</choice>
                </checkboxgroup>
            </choiceresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {"1_2_1": {"label": "", "descriptions": {}}}
        assert len(problem.tree.xpath("//p/img")) == 1

    @ddt.unpack
    @ddt.data(
        {"group_label": "Choose the correct color"},
        {"group_label": "Choose the <b>correct</b> <mark>color</mark>"},
    )
    def test_multiple_inputtypes(self, group_label):
        """
        Verify that group label and labels for individual inputtypes are extracted correctly.
        """
        input1_label = "What color is the sky?"
        input2_label = "What color are pine needles?"
        xml = f"""
        <problem>
            <optionresponse>
                <label>{group_label}</label>
                <optioninput options="('yellow','blue','green')" correct="blue" label="{input1_label}"/>
                <optioninput options="('orange','blue','green')" correct="green" label="{input2_label}"/>
            </optionresponse>
        </problem>
        """

        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {
            "1_2_1": {"group_label": group_label, "label": input1_label, "descriptions": {}},
            "1_2_2": {"group_label": group_label, "label": input2_label, "descriptions": {}},
        }

    def test_single_inputtypes(self):
        """
        Verify that HTML is correctly rendered when there is single inputtype.
        """
        question = "Enter sum of 1+2"
        xml = textwrap.dedent(
            f"""
        <problem>
            <customresponse cfn="test_sum" expect="3">
        <script type="loncapa/python">
        def test_sum(expect, ans):
            return int(expect) == int(ans)
        </script>
                <label>{question}</label>
                <textline size="20" correct_answer="3" />
            </customresponse>
        </problem>
        """
        )
        problem = new_loncapa_problem(xml, use_capa_render_template=True)
        problem_html = etree.XML(problem.get_html())

        # verify that only no multi input group div is present
        multi_inputs_group = problem_html.xpath('//div[@class="multi-inputs-group"]')
        assert len(multi_inputs_group) == 0

        # verify that question is rendered only once
        question = problem_html.xpath(f"//*[normalize-space(text())='{question}']")
        assert len(question) == 1

    def assert_question_tag(self, question1, question2, tag, label_attr=False):
        """
        Verify question tag correctness.
        """
        question1_tag = f"<{tag}>{question1}</{tag}>" if question1 else ""
        question2_tag = f"<{tag}>{question2}</{tag}>" if question2 else ""
        question1_label_attr = f'label="{question1}"' if label_attr else ""
        question2_label_attr = f'label="{question2}"' if label_attr else ""
        xml = f"""
        <problem>
            {question1_tag}
            <choiceresponse>
                <checkboxgroup {question1_label_attr}>
                    <choice correct="true">choice1</choice>
                    <choice correct="false">choice2</choice>
                </checkboxgroup>
            </choiceresponse>
            {question2_tag}
            <multiplechoiceresponse>
                <choicegroup type="MultipleChoice" {question2_label_attr}>
                    <choice correct="false">choice1</choice>
                    <choice correct="true">choice2</choice>
                </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem.problem_data == {
            "1_2_1": {"label": question1, "descriptions": {}},
            "1_3_1": {"label": question2, "descriptions": {}},
        }
        assert len(problem.tree.xpath(f"//{tag}")) == 0

    @ddt.unpack
    @ddt.data(
        {"question1": "question 1 label", "question2": "question 2 label"},
        {"question1": "", "question2": "question 2 label"},
        {"question1": "question 1 label", "question2": ""},
    )
    def test_correct_question_tag_is_picked(self, question1, question2):
        """
        For a problem with multiple questions verify that correct question tag is picked.
        """
        self.assert_question_tag(question1, question2, tag="label", label_attr=False)
        self.assert_question_tag(question1, question2, tag="p", label_attr=True)

    def test_optionresponse_xml_compatibility(self):
        """
        Verify that an optionresponse problem with multiple correct answers is not instantiated.

        Scenario:
        Given an optionresponse/Dropdown problem
        If there are multiple correct answers
        Then the problem is not instantiated
        And Loncapa problem error exception is raised
        If the problem is corrected by including only one correct answer
        Then the problem is created successfully
        """
        xml = """
        <problem>
            <optionresponse>
              <p>You can use this template as a guide to the simple editor markdown and
                OLX markup to use for dropdown problems. Edit this component to replace
                  this template with your own assessment.</p>
            <label>Add the question text, or prompt, here. This text is required.</label>
            <description>You can add an optional tip or note related to the prompt like this. </description>
            <optioninput>
                <option correct="False">an incorrect answer</option>
                <option correct="True">the correct answer</option>
                <option correct="{correctness}">an incorrect answer</option>
              </optioninput>
            </optionresponse>
        </problem>
        """
        with pytest.raises(LoncapaProblemError):
            new_loncapa_problem(xml.format(correctness=True))
        problem = new_loncapa_problem(xml.format(correctness=False))
        assert problem is not None

    def test_optionresponse_option_with_empty_text(self):
        """
        Verify successful instantiation of an optionresponse problem
        with an option with empty text
        """
        xml = """
        <problem>
            <optionresponse>
                <label>Select True or False</label>
                <optioninput>
                    <option correct="False">True <optionhint>Not this one</optionhint></option>
                    <option correct="True">False</option>
                    <option correct="False"><optionhint>Not this empty one either</optionhint></option>
                </optioninput>
            </optionresponse>
        </problem>
        """
        problem = new_loncapa_problem(xml)
        assert problem is not None


@ddt.ddt
@UseUnsafeCodejail()
class CAPAMultiInputProblemTest(unittest.TestCase):
    """TestCase for CAPA problems with multiple inputtypes"""

    def capa_problem(self, xml):
        """
        Create capa problem.
        """
        return new_loncapa_problem(xml, use_capa_render_template=True)

    def assert_problem_data(self, problem_data):
        """Verify problem data is in expected state"""
        for problem_value in problem_data.values():
            assert isinstance(problem_value["label"], Markup)

    def assert_problem_html(self, problem_html, group_label, *input_labels):
        """
        Verify that correct html is rendered for multiple inputtypes.

        Arguments:
            problem_html (str): problem HTML
            group_label (str or None): multi input group label or None if label is not present
            input_labels (tuple): individual input labels
        """
        html = etree.XML(problem_html)

        # verify that only one multi input group div is present at correct path
        multi_inputs_group = html.xpath('//div[@class="wrapper-problem-response"]/div[@class="multi-inputs-group"]')
        assert len(multi_inputs_group) == 1

        if group_label is None:
            # if multi inputs group label is not present then there shouldn't be `aria-labelledby` attribute
            assert multi_inputs_group[0].attrib.get("aria-labelledby") is None
        else:
            # verify that multi input group label <p> tag exists and its
            # id matches with correct multi input group aria-labelledby
            multi_inputs_group_label_id = multi_inputs_group[0].attrib.get("aria-labelledby")
            multi_inputs_group_label = html.xpath(f'//p[@id="{multi_inputs_group_label_id}"]')
            assert len(multi_inputs_group_label) == 1
            assert multi_inputs_group_label[0].text == group_label

        # verify that label for each input comes only once
        for input_label in input_labels:
            # normalize-space is used to remove whitespace around the text
            input_label_element = multi_inputs_group[0].xpath(f'//*[normalize-space(text())="{input_label}"]')
            assert len(input_label_element) == 1

    @ddt.unpack
    @ddt.data(
        {"label_html": "<label>Choose the correct color</label>", "group_label": "Choose the correct color"},
        {"label_html": "", "group_label": None},
    )
    def test_optionresponse(self, label_html, group_label):
        """
        Verify that optionresponse problem with multiple inputtypes is rendered correctly.
        """
        input1_label = "What color is the sky?"
        input2_label = "What color are pine needles?"
        xml = f"""
        <problem>
            <optionresponse>
                {label_html}
                <optioninput options="('yellow','blue','green')" correct="blue" label="{input1_label}"/>
                <optioninput options="('yellow','blue','green')" correct="green" label="{input2_label}"/>
            </optionresponse>
        </problem>
        """
        problem = self.capa_problem(xml)
        self.assert_problem_html(problem.get_html(), group_label, input1_label, input2_label)
        self.assert_problem_data(problem.problem_data)

    @ddt.unpack
    @ddt.data({"inputtype": "textline"}, {"inputtype": "formulaequationinput"})
    def test_customresponse(self, inputtype):
        """
        Verify that customresponse problem with multiple textline
        and formulaequationinput inputtypes is rendered correctly.
        """
        group_label = "Enter two integers that sum to 10."
        input1_label = "Integer 1"
        input2_label = "Integer 2"
        xml = textwrap.dedent(
            f"""
        <problem>
            <customresponse cfn="test_add_to_ten">
        <script type="loncapa/python">
        def test_add_to_ten(expect, ans):
            return test_add(10, ans)
        </script>
                <label>{group_label}</label>
                <{inputtype} size="40" correct_answer="3" label="{input1_label}" /><br/>
                <{inputtype} size="40" correct_answer="7" label="{input2_label}" />
            </customresponse>
        </problem>
        """
        )
        problem = self.capa_problem(xml)
        self.assert_problem_html(problem.get_html(), group_label, input1_label, input2_label)
        self.assert_problem_data(problem.problem_data)

    @ddt.unpack
    @ddt.data(
        {
            "descriptions": ("desc1", "desc2"),
            "descriptions_html": "<description>desc1</description><description>desc2</description>",
        },
        {"descriptions": (), "descriptions_html": ""},
    )
    def test_descriptions(self, descriptions, descriptions_html):
        """
        Verify that groups descriptions are rendered correctly.
        """
        xml = f"""
        <problem>
            <optionresponse>
                <label>group label</label>
                {descriptions_html}
                <optioninput options="('yellow','blue','green')" correct="blue" label="first label"/>
                <optioninput options="('yellow','blue','green')" correct="green" label="second label"/>
            </optionresponse>
        </problem>
        """
        problem = self.capa_problem(xml)
        problem_html = etree.XML(problem.get_html())

        multi_inputs_group = problem_html.xpath('//div[@class="multi-inputs-group"]')[0]
        description_ids = multi_inputs_group.attrib.get("aria-describedby", "").split()

        # Verify that number of descriptions matches description_ids
        assert len(description_ids) == len(descriptions)

        # For each description, check its order and text is correct
        for index, description_id in enumerate(description_ids):
            description_element = multi_inputs_group.xpath(f'//p[@id="{description_id}"]')
            assert len(description_element) == 1
            assert description_element[0].text == descriptions[index]


@ddt.ddt
class CAPAProblemReportHelpersTest(unittest.TestCase):
    """TestCase for CAPA methods for finding question labels and answer text"""

    @ddt.data(
        ("answerid_2_1", "label", "label"),
        ("answerid_2_2", "label <some>html</some>", "label html"),
        ("answerid_2_2", '<more html="yes"/>label <some>html</some>', "label html"),
        ("answerid_2_3", None, "Question 1"),
        ("answerid_2_3", "", "Question 1"),
        ("answerid_3_3", "", "Question 2"),
    )
    @ddt.unpack
    def test_find_question_label(self, answer_id, label, stripped_label):
        """Verify that find_question_label returns the correctly stripped question label."""
        problem = new_loncapa_problem(f'<problem><some-problem id="{answer_id}"/></problem>')
        mock_problem_data = {answer_id: {"label": HTML(label) if label else ""}}
        with patch.object(problem, "problem_data", mock_problem_data):
            assert problem.find_question_label(answer_id) == stripped_label

    @ddt.data(None, {}, [None])
    def test_find_answer_test_not_implemented(self, current_answer):
        """Ensure find_answer_text raises NotImplementedError for unsupported responses."""
        problem = new_loncapa_problem("<problem/>")
        self.assertRaises(NotImplementedError, problem.find_answer_text, "", current_answer)

    @ddt.data(
        ("1_2_1", "choice_0", "over-suspicious"),
        ("1_2_1", "choice_1", "funny"),
        ("1_3_1", "choice_0", "The iPad"),
        ("1_3_1", "choice_2", "The iPod"),
        ("1_3_1", ["choice_0", "choice_1"], "The iPad, Napster"),
        ("1_4_1", "yellow", "yellow"),
        ("1_4_1", "blue", "blue"),
    )
    @ddt.unpack
    def test_find_answer_text_choices(self, answer_id, choice_id, answer_text):
        """Verify find_answer_text returns correct answer text for choice, multiple-choice, and option responses."""
        problem = new_loncapa_problem(
            """
            <problem>
                <choiceresponse>
                    <checkboxgroup label="Select the correct synonym of paranoid?">
                        <choice correct="true">over-suspicious</choice>
                        <choice correct="false">funny</choice>
                    </checkboxgroup>
                </choiceresponse>
                <multiplechoiceresponse>
                    <choicegroup type="MultipleChoice">
                        <choice correct="false">The iPad</choice>
                        <choice correct="false">Napster</choice>
                        <choice correct="true">The iPod</choice>
                    </choicegroup>
                </multiplechoiceresponse>
                <optionresponse>
                    <optioninput options="('yellow','blue','green')" correct="blue" label="Color_1"/>
                </optionresponse>
            </problem>
            """
        )
        assert problem.find_answer_text(answer_id, choice_id) == answer_text

    @ddt.data(
        # Test for ChoiceResponse
        ("1_2_1", "choice_0", "Answer Text Missing"),
        ("1_2_1", "choice_1", "funny"),
        # Test for MultipleChoiceResponse
        ("1_3_1", "choice_0", "The iPad"),
        ("1_3_1", "choice_2", "Answer Text Missing"),
        ("1_3_1", ["choice_0", "choice_1"], "The iPad, Answer Text Missing"),
        # Test for OptionResponse
        ("1_4_1", "", "Answer Text Missing"),
    )
    @ddt.unpack
    def test_find_answer_text_choices_with_missing_text(self, answer_id, choice_id, answer_text):
        """Ensure find_answer_text handles missing answer text correctly and returns 'Answer Text Missing'."""
        problem = new_loncapa_problem(
            """
            <problem>
                <choiceresponse>
                    <checkboxgroup label="Select the correct synonym of paranoid?">
                        <choice correct="true"></choice>
                        <choice correct="false">funny</choice>
                    </checkboxgroup>
                </choiceresponse>
                <multiplechoiceresponse>
                    <choicegroup type="MultipleChoice">
                        <choice correct="false">The iPad</choice>
                        <choice correct="false"></choice>
                        <choice correct="true"></choice>
                    </choicegroup>
                </multiplechoiceresponse>
                <optionresponse>
                    <optioninput options="('yellow','blue','green')" correct="blue" label="Color_1"/>
                </optionresponse>
            </problem>
            """
        )
        assert problem.find_answer_text(answer_id, choice_id) == answer_text

    @ddt.data(
        # Test for ChoiceResponse
        ("1_2_1", "over-suspicious"),
        # Test for MultipleChoiceResponse
        ("1_3_1", "The iPad, Napster"),
        # Test for OptionResponse
        ("1_4_1", "blue"),
    )
    @ddt.unpack
    def test_find_correct_answer_text_choices(self, answer_id, answer_text):
        """
        Verify that ``find_correct_answer_text`` can find the correct answer for
        ChoiceResponse, MultipleChoiceResponse and OptionResponse problems.
        """
        problem = new_loncapa_problem(
            """
            <problem>
                <choiceresponse>
                    <checkboxgroup label="Select the correct synonym of paranoid?">
                        <choice correct="true">over-suspicious</choice>
                        <choice correct="false">funny</choice>
                    </checkboxgroup>
                </choiceresponse>
                <multiplechoiceresponse>
                    <choicegroup type="MultipleChoice">
                        <choice correct="true">The iPad</choice>
                        <choice correct="true">Napster</choice>
                        <choice correct="false">The iPod</choice>
                    </choicegroup>
                </multiplechoiceresponse>
                <optionresponse>
                    <optioninput options="('yellow','blue','green')" correct="blue" label="Color_1"/>
                </optionresponse>
            </problem>
            """
        )
        assert problem.find_correct_answer_text(answer_id) == answer_text

    def test_find_answer_text_textinput(self):
        """Check that find_answer_text correctly returns the answer for stringresponse/textinput problems."""
        problem = new_loncapa_problem(
            """
            <problem>
                <stringresponse answer="hide" type="ci">
                    <textline size="40"/>
                </stringresponse>
            </problem>
            """
        )
        assert problem.find_answer_text("1_2_1", "hide") == "hide"

    def test_get_question_answer(self):
        """Ensure get_question_answers returns answer text as strings suitable for JSON serialization."""
        problem = new_loncapa_problem(
            """
            <problem>
                <optionresponse>
                    <optioninput options="('yellow','blue','green')" correct="blue" label="Color_1"/>
                </optionresponse>
                <solution>
                    <div class="detailed-solution">
                        <p>Explanation</p>
                        <p>Blue is the answer.</p>
                    </div>
                </solution>
            </problem>
            """
        )

        # Ensure that the answer is a string so that the dict returned from this
        # function can eventualy be serialized to json without issues.
        assert isinstance(problem.get_question_answers()["1_solution_1"], str)

    def test_get_grade_from_current_answers(self):
        """
        Verify that `responder.evaluate_answers` is called with `student_answers`
        and `correct_map` sent to `get_grade_from_current_answers`.

        When both arguments are provided, means that the problem is being rescored.
        """
        student_answers = {"1_2_1": "over-suspicious"}
        correct_map = CorrectMap(answer_id="1_2_1", correctness="correct", npoints=1)
        problem = new_loncapa_problem(
            """
            <problem>
                <multiplechoiceresponse>
                    <choicegroup>
                        <choice correct="true">Answer1</choice>
                        <choice correct="false">Answer2</choice>
                        <choice correct="false">Answer3</choice>
                        <choice correct="false">Answer4</choice>
                    </choicegroup>
                </multiplechoiceresponse>
            </problem>
            """
        )
        responder_mock = MagicMock()

        with patch.object(problem, "responders", {"responder1": responder_mock}):
            responder_mock.allowed_inputfields = ["choicegroup"]
            responder_mock.evaluate_answers.return_value = correct_map

            result = problem.get_grade_from_current_answers(student_answers, correct_map)
            self.assertDictEqual(result.get_dict(), correct_map.get_dict())
            responder_mock.evaluate_answers.assert_called_once_with(student_answers, correct_map)

    def test_get_grade_from_current_answers_without_student_answers(self):
        """
        Verify that `responder.evaluate_answers` is called with appropriate arguments.

        When `student_answers` is None, `responder.evaluate_answers` should be called with
        the `self.student_answers` instead.
        """
        correct_map = CorrectMap(answer_id="1_2_1", correctness="correct", npoints=1)
        problem = new_loncapa_problem(
            """
            <problem>
                <multiplechoiceresponse>
                    <choicegroup>
                        <choice correct="true">Answer1</choice>
                        <choice correct="false">Answer2</choice>
                        <choice correct="false">Answer3</choice>
                        <choice correct="false">Answer4</choice>
                    </choicegroup>
                </multiplechoiceresponse>
            </problem>
            """
        )
        responder_mock = MagicMock()

        with patch.object(problem, "responders", {"responder1": responder_mock}):
            problem.responders["responder1"].allowed_inputfields = ["choicegroup"]
            problem.responders["responder1"].evaluate_answers.return_value = correct_map

            result = problem.get_grade_from_current_answers(None, correct_map)

            self.assertDictEqual(result.get_dict(), correct_map.get_dict())
            responder_mock.evaluate_answers.assert_called_once_with(None, correct_map)

    def test_get_grade_from_current_answers_with_filesubmission(self):
        """
        Verify that an exception is raised when `responder.evaluate_answers` is called
        with `student_answers` as None and `correct_map` sent to `get_grade_from_current_answers`

        This ensures that rescore is not allowed if the problem has a filesubmission.
        """
        correct_map = CorrectMap(answer_id="1_2_1", correctness="correct", npoints=1)
        problem = new_loncapa_problem(
            """
            <problem>
                <multiplechoiceresponse>
                    <choicegroup>
                        <choice correct="true">Answer1</choice>
                        <choice correct="false">Answer2</choice>
                        <choice correct="false">Answer3</choice>
                        <choice correct="false">Answer4</choice>
                    </choicegroup>
                </multiplechoiceresponse>
            </problem>
            """
        )
        responder_mock = MagicMock()

        with patch.object(problem, "responders", {"responder1": responder_mock}):
            responder_mock.allowed_inputfields = ["filesubmission"]
            responder_mock.evaluate_answers.return_value = correct_map

            with self.assertRaises(Exception):
                problem.get_grade_from_current_answers(None, correct_map)
            responder_mock.evaluate_answers.assert_not_called()
