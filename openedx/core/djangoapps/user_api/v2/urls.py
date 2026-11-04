"""URLs for version 2 of the User API."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import re_path
from rest_framework.routers import SimpleRouter

from openedx.core.djangoapps.profile_images.v2.views import ProfileImageView

from .views import (
    PREFERENCE_KEY_PATTERN_PARTIAL,
    AccountDeactivationView,
    AccountViewSet,
    CountryTimeZoneViewSet,
    EmailSearchViewSet,
    IDVerificationStatusView,
    IDVerificationViewSet,
    NameChangeViewSet,
    UserPreferenceViewSet,
    UserViewSet,
)

app_name = "user_v2"

#: The router's id-based preference route, replaced by the owner,key address below.
REPLACED_ROUTER_ROUTE = "user_preference_detail"

def without_route(routes, name):
    """Return ``routes`` without the one named ``name``, refusing a name the router does not register."""
    kept = [route for route in routes if route.name != name]
    if len(kept) == len(routes):
        raise ImproperlyConfigured(f"The router registers no route named {name!r} to replace.")
    return kept


class SnakeCaseRouter(SimpleRouter):
    """A router that names its routes ``<basename>_<action>``."""

    routes = [route._replace(name=route.name.replace("-", "_")) for route in SimpleRouter.routes]


router = SnakeCaseRouter()
router.register(r"accounts", AccountViewSet, basename="account")
router.register(
    rf"accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/verifications",
    IDVerificationViewSet,
    basename="account_verification",
)
# Beside the accounts rather than under them: an account may be named anything.
router.register(r"email_searches", EmailSearchViewSet, basename="email_search")
router.register(r"name_changes", NameChangeViewSet, basename="name_change")
router.register(r"users", UserViewSet, basename="user")
router.register(r"user_preferences", UserPreferenceViewSet, basename="user_preference")
router.register(r"time_zones", CountryTimeZoneViewSet, basename="time_zone")


urlpatterns = [
    *without_route(router.urls, REPLACED_ROUTER_ROUTE),
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/profile_image/$",
        ProfileImageView.as_view(),
        name="account_profile_image",
    ),
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/deactivation/$",
        AccountDeactivationView.as_view(),
        name="account_deactivation",
    ),
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/verification_status/$",
        IDVerificationStatusView.as_view(),
        name="account_verification_status",
    ),
    # The key half takes the rest of the segment after the comma.
    re_path(
        rf"^user_preferences/(?P<username>{settings.USERNAME_REGEX_PARTIAL})"
        rf",(?P<key>{PREFERENCE_KEY_PATTERN_PARTIAL})/$",
        UserPreferenceViewSet.as_view({"get": "retrieve"}),
        name="user_preference_detail",
    ),
]
