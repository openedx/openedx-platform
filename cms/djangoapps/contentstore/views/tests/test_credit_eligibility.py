"""
Unit tests for credit eligibility UI in Studio.
"""


from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class CreditEligibilityTest(CourseTestCase):
    """
    Base class to test the course settings details view in Studio for credit
    eligibility requirements.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org='edX', number='dummy', display_name='Credit Course')
        self.course_details_url = reverse_course_url('settings_handler', str(self.course.id))
