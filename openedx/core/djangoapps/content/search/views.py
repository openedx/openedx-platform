"""
REST API for content search
"""
from __future__ import annotations  # noqa: I001

import logging

from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.lib.api.view_utils import view_auth_classes

from . import api

User = get_user_model()
log = logging.getLogger(__name__)


@view_auth_classes(is_authenticated=True)
class StudioSearchView(APIView):
    """
    Give user details on how they can search studio content
    """

    def get(self, request):
        """
        Give user details on how they can search studio content
        """
        if not api.is_meilisearch_enabled():
            raise NotFound("Meilisearch features are not enabled.")

        response_data = api.generate_user_token_for_studio_search(request)

        return Response(response_data)
