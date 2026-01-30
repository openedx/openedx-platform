"""Test for LTI Xmodule functional logic."""


import datetime
import textwrap
from copy import copy
from unittest.mock import Mock, PropertyMock, patch
from urllib import parse
from zoneinfo import ZoneInfo


import pytest
from django.conf import settings
from django.test import TestCase, override_settings
from lxml import etree
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator
from webob.request import Request
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds, Timedelta


from common.djangoapps.xblock_django.constants import ATTR_KEY_ANONYMOUS_USER_ID
from xmodule import lti_block
from xmodule.tests.helpers import StubUserService

from . import get_test_system

from xmodule.lti_2_util import LTIError as BuiltInLTIError
from xblocks_contrib.lti.lti_2_util import LTIError as ExtractedLTIError


@override_settings(LMS_BASE="edx.org")
class _TestLTIBase(TestCase):
    """Logic tests for LTI block."""

    __test__ = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lti_class = lti_block.reset_class()
        if settings.USE_EXTRACTED_LTI_BLOCK:
            cls.LTIError = ExtractedLTIError
        else:
            cls.LTIError = BuiltInLTIError

    def setUp(self):
        super().setUp()
        self.environ = {'wsgi.url_scheme': 'http', 'REQUEST_METHOD': 'POST'}
        self.request_body_xml_template = textwrap.dedent("""
            <?xml version = "1.0" encoding = "UTF-8"?>
                <imsx_POXEnvelopeRequest xmlns = "{namespace}">
                  <imsx_POXHeader>
                    <imsx_POXRequestHeaderInfo>
                      <imsx_version>V1.0</imsx_version>
                      <imsx_messageIdentifier>{messageIdentifier}</imsx_messageIdentifier>
                    </imsx_POXRequestHeaderInfo>
                  </imsx_POXHeader>
                  <imsx_POXBody>
                    <{action}>
                      <resultRecord>
                        <sourcedGUID>
                          <sourcedId>{sourcedId}</sourcedId>
                        </sourcedGUID>
                        <result>
                          <resultScore>
                            <language>en-us</language>
                            <textString>{grade}</textString>
                          </resultScore>
                        </result>
                      </resultRecord>
                    </{action}>
                  </imsx_POXBody>
                </imsx_POXEnvelopeRequest>
            """)
        self.course_id = CourseKey.from_string('org/course/run')
        self.runtime = get_test_system(self.course_id)
        self.runtime.publish = Mock()
        self.runtime._services['rebind_user'] = Mock()  # pylint: disable=protected-access

        self.xblock = self.lti_class(
            self.runtime,
            DictFieldData({}),
            ScopeIds(None, None, None, BlockUsageLocator(self.course_id, 'lti', 'name'))
        )
        current_user = self.runtime.service(self.xblock, 'user').get_current_user()
        self.user_id = current_user.opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID)
        self.lti_id = self.xblock.lti_id

        self.unquoted_resource_link_id = '{}-i4x-2-3-lti-31de800015cf4afb973356dbe81496df'.format(
            settings.LMS_BASE
        )

        sourced_id = ':'.join(parse.quote(i) for i in (self.lti_id, self.unquoted_resource_link_id, self.user_id))  # lint-amnesty, pylint: disable=line-too-long

        self.defaults = {
            'namespace': "http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0",
            'sourcedId': sourced_id,
            'action': 'replaceResultRequest',
            'grade': 0.5,
            'messageIdentifier': '528243ba5241b',
        }

        self.xblock.due = None
        self.xblock.graceperiod = None

    def get_request_body(self, params=None):
        """Fetches the body of a request specified by params"""
        if params is None:
            params = {}
        data = copy(self.defaults)

        data.update(params)
        return self.request_body_xml_template.format(**data).encode('utf-8')

    def get_response_values(self, response):
        """Gets the values from the given response"""
        parser = etree.XMLParser(ns_clean=True, recover=True, encoding='utf-8')
        root = etree.fromstring(response.body.strip(), parser=parser)
        lti_spec_namespace = "http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0"
        namespaces = {'def': lti_spec_namespace}

        code_major = root.xpath("//def:imsx_codeMajor", namespaces=namespaces)[0].text
        description = root.xpath("//def:imsx_description", namespaces=namespaces)[0].text
        message_identifier = root.xpath("//def:imsx_messageIdentifier", namespaces=namespaces)[0].text
        imsx_pox_body = root.xpath("//def:imsx_POXBody", namespaces=namespaces)[0]

        try:
            action = imsx_pox_body.getchildren()[0].tag.replace('{' + lti_spec_namespace + '}', '')
        except Exception:  # pylint: disable=broad-except
            action = None

        return {
            'code_major': code_major,
            'description': description,
            'messageIdentifier': message_identifier,
            'action': action
        }

    @patch(
        'xmodule.lti_block.LTIBlock.get_client_key_secret',
        return_value=('test_client_key', 'test_client_secret')
    )
    def test_authorization_header_not_present(self, _get_key_secret):
        """
        Request has no Authorization header.

        This is an unknown service request, i.e., it is not a part of the original service specification.
        """
        request = Request(self.environ)
        request.body = self.get_request_body()
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        expected_response = {
            'action': None,
            'code_major': 'failure',
            'description': 'OAuth verification error: Malformed authorization header',
            'messageIdentifier': self.defaults['messageIdentifier'],
        }

        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)

    @patch(
        'xmodule.lti_block.LTIBlock.get_client_key_secret',
        return_value=('test_client_key', 'test_client_secret')
    )
    def test_authorization_header_empty(self, _get_key_secret):
        """
        Request Authorization header has no value.

        This is an unknown service request, i.e., it is not a part of the original service specification.
        """
        request = Request(self.environ)
        request.authorization = "bad authorization header"
        request.body = self.get_request_body()
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        expected_response = {
            'action': None,
            'code_major': 'failure',
            'description': 'OAuth verification error: Malformed authorization header',
            'messageIdentifier': self.defaults['messageIdentifier'],
        }
        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)

    def test_real_user_is_none(self):
        """
        If we have no real user, we should send back failure response.
        """
        self.runtime._services['user'] = StubUserService(user=None)  # pylint: disable=protected-access
        self.xblock.verify_oauth_body_sign = Mock()
        self.xblock.has_score = True
        request = Request(self.environ)
        request.body = self.get_request_body()
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        expected_response = {
            'action': None,
            'code_major': 'failure',
            'description': 'User not found.',
            'messageIdentifier': self.defaults['messageIdentifier'],
        }
        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)

    def test_grade_past_due(self):
        """
        Should fail if we do not accept past due grades, and it is past due.
        """
        self.xblock.accept_grades_past_due = False
        self.xblock.due = datetime.datetime.now(ZoneInfo("UTC"))
        self.xblock.graceperiod = Timedelta().from_json("0 seconds")
        request = Request(self.environ)
        request.body = self.get_request_body()
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        expected_response = {
            'action': None,
            'code_major': 'failure',
            'description': 'Grade is past due',
            'messageIdentifier': 'unknown',
        }
        assert response.status_code == 200
        assert expected_response == real_response

    def test_grade_not_in_range(self):
        """
        Grade returned from Tool Provider is outside the range 0.0-1.0.
        """
        self.xblock.verify_oauth_body_sign = Mock()
        request = Request(self.environ)
        request.body = self.get_request_body(params={'grade': '10'})
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        expected_response = {
            'action': None,
            'code_major': 'failure',
            'description': 'Request body XML parsing error: score value outside the permitted range of 0-1.',
            'messageIdentifier': 'unknown',
        }
        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)

    def test_bad_grade_decimal(self):
        """
        Grade returned from Tool Provider doesn't use a period as the decimal point.
        """
        self.xblock.verify_oauth_body_sign = Mock()
        request = Request(self.environ)
        request.body = self.get_request_body(params={'grade': '0,5'})
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        msg = "could not convert string to float: '0,5'"
        expected_response = {
            'action': None,
            'code_major': 'failure',
            'description': f'Request body XML parsing error: {msg}',
            'messageIdentifier': 'unknown',
        }
        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)

    def test_unsupported_action(self):
        """
        Action returned from Tool Provider isn't supported.
        `replaceResultRequest` is supported only.
        """
        self.xblock.verify_oauth_body_sign = Mock()
        request = Request(self.environ)
        request.body = self.get_request_body({'action': 'wrongAction'})
        response = self.xblock.grade_handler(request, '')
        real_response = self.get_response_values(response)
        expected_response = {
            'action': None,
            'code_major': 'unsupported',
            'description': 'Target does not support the requested operation.',
            'messageIdentifier': self.defaults['messageIdentifier'],
        }
        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)

    def test_good_request(self):
        """
        Response from Tool Provider is correct.
        """
        self.xblock.verify_oauth_body_sign = Mock()
        self.xblock.has_score = True
        request = Request(self.environ)
        request.body = self.get_request_body()
        response = self.xblock.grade_handler(request, '')
        description_expected = 'Score for {sourcedId} is now {score}'.format(
            sourcedId=self.defaults['sourcedId'],
            score=self.defaults['grade'],
        )
        real_response = self.get_response_values(response)
        expected_response = {
            'action': 'replaceResultResponse',
            'code_major': 'success',
            'description': description_expected,
            'messageIdentifier': self.defaults['messageIdentifier'],
        }

        assert response.status_code == 200
        self.assertDictEqual(expected_response, real_response)
        assert self.xblock.module_score == float(self.defaults['grade'])

    def test_user_id(self):
        expected_user_id = str(parse.quote(self.xblock.runtime.anonymous_student_id))
        real_user_id = self.xblock.get_user_id()
        assert real_user_id == expected_user_id

    def test_outcome_service_url(self):
        mock_url_prefix = 'https://hostname/'
        test_service_name = "test_service"

        def mock_handler_url(block, handler_name, **kwargs):  # pylint: disable=unused-argument
            """Mock function for returning fully-qualified handler urls"""
            return mock_url_prefix + handler_name

        self.xblock.runtime.handler_url = Mock(side_effect=mock_handler_url)
        real_outcome_service_url = self.xblock.get_outcome_service_url(service_name=test_service_name)
        assert real_outcome_service_url == (mock_url_prefix + test_service_name)

    def test_resource_link_id(self):
        with patch('xmodule.lti_block.LTIBlock.location', new_callable=PropertyMock):
            self.xblock.location.html_id = lambda: 'i4x-2-3-lti-31de800015cf4afb973356dbe81496df'
            expected_resource_link_id = str(parse.quote(self.unquoted_resource_link_id))
            real_resource_link_id = self.xblock.get_resource_link_id()
            assert real_resource_link_id == expected_resource_link_id

    def test_lis_result_sourcedid(self):
        expected_sourced_id = ':'.join(parse.quote(i) for i in (
            str(self.course_id),
            self.xblock.get_resource_link_id(),
            self.user_id
        ))
        real_lis_result_sourcedid = self.xblock.get_lis_result_sourcedid()
        assert real_lis_result_sourcedid == expected_sourced_id

    def test_client_key_secret(self):
        """
        LTI block gets client key and secret provided.
        """
        #this adds lti passports to system
        mocked_course = Mock(lti_passports=['lti_id:test_client:test_secret'])
        modulestore = Mock()
        modulestore.get_course.return_value = mocked_course
        runtime = Mock(modulestore=modulestore)
        self.xblock.runtime = runtime
        self.xblock.lti_id = "lti_id"
        key, secret = self.xblock.get_client_key_secret()
        expected = ('test_client', 'test_secret')
        assert expected == (key, secret)

    def test_client_key_secret_not_provided(self):
        """
        LTI block attempts to get client key and secret provided in cms.

        There are key and secret but not for specific LTI.
        """

        # this adds lti passports to system
        mocked_course = Mock(lti_passports=['test_id:test_client:test_secret'])
        modulestore = Mock()
        modulestore.get_course.return_value = mocked_course
        runtime = Mock(modulestore=modulestore)
        self.xblock.runtime = runtime
        # set another lti_id
        self.xblock.lti_id = "another_lti_id"
        key_secret = self.xblock.get_client_key_secret()
        expected = ('', '')
        assert expected == key_secret

    def test_bad_client_key_secret(self):
        """
        LTI block attempts to get client key and secret provided in cms.

        There are key and secret provided in wrong format.
        """
        # this adds lti passports to system
        mocked_course = Mock(lti_passports=['test_id_test_client_test_secret'])
        modulestore = Mock()
        modulestore.get_course.return_value = mocked_course
        runtime = Mock(modulestore=modulestore)
        self.xblock.runtime = runtime
        self.xblock.lti_id = 'lti_id'
        with pytest.raises(self.LTIError):
            self.xblock.get_client_key_secret()

    @patch('xmodule.lti_block.signature.verify_hmac_sha1', Mock(return_value=True))
    @patch(
        'xmodule.lti_block.LTIBlock.get_client_key_secret',
        Mock(return_value=('test_client_key', 'test_client_secret'))
    )
    def test_successful_verify_oauth_body_sign(self):
        """
        Test if OAuth signing was successful.
        """
        self.xblock.verify_oauth_body_sign(self.get_signed_grade_mock_request())

    @patch('xmodule.lti_block.LTIBlock.get_outcome_service_url', Mock(return_value='https://testurl/'))
    @patch('xmodule.lti_block.LTIBlock.get_client_key_secret',
           Mock(return_value=('__consumer_key__', '__lti_secret__')))
    def test_failed_verify_oauth_body_sign_proxy_mangle_url(self):
        """
        Oauth signing verify fail.
        """
        request = self.get_signed_grade_mock_request_with_correct_signature()
        self.xblock.verify_oauth_body_sign(request)
        # we should verify against get_outcome_service_url not
        # request url proxy and load balancer along the way may
        # change url presented to the method
        request.url = 'http://testurl/'
        self.xblock.verify_oauth_body_sign(request)

    def get_signed_grade_mock_request_with_correct_signature(self):
        """
        Generate a proper LTI request object
        """
        mock_request = Mock()
        mock_request.headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': (
                'OAuth realm="https://testurl/", oauth_body_hash="wwzA3s8gScKD1VpJ7jMt9b%2BMj9Q%3D",'
                'oauth_nonce="18821463", oauth_timestamp="1409321145", '
                'oauth_consumer_key="__consumer_key__", oauth_signature_method="HMAC-SHA1", '
                'oauth_version="1.0", oauth_signature="fHsE1hhIz76/msUoMR3Lyb7Aou4%3D"'
            )
        }
        mock_request.url = 'https://testurl'
        mock_request.http_method = 'POST'
        mock_request.method = mock_request.http_method

        mock_request.body = (
            b'<?xml version=\'1.0\' encoding=\'utf-8\'?>\n'
            b'<imsx_POXEnvelopeRequest xmlns="http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0">'
            b'<imsx_POXHeader><imsx_POXRequestHeaderInfo><imsx_version>V1.0</imsx_version>'
            b'<imsx_messageIdentifier>edX_fix</imsx_messageIdentifier></imsx_POXRequestHeaderInfo>'
            b'</imsx_POXHeader><imsx_POXBody><replaceResultRequest><resultRecord><sourcedGUID>'
            b'<sourcedId>MITxLTI/MITxLTI/201x:localhost%3A8000-i4x-MITxLTI-MITxLTI-lti-3751833a214a4f66a0d18f63234207f2'
            b':363979ef768ca171b50f9d1bfb322131</sourcedId>'
            b'</sourcedGUID><result><resultScore><language>en</language><textString>0.32</textString></resultScore>'
            b'</result></resultRecord></replaceResultRequest></imsx_POXBody></imsx_POXEnvelopeRequest>'
        )

        return mock_request

    def test_wrong_xml_namespace(self):
        """
        Test wrong XML Namespace.

        Tests that tool provider returned grade back with wrong XML Namespace.
        """
        with pytest.raises(IndexError):
            mocked_request = self.get_signed_grade_mock_request(namespace_lti_v1p1=False)
            self.xblock.parse_grade_xml_body(mocked_request.body)

    def test_parse_grade_xml_body(self):
        """
        Test XML request body parsing.

        Tests that xml body was parsed successfully.
        """
        mocked_request = self.get_signed_grade_mock_request()
        message_identifier, sourced_id, grade, action = self.xblock.parse_grade_xml_body(mocked_request.body)
        assert self.defaults['messageIdentifier'] == message_identifier
        assert self.defaults['sourcedId'] == sourced_id
        assert self.defaults['grade'] == grade
        assert self.defaults['action'] == action

    @patch('xmodule.lti_block.signature.verify_hmac_sha1', Mock(return_value=False))
    @patch(
        'xmodule.lti_block.LTIBlock.get_client_key_secret',
        Mock(return_value=('test_client_key', 'test_client_secret'))
    )
    def test_failed_verify_oauth_body_sign(self):
        """
        Oauth signing verify fail.
        """
        with pytest.raises(self.LTIError):
            req = self.get_signed_grade_mock_request()
            self.xblock.verify_oauth_body_sign(req)

    def get_signed_grade_mock_request(self, namespace_lti_v1p1=True):
        """
        Example of signed request from LTI Provider.

        When `namespace_v1p0` is set to True then the default namespase from
        LTI 1.1 will be used. Otherwise fake namespace will be added to XML.
        """
        mock_request = Mock()
        mock_request.headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': 'OAuth oauth_nonce="135685044251684026041377608307", \
                oauth_timestamp="1234567890", oauth_version="1.0", \
                oauth_signature_method="HMAC-SHA1", \
                oauth_consumer_key="test_client_key", \
                oauth_signature="my_signature%3D", \
                oauth_body_hash="JEpIArlNCeV4ceXxric8gJQCnBw="'
        }
        mock_request.url = 'http://testurl'
        mock_request.http_method = 'POST'

        params = {}
        if not namespace_lti_v1p1:
            params = {
                'namespace': "http://www.fakenamespace.com/fake"
            }
        mock_request.body = self.get_request_body(params)

        return mock_request

    def test_good_custom_params(self):
        """
        Custom parameters are presented in right format.
        """
        self.xblock.custom_parameters = ['test_custom_params=test_custom_param_value']
        self.xblock.get_client_key_secret = Mock(return_value=('test_client_key', 'test_client_secret'))
        self.xblock.oauth_params = Mock()
        self.xblock.get_input_fields()
        self.xblock.oauth_params.assert_called_with(
            {'custom_test_custom_params': 'test_custom_param_value'},
            'test_client_key', 'test_client_secret'
        )

    def test_bad_custom_params(self):
        """
        Custom parameters are presented in wrong format.
        """
        bad_custom_params = ['test_custom_params: test_custom_param_value']
        self.xblock.custom_parameters = bad_custom_params
        self.xblock.get_client_key_secret = Mock(return_value=('test_client_key', 'test_client_secret'))
        self.xblock.oauth_params = Mock()
        with pytest.raises(self.LTIError):
            self.xblock.get_input_fields()

    def test_max_score(self):
        self.xblock.weight = 100.0

        assert not self.xblock.has_score
        assert self.xblock.max_score() is None

        self.xblock.has_score = True

        assert self.xblock.max_score() == 100.0

    def test_context_id(self):
        """
        Tests that LTI parameter context_id is equal to course_id.
        """
        assert str(self.course_id) == self.xblock.context_id


@override_settings(USE_EXTRACTED_LTI_BLOCK=True)
class TestLTIExtracted(_TestLTIBase):
    __test__ = True


@override_settings(USE_EXTRACTED_LTI_BLOCK=False)
class TestLTIBuiltIn(_TestLTIBase):
    __test__ = True
