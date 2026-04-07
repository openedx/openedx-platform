"""
Tests for contentstore.views.preview.py
"""
import re
from unittest import mock

import ddt
from django.test.client import Client, RequestFactory
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from web_fragments.fragment import Fragment
from xblock.core import XBlock, XBlockAside

from cms.djangoapps.contentstore.toggles import INDIVIDUALIZE_ANONYMOUS_USER_ID
from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.xblock_config.models import StudioConfig
from common.djangoapps import static_replace
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.xblock_django.constants import ATTR_KEY_ANONYMOUS_USER_ID, ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    upload_file_to_course,
)
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.modulestore.tests.test_asides import AsideTestType

from ..preview import _prepare_runtime_for_preview, get_preview_fragment


@ddt.ddt
class GetPreviewHtmlTestCase(ModuleStoreTestCase):
    """
    Tests for get_preview_fragment.

    Note that there are other existing test cases in test_contentstore that indirectly execute
    get_preview_fragment via the xblock RESTful API.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    def test_preview_fragment(self):
        """
        Test for calling get_preview_html. Ensures data-usage-id is correctly set and
        asides are correctly included.
        """
        course = CourseFactory.create()
        html = BlockFactory.create(
            parent_location=course.location,
            category="html",
            data={'data': "<html>foobar</html>"}
        )

        config = StudioConfig.current()
        config.enabled = True
        config.save()

        request = RequestFactory().get('/dummy-url')
        request.user = UserFactory()
        request.session = {}

        # Call get_preview_fragment directly.
        context = {
            'reorderable_items': set(),
            'read_only': True
        }
        html = get_preview_fragment(request, html, context).content

        # Verify student view html is returned, and the usage ID is as expected.
        html_pattern = re.escape(
            str(course.id.make_usage_key('html', 'replaceme'))
        ).replace('replaceme', r'html_[0-9]*')
        self.assertRegex(
            html,
            f'data-usage-id="{html_pattern}"'
        )
        self.assertRegex(html, '<html>foobar</html>')
        self.assertRegex(html, r"data-block-type=[\"\']test_aside[\"\']")
        self.assertRegex(html, "Aside rendered")
        # Now ensure the acid_aside is not in the result
        self.assertNotRegex(html, r"data-block-type=[\"\']acid_aside[\"\']")

        # Ensure about pages don't have asides
        about = modulestore().get_item(course.id.make_usage_key('about', 'overview'))
        html = get_preview_fragment(request, about, context).content
        self.assertNotRegex(html, r"data-block-type=[\"\']test_aside[\"\']")
        self.assertNotRegex(html, "Aside rendered")

    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    def test_preview_no_asides(self):
        """
        Test for calling get_preview_html. Ensures data-usage-id is correctly set and
        asides are correctly excluded because they are not enabled.
        """
        course = CourseFactory.create()
        html = BlockFactory.create(
            parent_location=course.location,
            category="html",
            data={'data': "<html>foobar</html>"}
        )

        config = StudioConfig.current()
        config.enabled = False
        config.save()

        request = RequestFactory().get('/dummy-url')
        request.user = UserFactory()
        request.session = {}

        # Call get_preview_fragment directly.
        context = {
            'reorderable_items': set(),
            'read_only': True
        }
        html = get_preview_fragment(request, html, context).content

        self.assertNotRegex(html, r"data-block-type=[\"\']test_aside[\"\']")
        self.assertNotRegex(html, "Aside rendered")

    @mock.patch('xmodule.conditional_block.ConditionalBlock.is_condition_satisfied')
    def test_preview_conditional_block_children_context(self, mock_is_condition_satisfied):
        """
        Tests that when empty context is pass to children of ConditionalBlock it will not raise KeyError.
        """
        mock_is_condition_satisfied.return_value = True
        client = Client()
        client.login(username=self.user.username, password=self.user_password)

        course = CourseFactory.create()

        conditional_block = BlockFactory.create(
            parent_location=course.location,
            category="conditional"
        )

        # child conditional_block
        BlockFactory.create(
            parent_location=conditional_block.location,
            category="conditional"
        )

        url = reverse_usage_url(
            'preview_handler',
            conditional_block.location,
            kwargs={'handler': 'xmodule_handler/conditional_get'}
        )
        response = client.post(url)
        self.assertEqual(response.status_code, 200)

    def test_block_branch_not_changed_by_preview_handler(self):
        """
        Tests preview_handler should not update blocks being previewed
        """
        client = Client()
        client.login(username=self.user.username, password=self.user_password)

        course = CourseFactory.create()

        block = BlockFactory.create(
            parent_location=course.location,
            category="problem"
        )

        url = reverse_usage_url(
            'preview_handler',
            block.location,
            kwargs={'handler': 'xmodule_handler/problem_check'}
        )
        response = client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(modulestore().has_changes(modulestore().get_item(block.location)))


@XBlock.needs("i18n")
@XBlock.needs("mako")
@XBlock.needs("replace_urls")
@XBlock.needs("user")
@XBlock.needs("teams_configuration")
class PureXBlock(XBlock):
    """
    Pure XBlock to use in tests.
    """
    def student_view(self, context):
        """
        Renders the output that a student will see.
        """
        fragment = Fragment()
        fragment.add_content(self.runtime.service(self, 'mako').render_lms_template('edxmako.html', context))
        return fragment


@ddt.ddt
class StudioXBlockServiceBindingTest(ModuleStoreTestCase):
    """
    Tests that the Studio Module System (XBlock Runtime) provides an expected set of services.
    """
    def setUp(self):
        """
        Set up the user and request that will be used.
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create()
        self.request = mock.Mock()

    @XBlock.register_temp_plugin(PureXBlock, identifier='pure')
    @ddt.data("user", "i18n", "field-data", "teams_configuration", "replace_urls")
    def test_expected_services_exist(self, expected_service):
        """
        Tests that the 'user' and 'i18n' services are provided by the Studio runtime.
        """
        block = BlockFactory(category="pure", parent=self.course)
        _prepare_runtime_for_preview(self.request, block)
        service = block.runtime.service(block, expected_service)
        self.assertIsNotNone(service)


class CmsModuleSystemShimTest(ModuleStoreTestCase):
    """
    Tests that the deprecated attributes in the Module System (XBlock Runtime) return the expected values.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    COURSE_ID = 'course-v1:edX+LmsModuleShimTest+2021_Fall'
    PYTHON_LIB_FILENAME = 'test_python_lib.zip'
    PYTHON_LIB_SOURCE_FILE = './common/test/data/uploads/python_lib.zip'

    def setUp(self):
        """
        Set up the user, course and other fields that will be used to instantiate the runtime.
        """
        super().setUp()
        course = CourseFactory.create(org='edX', number='LmsModuleShimTest', run='2021_Fall')
        self.user = UserFactory()
        self.request = RequestFactory().get('/dummy-url')
        self.request.user = self.user
        self.request.session = {}
        self.contentstore = contentstore()
        self.block = BlockFactory(category="problem", parent=course)
        _prepare_runtime_for_preview(self.request, block=self.block)
        self.course = self.store.get_item(course.location)

    def test_get_user_role(self):
        assert self.block.runtime.get_user_role() == 'staff'

    @XBlock.register_temp_plugin(PureXBlock, identifier='pure')
    def test_render_template(self):
        block = BlockFactory(category="pure", parent=self.course)
        html = get_preview_fragment(self.request, block, {'element_id': 142}).content
        assert '<div id="142" ns="main">Testing the MakoService</div>' in html

    @override_settings(COURSES_WITH_UNSAFE_CODE=[r'course-v1:edX\+LmsModuleShimTest\+2021_Fall'])
    def test_can_execute_unsafe_code(self):
        assert self.block.runtime.can_execute_unsafe_code()

    def test_cannot_execute_unsafe_code(self):
        assert not self.block.runtime.can_execute_unsafe_code()

    @override_settings(PYTHON_LIB_FILENAME=PYTHON_LIB_FILENAME)
    def test_get_python_lib_zip(self):
        zipfile = upload_file_to_course(
            course_key=self.course.id,
            contentstore=self.contentstore,
            source_file=self.PYTHON_LIB_SOURCE_FILE,
            target_filename=self.PYTHON_LIB_FILENAME,
        )
        assert self.block.runtime.get_python_lib_zip() == zipfile

    def test_no_get_python_lib_zip(self):
        zipfile = upload_file_to_course(
            course_key=self.course.id,
            contentstore=self.contentstore,
            source_file=self.PYTHON_LIB_SOURCE_FILE,
            target_filename=self.PYTHON_LIB_FILENAME,
        )
        assert self.block.runtime.get_python_lib_zip() is None

    def test_cache(self):
        assert hasattr(self.block.runtime.cache, 'get')
        assert hasattr(self.block.runtime.cache, 'set')

    def test_replace_urls(self):
        html = '<a href="/static/id">'
        assert self.block.runtime.service(self.block, 'replace_urls').replace_urls(html) == \
            static_replace.replace_static_urls(html, course_id=self.course.id)

    def test_anonymous_user_id_preview(self):
        assert self.block.runtime.anonymous_student_id == 'student'

    @override_waffle_flag(INDIVIDUALIZE_ANONYMOUS_USER_ID, active=True)
    def test_anonymous_user_id_individual_per_student(self):
        """Test anonymous_user_id on a block which uses per-student anonymous IDs"""
        # Create the runtime with the flag turned on.
        block = BlockFactory(category="problem", parent=self.course)
        _prepare_runtime_for_preview(self.request, block=block)
        deprecated_anonymous_user_id = (
            block.runtime.service(block, 'user').get_current_user().opt_attrs.get(ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID)
        )
        assert deprecated_anonymous_user_id == '26262401c528d7c4a6bbeabe0455ec46'

    @override_waffle_flag(INDIVIDUALIZE_ANONYMOUS_USER_ID, active=True)
    def test_anonymous_user_id_individual_per_course(self):
        """Test anonymous_user_id on a block which uses per-course anonymous IDs"""
        # Create the runtime with the flag turned on.
        block = BlockFactory(category="lti", parent=self.course)
        _prepare_runtime_for_preview(self.request, block=block)

        anonymous_user_id = (
            block.runtime.service(block, 'user').get_current_user().opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID)
        )
        assert anonymous_user_id == 'ad503f629b55c531fed2e45aa17a3368'
