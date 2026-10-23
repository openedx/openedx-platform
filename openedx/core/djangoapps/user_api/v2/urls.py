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
    UnknownAddressView,
    UserPreferenceViewSet,
    UserViewSet,
)

app_name = "user_v2"

#: A preference is identified by its owner and its key, so the router's
#: database-id member route is dropped and replaced by the address declared
#: below under the same name.
REPLACED_ROUTER_ROUTE = "user_preference_detail"

#: The addresses under this prefix that belong to the authentication app, which
#: this mount is matched before. They are spelled out one by one rather than by
#: the segment they share, so that only what that app serves is left to it and
#: every other address under this prefix is answered here. The trailing slash is
#: optional, which leaves an address sent without one to be redirected to the
#: slashed form the app serves rather than answered as unknown.
OTHER_APP_ADDRESSES = r"account/(registration|login_session)/?"


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


# An address beneath an account is plural where it names rows that accumulate
# behind it, and singular where it names the one thing every account has.
router = SnakeCaseRouter()
router.register(r"accounts", AccountViewSet, basename="account")
# An identity check belongs to the user who went through it and is reached by
# no other route, so the collection is registered beneath the account it
# belongs to.
router.register(
    rf"accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/verifications",
    IDVerificationViewSet,
    basename="account_verification",
)
# What follows the account collection in an address is the name of an account,
# and a user may register any name, so the search over email addresses stands
# beside the accounts rather than under them.
router.register(r"email_searches", EmailSearchViewSet, basename="email_search")
router.register(r"name_changes", NameChangeViewSet, basename="name_change")
router.register(r"users", UserViewSet, basename="user")
router.register(r"user_preferences", UserPreferenceViewSet, basename="user_preference")
router.register(r"time_zones", CountryTimeZoneViewSet, basename="time_zone")


urlpatterns = [
    *without_route(router.urls, REPLACED_ROUTER_ROUTE),
    # The image belongs to one account and is stored in several sizes, so it
    # is addressed as a property of that account rather than as a collection.
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/profile_image/$",
        ProfileImageView.as_view(),
        name="account_profile_image",
    ),
    # Disabling an account is something a privileged caller does to somebody
    # else's account rather than a property of that account, so it is served
    # by a view of its own beneath the account it names.
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/deactivation/$",
        AccountDeactivationView.as_view(),
        name="account_deactivation",
    ),
    # Where one account's owner stands on having proved their identity is
    # worked out from the attempts behind it rather than stored, and there is
    # one such summary per account, so it is served from an address of its own.
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/verification_status/$",
        IDVerificationStatusView.as_view(),
        name="account_verification_status",
    ),
    # The username half carries the pattern a username is allowed to match and
    # the key half takes the rest of the segment.
    re_path(
        rf"^user_preferences/(?P<username>{settings.USERNAME_REGEX_PARTIAL})"
        rf",(?P<key>{PREFERENCE_KEY_PATTERN_PARTIAL})/$",
        UserPreferenceViewSet.as_view({"get": "retrieve"}),
        name="user_preference_detail",
    ),
    # Everything under this prefix that the authentication app does not serve
    # belongs to this version, including an address no route of it answers,
    # which is refused in the body the rest of this version answers with. The
    # route answers rather than reverses, so it carries no name.
    re_path(rf"^(?!{OTHER_APP_ADDRESSES}$).*$", UnknownAddressView.as_view()),
]
