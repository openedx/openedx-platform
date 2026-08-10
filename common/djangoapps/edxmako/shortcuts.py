#   Copyright (c) 2008 Mikeal Rogers  # pylint: disable=missing-module-docstring
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
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse  # pylint: disable=unused-import
from django.template import engines
from six.moves.urllib.parse import urljoin

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

from . import Engines

log = logging.getLogger(__name__)


def marketing_link(name):
    """Returns the URL for a marketing site link from MKTG_URLS.

    MKTG_URL_OVERRIDES take priority. 'ROOT' falls back to the local landing page
    ('/'); any other unconfigured link falls back to '#'.
    """
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

    if name in marketing_urls:
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
        return urljoin(marketing_urls.get('ROOT'), marketing_urls.get(name))

    # ROOT is the live site root (not a legacy marketing page), so default it to
    # the local landing page when no marketing site ROOT is configured.
    if name == 'ROOT':
        return '/'

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
    marketing_urls = configuration_helpers.get_value(
        'MKTG_URLS',
        settings.MKTG_URLS
    )
    return name in marketing_urls


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
        for k in marketing_urls.keys()
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
