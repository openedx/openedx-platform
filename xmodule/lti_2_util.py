"""
A mixin class for LTI 2.0 functionality.  This is really just done to refactor the code to
keep the LTIBlock class from getting too big
"""


import base64
import hashlib
import json
import logging
import re
from unittest import mock
from urllib import parse

from django.conf import settings
from oauthlib.oauth1 import Client
from webob import Response
from xblock.core import XBlock

from openedx.core.lib.grade_utils import round_away_from_zero

log = logging.getLogger(__name__)

LTI_2_0_REST_SUFFIX_PARSER = re.compile(r"^user/(?P<anon_id>\w+)", re.UNICODE)
LTI_2_0_JSON_CONTENT_TYPE = 'application/vnd.ims.lis.v2.result+json'


class LTIError(Exception):
    """Error class for LTIBlock and LTI20BlockMixin"""


class LTI20BlockMixin:
    """
    This class MUST be mixed into LTIBlock.  It does not do anything on its own.  It's just factored
    out for modularity.
    """

    #  LTI 2.0 Result Service Support
    @XBlock.handler
    def lti_2_0_result_rest_handler(self, request, suffix):
        """
        Handler function for LTI 2.0 JSON/REST result service.

        See http://www.imsglobal.org/lti/ltiv2p0/uml/purl.imsglobal.org/vocab/lis/v2/outcomes/Result/service.html
        An example JSON object:
        {
         "@context" : "http://purl.imsglobal.org/ctx/lis/v2/Result",
         "@type" : "Result",
         "resultScore" : 0.83,
         "comment" : "This is exceptional work."
        }
        For PUTs, the content type must be "application/vnd.ims.lis.v2.result+json".
        We use the "suffix" parameter to parse out the user from the end of the URL.  An example endpoint url is
        http://localhost:8000/courses/org/num/run/xblock/i4x:;_;_org;_num;_lti;_GUID/handler_noauth/lti_2_0_result_rest_handler/user/<anon_id>
        so suffix is of the form "user/<anon_id>"
        Failures result in 401, 404, or 500s without any body.  Successes result in 200.  Again see
        http://www.imsglobal.org/lti/ltiv2p0/uml/purl.imsglobal.org/vocab/lis/v2/outcomes/Result/service.html
        (Note: this prevents good debug messages for the client, so we might want to change this, or the spec)

        Arguments:
            request (xblock.django.request.DjangoWebobRequest):  Request object for current HTTP request
            suffix (unicode):  request path after "lti_2_0_result_rest_handler/".  expected to be "user/<anon_id>"

        Returns:
            webob.response:  response to this request.  See above for details.
        """
        if settings.DEBUG:
            self._log_correct_authorization_header(request)

        if not self.accept_grades_past_due and self.is_past_due():
            return Response(status=404)  # have to do 404 due to spec, but 400 is better, with error msg in body

        try:
            anon_id = self.parse_lti_2_0_handler_suffix(suffix)
        except LTIError:
            return Response(status=404)  # 404 because a part of the URL (denoting the anon user id) is invalid
        try:
            self.verify_lti_2_0_result_rest_headers(request, verify_content_type=True)
        except LTIError:
            return Response(status=401)  # Unauthorized in this case.  401 is right

        real_user = self.runtime.service(self, 'user').get_user_by_anonymous_id(anon_id)
        if not real_user:  # that means we can't save to database, as we do not have real user id.
            msg = f"[LTI]: Real user not found against anon_id: {anon_id}"
            log.info(msg)
            return Response(status=404)  # have to do 404 due to spec, but 400 is better, with error msg in body
        if request.method == "PUT":
            return self._lti_2_0_result_put_handler(request, real_user)
        elif request.method == "GET":
            return self._lti_2_0_result_get_handler(request, real_user)
        elif request.method == "DELETE":
            return self._lti_2_0_result_del_handler(request, real_user)
        else:
            return Response(status=404)  # have to do 404 due to spec, but 405 is better, with error msg in body

    def _log_correct_authorization_header(self, request):
        """
        Helper function that logs proper HTTP Authorization header for a given request

        Used only in debug situations, this logs the correct Authorization header based on
        the request header and body according to OAuth 1 Body signing

        Arguments:
            request (xblock.django.request.DjangoWebobRequest):  Request object to log Authorization header for

        Returns:
            nothing
        """
        sha1 = hashlib.sha1()
        sha1.update(request.body)
        oauth_body_hash = str(base64.b64encode(sha1.digest()))
        log.debug(f"[LTI] oauth_body_hash = {oauth_body_hash}")
        client_key, client_secret = self.get_client_key_secret()
        client = Client(client_key, client_secret)
        mock_request = mock.Mock(
            uri=str(parse.unquote(request.url)),
            headers=request.headers,
            body="",
            decoded_body="",
            http_method=str(request.method),
        )
        params = client.get_oauth_params(mock_request)
        mock_request.oauth_params = params
        mock_request.oauth_params.append(('oauth_body_hash', oauth_body_hash))
        sig = client.get_oauth_signature(mock_request)
        mock_request.oauth_params.append(('oauth_signature', sig))

        _, headers, _ = client._render(mock_request)  # pylint: disable=protected-access
        log.debug("\n\n#### COPY AND PASTE AUTHORIZATION HEADER ####\n{}\n####################################\n\n"
                  .format(headers['Authorization']))

    def parse_lti_2_0_handler_suffix(self, suffix):
        """
        Parser function for HTTP request path suffixes

        parses the suffix argument (the trailing parts of the URL) of the LTI2.0 REST handler.
        must be of the form "user/<anon_id>".  Returns anon_id if match found, otherwise raises LTIError

        Arguments:
            suffix (unicode):  suffix to parse

        Returns:
            unicode: anon_id if match found

        Raises:
            LTIError if suffix cannot be parsed or is not in its expected form
        """
        if suffix:
            match_obj = LTI_2_0_REST_SUFFIX_PARSER.match(suffix)
            if match_obj:
                return match_obj.group('anon_id')
        # fall-through handles all error cases
        msg = "No valid user id found in endpoint URL"
        log.info(f"[LTI]: {msg}")
        raise LTIError(msg)

    def _lti_2_0_result_get_handler(self, request, real_user):
        """
        Helper request handler for GET requests to LTI 2.0 result endpoint

        GET handler for lti_2_0_result.  Assumes all authorization has been checked.

        Arguments:
            request (xblock.django.request.DjangoWebobRequest):  Request object (unused)
            real_user (django.contrib.auth.models.User):  Actual user linked to anon_id in request path suffix

        Returns:
            webob.response:  response to this request, in JSON format with status 200 if success
        """
        base_json_obj = {
            "@context": "http://purl.imsglobal.org/ctx/lis/v2/Result",
            "@type": "Result"
        }
        self.runtime.service(self, 'rebind_user').rebind_noauth_module_to_user(self, real_user)
        if self.module_score is None:  # In this case, no score has been ever set
            return Response(json.dumps(base_json_obj).encode('utf-8'), content_type=LTI_2_0_JSON_CONTENT_TYPE)

        # Fall through to returning grade and comment
        base_json_obj['resultScore'] = round_away_from_zero(self.module_score, 2)
        base_json_obj['comment'] = self.score_comment
        return Response(json.dumps(base_json_obj).encode('utf-8'), content_type=LTI_2_0_JSON_CONTENT_TYPE)

    def _lti_2_0_result_del_handler(self, request, real_user):
        """
        Helper request handler for DELETE requests to LTI 2.0 result endpoint

        DELETE handler for lti_2_0_result.  Assumes all authorization has been checked.

        Arguments:
            request (xblock.django.request.DjangoWebobRequest):  Request object (unused)
            real_user (django.contrib.auth.models.User):  Actual user linked to anon_id in request path suffix

        Returns:
            webob.response:  response to this request.  status 200 if success
        """
        self.clear_user_module_score(real_user)
        return Response(status=200)

    def _lti_2_0_result_put_handler(self, request, real_user):
        """
        Helper request handler for PUT requests to LTI 2.0 result endpoint

        PUT handler for lti_2_0_result.  Assumes all authorization has been checked.

        Arguments:
            request (xblock.django.request.DjangoWebobRequest):  Request object
            real_user (django.contrib.auth.models.User):  Actual user linked to anon_id in request path suffix

        Returns:
            webob.response:  response to this request.  status 200 if success.  404 if body of PUT request is malformed
        """
        try:
            (score, comment) = self.parse_lti_2_0_result_json(request.body.decode('utf-8'))
        except LTIError:
            return Response(status=404)  # have to do 404 due to spec, but 400 is better, with error msg in body

        # According to http://www.imsglobal.org/lti/ltiv2p0/ltiIMGv2p0.html#_Toc361225514
        # PUTting a JSON object with no "resultScore" field is equivalent to a DELETE.
        if score is None:
            self.clear_user_module_score(real_user)
            return Response(status=200)

        # Fall-through record the score and the comment in the block
        self.set_user_module_score(real_user, score, self.max_score(), comment)
        return Response(status=200)

    def clear_user_module_score(self, user):
        """
        Clears the module user state, including grades and comments, and also scoring in db's courseware_studentmodule

        Arguments:
            user (django.contrib.auth.models.User):  Actual user whose module state is to be cleared

        Returns:
            nothing
        """
        self.set_user_module_score(user, None, None, score_deleted=True)

    def set_user_module_score(self, user, score, max_score, comment="", score_deleted=False):
        """
        Sets the module user state, including grades and comments, and also scoring in db's courseware_studentmodule

        Arguments:
            user (django.contrib.auth.models.User):  Actual user whose module state is to be set
            score (float):  user's numeric score to set.  Must be in the range [0.0, 1.0]
            max_score (float):  max score that could have been achieved on this module
            comment (unicode):  comments provided by the grader as feedback to the student

        Returns:
            nothing
        """
        if score is not None and max_score is not None:
            scaled_score = score * max_score
        else:
            scaled_score = None

        self.runtime.service(self, 'rebind_user').rebind_noauth_module_to_user(self, user)

        # have to publish for the progress page...
        self.runtime.publish(
            self,
            'grade',
            {
                'value': scaled_score,
                'max_value': max_score,
                'user_id': user.id,
                'score_deleted': score_deleted,
            },
        )
        self.module_score = scaled_score
        self.score_comment = comment

    def verify_lti_2_0_result_rest_headers(self, request, verify_content_type=True):
        """
        Helper method to validate LTI 2.0 REST result service HTTP headers.  returns if correct, else raises LTIError

        Arguments:
            request (xblock.django.request.DjangoWebobRequest):  Request object
            verify_content_type (bool):  If true, verifies the content type of the request is that spec'ed by LTI 2.0

        Returns:
            nothing, but will only return if verification succeeds

        Raises:
            LTIError if verification fails
        """
        content_type = request.headers.get('Content-Type')
        if verify_content_type and content_type != LTI_2_0_JSON_CONTENT_TYPE:
            log.info(f"[LTI]: v2.0 result service -- bad Content-Type: {content_type}")
            raise LTIError(
                "For LTI 2.0 result service, Content-Type must be {}.  Got {}".format(LTI_2_0_JSON_CONTENT_TYPE,  # noqa: UP032  # pylint: disable=line-too-long
                                                                                      content_type))
        try:
            self.verify_oauth_body_sign(request, content_type=LTI_2_0_JSON_CONTENT_TYPE)
        except (ValueError, LTIError) as err:
            log.info(f"[LTI]: v2.0 result service -- OAuth body verification failed:  {str(err)}")
            raise LTIError(str(err))  # pylint: disable=raise-missing-from  # noqa: B904

    def parse_lti_2_0_result_json(self, json_str):
        """
        Helper method for verifying LTI 2.0 JSON object contained in the body of the request.

        The json_str must be loadable.  It can either be an dict (object) or an array whose first element is an dict,
        in which case that first dict is considered.
        The dict must have the "@type" key with value equal to "Result",
        "resultScore" key with value equal to a number [0, 1],
        The "@context" key must be present, but we don't do anything with it.  And the "comment" key may be
        present, in which case it must be a string.

        Arguments:
            json_str (unicode):  The body of the LTI 2.0 results service request, which is a JSON string]

        Returns:
            (float, str):  (score, [optional]comment) if verification checks out

        Raises:
            LTIError (with message) if verification fails
        """
        try:
            json_obj = json.loads(json_str)
        except (ValueError, TypeError):
            msg = f"Supplied JSON string in request body could not be decoded: {json_str}"
            log.info(f"[LTI] {msg}")
            raise LTIError(msg)  # pylint: disable=raise-missing-from  # noqa: B904

        # the standard supports a list of objects, who knows why. It must contain at least 1 element, and the
        # first element must be a dict
        if not isinstance(json_obj, dict):
            if isinstance(json_obj, list) and len(json_obj) >= 1 and isinstance(json_obj[0], dict):
                json_obj = json_obj[0]
            else:
                msg = ("Supplied JSON string is a list that does not contain an object as the first element. {}"  # noqa: UP032  # pylint: disable=line-too-long
                       .format(json_str))
                log.info(f"[LTI] {msg}")
                raise LTIError(msg)

        # '@type' must be "Result"
        result_type = json_obj.get("@type")
        if result_type != "Result":
            msg = f"JSON object does not contain correct @type attribute (should be 'Result', is {result_type})"
            log.info(f"[LTI] {msg}")
            raise LTIError(msg)

        # '@context' must be present as a key
        REQUIRED_KEYS = ["@context"]  # pylint: disable=invalid-name
        for key in REQUIRED_KEYS:
            if key not in json_obj:
                msg = f"JSON object does not contain required key {key}"
                log.info(f"[LTI] {msg}")
                raise LTIError(msg)

        # 'resultScore' is not present.  If this was a PUT this means it's actually a DELETE according
        # to the LTI spec.  We will indicate this by returning None as score, "" as comment.
        # The actual delete will be handled by the caller
        if "resultScore" not in json_obj:
            return None, json_obj.get('comment', "")

        # if present, 'resultScore' must be a number between 0 and 1 inclusive
        try:
            score = float(json_obj.get('resultScore', "unconvertable"))  # Check if float is present and the right type
            if not 0 <= score <= 1:
                msg = 'score value outside the permitted range of 0-1.'
                log.info(f"[LTI] {msg}")
                raise LTIError(msg)
        except (TypeError, ValueError) as err:
            msg = f"Could not convert resultScore to float: {str(err)}"
            log.info(f"[LTI] {msg}")
            raise LTIError(msg)  # pylint: disable=raise-missing-from  # noqa: B904

        return score, json_obj.get('comment', "")
