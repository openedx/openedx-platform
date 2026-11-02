"""
Build JSON-serializable track selection page data for the Learning MFE.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Any, Optional

from babel.numbers import get_currency_symbol
from django.urls import reverse
from django.utils.translation import get_language
from opaque_keys.edx.keys import CourseKey
from openedx_filters.learning.filters import CourseModePriceRequested

from common.djangoapps.course_modes.filters import TrackSelectionAPIRenderStarted
from common.djangoapps.course_modes.helpers import get_verified_track_links
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.date_utils import strftime_localized_html
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.embargo import api as embargo_api
from openedx.core.djangoapps.enrollments.permissions import ENROLL_IN_COURSE
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.access import (
    get_user_course_duration,
    get_user_course_expiration_date,
)
from openedx.features.course_experience import course_home_url
from xmodule.modulestore.django import modulestore


@dataclass
class TrackSelectionRedirect:
    """Indicates the caller should redirect instead of rendering the MFE page."""

    url: str


@dataclass
class TrackSelectionSubmissionError:
    """Indicates track selection submission failed with a user-facing message."""

    error: str


def load_course_for_track_selection(course_key):
    """Modulestore first, then CourseOverview (same metadata as legacy track selection)."""
    course = modulestore().get_course(course_key)
    if course is not None:
        return course
    try:
        return CourseOverview.get_from_id(course_key)
    except CourseOverview.DoesNotExist:
        return None


def get_professional_mode_redirect(
    request,
    course_key: CourseKey,
    modes,
    enrollment_mode,
    is_active,
) -> Optional[TrackSelectionRedirect]:
    """Shared with ChooseModeView GET when professional is the only purchasable mode."""
    has_enrolled_professional = (
        CourseMode.is_professional_slug(enrollment_mode) and is_active
    )
    if not (CourseMode.has_professional_mode(modes) and not has_enrolled_professional):
        return None

    course_id = str(course_key)
    ecommerce_service = EcommerceService()
    redirect_url = IDVerificationService.get_verify_location(course_id=course_key)
    if ecommerce_service.is_enabled(request.user):
        professional_mode = modes.get(CourseMode.NO_ID_PROFESSIONAL_MODE) or modes.get(
            CourseMode.PROFESSIONAL
        )
        purchase_workflow = request.GET.get("purchase_workflow", "single")
        if purchase_workflow == "single" and professional_mode.sku:
            redirect_url = ecommerce_service.get_checkout_page_url(
                professional_mode.sku, course_run_keys=[course_id]
            )
        if purchase_workflow == "bulk" and professional_mode.bulk_sku:
            redirect_url = ecommerce_service.get_checkout_page_url(
                professional_mode.bulk_sku, course_run_keys=[course_id]
            )
    return TrackSelectionRedirect(url=redirect_url)


def _redirect_course_or_dashboard(course, course_key, user) -> TrackSelectionRedirect:
    """Match ChooseModeView._redirect_to_course_or_dashboard for the MFE API."""
    if course is None:
        return TrackSelectionRedirect(url=reverse("dashboard"))
    if course.has_started() or user.is_staff:
        return TrackSelectionRedirect(url=course_home_url(course_key))
    return TrackSelectionRedirect(url=reverse("dashboard"))


def _serialize_mode(mode) -> dict[str, Any]:
    """Serialize a course_modes.models.Mode namedtuple for the MFE."""
    return {
        "slug": mode.slug,
        "min_price": str(mode.min_price),
        "currency": mode.currency,
        "sku": mode.sku,
    }


def get_track_selection_page_data(
    request, course_id: str
) -> dict[str, Any] | TrackSelectionRedirect:
    """
    Return track selection data for the MFE, or a redirect when the LMS would not show the page.
    """
    course_key = CourseKey.from_string(course_id)
    if embargo_redirect := embargo_api.redirect_if_blocked(request, course_key):
        return TrackSelectionRedirect(url=embargo_redirect)

    enrollment_mode, is_active = CourseEnrollment.enrollment_mode_for_user(
        request.user, course_key
    )
    modes = CourseMode.modes_for_course_dict(course_key)

    if prof_redirect := get_professional_mode_redirect(
        request, course_key, modes, enrollment_mode, is_active
    ):
        return prof_redirect

    course = load_course_for_track_selection(course_key)
    if course is None:
        return TrackSelectionRedirect(
            url=reverse("course_modes_choose", kwargs={"course_id": course_id})
        )

    if not CourseMode.has_verified_mode(modes):
        return _redirect_course_or_dashboard(course, course_key, request.user)

    if is_active and enrollment_mode in CourseMode.VERIFIED_MODES + [
        CourseMode.NO_ID_PROFESSIONAL_MODE
    ]:
        return _redirect_course_or_dashboard(course, course_key, request.user)

    if CourseEnrollment.is_enrollment_closed(request.user, course):
        return TrackSelectionRedirect(url=reverse("dashboard"))

    if not request.user.has_perm(ENROLL_IN_COURSE, course):
        return {
            "error": "Enrollment is closed",
            "course_id": course_id,
        }

    gated_content = ContentTypeGatingConfig.enabled_for_enrollment(
        user=request.user,
        course_key=course_key,
    )
    duration = get_user_course_duration(request.user, course)
    deadline = duration and get_user_course_expiration_date(request.user, course)
    audit_access_deadline = (
        strftime_localized_html(deadline, "SHORT_DATE") if deadline else None
    )
    fbe_is_on = bool(deadline and gated_content)

    ecommerce_service = EcommerceService()
    verified_payload = None
    if CourseMode.VERIFIED in modes:
        verified_mode = modes[CourseMode.VERIFIED]
        price_before_discount = verified_mode.min_price
        # this filter applies discounts (e.g. enterprise-negotiated pricing) to the price
        # .. filter_implemented_name: CourseModePriceRequested
        # .. filter_type: org.openedx.learning.course_mode.price.requested.v1
        _, _, course_price = CourseModePriceRequested.run_filter(
            user=request.user,
            course_mode_data=verified_mode,
            price=price_before_discount,
        )

        verified_payload = {
            **_serialize_mode(verified_mode),
            "min_price": str(course_price),
            "currency_symbol": get_currency_symbol(verified_mode.currency.upper()),
            "use_ecommerce_payment_flow": bool(
                verified_mode.sku and ecommerce_service.is_enabled(request.user)
            ),
            "ecommerce_payment_page": ecommerce_service.payment_page_url(),
        }

    audit_payload = None
    if CourseMode.AUDIT in modes:
        audit_payload = _serialize_mode(modes[CourseMode.AUDIT])
    elif CourseMode.HONOR in modes:
        audit_payload = _serialize_mode(modes[CourseMode.HONOR])

    track_selection_data = {
        "course_id": course_id,
        "course_name": course.display_name_with_default,
        "course_org": course.display_org_with_default,
        "course_num": course.display_number_with_default,
        "fbe_is_on": fbe_is_on,
        "audit_access_deadline": audit_access_deadline,
        "track_links": get_verified_track_links(get_language()),
        "verified_mode": verified_payload,
        "audit_mode": audit_payload,
    }
    # this filter lets plugins add fields to the page data
    # .. filter_implemented_name: TrackSelectionAPIRenderStarted
    # .. filter_type: org.openedx.learning.course_mode.track_selection.api.rendered.started.v1
    return TrackSelectionAPIRenderStarted.run_filter(
        user=request.user,
        course_key=course_key,
        track_selection_data=track_selection_data,
    )


def submit_track_selection_choice(
    request,
    course_id: str,
    mode: str,
    contribution=None,
) -> TrackSelectionRedirect | TrackSelectionSubmissionError:
    """
    Process a track selection choice from the Learning MFE.

    Mirrors ChooseModeView.post so the MFE can submit via the BFF API.
    """
    course_key = CourseKey.from_string(course_id)
    user = request.user
    course = load_course_for_track_selection(course_key)

    if course is None or not user.has_perm(ENROLL_IN_COURSE, course):
        return TrackSelectionSubmissionError(error="Enrollment is closed")

    allowed_modes = CourseMode.modes_for_course_dict(course_key)
    requested_mode = mode

    if requested_mode not in allowed_modes:
        return TrackSelectionSubmissionError(error="Enrollment mode not supported")

    if requested_mode == CourseMode.AUDIT:
        CourseEnrollment.enroll(request.user, course_key, CourseMode.AUDIT)
        return _redirect_course_or_dashboard(course, course_key, user)

    if requested_mode == CourseMode.HONOR:
        CourseEnrollment.enroll(user, course_key, mode=CourseMode.HONOR)
        return _redirect_course_or_dashboard(course, course_key, user)

    if requested_mode == CourseMode.VERIFIED:
        amount = contribution or 0
        try:
            amount_value = decimal.Decimal(str(amount)).quantize(
                decimal.Decimal(".01"),
                rounding=decimal.ROUND_DOWN,
            )
        except decimal.InvalidOperation:
            return TrackSelectionSubmissionError(error="Invalid amount selected.")

        mode_info = allowed_modes[requested_mode]
        if amount_value < mode_info.min_price:
            return TrackSelectionSubmissionError(
                error="No selected price or selected price is too low."
            )

        donation_for_course = request.session.get("donation_for_course", {})
        donation_for_course[str(course_key)] = amount_value
        request.session["donation_for_course"] = donation_for_course

        verify_url = IDVerificationService.get_verify_location(course_id=course_key)
        return TrackSelectionRedirect(url=verify_url)

    return TrackSelectionSubmissionError(error="Enrollment mode not supported")
