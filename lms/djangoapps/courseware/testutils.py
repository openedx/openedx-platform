"""
Common test utilities for courseware functionality
"""


from abc import ABCMeta, abstractmethod
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlencode

import ddt

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import AdminFactory, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.utils import is_mode_upsellable
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
    check_mongo_calls,
)

from .field_overrides import OverrideModulestoreFieldData
from .tests.helpers import MasqueradeMixin


@ddt.ddt
class RenderXBlockTestMixin(MasqueradeMixin, metaclass=ABCMeta):
    """
    Mixin for testing the courseware.render_xblock function.
    It can be used for testing any higher-level endpoint that calls this method.
    """

    # DOM elements that appear in the LMS Courseware,
    # but are excluded from the xBlock-only rendering.
    COURSEWARE_CHROME_HTML_ELEMENTS = [
        '<ol class="tabs course-tabs"',
        '<footer id="footer-openedx"',
        '<div class="window-wrap"',
        '<div class="preview-menu"',
        '<div class="container"',
    ]

    # DOM elements that should only be present when viewing the XBlock as staff.
    XBLOCK_STAFF_DEBUG_INFO = [
        '<div class="wrap-instructor-info"',
    ]

    # DOM elements that appear in the LMS Courseware, but are excluded from the
    # xBlock-only rendering, and are specific to a particular block.
    BLOCK_SPECIFIC_CHROME_HTML_ELEMENTS = {
        # Although bookmarks were removed from all chromeless views of the
        # vertical, it is LTI specifically that must never include them.
        'vertical_block': ['<div class="bookmark-button-wrapper"'],
        'html_block': [],
    }

    def setUp(self):
        """
        Clear out the block to be requested/tested before each test.
        """
        super().setUp()
        # to adjust the block to be tested, update block_name_to_be_tested before calling setup_course.
        self.block_name_to_be_tested = 'html_block'

    @abstractmethod
    def get_response(self, usage_key, url_encoded_params=None):
        """
        Abstract method to get the response from the endpoint that is being tested.

        Arguments:
            usage_key: The course block usage key. This ensures that the positive and negative tests stay in sync.
            url_encoded_params: URL encoded parameters that should be appended to the requested URL.
        """
        pass  # pragma: no cover  # pylint: disable=unnecessary-pass

    def login(self):
        """
        Logs in the test user.
        """
        self.client.login(username=self.user.username, password='Password1234')

    def course_options(self):
        """
        Options to configure the test course. Intended to be overridden by
        subclasses.
        """
        return {
            'start': datetime.now() - timedelta(days=1)
        }

    def setup_course(self, default_store=None):
        """
        Helper method to create the course.
        """
        if not default_store:
            default_store = self.store.default_modulestore.get_modulestore_type()
        with self.store.default_store(default_store):
            self.course = CourseFactory.create(**self.course_options())
            chapter = BlockFactory.create(parent=self.course, category='chapter')
            self.vertical_block = BlockFactory.create(
                parent_location=chapter.location,
                category='vertical',
                display_name="Vertical"
            )
            self.html_block = BlockFactory.create(
                parent=self.vertical_block,
                category='html',
                data="<p>Test HTML Content<p>"
            )
            self.problem_block = BlockFactory.create(
                parent=self.vertical_block,
                category='problem',
                display_name='Problem'
            )
            self.video_block = BlockFactory.create(
                parent=self.vertical_block,
                category='video',
                display_name='Video'
            )
        CourseOverview.load_from_module_store(self.course.id)

        # block_name_to_be_tested can be `html_block` or `vertical_block`.
        # These attributes help ensure the positive and negative tests are in sync.
        self.block_to_be_tested = getattr(self, self.block_name_to_be_tested)
        self.block_specific_chrome_html_elements = self.BLOCK_SPECIFIC_CHROME_HTML_ELEMENTS[
            self.block_name_to_be_tested
        ]

    def setup_user(self, admin=False, enroll=False, login=False):
        """
        Helper method to create the user.
        """
        self.user = AdminFactory() if admin else UserFactory()

        if enroll:
            CourseEnrollmentFactory(user=self.user, course_id=self.course.id)

        if login:
            self.login()

    def verify_response(self, expected_response_code=200, url_params=None, is_staff=False):
        """
        Helper method that calls the endpoint, verifies the expected response code, and returns the response.

        Arguments:
            expected_response_code: The expected response code.
            url_params: URL parameters that will be encoded and passed to the request.
            is_staff: Whether the user has staff permissions in the course.
        """
        if url_params:
            url_params = urlencode(url_params)

        response = self.get_response(self.block_to_be_tested.location, url_params)
        if expected_response_code == 200:
            self.assertContains(response, self.html_block.data, status_code=expected_response_code)
            unexpected_elements = self.block_specific_chrome_html_elements + self.COURSEWARE_CHROME_HTML_ELEMENTS
            if not is_staff:
                unexpected_elements += self.XBLOCK_STAFF_DEBUG_INFO
            for chrome_element in unexpected_elements:
                self.assertNotContains(response, chrome_element)
        else:
            self.assertNotContains(response, self.html_block.data, status_code=expected_response_code)
        return response

    def test_success_enrolled_staff(self):
        self.setup_course()
        self.setup_user(admin=True, enroll=True, login=True)

        # The 5 mongoDB calls include calls for
        #   (1) structure - get_course_with_access
        #   (2) definition - get_course_with_access
        #   (3) definition - HTML block
        #   (4) definition - edx_notes decorator (original_get_html)
        with check_mongo_calls(4):
            self.verify_response(is_staff=True)

    def test_success_unenrolled_staff(self):
        self.setup_course()
        self.setup_user(admin=True, enroll=False, login=True)
        self.verify_response(is_staff=True)

    def test_success_unenrolled_staff_masquerading_as_student(self):
        self.setup_course()
        self.setup_user(admin=True, enroll=False, login=True)
        self.update_masquerade(role='student')
        self.verify_response()

    def test_success_enrolled_student(self):
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=True)
        self.verify_response()

    def test_unauthenticated(self):
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=False)
        self.verify_response(expected_response_code=404)

    def test_unenrolled_student(self):
        self.setup_course()
        self.setup_user(admin=False, enroll=False, login=True)
        self.verify_response(expected_response_code=404)

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_fail_block_unreleased(self):
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=True)
        self.block_to_be_tested.start = datetime.max
        modulestore().update_item(self.block_to_be_tested, self.user.id)
        self.verify_response(expected_response_code=404)

    def test_fail_block_nonvisible(self):
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=True)
        self.block_to_be_tested.visible_to_staff_only = True
        modulestore().update_item(self.block_to_be_tested, self.user.id)
        self.verify_response(expected_response_code=404)

    @ddt.data(
        'vertical_block',
        'html_block',
    )
    def test_student_view_param(self, block_name):
        self.block_name_to_be_tested = block_name
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=True)
        self.verify_response(url_params={'view': 'student_view'})

    def test_unsupported_view_param(self):
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=True)
        self.verify_response(url_params={'view': 'author_view'}, expected_response_code=400)


class FieldOverrideTestMixin:
    """
    A Mixin helper class for classes that test Field Overrides.
    """

    def setUp(self):
        super().setUp()
        OverrideModulestoreFieldData.provider_classes = None

    def tearDown(self):
        super().tearDown()
        OverrideModulestoreFieldData.provider_classes = None


@ddt.ddt
class CoursewareUtilsTests(SharedModuleStoreTestCase):
    """
    Tests of the courseware utils file
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()

    @ddt.data(
        (CourseMode.HONOR, True),
        (CourseMode.PROFESSIONAL, False),
        (CourseMode.VERIFIED, False),
        (CourseMode.AUDIT, True),
        (CourseMode.NO_ID_PROFESSIONAL_MODE, False),
        (CourseMode.CREDIT_MODE, False),
        (CourseMode.MASTERS, False),
        (CourseMode.EXECUTIVE_EDUCATION, False),
    )
    @ddt.unpack
    def test_is_mode_upsellable(self, mode, is_upsellable):
        """
        Test if this is a mode that is upsellable
        """
        CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)
        if mode == CourseMode.CREDIT_MODE:
            CourseModeFactory.create(mode_slug=CourseMode.VERIFIED, course_id=self.course.id)
        enrollment = CourseEnrollmentFactory(
            is_active=True,
            mode=mode,
            course_id=self.course.id,
            user=self.user
        )
        assert is_mode_upsellable(self.user, enrollment) is is_upsellable
