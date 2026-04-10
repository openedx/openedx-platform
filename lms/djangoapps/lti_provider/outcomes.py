"""
Helper functions for managing interactions with the LTI outcomes service defined
in LTI v1.1.
"""


import ipaddress
import logging
import socket
import uuid
from urllib.parse import urlparse

import requests
import requests_oauthlib
from django.conf import settings
from lxml import etree
from lxml.builder import ElementMaker
from requests.exceptions import RequestException

from lms.djangoapps.lti_provider.models import GradedAssignment, OutcomeService

log = logging.getLogger("edx.lti_provider")


def _validate_outcome_service_url(url):
    """
    Validate an ``lis_outcome_service_url`` supplied by an LTI consumer before
    the platform issues a signed outbound POST to it.

    Rejects non-HTTP(S) schemes, private / loopback / link-local / reserved
    IP ranges (SSRF protection), and — optionally — hostnames not present in
    ``settings.LTI_OUTCOME_SERVICE_ALLOWED_HOSTS``. HTTP is rejected unless
    ``settings.LTI_OUTCOME_SERVICE_ALLOW_HTTP`` is explicitly True (e.g. in
    devstack).

    Raises ``ValueError`` on any rejection so callers can log and skip.
    See the security advisory on LTI SSRF.
    """
    if not url or not isinstance(url, str):
        raise ValueError("lis_outcome_service_url is empty or not a string")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme!r}")

    if parsed.scheme == "http" and not getattr(
        settings, "LTI_OUTCOME_SERVICE_ALLOW_HTTP", False
    ):
        raise ValueError("http scheme rejected (set LTI_OUTCOME_SERVICE_ALLOW_HTTP to allow)")

    host = parsed.hostname
    if not host:
        raise ValueError("lis_outcome_service_url has no hostname")

    allowed_hosts = getattr(settings, "LTI_OUTCOME_SERVICE_ALLOWED_HOSTS", None) or []
    if allowed_hosts:
        host_l = host.lower()
        if not any(host_l == h.lower() or host_l.endswith("." + h.lower()) for h in allowed_hosts):
            raise ValueError(f"host {host!r} not in LTI_OUTCOME_SERVICE_ALLOWED_HOSTS")

    # Resolve the hostname and reject if any returned address belongs to a
    # private/loopback/link-local/reserved range. This protects against
    # DNS rebinding and direct IP targets like 169.254.169.254.
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as err:
        raise ValueError(f"cannot resolve host {host!r}: {err}") from err

    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"host {host!r} resolves to a disallowed address: {addr}"
            )


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

        # Reject outcome service URLs that point at private networks or use
        # unsafe schemes before we persist them. This prevents SSRF via a
        # signed outbound POST the first time a score is sent. See the
        # security advisory on LTI outcome URLs.
        try:
            _validate_outcome_service_url(result_service)
        except ValueError as err:
            log.warning(
                "Outcome Service: rejecting lis_outcome_service_url %r: %s",
                result_service, err,
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
    xml = elem.imsx_POXEnvelopeRequest(  # lint-amnesty, pylint: disable=no-member
        elem.imsx_POXHeader(  # lint-amnesty, pylint: disable=no-member
            elem.imsx_POXRequestHeaderInfo(  # lint-amnesty, pylint: disable=no-member
                elem.imsx_version('V1.0'),  # lint-amnesty, pylint: disable=no-member
                elem.imsx_messageIdentifier(str(uuid.uuid4()))  # lint-amnesty, pylint: disable=no-member
            )
        ),
        elem.imsx_POXBody(  # lint-amnesty, pylint: disable=no-member
            elem.replaceResultRequest(  # lint-amnesty, pylint: disable=no-member
                elem.resultRecord(  # lint-amnesty, pylint: disable=no-member
                    elem.sourcedGUID(  # lint-amnesty, pylint: disable=no-member
                        elem.sourcedId(result_sourcedid)  # lint-amnesty, pylint: disable=no-member
                    ),
                    elem.result(  # lint-amnesty, pylint: disable=no-member
                        elem.resultScore(  # lint-amnesty, pylint: disable=no-member
                            elem.language('en'),  # lint-amnesty, pylint: disable=no-member
                            elem.textString(str(score))  # lint-amnesty, pylint: disable=no-member
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

    Re-validates the target URL before sending so that any pre-existing
    ``OutcomeService`` rows written before the URL validator was introduced
    cannot be used as SSRF pivots. Also enforces a request timeout and
    disables redirect-following so a consumer cannot chain into a private
    network via a redirect.
    """
    outcome_service = assignment.outcome_service
    target_url = outcome_service.lis_outcome_service_url

    try:
        _validate_outcome_service_url(target_url)
    except ValueError as err:
        log.warning(
            "Outcome Service: refusing to POST score to %r: %s",
            target_url, err,
        )
        return None

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
    timeout = getattr(settings, "LTI_OUTCOME_SERVICE_TIMEOUT", 10)
    response = requests.post(
        target_url,
        data=xml,
        auth=oauth,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
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
