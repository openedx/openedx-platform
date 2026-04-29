"""
Unit tests for Programs REST APIs and Views
"""

from unittest import mock
from uuid import uuid4

from django.core.cache import cache
from django.test.utils import override_settings
from django.urls import reverse_lazy
from enterprise.models import EnterpriseCourseEnrollment

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.program_enrollments.rest_api.v1.tests.test_views import ProgramCacheMixin
from lms.djangoapps.program_enrollments.tests.factories import ProgramEnrollmentFactory
from openedx.core.djangoapps.catalog.cache import SITE_PROGRAM_UUIDS_CACHE_KEY_TPL
from openedx.core.djangoapps.catalog.constants import PathwayType
from openedx.core.djangoapps.catalog.tests.factories import (
    CourseFactory,
    CourseRunFactory,
    PathwayFactory,
    ProgramFactory,
)
from openedx.core.djangoapps.programs.tests.mixins import ProgramsApiConfigMixin
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.enterprise_support.api import enterprise_is_enabled
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerFactory,
    EnterpriseCustomerUserFactory,
)
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory as ModuleStoreCourseFactory

PROGRAMS_UTILS_MODULE = "openedx.core.djangoapps.programs.utils"


@skip_unless_lms
@mock.patch(PROGRAMS_UTILS_MODULE + ".get_pathways")
@mock.patch(PROGRAMS_UTILS_MODULE + ".get_programs")
class TestProgramProgressDetailView(ProgramsApiConfigMixin, SharedModuleStoreTestCase):
    """Unit tests for the program progress detail page."""

    program_uuid = str(uuid4())
    url = reverse_lazy(
        "openedx.core.djangoapps.programs:v0:program_progress_detail", kwargs={"program_uuid": program_uuid}
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        modulestore_course = ModuleStoreCourseFactory()
        course_run = CourseRunFactory(key=str(modulestore_course.id))  # pylint: disable=no-member
        course = CourseFactory(course_runs=[course_run])

        cls.program_data = ProgramFactory(uuid=cls.program_uuid, courses=[course])
        cls.pathway_data = PathwayFactory()
        cls.program_data["pathway_ids"] = [cls.pathway_data["id"]]
        cls.pathway_data["program_uuids"] = [cls.program_data["uuid"]]
        del cls.pathway_data["programs"]  # pylint: disable=unsupported-delete-operation

    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    def assert_program_data_present(self, response):
        """Verify that program data is present."""
        self.assertContains(response, "program_data")
        self.assertContains(response, "course_data")
        self.assertContains(response, "urls")
        self.assertContains(response, "certificate_data")
        self.assertContains(response, self.program_data["title"])

    def assert_pathway_data_present(self, response):
        """Verify that the correct pathway data is present."""
        self.assertContains(response, "industry_pathways")
        self.assertContains(response, "credit_pathways")

        industry_pathways = response.data["industry_pathways"]
        credit_pathways = response.data["credit_pathways"]
        if self.pathway_data["pathway_type"] == PathwayType.CREDIT.value:
            (credit_pathway,) = credit_pathways  # Verify that there is only one credit pathway
            assert self.pathway_data == credit_pathway
            assert [] == industry_pathways
        elif self.pathway_data["pathway_type"] == PathwayType.INDUSTRY.value:
            (industry_pathway,) = industry_pathways  # Verify that there is only one industry pathway
            assert self.pathway_data == industry_pathway
            assert [] == credit_pathways

    def test_api_returns_correct_program_data(self, mock_get_programs, mock_get_pathways):
        """
        Verify that API returns program data in the correct format.
        """
        self.create_programs_config()
        mock_get_programs.return_value = self.program_data
        mock_get_pathways.return_value = self.pathway_data

        with mock.patch("openedx.core.djangoapps.programs.rest_api.v1.views.get_certificates") as certs:
            certs.return_value = [{"type": "program", "url": "/"}]
            response = self.client.get(self.url)

        self.assertEqual(200, response.status_code)  # noqa: PT009
        self.assert_program_data_present(response)
        self.assert_pathway_data_present(response)

    def test_login_required(self, mock_get_programs, mock_get_pathways):
        """
        Verify that API returns 401 to an unauthenticated user.
        """
        self.create_programs_config()
        mock_get_programs.return_value = self.program_data
        mock_get_pathways.return_value = self.pathway_data

        self.client.logout()

        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_404_if_no_program_data(self, mock_get_programs, _mock_get_pathways):  # noqa: PT019
        """
        Verify that the API returns 404 if program data is not available.
        """
        self.create_programs_config()

        mock_get_programs.return_value = {}

        response = self.client.get(self.url)
        assert response.status_code == 404
        assert response.data["error_code"] == "No program data available."


@skip_unless_lms
class TestProgramsEnterpriseView(SharedModuleStoreTestCase, ProgramCacheMixin):
    """Unit tests for the program details page."""

    enterprise_uuid = str(uuid4())
    program_uuid = str(uuid4())
    url = reverse_lazy("openedx.core.djangoapps.programs:v0:program_list", kwargs={"enterprise_uuid": enterprise_uuid})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.user = UserFactory()
        modulestore_course = ModuleStoreCourseFactory()
        course_run = CourseRunFactory(key=str(modulestore_course.id))
        course = CourseFactory(course_runs=[course_run])
        enterprise_customer = EnterpriseCustomerFactory(uuid=cls.enterprise_uuid)
        enterprise_customer_user = EnterpriseCustomerUserFactory(
            user_id=cls.user.id, enterprise_customer=enterprise_customer
        )
        CourseEnrollmentFactory(is_active=True, course_id=modulestore_course.id, user=cls.user)
        EnterpriseCourseEnrollmentFactory(
            course_id=modulestore_course.id,
            enterprise_customer_user=enterprise_customer_user,
        )

        cls.program = ProgramFactory(
            uuid=cls.program_uuid,
            courses=[course],
            title="Journey to cooking",
            type="MicroMasters",
            authoring_organizations=[
                {
                    "key": "MAX",
                    "logo_image_url": "http://test.org/media/organization/logos/test-logo.png",
                }
            ],
        )
        cls.site = SiteFactory(domain="test.localhost")

    def setUp(self):
        super().setUp()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.set_program_in_catalog_cache(self.program_uuid, self.program)
        ProgramEnrollmentFactory.create(
            user=self.user,
            program_uuid=self.program_uuid,
            external_user_key="0001",
        )

    @with_site_configuration(configuration={"COURSE_CATALOG_API_URL": "foo"})
    @override_settings(FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=True))
    @enterprise_is_enabled()
    def test_program_list_enterprise(self):
        """
        Verify API returns proper response.
        """
        cache.set(
            SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site.domain),
            [self.program_uuid],
            None,
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        program = response.data[0]

        assert len(program)
        assert program["uuid"] == self.program["uuid"]
        assert program["title"] == self.program["title"]
        assert program["type"] == self.program["type"]
        assert program["authoring_organizations"] == self.program["authoring_organizations"]
        assert program["banner_image"] == self.program["banner_image"]
        assert program["progress"] == {
            "uuid": self.program["uuid"],
            "completed": 0,
            "in_progress": 0,
            "not_started": 1,
            "all_unenrolled": False,
        }

    @with_site_configuration(configuration={"COURSE_CATALOG_API_URL": "foo"})
    @override_settings(FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=True))
    @enterprise_is_enabled()
    def test_program_empty_list_if_no_enterprise_enrollments(self):
        """
        Verify API returns empty response if no enterprise enrollments exists for a learner.
        """
        # delete all enterprise course enrollments for the user
        EnterpriseCourseEnrollment.objects.filter(enterprise_customer_user__user_id=self.user.id).delete()

        cache.set(
            SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site.domain),
            [self.program_uuid],
            None,
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertEqual(response.data, [])  # noqa: PT009


@skip_unless_lms
class TestProgramsB2CView(SharedModuleStoreTestCase, ProgramCacheMixin):
    """Unit tests for the program details page."""

    program_uuid = str(uuid4())
    url = reverse_lazy("openedx.core.djangoapps.programs:v0:program_list_b2c")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.user = UserFactory()
        modulestore_course = ModuleStoreCourseFactory()
        course_run = CourseRunFactory(key=str(modulestore_course.id))
        course = CourseFactory(course_runs=[course_run])

        CourseEnrollmentFactory(is_active=True, course_id=modulestore_course.id, user=cls.user)

        cls.program = ProgramFactory(
            uuid=cls.program_uuid,
            courses=[course],
            title="Journey to cooking",
            type="MicroMasters",
            authoring_organizations=[
                {
                    "key": "MAX",
                    "logo_image_url": "http://test.org/media/organization/logos/test-logo.png",
                }
            ],
        )
        cls.site = SiteFactory(domain="test.localhost")

    def setUp(self):
        super().setUp()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.set_program_in_catalog_cache(self.program_uuid, self.program)
        ProgramEnrollmentFactory.create(
            user=self.user,
            program_uuid=self.program_uuid,
            external_user_key="0001",
        )

    @with_site_configuration(configuration={"COURSE_CATALOG_API_URL": "foo"})
    def test_program_list_b2c(self):
        """
        Verify API returns proper response.
        """
        cache.set(
            SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site.domain),
            [self.program_uuid],
            None,
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        program = response.data[0]

        assert len(program)
        assert program["uuid"] == self.program["uuid"]
        assert program["title"] == self.program["title"]
        assert program["type"] == self.program["type"]
        assert program["authoring_organizations"] == self.program["authoring_organizations"]
        assert program["banner_image"] == self.program["banner_image"]
        assert program["progress"] == {
            "uuid": self.program["uuid"],
            "completed": 0,
            "in_progress": 0,
            "not_started": 1,
            "all_unenrolled": False,
        }

    @with_site_configuration(configuration={"COURSE_CATALOG_API_URL": "foo"})
    def test_program_empty_list_if_no_enrollments(self):
        """
        Verify API returns empty response if no enrollments exists for a learner.
        """
        CourseEnrollment.objects.filter(user=self.user).delete()

        cache.set(
            SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site.domain),
            [self.program_uuid],
            None,
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertEqual(response.data, [])  # noqa: PT009
