"""
Define request handlers used by the zendesk_proxy djangoapp
"""
import logging  # noqa: I001

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from openedx.core.djangoapps.zendesk_proxy.utils import create_zendesk_ticket
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser

logger = logging.getLogger(__name__)
REQUESTS_PER_HOUR = 50


class ZendeskProxyThrottle(UserRateThrottle):
    """
    Custom throttle rates for this particular endpoint's use case.
    """

    def __init__(self):
        self.rate = f'{REQUESTS_PER_HOUR}/hour'
        super().__init__()


class ZendeskPassthroughView(APIView):
    """
    An APIView that will take in inputs from an unauthenticated endpoint, and use them to securely create a zendesk
    ticket.
    """
    throttle_classes = (ZendeskProxyThrottle,)
    parser_classes = (JSONParser,)
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    def post(self, request):
        """
        request body is expected to look like this:
        {
            "requester": {
                "email": "john@example.com",
                "name": "name"
            },
            "subject": "test subject",
            "comment": {
                "body": "message details",
                "uploads": ['file_token'],
            },
            "custom_fields": [
                {
                    "id": '001',
                    "value": 'demo-course'
                }
            ],
            "tags": ["LMS"]
        }
        """
        try:
            proxy_status = create_zendesk_ticket(
                requester_name=request.user.username,
                requester_email=request.user.email,
                subject=request.data['subject'],
                body=request.data['comment']['body'],
                custom_fields=request.data['custom_fields'],
                tags=request.data['tags']
            )
        except AttributeError as attribute:
            logger.error('Zendesk Proxy Bad Request AttributeError: %s', attribute)
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except KeyError as key:
            logger.error('Zendesk Proxy Bad Request KeyError: %s', key)
            return Response(status=status.HTTP_400_BAD_REQUEST)

        return Response(
            status=proxy_status
        )
