"""
Test for LMS courseware app.
"""


from textwrap import dedent
from unittest import TestCase, mock

from django.urls import reverse
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from xmodule.error_block import ErrorBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import ToyCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class ActivateLoginTest(LoginEnrollmentTestCase):
    """
    Test logging in and logging out.
    """
    def setUp(self):
        super().setUp()
        self.setup_user()

    def test_activate_login(self):
        """
        Test login -- the setup function does all the work.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def test_logout(self):
        """
        Test logout -- setup function does login.
        """
        self.logout()


class PageLoaderTestCase(LoginEnrollmentTestCase):
    """
    Base class that adds a function to load all pages in a modulestore.
    """

    def check_all_pages_load(self, course_key):
        """
        Assert that all pages in the course load correctly.
        `course_id` is the ID of the course to check.
        """

        store = modulestore()

        # Enroll in the course before trying to access pages
        course = store.get_course(course_key)
        self.enroll(course, True)

        # Search for items in the course
        items = store.get_items(course_key)

        if len(items) < 1:
            self.fail('Could not retrieve any items from course')

        # Try to load each item in the course
        for block in items:

            if block.location.category == 'about':
                self._assert_loads('about_course',
                                   {'course_id': str(course_key)},
                                   block)

            elif block.location.category == 'static_tab':
                kwargs = {'course_id': str(course_key),
                          'tab_slug': block.location.name}
                self._assert_loads('static_tab', kwargs, block)

            elif block.location.category == 'course_info':
                self._assert_loads('info', {'course_id': str(course_key)},
                                   block)

            else:

                kwargs = {'course_id': str(course_key),
                          'location': str(block.location)}

                self._assert_loads('jump_to', kwargs, block,
                                   expect_redirect=True,
                                   check_content=True)

    def _assert_loads(self, django_url, kwargs, block,
                      expect_redirect=False,
                      check_content=False):
        """
        Assert that the url loads correctly.
        If expect_redirect, then also check that we were redirected.
        If check_content, then check that we don't get
        an error message about unavailable blocks.
        """

        url = reverse(django_url, kwargs=kwargs)
        response = self.client.get(url, follow=True)

        if response.status_code != 200:
            self.fail('Status %d for page %s' %
                      (response.status_code, block.location))

        if expect_redirect:
            assert response.redirect_chain[0][1] == 302

        if check_content:
            self.assertNotContains(response, "this module is temporarily unavailable")
            assert not isinstance(block, ErrorBlock)


class TestMongoCoursesLoad(ModuleStoreTestCase, PageLoaderTestCase):
    """
    Check that all pages in test courses load properly from Mongo.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.setup_user()
        self.toy_course_key = ToyCourseFactory.create().id

    @mock.patch('xmodule.course_block.requests.get')
    def test_toy_textbooks_loads(self, mock_get):
        mock_get.return_value.text = dedent("""
            <?xml version="1.0"?><table_of_contents>
            <entry page="5" page_label="ii" name="Table of Contents"/>
            </table_of_contents>
        """).strip()
        location = self.toy_course_key.make_usage_key('course', 'course')
        course = self.store.get_item(location)
        assert len(course.textbooks) > 0


class TestDraftModuleStore(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def test_get_items_with_course_items(self):
        store = modulestore()

        # fix was to allow get_items() to take the course_id parameter
        store.get_items(CourseKey.from_string('abc/def/ghi'), qualifiers={'category': 'vertical'})

        # test success is just getting through the above statement.
        # The bug was that 'course_id' argument was
        # not allowed to be passed in (i.e. was throwing exception)


class TestLmsFieldData(TestCase):
    """
    Tests of the LmsFieldData class
    """
    def test_lms_field_data_wont_nest(self):
        # Verify that if an LmsFieldData is passed into LmsFieldData as the
        # authored_data, that it doesn't produced a nested field data.
        #
        # This fixes a bug where re-use of the same block for many modules
        # would cause more and more nesting, until the recursion depth would be
        # reached on any attribute access

        # pylint: disable=protected-access
        base_authored = mock.Mock()
        base_student = mock.Mock()
        first_level = LmsFieldData(base_authored, base_student)
        second_level = LmsFieldData(first_level, base_student)
        assert second_level._authored_data == first_level._authored_data
        assert not isinstance(second_level._authored_data, LmsFieldData)
