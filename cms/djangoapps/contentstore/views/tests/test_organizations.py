"""Tests covering the Organizations listing on the Studio home."""


import json

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from organizations.api import add_organization

from cms.djangoapps.course_creators.models import CourseCreator
from common.djangoapps.student.roles import OrgStaffRole
from common.djangoapps.student.tests.factories import UserFactory

from ..course import get_allowed_organizations_for_libraries


class TestOrganizationListing(TestCase):
    """Verify Organization listing behavior."""
    def setUp(self):
        super().setUp()
        self.password = "password1234"
        self.staff = UserFactory(is_staff=True, password=self.password)
        self.client.login(username=self.staff.username, password=self.password)
        self.org_names_listing_url = reverse('organizations')
        self.org_short_names = ["alphaX", "betaX", "orgX"]
        for index, short_name in enumerate(self.org_short_names):
            add_organization(organization_data={
                'name': 'Test Organization %s' % index,
                'short_name': short_name,
                'description': 'Testing Organization %s Description' % index,
            })

    def test_organization_list(self):
        """Verify that the organization names list api returns list of organization short names."""
        response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        org_names = json.loads(response.content.decode('utf-8'))
        self.assertEqual(org_names, self.org_short_names)


class TestOrganizationsForLibraries(TestCase):
    """
    Verify who is allowed to create Libraries.

    This uses some low-level implementation details to set up course creator and
    org staff data, which should be replaced by API calls.

    The behavior of this call depends on two FEATURES toggles:

    * ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES
    * ENABLE_CREATOR_GROUP
    """

    @classmethod
    def setUpTestData(cls):
        cls.library_author = UserFactory(is_staff=False)
        cls.org_short_names = ["OrgStaffOrg", "CreatorOrg", "RandomOrg"]
        cls.orgs = {}
        for index, short_name in enumerate(cls.org_short_names):
            cls.orgs[short_name] = add_organization(organization_data={
                'name': 'Test Organization %s' % index,
                'short_name': short_name,
                'description': 'Testing Organization %s Description' % index,
            })

        # Our user is an org staff for OrgStaffOrg
        OrgStaffRole("OrgStaffOrg").add_users(cls.library_author)

        # Our user is also a CourseCreator in CreatorOrg
        creator = CourseCreator.objects.create(
            user=cls.library_author,
            state=CourseCreator.GRANTED,
            all_organizations=False,
        )
        # The following is because course_creators app logic assumes that all
        # updates to CourseCreator go through the CourseCreatorAdmin.
        # Specifically, CourseCreatorAdmin.save_model() attaches the current
        # request.user to the model instance's .admin field, and then the
        # course_creator_organizations_changed_callback() signal handler assumes
        # creator.admin is present. I think that code could use some judicious
        # refactoring, but I'm just writing this test as part of a last-minute
        # Ulmo bug fix, and I don't want to add risk by refactoring something as
        # critical-path as course_creators as part of this work.
        creator.admin = UserFactory(is_staff=True)
        creator.organizations.add(
            cls.orgs["CreatorOrg"]['id']
        )

    @override_settings(
        FEATURES={
            **settings.FEATURES,
            'ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES': False,
            'ENABLE_CREATOR_GROUP': False,
        }
    )
    def test_both_toggles_disabled(self):
        allowed_orgs = get_allowed_organizations_for_libraries(self.library_author)
        assert allowed_orgs == []

    @override_settings(
        FEATURES={
            **settings.FEATURES,
            'ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES': True,
            'ENABLE_CREATOR_GROUP': True,
        }
    )
    def test_both_toggles_enabled(self):
        allowed_orgs = get_allowed_organizations_for_libraries(self.library_author)
        assert allowed_orgs == ["CreatorOrg", "OrgStaffOrg"]

    @override_settings(
        FEATURES={
            **settings.FEATURES,
            'ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES': True,
            'ENABLE_CREATOR_GROUP': False,
        }
    )
    def test_org_staff_enabled(self):
        allowed_orgs = get_allowed_organizations_for_libraries(self.library_author)
        assert allowed_orgs == ["OrgStaffOrg"]

    @override_settings(
        FEATURES={
            **settings.FEATURES,
            'ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES': False,
            'ENABLE_CREATOR_GROUP': True,
        }
    )
    def test_creator_group_enabled(self):
        allowed_orgs = get_allowed_organizations_for_libraries(self.library_author)
        assert allowed_orgs == ["CreatorOrg"]
