"""Learner dashboard views"""

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.edxmako.shortcuts import render_to_response
from lms.djangoapps.learner_dashboard.programs import (
    ProgramDetailsFragmentView,
    ProgramDiscussionLTI,
    ProgramLiveLTI,
    ProgramsFragmentView,
)
from lms.djangoapps.learner_dashboard.utils import is_enrolled_or_staff, masters_program_tab_view_is_enabled
from lms.djangoapps.program_enrollments.rest_api.v1.utils import ProgramSpecificViewMixin
from openedx.core.djangoapps.programs.models import ProgramsApiConfig
from openedx.core.lib.api.authentication import BearerAuthentication


@login_required
@require_GET
def program_listing(request):
    """View a list of programs in which the user is engaged."""
    programs_config = ProgramsApiConfig.current()
    programs_fragment = ProgramsFragmentView().render_to_fragment(request, programs_config=programs_config)

    context = {
        'disable_courseware_js': True,
        'programs_fragment': programs_fragment,
        'nav_hidden': True,
        'show_dashboard_tabs': True,
        'show_program_listing': programs_config.enabled,
        'uses_bootstrap': True,
    }

    return render_to_response('learner_dashboard/programs.html', context)


@login_required
@require_GET
def program_details(request, program_uuid):
    """View details about a specific program."""
    programs_config = ProgramsApiConfig.current()
    program_fragment = ProgramDetailsFragmentView().render_to_fragment(
        request, program_uuid, programs_config=programs_config
    )

    context = {
        'program_fragment': program_fragment,
        'show_program_listing': programs_config.enabled,
        'show_dashboard_tabs': True,
        'nav_hidden': True,
        'disable_courseware_js': True,
        'uses_bootstrap': True,
    }

    return render_to_response('learner_dashboard/program_details.html', context)


class ProgramDiscussionIframeView(APIView, ProgramSpecificViewMixin):
    """
    A view for retrieving Program Discussion IFrame .

    Path: ``/dashboard/programs/{program_uuid}/discussion/``

    Accepts: [GET]

    ------------------------------------------------------------------------------------
    GET
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: OK - Contains a program discussion iframe.
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access to the program.
        * 404: The requested program does not exist.

    **Response**

        In the case of a 200 response code, the response will be iframe HTML and status if discussion is configured
        for the program.

    **Example**

        {
            'tab_view_enabled': True,
            'discussion': {
                "iframe": "
                            <iframe
                                id='lti-tab-embed'
                                style='width: 100%; min-height: 800px; border: none'
                                srcdoc='{srcdoc}'
                             >
                            </iframe>
                            ",
                "configured": false
            }
        }

    """
    authentication_classes = (JwtAuthentication, BearerAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, program_uuid):
        """ GET handler """
        if not is_enrolled_or_staff(request, program_uuid):
            default_response = {
                'tab_view_enabled': False,
                'discussion': {
                    'configured': False,
                    'iframe': ''
                }
            }
            return Response(default_response, status=status.HTTP_200_OK)
        program_discussion_lti = ProgramDiscussionLTI(program_uuid, request)
        response_data = {
            'tab_view_enabled': masters_program_tab_view_is_enabled(),
            'discussion': {
                'iframe': program_discussion_lti.render_iframe(),
                'configured': program_discussion_lti.is_configured,
            }
        }
        return Response(response_data, status=status.HTTP_200_OK)


class ProgramLiveIframeView(APIView, ProgramSpecificViewMixin):
    """
    A view for retrieving Program live IFrame .

    Path: ``/dashboard/programs/{program_uuid}/live/``

    Accepts: [GET]

    ------------------------------------------------------------------------------------
    GET
    ------------------------------------------------------------------------------------

    **Returns**

        * 200: OK - Contains a program live zoom iframe.
        * 401: The requesting user is not authenticated.
        * 403: The requesting user lacks access to the program.
        * 404: The requested program does not exist.

    **Response**

        In the case of a 200 response code, the response will be iframe HTML and status if discussion is configured
        for the program.

    **Example**

        {
            'tab_view_enabled': True,
            'live': {
                "iframe": "
                            <iframe
                                id='lti-tab-embed'
                                style='width: 100%; min-height: 800px; border: none'
                                srcdoc='{srcdoc}'
                             >
                            </iframe>
                            ",
                "configured": false
            }
        }

    """
    authentication_classes = (JwtAuthentication, BearerAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, program_uuid):
        """ GET handler """
        if not is_enrolled_or_staff(request, program_uuid):
            default_response = {
                'tab_view_enabled': False,
                'live': {
                    'configured': False,
                    'iframe': ''
                }
            }
            return Response(default_response, status=status.HTTP_200_OK)
        program_live_lti = ProgramLiveLTI(program_uuid, request)
        response_data = {
            'tab_view_enabled': masters_program_tab_view_is_enabled(),
            'live': {
                'iframe': program_live_lti.render_iframe(),
                'configured': program_live_lti.is_configured,
            }
        }
        return Response(response_data, status=status.HTTP_200_OK)
