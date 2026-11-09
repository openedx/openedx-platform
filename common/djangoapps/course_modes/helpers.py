""" Helper methods for CourseModes. """


import logging
from urllib.parse import urljoin

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from requests.exceptions import RequestException

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.commerce.utils import get_ecommerce_api_base_url, get_ecommerce_api_client

DISPLAY_VERIFIED = "verified"
DISPLAY_HONOR = "honor"
DISPLAY_AUDIT = "audit"
DISPLAY_PROFESSIONAL = "professional"

LOGGER = logging.getLogger(__name__)


def enrollment_mode_display(mode, course_id):
    """ Select appropriate display strings and CSS classes.

        Uses mode and verification status to select appropriate display strings and CSS classes
        for certificate display.

        Args:
            mode (str): enrollment mode.
            verification_status (str) : verification status of student

        Returns:
            dictionary:
    """
    show_image = False
    image_alt = ''
    enrollment_title = ''
    enrollment_value = ''
    display_mode = _enrollment_mode_display(mode, course_id)

    if display_mode == DISPLAY_VERIFIED:
        enrollment_title = _("You're enrolled as a verified student")
        enrollment_value = _("Verified")
        show_image = True
        image_alt = _("ID Verified Ribbon/Badge")
    elif display_mode == DISPLAY_HONOR:
        enrollment_title = _("You're enrolled as an honor code student")
        enrollment_value = _("Honor Code")
    elif display_mode == DISPLAY_PROFESSIONAL:
        enrollment_title = _("You're enrolled as a professional education student")
        enrollment_value = _("Professional Ed")

    return {
        'enrollment_title': str(enrollment_title),
        'enrollment_value': str(enrollment_value),
        'show_image': show_image,
        'image_alt': str(image_alt),
        'display_mode': display_mode
    }


def _enrollment_mode_display(enrollment_mode, course_id):
    """Checking enrollment mode and status and returns the display mode
     Args:
        enrollment_mode (str): enrollment mode.
        verification_status (str) : verification status of student

    Returns:
        display_mode (str) : display mode for certs
    """
    course_mode_slugs = [mode.slug for mode in CourseMode.modes_for_course(course_id)]

    if enrollment_mode == CourseMode.VERIFIED:
        display_mode = DISPLAY_VERIFIED
        if DISPLAY_HONOR in course_mode_slugs:
            display_mode = DISPLAY_HONOR
    elif enrollment_mode in [CourseMode.PROFESSIONAL, CourseMode.NO_ID_PROFESSIONAL_MODE]:
        display_mode = DISPLAY_PROFESSIONAL
    else:
        display_mode = enrollment_mode

    return display_mode


def get_course_final_price(user, sku, course_price):
    """
    Return the course's discounted price for a user (if eligible), otherwise return course original price.

    Note: this function is no longer called directly within edx-platform. It is called externally
    via the openedx-filters pipeline, by edx-enterprise's CalculateEnterpriseDiscountedPrice
    step (org.openedx.learning.course_mode.price.requested.v1). Do not delete this function even
    though static analysis may flag it as unused here.
    """
    price_details = {}
    try:
        api_url = urljoin(f"{get_ecommerce_api_base_url()}/", "baskets/calculate/")
        response = get_ecommerce_api_client(user).get(
            api_url,
            params={
                "sku": [sku],
                "username": user.username,
            }
        )
        response.raise_for_status()
        price_details = response.json()
    except RequestException as exc:
        LOGGER.info(
            '[e-commerce calculate endpoint] Exception raise for sku [%s] - user [%s] and exception: %s',
            sku,
            user.username,
            str(exc)
        )

    LOGGER.info(
        '[e-commerce calculate endpoint] The discounted price for sku [%s] and user [%s] is [%s]',
        sku,
        user.username,
        price_details.get('total_incl_tax')
    )
    result = price_details.get('total_incl_tax', course_price)

    # When ecommerce price has zero cents, 'result' gets 149.0
    # As per REV-2260: if zero cents, then only show dollars
    if int(result) == result:
        result = int(result)

    return result


def get_verified_track_links(language):
    """
    Format the URL's for Value Prop's Track Selection verified option, for the specified language.

    Arguments:
        language (str): The language from the user's account settings.

    Returns: dict
        Dictionary with URL's with verified certificate informational links.
        If not edx.org, returns a dictionary with default URL's.
    """
    support_root_url = settings.SUPPORT_SITE_LINK
    marketing_root_url = settings.MKTG_URLS.get('ROOT')

    enabled_languages = {
        'en': 'hc/en-us',
        'es-419': 'hc/es-419',
    }

    # Add edX specific links only to edx.org
    if marketing_root_url and 'edx.org' in marketing_root_url:
        track_verified_url = urljoin(marketing_root_url, 'verified-certificate')
        if support_root_url and 'support.edx.org' in support_root_url:
            support_article_params = '/articles/360013426573-'
            # Must specify the language in the URL since
            # support links do not auto detect the language settings
            language_specific_params = {
                'en': 'What-are-the-differences-between-audit-free-and-verified-paid-courses-',
                'es-419': ('-Cu%C3%A1les-son-las-diferencias'
                           '-entre-los-cursos-de-auditor%C3%ADa-gratuitos-y-verificados-pagos-')
            }
            if language in ('es-419', 'es'):
                full_params = enabled_languages['es-419'] + support_article_params + language_specific_params['es-419']
            else:
                full_params = enabled_languages['en'] + support_article_params + language_specific_params['en']
            track_comparison_url = urljoin(
                support_root_url,
                full_params
            )
            return {
                'verified_certificate': track_verified_url,
                'learn_more': track_comparison_url,
            }
    # Default URL's are used if not edx.org
    return {
        'verified_certificate': marketing_root_url,
        'learn_more': support_root_url,
    }
