"""
This file contains utility functions which will responsible for sending emails.
"""


import html
import logging
import os
import urllib
import uuid
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.mail import EmailMessage, SafeMIMEText
from django.urls import reverse
from django.utils.translation import gettext as _
from edx_django_utils.plugins import pluggable_override
from eventtracking import tracker

from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.edxmako.template import Template
from openedx.core.djangoapps.commerce.utils import get_ecommerce_api_base_url, get_ecommerce_api_client
from openedx.core.djangoapps.credit.models import CreditConfig, CreditProvider
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangolib.markup import HTML
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


def send_credit_notifications(username, course_key):  # pylint: disable=too-many-statements
    """Sends email notification to user on different phases during credit
    course e.g., credit eligibility, credit payment etc.
    """
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        log.error('No user with %s exist', username)
        return

    course = modulestore().get_course(course_key, depth=0)
    course_display_name = course.display_name
    tracking_context = tracker.get_tracker().resolve_context()
    tracking_id = str(tracking_context.get('user_id'))
    client_id = str(tracking_context.get('client_id'))
    events = '&t=event&ec=email&ea=open'
    tracking_pixel = 'https://www.google-analytics.com/collect?v=1&tid' + tracking_id + '&cid' + client_id + events
    dashboard_link = _email_url_parser('dashboard')
    credit_course_link = _email_url_parser('courses', '?type=credit')

    # get attached branded logo
    logo_image = cache.get('credit.email.attached-logo')
    if logo_image is None:
        branded_logo = {
            'title': 'Logo',
            'path': settings.NOTIFICATION_EMAIL_EDX_LOGO,
            'cid': str(uuid.uuid4())
        }
        logo_image_id = branded_logo['cid']
        logo_image = attach_image(branded_logo, 'Header Logo')
        if logo_image:
            cache.set('credit.email.attached-logo', logo_image, settings.CREDIT_NOTIFICATION_CACHE_TIMEOUT)
    else:
        # strip enclosing angle brackets from 'logo_image' cache 'Content-ID'
        logo_image_id = logo_image.get('Content-ID', '')[1:-1]

    providers_names = get_credit_provider_attribute_values(course_key, 'display_name')
    providers_string = make_providers_strings(providers_names)
    context = {
        'full_name': user.get_full_name(),
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'course_name': course_display_name,
        'branded_logo': logo_image_id,
        'dashboard_link': dashboard_link,
        'credit_course_link': credit_course_link,
        'tracking_pixel': tracking_pixel,
        'providers': providers_string,
    }

    # create the root email message
    notification_msg = MIMEMultipart('related')
    # add 'alternative' part to root email message to encapsulate the plain and
    # HTML versions, so message agents can decide which they want to display.
    msg_alternative = MIMEMultipart('alternative')
    notification_msg.attach(msg_alternative)
    # render the credit notification templates
    subject = _('Course Credit Eligibility')

    if providers_string:
        subject = _('You are eligible for credit from {providers_string}').format(
            providers_string=providers_string
        )

    # add alternative plain text message
    email_body_plain = render_to_string('credit_notifications/credit_eligibility_email.txt', context)
    msg_alternative.attach(SafeMIMEText(email_body_plain, _subtype='plain', _charset='utf-8'))

    # add alternative html message
    email_body_content = cache.get('credit.email.css-email-body')
    if email_body_content is None:
        html_file_path = file_path_finder('templates/credit_notifications/credit_eligibility_email.html')
        if html_file_path:
            with open(html_file_path) as cur_file:
                cur_text = cur_file.read()
                # use html parser to unescape html characters which are changed
                # by the 'pynliner' while adding inline css to html content
                email_body_content = html.unescape(with_inline_css(cur_text))
                # cache the email body content before rendering it since the
                # email context will change for each user e.g., 'full_name'
                cache.set('credit.email.css-email-body', email_body_content, settings.CREDIT_NOTIFICATION_CACHE_TIMEOUT)
        else:
            email_body_content = ''

    email_body = Template(email_body_content).render(context)
    msg_alternative.attach(SafeMIMEText(email_body, _subtype='html', _charset='utf-8'))

    # attach logo image
    if logo_image:
        notification_msg.attach(logo_image)

    # add email addresses of sender and receiver
    from_address = configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
    to_address = user.email

    # send the root email message
    msg = EmailMessage(subject, '', from_address, [to_address])
    msg.attach(notification_msg)
    msg.send()


def with_inline_css(html_without_css):
    """Returns html with inline css if the css file path exists
    else returns html with out the inline css.
    """
    css_filepath = settings.NOTIFICATION_EMAIL_CSS
    if not css_filepath.startswith('/'):
        css_filepath = file_path_finder(settings.NOTIFICATION_EMAIL_CSS)

    if css_filepath:
        with open(css_filepath) as _file:
            css_content = _file.read()

        # pynliner imports cssutils, which has an expensive initialization. All
        # told, it can account for 15-20% of "fast" LMS startup (without asset
        # compilation). So we're going to load it locally here so that we delay
        # that one-time hit until we actually do the (rare) operation that is
        # sending a credit notification email.
        import pynliner

        # insert style tag in the html and run pyliner.
        html_with_inline_css = pynliner.fromString(HTML('<style>{}</style>{}').format(css_content, html_without_css))
        return html_with_inline_css

    return html_without_css


def attach_image(img_dict, filename):
    """
    Attach images in the email headers.
    """
    img_path = img_dict['path']
    if not img_path.startswith('/'):
        img_path = file_path_finder(img_path)

    if img_path:
        with open(img_path, 'rb') as img:
            msg_image = MIMEImage(img.read(), name=os.path.basename(img_path))
            msg_image.add_header('Content-ID', '<{}>'.format(img_dict['cid']))  # xss-lint: disable=python-wrap-html
            msg_image.add_header("Content-Disposition", "inline", filename=filename)
        return msg_image


def file_path_finder(path):
    """
    Return physical path of file if found.
    """
    return finders.FileSystemFinder().find(path)


def _email_url_parser(url_name, extra_param=None):
    """Parse url according to 'SITE_NAME' which will be used in the mail.

    Args:
        url_name(str): Name of the url to be parsed
        extra_param(str): Any extra parameters to be added with url if any

    Returns:
        str
    """
    site_name = configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME)
    dashboard_url_path = reverse(url_name) + extra_param if extra_param else reverse(url_name)
    dashboard_link_parts = ("https", site_name, dashboard_url_path, '', '', '')
    return urllib.parse.urlunparse(dashboard_link_parts)  # pylint: disable=too-many-function-args


@pluggable_override('OVERRIDE_GET_CREDIT_PROVIDER_IDS_FOR_COURSE')
def get_credit_provider_ids_for_course(course_id):
    """
    Get the provider ids for the course from ecommerce.

    Arguments:
        course_id (str): The identifier for the course.

    Returns:
        List of provider ids.
    """
    try:
        user = User.objects.get(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME)
        api_url = urljoin(f"{get_ecommerce_api_base_url()}/", f"courses/{course_id}/")
        response = get_ecommerce_api_client(user).get(api_url, params={"include_products": 1})
        response.raise_for_status()
        response = response.json() if response.content else None
    except Exception:  # pylint: disable=broad-except
        log.exception("Failed to receive data from the ecommerce course API for Course ID '%s'.", course_id)
        return None

    if not response:
        log.info("No Course information found from ecommerce API for Course ID '%s'.", course_id)
        return None

    provider_ids = []
    for product in response.get('products'):
        provider_ids += [
            attr.get('value') for attr in product.get('attribute_values') if attr.get('name') == 'credit_provider'
        ]

    return provider_ids


def get_credit_provider_attribute_values(course_key, attribute_name):
    """Get the course information from ecommerce and parse the data to get providers.

    Arguments:
        course_key (CourseKey): The identifier for the course.
        attribute_name (String): Name of the attribute of credit provider.

    Returns:
        List of provided credit provider attribute values.
    """
    course_id = str(course_key)
    credit_config = CreditConfig.current()

    cache_key = None
    attribute_values = None

    if credit_config.is_cache_enabled:
        cache_key = '{key_prefix}.{course_key}.{attribute_name}'.format(  # noqa: UP032
            key_prefix=credit_config.CACHE_KEY,
            course_key=course_id,
            attribute_name=attribute_name
        )
        attribute_values = cache.get(cache_key)

    if attribute_values is not None:
        return attribute_values

    provider_ids = get_credit_provider_ids_for_course(course_id)
    if not provider_ids:
        return provider_ids

    attribute_values = []
    credit_providers = CreditProvider.get_credit_providers()
    for provider in credit_providers:
        if provider['id'] in provider_ids:
            attribute_values.append(provider[attribute_name])

    if credit_config.is_cache_enabled:
        cache.set(cache_key, attribute_values, credit_config.cache_ttl)

    return attribute_values


def make_providers_strings(providers):
    """Get the list of course providers and make them comma seperated string.

    Arguments:
        providers : List containing the providers names

    Returns:
        strings containing providers names in readable way .
    """
    if not providers:
        return None

    if len(providers) == 1:
        providers_string = providers[0]

    elif len(providers) == 2:
        # Translators: The join of two university names (e.g., Harvard and MIT).
        providers_string = _("{first_provider} and {second_provider}").format(
            first_provider=providers[0],
            second_provider=providers[1]
        )
    else:
        # Translators: The join of three or more university names. The first of these formatting strings
        # represents a comma-separated list of names (e.g., MIT, Harvard, Dartmouth).
        providers_string = _("{first_providers}, and {last_provider}").format(
            first_providers=", ".join(providers[:-1]),
            last_provider=providers[-1]
        )

    return providers_string
