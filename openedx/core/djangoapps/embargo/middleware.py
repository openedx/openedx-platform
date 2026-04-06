"""Middleware for embargoing site and courses.

IMPORTANT NOTE: This code WILL NOT WORK if you have a misconfigured proxy
server.  If you are configuring embargo functionality, or if you are
experiencing mysterious problems with embargoing, please check that your
reverse proxy is setting any of the well known client IP address headers (ex.,
HTTP_X_FORWARDED_FOR).

This middleware allows you to:

* Embargoing courses (access restriction by courses)
* Embargoing site (access restriction of the main site)

Embargo can restrict by states and whitelist/blacklist (IP Addresses
(ie. 10.0.0.0), Networks (ie. 10.0.0.0/24)), or the user profile country.

Usage:

1) Enable embargo by setting `settings.FEATURES['EMBARGO']` to True.

2) In Django admin, create a new `IPFilter` model to block or whitelist
    an IP address from accessing the site.

3) In Django admin, create a new `RestrictedCourse` model and
    configure a whitelist or blacklist of countries for that course.

"""

import logging
import re
from typing import Optional

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from edx_django_utils import ip
from rest_framework.request import Request
from rest_framework.response import Response

from openedx.core.lib.request_utils import course_id_from_url

from . import api as embargo_api
from .models import IPFilter

log = logging.getLogger(__name__)


class EmbargoMiddleware(MiddlewareMixin):
    """Middleware for embargoing site and courses. """

    ALLOW_URL_PATTERNS = [
        # Don't block the embargo message pages; otherwise we'd
        # end up in an infinite redirect loop.
        re.compile(r'^/embargo/blocked-message/'),

        # Don't block the Django admin pages.  Otherwise, we might
        # accidentally lock ourselves out of Django admin
        # during testing.
        re.compile(r'^/admin/'),
    ]

    def __init__(self, *args, **kwargs):
        # If embargoing is turned off, make this middleware do nothing
        if not settings.FEATURES.get('EMBARGO'):
            raise MiddlewareNotUsed()
        super().__init__(*args, **kwargs)

    def process_request(self, request: Request) -> Optional[Response]:
        """Block requests based on embargo rules.

        This will perform the following checks:

        1) If the user's IP address is blacklisted, block.
        2) If the user's IP address is whitelisted, allow.
        3) If the user's country (inferred from their IP address) is blocked for
            a courseware page, block.
        4) If the user's country (retrieved from the user's profile) is blocked
            for a courseware page, block.
        5) Allow access.

        """
        # Never block certain patterns by IP address
        for pattern in self.ALLOW_URL_PATTERNS:
            if pattern.match(request.path) is not None:
                return None

        safest_ip_address = ip.get_safest_client_ip(request)
        all_ip_addresses = ip.get_all_client_ips(request)

        ip_filter = IPFilter.current()

        # When checking if a request is block-listed, we need to check EVERY client IP address in the chain, in case
        # a blocked ip tried to hop through other proxies.
        blocked_ips = [x for x in all_ip_addresses if x in ip_filter.blacklist_ips]
        if ip_filter.enabled and blocked_ips:
            log.info(
                (
                    "User %s was blocked from accessing %s "
                    "because IP address %s is blacklisted."
                ), request.user.id, request.path, blocked_ips[0]
            )

            # If the IP is blacklisted, reject.
            # This applies to any request, not just courseware URLs.
            ip_blacklist_url = reverse(
                'embargo:blocked_message',
                kwargs={
                    'access_point': 'courseware',
                    'message_key': 'embargo'
                }
            )
            return redirect(ip_blacklist_url)

        # When checking if a request is allow-listed, we should only look at the safest client IP address, as the
        # others in the chain might be spoofed.
        elif ip_filter.enabled and safest_ip_address in ip_filter.whitelist_ips:
            log.info(
                (
                    "User %s was allowed access to %s because "
                    "IP address %s is whitelisted."
                ),
                request.user.id, request.path, safest_ip_address
            )

            # If the IP is whitelisted, then allow access,
            # skipping later checks.
            return None

        else:
            # Otherwise, perform the country access checks.
            # This applies only to courseware URLs.
            return self.country_access_rules(request)

    def country_access_rules(self, request: Request) -> Optional[Response]:
        """
        Check the country access rules for a given course.
        Applies only to courseware URLs.

        Args:
            request: The request to validate against the embargo rules

        Returns:
            HttpResponse or None

        """
        course_id = course_id_from_url(request.path)

        if course_id:
            redirect_url = embargo_api.redirect_if_blocked(request, course_id, access_point='courseware')

            if redirect_url:
                return redirect(redirect_url)
