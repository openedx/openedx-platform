"""
The Discount API Views should return information about discounts that apply to the user and course.

"""
from django.http import HttpResponseBadRequest  # noqa: I001
from django.utils.decorators import method_decorator
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_toggles.toggles import WaffleFlag
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.course_modes.models import get_cosmetic_verified_display_price
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.track import segment
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.courseware.utils import can_show_verified_upgrade
from lms.djangoapps.experiments.stable_bucketing import stable_bucketing_hash_group
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.cors_csrf.decorators import ensure_csrf_cookie_cross_domain
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.permissions import ApiKeyHeaderPermissionIsAuthenticated
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin

# .. toggle_name: experiments.mobile_upsell_rev934
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Toggle mobile upsell enabled
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2019-09-05
# .. toggle_target_removal_date: None
# .. toggle_tickets: REV-934
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
MOBILE_UPSELL_FLAG = WaffleFlag('experiments.mobile_upsell_rev934', __name__)
MOBILE_UPSELL_EXPERIMENT = 'mobile_upsell_experiment'


class Rev934(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Request upsell information for mobile app users

    **Example Requests**

        GET /api/experiments/v0/custom/REV-934/?course_id={course_key_string}

    **Response Values**

        Body consists of the following fields:
            show_upsell:
                whether to show upsell in the moble app in this case
            price:
                (optional) the price to show if show_upsell is true
            basket_url:
                (optional) the url to the checkout page with the course's sku if show_upsell is true
            upsell_flag:
                (optional) false if the upsell flag is off, not present otherwise

        Response:
            {
            "show_upsell": true,
            "price": "$199",
            "basket_url": "https://ecommerce.edx.org/basket/add?sku=abcdef"
            }

    **Parameters:**

        course_key_string:
            The course key that may be upsold

    **Returns**

        * 200 on success with above fields.
        * 401 if there is no user signed in.

        Example response:
        {
            "show_upsell": true,
            "price": "$199",
            "basket_url": "https://ecommerce.edx.org/basket/add?sku=abcdef"
        }
    """
    # https://courses.stage.edx.org/api/experiments/v0/custom/REV-934/?course_id=course-v1%3AedX%2BDemoX%2BDemo_Course

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)

    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request):
        """
        Return the if the course should be upsold in the mobile app, if the user has appropriate permissions.
        """
        if not MOBILE_UPSELL_FLAG.is_enabled():
            return Response({
                'show_upsell': False,
                'upsell_flag': False,
            })

        course_id = request.GET.get('course_id')
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return HttpResponseBadRequest("Missing or invalid course_id")

        course = CourseOverview.get_from_id(course_key)
        if not course.has_started() or course.has_ended():
            return Response({
                'show_upsell': False,
                'upsell_flag': MOBILE_UPSELL_FLAG.is_enabled(),
                'course_running': False,
            })

        user = request.user
        try:
            enrollment = CourseEnrollment.objects.select_related(
                'course'
            ).get(user_id=user.id, course_id=course.id)
            user_upsell = can_show_verified_upgrade(user, enrollment)
        except CourseEnrollment.DoesNotExist:
            user_upsell = True

        basket_url = EcommerceService().upgrade_url(user, course.id)
        upgrade_price = str(get_cosmetic_verified_display_price(course))
        could_upsell = bool(user_upsell and basket_url)

        bucket = stable_bucketing_hash_group(MOBILE_UPSELL_EXPERIMENT, 2, user)

        if could_upsell and hasattr(request, 'session') and MOBILE_UPSELL_EXPERIMENT not in request.session:
            properties = {
                'site': request.site.domain,
                'app_label': 'experiments',
                'bucket': bucket,
                'experiment': 'REV-934',
            }
            segment.track(
                user_id=user.id,
                event_name='edx.bi.experiment.user.bucketed',
                properties=properties,
            )

            # Mark that we've recorded this bucketing, so that we don't do it again this session
            request.session[MOBILE_UPSELL_EXPERIMENT] = True

        show_upsell = bool(bucket != 0 and could_upsell)
        if show_upsell:
            return Response({
                'show_upsell': show_upsell,
                'price': upgrade_price,
                'basket_url': basket_url,
            })
        else:
            return Response({
                'show_upsell': show_upsell,
                'upsell_flag': MOBILE_UPSELL_FLAG.is_enabled(),
                'experiment_bucket': bucket,
                'user_upsell': user_upsell,
                'basket_url': basket_url,
            })
