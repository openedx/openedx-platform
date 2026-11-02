"""
Utility functions for working with discounts and discounted pricing.
"""

from datetime import datetime

from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from edx_django_utils.plugins import pluggable_override

from common.djangoapps.course_modes.models import format_course_price, get_course_prices
from lms.djangoapps.experiments.models import ExperimentData
from lms.djangoapps.courseware.utils import verified_upgrade_deadline_link
from openedx.core.djangolib.markup import HTML
from openedx.features.discounts.applicability import (
    FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG,
    REV1008_EXPERIMENT_ID,
    can_receive_discount,
    discount_percentage,
    get_discount_expiration_date
)


def offer_banner_wrapper(user, block, view, frag, context):  # pylint: disable=W0613
    """
    A wrapper that prepends the First Purchase Discount banner if
    the user hasn't upgraded yet.
    """
    if block.category != 'vertical':
        return frag

    offer_banner_fragment = None

    if not offer_banner_fragment:
        return frag

    # Course content must be escaped to render correctly due to the way the
    # way the XBlock rendering works. Transforming the safe markup to unicode
    # escapes correctly.
    offer_banner_fragment.content = str(offer_banner_fragment.content)

    offer_banner_fragment.add_content(frag.content)
    offer_banner_fragment.add_fragment_resources(frag)

    return offer_banner_fragment


def _get_discount_prices(user, course, assume_discount=False):
    """
    Return a tuple of (original, discounted, percentage)

    If assume_discount is True, we do not check if a discount applies and just go ahead with discount math anyway.

    Each returned price is a string with appropriate currency formatting added already.
    discounted and percentage will be returned as None if no discount is applicable.
    """
    base_price = get_course_prices(course, verified_only=True)[0]
    can_discount = assume_discount or can_receive_discount(user, course)

    if can_discount:
        percentage = discount_percentage(course)

        discounted_price = base_price * ((100.0 - percentage) / 100)
        if discounted_price:  # leave 0 prices alone, as format_course_price below will adjust to 'Free'
            if discounted_price == int(discounted_price):
                discounted_price = f'{discounted_price:0.0f}'
            else:
                discounted_price = f'{discounted_price:0.2f}'

        return format_course_price(base_price), format_course_price(discounted_price), percentage
    else:
        return format_course_price(base_price), None, None


@pluggable_override("OVERRIDE_GENERATE_OFFER_DATA")
def generate_offer_data(user, course):
    """
    Create a dictionary of information about the current discount offer.

    Used by serializers to pass onto frontends and by the LMS locally to generate HTML for template rendering.

    Returns a dictionary of data, or None if no offer is applicable.
    """
    if not user or not course or user.is_anonymous:
        return None

    ExperimentData.objects.get_or_create(
        user=user, experiment_id=REV1008_EXPERIMENT_ID, key=str(course),
        defaults={
            'value': datetime.now(tz=ZoneInfo("UTC")).strftime('%Y-%m-%d %H:%M:%S%z'),
        },
    )

    expiration_date = get_discount_expiration_date(user, course)
    if not expiration_date:
        return None

    if not can_receive_discount(user, course, discount_expiration_date=expiration_date):
        return None

    original, discounted, percentage = _get_discount_prices(user, course, assume_discount=True)

    # Override the First Purchase Discount to another code only if flag is enabled
    first_purchase_discount_code = 'BIENVENIDOAEDX' if get_language() == 'es-419' else 'EDXWELCOME'
    if FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG.is_enabled():
        first_purchase_discount_code = getattr(
            settings,
            'FIRST_PURCHASE_DISCOUNT_OVERRIDE_CODE',
            first_purchase_discount_code
        )

    return {
        'code': first_purchase_discount_code,
        'expiration_date': expiration_date,
        'original_price': original,
        'discounted_price': discounted,
        'percentage': percentage,
        'upgrade_url': verified_upgrade_deadline_link(user, course=course),
    }


def _format_discounted_price(original_price, discount_price):
    """Helper method that returns HTML containing a strikeout price with discount."""
    # Separate out this string because it has a lot of syntax but no actual information for
    # translators to translate
    formatted_discount_price = HTML(
        '{s_dp}{discount_price}{e_p} {s_st}{s_op}{original_price}{e_p}{e_st}'
    ).format(
        original_price=original_price,
        discount_price=discount_price,
        s_op=HTML("<span class='price original'>"),
        s_dp=HTML("<span class='price discount'>"),
        s_st=HTML("<del aria-hidden='true'>"),
        e_p=HTML('</span>'),
        e_st=HTML('</del>'),
    )

    return (
        HTML(_(
            '{s_sr}Original price: {s_op}{original_price}{e_p}, discount price: {e_sr}{formatted_discount_price}'
        )).format(
            original_price=original_price,
            formatted_discount_price=formatted_discount_price,
            s_sr=HTML("<span class='sr-only'>"),
            s_op=HTML("<span class='price original'>"),
            e_p=HTML('</span>'),
            e_sr=HTML('</span>'),
        )
    )


def format_strikeout_price(user, course):
    """
    Return a formatted price, including a struck-out original price if a discount applies, and also
        whether a discount was applied, as the tuple (formatted_price, has_discount).
    """
    original_price, discounted_price, _ = _get_discount_prices(user, course)

    if discounted_price is None:
        return HTML("<span class='price'>{}</span>").format(original_price), False
    else:
        return _format_discounted_price(original_price, discounted_price), True
