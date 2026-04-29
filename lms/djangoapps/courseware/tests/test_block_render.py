"""
Test for lms courseware app, block render unit
"""
import json
import textwrap
import warnings
from datetime import datetime
from functools import partial
from unittest.mock import MagicMock, Mock, patch

import ddt
import pytest
import pytz
from bson import ObjectId
from completion.models import BlockCompletion  # pylint: disable=wrong-import-order
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH  # pylint: disable=wrong-import-order
from django.conf import settings  # pylint: disable=wrong-import-order
from django.contrib.auth.models import AnonymousUser  # pylint: disable=wrong-import-order
from django.http import Http404, HttpResponse  # pylint: disable=wrong-import-order
from django.middleware.csrf import get_token  # pylint: disable=wrong-import-order
from django.test.client import RequestFactory  # pylint: disable=wrong-import-order
from django.test.utils import override_settings  # pylint: disable=wrong-import-order
from django.urls import reverse  # pylint: disable=wrong-import-order
from edx_proctoring.api import (  # pylint: disable=wrong-import-order
    create_exam,
    create_exam_attempt,
    update_attempt_status,
)
from edx_proctoring.runtime import set_runtime_service  # pylint: disable=wrong-import-order
from edx_proctoring.tests.test_services import (  # pylint: disable=wrong-import-order
    MockCertificateService,
    MockCreditService,
    MockGradesService,
)
from edx_toggles.toggles.testutils import override_waffle_switch  # pylint: disable=wrong-import-order
from edx_when.field_data import DateLookupFieldData  # pylint: disable=wrong-import-order
from freezegun import freeze_time  # pylint: disable=wrong-import-order
from milestones.tests.utils import MilestonesTestCaseMixin  # pylint: disable=wrong-import-order
from opaque_keys.edx.asides import AsideUsageKeyV2  # pylint: disable=wrong-import-order
from opaque_keys.edx.keys import CourseKey, UsageKey  # pylint: disable=wrong-import-order
from pyquery import PyQuery  # pylint: disable=wrong-import-order
from web_fragments.fragment import Fragment  # pylint: disable=wrong-import-order
from xblock.completable import CompletableXBlockMixin  # pylint: disable=wrong-import-order
from xblock.core import XBlock, XBlockAside  # pylint: disable=wrong-import-order
from xblock.exceptions import NoSuchServiceError
from xblock.field_data import FieldData  # pylint: disable=wrong-import-order
from xblock.fields import ScopeIds  # pylint: disable=wrong-import-order
from xblock.runtime import (  # pylint: disable=wrong-import-order
    DictKeyValueStore,
    KvsFieldData,
    Mixologist,  # pylint: disable=wrong-import-order
)
from xblock.test.tools import TestRuntime  # pylint: disable=wrong-import-order
from xblocks_contrib.problem.capa.tests.response_xml_factory import (
    OptionResponseXMLFactory,  # pylint: disable=reimported
)

from common.djangoapps.course_modes.models import CourseMode  # pylint: disable=reimported
from common.djangoapps.student.models import CourseEnrollment, anonymous_id_for_user
from common.djangoapps.student.tests.factories import (
    BetaTesterFactory,
    GlobalStaffFactory,
    InstructorFactory,
    RequestFactoryNoCsrf,
    StaffFactory,
    UserFactory,
)
from common.djangoapps.xblock_django.constants import (
    ATTR_KEY_ANONYMOUS_USER_ID,
    ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID,
    ATTR_KEY_USER_IS_BETA_TESTER,
    ATTR_KEY_USER_IS_GLOBAL_STAFF,
    ATTR_KEY_USER_IS_STAFF,
    ATTR_KEY_USER_ROLE,
)
from common.djangoapps.xblock_django.models import XBlockConfiguration
from lms.djangoapps.courseware import block_render as render
from lms.djangoapps.courseware.access_response import AccessResponse
from lms.djangoapps.courseware.block_render import get_block_for_descriptor, hash_resource
from lms.djangoapps.courseware.courses import get_course_info_section, get_course_with_access
from lms.djangoapps.courseware.field_overrides import OverrideFieldData
from lms.djangoapps.courseware.masquerade import CourseMasquerade
from lms.djangoapps.courseware.model_data import FieldDataCache
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from lms.djangoapps.courseware.tests.test_submitting_problems import TestSubmittingProblems
from lms.djangoapps.courseware.tests.tests import LoginEnrollmentTestCase
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from lms.djangoapps.verify_student.tests.factories import SoftwareSecurePhotoVerificationFactory
from openedx.core.djangoapps.credit.api import set_credit_requirement_status, set_credit_requirements
from openedx.core.djangoapps.credit.models import CreditCourse
from openedx.core.djangoapps.oauth_dispatch.jwt import _create_jwt, create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from openedx.core.lib.courses import course_image_url
from openedx.core.lib.gating import api as gating_api
from openedx.core.lib.url_utils import quote_slashes
from xmodule.capa_block import ProblemBlock
from xmodule.contentstore.django import contentstore
from xmodule.html_block import AboutBlock, CourseInfoBlock, HtmlBlock, StaticTabBlock
from xmodule.lti_block import LTIBlock
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import XBlockI18nService, modulestore
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
    upload_file_to_course,
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
    ToyCourseFactory,
    check_mongo_calls,
)
from xmodule.modulestore.tests.test_asides import AsideTestType  # pylint: disable=wrong-import-order
from xmodule.services import RebindUserServiceError
from xmodule.video_block import VideoBlock  # pylint: disable=wrong-import-order
from xmodule.x_module import STUDENT_VIEW, ModuleStoreRuntime  # pylint: disable=wrong-import-order

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


@XBlock.needs('fs')
@XBlock.needs('mako')
@XBlock.needs('user')
@XBlock.needs('verification')
@XBlock.needs('proctoring')
@XBlock.needs('milestones')
@XBlock.needs('credit')
@XBlock.needs('bookmarks')
@XBlock.needs('gating')
@XBlock.needs('grade_utils')
@XBlock.needs('user_state')
@XBlock.needs('content_type_gating')
@XBlock.needs('cache')
@XBlock.needs('sandbox')
@XBlock.needs('replace_urls')
@XBlock.needs('rebind_user')
@XBlock.needs('completion')
@XBlock.needs('i18n')
@XBlock.needs('library_tools')
@XBlock.needs('partitions')
@XBlock.needs('settings')
@XBlock.needs('user_tags')
@XBlock.needs('badging')
@XBlock.needs('teams')
@XBlock.needs('teams_configuration')
@XBlock.needs('call_to_action')
class PureXBlock(XBlock):
    """
    Pure XBlock to use in tests.
    """
    pass  # pylint: disable=unnecessary-pass


class GradedStatelessXBlock(XBlock):
    """
    This XBlock exists to test grade storage for blocks that don't store
    student state in a scoped field.
    """

    @XBlock.json_handler
    def set_score(self, json_data, suffix):  # pylint: disable=unused-argument
        """
        Set the score for this testing XBlock.
        """
        self.runtime.publish(
            self,
            'grade',
            {
                'value': json_data['grade'],
                'max_value': 1
            }
        )


class StubCompletableXBlock(CompletableXBlockMixin):
    """
    This XBlock exists to test completion storage.
    """

    @XBlock.json_handler
    def complete(self, json_data, suffix):  # pylint: disable=unused-argument
        """
        Mark the block's completion value using the completion API.
        """
        return self.runtime.publish(  # pylint: disable=no-member
            self,
            'completion',
            {'completion': json_data['completion']},
        )

    @XBlock.json_handler
    def progress(self, json_data, suffix):  # pylint: disable=unused-argument
        """
        Mark the block as complete using the deprecated progress interface.

        New code should use the completion event instead.
        """
        return self.runtime.publish(self, 'progress', {})  # pylint: disable=no-member


class XBlockWithoutCompletionAPI(XBlock):
    """
    This XBlock exists to test completion storage for xblocks
    that don't support completion API but do emit progress signal.
    """

    @XBlock.json_handler
    def progress(self, json_data, suffix):  # pylint: disable=unused-argument
        """
        Mark the block as complete using the deprecated progress interface.

        New code should use the completion event instead.
        """
        return self.runtime.publish(self, 'progress', {})


@ddt.ddt
class BlockRenderTestCase(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Tests of courseware.block_render
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = ToyCourseFactory.create().id
        cls.toy_course = modulestore().get_course(cls.course_key)

    # TODO: this test relies on the specific setup of the toy course.
    # It should be rewritten to build the course it needs and then test that.
    def setUp(self):
        """
        Set up the course and user context
        """
        super().setUp()
        OverrideFieldData.provider_classes = None

        self.mock_user = UserFactory()
        self.mock_user.id = 1
        self.request_factory = RequestFactoryNoCsrf()

        # Construct a mock block for the modulestore to return
        self.mock_block = MagicMock()
        self.mock_block.id = 1
        self.dispatch = 'score_update'

        # Construct a 'standard' xqueue_callback url
        self.callback_url = reverse(
            'xqueue_callback',
            kwargs=dict(
                course_id=str(self.course_key),
                userid=str(self.mock_user.id),
                mod_id=self.mock_block.id,
                dispatch=self.dispatch
            )
        )

    def tearDown(self):
        OverrideFieldData.provider_classes = None
        super().tearDown()

    def test_get_block(self):
        assert render.get_block('dummyuser', None, 'invalid location', None) is None

    def test_block_render_with_jump_to_id(self):
        """
        This test validates that the /jump_to_id/<id> shorthand for intracourse linking works assertIn
        expected. Note there's a HTML element in the 'toy' course with the url_name 'toyjumpto' which
        defines this linkage
        """
        mock_request = MagicMock()
        mock_request.user = self.mock_user

        course = get_course_with_access(self.mock_user, 'load', self.course_key)

        field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course_key, self.mock_user, course, depth=2)

        block = render.get_block(
            self.mock_user,
            mock_request,
            self.course_key.make_usage_key('html', 'toyjumpto'),
            field_data_cache,
        )

        # get the rendered HTML output which should have the rewritten link
        html = block.render(STUDENT_VIEW).content

        # See if the url got rewritten to the target link
        # note if the URL mapping changes then this assertion will break
        assert '/courses/' + str(self.course_key) + '/jump_to_id/vertical_test' in html

    def test_xqueue_callback_success(self):
        """
        Test for happy-path xqueue_callback
        """
        fake_key = 'fake key'
        xqueue_header = json.dumps({'lms_key': fake_key})
        data = {
            'xqueue_header': xqueue_header,
            'xqueue_body': 'hello world',
        }

        # Patch getmodule to return our mock block
        with patch('lms.djangoapps.courseware.block_render.load_single_xblock', return_value=self.mock_block):
            # call xqueue_callback with our mocked information
            request = self.request_factory.post(self.callback_url, data)
            render.xqueue_callback(
                request,
                str(self.course_key),
                self.mock_user.id,
                self.mock_block.id,
                self.dispatch
            )

        # Verify that handle ajax is called with the correct data
        request.POST._mutable = True  # pylint: disable=protected-access
        request.POST['queuekey'] = fake_key
        self.mock_block.handle_ajax.assert_called_once_with(self.dispatch, request.POST)

    def test_xqueue_callback_missing_header_info(self):
        data = {
            'xqueue_header': '{}',
            'xqueue_body': 'hello world',
        }

        with patch('lms.djangoapps.courseware.block_render.load_single_xblock', return_value=self.mock_block):
            # Test with missing xqueue data
            with pytest.raises(Http404):  # noqa: PT012
                request = self.request_factory.post(self.callback_url, {})
                render.xqueue_callback(
                    request,
                    str(self.course_key),
                    self.mock_user.id,
                    self.mock_block.id,
                    self.dispatch
                )

            # Test with missing xqueue_header
            with pytest.raises(Http404):  # noqa: PT012
                request = self.request_factory.post(self.callback_url, data)
                render.xqueue_callback(
                    request,
                    str(self.course_key),
                    self.mock_user.id,
                    self.mock_block.id,
                    self.dispatch
                )

    def _get_dispatch_url(self):
        """Helper to get dispatch URL for testing xblock callback."""
        return reverse(
            'xblock_handler',
            args=[
                str(self.course_key),
                quote_slashes(str(self.course_key.make_usage_key('sequential', 'Toy_Videos'))),
                'xmodule_handler',
                'goto_position'
            ]
        )

    def test_anonymous_get_xblock_callback(self):
        """Test that anonymous GET is allowed."""
        dispatch_url = self._get_dispatch_url()
        response = self.client.get(dispatch_url)
        assert 200 == response.status_code

    def test_anonymous_post_xblock_callback(self):
        """Test that anonymous POST is not allowed."""
        dispatch_url = self._get_dispatch_url()
        response = self.client.post(dispatch_url, {'position': 2})

        # https://openedx.atlassian.net/browse/LEARNER-7131
        assert 'Unauthenticated' == response.content.decode('utf-8')
        assert 403 == response.status_code

    def test_session_authentication(self):
        """ Test that the xblock endpoint supports session authentication."""
        self.client.login(username=self.mock_user.username, password=self.TEST_PASSWORD)
        dispatch_url = self._get_dispatch_url()
        response = self.client.post(dispatch_url)
        assert 200 == response.status_code

    def test_oauth_authentication(self):
        """ Test that the xblock endpoint supports OAuth authentication."""
        dispatch_url = self._get_dispatch_url()
        access_token = AccessTokenFactory(user=self.mock_user, application=ApplicationFactory()).token
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + access_token}
        response = self.client.post(dispatch_url, {}, **headers)
        assert 200 == response.status_code

    def test_jwt_authentication(self):
        """ Test that the xblock endpoint supports JWT authentication."""
        dispatch_url = self._get_dispatch_url()
        token = create_jwt_for_user(self.mock_user)
        headers = {'HTTP_AUTHORIZATION': 'JWT ' + token}
        response = self.client.post(dispatch_url, {}, **headers)
        assert 200 == response.status_code

    def test_jwt_authentication_with_restricted_application(self):
        """Test that the XBlock endpoint disallows JWT authentication with restricted applications."""

        def _mock_create_restricted_jwt(*args, **kwargs):
            """Pass an additional argument to `_create_jwt` without modifying the signature of `create_jwt_for_user`."""
            kwargs['is_restricted'] = True
            return _create_jwt(*args, **kwargs)

        with patch('openedx.core.djangoapps.oauth_dispatch.jwt._create_jwt', _mock_create_restricted_jwt):
            token = create_jwt_for_user(self.mock_user)

        dispatch_url = self._get_dispatch_url()
        headers = {'HTTP_AUTHORIZATION': 'JWT ' + token}

        response = self.client.get(dispatch_url, {}, **headers)
        assert 403 == response.status_code

        response = self.client.post(dispatch_url, {}, **headers)
        assert 403 == response.status_code

    def test_missing_position_handler(self):
        """
        Test that sending POST request without or invalid position argument don't raise server error
        """
        self.client.login(username=self.mock_user.username, password=self.TEST_PASSWORD)
        dispatch_url = self._get_dispatch_url()
        response = self.client.post(dispatch_url)
        assert 200 == response.status_code
        assert json.loads(response.content.decode('utf-8')) == {'success': True}

        response = self.client.post(dispatch_url, {'position': ''})
        assert 200 == response.status_code
        assert json.loads(response.content.decode('utf-8')) == {'success': True}

        response = self.client.post(dispatch_url, {'position': '-1'})
        assert 200 == response.status_code
        assert json.loads(response.content.decode('utf-8')) == {'success': True}

        response = self.client.post(dispatch_url, {'position': "string"})
        assert 200 == response.status_code
        assert json.loads(response.content.decode('utf-8')) == {'success': True}

        response = self.client.post(dispatch_url, {'position': "Φυσικά"})
        assert 200 == response.status_code
        assert json.loads(response.content.decode('utf-8')) == {'success': True}

        response = self.client.post(dispatch_url, {'position': ''})
        assert 200 == response.status_code
        assert json.loads(response.content.decode('utf-8')) == {'success': True}

    @ddt.data('pure', 'vertical')
    @XBlock.register_temp_plugin(PureXBlock, identifier='pure')
    def test_rebinding_same_user(self, block_type):
        request = self.request_factory.get('')
        request.user = self.mock_user
        course = CourseFactory()
        block = BlockFactory(category=block_type, parent=course)
        field_data_cache = FieldDataCache([self.toy_course, block], self.toy_course.id, self.mock_user)
        # This is verifying that caching doesn't cause an error during get_block_for_descriptor, which
        # is why it calls the method twice identically.
        render.get_block_for_descriptor(
            self.mock_user,
            request,
            block,
            field_data_cache,
            self.toy_course.id,
            course=self.toy_course
        )
        render.get_block_for_descriptor(
            self.mock_user,
            request,
            block,
            field_data_cache,
            self.toy_course.id,
            course=self.toy_course
        )

    @override_settings(FIELD_OVERRIDE_PROVIDERS=(
        'lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider',
    ))
    @patch(
        'xmodule.modulestore.xml.XMLImportingModuleStoreRuntime.applicable_aside_types',
        lambda self, block: ['test_aside']
    )
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    @ddt.data('regular', 'test_aside')
    def test_rebind_different_users(self, block_category):
        """
        This tests the rebinding a block to a student does not result
        in overly nested _field_data.
        """
        def create_aside(item, block_type):
            """
            Helper function to create aside
            """
            key_store = DictKeyValueStore()
            field_data = KvsFieldData(key_store)
            runtime = TestRuntime(services={'field-data': field_data})

            def_id = runtime.id_generator.create_definition(block_type)
            usage_id = AsideUsageKeyV2(runtime.id_generator.create_usage(def_id), "aside")
            aside = AsideTestType(scope_ids=ScopeIds('user', block_type, def_id, usage_id), runtime=runtime)
            aside.content = '%s_new_value11' % block_type  # noqa: UP031
            aside.data_field = '%s_new_value12' % block_type  # noqa: UP031
            aside.has_score = False

            modulestore().update_item(item, self.mock_user.id, asides=[aside])
            return item

        request = self.request_factory.get('')
        request.user = self.mock_user
        course = CourseFactory.create()

        block = BlockFactory(category="html", parent=course)
        if block_category == 'test_aside':
            block = create_aside(block, "test_aside")

        field_data_cache = FieldDataCache(
            [course, block], course.id, self.mock_user
        )

        # grab what _field_data was originally set to
        original_field_data = block._field_data  # pylint: disable=no-member, protected-access

        render.get_block_for_descriptor(
            self.mock_user, request, block, field_data_cache, course.id, course=course
        )

        # check that block.runtime.service(block, 'field-data-unbound') is the same as the original
        # _field_data, but now _field_data as been reset.
        assert block.runtime.service(block, 'field-data-unbound') is original_field_data
        assert block.runtime.service(block, 'field-data-unbound') is not block._field_data  # pylint: disable=protected-access, line-too-long

        # now bind this block to a few other students
        for user in [UserFactory(), UserFactory(), self.mock_user]:
            render.get_block_for_descriptor(
                user,
                request,
                block,
                field_data_cache,
                course.id,
                course=course
            )

        # _field_data should now be wrapped by LmsFieldData
        # pylint: disable=protected-access
        assert isinstance(block._field_data, LmsFieldData)  # pylint: disable=no-member

        # the LmsFieldData should now wrap OverrideFieldData
        assert isinstance(block._field_data._authored_data._source, OverrideFieldData)   # pylint: disable=no-member, line-too-long

        # the OverrideFieldData should point to the date FieldData
        assert isinstance(block._field_data._authored_data._source.fallback, DateLookupFieldData)    # pylint: disable=no-member, line-too-long
        assert block._field_data._authored_data._source.fallback._defaults \
            is block.runtime.service(block, 'field-data-unbound')

    def test_hash_resource(self):
        """
        Ensure that the resource hasher works and does not fail on unicode,
        decoded or otherwise.
        """
        resources = ['ASCII text', '❄ I am a special snowflake.', "❄ So am I, but I didn't tell you."]
        assert hash_resource(resources) == '50c2ae79fbce9980e0803848914b0a09'


@ddt.ddt
class TestHandleXBlockCallback(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test the handle_xblock_callback function
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = ToyCourseFactory.create().id
        cls.toy_course = modulestore().get_course(cls.course_key)

    def setUp(self):
        super().setUp()

        self.location = self.course_key.make_usage_key('chapter', 'Overview')
        self.mock_user = UserFactory.create()
        self.request_factory = RequestFactoryNoCsrf()

        # Construct a mock block for the modulestore to return
        self.mock_block = MagicMock()
        self.mock_block.id = 1
        self.dispatch = 'score_update'

        # Construct a 'standard' xqueue_callback url
        self.callback_url = reverse(
            'xqueue_callback', kwargs={
                'course_id': str(self.course_key),
                'userid': str(self.mock_user.id),
                'mod_id': self.mock_block.id,
                'dispatch': self.dispatch
            }
        )

    def _mock_file(self, name='file', size=10):
        """Create a mock file object for testing uploads"""
        mock_file = MagicMock(
            size=size,
            read=lambda: 'x' * size
        )
        # We can't use `name` as a kwarg to Mock to set the name attribute
        # because mock uses `name` to name the mock itself
        mock_file.name = name
        return mock_file

    def make_xblock_callback_response(self, request_data, course, block, handler):
        """
        Prepares an xblock callback request and returns response to it.
        """
        request = self.request_factory.post(
            '/',
            data=json.dumps(request_data),
            content_type='application/json',
        )
        request.user = self.mock_user
        response = render.handle_xblock_callback(
            request,
            str(course.id),
            quote_slashes(str(block.scope_ids.usage_id)),
            handler,
            '',
        )

        return response

    def test_invalid_csrf_token(self):
        """
        Verify that invalid CSRF token is rejected.
        """
        request = RequestFactory().post('dummy_url', data={'position': 1})
        csrf_token = get_token(request)
        request._post = {'csrfmiddlewaretoken': f'{csrf_token}-dummy'}  # pylint: disable=protected-access
        request.user = self.mock_user
        request.COOKIES[settings.CSRF_COOKIE_NAME] = csrf_token

        response = render.handle_xblock_callback(
            request,
            str(self.course_key),
            quote_slashes(str(self.location)),
            'xmodule_handler',
            'goto_position',
        )
        assert 403 == response.status_code

    def test_valid_csrf_token(self):
        """
        Verify that valid CSRF token is accepted.
        """
        request = RequestFactory().post('dummy_url', data={'position': 1})
        csrf_token = get_token(request)
        request._post = {'csrfmiddlewaretoken': csrf_token}  # pylint: disable=protected-access
        request.user = self.mock_user
        request.COOKIES[settings.CSRF_COOKIE_NAME] = csrf_token

        response = render.handle_xblock_callback(
            request,
            str(self.course_key),
            quote_slashes(str(self.location)),
            'xmodule_handler',
            'goto_position',
        )
        assert 200 == response.status_code

    def test_invalid_location(self):
        request = self.request_factory.post('dummy_url', data={'position': 1})
        request.user = self.mock_user
        with pytest.raises(Http404):
            render.handle_xblock_callback(
                request,
                str(self.course_key),
                'invalid Location',
                'dummy_handler'
                'dummy_dispatch'
            )

    def test_too_many_files(self):
        request = self.request_factory.post(
            'dummy_url',
            data={'file_id': (self._mock_file(), ) * (settings.MAX_FILEUPLOADS_PER_INPUT + 1)}
        )
        request.user = self.mock_user
        assert render.handle_xblock_callback(request, str(self.course_key), quote_slashes(str(self.location)), 'dummy_handler').content.decode('utf-8') == json.dumps({'success': (f'Submission aborted! Maximum {settings.MAX_FILEUPLOADS_PER_INPUT:d} files may be submitted at once')}, indent=2)  # pylint: disable=line-too-long

    def test_too_large_file(self):
        inputfile = self._mock_file(size=1 + settings.STUDENT_FILEUPLOAD_MAX_SIZE)
        request = self.request_factory.post(
            'dummy_url',
            data={'file_id': inputfile}
        )
        request.user = self.mock_user
        assert render.handle_xblock_callback(request, str(self.course_key), quote_slashes(str(self.location)), 'dummy_handler').content.decode('utf-8') == json.dumps({'success': ('Submission aborted! Your file "%s" is too large (max size: %d MB)' % (inputfile.name, (settings.STUDENT_FILEUPLOAD_MAX_SIZE / (1000 ** 2))))}, indent=2)  # pylint: disable=line-too-long  # noqa: UP031

    def test_xblock_dispatch(self):
        request = self.request_factory.post('dummy_url', data={'position': 1})
        request.user = self.mock_user
        response = render.handle_xblock_callback(
            request,
            str(self.course_key),
            quote_slashes(str(self.location)),
            'xmodule_handler',
            'goto_position',
        )
        assert isinstance(response, HttpResponse)

    def test_bad_course_id(self):
        request = self.request_factory.post('dummy_url')
        request.user = self.mock_user
        with pytest.raises(Http404):
            render.handle_xblock_callback(
                request,
                'bad_course_id',
                quote_slashes(str(self.location)),
                'xmodule_handler',
                'goto_position',
            )

    def test_bad_location(self):
        request = self.request_factory.post('dummy_url')
        request.user = self.mock_user
        with pytest.raises(Http404):
            render.handle_xblock_callback(
                request,
                str(self.course_key),
                quote_slashes(str(self.course_key.make_usage_key('chapter', 'bad_location'))),
                'xmodule_handler',
                'goto_position',
            )

    def test_bad_xblock_dispatch(self):
        request = self.request_factory.post('dummy_url')
        request.user = self.mock_user
        with pytest.raises(Http404):
            render.handle_xblock_callback(
                request,
                str(self.course_key),
                quote_slashes(str(self.location)),
                'xmodule_handler',
                'bad_dispatch',
            )

    def test_missing_handler(self):
        request = self.request_factory.post('dummy_url')
        request.user = self.mock_user
        with pytest.raises(Http404):
            render.handle_xblock_callback(
                request,
                str(self.course_key),
                quote_slashes(str(self.location)),
                'bad_handler',
                'bad_dispatch',
            )

    @XBlock.register_temp_plugin(GradedStatelessXBlock, identifier='stateless_scorer')
    def test_score_without_student_state(self):
        course = CourseFactory.create()
        block = BlockFactory.create(category='stateless_scorer', parent=course)

        request = self.request_factory.post(
            'dummy_url',
            data=json.dumps({"grade": 0.75}),
            content_type='application/json'
        )
        request.user = self.mock_user

        response = render.handle_xblock_callback(
            request,
            str(course.id),
            quote_slashes(str(block.scope_ids.usage_id)),
            'set_score',
            '',
        )
        assert response.status_code == 200
        student_module = StudentModule.objects.get(
            student=self.mock_user,
            module_state_key=block.scope_ids.usage_id,
        )
        assert student_module.grade == 0.75
        assert student_module.max_grade == 1

    @ddt.data(
        ('complete', {'completion': 0.625}),
        ('progress', {}),
    )
    @ddt.unpack
    @XBlock.register_temp_plugin(StubCompletableXBlock, identifier='comp')
    def test_completion_events_with_completion_disabled(self, signal, data):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, False):
            course = CourseFactory.create()
            block = BlockFactory.create(category='comp', parent=course)
            request = self.request_factory.post(
                '/',
                data=json.dumps(data),
                content_type='application/json',
            )
            request.user = self.mock_user
            with patch('completion.models.BlockCompletionManager.submit_completion') as mock_complete:
                render.handle_xblock_callback(
                    request,
                    str(course.id),
                    quote_slashes(str(block.scope_ids.usage_id)),
                    signal,
                    '',
                )
                mock_complete.assert_not_called()
            assert not BlockCompletion.objects.filter(block_key=block.scope_ids.usage_id).exists()

    @XBlock.register_temp_plugin(StubCompletableXBlock, identifier='comp')
    def test_completion_signal_for_completable_xblock(self):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            course = CourseFactory.create()
            block = BlockFactory.create(category='comp', parent=course)

            response = self.make_xblock_callback_response(
                {'completion': 0.625}, course, block, 'complete'
            )

            assert response.status_code == 200
            completion = BlockCompletion.objects.get(block_key=block.scope_ids.usage_id)
            assert completion.completion == 0.625

    @XBlock.register_temp_plugin(StubCompletableXBlock, identifier='comp')
    @ddt.data((True, True), (False, False),)
    @ddt.unpack
    def test_aside(self, is_xblock_aside, is_get_aside_called):
        """
        test get_aside_from_xblock called
        """
        course = CourseFactory.create()
        block = BlockFactory.create(category='comp', parent=course)
        request = self.request_factory.post(
            '/',
            data=json.dumps({'completion': 0.625}),
            content_type='application/json',
        )
        request.user = self.mock_user

        def get_usage_key():
            """return usage key"""
            return (
                quote_slashes(str(AsideUsageKeyV2(block.scope_ids.usage_id, "aside")))
                if is_xblock_aside
                else str(block.scope_ids.usage_id)
            )

        with patch(
            'lms.djangoapps.courseware.block_render.is_xblock_aside',
            return_value=is_xblock_aside
        ), patch(
            'lms.djangoapps.courseware.block_render.get_aside_from_xblock'
        ) as mocked_get_aside_from_xblock, patch(
            'lms.djangoapps.courseware.block_render.webob_to_django_response'
        ) as mocked_webob_to_django_response:
            render.handle_xblock_callback(
                request,
                str(course.id),
                get_usage_key(),
                'complete',
                '',
            )
            assert mocked_webob_to_django_response.called is True
        assert mocked_get_aside_from_xblock.called is is_get_aside_called

    def test_aside_invalid_usage_id(self):
        """
        test aside work when invalid usage id
        """
        course = CourseFactory.create()
        request = self.request_factory.post(
            '/',
            data=json.dumps({'completion': 0.625}),
            content_type='application/json',
        )
        request.user = self.mock_user

        with patch(
            'lms.djangoapps.courseware.block_render.is_xblock_aside',
            return_value=True
        ), self.assertRaises(Http404):  # noqa: PT027
            render.handle_xblock_callback(
                request,
                str(course.id),
                "foo@bar",
                'complete',
                '',
            )

    @XBlock.register_temp_plugin(StubCompletableXBlock, identifier='comp')
    def test_progress_signal_ignored_for_completable_xblock(self):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            course = CourseFactory.create()
            block = BlockFactory.create(category='comp', parent=course)

            response = self.make_xblock_callback_response(
                {}, course, block, 'progress'
            )

            assert response.status_code == 200
            assert not BlockCompletion.objects.filter(block_key=block.scope_ids.usage_id).exists()

    @XBlock.register_temp_plugin(XBlockWithoutCompletionAPI, identifier='no_comp')
    def test_progress_signal_processed_for_xblock_without_completion_api(self):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            course = CourseFactory.create()
            block = BlockFactory.create(category='no_comp', parent=course)

            response = self.make_xblock_callback_response(
                {}, course, block, 'progress'
            )

            assert response.status_code == 200
            completion = BlockCompletion.objects.get(block_key=block.scope_ids.usage_id)
            assert completion.completion == 1.0

    @XBlock.register_temp_plugin(StubCompletableXBlock, identifier='comp')
    def test_skip_handlers_for_masquerading_staff(self):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            course = CourseFactory.create()
            block = BlockFactory.create(category='comp', parent=course)
            request = self.request_factory.post(
                '/',
                data=json.dumps({'completion': 0.8}),
                content_type='application/json',
            )
            request.user = self.mock_user
            request.session = {}
            request.user.real_user = GlobalStaffFactory.create()
            request.user.real_user.masquerade_settings = CourseMasquerade(course.id, user_name="jem")
            with patch('xmodule.services.is_masquerading_as_specific_student') as mock_masq:
                mock_masq.return_value = True
                response = render.handle_xblock_callback(
                    request,
                    str(course.id),
                    quote_slashes(str(block.scope_ids.usage_id)),
                    'complete',
                    '',
                )
            mock_masq.assert_called()
        assert response.status_code == 200
        with pytest.raises(BlockCompletion.DoesNotExist):
            BlockCompletion.objects.get(block_key=block.scope_ids.usage_id)

    @XBlock.register_temp_plugin(GradedStatelessXBlock, identifier='stateless_scorer')
    @patch('xmodule.services.grades_signals.SCORE_PUBLISHED.send')
    def test_anonymous_user_not_be_graded(self, mock_score_signal):
        course = CourseFactory.create()
        block_kwargs = {
            'category': 'problem',
        }
        request = self.request_factory.get('/')
        request.user = AnonymousUser()
        block = BlockFactory.create(**block_kwargs)

        render.handle_xblock_callback(
            request,
            str(course.id),
            quote_slashes(str(block.location)),
            'xmodule_handler',
            'problem_check',
        )
        assert not mock_score_signal.called

    @ddt.data(
        # See seq_block.py for the definition of these handlers
        ('get_completion', True),  # has the 'will_recheck_access' attribute set to True
        ('goto_position', False),  # does not set it
    )
    @ddt.unpack
    @patch('lms.djangoapps.courseware.block_render.get_block_for_descriptor', wraps=get_block_for_descriptor)
    def test_will_recheck_access_handler_attribute(self, handler, will_recheck_access, mock_get_block):
        """Confirm that we pay attention to any 'will_recheck_access' attributes on handler methods"""
        course = CourseFactory.create()
        block_kwargs = {
            'category': 'sequential',
            'parent': course,
        }
        block = BlockFactory.create(**block_kwargs)
        usage_id = str(block.location)

        # Send no special parameters, which will be invalid, but we don't care
        request = self.request_factory.post('/', data='{}', content_type='application/json')
        request.user = self.mock_user

        render.handle_xblock_callback(request, str(course.id), usage_id, handler)
        assert mock_get_block.call_count == 2
        assert mock_get_block.call_args[1]['will_recheck_access'] == will_recheck_access


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_XBLOCK_VIEW_ENDPOINT': True})
class TestXBlockView(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test the handle_xblock_callback function
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = ToyCourseFactory.create().id
        cls.toy_course = modulestore().get_course(cls.course_key)

    def setUp(self):
        super().setUp()

        self.location = str(self.course_key.make_usage_key('html', 'toyhtml'))
        self.request_factory = RequestFactory()

        self.view_args = [str(self.course_key), quote_slashes(self.location), 'student_view']
        self.xblock_view_url = reverse('xblock_view', args=self.view_args)

    def test_xblock_view_handler(self):
        request = self.request_factory.get(self.xblock_view_url)
        request.user = UserFactory.create()
        response = render.xblock_view(request, *self.view_args)
        assert 200 == response.status_code

        expected = ['csrf_token', 'html', 'resources']
        content = json.loads(response.content.decode('utf-8'))
        for section in expected:
            assert section in content
        doc = PyQuery(content['html'])
        assert len(doc('div.xblock-student_view-html')) == 1

    @ddt.data(True, False)
    def test_hide_staff_markup(self, hide):
        """
        When xblock_view gets 'hide_staff_markup' in its context, the staff markup
        should not be included. See 'add_staff_markup' in xblock_utils/__init__.py
        """
        request = self.request_factory.get(self.xblock_view_url)
        request.user = GlobalStaffFactory.create()
        request.session = {}
        if hide:
            request.GET = {'hide_staff_markup': 'true'}
        response = render.xblock_view(request, *self.view_args)
        assert 200 == response.status_code

        html = json.loads(response.content.decode('utf-8'))['html']
        assert ('Staff Debug Info' in html) == (not hide)

    def test_xblock_view_handler_not_authenticated(self):
        request = self.request_factory.get(self.xblock_view_url)
        request.user = AnonymousUser()
        response = render.xblock_view(request, *self.view_args)
        assert 401 == response.status_code


@ddt.ddt
class TestTOC(ModuleStoreTestCase):
    """Check the Table of Contents for a course"""

    def setup_request_and_course(self, num_finds, num_sends):
        """
        Sets up the toy course in the modulestore and the request object.
        """
        self.course_key = ToyCourseFactory.create().id  # pylint: disable=attribute-defined-outside-init
        self.chapter = 'Overview'  # pylint: disable=attribute-defined-outside-init
        chapter_url = '{}/{}/{}'.format('/courses', self.course_key, self.chapter)
        factory = RequestFactoryNoCsrf()
        self.request = factory.get(chapter_url)  # pylint: disable=attribute-defined-outside-init
        self.request.user = UserFactory()
        self.modulestore = self.store._get_modulestore_for_courselike(self.course_key)  # pylint: disable=protected-access, attribute-defined-outside-init
        with self.modulestore.bulk_operations(self.course_key):
            with check_mongo_calls(num_finds, num_sends):
                self.toy_course = self.store.get_course(self.course_key, depth=2)  # pylint: disable=attribute-defined-outside-init
                self.field_data_cache = FieldDataCache.cache_for_block_descendents(  # pylint: disable=attribute-defined-outside-init
                    self.course_key, self.request.user, self.toy_course, depth=2
                )

    # Split makes 2 queries to load the course to depth 2:
    #     - 1 for the structure
    #     - 1 for 5 definitions
    # Split makes 1 MySQL query to render the toc:
    #     - 1 MySQL for the active version at the start of the bulk operation (no mongo calls)
    def test_toc_toy_from_chapter(self):
        with self.store.default_store(ModuleStoreEnum.Type.split):
            self.setup_request_and_course(2, 0)

            expected = ([{'active': True, 'sections':
                          [{'url_name': 'Toy_Videos', 'display_name': 'Toy Videos', 'graded': True,
                            'format': 'Lecture Sequence', 'due': None, 'active': False},
                           {'url_name': 'Welcome', 'display_name': 'Welcome', 'graded': True,
                            'format': '', 'due': None, 'active': False},
                           {'url_name': 'video_123456789012', 'display_name': 'Test Video', 'graded': True,
                            'format': '', 'due': None, 'active': False},
                           {'url_name': 'video_4f66f493ac8f', 'display_name': 'Video', 'graded': True,
                            'format': '', 'due': None, 'active': False}],
                          'url_name': 'Overview', 'display_name': 'Overview', 'display_id': 'overview'},
                         {'active': False, 'sections':
                          [{'url_name': 'toyvideo', 'display_name': 'toyvideo', 'graded': True,
                            'format': '', 'due': None, 'active': False}],
                          'url_name': 'secret:magic', 'display_name': 'secret:magic', 'display_id': 'secretmagic'}])

            course = self.store.get_course(self.toy_course.id, depth=2)
            with check_mongo_calls(0):
                actual = render.toc_for_course(
                    self.request.user, self.request, course, self.chapter, None, self.field_data_cache
                )
        for toc_section in expected:
            assert toc_section in actual['chapters']
        assert actual['previous_of_active_section'] is None
        assert actual['next_of_active_section'] is None

    # Split makes 2 queries to load the course to depth 2:
    #     - 1 for the structure
    #     - 1 for 5 definitions
    # Split makes 1 MySQL query to render the toc:
    #     - 1 MySQL for the active version at the start of the bulk operation (no mongo calls)
    def test_toc_toy_from_section(self):
        with self.store.default_store(ModuleStoreEnum.Type.split):
            self.setup_request_and_course(2, 0)
            section = 'Welcome'
            expected = ([{'active': True, 'sections':
                          [{'url_name': 'Toy_Videos', 'display_name': 'Toy Videos', 'graded': True,
                            'format': 'Lecture Sequence', 'due': None, 'active': False},
                           {'url_name': 'Welcome', 'display_name': 'Welcome', 'graded': True,
                            'format': '', 'due': None, 'active': True},
                           {'url_name': 'video_123456789012', 'display_name': 'Test Video', 'graded': True,
                            'format': '', 'due': None, 'active': False},
                           {'url_name': 'video_4f66f493ac8f', 'display_name': 'Video', 'graded': True,
                            'format': '', 'due': None, 'active': False}],
                          'url_name': 'Overview', 'display_name': 'Overview', 'display_id': 'overview'},
                         {'active': False, 'sections':
                          [{'url_name': 'toyvideo', 'display_name': 'toyvideo', 'graded': True,
                            'format': '', 'due': None, 'active': False}],
                          'url_name': 'secret:magic', 'display_name': 'secret:magic', 'display_id': 'secretmagic'}])

            with check_mongo_calls(0):
                actual = render.toc_for_course(
                    self.request.user, self.request, self.toy_course, self.chapter, section, self.field_data_cache
                )
            for toc_section in expected:
                assert toc_section in actual['chapters']
            assert actual['previous_of_active_section']['url_name'] == 'Toy_Videos'
            assert actual['next_of_active_section']['url_name'] == 'video_123456789012'


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
class TestProctoringRendering(ModuleStoreTestCase):
    """Check the Table of Contents for a course"""
    def setUp(self):
        """
        Set up the initial mongo datastores
        """
        super().setUp()
        self.course_key = ToyCourseFactory.create(enable_proctored_exams=True).id
        self.chapter = 'Overview'
        chapter_url = '{}/{}/{}'.format('/courses', self.course_key, self.chapter)
        factory = RequestFactoryNoCsrf()
        self.request = factory.get(chapter_url)
        self.request.user = UserFactory.create()
        self.user = UserFactory.create()
        SoftwareSecurePhotoVerificationFactory.create(user=self.request.user)
        self.modulestore = self.store._get_modulestore_for_courselike(self.course_key)  # pylint: disable=protected-access
        with self.modulestore.bulk_operations(self.course_key):
            self.toy_course = self.store.get_course(self.course_key, depth=2)
            self.field_data_cache = FieldDataCache.cache_for_block_descendents(
                self.course_key, self.request.user, self.toy_course, depth=2
            )

    @ddt.data(
        (CourseMode.DEFAULT_MODE_SLUG, False, None, None),
        (
            CourseMode.DEFAULT_MODE_SLUG,
            True,
            'eligible',
            {
                'status': 'eligible',
                'short_description': 'Ungraded Practice Exam',
                'suggested_icon': '',
                'in_completed_state': False
            }
        ),
        (
            CourseMode.DEFAULT_MODE_SLUG,
            True,
            'submitted',
            {
                'status': 'submitted',
                'short_description': 'Practice Exam Completed',
                'suggested_icon': 'fa-check',
                'in_completed_state': True
            }
        ),
        (
            CourseMode.DEFAULT_MODE_SLUG,
            True,
            'error',
            {
                'status': 'error',
                'short_description': 'Practice Exam Failed',
                'suggested_icon': 'fa-exclamation-triangle',
                'in_completed_state': True
            }
        ),
        (
            CourseMode.VERIFIED,
            False,
            None,
            {
                'status': 'eligible',
                'short_description': 'Proctored Option Available',
                'suggested_icon': 'fa-pencil-square-o',
                'in_completed_state': False
            }
        ),
        (
            CourseMode.VERIFIED,
            False,
            'declined',
            {
                'status': 'declined',
                'short_description': 'Taking As Open Exam',
                'suggested_icon': 'fa-pencil-square-o',
                'in_completed_state': False
            }
        ),
        (
            CourseMode.VERIFIED,
            False,
            'submitted',
            {
                'status': 'submitted',
                'short_description': 'Pending Session Review',
                'suggested_icon': 'fa-spinner fa-spin',
                'in_completed_state': True
            }
        ),
        (
            CourseMode.VERIFIED,
            False,
            'verified',
            {
                'status': 'verified',
                'short_description': 'Passed Proctoring',
                'suggested_icon': 'fa-check',
                'in_completed_state': True
            }
        ),
        (
            CourseMode.VERIFIED,
            False,
            'rejected',
            {
                'status': 'rejected',
                'short_description': 'Failed Proctoring',
                'suggested_icon': 'fa-exclamation-triangle',
                'in_completed_state': True
            }
        ),
        (
            CourseMode.VERIFIED,
            False,
            'error',
            {
                'status': 'error',
                'short_description': 'Failed Proctoring',
                'suggested_icon': 'fa-exclamation-triangle',
                'in_completed_state': True
            }
        ),
    )
    @ddt.unpack
    def test_proctored_exam_toc(self, enrollment_mode, is_practice_exam,
                                attempt_status, expected):
        """
        Generate TOC for a course with a single chapter/sequence which contains proctored exam
        """
        self._setup_test_data(enrollment_mode, is_practice_exam, attempt_status)

        actual = render.toc_for_course(
            self.request.user,
            self.request,
            self.toy_course,
            self.chapter,
            'Toy_Videos',
            self.field_data_cache
        )
        section_actual = self._find_section(actual['chapters'], 'Overview', 'Toy_Videos')

        if expected:
            assert expected in [section_actual['proctoring']]
        else:
            # we expect there not to be a 'proctoring' key in the dict
            assert 'proctoring' not in section_actual
        assert actual['previous_of_active_section'] is None
        assert actual['next_of_active_section']['url_name'] == 'Welcome'

    @ddt.data(
        (
            CourseMode.VERIFIED,
            False,
            None,
            'This exam is proctored',
            False
        ),
        (
            CourseMode.VERIFIED,
            False,
            'submitted',
            'You have submitted this proctored exam for review',
            True
        ),
        (
            CourseMode.VERIFIED,
            False,
            'verified',
            'Your proctoring session was reviewed successfully',
            False
        ),
        (
            CourseMode.VERIFIED,
            False,
            'rejected',
            'Your proctoring session was reviewed, but did not pass all requirements',
            True
        ),
        (
            CourseMode.VERIFIED,
            False,
            'error',
            'A system error has occurred with your proctored exam',
            False
        ),
    )
    @ddt.unpack
    def test_render_proctored_exam(self, enrollment_mode, is_practice_exam,
                                   attempt_status, expected, with_credit_context):
        """
        Verifies gated content from the student view rendering of a sequence
        this is labeled as a proctored exam
        """
        usage_key = self._setup_test_data(enrollment_mode, is_practice_exam, attempt_status)

        # initialize some credit requirements, if so then specify
        if with_credit_context:
            credit_course = CreditCourse(course_key=self.course_key, enabled=True)
            credit_course.save()
            set_credit_requirements(
                self.course_key,
                [
                    {
                        'namespace': 'reverification',
                        'name': 'reverification-1',
                        'display_name': 'ICRV1',
                        'criteria': {},
                    },
                    {
                        'namespace': 'proctored-exam',
                        'name': 'Exam1',
                        'display_name': 'A Proctored Exam',
                        'criteria': {}
                    }
                ]
            )

            set_credit_requirement_status(
                self.request.user,
                self.course_key,
                'reverification',
                'ICRV1'
            )

        block = render.get_block(
            self.request.user,
            self.request,
            usage_key,
            self.field_data_cache,
            wrap_xblock_display=True,
        )
        content = block.render(STUDENT_VIEW).content

        assert expected in content

    def _setup_test_data(self, enrollment_mode, is_practice_exam, attempt_status):
        """
        Helper method to consolidate some courseware/proctoring/credit
        test harness data
        """
        usage_key = self.course_key.make_usage_key('sequential', 'Toy_Videos')

        with self.modulestore.bulk_operations(self.toy_course.id):
            sequence = self.modulestore.get_item(usage_key)
            sequence.is_time_limited = True
            sequence.is_proctored_exam = True
            sequence.is_practice_exam = is_practice_exam
            self.modulestore.update_item(sequence, self.user.id)

        self.toy_course = self.update_course(self.toy_course, self.user.id)

        # refresh cache after update
        self.field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course_key, self.request.user, self.toy_course, depth=2
        )

        set_runtime_service(
            'credit',
            MockCreditService(enrollment_mode=enrollment_mode)
        )
        CourseEnrollment.enroll(self.request.user, self.course_key, mode=enrollment_mode)

        set_runtime_service(
            'grades',
            MockGradesService()
        )

        set_runtime_service(
            'certificates',
            MockCertificateService()
        )

        exam_id = create_exam(
            course_id=str(self.course_key),
            content_id=str(sequence.location.replace(branch=None, version=None)),
            exam_name='foo',
            time_limit_mins=10,
            is_proctored=True,
            is_practice_exam=is_practice_exam
        )

        if attempt_status:
            attempt_id = create_exam_attempt(
                str(exam_id).encode('utf-8'),
                self.request.user.id,
                taking_as_proctored=True
            )
            update_attempt_status(attempt_id, attempt_status)

        return usage_key

    def _find_url_name(self, toc, url_name):
        """
        Helper to return the dict TOC section associated with a Chapter of url_name
        """

        for entry in toc:
            if entry['url_name'] == url_name:
                return entry

        return None

    def _find_section(self, toc, chapter_url_name, section_url_name):
        """
        Helper to return the dict TOC section associated with a section of url_name
        """

        chapter = self._find_url_name(toc, chapter_url_name)
        if chapter:
            return self._find_url_name(chapter['sections'], section_url_name)

        return None


class TestGatedSubsectionRendering(ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Test the toc for a course is rendered correctly when there is gated content
    """
    def setUp(self):
        """
        Set up the initial test data
        """
        super().setUp()

        self.course = CourseFactory.create(enable_subsection_gating=True)
        self.chapter = BlockFactory.create(
            parent=self.course,
            category="chapter",
            display_name="Chapter"
        )
        self.open_seq = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name="Open Sequential"
        )
        self.gated_seq = BlockFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name="Gated Sequential"
        )
        self.course = self.update_course(self.course, 0)

        self.request = RequestFactoryNoCsrf().get(f'/courses/{self.course.id}/{self.chapter.display_name}')
        self.request.user = UserFactory()
        self.field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id, self.request.user, self.course, depth=2
        )
        gating_api.add_prerequisite(self.course.id, self.open_seq.location)
        gating_api.set_required_content(self.course.id, self.gated_seq.location, self.open_seq.location, 100)

    def _find_url_name(self, toc, url_name):
        """
        Helper to return the TOC section associated with url_name
        """

        for entry in toc:
            if entry['url_name'] == url_name:
                return entry

        return None

    def _find_sequential(self, toc, chapter_url_name, sequential_url_name):
        """
        Helper to return the sequential associated with sequential_url_name
        """

        chapter = self._find_url_name(toc, chapter_url_name)
        if chapter:
            return self._find_url_name(chapter['sections'], sequential_url_name)

        return None

    def test_toc_with_gated_sequential(self):
        """
        Test generation of TOC for a course with a gated subsection
        """
        actual = render.toc_for_course(
            self.request.user,
            self.request,
            self.course,
            self.chapter.display_name,
            self.open_seq.display_name,
            self.field_data_cache
        )
        assert self._find_sequential(actual['chapters'], 'Chapter', 'Open_Sequential') is not None
        assert self._find_sequential(actual['chapters'], 'Chapter', 'Gated_Sequential') is not None
        assert self._find_sequential(actual['chapters'], 'Non-existent_Chapter', 'Non-existent_Sequential') is None
        assert actual['previous_of_active_section'] is None
        assert actual['next_of_active_section'] is None


@ddt.ddt
class TestHtmlModifiers(ModuleStoreTestCase):
    """
    Tests to verify that standard modifications to the output of XModule/XBlock
    student_view are taking place
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.request = RequestFactoryNoCsrf().get('/')
        self.request.user = self.user
        self.request.session = {}
        self.content_string = '<p>This is the content<p>'
        self.rewrite_link = '<a href="/static/foo/content">Test rewrite</a>'
        self.rewrite_bad_link = '<img src="/static//file.jpg" />'
        self.course_link = '<a href="/course/bar/content">Test course rewrite</a>'
        self.block = BlockFactory.create(
            category='html',
            data=self.content_string + self.rewrite_link + self.rewrite_bad_link + self.course_link
        )
        self.location = self.block.location
        self.field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id,
            self.user,
            self.block
        )

    def test_xblock_display_wrapper_enabled(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
            wrap_xblock_display=True,
        )
        result_fragment = block.render(STUDENT_VIEW)

        assert len(PyQuery(result_fragment.content)('div.xblock.xblock-student_view.xmodule_HtmlBlock')) == 1

    def test_xmodule_display_wrapper_disabled(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
            wrap_xblock_display=False,
        )
        result_fragment = block.render(STUDENT_VIEW)

        assert 'div class="xblock xblock-student_view xmodule_display xmodule_HtmlBlock"' not in result_fragment.content

    def test_static_link_rewrite(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)
        key = self.course.location
        assert f'/asset-v1:{key.org}+{key.course}+{key.run}+type@asset+block/foo_content' in result_fragment.content

    def test_static_badlink_rewrite(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)

        key = self.course.location
        assert f'/asset-v1:{key.org}+{key.course}+{key.run}+type@asset+block/file.jpg' in result_fragment.content

    def test_static_asset_path_use(self):
        '''
        when a course is loaded with do_import_static=False (see xml_importer.py), then
        static_asset_path is set as an lms kv in course.  That should make static paths
        not be mangled (ie not changed to c4x://).
        '''
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
            static_asset_path="toy_course_dir",
        )
        result_fragment = block.render(STUDENT_VIEW)
        assert 'href="/static/toy_course_dir' in result_fragment.content

    def test_course_image(self):
        url = course_image_url(self.course)
        assert url.startswith('/asset-v1:')

        self.course.static_asset_path = "toy_course_dir"
        url = course_image_url(self.course)
        assert url.startswith('/static/toy_course_dir/')
        self.course.static_asset_path = ""

    @override_settings(DEFAULT_COURSE_ABOUT_IMAGE_URL='test.png')
    def test_course_image_for_split_course(self):
        """
        for split courses if course_image is empty then course_image_url will be
        the default image url defined in settings
        """
        self.course = CourseFactory.create()
        self.course.course_image = ''

        url = course_image_url(self.course)
        assert '/static/test.png' == url

    def test_get_course_info_section(self):
        self.course.static_asset_path = "toy_course_dir"
        get_course_info_section(self.request, self.request.user, self.course, "handouts")
        # NOTE: check handouts output...right now test course seems to have no such content
        # at least this makes sure get_course_info_section returns without exception

    def test_course_link_rewrite(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)

        assert f'/courses/{str(self.course.id)}/bar/content' in result_fragment.content


class XBlockWithJsonInitData(XBlock):
    """
    Pure XBlock to use in tests, with JSON init data.
    """
    the_json_data = None

    def student_view(self, context=None):       # pylint: disable=unused-argument
        """
        A simple view that returns just enough to test.
        """
        frag = Fragment("Hello there!")
        frag.add_javascript('alert("Hi!");')
        frag.initialize_js('ThumbsBlock', self.the_json_data)
        return frag


@ddt.ddt
class JsonInitDataTest(ModuleStoreTestCase):
    """Tests for JSON data injected into the JS init function."""

    @ddt.data(
        ({'a': 17}, '''{"a": 17}'''),
        ({'xss': '</script>alert("XSS")'}, r'''{"xss": "\u003c/script\u003ealert(\"XSS\")"}'''),
    )
    @ddt.unpack
    @XBlock.register_temp_plugin(XBlockWithJsonInitData, identifier='withjson')
    def test_json_init_data(self, json_data, json_output):
        XBlockWithJsonInitData.the_json_data = json_data
        mock_user = UserFactory()
        mock_request = MagicMock()
        mock_request.user = mock_user
        course = CourseFactory()
        block = BlockFactory(category='withjson', parent=course)
        field_data_cache = FieldDataCache([course, block], course.id, mock_user)
        block = render.get_block_for_descriptor(
            mock_user,
            mock_request,
            block,
            field_data_cache,
            course.id,
            course=course
        )
        html = block.render(STUDENT_VIEW).content
        assert json_output in html
        # No matter what data goes in, there should only be one close-script tag.
        assert html.count('</script>') == 1


@XBlock.tag("detached")
class DetachedXBlock(XBlock):
    """
    XBlock marked with the 'detached' flag.
    """

    def student_view(self, context=None):  # pylint: disable=unused-argument
        """
        A simple view that returns just enough to test.
        """
        frag = Fragment("Hello there!")
        return frag


@patch.dict('django.conf.settings.FEATURES', {'DISPLAY_DEBUG_INFO_TO_STAFF': True, 'DISPLAY_HISTOGRAMS_TO_STAFF': True})
@patch('lms.djangoapps.courseware.block_render.has_access', Mock(return_value=True, autospec=True))
class TestStaffDebugInfo(SharedModuleStoreTestCase):
    """Tests to verify that Staff Debug Info panel and histograms are displayed to staff."""
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.request = RequestFactoryNoCsrf().get('/')
        self.request.user = self.user
        self.request.session = {}

        problem_xml = OptionResponseXMLFactory().build_xml(
            question_text='The correct answer is Correct',
            num_inputs=2,
            weight=2,
            options=['Correct', 'Incorrect'],
            correct_option='Correct'
        )
        self.block = BlockFactory.create(
            category='problem',
            data=problem_xml,
            display_name='Option Response Problem'
        )

        self.location = self.block.location
        self.field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id,
            self.user,
            self.block
        )

    @patch.dict('django.conf.settings.FEATURES', {'DISPLAY_DEBUG_INFO_TO_STAFF': False})
    def test_staff_debug_info_disabled(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)
        assert 'Staff Debug' not in result_fragment.content

    def test_staff_debug_info_enabled(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)
        assert 'Staff Debug' in result_fragment.content

    def test_staff_debug_info_score_for_invalid_dropdown(self):
        """
        Verifies that for an invalid drop down problem, the max score is set
        to zero in the html.
        """
        problem_xml = """
        <problem>
            <optionresponse>
              <p>You can use this template as a guide to the simple editor markdown and OLX markup to use for dropdown problems. Edit this component to replace this template with your own assessment.</p>
            <label>Add the question text, or prompt, here. This text is required.</label>
            <description>You can add an optional tip or note related to the prompt like this. </description>
            <optioninput>
                <option correct="False">an incorrect answer</option>
                <option correct="True">the correct answer</option>
                <option correct="True">an incorrect answer</option>
              </optioninput>
            </optionresponse>
        </problem>
        """
        problem_block = BlockFactory.create(
            category='problem',
            data=problem_xml
        )
        block = render.get_block(
            self.user,
            self.request,
            problem_block.location,
            self.field_data_cache
        )
        html_fragment = block.render(STUDENT_VIEW)
        expected_score_override_html = textwrap.dedent("""<div>
        <label for="sd_fs_{block_id}">Score (for override only):</label>
        <input type="text" tabindex="0" id="sd_fs_{block_id}" placeholder="0"/>
        <label for="sd_fs_{block_id}"> / 0</label>
      </div>""")

        assert expected_score_override_html.format(block_id=problem_block.location.block_id) in\
               html_fragment.content

    @XBlock.register_temp_plugin(DetachedXBlock, identifier='detached-block')
    def test_staff_debug_info_disabled_for_detached_blocks(self):
        """Staff markup should not be present on detached blocks."""

        detached_block = BlockFactory.create(
            category='detached-block',
            display_name='Detached Block'
        )
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id,
            self.user,
            detached_block
        )
        block = render.get_block(
            self.user,
            self.request,
            detached_block.location,
            field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)
        assert 'Staff Debug' not in result_fragment.content

    @patch.dict('django.conf.settings.FEATURES', {'DISPLAY_HISTOGRAMS_TO_STAFF': False})
    def test_histogram_disabled(self):
        block = render.get_block(
            self.user,
            self.request,
            self.location,
            self.field_data_cache,
        )
        result_fragment = block.render(STUDENT_VIEW)
        assert 'histrogram' not in result_fragment.content

    def test_histogram_enabled_for_unscored_xblocks(self):
        """Histograms should not display for xblocks which are not scored."""

        html_block = BlockFactory.create(
            category='html',
            data='Here are some course details.'
        )
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id,
            self.user,
            self.block
        )
        with patch('openedx.core.lib.xblock_utils.grade_histogram') as mock_grade_histogram:
            mock_grade_histogram.return_value = []
            block = render.get_block(
                self.user,
                self.request,
                html_block.location,
                field_data_cache,
            )
            block.render(STUDENT_VIEW)
            assert not mock_grade_histogram.called

    def test_histogram_enabled_for_scored_xblocks(self):
        """Histograms should display for xblocks which are scored."""

        StudentModuleFactory.create(
            course_id=self.course.id,
            module_state_key=self.location,
            student=UserFactory(),
            grade=1,
            max_grade=1,
            state="{}",
        )
        with patch('openedx.core.lib.xblock_utils.grade_histogram') as mock_grade_histogram:
            mock_grade_histogram.return_value = []
            block = render.get_block(
                self.user,
                self.request,
                self.location,
                self.field_data_cache,
            )
            block.render(STUDENT_VIEW)
            assert mock_grade_histogram.called


PER_COURSE_ANONYMIZED_XBLOCKS = (
    LTIBlock,
    VideoBlock,
)
PER_STUDENT_ANONYMIZED_XBLOCKS = [
    AboutBlock,
    CourseInfoBlock,
    HtmlBlock,
    ProblemBlock,
    StaticTabBlock,
]


@ddt.ddt
class TestAnonymousStudentId(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Test that anonymous_student_id is set correctly across a variety of XBlock types
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_key = ToyCourseFactory.create().id
        cls.course = modulestore().get_course(cls.course_key)

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

    @patch('lms.djangoapps.courseware.block_render.has_access', Mock(return_value=True, autospec=True))
    def _get_anonymous_id(self, course_id, xblock_class, should_get_deprecated_id: bool):  # pylint: disable=missing-function-docstring
        location = course_id.make_usage_key('dummy_category', 'dummy_name')
        mixed_class = Mixologist(settings.XBLOCK_MIXINS).mix(xblock_class)
        block = Mock(
            spec=mixed_class,
            _field_data=Mock(spec=FieldData, name='field_data'),
            location=location,
            static_asset_path=None,
            _runtime=Mock(
                spec=ModuleStoreRuntime,
                resources_fs=None,
                mixologist=Mock(_mixins=(), name='mixologist'),
                _services={},
                name='runtime',
            ),
            scope_ids=Mock(spec=ScopeIds),
            name='block',
            _field_data_cache={},
            _dirty_fields={},
            fields={},
            days_early_for_beta=None,
        )
        block.runtime = ModuleStoreRuntime(None, None, None)
        block.bind_for_student = partial(mixed_class.bind_for_student, block)

        if hasattr(xblock_class, 'module_class'):
            block.module_class = xblock_class.module_class

        rendered_block = render.get_block_for_descriptor(
            user=self.user,
            block=block,
            student_data=Mock(spec=FieldData, name='student_data'),
            course_key=course_id,
            track_function=Mock(name='track_function'),  # Track Function
            request_token='request_token',
            course=self.course,
            request=None,
            field_data_cache=None,
        )
        current_user = rendered_block.runtime.service(rendered_block, 'user').get_current_user()

        if should_get_deprecated_id:
            return current_user.opt_attrs.get(ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID)
        return current_user.opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID)

    @ddt.data(*PER_STUDENT_ANONYMIZED_XBLOCKS)
    def test_per_student_anonymized_id(self, block_class):
        for course_id in ('MITx/6.00x/2012_Fall', 'MITx/6.00x/2013_Spring'):
            assert 'de619ab51c7f4e9c7216b4644c24f3b5' == \
                   self._get_anonymous_id(CourseKey.from_string(course_id), block_class, True)

    @ddt.data(*PER_COURSE_ANONYMIZED_XBLOCKS)
    def test_per_course_anonymized_id(self, xblock_class):
        assert '0c706d119cad686d28067412b9178454' == \
               self._get_anonymous_id(CourseKey.from_string('MITx/6.00x/2012_Fall'), xblock_class, False)

        assert 'e9969c28c12c8efa6e987d6dbeedeb0b' == \
               self._get_anonymous_id(CourseKey.from_string('MITx/6.00x/2013_Spring'), xblock_class, False)


@patch('common.djangoapps.track.views.eventtracker', autospec=True)
class TestModuleTrackingContext(SharedModuleStoreTestCase):
    """
    Ensure correct tracking information is included in events emitted during XBlock callback handling.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create()
        self.request = RequestFactoryNoCsrf().get('/')
        self.request.user = self.user
        self.request.session = {}
        self.course = CourseFactory.create()

        self.problem_xml = OptionResponseXMLFactory().build_xml(
            question_text='The correct answer is Correct',
            num_inputs=2,
            weight=2,
            options=['Correct', 'Incorrect'],
            correct_option='Correct'
        )

    def test_context_contains_display_name(self, mock_tracker):
        problem_display_name = 'Option Response Problem'
        block_info = self.handle_callback_and_get_block_info(mock_tracker, problem_display_name)
        assert problem_display_name == block_info['display_name']

    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    @patch('xmodule.modulestore.mongo.base.OldModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    @patch('xmodule.x_module.ModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    def test_context_contains_aside_info(self, mock_tracker):
        """
        Check that related xblock asides populate information in the 'problem_check' event in case
        the 'get_event_context' method is exist
        """
        problem_display_name = 'Test Problem'

        def get_event_context(self, event_type, event):  # pylint: disable=unused-argument
            """
            This method return data that should be associated with the "check_problem" event
            """
            return {'content': 'test1', 'data_field': 'test2'}

        AsideTestType.get_event_context = get_event_context

        # for different operations, there are different number of context calls.
        # We are sending this `call_idx` to get the mock call that we are interested in.
        context_info = self.handle_callback_and_get_context_info(mock_tracker, problem_display_name, call_idx=4)

        assert 'asides' in context_info
        assert 'test_aside' in context_info['asides']
        assert 'content' in context_info['asides']['test_aside']
        assert context_info['asides']['test_aside']['content'] == 'test1'
        assert 'data_field' in context_info['asides']['test_aside']
        assert context_info['asides']['test_aside']['data_field'] == 'test2'

    def handle_callback_and_get_context_info(self,
                                             mock_tracker,
                                             problem_display_name=None,
                                             call_idx=0):
        """
        Creates a fake block, invokes the callback and extracts the 'context'
        metadata from the emitted problem_check event.
        """

        block_kwargs = {
            'category': 'problem',
            'data': self.problem_xml
        }
        if problem_display_name:
            block_kwargs['display_name'] = problem_display_name

        block = BlockFactory.create(**block_kwargs)
        mock_tracker_for_context = MagicMock()
        with patch('lms.djangoapps.courseware.block_render.tracker', mock_tracker_for_context), patch(
            'xmodule.services.tracker', mock_tracker_for_context
        ):
            render.handle_xblock_callback(
                self.request,
                str(self.course.id),
                quote_slashes(str(block.location)),
                'xmodule_handler',
                'problem_check',
            )

            assert len(mock_tracker.emit.mock_calls) == 1
            mock_call = mock_tracker.emit.mock_calls[0]
            event = mock_call[2]

            assert event['name'] == 'problem_check'

            # for different operations, there are different number of context calls.
            # We are sending this `call_idx` to get the mock call that we are interested in.
            context = mock_tracker_for_context.get_tracker.mock_calls[call_idx][1][1]

            return context

    def handle_callback_and_get_block_info(self, mock_tracker, problem_display_name=None):
        """
        Creates a fake block, invokes the callback and extracts the 'block'
        metadata from the emitted problem_check event.
        """
        event = self.handle_callback_and_get_context_info(
            mock_tracker, problem_display_name, call_idx=1
        )
        return event['module']

    def test_missing_display_name(self, mock_tracker):
        actual_display_name = self.handle_callback_and_get_block_info(mock_tracker)['display_name']
        assert actual_display_name.startswith('problem')

    def test_library_source_information(self, mock_tracker):
        """
        Check that XBlocks that are inherited from a library include the
        information about their library block source in events.
        We patch the modulestore to avoid having to create a library.
        """
        original_usage_key = UsageKey.from_string('block-v1:A+B+C+type@problem+block@abcd1234')
        original_usage_version = ObjectId()

        def _mock_get_original_usage(_, __):
            return original_usage_key, original_usage_version

        with patch('xmodule.modulestore.mixed.MixedModuleStore.get_block_original_usage', _mock_get_original_usage):
            block_info = self.handle_callback_and_get_block_info(mock_tracker)
            assert 'original_usage_key' in block_info
            assert block_info['original_usage_key'] == str(original_usage_key)
            assert 'original_usage_version' in block_info
            assert block_info['original_usage_version'] == str(original_usage_version)


class TestXBlockRuntimeEvent(TestSubmittingProblems):
    """
    Inherit from TestSubmittingProblems to get functionality that set up a course and problems structure
    """

    def setUp(self):
        super().setUp()
        self.homework = self.add_graded_section_to_course('homework')
        self.problem = self.add_dropdown_to_section(self.homework.location, 'p1', 1)
        self.grade_dict = {'value': 0.18, 'max_value': 32}
        self.delete_dict = {'value': None, 'max_value': None}

    def get_block_for_user(self, user):
        """Helper function to get useful block at self.location in self.course_id for user"""
        mock_request = MagicMock()
        mock_request.user = user
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id, user, self.course, depth=2)

        return render.get_block(
            user,
            mock_request,
            self.problem.location,
            field_data_cache,
        )

    def set_block_grade_using_publish(self, grade_dict):
        """Publish the user's grade, takes grade_dict as input"""
        block = self.get_block_for_user(self.student_user)
        block.runtime.publish(block, 'grade', grade_dict)
        return block

    def test_xblock_runtime_publish(self):
        """Tests the publish mechanism"""
        self.set_block_grade_using_publish(self.grade_dict)
        student_module = StudentModule.objects.get(student=self.student_user, module_state_key=self.problem.location)
        assert student_module.grade == self.grade_dict['value']
        assert student_module.max_grade == self.grade_dict['max_value']

    def test_xblock_runtime_publish_delete(self):
        """Test deleting the grade using the publish mechanism"""
        block = self.set_block_grade_using_publish(self.grade_dict)
        block.runtime.publish(block, 'grade', self.delete_dict)
        student_module = StudentModule.objects.get(student=self.student_user, module_state_key=self.problem.location)
        assert student_module.grade is None
        assert student_module.max_grade is None

    @patch('lms.djangoapps.grades.signals.handlers.PROBLEM_RAW_SCORE_CHANGED.send')
    def test_score_change_signal(self, send_mock):
        """Test that a Django signal is generated when a score changes"""
        with freeze_time(datetime.now().replace(tzinfo=pytz.UTC)):
            self.set_block_grade_using_publish(self.grade_dict)
            expected_signal_kwargs = {
                'sender': None,
                'raw_possible': self.grade_dict['max_value'],
                'raw_earned': self.grade_dict['value'],
                'weight': None,
                'user_id': self.student_user.id,
                'course_id': str(self.course.id),
                'usage_id': str(self.problem.location),
                'only_if_higher': None,
                'modified': datetime.now().replace(tzinfo=pytz.UTC),
                'score_db_table': 'csm',
                'score_deleted': None,
                'grader_response': None
            }
            send_mock.assert_called_with(**expected_signal_kwargs)


class TestRebindBlock(TestSubmittingProblems):
    """
    Tests to verify the functionality of rebinding a block.
    Inherit from TestSubmittingProblems to get functionality that set up a course structure
    """

    def setUp(self):
        super().setUp()
        self.homework = self.add_graded_section_to_course('homework')
        self.lti = BlockFactory.create(category='lti', parent=self.homework)
        self.problem = BlockFactory.create(category='problem', parent=self.homework)
        self.user = UserFactory.create()
        self.anon_user = AnonymousUser()

    def get_block_for_user(self, user, item=None):
        """Helper function to get useful block at self.location in self.course_id for user"""
        mock_request = MagicMock()
        mock_request.user = user
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id, user, self.course, depth=2)

        if item is None:
            item = self.lti

        return render.get_block(
            user,
            mock_request,
            item.location,
            field_data_cache,
        )

    def test_rebind_block_to_new_users(self):
        block = self.get_block_for_user(self.user, self.problem)

        # Bind the block to another student, which will remove "correct_map"
        # from the block's _field_data_cache and _dirty_fields.
        user2 = UserFactory.create()
        block.bind_for_student(user2.id)

        # XBlock's save method assumes that if a field is in _dirty_fields,
        # then it's also in _field_data_cache. If this assumption
        # doesn't hold, then we get an error trying to bind this block
        # to a third student, since we've removed "correct_map" from
        # _field_data cache, but not _dirty_fields, when we bound
        # this block to the second student. (TNL-2640)
        user3 = UserFactory.create()
        block.bind_for_student(user3.id)

    def test_rebind_noauth_block_to_user_not_anonymous(self):
        """
        Tests that an exception is thrown when rebind_noauth_block_to_user is run from a
        block bound to a real user
        """
        block = self.get_block_for_user(self.user)
        user2 = UserFactory()
        user2.id = 2
        with self.assertRaisesRegex(  # noqa: PT027
            RebindUserServiceError,
            "rebind_noauth_module_to_user can only be called from a module bound to an anonymous user"
        ):
            assert block.runtime.service(block, 'rebind_user').rebind_noauth_module_to_user(block, user2)

    def test_rebind_noauth_block_to_user_anonymous(self):
        """
        Tests that get_user_block_for_noauth succeeds when rebind_noauth_block_to_user is run from a
        block bound to AnonymousUser
        """
        block = self.get_block_for_user(self.anon_user)
        user2 = UserFactory()
        user2.id = 2
        block.runtime.service(block, 'rebind_user').rebind_noauth_module_to_user(block, user2)
        assert block
        block_user_info = block.runtime.service(block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID) == anonymous_id_for_user(user2, self.course.id)
        assert block.scope_ids.user_id == user2.id
        assert block.scope_ids.user_id == user2.id


@ddt.ddt
class TestEventPublishing(ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Tests of event publishing for both XModules and XBlocks.
    """

    def setUp(self):
        """
        Set up the course and user context
        """
        super().setUp()

        self.mock_user = UserFactory()
        self.mock_user.id = 1
        self.request_factory = RequestFactoryNoCsrf()

    @XBlock.register_temp_plugin(PureXBlock, identifier='xblock')
    @patch.object(render, 'make_track_function')
    def test_event_publishing(self, mock_track_function):
        request = self.request_factory.get('')
        request.user = self.mock_user
        course = CourseFactory()
        block = BlockFactory(category='xblock', parent=course)
        field_data_cache = FieldDataCache([course, block], course.id, self.mock_user)
        block = render.get_block(self.mock_user, request, block.location, field_data_cache)

        event_type = 'event_type'
        event = {'event': 'data'}

        block.runtime.publish(block, event_type, event)

        mock_track_function.assert_called_once_with(request)

        mock_track_function.return_value.assert_called_once_with(event_type, event)


class LMSXBlockServiceMixin(SharedModuleStoreTestCase):
    """
    Helper class that initializes the runtime.
    """
    def _prepare_runtime(self):
        """
        Instantiate the runtem.
        """
        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course
        )

    @XBlock.register_temp_plugin(PureXBlock, identifier='pure')
    def setUp(self):
        """
        Set up the user and other fields that will be used to instantiate the runtime.
        """
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory()
        self.student_data = Mock()
        self.track_function = Mock()
        self.request_token = Mock()
        self.block = BlockFactory(category="pure", parent=self.course)
        self._prepare_runtime()


@ddt.ddt
class LMSXBlockServiceBindingTest(LMSXBlockServiceMixin):
    """
    Tests that the LMS Module System (XBlock Runtime) provides an expected set of services.
    """

    @ddt.data(
        'fs',
        'field-data',
        'mako',
        'user',
        'verification',
        'proctoring',
        'milestones',
        'credit',
        'bookmarks',
        'gating',
        'grade_utils',
        'user_state',
        'content_type_gating',
        'cache',
        'sandbox',
        'replace_urls',
        'rebind_user',
        'completion',
        'i18n',
        'library_tools',
        'partitions',
        'settings',
        'user_tags',
        'teams',
        'teams_configuration',
        'call_to_action',
    )
    def test_expected_services_exist(self, expected_service):
        """
        Tests that the 'user', 'i18n', and 'fs' services are provided by the LMS runtime.
        """
        service = self.block.runtime.service(self.block, expected_service)
        assert service is not None

    def test_get_set_tag(self):
        """
        Tests the user service interface.
        """
        scope = 'course'
        key = 'key1'

        # test for when we haven't set the tag yet
        tag = self.block.runtime.service(self.block, 'user_tags').get_tag(scope, key)
        assert tag is None

        # set the tag
        set_value = 'value'
        self.block.runtime.service(self.block, 'user_tags').set_tag(scope, key, set_value)
        tag = self.block.runtime.service(self.block, 'user_tags').get_tag(scope, key)

        assert tag == set_value

        # Try to set tag in wrong scope
        with pytest.raises(ValueError):  # noqa: PT011
            self.block.runtime.service(self.block, 'user_tags').set_tag('fake_scope', key, set_value)

        # Try to get tag in wrong scope
        with pytest.raises(ValueError):  # noqa: PT011
            self.block.runtime.service(self.block, 'user_tags').get_tag('fake_scope', key)


class TestI18nService(LMSXBlockServiceMixin):
    """ Test XBlockI18nService """

    def test_module_i18n_lms_service(self):
        """
        Test: module i18n service in LMS
        """
        i18n_service = self.block.runtime.service(self.block, 'i18n')
        assert i18n_service is not None
        assert isinstance(i18n_service, XBlockI18nService)

    def test_no_service_exception_with_none_declaration_(self):
        """
        Test: NoSuchServiceError should be raised block declaration returns none
        """
        self.block.service_declaration = Mock(return_value=None)
        with pytest.raises(NoSuchServiceError):
            self.block.runtime.service(self.block, 'i18n')

    def test_no_service_exception_(self):
        """
        Test: NoSuchServiceError should be raised if i18n service is none.
        """
        i18nService = self.block.runtime._services['i18n']  # pylint: disable=protected-access
        self.block.runtime._services['i18n'] = None  # pylint: disable=protected-access
        with pytest.raises(NoSuchServiceError):
            self.block.runtime.service(self.block, 'i18n')
        self.block.runtime._services['i18n'] = i18nService  # pylint: disable=protected-access

    def test_i18n_service_callable(self):
        """
        Test: _services dict should contain the callable i18n service in LMS.
        """
        assert callable(self.block.runtime._services.get('i18n'))  # pylint: disable=protected-access

    def test_i18n_service_not_callable(self):
        """
        Test: i18n service should not be callable in LMS after initialization.
        """
        assert not callable(self.block.runtime.service(self.block, 'i18n'))


class PureXBlockWithChildren(PureXBlock):
    """
    Pure XBlock with children to use in tests.
    """
    has_children = True


USER_NUMBERS = list(range(2))


@ddt.ddt
class TestFilteredChildren(SharedModuleStoreTestCase):
    """
    Tests that verify access to XBlock/XModule children work correctly
    even when those children are filtered by the runtime when loaded.
    """
    # pylint: disable=attribute-defined-outside-init
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.users = {number: UserFactory() for number in USER_NUMBERS}

        self._old_has_access = render.has_access
        patcher = patch('lms.djangoapps.courseware.block_render.has_access', self._has_access)
        patcher.start()
        self.addCleanup(patcher.stop)

    @XBlock.register_temp_plugin(PureXBlockWithChildren, identifier='xblock')
    def test_unbound(self):
        block = self._load_block()
        self.assertUnboundChildren(block)

    @ddt.data(*USER_NUMBERS)
    @XBlock.register_temp_plugin(PureXBlockWithChildren, identifier='xblock')
    def test_unbound_then_bound_as_xblock(self, user_number):
        user = self.users[user_number]
        block = self._load_block()
        self.assertUnboundChildren(block)
        self._bind_block(block, user)
        self.assertBoundChildren(block, user)

    @ddt.data(*USER_NUMBERS)
    @XBlock.register_temp_plugin(PureXBlockWithChildren, identifier='xblock')
    def test_bound_only_as_xblock(self, user_number):
        user = self.users[user_number]
        block = self._load_block()
        self._bind_block(block, user)
        self.assertBoundChildren(block, user)

    def _load_block(self):
        """
        Instantiate an XBlock with the appropriate set of children.
        """
        self.parent = BlockFactory(category='xblock', parent=self.course)

        # Create a child for each user
        self.children_for_user = {
            user: BlockFactory(category='xblock', parent=self.parent).scope_ids.usage_id  # pylint: disable=no-member
            for user in self.users.values()
        }

        self.all_children = self.children_for_user.values()

        return modulestore().get_item(self.parent.scope_ids.usage_id)  # pylint: disable=no-member

    def _bind_block(self, block, user):
        """
        Bind `block` to the supplied `user`.
        """
        course_id = self.course.id
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            course_id,
            user,
            block,
        )
        return get_block_for_descriptor(
            user,
            Mock(name='request', user=user),
            block,
            field_data_cache,
            course_id,
            course=self.course
        )

    def _has_access(self, user, action, obj, course_key=None):
        """
        Mock implementation of `has_access` used to control which blocks
        have access to which children during tests.
        """
        if action != 'load':
            return self._old_has_access(user, action, obj, course_key)

        if isinstance(obj, XBlock):
            key = obj.scope_ids.usage_id
        elif isinstance(obj, UsageKey):
            key = obj
        if key == self.parent.scope_ids.usage_id:  # pylint: disable=no-member
            return AccessResponse(True)
        return AccessResponse(key == self.children_for_user[user])

    def assertBoundChildren(self, block, user):
        """
        Ensure the bound children are indeed children.
        """
        self.assertChildren(block, [self.children_for_user[user]])

    def assertUnboundChildren(self, block):
        """
        Ensure unbound children are indeed children.
        """
        self.assertChildren(block, self.all_children)

    def assertChildren(self, block, child_usage_ids):
        """
        Used to assert that sets of children are equivalent.
        """
        assert set(child_usage_ids) == {child.scope_ids.usage_id for child in block.get_children()}


@ddt.ddt
class TestDisabledXBlockTypes(ModuleStoreTestCase):
    """
    Tests that verify disabled XBlock types are not loaded.
    """

    def setUp(self):
        super().setUp()
        XBlockConfiguration(name='video', enabled=False).save()

    def test_get_item(self):
        course = CourseFactory()
        self._verify_block('video', course, 'HiddenBlockWithMixins')

    def test_dynamic_updates(self):
        """Tests that the list of disabled xblocks can dynamically update."""
        course = CourseFactory()
        item_usage_id = self._verify_block('problem', course, 'ProblemBlockWithMixins')
        XBlockConfiguration(name='problem', enabled=False).save()

        # First verify that the cached value is used until there is a new request cache.
        self._verify_block('problem', course, 'ProblemBlockWithMixins', item_usage_id)

        # Now simulate a new request cache.
        self.store.request_cache.data.clear()
        self._verify_block('problem', course, 'HiddenBlockWithMixins', item_usage_id)

    def _verify_block(self, category, course, block, item_id=None):
        """
        Helper method that gets an item with the specified category from the
        modulestore and verifies that it has the expected block name.

        Returns the item's usage_id.
        """
        if not item_id:
            item = BlockFactory(category=category, parent=course)
            item_id = item.scope_ids.usage_id  # pylint: disable=no-member

        item = self.store.get_item(item_id)
        assert item.__class__.__name__ == block
        return item_id


@ddt.ddt
class LmsModuleSystemShimTest(SharedModuleStoreTestCase):
    """
    Tests that the deprecated attributes in the LMS Module System (XBlock Runtime) return the expected values.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    COURSE_ID = 'course-v1:edX+LmsModuleShimTest+2021_Fall'
    PYTHON_LIB_FILENAME = 'test_python_lib.zip'
    PYTHON_LIB_SOURCE_FILE = './common/test/data/uploads/python_lib.zip'

    @classmethod
    def setUpClass(cls):
        """
        Set up the course and block used to instantiate the runtime.
        """
        super().setUpClass()
        org = 'edX'
        number = 'LmsModuleShimTest'
        run = '2021_Fall'
        cls.course = CourseFactory.create(org=org, number=number, run=run)
        cls.block = BlockFactory(category="vertical", parent=cls.course)
        cls.problem_block = BlockFactory(category="problem", parent=cls.course)

    def setUp(self):
        """
        Set up the user and other fields that will be used to instantiate the runtime.
        """
        super().setUp()
        self.user = UserFactory(id=232)
        self.student_data = Mock()
        self.track_function = Mock()
        self.request_token = Mock()
        self.contentstore = contentstore()
        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )

    @ddt.data(
        ('seed', 232),
        ('user_id', 232),
        ('user_is_staff', False),
    )
    @ddt.unpack
    def test_user_service_attributes(self, attribute, expected_value):
        """
        Tests that the deprecated attributes provided by the user service match expected values.
        """
        assert getattr(self.block.runtime, attribute) == expected_value

    @ddt.data((True, 'staff'), (False, 'student'))
    @ddt.unpack
    def test_user_is_staff(self, is_staff, expected_role):
        if is_staff:
            self.user = StaffFactory(course_key=self.course.id)

        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        block_user_info = self.block.runtime.service(self.block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_USER_IS_STAFF) == is_staff
        assert block_user_info.opt_attrs.get(ATTR_KEY_USER_ROLE) == expected_role
        with warnings.catch_warnings():  # For now, also test the deprecated accessors for backwards compatibility:
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert self.block.runtime.user_is_staff == is_staff
            assert self.block.runtime.get_user_role() == expected_role

    @ddt.data(True, False)
    def test_user_is_admin(self, is_global_staff):
        if is_global_staff:
            self.user = GlobalStaffFactory.create()

        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        block_user_info = self.block.runtime.service(self.block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_USER_IS_GLOBAL_STAFF) == is_global_staff
        with warnings.catch_warnings():  # For now, also test the deprecated accessors for backwards compatibility:
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert self.block.runtime.user_is_admin == is_global_staff

    @ddt.data(True, False)
    def test_user_is_beta_tester(self, is_beta_tester):
        if is_beta_tester:
            self.user = BetaTesterFactory(course_key=self.course.id)

        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        block_user_info = self.block.runtime.service(self.block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_USER_IS_BETA_TESTER) == is_beta_tester
        with warnings.catch_warnings():  # For now, also test the deprecated accessors for backwards compatibility:
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert self.block.runtime.user_is_beta_tester == is_beta_tester

    @ddt.data((True, 'instructor'), (False, 'student'))
    @ddt.unpack
    def test_get_user_role(self, is_instructor, expected_role):
        if is_instructor:
            self.user = InstructorFactory(course_key=self.course.id)

        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        block_user_info = self.block.runtime.service(self.block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_USER_ROLE) == expected_role
        with warnings.catch_warnings():  # For now, also test the deprecated accessor for backwards compatibility:
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert self.block.runtime.get_user_role() == expected_role

    def test_anonymous_student_id(self):
        expected_anon_id = anonymous_id_for_user(self.user, self.course.id)
        block_user_info = self.block.runtime.service(self.block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID) == expected_anon_id
        with warnings.catch_warnings():  # For now, also test the deprecated accessor for backwards compatibility:
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert self.block.runtime.anonymous_student_id == expected_anon_id

    def test_anonymous_student_id_bug(self):
        """
        Verifies that subsequent calls to prepare_runtime_for_user have no effect on each block runtime's
        anonymous_student_id value.
        """

        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.problem_block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        # Ensure the problem block returns a per-user anonymous id
        assert self.problem_block.runtime.service(self.problem_block, 'user').get_current_user().opt_attrs.get(
            ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID
        ) == anonymous_id_for_user(self.user, None)

        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        # Ensure the vertical block returns a per-course+user anonymous id
        assert self.block.runtime.service(self.block, 'user').get_current_user().opt_attrs.get(
            ATTR_KEY_ANONYMOUS_USER_ID
        ) == anonymous_id_for_user(self.user, self.course.id)

        # Ensure the problem runtime's anonymous student ID is unchanged after the above call.
        assert self.problem_block.runtime.service(self.problem_block, 'user').get_current_user().opt_attrs.get(
            ATTR_KEY_DEPRECATED_ANONYMOUS_USER_ID
        ) == anonymous_id_for_user(self.user, None)

    def test_user_service_with_anonymous_user(self):
        render.prepare_runtime_for_user(
            AnonymousUser(),
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        block_user_info = self.block.runtime.service(self.block, "user").get_current_user()
        assert block_user_info.opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID) is None
        assert self.block.scope_ids.user_id is None
        assert not block_user_info.opt_attrs.get(ATTR_KEY_USER_IS_STAFF)
        assert not block_user_info.opt_attrs.get(ATTR_KEY_USER_ROLE)
        # Also test the deprecated accessors for backwards compatibility:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert self.block.runtime.anonymous_student_id is None
            assert self.block.runtime.seed == 0
            assert self.block.runtime.user_id is None
            assert not self.block.runtime.user_is_staff
            assert not self.block.runtime.get_user_role()

    def test_get_real_user(self):
        """
        Test the deprecated runtime.get_real_user() method, to ensure backwards compatibility.

        Newer code should use the user service, which gets tested in test_user_service.py
        """
        render.prepare_runtime_for_user(
            self.user,
            self.student_data,
            self.block.runtime,
            self.course.id,
            self.track_function,
            self.request_token,
            course=self.course,
        )
        course_anonymous_student_id = anonymous_id_for_user(self.user, self.course.id)
        no_course_anonymous_student_id = anonymous_id_for_user(self.user, None)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            # pylint: disable=not-callable
            assert self.block.runtime.get_real_user(course_anonymous_student_id) == self.user
            assert self.block.runtime.get_real_user(no_course_anonymous_student_id) == self.user
            # Tests that the default is to use the user service's anonymous_student_id
            assert self.block.runtime.get_real_user() == self.user

    def test_render_template(self):
        rendered = self.block.runtime.render_template('templates/edxmako.html', {'element_id': 'hi'})  # pylint: disable=not-callable
        assert rendered == '<div id="hi" ns="main">Testing the MakoService</div>\n'

    @override_settings(COURSES_WITH_UNSAFE_CODE=[r'course-v1:edX\+LmsModuleShimTest\+2021_Fall'])
    def test_can_execute_unsafe_code_when_allowed(self):
        assert self.block.runtime.can_execute_unsafe_code()

    @override_settings(COURSES_WITH_UNSAFE_CODE=[r'course-v1:edX\+full\+2021_Fall'])
    def test_cannot_execute_unsafe_code_when_disallowed(self):
        assert not self.block.runtime.can_execute_unsafe_code()

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
        zipfile = upload_file_to_course(  # noqa: F841
            course_key=self.course.id,
            contentstore=self.contentstore,
            source_file=self.PYTHON_LIB_SOURCE_FILE,
            target_filename=self.PYTHON_LIB_FILENAME,
        )
        assert self.block.runtime.get_python_lib_zip() is None

    def test_cache(self):
        assert hasattr(self.block.runtime.cache, 'get')
        assert hasattr(self.block.runtime.cache, 'set')

    @XBlock.register_temp_plugin(PureXBlock, 'pure')
    @XBlock.register_temp_plugin(PureXBlockWithChildren, identifier='xblock')
    def test_course_id(self):
        block = BlockFactory(category="pure", parent=self.course)

        rendered_block = render.get_block(self.user, Mock(), block.location, None)
        assert str(rendered_block.scope_ids.usage_id.context_key) == self.COURSE_ID
        with warnings.catch_warnings():  # For now, also test the deprecated accessor for backwards compatibility:
            warnings.simplefilter("ignore", category=DeprecationWarning)
            assert str(rendered_block.runtime.course_id) == self.COURSE_ID
