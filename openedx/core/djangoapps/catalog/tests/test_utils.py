"""Tests covering utilities for integrating with the catalog service."""

# pylint: disable=missing-docstring


from collections import defaultdict
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.utils.timezone import now
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.helpers import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.constants import COURSE_UNPUBLISHED
from openedx.core.djangoapps.catalog.cache import (
    CATALOG_COURSE_PROGRAMS_CACHE_KEY_TPL,
    COURSE_PROGRAMS_CACHE_KEY_TPL,
    PATHWAY_CACHE_KEY_TPL,
    PROGRAM_CACHE_KEY_TPL,
    PROGRAMS_BY_TYPE_CACHE_KEY_TPL,
    PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL,
    SITE_PATHWAY_IDS_CACHE_KEY_TPL,
    SITE_PROGRAM_UUIDS_CACHE_KEY_TPL,
)
from openedx.core.djangoapps.catalog.models import CatalogIntegration
from openedx.core.djangoapps.catalog.tests.factories import (
    CourseFactory,
    CourseRunFactory,
    PathwayFactory,
    ProgramFactory,
    ProgramTypeAttrsFactory,
    ProgramTypeFactory,
)
from openedx.core.djangoapps.catalog.tests.mixins import CatalogIntegrationMixin
from openedx.core.djangoapps.catalog.utils import (
    child_programs,
    course_run_keys_for_program,
    get_course_run_details,
    get_course_runs,
    get_course_runs_for_course,
    get_currency_data,
    get_localized_price_text,
    get_owners_for_course,
    get_pathways,
    get_program_types,
    get_programs,
    get_programs_by_type,
    get_programs_by_type_slug,
    get_visible_sessions_for_entitlement,
    is_course_run_in_program,
    normalize_program_type,
)
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms

UTILS_MODULE = "openedx.core.djangoapps.catalog.utils"
User = get_user_model()  # pylint: disable=invalid-name


@skip_unless_lms
@mock.patch(UTILS_MODULE + ".logger.info")
@mock.patch(UTILS_MODULE + ".logger.warning")
class TestGetPrograms(CacheIsolationTestCase):
    ENABLED_CACHES = ["default"]

    def setUp(self):
        super().setUp()
        self.site = SiteFactory()

    def test_get_many(self, mock_warning, mock_info):
        programs = ProgramFactory.create_batch(3)

        # Cache details for 2 of 3 programs.
        partial_programs = {PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"]): program for program in programs[:2]}
        cache.set_many(partial_programs, None)

        # When called before UUIDs are cached, the function should return an
        # empty list and log a warning.
        with with_site_configuration_context(domain=self.site.name, configuration={"COURSE_CATALOG_API_URL": "foo"}):
            assert get_programs(site=self.site) == []
            mock_warning.assert_called_once_with(
                f"Failed to get program UUIDs from the cache for site {self.site.domain}."
            )
            mock_warning.reset_mock()

        # Cache UUIDs for all 3 programs.
        cache.set(
            SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site.domain),
            [program["uuid"] for program in programs],
            None,
        )

        actual_programs = get_programs(site=self.site)

        # The 2 cached programs should be returned while info and warning
        # messages should be logged for the missing one.
        assert {program["uuid"] for program in actual_programs} == {
            program["uuid"] for program in partial_programs.values()
        }
        mock_info.assert_called_with("Failed to get details for 1 programs. Retrying.")
        mock_warning.assert_called_with(
            "Failed to get details for program {uuid} from the cache.".format(uuid=programs[2]["uuid"])
        )
        mock_warning.reset_mock()

        # We can't use a set comparison here because these values are dictionaries
        # and aren't hashable. We've already verified that all programs came out
        # of the cache above, so all we need to do here is verify the accuracy of
        # the data itself.
        for program in actual_programs:
            key = PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"])
            assert program == partial_programs[key]

        # Cache details for all 3 programs.
        all_programs = {PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"]): program for program in programs}
        cache.set_many(all_programs, None)

        actual_programs = get_programs(site=self.site)

        # All 3 programs should be returned.
        assert {program["uuid"] for program in actual_programs} == {
            program["uuid"] for program in all_programs.values()
        }
        assert not mock_warning.called

        for program in actual_programs:
            key = PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"])
            assert program == all_programs[key]

    @mock.patch(UTILS_MODULE + ".cache")
    def test_get_many_with_missing(self, mock_cache, mock_warning, mock_info):
        programs = ProgramFactory.create_batch(3)

        all_programs = {PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"]): program for program in programs}

        partial_programs = {PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"]): program for program in programs[:2]}

        def fake_get_many(keys):
            if len(keys) == 1:
                return {PROGRAM_CACHE_KEY_TPL.format(uuid=programs[-1]["uuid"]): programs[-1]}
            else:
                return partial_programs

        mock_cache.get.return_value = [program["uuid"] for program in programs]
        mock_cache.get_many.side_effect = fake_get_many

        with with_site_configuration_context(domain=self.site.name, configuration={"COURSE_CATALOG_API_URL": "foo"}):
            actual_programs = get_programs(site=self.site)

            # All 3 cached programs should be returned. An info message should be
            # logged about the one that was initially missing, but the code should
            # be able to stitch together all the details.
            assert {program["uuid"] for program in actual_programs} == {
                program["uuid"] for program in all_programs.values()
            }
            assert not mock_warning.called
            mock_info.assert_called_with("Failed to get details for 1 programs. Retrying.")

            for program in actual_programs:
                key = PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"])
                assert program == all_programs[key]

    def test_get_one(self, mock_warning, _mock_info):
        expected_program = ProgramFactory()
        expected_uuid = expected_program["uuid"]

        assert get_programs(uuid=expected_uuid) is None
        mock_warning.assert_called_once_with(f"Failed to get details for program {expected_uuid} from the cache.")
        mock_warning.reset_mock()

        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=expected_uuid), expected_program, None)

        actual_program = get_programs(uuid=expected_uuid)
        assert actual_program == expected_program
        assert not mock_warning.called

    def test_get_from_course(self, mock_warning, _mock_info):
        expected_program = ProgramFactory()
        expected_course = expected_program["courses"][0]["course_runs"][0]["key"]

        assert get_programs(course=expected_course) == []

        cache.set(COURSE_PROGRAMS_CACHE_KEY_TPL.format(course_run_id=expected_course), [expected_program["uuid"]], None)
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=expected_program["uuid"]), expected_program, None)

        actual_program = get_programs(course=expected_course)
        assert actual_program == [expected_program]
        assert not mock_warning.called

    def test_get_via_uuids(self, mock_warning, _mock_info):
        first_program = ProgramFactory()
        second_program = ProgramFactory()

        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=first_program["uuid"]), first_program, None)
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=second_program["uuid"]), second_program, None)

        results = get_programs(uuids=[first_program["uuid"], second_program["uuid"]])

        assert first_program in results
        assert second_program in results
        assert not mock_warning.called

    def test_get_from_catalog_course(self, mock_warning, _mock_info):
        expected_program = ProgramFactory()
        expected_catalog_course = expected_program["courses"][0]

        assert get_programs(catalog_course_uuid=expected_catalog_course["uuid"]) == []

        cache.set(
            CATALOG_COURSE_PROGRAMS_CACHE_KEY_TPL.format(course_uuid=expected_catalog_course["uuid"]),
            [expected_program["uuid"]],
            None,
        )
        cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=expected_program["uuid"]), expected_program, None)

        actual_program = get_programs(catalog_course_uuid=expected_catalog_course["uuid"])

        assert actual_program == [expected_program]
        assert not mock_warning.called


@skip_unless_lms
@mock.patch(UTILS_MODULE + ".logger.info")
@mock.patch(UTILS_MODULE + ".logger.warning")
class TestGetPathways(CacheIsolationTestCase):
    ENABLED_CACHES = ["default"]

    def setUp(self):
        super().setUp()
        self.site = SiteFactory()

    def test_get_many(self, mock_warning, mock_info):
        pathways = PathwayFactory.create_batch(3)

        # Cache details for 2 of 3 programs.
        partial_pathways = {PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"]): pathway for pathway in pathways[:2]}
        cache.set_many(partial_pathways, None)

        # When called before pathways are cached, the function should return an
        # empty list and log a warning.
        assert get_pathways(self.site) == []
        mock_warning.assert_called_once_with("Failed to get credit pathway ids from the cache.")
        mock_warning.reset_mock()

        # Cache all 3 pathways
        cache.set(
            SITE_PATHWAY_IDS_CACHE_KEY_TPL.format(domain=self.site.domain),
            [pathway["id"] for pathway in pathways],
            None,
        )

        actual_pathways = get_pathways(self.site)

        # The 2 cached pathways should be returned while info and warning
        # messages should be logged for the missing one.
        assert {pathway["id"] for pathway in actual_pathways} == {
            pathway["id"] for pathway in partial_pathways.values()
        }
        mock_info.assert_called_with("Failed to get details for 1 pathways. Retrying.")
        mock_warning.assert_called_with(
            "Failed to get details for credit pathway {id} from the cache.".format(id=pathways[2]["id"])
        )
        mock_warning.reset_mock()

        # We can't use a set comparison here because these values are dictionaries
        # and aren't hashable. We've already verified that all pathways came out
        # of the cache above, so all we need to do here is verify the accuracy of
        # the data itself.
        for pathway in actual_pathways:
            key = PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"])
            assert pathway == partial_pathways[key]

        # Cache details for all 3 pathways.
        all_pathways = {PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"]): pathway for pathway in pathways}
        cache.set_many(all_pathways, None)

        actual_pathways = get_pathways(self.site)

        # All 3 pathways should be returned.
        assert {pathway["id"] for pathway in actual_pathways} == {pathway["id"] for pathway in all_pathways.values()}
        assert not mock_warning.called

        for pathway in actual_pathways:
            key = PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"])
            assert pathway == all_pathways[key]

    @mock.patch(UTILS_MODULE + ".cache")
    def test_get_many_with_missing(self, mock_cache, mock_warning, mock_info):
        pathways = PathwayFactory.create_batch(3)

        all_pathways = {PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"]): pathway for pathway in pathways}

        partial_pathways = {PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"]): pathway for pathway in pathways[:2]}

        def fake_get_many(keys):
            if len(keys) == 1:
                return {PATHWAY_CACHE_KEY_TPL.format(id=pathways[-1]["id"]): pathways[-1]}
            else:
                return partial_pathways

        mock_cache.get.return_value = [pathway["id"] for pathway in pathways]
        mock_cache.get_many.side_effect = fake_get_many

        actual_pathways = get_pathways(self.site)

        # All 3 cached pathways should be returned. An info message should be
        # logged about the one that was initially missing, but the code should
        # be able to stitch together all the details.
        assert {pathway["id"] for pathway in actual_pathways} == {pathway["id"] for pathway in all_pathways.values()}
        assert not mock_warning.called
        mock_info.assert_called_with("Failed to get details for 1 pathways. Retrying.")

        for pathway in actual_pathways:
            key = PATHWAY_CACHE_KEY_TPL.format(id=pathway["id"])
            assert pathway == all_pathways[key]

    def test_get_one(self, mock_warning, _mock_info):
        expected_pathway = PathwayFactory()
        expected_id = expected_pathway["id"]

        assert get_pathways(self.site, pathway_id=expected_id) is None
        mock_warning.assert_called_once_with(f"Failed to get details for credit pathway {expected_id} from the cache.")
        mock_warning.reset_mock()

        cache.set(PATHWAY_CACHE_KEY_TPL.format(id=expected_id), expected_pathway, None)

        actual_pathway = get_pathways(self.site, pathway_id=expected_id)
        assert actual_pathway == expected_pathway
        assert not mock_warning.called


@mock.patch(UTILS_MODULE + ".get_api_data")
class TestGetProgramTypes(CatalogIntegrationMixin, TestCase):
    """Tests covering retrieval of program types from the catalog service."""

    @override_settings(COURSE_CATALOG_API_URL="https://api.example.com/v1/")
    def test_get_program_types(self, mock_get_edx_api_data):
        """Verify get_program_types returns the expected list of program types."""
        program_types = ProgramTypeFactory.create_batch(3)
        mock_get_edx_api_data.return_value = program_types

        # Catalog integration is disabled.
        data = get_program_types()
        assert data == []

        catalog_integration = self.create_catalog_integration()
        UserFactory(username=catalog_integration.service_username)
        data = get_program_types()
        assert data == program_types

        program = program_types[0]
        data = get_program_types(name=program["name"])
        assert data == program


@mock.patch(UTILS_MODULE + ".get_api_data")
class TestGetCurrency(CatalogIntegrationMixin, TestCase):
    """Tests covering retrieval of currency data from the catalog service."""

    @override_settings(COURSE_CATALOG_API_URL="https://api.example.com/v1/")
    def test_get_currency_data(self, mock_get_edx_api_data):
        """Verify get_currency_data returns the currency data."""
        currency_data = {"code": "CAD", "rate": 1.257237, "symbol": "$"}
        mock_get_edx_api_data.return_value = currency_data

        # Catalog integration is disabled.
        data = get_currency_data()
        assert data == []

        catalog_integration = self.create_catalog_integration()
        UserFactory(username=catalog_integration.service_username)
        data = get_currency_data()
        assert data == currency_data


@mock.patch(UTILS_MODULE + ".get_currency_data")
class TestGetLocalizedPriceText(TestCase):
    """
    Tests covering converting prices to a localized currency
    """

    def test_localized_string(self, mock_get_currency_data):
        currency_data = {
            "BEL": {"rate": 0.835621, "code": "EUR", "symbol": "\u20ac"},
            "GBR": {"rate": 0.737822, "code": "GBP", "symbol": "\u00a3"},
            "CAN": {"rate": 2, "code": "CAD", "symbol": "$"},
        }
        mock_get_currency_data.return_value = currency_data

        request = RequestFactory().get("/dummy-url")
        request.session = {"country_code": "CA"}
        expected_result = "$20 CAD"
        assert get_localized_price_text(10, request) == expected_result


@skip_unless_lms
@mock.patch(UTILS_MODULE + ".get_api_data")
class TestGetCourseRuns(CatalogIntegrationMixin, CacheIsolationTestCase):
    """
    Tests covering retrieval of course runs from the catalog service.
    """

    def setUp(self):
        super().setUp()

        self.catalog_integration = self.create_catalog_integration(cache_ttl=1)
        self.user = UserFactory(username=self.catalog_integration.service_username)

    def assert_contract(self, call_args):
        """
        Verify that API data retrieval utility is used correctly.
        """
        args, kwargs = call_args

        for arg in (self.catalog_integration, "course_runs"):
            assert arg in args

        assert (
            kwargs["base_api_url"] == self.catalog_integration.get_internal_api_url()
        )  # pylint: disable=protected-access, line-too-long

        querystring = {
            "page_size": 20,
            "exclude_utm": 1,
        }

        assert kwargs["querystring"] == querystring

        return args, kwargs

    def test_config_missing(self, mock_get_edx_api_data):
        """
        Verify that no errors occur when catalog config is missing.
        """
        CatalogIntegration.objects.all().delete()
        self.clear_caches()

        data = get_course_runs()
        assert not mock_get_edx_api_data.called
        assert data == []

    @mock.patch(UTILS_MODULE + ".logger.error")
    def test_service_user_missing(self, mock_log_error, mock_get_edx_api_data):
        """
        Verify that no errors occur when the catalog service user is missing.
        """
        catalog_integration = self.create_catalog_integration(service_username="nonexistent-user")

        data = get_course_runs()
        mock_log_error.assert_any_call(
            "Catalog service user with username [{username}] does not exist. "
            "{field} will not be retrieved.".format(
                username=catalog_integration.service_username,
                field="Course runs",
            )
        )
        assert not mock_get_edx_api_data.called
        assert data == []

    def test_get_course_runs(self, mock_get_edx_api_data):
        """
        Test retrieval of course runs.
        """
        catalog_course_runs = CourseRunFactory.create_batch(10)
        mock_get_edx_api_data.return_value = catalog_course_runs

        data = get_course_runs()
        assert mock_get_edx_api_data.called
        self.assert_contract(mock_get_edx_api_data.call_args)
        assert data == catalog_course_runs

    def test_get_course_runs_by_course(self, mock_get_edx_api_data):
        """
        Test retrievals of run from a Course.
        """
        catalog_course_runs = CourseRunFactory.create_batch(10)
        catalog_course = CourseFactory(course_runs=catalog_course_runs)
        mock_get_edx_api_data.return_value = catalog_course

        data = get_course_runs_for_course(course_uuid=str(catalog_course["uuid"]))
        assert mock_get_edx_api_data.called
        assert data == catalog_course_runs


@skip_unless_lms
@mock.patch(UTILS_MODULE + ".get_api_data")
class TestGetCourseOwners(CatalogIntegrationMixin, TestCase):
    """
    Tests covering retrieval of course runs from the catalog service.
    """

    def setUp(self):
        super().setUp()

        self.catalog_integration = self.create_catalog_integration(cache_ttl=1)
        self.user = UserFactory(username=self.catalog_integration.service_username)

    def test_get_course_owners_by_course(self, mock_get_edx_api_data):
        """
        Test retrieval of course runs.
        """
        catalog_course_runs = CourseRunFactory.create_batch(10)
        catalog_course = CourseFactory(course_runs=catalog_course_runs)
        mock_get_edx_api_data.return_value = catalog_course

        data = get_owners_for_course(course_uuid=str(catalog_course["uuid"]))
        assert mock_get_edx_api_data.called
        assert data == catalog_course["owners"]


@skip_unless_lms
@mock.patch(UTILS_MODULE + ".get_api_data")
class TestSessionEntitlement(CatalogIntegrationMixin, TestCase):
    """
    Test Covering data related Entitlements.
    """

    def setUp(self):
        super().setUp()

        self.catalog_integration = self.create_catalog_integration(cache_ttl=1)
        self.user = UserFactory(username=self.catalog_integration.service_username)
        self.tomorrow = now() + timedelta(days=1)

    def test_get_visible_sessions_for_entitlement(self, mock_get_edx_api_data):
        """
        Test retrieval of visible session entitlements.
        """
        catalog_course_run = CourseRunFactory.create()
        catalog_course = CourseFactory(course_runs=[catalog_course_run])
        mock_get_edx_api_data.return_value = catalog_course
        course_key = CourseKey.from_string(catalog_course_run.get("key"))
        course_overview = CourseOverviewFactory.create(id=course_key, start=self.tomorrow)
        CourseModeFactory.create(mode_slug=CourseMode.VERIFIED, min_price=100, course_id=course_overview.id)
        course_enrollment = CourseEnrollmentFactory(user=self.user, course=course_overview, mode=CourseMode.VERIFIED)
        entitlement = CourseEntitlementFactory(
            user=self.user, enrollment_course_run=course_enrollment, mode=CourseMode.VERIFIED
        )

        session_entitlements = get_visible_sessions_for_entitlement(entitlement)
        assert session_entitlements == [catalog_course_run]

    def test_get_visible_sessions_for_entitlement_expired_mode(self, mock_get_edx_api_data):
        """
        Test retrieval of visible session entitlements.
        """
        catalog_course_run = CourseRunFactory.create()
        catalog_course = CourseFactory(course_runs=[catalog_course_run])
        mock_get_edx_api_data.return_value = catalog_course
        course_key = CourseKey.from_string(catalog_course_run.get("key"))
        course_overview = CourseOverviewFactory.create(id=course_key, start=self.tomorrow)
        CourseModeFactory.create(
            mode_slug=CourseMode.VERIFIED,
            min_price=100,
            course_id=course_overview.id,
            expiration_datetime=now() - timedelta(days=1),
        )
        course_enrollment = CourseEnrollmentFactory(user=self.user, course=course_overview, mode=CourseMode.VERIFIED)
        entitlement = CourseEntitlementFactory(
            user=self.user, enrollment_course_run=course_enrollment, mode=CourseMode.VERIFIED
        )

        session_entitlements = get_visible_sessions_for_entitlement(entitlement)
        assert session_entitlements == [catalog_course_run]

    def test_unpublished_sessions_for_entitlement_when_enrolled(self, mock_get_edx_api_data):
        """
        Test unpublished course runs are part of visible session entitlements when the user
        is enrolled.
        """
        catalog_course_run = CourseRunFactory.create(status=COURSE_UNPUBLISHED)
        catalog_course = CourseFactory(course_runs=[catalog_course_run])
        mock_get_edx_api_data.return_value = catalog_course
        course_key = CourseKey.from_string(catalog_course_run.get("key"))
        course_overview = CourseOverviewFactory.create(id=course_key, start=self.tomorrow)
        CourseModeFactory.create(
            mode_slug=CourseMode.VERIFIED,
            min_price=100,
            course_id=course_overview.id,
            expiration_datetime=now() - timedelta(days=1),
        )
        course_enrollment = CourseEnrollmentFactory(user=self.user, course=course_overview, mode=CourseMode.VERIFIED)
        entitlement = CourseEntitlementFactory(
            user=self.user, enrollment_course_run=course_enrollment, mode=CourseMode.VERIFIED
        )

        session_entitlements = get_visible_sessions_for_entitlement(entitlement)
        assert session_entitlements == [catalog_course_run]

    def test_unpublished_sessions_for_entitlement(self, mock_get_edx_api_data):
        """
        Test unpublished course runs are not part of visible session entitlements when the user
        is not enrolled and upgrade deadline is passed.
        """
        catalog_course_run = CourseRunFactory.create(status=COURSE_UNPUBLISHED)
        catalog_course = CourseFactory(course_runs=[catalog_course_run])
        mock_get_edx_api_data.return_value = catalog_course
        course_key = CourseKey.from_string(catalog_course_run.get("key"))
        course_overview = CourseOverviewFactory.create(id=course_key, start=self.tomorrow)
        CourseModeFactory.create(
            mode_slug=CourseMode.VERIFIED,
            min_price=100,
            course_id=course_overview.id,
            expiration_datetime=now() - timedelta(days=1),
        )
        entitlement = CourseEntitlementFactory(user=self.user, mode=CourseMode.VERIFIED)

        session_entitlements = get_visible_sessions_for_entitlement(entitlement)
        assert not session_entitlements


@skip_unless_lms
@mock.patch(UTILS_MODULE + ".get_api_data")
class TestGetCourseRunDetails(CatalogIntegrationMixin, TestCase):
    """
    Tests covering retrieval of information about a specific course run from the catalog service.
    """

    def setUp(self):
        super().setUp()
        self.catalog_integration = self.create_catalog_integration(cache_ttl=1)
        self.user = UserFactory(username=self.catalog_integration.service_username)

    def test_get_course_run_details(self, mock_get_edx_api_data):
        """
        Test retrieval of details about a specific course run
        """
        course_run = CourseRunFactory()
        course_run_details = {
            "content_language": course_run["content_language"],
            "weeks_to_complete": course_run["weeks_to_complete"],
            "max_effort": course_run["max_effort"],
        }
        mock_get_edx_api_data.return_value = course_run_details
        data = get_course_run_details(course_run["key"], ["content_language", "weeks_to_complete", "max_effort"])
        assert mock_get_edx_api_data.called
        assert data == course_run_details


class TestProgramCourseRunCrawling(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.grandchild_1 = {
            "title": "grandchild 1",
            "curricula": [{"is_active": True, "courses": [], "programs": []}],
        }
        cls.grandchild_2 = {
            "title": "grandchild 2",
            "curricula": [
                {
                    "is_active": True,
                    "courses": [
                        {
                            "course_runs": [
                                {"key": "course-run-4"},
                            ],
                        }
                    ],
                    "programs": [],
                },
            ],
        }
        cls.grandchild_3 = {
            "title": "grandchild 3",
            "curricula": [{"is_active": False}],
        }
        cls.child_1 = {
            "title": "child 1",
            "curricula": [{"is_active": True, "courses": [], "programs": [cls.grandchild_1]}],
        }
        cls.child_2 = {
            "title": "child 2",
            "curricula": [
                {
                    "is_active": True,
                    "courses": [
                        {
                            "course_runs": [
                                {"key": "course-run-3"},
                            ],
                        }
                    ],
                    "programs": [cls.grandchild_2, cls.grandchild_3],
                },
            ],
        }
        cls.complex_program = {
            "title": "complex program",
            "curricula": [
                {
                    "is_active": True,
                    "courses": [
                        {
                            "course_runs": [
                                {"key": "course-run-2"},
                            ],
                        }
                    ],
                    "programs": [cls.child_1, cls.child_2],
                },
            ],
        }
        cls.simple_program = {
            "title": "simple program",
            "curricula": [
                {
                    "is_active": True,
                    "courses": [
                        {
                            "course_runs": [
                                {"key": "course-run-1"},
                            ],
                        }
                    ],
                    "programs": [cls.grandchild_1],
                },
            ],
        }
        cls.empty_program = {
            "title": "notice that I have a curriculum, but no programs inside it",
            "curricula": [
                {
                    "is_active": True,
                    "courses": [],
                    "programs": [],
                },
            ],
        }

    def test_child_programs_no_curriculum(self):
        program = {
            "title": "notice that I do not have a curriculum",
        }
        assert not child_programs(program)

    def test_child_programs_no_children(self):
        assert not child_programs(self.empty_program)

    def test_child_programs_one_child(self):
        assert [self.grandchild_1] == child_programs(self.simple_program)

    def test_child_programs_many_children(self):
        expected_children = [
            self.child_1,
            self.grandchild_1,
            self.child_2,
            self.grandchild_2,
            self.grandchild_3,
        ]
        assert expected_children == child_programs(self.complex_program)

    def test_course_run_keys_for_program_no_courses(self):
        assert set() == course_run_keys_for_program(self.empty_program)

    def test_course_run_keys_for_program_one_course(self):
        assert {"course-run-1"} == course_run_keys_for_program(self.simple_program)

    def test_course_run_keys_for_program_many_courses(self):
        expected_course_runs = {
            "course-run-2",
            "course-run-3",
            "course-run-4",
        }
        assert expected_course_runs == course_run_keys_for_program(self.complex_program)

    def test_is_course_run_in_program(self):
        assert is_course_run_in_program("course-run-4", self.complex_program)
        assert not is_course_run_in_program("course-run-5", self.complex_program)
        assert not is_course_run_in_program("course-run-4", self.simple_program)


@skip_unless_lms
class TestGetProgramsByType(CacheIsolationTestCase):
    """Test for the ``get_programs_by_type()`` and the ``get_programs_by_type_slug()`` functions."""

    ENABLED_CACHES = ["default"]

    @classmethod
    def setUpClass(cls):
        """Sets up program data."""
        super().setUpClass()
        cls.site = SiteFactory()
        cls.other_site = SiteFactory()
        cls.masters_program_1 = ProgramFactory.create(
            type="Masters", type_attrs=ProgramTypeAttrsFactory.create(slug="masters")
        )
        cls.masters_program_2 = ProgramFactory.create(
            type="Masters", type_attrs=ProgramTypeAttrsFactory.create(slug="masters")
        )
        cls.masters_program_other_site = ProgramFactory.create(
            type="Masters", type_attrs=ProgramTypeAttrsFactory.create(slug="masters")
        )
        cls.bachelors_program = ProgramFactory.create(
            type="Bachelors", type_attrs=ProgramTypeAttrsFactory.create(slug="bachelors")
        )
        cls.no_type_program = ProgramFactory.create(type=None, type_attrs=None)

    def setUp(self):
        """Loads program data into the cache before each test function."""
        super().setUp()
        self.init_cache()

    def init_cache(self):
        """This function plays the role of the ``cache_programs`` management command."""
        all_programs = [
            self.masters_program_1,
            self.masters_program_2,
            self.bachelors_program,
            self.no_type_program,
            self.masters_program_other_site,
        ]
        cached_programs = {PROGRAM_CACHE_KEY_TPL.format(uuid=program["uuid"]): program for program in all_programs}
        cache.set_many(cached_programs, None)

        programs_by_type = defaultdict(list)
        programs_by_type_slug = defaultdict(list)
        for program in all_programs:
            program_type = normalize_program_type(program.get("type"))
            program_type_slug = (program.get("type_attrs") or {}).get("slug")
            site_id = self.site.id

            if program == self.masters_program_other_site:
                site_id = self.other_site.id

            program_type_cache_key = PROGRAMS_BY_TYPE_CACHE_KEY_TPL.format(site_id=site_id, program_type=program_type)
            program_type_slug_cache_key = PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL.format(
                site_id=site_id, program_slug=program_type_slug
            )
            programs_by_type[program_type_cache_key].append(program["uuid"])
            programs_by_type_slug[program_type_slug_cache_key].append(program["uuid"])

        cache.set_many(programs_by_type, None)
        cache.set_many(programs_by_type_slug, None)

    def test_get_masters_programs(self):
        expected_programs = [self.masters_program_1, self.masters_program_2]
        self.assertCountEqual(expected_programs, get_programs_by_type(self.site, "masters"))
        self.assertCountEqual(expected_programs, get_programs_by_type_slug(self.site, "masters"))

    def test_get_bachelors_programs(self):
        expected_programs = [self.bachelors_program]
        assert expected_programs == get_programs_by_type(self.site, "bachelors")
        assert expected_programs == get_programs_by_type_slug(self.site, "bachelors")

    def test_get_no_such_type_programs(self):
        expected_programs = []
        assert expected_programs == get_programs_by_type(self.site, "doctorate")
        assert expected_programs == get_programs_by_type_slug(self.site, "doctorate")

    def test_get_masters_programs_other_site(self):
        expected_programs = [self.masters_program_other_site]
        assert expected_programs == get_programs_by_type(self.other_site, "masters")
        assert expected_programs == get_programs_by_type_slug(self.other_site, "masters")

    def test_get_programs_null_type(self):
        expected_programs = [self.no_type_program]
        assert expected_programs == get_programs_by_type(self.site, None)
        assert expected_programs == get_programs_by_type_slug(self.site, None)

    def test_get_programs_false_type(self):
        expected_programs = []
        assert expected_programs == get_programs_by_type(self.site, False)
        assert expected_programs == get_programs_by_type_slug(self.site, False)

    def test_normalize_program_type(self):
        assert "none" == normalize_program_type(None)
        assert "false" == normalize_program_type(False)
        assert "true" == normalize_program_type(True)
        assert "" == normalize_program_type("")
        assert "masters" == normalize_program_type("Masters")
        assert "masters" == normalize_program_type("masters")
