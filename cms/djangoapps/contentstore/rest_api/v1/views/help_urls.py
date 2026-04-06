""" API Views for help tokens """

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.lib.api.view_utils import view_auth_classes

from ....utils import get_help_urls


@view_auth_classes(is_authenticated=True)
class HelpUrlsView(APIView):
    """
    View for getting all help urls.
    """
    def get(self, request: Request):
        """
        Get an help url.

        **Example Request**

            GET /api/contentstore/v1/help_urls

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains a single dict that contains keys for
        pages and locales

        **Example Response**

        ```json
        {
            "default": "http://edx.readthedocs.io/projects/.../index.html",
            "home": "http://edx.readthedocs.io/projects/.../CA_get_started_Studio.html",
            "develop_course": "http://edx.readthedocs.io/projects/.../developing_course/index.html",
            ...
        }
        ```
        """

        data = get_help_urls()
        return Response(data)
