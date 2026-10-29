"""
REST API for content search
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound, ValidationError
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

        keys = request.query_params.getlist("library_key")
        if len(keys) > 1:
            raise ValidationError({"library_key": "Specify exactly one library."})
        response_data = api.generate_user_token_for_studio_search(request, library_key=keys[0] if keys else None)

        return Response(response_data)
