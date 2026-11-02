"""
Tests for Track Selection API in the course_modes REST API.
"""

from datetime import datetime
from unittest import mock

import ddt
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_filters import PipelineStep
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.outlines import update_outline_from_modulestore
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.course_modes.toggles import COURSE_MODES_MFE_TRACK_SELECTION
from common.djangoapps.course_modes.track_selection_data import get_track_options
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.commerce.models import CommerceConfiguration
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.tests.factories import (
    CourseOverviewFactory,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


TEST_LIST_PRICE = 149
TEST_DISCOUNTED_PRICE = 99
TEST_SUBSCRIBE_URL = "https://checkout.example.com/subscribe"
TEST_CATALOG_OFFER = {"in_catalog": True, "subscribe_url": TEST_SUBSCRIBE_URL}
TEST_PROGRAM_UUID = "6d1b9c2e-3f4a-4b5c-8d6e-7f8a9b0c1d2e"


class TestCourseModePriceRequestedPipelineStep(PipelineStep):
    """
    Utility pipeline step that overrides a course mode price with a fixed discounted price.
    """

    def run_filter(self, user, course_mode_data, price):  # pylint: disable=arguments-differ
        """Pipeline step that returns a discounted price for the given course mode."""
        return {
            "user": user,
            "course_mode_data": course_mode_data,
            "price": TEST_DISCOUNTED_PRICE,
        }


# Built from the module and class names rather than hardcoded, so the dotted path stays
# correct if this test module is moved or the step class is renamed.
TEST_STEP_PATH = f"{__name__}.{TestCourseModePriceRequestedPipelineStep.__name__}"


class TestTrackSelectionAPIRenderStartedPipelineStep(PipelineStep):
    """
    Utility pipeline step that adds a field to the track selection data.
    """

    def run_filter(self, user, course_key, track_selection_data):  # pylint: disable=arguments-differ
        """Pipeline step that adds a field built from the learner and the course."""
        track_selection_data["plugin_field"] = {"course_id": str(course_key), "username": user.username}
        return {"track_selection_data": track_selection_data}


TEST_RENDER_STEP_PATH = f"{__name__}.{TestTrackSelectionAPIRenderStartedPipelineStep.__name__}"


class TestTrackSelectionOfferPipelineStep(PipelineStep):
    """
    Utility pipeline step that adds a subscription offer to the track selection data.
    """

    def run_filter(self, track_selection_data, **kwargs):  # pylint: disable=arguments-differ
        """Pipeline step that offers Subscribe for a course in the catalog."""
        track_selection_data["extra_offer"] = TEST_CATALOG_OFFER
        return {"track_selection_data": track_selection_data}


TEST_OFFER_STEP_PATH = f"{__name__}.{TestTrackSelectionOfferPipelineStep.__name__}"


@ddt.ddt
@skip_unless_lms
@override_waffle_flag(COURSE_MODES_MFE_TRACK_SELECTION, active=True)
class TrackSelectionTestViews(ModuleStoreTestCase, APITestCase):
    """
    Tests for the Track Selection BFF API.
    """

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(
            start=datetime(2020, 1, 1),
            end=datetime(2028, 1, 1),
            enrollment_start=datetime(2020, 1, 1),
            enrollment_end=datetime(2028, 1, 1),
            emit_signals=True,
            modulestore=self.store,
        )
        chapter = BlockFactory(parent=self.course, category="chapter")
        BlockFactory(parent=chapter, category="sequential")

        CourseModeFactory(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=datetime(2028, 1, 1),
            min_price=TEST_LIST_PRICE,
            sku="ABCD1234",
        )
        VerificationDeadline.objects.create(
            course_key=self.course.id, deadline=datetime(2028, 1, 1)
        )

        CourseOverviewFactory.create(run="1T2020")
        update_outline_from_modulestore(self.course.id)

        self.user, password = self.create_non_staff_user()
        self.client.login(username=self.user.username, password=password)
        self.url = reverse("course_modes_api:v1:track-selection", args=[self.course.id])

    def test_verified_price_is_list_price_without_filter_step(self):
        """
        With no pipeline step configured, the verified mode price is the unmodified list price.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"]["min_price"] == str(TEST_LIST_PRICE)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.price.requested.v1": {
                "pipeline": [
                    TEST_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_verified_price_uses_course_mode_price_requested_filter(self):
        """
        A configured CourseModePriceRequested step overrides the verified mode price.

        This is the hook edx-enterprise uses to apply enterprise-negotiated pricing.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"]["min_price"] == str(TEST_DISCOUNTED_PRICE)

    def test_post_loads_track_selection_page_data(self):
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None
        assert response.data["audit_mode"] is not None
        assert response.data["course_name"] == self.course.display_name_with_default
        assert response.data["program"] is None
        assert response.data["options"] == [{"slug": "purchase", "url": None}, {"slug": "audit", "url": None}]

    def test_get_is_not_supported(self):
        response = self.client.get(self.url)
        assert response.status_code == 405

    def test_post_authenticated_audit_enrolled_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None
        assert response.data["audit_mode"] is not None
        assert response.data["course_name"] == self.course.display_name_with_default
        # without the upgrade intent, an audit learner may have just enrolled, so Audit stays
        assert [option["slug"] for option in response.data["options"]] == ["purchase", "audit"]

    def test_post_authenticated_verified_enrolled_user_redirects(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert "redirect_url" in response.data
        assert response.data["redirect_url"].endswith(f"/course/{self.course.id}/home")

    def test_post_authenticated_unenrolled_user_with_open_enrollment(self):
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None

    def test_post_authenticated_previously_enrolled_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["verified_mode"] is not None

    def test_post_unauthenticated_user(self):
        self.client.logout()
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 401

    def test_post_unknown_course_redirects_to_legacy_choose(self):
        url = reverse(
            "course_modes_api:v1:track-selection",
            args=["course-v1:unknown+course+2T2020"],
        )
        response = self.client.post(url, {}, format="json")
        assert response.status_code == 200
        assert "redirect_url" in response.data
        assert response.data["redirect_url"].endswith(
            "/course_modes/choose/course-v1:unknown+course+2T2020/"
        )

    @override_waffle_flag(COURSE_MODES_MFE_TRACK_SELECTION, active=False)
    def test_waffle_flag_disabled(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 404

    def test_post_audit_track_selection_enrolls_and_redirects(self):
        response = self.client.post(self.url, {"mode": CourseMode.AUDIT}, format="json")
        assert response.status_code == 200
        assert "redirect_url" in response.data

        mode, is_active = CourseEnrollment.enrollment_mode_for_user(
            self.user, self.course.id
        )
        assert mode == CourseMode.AUDIT
        assert is_active

    def test_post_verified_track_selection_redirects_to_verification(self):
        response = self.client.post(
            self.url,
            {"mode": CourseMode.VERIFIED, "contribution": "149"},
            format="json",
        )
        assert response.status_code == 200
        assert "redirect_url" in response.data

    def test_post_invalid_mode_returns_error(self):
        response = self.client.post(self.url, {"mode": "unsupported"}, format="json")
        assert response.status_code == 400
        assert response.data["error"] == "Enrollment mode not supported"

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_RENDER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_page_data_uses_track_selection_render_filter(self):
        """
        A configured TrackSelectionAPIRenderStarted step can add fields to the page data.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["plugin_field"] == {
            "course_id": str(self.course.id),
            "username": self.user.username,
        }
        assert response.data["verified_mode"] is not None

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_page_data_options_use_the_plugin_offer(self):
        """
        Core picks the options after the filter, from the offer a plugin adds.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["options"] == [
            {"slug": "subscribe", "url": TEST_SUBSCRIBE_URL},
            {"slug": "audit", "url": None},
        ]

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    @mock.patch(
        "common.djangoapps.course_modes.track_selection_data.get_track_options",
        return_value=[{"slug": "subscribe", "url": TEST_SUBSCRIBE_URL}],
    )
    def test_post_single_option_redirects_to_its_link(self, _mock_get_track_options):
        """
        With a single option that has a link, the learner skips the page and goes to its link.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data == {"redirect_url": TEST_SUBSCRIBE_URL}

    @mock.patch("common.djangoapps.course_modes.track_selection_data.ProgramDataExtender")
    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs")
    def test_post_page_data_offers_the_program(self, mock_get_programs, mock_extender):
        """
        An active program of the course run that the learner can buy in one click is an option.
        """
        mock_get_programs.return_value = [{"status": "retired"}, {"status": "active"}]
        mock_extender.return_value.extend.return_value = {
            "uuid": TEST_PROGRAM_UUID,
            "title": "Test Program",
            "courses": [{}, {}, {}],
            "is_learner_eligible_for_one_click_purchase": True,
            "skus": ["SKU1", "SKU2"],
            "discount_data": {"total_incl_tax_excl_discounts": 300.0, "total_incl_tax": 270.0, "currency": "USD"},
        }
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        program = response.data["program"]
        assert program["url"].endswith(f"?sku=SKU1&sku=SKU2&bundle={TEST_PROGRAM_UUID}")
        assert {key: program[key] for key in ("title", "number_of_courses", "price", "discounted_price")} == {
            "title": "Test Program",
            "number_of_courses": 3,
            "price": 300.0,
            "discounted_price": 270.0,
        }
        assert [option["slug"] for option in response.data["options"]] == ["purchase", "program", "audit"]
        mock_extender.assert_called_once_with({"status": "active"}, self.user)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1": {
                "pipeline": [
                    TEST_OFFER_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_post_upgrading_audit_learner_chooses_subscribe_or_purchase(self):
        """
        An audit learner who came to upgrade a catalog course chooses between Subscribe and Purchase.
        """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {"intent": "upgrade"}, format="json")
        assert response.status_code == 200
        assert [option["slug"] for option in response.data["options"]] == ["subscribe", "purchase"]

    def test_post_upgrading_audit_learner_goes_to_the_checkout(self):
        """
        An audit learner who came to upgrade a course outside the catalog skips the page.
        """
        CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)
        response = self.client.post(self.url, {"intent": "upgrade"}, format="json")
        assert response.status_code == 200
        assert "/basket/add/?sku=ABCD1234&course_run_key=" in response.data["redirect_url"]

    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_programs", side_effect=Exception("down"))
    def test_post_page_data_leaves_out_a_program_that_cannot_be_read(self, _mock_get_programs):
        """
        An error while reading the program leaves the program out instead of failing the page.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["program"] is None
        assert [option["slug"] for option in response.data["options"]] == ["purchase", "audit"]

    @mock.patch("common.djangoapps.course_modes.track_selection_data.get_track_options", return_value=[])
    def test_post_without_options_redirects_to_the_course(self, _mock_get_track_options):
        """
        With nothing to choose, the learner skips the page and goes to the course.
        """
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == 200
        assert response.data["redirect_url"].endswith(f"/course/{self.course.id}/home")


@ddt.ddt
@skip_unless_lms
class GetTrackOptionsTests(SimpleTestCase):
    """
    Tests for the options the track selection page shows.
    """

    @ddt.data(
        (None, True, False, False, ["purchase", "audit"]),
        (None, False, False, False, ["purchase"]),
        (None, True, True, False, ["purchase", "program", "audit"]),
        (TEST_CATALOG_OFFER, True, True, False, ["subscribe", "program", "audit"]),
        ({"in_catalog": True, "is_subscriber": True}, True, True, False, ["upgrade"]),
        ({"in_catalog": False, "is_subscriber": True}, True, False, False, ["purchase", "audit"]),
        ({"in_catalog": True, "is_subscriber": None}, True, False, False, ["purchase", "audit"]),
        (TEST_CATALOG_OFFER, False, False, False, ["subscribe", "purchase"]),
        (None, True, True, True, ["purchase", "program"]),
        (TEST_CATALOG_OFFER, True, False, True, ["subscribe", "purchase"]),
    )
    @ddt.unpack
    def test_options(self, extra_offer, has_audit_mode, has_program, upgrading, expected):
        data = {
            "course_id": "course-v1:a+b+c",
            "verified_mode": {"use_ecommerce_payment_flow": False},
            "audit_mode": {"slug": CourseMode.AUDIT} if has_audit_mode else None,
            "program": {"url": "https://ecommerce.test/program"} if has_program else None,
            "extra_offer": extra_offer,
        }
        assert [option["slug"] for option in get_track_options(data, upgrading)] == expected

    def test_purchase_link(self):
        data = {
            "course_id": "course-v1:a+b+c",
            "verified_mode": {
                "sku": "ABCD1234",
                "use_ecommerce_payment_flow": True,
                "ecommerce_payment_page": "https://ecommerce.test/basket/add/",
            },
            "audit_mode": None,
        }
        purchase_url = "https://ecommerce.test/basket/add/?sku=ABCD1234&course_run_key=course-v1%3Aa%2Bb%2Bc"
        assert get_track_options(data) == [{"slug": "purchase", "url": purchase_url}]

    def test_subscriber_upgrade_needs_the_verified_mode(self):
        data = {
            "course_id": "course-v1:a+b+c",
            "verified_mode": None,
            "audit_mode": {"slug": CourseMode.AUDIT},
            "extra_offer": {"in_catalog": True, "is_subscriber": True},
        }
        assert get_track_options(data) == [{"slug": "audit", "url": None}]
