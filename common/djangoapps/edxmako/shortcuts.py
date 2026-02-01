#   Copyright (c) 2008 Mikeal Rogers  # lint-amnesty, pylint: disable=missing-module-docstring
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.


import logging

from django.conf import settings
from django.http import HttpResponse  # lint-amnesty, pylint: disable=unused-import
from django.template import engines
from django.urls import reverse, NoReverseMatch
from six.moves.urllib.parse import urljoin
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError

from edx_django_utils.monitoring import set_custom_attribute
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from xmodule.util.xmodule_django import get_current_request_hostname  # lint-amnesty, pylint: disable=wrong-import-order

from . import Engines

log = logging.getLogger(__name__)


def marketing_link(name):
    """Returns the correct URL for a link to the marketing site
    depending on if the marketing site is enabled

    Since the marketing site is enabled by a setting, we have two
    possible URLs for certain links. This function is to decides
    which URL should be provided.
    """
    # link_map maps URLs from the marketing site to the old equivalent on
    # the Django site
    link_map = settings.MKTG_URL_LINK_MAP
    enable_mktg_site = configuration_helpers.get_value(
        'ENABLE_MKTG_SITE',
        settings.FEATURES.get('ENABLE_MKTG_SITE', False)
    )
    marketing_urls = configuration_helpers.get_value(
        'MKTG_URLS',
        settings.MKTG_URLS
    )
    marketing_url_overrides = configuration_helpers.get_value(
        'MKTG_URL_OVERRIDES',
        settings.MKTG_URL_OVERRIDES
    )

    if name in marketing_url_overrides:
        validate = URLValidator()
        url = marketing_url_overrides.get(name)
        try:
            validate(url)
            return url
        except ValidationError as err:
            log.debug("Invalid link set for link %s: %s", name, err)
            return '#'

    if enable_mktg_site and name in marketing_urls:
        # special case for when we only want the root marketing URL
        if name == 'ROOT':
            return marketing_urls.get('ROOT')
        # special case for new enterprise marketing url with custom tracking query params
        if name == 'ENTERPRISE':
            enterprise_url = marketing_urls.get(name)
            # if url is not relative, then return it without joining to root
            if not enterprise_url.startswith('/'):
                return enterprise_url
        # Using urljoin here allows us to enable a marketing site and set
        # a site ROOT, but still specify absolute URLs for other marketing
        # URLs in the MKTG_URLS setting
        # e.g. urljoin('https://marketing.com', 'https://open-edx.org/about') >>> 'https://open-edx.org/about'
        marketing_root = marketing_urls.get('ROOT')
        marketing_path = marketing_urls.get(name)
        if not marketing_path:
            # If marketing_path is not set, return empty string
            return ''
        # If marketing_path is a relative path, return it directly to avoid redirect loops
        if marketing_path.startswith('/'):
            return marketing_path
        return urljoin(marketing_root, marketing_path)
    # only link to the old pages when the marketing site isn't on
    elif not enable_mktg_site and name in link_map:
        # don't try to reverse disabled marketing links
        if link_map[name] is not None:
            host_name = get_current_request_hostname()  # lint-amnesty, pylint: disable=unused-variable
            if link_map[name].startswith('http'):
                return link_map[name]
            else:
                try:
                    return reverse(link_map[name])
                except NoReverseMatch:
                    log.debug("Cannot find corresponding link for name: %s", name)
                    set_custom_attribute('unresolved_marketing_link', name)
                    return '#'
    else:
        log.debug("Cannot find corresponding link for name: %s", name)
        return '#'


def is_any_marketing_link_set(names):
    """
    Returns a boolean if any given named marketing links are configured.
    """

    return any(is_marketing_link_set(name) for name in names)


def is_marketing_link_set(name):
    """
    Returns a boolean if a given named marketing link is configured.
    """

    enable_mktg_site = configuration_helpers.get_value(
        'ENABLE_MKTG_SITE',
        settings.FEATURES.get('ENABLE_MKTG_SITE', False)
    )
    marketing_urls = configuration_helpers.get_value(
        'MKTG_URLS',
        settings.MKTG_URLS
    )

    if enable_mktg_site:
        return name in marketing_urls
    else:
        return name in settings.MKTG_URL_LINK_MAP


def marketing_link_context_processor(request):
    """
    A django context processor to give templates access to marketing URLs

    Returns a dict whose keys are the marketing link names usable with the
    marketing_link method (e.g. 'ROOT', 'CONTACT', etc.) prefixed with
    'MKTG_URL_' and whose values are the corresponding URLs as computed by the
    marketing_link method.
    """
    marketing_urls = configuration_helpers.get_value(
        'MKTG_URLS',
        settings.MKTG_URLS
    )

    return {
        "MKTG_URL_" + k: marketing_link(k)
        for k in (
            settings.MKTG_URL_LINK_MAP.keys() | marketing_urls.keys()
        )
    }


def render_to_string(template_name, dictionary, namespace='main', request=None):
    """
    Render a Mako template to as a string.

    The following values are available to all templates:
        settings: the django settings object
        EDX_ROOT_URL: settings.EDX_ROOT_URL
        marketing_link: The :func:`marketing_link` function
        is_any_marketing_link_set: The :func:`is_any_marketing_link_set` function
        is_marketing_link_set: The :func:`is_marketing_link_set` function

    Arguments:
        template_name: The name of the template to render. Will be loaded
            from the template paths specified in configuration.
        dictionary: A dictionary of variables to insert into the template during
            rendering.
        namespace: The Mako namespace to find the named template in.
        request: The request to use to construct the RequestContext for rendering
            this template. If not supplied, the current request will be used.
    """
    if namespace == 'lms.main':
        engine = engines[Engines.PREVIEW]
    else:
        engine = engines[Engines.MAKO]
    template = engine.get_template(template_name)
    return template.render(dictionary, request)


def render_to_response(template_name, dictionary=None, namespace='main', request=None, **kwargs):
    """
    Returns a HttpResponse whose content is filled with the result of calling
    lookup.get_template(args[0]).render with the passed arguments.
    """

    dictionary = dictionary or {}
    return HttpResponse(render_to_string(template_name, dictionary, namespace, request), **kwargs)
