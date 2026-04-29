"""
Fake Software Secure page for use in acceptance tests.
"""


from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic.base import View

from common.djangoapps.edxmako.shortcuts import render_to_response
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification


class SoftwareSecureFakeView(View):
    """
    Fake SoftwareSecure view for testing different photo verification statuses
    and email functionality.
    """

    @method_decorator(login_required)
    def get(self, request):
        """
        Render a fake Software Secure page that will pick the most recent
        attempt for a given user and pass it to the html page.
        """
        context_dict = self.response_post_params(request.user)
        return render_to_response("verify_student/test/fake_softwaresecure_response.html", context_dict)

    @classmethod
    def response_post_params(cls, user):
        """
        Calculate the POST params we want to send back to the client.
        """
        access_key = settings.VERIFY_STUDENT["SOFTWARE_SECURE"]["API_ACCESS_KEY"]
        context = {
            'receipt_id': None,
            'authorization_code': f'SIS {access_key}:0000',
            'results_callback': reverse('verify_student_results_callback')
        }

        try:
            most_recent = SoftwareSecurePhotoVerification.objects.filter(user=user).order_by("-updated_at")[0]
            context["receipt_id"] = most_recent.receipt_id
        except:  # noqa: E722
            pass

        return context
