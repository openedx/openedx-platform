"""
Tests that policy json files import correctly when loading XML
"""


import pytest

from xmodule.tests.xml import XModuleXmlImportTest
from xmodule.tests.xml.factories import CourseFactory


class TestPolicy(XModuleXmlImportTest):
    """
    Tests that policy json files import correctly when loading xml
    """

    def test_no_attribute_mapping(self):
        # Policy files are json, and thus the values aren't passed through 'deserialize_field'
        # Therefor, the string 'null' is passed unchanged to the Float field, which will trigger
        # a ValueError
        with pytest.raises(ValueError):  # noqa: PT011, PT012
            course = self.process_xml(CourseFactory.build(policy={'days_early_for_beta': 'null'}))

            # Trigger the exception by looking at the imported data
            course.days_early_for_beta  # pylint: disable=pointless-statement  # noqa: B018

    def test_course_policy(self):
        course = self.process_xml(CourseFactory.build(policy={'days_early_for_beta': None}))
        assert course.days_early_for_beta is None

        course = self.process_xml(CourseFactory.build(policy={'days_early_for_beta': 9}))
        assert course.days_early_for_beta == 9
