""" View For Getting the Status of The Authoring API """
import edx_api_doc_tools as apidocs
from rest_framework.views import APIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework import status
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes


class APIHeartBeatView(DeveloperErrorViewMixin, APIView):
    """
    View for getting the Authoring API's status
    """

    @apidocs.schema(
        parameters=[],
        responses={
            200: "The API is online",
            401: "The requester is not authenticated.",
            403: "The API is not availible",
        },
    )
    @view_auth_classes(is_authenticated=True)
    def get(self, request: Request):
        """
        Get an object containing the Authoring API's status

        **Example Request**

            GET /api/contentstore/v0/heartbeat

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.
        The HTTP 200 response contains a single dict with the  "content_api_enabled" value "True".

        **Example Response**

        ```json
        {
            "content_api_enabled": "True"
        }
        ```
        """
        return Response({'status': 'heartbeat successful'}, status=status.HTTP_200_OK)
