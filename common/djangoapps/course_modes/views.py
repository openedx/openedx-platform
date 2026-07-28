"""
Views for the course_mode module
"""


import decimal
import json
import logging
from urllib.parse import urljoin  # pylint: disable=wrong-import-order

import six
import waffle  # pylint: disable=invalid-django-waffle-import
from babel.dates import format_datetime
from babel.numbers import get_currency_symbol
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import get_language, to_locale
from django.utils.translation import gettext as _
from django.views.generic.base import View
from edx_django_utils.monitoring.utils import increment
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.helpers import get_course_final_price, get_verified_track_links
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.date_utils import strftime_localized_html
from common.djangoapps.util.db import outer_atomic
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.experiments.utils import get_experiment_user_metadata_context
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.catalog.utils import get_currency_data
from openedx.core.djangoapps.embargo import api as embargo_api
from openedx.core.djangoapps.enrollments.permissions import ENROLL_IN_COURSE
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.access import get_user_course_duration, get_user_course_expiration_date
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience import course_home_url
from openedx.features.enterprise_support.api import enterprise_customer_for_request
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

LOG = logging.getLogger(__name__)


class ChooseModeView(View):
    """View used when the user is asked to pick a mode.

    When a get request is used, shows the selection page.

    When a post request is used, assumes that it is a form submission
    from the selection page, parses the response, and then sends user
    to the next step in the flow.

    """

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, *args, **kwargs):
        """Disable atomicity for the view.

        Otherwise, we'd be unable to commit to the database until the
        request had concluded; Django will refuse to commit when an
        atomic() block is active, since that would break atomicity.

        """
        return super().dispatch(*args, **kwargs)

    @method_decorator(login_required)
    @method_decorator(transaction.atomic)
    def get(self, request, course_id, error=None):  # pylint: disable=too-many-statements
        """Displays the course mode choice page.

        Args:
            request (`Request`): The Django Request object.
            course_id (unicode): The slash-separated course key.

        Keyword Args:
            error (unicode): If provided, display this error message
                on the page.

        Returns:
            Response

        """
        course_key = CourseKey.from_string(course_id)

        # Check whether the user has access to this course
        # based on country access rules.
        embargo_redirect = embargo_api.redirect_if_blocked(request, course_key)
        if embargo_redirect:
            return redirect(embargo_redirect)

        enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(request.user, course_key)

        increment('track-selection.{}.{}'.format(enrollment_mode, 'active' if is_active else 'inactive'))
        increment('track-selection.views')

        if enrollment_mode is None:
            LOG.info('Rendering track selection for unenrolled user, referred by %s', request.META.get('HTTP_REFERER'))

        modes = CourseMode.modes_for_course_dict(course_key)
        ecommerce_service = EcommerceService()

        # We assume that, if 'professional' is one of the modes, it should be the *only* mode.
        # If there are both modes, default to 'no-id-professional'.
        has_enrolled_professional = (CourseMode.is_professional_slug(enrollment_mode) and is_active)
        if CourseMode.has_professional_mode(modes) and not has_enrolled_professional:
            purchase_workflow = request.GET.get("purchase_workflow", "single")
            redirect_url = IDVerificationService.get_verify_location(course_id=course_key)
            if ecommerce_service.is_enabled(request.user):
                professional_mode = modes.get(CourseMode.NO_ID_PROFESSIONAL_MODE) or modes.get(CourseMode.PROFESSIONAL)
                if purchase_workflow == "single" and professional_mode.sku:
                    redirect_url = ecommerce_service.get_checkout_page_url(
                        professional_mode.sku, course_run_keys=[course_id]
                    )
                if purchase_workflow == "bulk" and professional_mode.bulk_sku:
                    redirect_url = ecommerce_service.get_checkout_page_url(
                        professional_mode.bulk_sku, course_run_keys=[course_id]
                    )
            return redirect(redirect_url)
        course = modulestore().get_course(course_key)

        # If there isn't a verified mode available, then there's nothing
        # to do on this page.  Send the user to the dashboard.
        if not CourseMode.has_verified_mode(modes):
            return self._redirect_to_course_or_dashboard(course, course_key, request.user)

        # If a user has already paid, redirect them to the dashboard.
        if is_active and (enrollment_mode in CourseMode.VERIFIED_MODES + [CourseMode.NO_ID_PROFESSIONAL_MODE]):
            return self._redirect_to_course_or_dashboard(course, course_key, request.user)

        donation_for_course = request.session.get("donation_for_course", {})
        chosen_price = donation_for_course.get(str(course_key), None)

        if CourseEnrollment.is_enrollment_closed(request.user, course):
            locale = to_locale(get_language())
            enrollment_end_date = format_datetime(course.enrollment_end, 'short', locale=locale)
            params = six.moves.urllib.parse.urlencode({'course_closed': enrollment_end_date})
            LOG.info(
                '[Track Selection Check] Enrollment is closed redirect for course [%s], user [%s]',
                course_id, request.user.username
            )
            return redirect('{}?{}'.format(reverse('dashboard'), params))

        course_id = str(course_key)
        gated_content = ContentTypeGatingConfig.enabled_for_enrollment(
            user=request.user,
            course_key=course_key
        )
        is_single_mode = len(modes) == 1

        context = {
            "course_modes_choose_url": reverse(
                "course_modes_choose",
                kwargs={'course_id': course_id}
            ),
            "modes": modes,
            "is_single_mode": is_single_mode,
            "course_name": course.display_name_with_default,
            "course_org": course.display_org_with_default,
            "course_num": course.display_number_with_default,
            "chosen_price": chosen_price,
            "error": error,
            "responsive": True,
            "nav_hidden": True,
            "content_gating_enabled": gated_content,
            "course_duration_limit_enabled": CourseDurationLimitConfig.enabled_for_enrollment(request.user, course),
            "search_courses_url": urljoin(settings.MKTG_URLS.get('ROOT'), '/search?tab=course'),
            "course_run_key": course_id,
        }
        context.update(
            get_experiment_user_metadata_context(
                course,
                request.user,
            )
        )

        if "verified" in modes:
            verified_mode = modes["verified"]
            context["suggested_prices"] = [
                decimal.Decimal(x.strip())
                for x in verified_mode.suggested_prices.split(",")
                if x.strip()
            ]
            price_before_discount = verified_mode.min_price
            course_price = price_before_discount
            enterprise_customer = enterprise_customer_for_request(request)
            LOG.info(
                '[e-commerce calculate API] Going to hit the API for user [%s] linked to [%s] enterprise',
                request.user.username,
                enterprise_customer.get('name') if isinstance(enterprise_customer, dict) else None  # Test Purpose
            )
            if enterprise_customer and verified_mode.sku:
                course_price = get_course_final_price(request.user, verified_mode.sku, price_before_discount)

            context["currency"] = verified_mode.currency.upper()
            context["currency_symbol"] = get_currency_symbol(verified_mode.currency.upper())
            context["min_price"] = course_price
            context["verified_name"] = verified_mode.name
            context["verified_description"] = verified_mode.description
            # if course_price is equal to price_before_discount then user doesn't entitle to any discount.
            if course_price != price_before_discount:
                context["price_before_discount"] = price_before_discount

            if verified_mode.sku:
                context["use_ecommerce_payment_flow"] = ecommerce_service.is_enabled(request.user)
                context["ecommerce_payment_page"] = ecommerce_service.payment_page_url()
                context["sku"] = verified_mode.sku
                context["bulk_sku"] = verified_mode.bulk_sku

        # REV-2415 TODO: remove [Track Selection Check] logs introduced by REV-2355 for error handling check
        context['currency_data'] = []
        if waffle.switch_is_active('local_currency'):
            if 'edx-price-l10n' not in request.COOKIES:
                currency_data = get_currency_data()
                LOG.info('[Track Selection Check] Currency data: [%s], for course [%s]', currency_data, course_id)
                try:
                    context['currency_data'] = json.dumps(currency_data)
                except TypeError:
                    pass

        language = get_language()
        context['track_links'] = get_verified_track_links(language)

        duration = get_user_course_duration(request.user, course)
        deadline = duration and get_user_course_expiration_date(request.user, course)
        if deadline:
            formatted_audit_access_date = strftime_localized_html(deadline, 'SHORT_DATE')
            context['audit_access_deadline'] = formatted_audit_access_date
        fbe_is_on = deadline and gated_content

        if error:
            return render_to_response("course_modes/error.html", context)
        if fbe_is_on:
            return render_to_response("course_modes/fbe.html", context)
        else:
            return render_to_response("course_modes/unfbe.html", context)

    @method_decorator(transaction.non_atomic_requests)
    @method_decorator(login_required)
    @method_decorator(outer_atomic())
    def post(self, request, course_id):
        """Takes the form submission from the page and parses it.

        Args:
            request (`Request`): The Django Request object.
            course_id (unicode): The slash-separated course key.

        Returns:
            When the requested mode is unsupported, returns error message.
            When the honor mode is selected, redirects to the dashboard.
            When the verified mode is selected, returns error messages
            if the indicated contribution amount is invalid or
            below the minimum, otherwise redirects to the verification flow.

        """
        course_key = CourseKey.from_string(course_id)
        user = request.user

        # This is a bit redundant with logic in student.views.change_enrollment,
        # but I don't really have the time to refactor it more nicely and test.
        course = modulestore().get_course(course_key)
        if not user.has_perm(ENROLL_IN_COURSE, course):
            error_msg = _("Enrollment is closed")
            LOG.info(
                '[Track Selection Check] Error: [%s], for course [%s], user [%s]',
                error_msg, course_id, request.user.username
            )
            return self.get(request, course_id, error=error_msg)

        requested_mode = self._get_requested_mode(request.POST)

        allowed_modes = CourseMode.modes_for_course_dict(course_key)
        if requested_mode not in allowed_modes:
            LOG.info(
                '[Track Selection Check] Error: requested enrollment mode [%s] is not supported for course [%s]',
                requested_mode, course_id
            )
            error_msg = _("Enrollment mode not supported")
            return self.get(request, course_id, error=error_msg)

        if requested_mode == 'audit':
            # If the learner has arrived at this screen via the traditional enrollment workflow,
            # then they should already be enrolled in an audit mode for the course, assuming one has
            # been configured.  However, alternative enrollment workflows have been introduced into the
            # system, such as third-party discovery.  These workflows result in learners arriving
            # directly at this screen, and they will not necessarily be pre-enrolled in the audit mode.
            CourseEnrollment.enroll(request.user, course_key, CourseMode.AUDIT)
            return self._redirect_to_course_or_dashboard(course, course_key, user)

        if requested_mode == 'honor':
            CourseEnrollment.enroll(user, course_key, mode=requested_mode)
            return self._redirect_to_course_or_dashboard(course, course_key, user)

        mode_info = allowed_modes[requested_mode]

        if requested_mode == 'verified':
            amount = request.POST.get("contribution") or \
                request.POST.get("contribution-other-amt") or 0
            LOG.info(
                '[Track Selection Check][%s] Requested verified mode - '
                'contribution: [%s], contribution other amount: [%s]',
                course_id, request.POST.get("contribution"), request.POST.get("contribution-other-amt")
            )

            try:
                # Validate the amount passed in and force it into two digits
                amount_value = decimal.Decimal(amount).quantize(decimal.Decimal('.01'), rounding=decimal.ROUND_DOWN)
            except decimal.InvalidOperation:
                error_msg = _("Invalid amount selected.")
                LOG.info(
                    '[Track Selection Check][%s] Requested verified mode - Error: [%s]',
                    course_id, error_msg
                )
                return self.get(request, course_id, error=error_msg)

            # Check for minimum pricing
            if amount_value < mode_info.min_price:
                error_msg = _("No selected price or selected price is too low.")
                LOG.info(
                    '[Track Selection Check][%s] Requested verified mode - Error: '
                    'amount value [%s] is less than minimum price [%s]',
                    course_id, amount_value, mode_info.min_price
                )
                return self.get(request, course_id, error=error_msg)

            donation_for_course = request.session.get("donation_for_course", {})
            LOG.info(
                '[Track Selection Check] Donation for course [%s]: [%s], amount value: [%s]',
                course_id, donation_for_course, amount_value
            )
            donation_for_course[str(course_key)] = amount_value
            request.session["donation_for_course"] = donation_for_course

            verify_url = IDVerificationService.get_verify_location(course_id=course_key)
            return redirect(verify_url)

    def _get_requested_mode(self, request_dict):
        """Get the user's requested mode

        Args:
            request_dict (`QueryDict`): A dictionary-like object containing all given HTTP POST parameters.

        Returns:
            The course mode slug corresponding to the choice in the POST parameters,
            None if the choice in the POST parameters is missing or is an unsupported mode.

        """
        if 'verified_mode' in request_dict:
            return 'verified'
        if 'honor_mode' in request_dict:
            return 'honor'
        if 'audit_mode' in request_dict:
            return 'audit'
        else:
            return None

    def _redirect_to_course_or_dashboard(self, course, course_key, user):
        """Perform a redirect to the course if the user is able to access the course.

        If the user is not able to access the course, redirect the user to the dashboard.

        Args:
            course: modulestore object for course
            course_key: course_id converted to a course_key
            user: request.user, the current user for the request

        Returns:
            302 to the course if possible or the dashboard if not.
        """
        if course.has_started() or user.is_staff:
            return redirect(course_home_url(course_key))
        else:
            return redirect(reverse('dashboard'))


def create_mode(request, course_id):
    """Add a mode to the course corresponding to the given course ID.

    Only available when settings.FEATURES['MODE_CREATION_FOR_TESTING'] is True.

    Attempts to use the following querystring parameters from the request:
        `mode_slug` (str): The mode to add, either 'honor', 'verified', or 'professional'
        `mode_display_name` (str): Describes the new course mode
        `min_price` (int): The minimum price a user must pay to enroll in the new course mode
        `suggested_prices` (str): Comma-separated prices to suggest to the user.
        `currency` (str): The currency in which to list prices.
        `sku` (str): The product SKU value.

    By default, this endpoint will create an 'honor' mode for the given course with display name
    'Honor Code', a minimum price of 0, no suggested prices, and using USD as the currency.

    Args:
        request (`Request`): The Django Request object.
        course_id (unicode): A course ID.

    Returns:
        Response
    """
    PARAMETERS = {
        'mode_slug': 'honor',
        'mode_display_name': 'Honor Code Certificate',
        'min_price': 0,
        'suggested_prices': '',
        'currency': 'usd',
        'sku': None,
    }

    # Try pulling querystring parameters out of the request
    for parameter, default in PARAMETERS.items():
        PARAMETERS[parameter] = request.GET.get(parameter, default)

    # Attempt to create the new mode for the given course
    course_key = CourseKey.from_string(course_id)
    CourseMode.objects.get_or_create(course_id=course_key, **PARAMETERS)

    # Return a success message and a 200 response
    return HttpResponse("Mode '{mode_slug}' created for '{course}'.".format(
        mode_slug=PARAMETERS['mode_slug'],
        course=course_id
    ))
