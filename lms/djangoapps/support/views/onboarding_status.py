"""
Views for Onboarding Status.
"""

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db.models import Q
from django.utils.decorators import method_decorator
from edx_proctoring.statuses import ProctoredExamStudentAttemptStatus
from edx_proctoring.views import StudentOnboardingStatusView
from rest_framework.generics import GenericAPIView

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.support.decorators import require_support_permission
from openedx.core.djangoapps.enrollments.api import get_enrollments


class OnboardingView(GenericAPIView):
    """
    Return most recent and originally verified onboarding exam status for a given user.
    Return 404 is user not found.
    """
    @method_decorator(require_support_permission)
    def get(self, request, username_or_email):
        """
        * Example Request:
            - GET /support/onboarding_status/<username_or_email>

        * Example Response:
            {
                "verified_in": {
                    "onboarding_status": "verified",
                    "onboarding_link": "/courses/<course_id>/jump_to/<block_id>",
                    "expiration_date": null,
                    "onboarding_past_due": false,
                    "onboarding_release_date": "2016-01-01T00:00:00+00:00",
                    "review_requirements_url": "",
                    "course_id": "<course_id>",
                    "enrollment_date": "2021-12-29T14:30:18.895435Z",
                    "instructor_dashboard_link": "/courses/<course_id>/instructor#view-special_exams"
                },
                "current_status": {
                    "onboarding_status": "other_course_approved",
                    "onboarding_link": "/courses/<course_id>/jump_to/<block_id>",
                    "expiration_date": "2023-12-29T15:52:28.245Z",
                    "onboarding_past_due": false,
                    "onboarding_release_date": "2020-01-01T00:00:00+00:00",
                    "review_requirements_url": "",
                    "course_id": "<course_id>",
                    "enrollment_date": "2021-12-29T15:58:29.489916Z",
                    "instructor_dashboard_link": "/courses/<course_id>/instructor#view-special_exams"
                }
            }
        """
        # return dict
        onboarding_status = {
            'verified_in': None,
            'current_status': None
        }

        # make object mutable
        request.GET = request.GET.copy()

        try:
            user = User.objects.get(Q(username=username_or_email) | Q(email=username_or_email))
        except User.DoesNotExist:
            return JsonResponse(onboarding_status, status=404)

        request.GET['username'] = user.username
        enrollments = get_enrollments(user.username)

        enrollments = sorted(enrollments, key=lambda enrollment: enrollment['created'], reverse=True)
        enrollments = filter(
            lambda enrollment: enrollment['mode'] in [CourseMode.VERIFIED, CourseMode.PROFESSIONAL],
            enrollments
        )

        for enrollment in enrollments:
            request.GET['course_id'] = enrollment['course_details']['course_id']

            status = StudentOnboardingStatusView().get(request).data

            if 'onboarding_status' in status:
                status['course_id'] = enrollment['course_details']['course_id']
                status['enrollment_date'] = enrollment['created']
                status['instructor_dashboard_link'] = \
                    '/courses/{}/instructor#view-special_exams'.format(status['course_id'])

                # set most recent status only at first iteration
                if onboarding_status['current_status'] is None:
                    onboarding_status['current_status'] = status

                # stay in loop to find original verified enrollment. Expensive!
                if status['onboarding_status'] == ProctoredExamStudentAttemptStatus.verified:
                    onboarding_status['verified_in'] = status
                    break

        return JsonResponse(onboarding_status)
