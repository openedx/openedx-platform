"""EdX Branding API

Provides a way to retrieve "branded" parts of the site,
such as the site footer.

This information exposed to:
1) Templates in the LMS.
2) Consumers of the branding API.

This ensures that branded UI elements such as the footer
are consistent across the LMS and other sites (such as
the marketing site and blog).

"""

import logging

import six
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse
from django.utils.translation import gettext as _
from six.moves.urllib.parse import urljoin

from common.djangoapps.edxmako.shortcuts import marketing_link
from lms.djangoapps.branding.models import BrandingApiConfig
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

log = logging.getLogger("edx.footer")
EMPTY_URL = '#'


def is_enabled():
    """Check whether the branding API is enabled. """
    return BrandingApiConfig.current().enabled


def get_footer(is_secure=True, language=settings.LANGUAGE_CODE):
    """Retrieve information used to render the footer.

    This will handle both the Open edX and edX.org versions
    of the footer.  All user-facing text is internationalized.

    Currently, this does NOT support theming.

    Keyword Arguments:
        is_secure (bool): If True, use https:// in URLs.

    Returns: dict

    Example:
    >>> get_footer()
    {
        "copyright": "(c) 2015 EdX Inc",
        "logo_image": "http://www.example.com/logo.png",
        "social_links": [
            {
                "name": "facebook",
                "title": "Facebook",
                "url": "http://www.facebook.com/example",
                "icon-class": "fa-facebook-square",
                "action": "Sign up on Facebook!"
            },
            ...
        ],
        "navigation_links": [
            {
                "name": "about",
                "title": "About",
                "url": "http://www.example.com/about.html"
            },
            ...
        ],
        "mobile_links": [
            {
                "name": "apple",
                "title": "Apple",
                "url": "http://store.apple.com/example_app",
                "image": "http://example.com/static/apple_logo.png"
            },
            ...
        ],
        "legal_links": [
            {
                "url": "http://example.com/terms-of-service.html",
                "name": "terms_of_service",
                "title': "Terms of Service"
            },
            # ...
        ],
        "openedx_link": {
            "url": "https://open.edx.org",
            "title": "Powered by Open edX",
            "image": "http://example.com/openedx.png"
        }
    }

    """
    return {
        "copyright": _footer_copyright(),
        "logo_image": _footer_logo_img(is_secure),
        "social_links": _footer_social_links(),
        "business_links": _footer_business_links(language),
        "mobile_links": _footer_mobile_links(is_secure),
        "more_info_links": _footer_more_info_links(language),
        "connect_links": _footer_connect_links(language),
        "openedx_link": _footer_openedx_link(),
        "navigation_links": _footer_navigation_links(language),
        "legal_links": _footer_legal_links(language),
        "edx_org_link": {
            "url": "https://www.edx.org/?utm_medium=affiliate_partner"
                   "&utm_source=opensource-partner"
                   "&utm_content=open-edx-partner-footer-link"
                   "&utm_campaign=open-edx-footer",
            # Translators: This string is used across Open edX installations
            # as a callback to edX. Please do not translate `edX.org`
            "text": _("Take free online courses at edX.org"),
        },
    }


def _footer_copyright():
    """Return the copyright to display in the footer.

    Returns: unicode

    """
    return _(
        # Translators: 'edX' and 'Open edX' are trademarks of 'edX Inc.'.
        # Please do not translate any of these trademarks and company names.
        "© {org_name}.  All rights reserved except where noted.  edX, Open edX "
        "and their respective logos are registered trademarks of edX Inc."
    ).format(org_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME))


def _footer_openedx_link():
    """Return the image link for "Powered by Open edX".

    Args:
        is_secure (bool): Whether the request is using TLS.

    Returns: dict

    """
    # Translators: 'Open edX' is a trademark, please keep this untranslated.
    # See http://openedx.org for more information.
    title = _("Powered by Open edX")
    return {
        "url": settings.FOOTER_OPENEDX_URL,
        "title": title,
        "image": settings.FOOTER_OPENEDX_LOGO_IMAGE,
    }


def _footer_social_links():
    """Return the social media links to display in the footer.

    Returns: list

    """
    platform_name = configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)
    links = []

    for social_name in settings.SOCIAL_MEDIA_FOOTER_NAMES:
        display = settings.SOCIAL_MEDIA_FOOTER_DISPLAY.get(social_name, {})
        links.append(
            {
                "name": social_name,
                "title": str(display.get("title", "")),
                "url": settings.SOCIAL_MEDIA_FOOTER_ACE_URLS.get(social_name, "#"),
                "icon-class": display.get("icon", ""),
                "action": str(display.get("action", "")).format(platform_name=platform_name),
            }
        )
    return links


def _build_support_form_url(full_path=False):
    """
    Return the support form path

    Returns url of support form, which can have 3 possible values:
    - '' if the contact form is disabled (by setting SiteConfiguration
      `CONTACT_US_ENABLE = False`)

    - The normal edx support form path using reverse("support:contact_us") if
      `CONTACT_US_ENABLE = True` and a custom link isn't set on
      CONTACT_US_CUSTOM_LINK. There's the optional parameter `full_path`, that
      if set to True will append the LMS base url to the relative path before
      returning.

    - CONTACT_US_CUSTOM_LINK if the the user has set a custom URL redirect
      for support forms (by setting `CONTACT_US_ENABLE = True` and
      `CONTACT_US_CUSTOM_LINK = http://some.url/for/contact`).
      If this is set, the returned link is the content of
      CONTACT_US_CUSTOM_LINK and the `full_path` variable is ignored since this
      is a path outside the LMS

    Parameters:
        - full_path: bool. Appends base_url to returned value if
                     `CONTACT_US_ENABLE = True`and no link is set on
                     `CONTACT_US_CUSTOM_LINK`

    Returns: string

    """
    contact_us_page = ''

    if configuration_helpers.get_value('CONTACT_US_ENABLE', True):
        # Gets custom url ad check if it's enabled
        contact_us_page = configuration_helpers.get_value('CONTACT_US_CUSTOM_LINK', '')

        # If no custom link is set, get default support form using reverse
        if not contact_us_page:
            contact_us_page = reverse("support:contact_us")

            # Prepend with lms base_url if specified by `full_path`
            if full_path:
                contact_us_page = f'{settings.LMS_ROOT_URL}{contact_us_page}'

    return contact_us_page


def _build_help_center_url(language):
    """
    Return the help-center URL based on the language selected on the homepage.

    :param language: selected language
    :return: help-center URL
    """
    support_url = settings.SUPPORT_SITE_LINK
    # Changing the site url only for the Edx.org and not for OpenEdx.
    if support_url and 'support.edx.org' in support_url:
        enabled_languages = {
            'en': 'hc/en-us',
            'es-419': 'hc/es-419'
        }
        if language in enabled_languages:
            support_url = urljoin(support_url, enabled_languages[language])

    return support_url


def _security_url():
    """
    Return the security policy page URL.
    """
    return settings.SECURITY_PAGE_URL


def _footer_connect_links(language=settings.LANGUAGE_CODE):
    """Return the connect links to display in the footer. """
    links = [
        ("blog", (marketing_link("BLOG"), _("Blog"))),
        ("contact", (_build_support_form_url(full_path=True), _("Contact Us"))),
        ("help-center", (_build_help_center_url(language), _("Help Center"))),
        ("security", (_security_url(), _("Security"))),
    ]

    if language == settings.LANGUAGE_CODE:
        links.append(("media_kit", (marketing_link("MEDIA_KIT"), _("Media Kit"))))
        links.append(("donate", (marketing_link("DONATE"), _("Donate"))))

    return [
        {
            "name": link_name,
            "title": link_title,
            "url": link_url,
        }
        for link_name, (link_url, link_title) in links
        if link_url and link_url != "#"
    ]


def _find_position_of_link(links, key):
    "Returns position of the link to be inserted"
    for link in links:
        if link[0] == key:
            return links.index(link) + 1


def _footer_navigation_links(language=settings.LANGUAGE_CODE):
    """Return the navigation links to display in the footer. """
    platform_name = configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)
    links = [
        ("about", (marketing_link("ABOUT"), _("About"))),
        ("enterprise", (
            marketing_link("ENTERPRISE"),
            _("{platform_name} for Business").format(platform_name=platform_name)
        )),
        ("blog", (marketing_link("BLOG"), _("Blog"))),
        ("help-center", (_build_help_center_url(language), _("Help Center"))),
        ("contact", (_build_support_form_url(), _("Contact"))),
        ("careers", (marketing_link("CAREERS"), _("Careers"))),
        ("donate", (marketing_link("DONATE"), _("Donate"))),
    ]

    if language == settings.LANGUAGE_CODE:
        position = _find_position_of_link(links, 'blog')
        links.insert(position, ("news", (marketing_link("NEWS"), _("News"))))

    return [
        {
            "name": link_name,
            "title": link_title,
            "url": link_url,
        }
        for link_name, (link_url, link_title) in links
        if link_url and link_url != "#"
    ]


def _footer_legal_links(language=settings.LANGUAGE_CODE):
    """Return the legal footer links (e.g. terms of service). """

    links = [
        ("terms_of_service_and_honor_code", (marketing_link("TOS_AND_HONOR"), _("Terms of Service & Honor Code"))),
        ("privacy_policy", (marketing_link("PRIVACY"), _("Privacy Policy"))),
        ("accessibility_policy", (marketing_link("ACCESSIBILITY"), _("Accessibility Policy"))),
        ("media_kit", (marketing_link("MEDIA_KIT"), _("Media Kit"))),
    ]

    # Backwards compatibility: If a combined "terms of service and honor code"
    # link isn't provided, add separate TOS and honor code links.
    tos_and_honor_link = marketing_link("TOS_AND_HONOR")
    if not (tos_and_honor_link and tos_and_honor_link != "#"):
        links.extend([
            ("terms_of_service", (marketing_link("TOS"), _("Terms of Service"))),
            ("honor_code", (marketing_link("HONOR"), _("Honor Code"))),
        ])

    if language == settings.LANGUAGE_CODE:
        position = _find_position_of_link(links, 'accessibility_policy')
        links.insert(position, ("sitemap", (marketing_link("SITE_MAP"), _("Sitemap"))))

    return [
        {
            "name": link_name,
            "title": link_title,
            "url": link_url,
        }
        for link_name, (link_url, link_title) in links
        if link_url and link_url != "#"
    ]


def _add_enterprise_marketing_footer_query_params(url):
    """Add query params to url if they exist in the settings"""
    params = settings.ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS
    if params:
        return "{url}/?{params}".format(  # noqa: UP032
            url=url,
            params=six.moves.urllib.parse.urlencode(params),
        )
    return url


def _footer_business_links(language=settings.LANGUAGE_CODE):
    """Return the business links to display in the footer. """
    platform_name = configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)
    links = [
        ("about", (marketing_link("ABOUT"), _("About"))),
        ("enterprise", (
            _add_enterprise_marketing_footer_query_params(marketing_link("ENTERPRISE")),
            _("{platform_name} for Business").format(platform_name=platform_name)
        )),
    ]

    if language == settings.LANGUAGE_CODE:
        links.append(('affiliates', (marketing_link("AFFILIATES"), _("Affiliates"))))
        # Translators: 'Open edX' is a trademark, please keep this untranslated
        links.append(('openedx', (_footer_openedx_link()["url"], _("Open edX"))))
        links.append(('careers', (marketing_link("CAREERS"), _("Careers"))))
        links.append(("news", (marketing_link("NEWS"), _("News"))))

    return [
        {
            "name": link_name,
            "title": link_title,
            "url": link_url,
        }
        for link_name, (link_url, link_title) in links
        if link_url and link_url != "#"
    ]


def _footer_more_info_links(language=settings.LANGUAGE_CODE):
    """Return the More Information footer links (e.g. terms of service). """

    links = [
        ("terms_of_service_and_honor_code", (marketing_link("TOS_AND_HONOR"), _("Terms of Service & Honor Code"))),
        ("privacy_policy", (marketing_link("PRIVACY"), _("Privacy Policy"))),
        ("cookie_policy_link", (marketing_link("COOKIE"), _("Cookie Policy"))),
        ("accessibility_policy", (marketing_link("ACCESSIBILITY"), _("Accessibility Policy"))),
        ("ccpa_link", (marketing_link("CCPA"), _("Your Privacy Choices")))
    ]

    # Backwards compatibility: If a combined "terms of service and honor code"
    # link isn't provided, add separate TOS and honor code links.
    tos_and_honor_link = marketing_link("TOS_AND_HONOR")
    if not (tos_and_honor_link and tos_and_honor_link != "#"):
        links.extend([
            ("terms_of_service", (marketing_link("TOS"), _("Terms of Service"))),
            ("honor_code", (marketing_link("HONOR"), _("Honor Code"))),
        ])

    if language == settings.LANGUAGE_CODE:
        links.append(("trademarks", (marketing_link("TRADEMARKS"), _("Trademark Policy"))))
        links.append(("sitemap", (marketing_link("SITE_MAP"), _("Sitemap"))))

    return [
        {
            "name": link_name,
            "title": link_title,
            "url": link_url,
        }
        for link_name, (link_url, link_title) in links
        if link_url and link_url != "#"
    ]


def _footer_mobile_links(is_secure):
    """Return the mobile app store links.

    Args:
        is_secure (bool): Whether the request is using TLS.

    Returns: list

    """
    platform_name = configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)

    mobile_links = []
    if settings.FEATURES.get('ENABLE_FOOTER_MOBILE_APP_LINKS'):
        mobile_links = [
            {
                "name": "apple",
                "title": _(
                    "Download the {platform_name} mobile app from the Apple App Store"
                ).format(platform_name=platform_name),
                "url": settings.MOBILE_STORE_ACE_URLS.get('apple', '#'),
                "image": _absolute_url_staticfile(is_secure, 'images/app/app_store_badge_135x40.svg'),
            },
            {
                "name": "google",
                "title": _(
                    "Download the {platform_name} mobile app from Google Play"
                ).format(platform_name=platform_name),
                "url": settings.MOBILE_STORE_ACE_URLS.get('google', '#'),
                "image": _absolute_url_staticfile(is_secure, 'images/app/google_play_badge_45.png'),
            }
        ]
    return mobile_links


def _footer_logo_img(is_secure):
    """
    Return the logo used for footer about link

    Arguments:
        is_secure (bool): Whether the request is using TLS.

    Returns:
        URL of the brand logo
    """
    default_local_path = 'images/logo.png'
    brand_footer_logo_url = settings.LOGO_TRADEMARK_URL
    # `logo_name` is looked up from the configuration,
    # which falls back on the Django settings, which loads it from
    # `lms.yml`, which is created and managed by Ansible. Because of
    # this runaround, we lose a lot of the flexibility that Django's
    # staticfiles system provides, and we end up having to hardcode the path
    # to the footer logo rather than use the comprehensive theming system.
    # logo by default, so that it can display properly on edx.org -- both
    # within the LMS, and on the Drupal marketing site, which uses this API.
    if brand_footer_logo_url:
        return brand_footer_logo_url

    log.info(
        default_local_path,
    )

    # And we'll use the default logo path of "images/logo.png" instead.
    # There is a core asset that corresponds to this logo, so this should
    # always succeed.
    return staticfiles_storage.url(default_local_path)


def _absolute_url(is_secure, url_path):
    """Construct an absolute URL back to the site.

    Arguments:
        is_secure (bool): If true, use HTTPS as the protocol.
        url_path (unicode): The path of the URL.

    Returns:
        unicode

    """
    site_name = configuration_helpers.get_value('SITE_NAME', settings.SITE_NAME)
    parts = ("https" if is_secure else "http", site_name, url_path, '', '', '')
    return six.moves.urllib.parse.urlunparse(parts)


def _absolute_url_staticfile(is_secure, name):
    """Construct an absolute URL to a static resource on the site.

    Arguments:
        is_secure (bool): If true, use HTTPS as the protocol.
        name (unicode): The name of the static resource to retrieve.

    Returns:
        unicode

    """
    url_path = staticfiles_storage.url(name)

    # In production, the static files URL will be an absolute
    # URL pointing to a CDN.  If this happens, we can just
    # return the URL.
    if six.moves.urllib.parse.urlparse(url_path).netloc:
        return url_path

    # For local development, the returned URL will be relative,
    # so we need to make it absolute.
    return _absolute_url(is_secure, url_path)


def get_configuration_url(name):
    """
    Look up and return the value for given url name in configuration.
    URLs are saved in "urls" dictionary inside configuration.

    Return 'EMPTY_URL' if given url name is not defined in configuration urls.
    """
    urls = configuration_helpers.get_value("urls", default={})
    return urls.get(name) or EMPTY_URL


def get_url(name):
    """
    Lookup and return page url, lookup is performed in the following order

    1. get url, If configuration URL override exists, return it
    2. Otherwise return the marketing URL.

    :return: string containing page url.
    """
    # If a configuration URL override exists, return it.  Otherwise return the marketing URL.
    configuration_url = get_configuration_url(name)
    if configuration_url != EMPTY_URL:
        return configuration_url

    # get marketing link, if marketing is disabled then platform url will be used instead.
    url = marketing_link(name)

    return url or EMPTY_URL


def get_base_url(is_secure):
    """
    Return Base URL for site.
    Arguments:
        is_secure (bool): If true, use HTTPS as the protocol.
    """
    return _absolute_url(is_secure=is_secure, url_path="")


def get_logo_url(is_secure=True):
    """
    Return the url for the branded logo image to be used.

    Preference of the logo should be,
        Look for site configuration override and return absolute url
        Absolute url of brand Logo if defined in django settings
        Relative default local image path

    Arguments:
        is_secure (bool): If true, use HTTPS as the protocol.
    """
    brand_logo_url = settings.LOGO_URL
    default_local_path = 'images/logo.png'
    logo_url_from_site_config = configuration_helpers.get_value('logo_image_url')
    university = configuration_helpers.get_value('university')

    if logo_url_from_site_config:
        return _absolute_url_staticfile(is_secure=is_secure, name=logo_url_from_site_config)

    if university:
        return staticfiles_storage.url(f'images/{university}-on-edx-logo.png')

    if brand_logo_url:
        return brand_logo_url

    return staticfiles_storage.url(default_local_path)


def get_favicon_url():
    """
    Return the url for the branded favicon image to be used.

    Preference of the icon should be,
        Look for site configuration override
        Brand favicon url is defined in settings
        Default local image path
    """
    brand_favicon_url = settings.FAVICON_URL
    default_local_path = getattr(settings, 'FAVICON_PATH', 'images/favicon.ico')
    favicon_url_from_site_config = configuration_helpers.get_value('favicon_path')

    if favicon_url_from_site_config:
        return staticfiles_storage.url(favicon_url_from_site_config)

    if brand_favicon_url:
        return brand_favicon_url

    return staticfiles_storage.url(default_local_path)


def get_tos_and_honor_code_url():
    """
    Lookup and return terms of services page url
    """
    return get_url("TOS_AND_HONOR")


def get_privacy_url():
    """
    Lookup and return privacy policies page url
    """
    return get_url("PRIVACY")


def get_about_url():
    """
    Lookup and return About page url
    """
    return get_url("ABOUT")


def get_home_url():
    """
    Return Dashboard page url
    """
    return reverse('dashboard')


def get_logo_url_for_email():
    """
    Returns the url for the branded logo image for embedding in email templates.
    """
    default_logo_url = getattr(settings, 'DEFAULT_EMAIL_LOGO_URL', None)
    # The LOGO_URL_PNG might be reused in the future for other things, so including an email specific png logo
    return (getattr(settings, 'LOGO_URL_PNG_FOR_EMAIL', None) or
            getattr(settings, 'LOGO_URL_PNG', None) or default_logo_url)
