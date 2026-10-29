"""Page-size handling on the paginated collections of version 2 of the User API."""

import inspect

from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2 import views as user_api_views
from openedx.core.djangoapps.user_api.v2.paginators import UserApiPagination
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"

#: Values naming a number of rows no page can carry: none at all, fewer than
#: none, and values that are not a whole number of rows in the first place.
UNHONOURED_PAGE_SIZES = ("0", "-1", "abc", "1.5", "0x10", "%20")

#: What a caller is told when it asks for one of them.
REFUSAL = (
    "Enter a whole number of rows of at least 1. A larger number than "
    f"{UserApiPagination.max_page_size} returns {UserApiPagination.max_page_size} rows."
)


@skip_unless_lms
class PageSizeTests(APITestCase):
    """
    A page size the collection will not give is refused rather than answered.

    The size asked for is dropped by the framework when it cannot be honoured
    and the default page is answered in its place, which a caller reads as the
    page it asked for; the links on that page carry the value back, so a
    client following them keeps sending a number that is never read. Each
    collection below is checked, because the number is read by the paginator
    they share and a collection that stopped sharing it would stop refusing.
    """

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory.create(username="page-admin", password=PASSWORD)
        self.learner = UserFactory.create(username="page-learner", password=PASSWORD)
        UserPreferenceFactory.create(user=self.learner, key="time_zone", value="UTC")

    def collections(self):
        """Return each paginated collection, the caller it admits and how it is asked."""
        return {
            "user_list": (self.admin, reverse("user_v2:user_list"), {}),
            "user_preference_list": (self.admin, reverse("user_v2:user_preference_list"), {}),
            "time_zone_list": (None, reverse("user_v2:time_zone_list"), {}),
            "account_list": (self.learner, reverse("user_v2:account_list"), {}),
            "account_verification_list": (
                self.learner,
                reverse(
                    "user_v2:account_verification_list",
                    kwargs={"username": self.learner.username},
                ),
                {},
            ),
            "email_search_list": (
                self.admin,
                reverse("user_v2:email_search_list"),
                {"body": {"emails": [self.learner.email]}},
            ),
        }

    def ask(self, caller, address, page_size, body=None):
        """Ask one collection for a page of ``page_size`` rows as ``caller``."""
        self.client.logout()
        if caller is not None:
            assert self.client.login(username=caller.username, password=PASSWORD)
        query = "" if page_size is None else f"?page_size={page_size}"
        if body is None:
            return self.client.get(f"{address}{query}")
        return self.client.post(f"{address}{query}", body, format="json")

    def test_a_number_of_rows_no_page_can_carry_is_refused(self):
        for name, (caller, address, extra) in self.collections().items():
            for page_size in UNHONOURED_PAGE_SIZES:
                with self.subTest(collection=name, page_size=page_size):
                    response = self.ask(caller, address, page_size, **extra)
                    envelope = assert_error_envelope(
                        response,
                        expected_status=status.HTTP_400_BAD_REQUEST,
                        expected_type_slug="validation",
                    )
                    assert envelope["errors"] == {"page_size": [REFUSAL]}

    def test_a_value_carrying_nothing_asks_for_nothing_and_is_answered(self):
        """An empty value leaves the size alone, as an empty selector leaves the rows alone."""
        for name, (caller, address, extra) in self.collections().items():
            with self.subTest(collection=name):
                response = self.ask(caller, address, "", **extra)
                assert response.status_code in (
                    status.HTTP_200_OK, status.HTTP_201_CREATED,
                ), response.content
                assert len(response.data["results"]) <= UserApiPagination.page_size

    def test_more_rows_than_a_page_carries_is_answered_with_as_many_as_it_carries(self):
        """A number above the cap keeps being answered with the cap rather than refused."""
        for index in range(UserApiPagination.max_page_size + 5):
            UserFactory.create(username=f"page-filler{index:03d}")
        self.client.logout()
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        response = self.client.get(reverse("user_v2:user_list"), {"page_size": 100000})
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(response.data["results"]) == UserApiPagination.max_page_size

    def test_the_link_to_the_next_page_carries_a_size_the_next_page_will_give(self):
        """
        A refused size cannot reach the links, and an honoured one is carried back unchanged.

        A link carrying a size the collection drops sends the client back with
        the same value on every page it follows, and every one of those pages
        is a different size from the one it asked for.
        """
        for index in range(5):
            UserFactory.create(username=f"page-walked{index:03d}")
        self.client.logout()
        assert self.client.login(username=self.admin.username, password=PASSWORD)
        response = self.client.get(reverse("user_v2:user_list"), {"page_size": 2})
        assert response.status_code == status.HTTP_200_OK, response.content
        assert len(response.data["results"]) == 2
        assert response.data["next"].endswith("?page=2&page_size=2")

        followed = self.client.get(response.data["next"])
        assert followed.status_code == status.HTTP_200_OK, followed.content
        assert len(followed.data["results"]) == 2

    def test_every_collection_of_this_version_that_pages_is_checked_here(self):
        """
        The refusal lives in one paginator, so the check is only worth what it covers.

        A collection added later that pages its rows with the same paginator
        is listed above or this fails, rather than the refusal quietly
        applying to an address nothing exercises.
        """
        paging = {
            name
            for name, member in inspect.getmembers(user_api_views, inspect.isclass)
            if getattr(member, "pagination_class", None) is UserApiPagination
        }
        assert paging == {
            "AccountViewSet",
            "CountryTimeZoneViewSet",
            "EmailSearchViewSet",
            "IDVerificationViewSet",
            "UserPreferenceViewSet",
            "UserViewSet",
        }
        assert len(self.collections()) == len(paging)
