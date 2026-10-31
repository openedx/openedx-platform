"""URLs for version 2 of the User API."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import re_path
from rest_framework.routers import SimpleRouter

from .views import (
    PREFERENCE_KEY_PATTERN_PARTIAL,
    CountryTimeZoneViewSet,
    UnknownAddressView,
    UserPreferenceViewSet,
    UserViewSet,
)

app_name = "user_v2"

#: A preference is identified by its owner and its key, so the router's
#: database-id member route is dropped and replaced by the address declared
#: below under the same name.
REPLACED_ROUTER_ROUTE = "user_preference_detail"


def without_route(routes, name):
    """
    Return ``routes`` without the one registered under ``name``.

    A name that matches nothing means the router stopped generating that route
    under that name: the address it serves would then be published alongside
    the one declared here instead of being replaced by it, so the mismatch is
    refused here rather than discovered at the address.
    """
    kept = [route for route in routes if route.name != name]
    if len(kept) == len(routes):
        raise ImproperlyConfigured(f"The router registers no route named {name!r} to replace.")
    return kept


class SnakeCaseRouter(SimpleRouter):
    """
    A router that names its routes ``<basename>_<action>``.

    The names a route is registered under are the ones ``resolve()`` reports
    and the ones ``reverse()`` accepts, so they are generated in the published
    form rather than rewritten afterwards.
    """

    routes = [route._replace(name=route.name.replace("-", "_")) for route in SimpleRouter.routes]


router = SnakeCaseRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"user_preferences", UserPreferenceViewSet, basename="user_preference")
router.register(r"time_zones", CountryTimeZoneViewSet, basename="time_zone")


urlpatterns = [
    *without_route(router.urls, REPLACED_ROUTER_ROUTE),
    # The username half carries the pattern a username is allowed to match and
    # the key half takes the rest of the segment.
    re_path(
        rf"^user_preferences/(?P<username>{settings.USERNAME_REGEX_PARTIAL})"
        rf",(?P<key>{PREFERENCE_KEY_PATTERN_PARTIAL})/$",
        UserPreferenceViewSet.as_view({"get": "retrieve"}),
        name="user_preference_detail",
    ),
    # The authentication app serves two addresses under this prefix, which this
    # mount is matched before; everything else under it belongs to this version.
    # The route answers rather than reverses, so it carries no name.
    re_path(r"^(?!account/).*$", UnknownAddressView.as_view()),
]
