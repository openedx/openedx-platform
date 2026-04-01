"""Tests for LTI Xmodule LTIv2.0 functional logic."""


import datetime
import textwrap
from django.conf import settings
from django.test import TestCase, override_settings
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from xblock.field_data import DictFieldData

from xmodule import lti_block
from xmodule.tests.helpers import StubUserService

from . import get_test_system


from xmodule.lti_2_util import LTIError as BuiltInLTIError
from xblocks_contrib.lti.lti_2_util import LTIError as ExtractedLTIError


class _LTI20RESTResultServiceTestBase(TestCase):
    """Logic tests for LTI block. LTI2.0 REST ResultService"""

    __test__ = False
    USER_STANDIN = Mock()
    USER_STANDIN.id = 999

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
        self.runtime = get_test_system(user=self.USER_STANDIN)
        self.environ = {'wsgi.url_scheme': 'http', 'REQUEST_METHOD': 'POST'}
        self.runtime.publish = Mock()
        self.runtime._services['rebind_user'] = Mock()  # pylint: disable=protected-access

        self.xblock = self.lti_class(self.runtime, DictFieldData({}), Mock())
        self.lti_id = self.xblock.lti_id
        self.xblock.due = None
        self.xblock.graceperiod = None

    def test_sanitize_get_context(self):
        """Tests that the get_context function does basic sanitization"""
        # get_context, unfortunately, requires a lot of mocking machinery
        mocked_course = Mock(name='mocked_course', lti_passports=['lti_id:test_client:test_secret'])
        modulestore = Mock(name='modulestore')
        modulestore.get_course.return_value = mocked_course
        self.xblock.runtime.modulestore = modulestore
        self.xblock.lti_id = "lti_id"

        test_cases = (  # (before sanitize, after sanitize)
            ("plaintext", "plaintext"),
            ("a <script>alert(3)</script>", "a "),  # drops scripts
            ("<b>bold 包</b>", "<b>bold 包</b>"),  # unicode, and <b> tags pass through
        )
        for case in test_cases:
            self.xblock.score_comment = case[0]
            assert case[1] == self.xblock.get_context()['comment']

    def test_lti20_rest_bad_contenttype(self):
        """
        Input with bad content type
        """
        with self.assertRaisesRegex(self.LTIError, "Content-Type must be"):
            request = Mock(headers={'Content-Type': 'Non-existent'})
            self.xblock.verify_lti_2_0_result_rest_headers(request)

    def test_lti20_rest_failed_oauth_body_verify(self):
        """
        Input with bad oauth body hash verification
        """
        err_msg = "OAuth body verification failed"
        self.xblock.verify_oauth_body_sign = Mock(side_effect=self.LTIError(err_msg))
        with self.assertRaisesRegex(self.LTIError, err_msg):
            request = Mock(headers={'Content-Type': 'application/vnd.ims.lis.v2.result+json'})
            self.xblock.verify_lti_2_0_result_rest_headers(request)

    def test_lti20_rest_good_headers(self):
        """
        Input with good oauth body hash verification
        """
        self.xblock.verify_oauth_body_sign = Mock(return_value=True)

        request = Mock(headers={'Content-Type': 'application/vnd.ims.lis.v2.result+json'})
        self.xblock.verify_lti_2_0_result_rest_headers(request)
        #  We just want the above call to complete without exceptions, and to have called verify_oauth_body_sign
        assert self.xblock.verify_oauth_body_sign.called

    BAD_DISPATCH_INPUTS = [
        None,
        "",
        "abcd"
        "notuser/abcd"
        "user/"
        "user//"
        "user/gbere/"
        "user/gbere/xsdf"
        "user/ಠ益ಠ"  # not alphanumeric
    ]

    def test_lti20_rest_bad_dispatch(self):
        """
        Test the error cases for the "dispatch" argument to the LTI 2.0 handler.  Anything that doesn't
        fit the form user/<anon_id>
        """
        for einput in self.BAD_DISPATCH_INPUTS:
            with self.assertRaisesRegex(self.LTIError, "No valid user id found in endpoint URL"):
                self.xblock.parse_lti_2_0_handler_suffix(einput)

    GOOD_DISPATCH_INPUTS = [
        ("user/abcd3", "abcd3"),
        ("user/Äbcdè2", "Äbcdè2"),  # unicode, just to make sure
    ]

    def test_lti20_rest_good_dispatch(self):
        """
        Test the good cases for the "dispatch" argument to the LTI 2.0 handler.  Anything that does
        fit the form user/<anon_id>
        """
        for ginput, expected in self.GOOD_DISPATCH_INPUTS:
            assert self.xblock.parse_lti_2_0_handler_suffix(ginput) == expected

    BAD_JSON_INPUTS = [
        # (bad inputs, error message expected)
        ([
            "kk",   # ValueError
            "{{}",  # ValueError
            "{}}",  # ValueError
            3,       # TypeError
            {},      # TypeError
        ], "Supplied JSON string in request body could not be decoded"),
        ([
            "3",        # valid json, not array or object
            "[]",       # valid json, array too small
            "[3, {}]",  # valid json, 1st element not an object
        ], "Supplied JSON string is a list that does not contain an object as the first element"),
        ([
            '{"@type": "NOTResult"}',  # @type key must have value 'Result'
        ], "JSON object does not contain correct @type attribute"),
        ([
            # @context missing
            '{"@type": "Result", "resultScore": 0.1}',
        ], "JSON object does not contain required key"),
        ([
            '''
            {"@type": "Result",
             "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
             "resultScore": 100}'''  # score out of range
        ], "score value outside the permitted range of 0-1."),
        ([
            '''
            {"@type": "Result",
             "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
             "resultScore": "1b"}''',   # score ValueError
            '''
            {"@type": "Result",
             "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
             "resultScore": {}}''',   # score TypeError
        ], "Could not convert resultScore to float"),
    ]

    def test_lti20_bad_json(self):
        """
        Test that bad json_str to parse_lti_2_0_result_json inputs raise appropriate LTI Error
        """
        for error_inputs, error_message in self.BAD_JSON_INPUTS:
            for einput in error_inputs:
                with self.assertRaisesRegex(self.LTIError, error_message):
                    self.xblock.parse_lti_2_0_result_json(einput)

    GOOD_JSON_INPUTS = [
        ('''
        {"@type": "Result",
         "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
         "resultScore": 0.1}''', ""),  # no comment means we expect ""
        ('''
        [{"@type": "Result",
         "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
         "@id": "anon_id:abcdef0123456789",
         "resultScore": 0.1}]''', ""),  # OK to have array of objects -- just take the first.  @id is okay too
        ('''
        {"@type": "Result",
         "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
         "resultScore": 0.1,
         "comment": "ಠ益ಠ"}''', "ಠ益ಠ"),  # unicode comment
    ]

    def test_lti20_good_json(self):
        """
        Test the parsing of good comments
        """
        for json_str, expected_comment in self.GOOD_JSON_INPUTS:
            score, comment = self.xblock.parse_lti_2_0_result_json(json_str)
            assert score == 0.1
            assert comment == expected_comment

    GOOD_JSON_PUT = textwrap.dedent("""
        {"@type": "Result",
         "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
         "@id": "anon_id:abcdef0123456789",
         "resultScore": 0.1,
         "comment": "ಠ益ಠ"}
    """).encode('utf-8')

    GOOD_JSON_PUT_LIKE_DELETE = textwrap.dedent("""
        {"@type": "Result",
         "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
         "@id": "anon_id:abcdef0123456789",
         "comment": "ಠ益ಠ"}
    """).encode('utf-8')

    def get_signed_lti20_mock_request(self, body, method='PUT'):
        """
        Example of signed from LTI 2.0 Provider.  Signatures and hashes are example only and won't verify
        """
        mock_request = Mock()
        mock_request.headers = {
            'Content-Type': 'application/vnd.ims.lis.v2.result+json',
            'Authorization': (
                'OAuth oauth_nonce="135685044251684026041377608307", '
                'oauth_timestamp="1234567890", oauth_version="1.0", '
                'oauth_signature_method="HMAC-SHA1", '
                'oauth_consumer_key="test_client_key", '
                'oauth_signature="my_signature%3D", '
                'oauth_body_hash="gz+PeJZuF2//n9hNUnDj2v5kN70="'
            )
        }
        mock_request.url = 'http://testurl'
        mock_request.http_method = method
        mock_request.method = method
        mock_request.body = body
        return mock_request

    def setup_system_xblock_mocks_for_lti20_request_test(self):
        """
        Helper fn to set up mocking for lti 2.0 request test
        """
        self.xblock.max_score = Mock(return_value=1.0)
        self.xblock.get_client_key_secret = Mock(return_value=('test_client_key', 'test_client_secret'))
        self.xblock.verify_oauth_body_sign = Mock()

    def test_lti20_put_like_delete_success(self):
        """
        The happy path for LTI 2.0 PUT that acts like a delete
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        SCORE = 0.55  # pylint: disable=invalid-name
        COMMENT = "ಠ益ಠ"  # pylint: disable=invalid-name
        self.xblock.module_score = SCORE
        self.xblock.score_comment = COMMENT
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT_LIKE_DELETE)
        # Now call the handler
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        # Now assert there's no score
        assert response.status_code == 200
        assert self.xblock.module_score is None
        assert self.xblock.score_comment == ''
        (_, evt_type, called_grade_obj), _ = self.runtime.publish.call_args  # pylint: disable=unpacking-non-sequence
        assert called_grade_obj ==\
               {'user_id': self.USER_STANDIN.id, 'value': None, 'max_value': None, 'score_deleted': True}
        assert evt_type == 'grade'

    def test_lti20_delete_success(self):
        """
        The happy path for LTI 2.0 DELETE
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        SCORE = 0.55  # pylint: disable=invalid-name
        COMMENT = "ಠ益ಠ"  # pylint: disable=invalid-name
        self.xblock.module_score = SCORE
        self.xblock.score_comment = COMMENT
        mock_request = self.get_signed_lti20_mock_request(b"", method='DELETE')
        # Now call the handler
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        # Now assert there's no score
        assert response.status_code == 200
        assert self.xblock.module_score is None
        assert self.xblock.score_comment == ''
        (_, evt_type, called_grade_obj), _ = self.runtime.publish.call_args  # pylint: disable=unpacking-non-sequence
        assert called_grade_obj ==\
               {'user_id': self.USER_STANDIN.id, 'value': None, 'max_value': None, 'score_deleted': True}
        assert evt_type == 'grade'

    def test_lti20_put_set_score_success(self):
        """
        The happy path for LTI 2.0 PUT that sets a score
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        # Now call the handler
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        # Now assert
        assert response.status_code == 200
        assert self.xblock.module_score == 0.1
        assert self.xblock.score_comment == 'ಠ益ಠ'
        (_, evt_type, called_grade_obj), _ = self.runtime.publish.call_args  # pylint: disable=unpacking-non-sequence
        assert evt_type == 'grade'
        assert called_grade_obj ==\
               {'user_id': self.USER_STANDIN.id, 'value': 0.1, 'max_value': 1.0, 'score_deleted': False}

    def test_lti20_get_no_score_success(self):
        """
        The happy path for LTI 2.0 GET when there's no score
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        mock_request = self.get_signed_lti20_mock_request(b"", method='GET')
        # Now call the handler
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        # Now assert
        assert response.status_code == 200
        assert response.json == {'@context': 'http://purl.imsglobal.org/ctx/lis/v2/Result', '@type': 'Result'}

    def test_lti20_get_with_score_success(self):
        """
        The happy path for LTI 2.0 GET when there is a score
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        SCORE = 0.55  # pylint: disable=invalid-name
        COMMENT = "ಠ益ಠ"  # pylint: disable=invalid-name
        self.xblock.module_score = SCORE
        self.xblock.score_comment = COMMENT
        mock_request = self.get_signed_lti20_mock_request(b"", method='GET')
        # Now call the handler
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        # Now assert
        assert response.status_code == 200
        assert response.json ==\
               {'@context': 'http://purl.imsglobal.org/ctx/lis/v2/Result',
                '@type': 'Result', 'resultScore': SCORE, 'comment': COMMENT}

    UNSUPPORTED_HTTP_METHODS = ["OPTIONS", "HEAD", "POST", "TRACE", "CONNECT"]

    def test_lti20_unsupported_method_error(self):
        """
        Test we get a 404 when we don't GET or PUT
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        for bad_method in self.UNSUPPORTED_HTTP_METHODS:
            mock_request.method = bad_method
            response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
            assert response.status_code == 404

    def test_lti20_request_handler_bad_headers(self):
        """
        Test that we get a 401 when header verification fails
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        self.xblock.verify_lti_2_0_result_rest_headers = Mock(side_effect=self.LTIError())
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        assert response.status_code == 401

    def test_lti20_request_handler_bad_dispatch_user(self):
        """
        Test that we get a 404 when there's no (or badly formatted) user specified in the url
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, None)
        assert response.status_code == 404

    def test_lti20_request_handler_bad_json(self):
        """
        Test that we get a 404 when json verification fails
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        self.xblock.parse_lti_2_0_result_json = Mock(side_effect=self.LTIError())
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        assert response.status_code == 404

    def test_lti20_request_handler_bad_user(self):
        """
        Test that we get a 404 when the supplied user does not exist
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        self.runtime._services['user'] = StubUserService(user=None)  # pylint: disable=protected-access
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        assert response.status_code == 404

    def test_lti20_request_handler_grade_past_due(self):
        """
        Test that we get a 404 when accept_grades_past_due is False and it is past due
        """
        self.setup_system_xblock_mocks_for_lti20_request_test()
        self.xblock.due = datetime.datetime.now(ZoneInfo("UTC"))
        self.xblock.accept_grades_past_due = False
        mock_request = self.get_signed_lti20_mock_request(self.GOOD_JSON_PUT)
        response = self.xblock.lti_2_0_result_rest_handler(mock_request, "user/abcd")
        assert response.status_code == 404


@override_settings(USE_EXTRACTED_LTI_BLOCK=True)
class TestLTI20RESTResultServiceWithExtracted(_LTI20RESTResultServiceTestBase):
    __test__ = True


@override_settings(USE_EXTRACTED_LTI_BLOCK=False)
class TestLTI20RESTResultServiceWithBuiltIn(_LTI20RESTResultServiceTestBase):
    __test__ = True
