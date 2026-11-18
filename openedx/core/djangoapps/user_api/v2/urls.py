"""URLs for version 2 of the User API."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import re_path
from rest_framework.routers import SimpleRouter

from openedx.core.djangoapps.profile_images.v2.views import ProfileImageView

from .views import (
    PREFERENCE_KEY_PATTERN_PARTIAL,
    AccountDeactivationView,
    AccountPreferenceViewSet,
    AccountViewSet,
    CountryTimeZoneViewSet,
    EmailOptInViewSet,
    EmailSearchViewSet,
    IDVerificationStatusView,
    IDVerificationViewSet,
    NameChangeViewSet,
    RetirementCleanupViewSet,
    RetirementPartnerReportCleanupViewSet,
    RetirementPartnerReportViewSet,
    RetirementViewSet,
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
# What a user wants to be sent is recorded against that user and against the
# organization running a course they named, so the choice is reached without
# naming either in the address and stands beside the accounts rather than
# under them.
router.register(r"email_opt_ins", EmailOptInViewSet, basename="email_opt_in")
router.register(r"name_changes", NameChangeViewSet, basename="name_change")
router.register(r"users", UserViewSet, basename="user")
router.register(r"user_preferences", UserPreferenceViewSet, basename="user_preference")
router.register(r"time_zones", CountryTimeZoneViewSet, basename="time_zone")
# Retirements are worked across accounts, so they stand beside accounts, keyed
# by the name the account was registered under.
router.register(r"retirements", RetirementViewSet, basename="retirement")
# Batch disposal has no member address, so it gets its own collection: a fixed
# word after a collection would take the place of a member's name.
router.register(r"retirement_cleanups", RetirementCleanupViewSet, basename="retirement_cleanup")
# What is owed to the organizations an account studied with outlives the
# account, so it is recorded on its own and reached without naming one.
router.register(
    r"retirement_partner_reports",
    RetirementPartnerReportViewSet,
    basename="retirement_partner_report",
)
# The entries that have been reported on are disposed of in the same way, and
# at a collection of their own for the same reason.
router.register(
    r"retirement_partner_report_cleanups",
    RetirementPartnerReportCleanupViewSet,
    basename="retirement_partner_report_cleanup",
)


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
    # One account's preferences are one document, read and changed whole, and
    # each of them is also reached on its own; a preference is stored under its
    # owner and its name together, so neither address can be built without the
    # account. The two are declared rather than generated because the document
    # is changed with a method the generated collection route does not carry.
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/preferences/$",
        AccountPreferenceViewSet.as_view({"get": "retrieve_all", "patch": "partial_update_all"}),
        name="account_preference_list",
    ),
    # The name a preference is stored under takes the rest of the segment, so
    # every name that carries no separator of its own can be addressed.
    re_path(
        rf"^accounts/(?P<username>{settings.USERNAME_REGEX_PARTIAL})/preferences/"
        rf"(?P<key>{PREFERENCE_KEY_PATTERN_PARTIAL})/$",
        AccountPreferenceViewSet.as_view(
            {"get": "retrieve", "put": "update", "delete": "destroy"},
        ),
        name="account_preference_detail",
    ),
    # The key half takes the rest of the segment after the comma.
    re_path(
        rf"^user_preferences/(?P<username>{settings.USERNAME_REGEX_PARTIAL})"
        rf",(?P<key>{PREFERENCE_KEY_PATTERN_PARTIAL})/$",
        UserPreferenceViewSet.as_view({"get": "retrieve"}),
        name="user_preference_detail",
    ),
]
