"""
Helper functions for managing interactions with the LTI outcomes service defined
in LTI v1.1.
"""


import logging
import uuid

import requests
import requests_oauthlib
from lxml import etree
from lxml.builder import ElementMaker
from requests.exceptions import RequestException

from lms.djangoapps.lti_provider.models import GradedAssignment, OutcomeService

log = logging.getLogger("edx.lti_provider")


def store_outcome_parameters(request_params, user, lti_consumer):
    """
    Determine whether a set of LTI launch parameters contains information about
    an expected score, and if so create a GradedAssignment record. Create a new
    OutcomeService record if none exists for the tool consumer, and update any
    incomplete record with additional data if it is available.
    """
    result_id = request_params.get('lis_result_sourcedid', None)

    # We're only interested in requests that include a lis_result_sourcedid
    # parameter. An LTI consumer that does not send that parameter does not
    # expect scoring updates for that particular request.
    if result_id:
        result_service = request_params.get('lis_outcome_service_url', None)
        if not result_service:
            # TODO: There may be a way to recover from this error; if we know
            # the LTI consumer that the request comes from then we may be able
            # to figure out the result service URL. As it stands, though, this
            # is a badly-formed LTI request
            log.warning(
                "Outcome Service: lis_outcome_service_url parameter missing "
                "from scored assignment; we will be unable to return a score. "
                "Request parameters: %s",
                request_params
            )
            return

        # Both usage and course ID parameters are supplied in the LTI launch URL
        usage_key = request_params['usage_key']
        course_key = request_params['course_key']

        # Create a record of the outcome service if necessary
        outcomes, __ = OutcomeService.objects.get_or_create(
            lis_outcome_service_url=result_service,
            lti_consumer=lti_consumer
        )

        GradedAssignment.objects.get_or_create(
            lis_result_sourcedid=result_id,
            course_key=course_key,
            usage_key=usage_key,
            user=user,
            outcome_service=outcomes
        )


def generate_replace_result_xml(result_sourcedid, score):
    """
    Create the XML document that contains the new score to be sent to the LTI
    consumer. The format of this message is defined in the LTI 1.1 spec.
    """
    # Pylint doesn't recognize members in the LXML module
    elem = ElementMaker(nsmap={None: 'http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0'})
    xml = elem.imsx_POXEnvelopeRequest(  # pylint: disable=no-member
        elem.imsx_POXHeader(  # pylint: disable=no-member
            elem.imsx_POXRequestHeaderInfo(  # pylint: disable=no-member
                elem.imsx_version('V1.0'),  # pylint: disable=no-member
                elem.imsx_messageIdentifier(str(uuid.uuid4()))  # pylint: disable=no-member
            )
        ),
        elem.imsx_POXBody(  # pylint: disable=no-member
            elem.replaceResultRequest(  # pylint: disable=no-member
                elem.resultRecord(  # pylint: disable=no-member
                    elem.sourcedGUID(  # pylint: disable=no-member
                        elem.sourcedId(result_sourcedid)  # pylint: disable=no-member
                    ),
                    elem.result(  # pylint: disable=no-member
                        elem.resultScore(  # pylint: disable=no-member
                            elem.language('en'),  # pylint: disable=no-member
                            elem.textString(str(score))  # pylint: disable=no-member
                        )
                    )
                )
            )
        )
    )
    return etree.tostring(xml, xml_declaration=True, encoding='UTF-8')


def get_assignments_for_problem(problem_block, user_id, course_key):
    """
    Trace the parent hierarchy from a given problem to find all blocks that
    correspond to graded assignment launches for this user. A problem may
    show up multiple times for a given user; the problem could be embedded in
    multiple courses (or multiple times in the same course), or the block could
    be embedded more than once at different granularities (as an individual
    problem and as a problem in a vertical, for example).

    Returns a list of GradedAssignment objects that are associated with the
    given block for the current user.
    """
    locations = []
    current_block = problem_block
    while current_block:
        locations.append(current_block.location)
        current_block = current_block.get_parent()
    assignments = GradedAssignment.objects.filter(
        user=user_id, course_key=course_key, usage_key__in=locations
    )
    return assignments


def send_score_update(assignment, score):
    """
    Create and send the XML message to the campus LMS system to update the grade
    for a single graded assignment.
    """
    xml = generate_replace_result_xml(
        assignment.lis_result_sourcedid, score
    )
    try:
        response = sign_and_send_replace_result(assignment, xml)
    except RequestException:
        # failed to send result. 'response' is None, so more detail will be
        # logged at the end of the method.
        response = None
        log.exception("Outcome Service: Error when sending result.")

    # If something went wrong, make sure that we have a complete log record.
    # That way we can manually fix things up on the campus system later if
    # necessary.
    if not (response and check_replace_result_response(response)):
        log.error(
            "Outcome Service: Failed to update score on LTI consumer. "
            "User: %s, course: %s, usage: %s, score: %s, status: %s, body: %s",
            assignment.user,
            assignment.course_key,
            assignment.usage_key,
            score,
            response,
            response.text if response else 'Unknown'
        )


def sign_and_send_replace_result(assignment, xml):
    """
    Take the XML document generated in generate_replace_result_xml, and sign it
    with the consumer key and secret assigned to the consumer. Send the signed
    message to the LTI consumer.
    """
    outcome_service = assignment.outcome_service
    consumer = outcome_service.lti_consumer
    consumer_key = consumer.consumer_key
    consumer_secret = consumer.consumer_secret

    # Calculate the OAuth signature for the replace_result message.
    oauth = requests_oauthlib.OAuth1(
        consumer_key,
        consumer_secret,
        signature_method='HMAC-SHA1',
        force_include_body=True,
        decoding=None,
    )

    headers = {'content-type': 'application/xml'}
    response = requests.post(
        assignment.outcome_service.lis_outcome_service_url,
        data=xml,
        auth=oauth,
        headers=headers
    )

    return response


def check_replace_result_response(response):
    """
    Parse the response sent by the LTI consumer after an score update message
    has been processed. Return True if the message was properly received, or
    False if not. The format of this message is defined in the LTI 1.1 spec.
    """
    # Pylint doesn't recognize members in the LXML module
    if response.status_code != 200:
        log.error(
            "Outcome service response: Unexpected status code %s",
            response.status_code
        )
        return False

    try:
        xml = response.content
        root = etree.fromstring(xml)
    except etree.ParseError as ex:
        log.error("Outcome service response: Failed to parse XML: %s\n %s", ex, xml)
        return False

    major_codes = root.xpath(
        '//ns:imsx_codeMajor',
        namespaces={'ns': 'http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0'})
    if len(major_codes) != 1:
        log.error(
            "Outcome service response: Expected exactly one imsx_codeMajor field in response. Received %s",
            major_codes
        )
        return False

    if major_codes[0].text != 'success':
        log.error(
            "Outcome service response: Unexpected major code: %s.",
            major_codes[0].text
        )
        return False

    return True
