"""
Support tool for changing and granting course entitlements
"""


from django.utils.decorators import method_decorator  # noqa: I001
from django.views.generic import View

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.entitlements.models import CourseEntitlementSupportDetail
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.support.decorators import require_support_permission

REQUIRED_CREATION_FIELDS = ['course_uuid', 'reason', 'mode']


class EntitlementSupportView(View):
    """
    View for viewing and changing learner enrollments, used by the
    support team.
    """
    @method_decorator(require_support_permission)
    def get(self, request):
        """Render the enrollment support tool view."""
        support_actions = CourseEntitlementSupportDetail.get_support_actions_list()

        ecommerce_url = EcommerceService().get_order_dashboard_url()
        context = {
            'username': request.GET.get('user', ''),
            'uses_bootstrap': True,
            'ecommerce_url': ecommerce_url,
            'support_actions': support_actions
        }
        return render_to_response('support/entitlement.html', context)
