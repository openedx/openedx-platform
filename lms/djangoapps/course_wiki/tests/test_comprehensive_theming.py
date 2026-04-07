"""
Tests for wiki middleware.
"""


from unittest import skip

from django.test.client import Client
from wiki.models import URLPath

from common.djangoapps.student.tests.factories import InstructorFactory
from lms.djangoapps.course_wiki.views import get_or_create_root
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestComprehensiveTheming(ModuleStoreTestCase):
    """Tests for comprehensive theming of wiki pages."""

    def setUp(self):
        """Test setup."""
        super().setUp()

        self.wiki = get_or_create_root()

        self.course_math101 = CourseFactory.create(org='edx', number='math101', display_name='2014',
                                                   metadata={'use_unique_wiki_id': 'false'})
        self.course_math101_instructor = InstructorFactory(course_key=self.course_math101.id, username='instructor',
                                                           password='secret')
        self.wiki_math101 = URLPath.create_article(self.wiki, 'math101', title='math101')

        self.client = Client()
        self.client.login(username='instructor', password='secret')

    @skip("Fails when run immediately after lms.djangoapps.course_wiki.tests.test_middleware")
    @with_comprehensive_theme('red-theme')
    def test_themed_footer(self):
        """
        Tests that theme footer is used rather than standard
        footer when comprehensive theme is enabled.
        """
        response = self.client.get('/courses/edx/math101/2014/wiki/math101/')
        assert response.status_code == 200
        # This string comes from themes/red-theme/lms/templates/footer.html
        self.assertContains(response, "super-ugly")
