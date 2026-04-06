"""
Support tool for viewing course duration information
"""

from django.utils.decorators import method_decorator
from django.views.generic import View
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.support.decorators import require_support_permission
from lms.djangoapps.support.views.utils import get_course_duration_info


class FeatureBasedEnrollmentsSupportView(View):
    """
    View for listing course duration settings for
    support team.
    """
    @method_decorator(require_support_permission)
    def get(self, request):
        """
        Render the course duration tool view.
        """
        course_key = request.GET.get('course_key', '')

        if course_key:
            results = get_course_duration_info(course_key)
        else:
            results = {}

        return render_to_response('support/feature_based_enrollments.html', {
            'course_key': course_key,
            'results': results,
        })


class FeatureBasedEnrollmentSupportAPIView(GenericAPIView):
    """
    Support-only API View for getting feature based enrollment configuration details
    for a course.
    """
    permission_classes = (IsAuthenticated,)

    @method_decorator(require_support_permission)
    def get(self, request, course_id):
        """
        Returns the duration config information if FBE is enabled. If
        FBE is not enabled, empty dict is returned.

        * Example Request:
            - GET /support/feature_based_enrollment_details/<course_id>

        * Example Response:
            {
              "course_id": <course_id>,
              "course_name": "FBE course",
              "gating_config": {
                "enabled": true,
                "enabled_as_of": "2030-01-01 00:00:00+00:00",
                "reason": "Site"
              },
              "duration_config": {
                "enabled": true,
                "enabled_as_of": "2030-01-01 00:00:00+00:00",
                "reason": "Site"
              }
            }
        """
        return JsonResponse(get_course_duration_info(course_id))
