"""
Tests for wiki permissions
"""


from django.contrib.auth.models import Group
from wiki.models import URLPath

from common.djangoapps.student.tests.factories import InstructorFactory, StaffFactory, UserFactory
from lms.djangoapps.course_wiki import settings
from lms.djangoapps.course_wiki.utils import course_wiki_slug, user_is_article_course_staff
from lms.djangoapps.course_wiki.views import get_or_create_root
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class TestWikiAccessBase(ModuleStoreTestCase):
    """Base class for testing wiki access."""
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()

        self.wiki = get_or_create_root()

        self.course_math101 = CourseFactory.create(org='org', number='math101', display_name='Course')  # lint-amnesty, pylint: disable=line-too-long
        self.course_math101_staff = self.create_staff_for_course(self.course_math101)

        wiki_math101 = self.create_urlpath(self.wiki, course_wiki_slug(self.course_math101))
        wiki_math101_page = self.create_urlpath(wiki_math101, 'Child')
        wiki_math101_page_page = self.create_urlpath(wiki_math101_page, 'Grandchild')
        self.wiki_math101_pages = [wiki_math101, wiki_math101_page, wiki_math101_page_page]

        self.course_math101b = CourseFactory.create(org='org', number='math101b', display_name='Course')  # lint-amnesty, pylint: disable=line-too-long
        self.course_math101b_staff = self.create_staff_for_course(self.course_math101b)

        wiki_math101b = self.create_urlpath(self.wiki, course_wiki_slug(self.course_math101b))
        wiki_math101b_page = self.create_urlpath(wiki_math101b, 'Child')
        wiki_math101b_page_page = self.create_urlpath(wiki_math101b_page, 'Grandchild')
        self.wiki_math101b_pages = [wiki_math101b, wiki_math101b_page, wiki_math101b_page_page]

    def create_urlpath(self, parent, slug):
        """Creates an article at /parent/slug and returns its URLPath"""
        return URLPath.create_article(parent, slug, title=slug)

    def create_staff_for_course(self, course):
        """Creates and returns users with instructor and staff access to course."""

        return [
            InstructorFactory(course_key=course.id),  # Creates instructor_org/number/run role name
            StaffFactory(course_key=course.id),  # Creates staff_org/number/run role name
        ]


class TestWikiAccess(TestWikiAccessBase):
    """Test wiki access for course staff."""
    def setUp(self):
        super().setUp()

        self.course_310b = CourseFactory.create(org='org', number='310b', display_name='Course')
        self.course_310b_staff = self.create_staff_for_course(self.course_310b)
        self.course_310b2 = CourseFactory.create(org='org', number='310b_', display_name='Course')
        self.course_310b2_staff = self.create_staff_for_course(self.course_310b2)

        self.wiki_310b = self.create_urlpath(self.wiki, course_wiki_slug(self.course_310b))
        self.wiki_310b2 = self.create_urlpath(self.wiki, course_wiki_slug(self.course_310b2))

    def test_no_one_is_root_wiki_staff(self):
        all_course_staff = self.course_math101_staff + self.course_310b_staff + self.course_310b2_staff
        for course_staff in all_course_staff:
            assert not user_is_article_course_staff(course_staff, self.wiki.article)

    def test_course_staff_is_course_wiki_staff(self):
        for page in self.wiki_math101_pages:
            for course_staff in self.course_math101_staff:
                assert user_is_article_course_staff(course_staff, page.article)

        for page in self.wiki_math101b_pages:
            for course_staff in self.course_math101b_staff:
                assert user_is_article_course_staff(course_staff, page.article)

    def test_settings(self):
        for page in self.wiki_math101_pages:
            for course_staff in self.course_math101_staff:
                assert settings.CAN_DELETE(page.article, course_staff)
                assert settings.CAN_MODERATE(page.article, course_staff)
                assert settings.CAN_CHANGE_PERMISSIONS(page.article, course_staff)
                assert settings.CAN_ASSIGN(page.article, course_staff)
                assert settings.CAN_ASSIGN_OWNER(page.article, course_staff)

        for page in self.wiki_math101b_pages:
            for course_staff in self.course_math101b_staff:
                assert settings.CAN_DELETE(page.article, course_staff)
                assert settings.CAN_MODERATE(page.article, course_staff)
                assert settings.CAN_CHANGE_PERMISSIONS(page.article, course_staff)
                assert settings.CAN_ASSIGN(page.article, course_staff)
                assert settings.CAN_ASSIGN_OWNER(page.article, course_staff)

    def test_other_course_staff_is_not_course_wiki_staff(self):
        for page in self.wiki_math101_pages:
            for course_staff in self.course_math101b_staff:
                assert not user_is_article_course_staff(course_staff, page.article)

        for page in self.wiki_math101_pages:
            for course_staff in self.course_310b_staff:
                assert not user_is_article_course_staff(course_staff, page.article)

        for course_staff in self.course_310b_staff:
            assert not user_is_article_course_staff(course_staff, self.wiki_310b2.article)

        for course_staff in self.course_310b2_staff:
            assert not user_is_article_course_staff(course_staff, self.wiki_310b.article)


class TestWikiAccessForStudent(TestWikiAccessBase):
    """Test access for students."""
    def setUp(self):
        super().setUp()

        self.student = UserFactory.create()

    def test_student_is_not_root_wiki_staff(self):
        assert not user_is_article_course_staff(self.student, self.wiki.article)

    def test_student_is_not_course_wiki_staff(self):
        for page in self.wiki_math101_pages:
            assert not user_is_article_course_staff(self.student, page.article)


class TestWikiAccessForNumericalCourseNumber(TestWikiAccessBase):
    """Test staff has access if course number is numerical and wiki slug has an underscore appended."""
    def setUp(self):
        super().setUp()

        self.course_200 = CourseFactory.create(org='org', number='200', display_name='Course')
        self.course_200_staff = self.create_staff_for_course(self.course_200)

        wiki_200 = self.create_urlpath(self.wiki, course_wiki_slug(self.course_200))
        wiki_200_page = self.create_urlpath(wiki_200, 'Child')
        wiki_200_page_page = self.create_urlpath(wiki_200_page, 'Grandchild')
        self.wiki_200_pages = [wiki_200, wiki_200_page, wiki_200_page_page]

    def test_course_staff_is_course_wiki_staff_for_numerical_course_number(self):
        for page in self.wiki_200_pages:
            for course_staff in self.course_200_staff:
                assert user_is_article_course_staff(course_staff, page.article)


class TestWikiAccessForOldFormatCourseStaffGroups(TestWikiAccessBase):
    """Test staff has access if course group has old format."""
    def setUp(self):
        super().setUp()

        self.course_math101c = CourseFactory.create(org='org', number='math101c', display_name='Course')
        Group.objects.get_or_create(name='instructor_math101c')
        self.course_math101c_staff = self.create_staff_for_course(self.course_math101c)

        wiki_math101c = self.create_urlpath(self.wiki, course_wiki_slug(self.course_math101c))
        wiki_math101c_page = self.create_urlpath(wiki_math101c, 'Child')
        wiki_math101c_page_page = self.create_urlpath(wiki_math101c_page, 'Grandchild')
        self.wiki_math101c_pages = [wiki_math101c, wiki_math101c_page, wiki_math101c_page_page]

    def test_course_staff_is_course_wiki_staff(self):
        for page in self.wiki_math101c_pages:
            for course_staff in self.course_math101c_staff:
                assert user_is_article_course_staff(course_staff, page.article)
