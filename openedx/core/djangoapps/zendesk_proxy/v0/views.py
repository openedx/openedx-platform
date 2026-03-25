"""
Define request handlers used by the zendesk_proxy djangoapp
"""

from rest_framework import status  # noqa: I001
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from openedx.core.djangoapps.zendesk_proxy.utils import create_zendesk_ticket

ZENDESK_REQUESTS_PER_HOUR = 50


class ZendeskProxyThrottle(SimpleRateThrottle):
    """
    Custom throttle rates for this particular endpoint's use case.
    """
    def __init__(self):
        self.rate = f'{ZENDESK_REQUESTS_PER_HOUR}/hour'
        super().__init__()

    def get_cache_key(self, request, view):
        """
        By providing a static string here, we are limiting *all* users to the same combined limit.
        """
        return "ZendeskProxy_rate_limit_cache_key"


class ZendeskPassthroughView(APIView):
    """
    An APIView that will take in inputs from an unauthenticated endpoint, and use them to securely create a zendesk
    ticket.
    """
    throttle_classes = (ZendeskProxyThrottle,)
    parser_classes = (JSONParser,)

    def post(self, request):
        """
        request body is expected to look like this:
        {
            "name": "John Q. Student",
            "email": {
                "from": "JohnQStudent@realemailhost.com",
                "message": "I, John Q. Student, am having problems for the following reasons: ...",
                "subject": "Help Request"
            },
            "tags": ["zendesk_help_request"]
        }
        """
        try:
            proxy_status = create_zendesk_ticket(
                requester_name=request.data['name'],
                requester_email=request.data['email']['from'],
                subject=request.data['email']['subject'],
                body=request.data['email']['message'],
                tags=request.data['tags']
            )
        except KeyError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        return Response(
            status=proxy_status
        )
