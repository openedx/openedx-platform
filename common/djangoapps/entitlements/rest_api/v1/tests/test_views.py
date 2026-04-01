"""
Test file to test the Entitlement API Views.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils.timezone import now
from opaque_keys.edx.locator import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import TEST_PASSWORD, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.models import DynamicUpgradeDeadlineConfiguration
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangoapps.user_api.models import UserOrgTag
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import \
    ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)

# Entitlements is not in CMS' INSTALLED_APPS so these imports will error during test collection
if settings.ROOT_URLCONF == 'lms.urls':
    from common.djangoapps.entitlements.models import (  # lint-amnesty, pylint: disable=line-too-long
        CourseEntitlement,
        CourseEntitlementPolicy,
        CourseEntitlementSupportDetail
    )
    from common.djangoapps.entitlements.rest_api.v1.serializers import CourseEntitlementSerializer
    from common.djangoapps.entitlements.rest_api.v1.views import set_entitlement_policy
    from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory


@skip_unless_lms
class EntitlementViewSetTest(ModuleStoreTestCase):
    """
    Tests for the Entitlements API Views.
    """
    ENTITLEMENTS_DETAILS_PATH = 'entitlements_api:v1:entitlements-detail'

    def setUp(self):
        super().setUp()
        self.user = UserFactory(is_staff=True)
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.course = CourseFactory()
        self.course_mode = CourseModeFactory(
            course_id=self.course.id,  # pylint: disable=no-member
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )

        self.entitlements_list_url = reverse('entitlements_api:v1:entitlements-list')

    def _get_data_set(self, user, course_uuid):
        """
        Get a basic data set for an entitlement
        """
        return {
            "user": user.username,
            "mode": CourseMode.VERIFIED,
            "course_uuid": course_uuid,
            "order_number": "EDX-1001",
        }

    def _assert_default_policy(self, policy):
        """
        Assert that a policy is equal to the default Course Entitlement Policy.
        """
        default_policy = CourseEntitlementPolicy()
        assert policy.expiration_period == default_policy.expiration_period
        assert policy.refund_period == default_policy.refund_period
        assert policy.regain_period == default_policy.regain_period
        assert policy.mode == default_policy.mode

    def test_auth_required(self):
        self.client.logout()
        response = self.client.get(self.entitlements_list_url)
        assert response.status_code == 401

    def test_staff_user_not_required_for_get(self):
        not_staff_user = UserFactory()
        self.client.login(username=not_staff_user.username, password=TEST_PASSWORD)
        response = self.client.get(self.entitlements_list_url)
        assert response.status_code == 200

    def test_add_entitlement_with_missing_data(self):
        entitlement_data_missing_parts = self._get_data_set(self.user, str(uuid.uuid4()))
        entitlement_data_missing_parts.pop('mode')
        entitlement_data_missing_parts.pop('course_uuid')

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data_missing_parts),
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_staff_user_required_for_post(self):
        not_staff_user = UserFactory()
        self.client.login(username=not_staff_user.username, password=TEST_PASSWORD)

        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 403

    def test_staff_user_required_for_delete(self):
        not_staff_user = UserFactory()
        self.client.login(username=not_staff_user.username, password=TEST_PASSWORD)

        course_entitlement = CourseEntitlementFactory.create()
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(course_entitlement.uuid)])

        response = self.client.delete(
            url,
            content_type='application/json',
        )
        assert response.status_code == 403

    def test_add_entitlement(self):
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        results = response.data

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        assert results == CourseEntitlementSerializer(course_entitlement).data

    def test_add_duplicate_entitlement(self):
        """
        Request with identical course_uuid and order_number should not create duplicate
        entitlement
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 400
        course_entitlement = CourseEntitlement.objects.filter(
            course_uuid=course_uuid,
            order_number=entitlement_data['order_number']
        )
        assert course_entitlement.count() == 1

    def test_order_number_null(self):
        """
        Test that for same course_uuid order_number set to null is treated as unique
        entitlement
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['order_number'] = None

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        course_entitlement = CourseEntitlement.objects.filter(
            course_uuid=course_uuid,
            order_number=entitlement_data['order_number']
        )
        assert course_entitlement.count() == 2

    def test_default_no_policy_entry(self):
        """
        Verify that, when there are no entries in the course entitlement policy table,
        the default policy is used for a newly created entitlement.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        self._assert_default_policy(course_entitlement.policy)

    def test_default_no_matching_policy_entry(self):
        """
        Verify that, when no course entitlement policy is found with the same mode or site
        as the created entitlement, the default policy is used for the entitlement.
        """
        CourseEntitlementPolicy.objects.create(mode=CourseMode.PROFESSIONAL, site=None)
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        self._assert_default_policy(course_entitlement.policy)

    def test_set_custom_mode_policy_on_create(self):
        """
        Verify that, when there does not exist a course entitlement policy with the same mode and site as
        a created entitlement, but there does exist a policy with the same mode and a null site,
        that policy is assigned to the entitlement.
        """
        policy = CourseEntitlementPolicy.objects.create(mode=CourseMode.PROFESSIONAL, site=None)
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['mode'] = CourseMode.PROFESSIONAL

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        assert course_entitlement.policy == policy

    # To verify policy selecting behavior involving site specificity, we interact directly
    # with the 'set_entitlement_policy' method due to an inablity to predict or manually assign
    # the site associated with the requests made in unittests.
    def test_set_custom_site_policy_on_create(self):
        """
        Verify that, when there does not exist a course entitlement policy with the same mode and site as
        a created entitlement, but there does exist a policy with the same site and a null mode,
        that policy is assigned to the entitlement.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )

        policy_site = SiteFactory.create()
        policy = CourseEntitlementPolicy.objects.create(mode=None, site=policy_site)

        set_entitlement_policy(course_entitlement, policy_site)
        assert course_entitlement.policy == policy

    def test_set_policy_match_site_over_mode(self):
        """
        Verify that, when both a mode-agnostic policy matching the site of a created entitlement and a site-agnostic
        policy matching the mode of a created entitlement exist but no policy matching both the site and mode of the
        created entitlement exists, the site-specific (mode-agnostic) policy matching the entitlement is selected over
        the mode-specific (site-agnostic) policy.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )

        policy_site = SiteFactory.create()
        policy = CourseEntitlementPolicy.objects.create(mode=None, site=policy_site)
        CourseEntitlementPolicy.objects.create(mode=entitlement_data['mode'], site=None)

        set_entitlement_policy(course_entitlement, policy_site)
        assert course_entitlement.policy == policy

    def test_set_policy_site_and_mode_specific(self):
        """
        Verify that, when there exists a policy matching both the mode and site of the a given course entitlement,
        it is selected over appropriate site- and mode-specific (mode- and site-agnostic) policies and the default
        policy for assignment to the entitlement.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['mode'] = CourseMode.PROFESSIONAL

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )

        policy_site = SiteFactory.create()
        policy = CourseEntitlementPolicy.objects.create(mode=entitlement_data['mode'], site=policy_site)
        CourseEntitlementPolicy.objects.create(mode=entitlement_data['mode'], site=None)
        CourseEntitlementPolicy.objects.create(mode=None, site=policy_site)

        set_entitlement_policy(course_entitlement, policy_site)
        assert course_entitlement.policy == policy

    def test_professional_policy_for_no_id_professional(self):
        """
        Verify that when there exists a policy with a professional mode that it is assigned
        to new entitlements with the mode no-id-professional.
        """
        policy = CourseEntitlementPolicy.objects.create(mode=CourseMode.PROFESSIONAL)
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['mode'] = CourseMode.NO_ID_PROFESSIONAL_MODE

        self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        assert course_entitlement.policy == policy

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_owners_for_course")
    def test_email_opt_in_single_org(self, mock_get_owners):
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['email_opt_in'] = True

        org = 'particularly'
        mock_get_owners.return_value = [{'key': org}]

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201

        result_obj = UserOrgTag.objects.get(user=self.user, org=org, key='email-optin')
        assert result_obj.value == 'True'

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_owners_for_course")
    def test_email_opt_in_multiple_orgs(self, mock_get_owners):
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['email_opt_in'] = True

        org_1 = 'particularly'
        org_2 = 'underwood'
        mock_get_owners.return_value = [{'key': org_1}, {'key': org_2}]

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201

        result_obj = UserOrgTag.objects.get(user=self.user, org=org_1, key='email-optin')
        assert result_obj.value == 'True'
        result_obj = UserOrgTag.objects.get(user=self.user, org=org_2, key='email-optin')
        assert result_obj.value == 'True'

    def test_add_entitlement_with_support_detail(self):
        """
        Verify that an EntitlementSupportDetail entry is made when the request includes support interaction information.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        entitlement_data['support_details'] = [
            {
                "action": "CREATE",
                "comments": "Family emergency."
            },
        ]

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        results = response.data

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        assert results == CourseEntitlementSerializer(course_entitlement).data

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_add_entitlement_and_upgrade_audit_enrollment(self, mock_get_course_runs):
        """
        Verify that if an entitlement is added for a user, if the user has one upgradeable enrollment
        that enrollment is upgraded to the mode of the entitlement and linked to the entitlement.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        mock_get_course_runs.return_value = [{'key': str(self.course.id)}]  # pylint: disable=no-member

        # Add an audit course enrollment for user.
        enrollment = CourseEnrollment.enroll(
            self.user,
            self.course.id,  # pylint: disable=no-member
            mode=CourseMode.AUDIT)

        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        results = response.data

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        # Assert that enrollment mode is now verified
        enrollment_mode = CourseEnrollment.enrollment_mode_for_user(
            self.user,
            self.course.id  # pylint: disable=no-member
        )[0]
        assert enrollment_mode == course_entitlement.mode
        assert course_entitlement.enrollment_course_run == enrollment
        assert results == CourseEntitlementSerializer(course_entitlement).data

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_add_entitlement_and_upgrade_audit_enrollment_with_dynamic_deadline(self, mock_get_course_runs):
        """
        Verify that if an entitlement is added for a user, if the user has one upgradeable enrollment
        that enrollment is upgraded to the mode of the entitlement and linked to the entitlement regardless of
        dynamic upgrade deadline being set.
        """
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        course = CourseFactory.create(self_paced=True)
        course_uuid = uuid.uuid4()
        CourseModeFactory(
            course_id=course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )

        # Set up Entitlement
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        mock_get_course_runs.return_value = [{'key': str(course.id)}]

        # Add an audit course enrollment for user.
        enrollment = CourseEnrollment.enroll(self.user, course.id, mode=CourseMode.AUDIT)

        # Set an expired dynamic upgrade deadline
        enrollment.schedule.upgrade_deadline = now() + timedelta(days=-2)
        enrollment.schedule.save()

        # The upgrade should complete and ignore the deadline
        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        results = response.data

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        # Assert that enrollment mode is now verified
        enrollment_mode = CourseEnrollment.enrollment_mode_for_user(self.user, course.id)[0]
        assert enrollment_mode == course_entitlement.mode
        assert course_entitlement.enrollment_course_run == enrollment
        assert results == CourseEntitlementSerializer(course_entitlement).data

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_add_entitlement_inactive_audit_enrollment(self, mock_get_course_runs):
        """
        Verify that if an entitlement is added for a user, if the user has an inactive audit enrollment
        that enrollment is NOT upgraded to the mode of the entitlement and linked to the entitlement.
        """
        course_uuid = uuid.uuid4()
        entitlement_data = self._get_data_set(self.user, str(course_uuid))
        mock_get_course_runs.return_value = [{'key': str(self.course.id)}]  # pylint: disable=no-member

        # Add an audit course enrollment for user.
        enrollment = CourseEnrollment.enroll(
            self.user,
            self.course.id,  # pylint: disable=no-member
            mode=CourseMode.AUDIT
        )
        enrollment.update_enrollment(is_active=False)
        response = self.client.post(
            self.entitlements_list_url,
            data=json.dumps(entitlement_data),
            content_type='application/json',
        )
        assert response.status_code == 201
        results = response.data

        course_entitlement = CourseEntitlement.objects.get(
            user=self.user,
            course_uuid=course_uuid
        )
        # Assert that enrollment mode is now verified
        enrollment_mode, enrollment_active = CourseEnrollment.enrollment_mode_for_user(
            self.user,
            self.course.id  # pylint: disable=no-member
        )
        assert enrollment_mode == CourseMode.AUDIT
        assert enrollment_active is False
        assert course_entitlement.enrollment_course_run is None
        assert results == CourseEntitlementSerializer(course_entitlement).data

    def test_non_staff_get_select_entitlements(self):
        not_staff_user = UserFactory()
        self.client.login(username=not_staff_user.username, password=TEST_PASSWORD)
        CourseEntitlementFactory.create_batch(2)
        entitlement = CourseEntitlementFactory.create(user=not_staff_user)
        response = self.client.get(
            self.entitlements_list_url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data.get('results', [])
        assert results == CourseEntitlementSerializer([entitlement], many=True).data

    def test_staff_get_only_staff_entitlements(self):
        CourseEntitlementFactory.create_batch(2)
        entitlement = CourseEntitlementFactory.create(user=self.user)

        response = self.client.get(
            self.entitlements_list_url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data.get('results', [])
        assert results == CourseEntitlementSerializer([entitlement], many=True).data

    def test_staff_get_expired_entitlements(self):
        past_datetime = now() - timedelta(days=365 * 2)
        entitlements = CourseEntitlementFactory.create_batch(2, created=past_datetime, user=self.user)

        # Set the first entitlement to be at a time that it isn't expired
        entitlements[0].created = now()
        entitlements[0].save()

        response = self.client.get(
            self.entitlements_list_url,
            content_type='application/json',
        )
        assert response.status_code == 200
        results = response.data.get('results', [])
        # Make sure that the first result isn't expired, and the second one is also not for staff users
        assert results[0].get('expired_at') is None and results[1].get('expired_at') is None

    def test_get_user_expired_entitlements(self):
        past_datetime = now() - timedelta(days=365 * 2)
        not_staff_user = UserFactory()
        self.client.login(username=not_staff_user.username, password=TEST_PASSWORD)
        entitlement_user2 = CourseEntitlementFactory.create_batch(2, user=not_staff_user, created=past_datetime)
        url = reverse('entitlements_api:v1:entitlements-list')
        url += f'?user={not_staff_user.username}'

        # Set the first entitlement to be at a time that it isn't expired
        entitlement_user2[0].created = now()
        entitlement_user2[0].save()

        response = self.client.get(
            url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data.get('results', [])
        assert results[0].get('expired_at') is None and results[1].get('expired_at')

    def test_get_user_entitlements(self):
        user2 = UserFactory()
        CourseEntitlementFactory.create()
        entitlement_user2 = CourseEntitlementFactory.create(user=user2)
        url = reverse('entitlements_api:v1:entitlements-list')
        url += f'?user={user2.username}'
        response = self.client.get(
            url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data.get('results', [])
        assert results == CourseEntitlementSerializer([entitlement_user2], many=True).data

    def test_get_entitlement_by_uuid(self):
        entitlement = CourseEntitlementFactory.create()
        CourseEntitlementFactory.create_batch(2)

        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(entitlement.uuid)])

        response = self.client.get(
            url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data
        assert results == CourseEntitlementSerializer(entitlement).data and results.get('expired_at') is None

    def test_get_expired_entitlement_by_uuid(self):
        past_datetime = now() - timedelta(days=365 * 2)
        entitlement = CourseEntitlementFactory(created=past_datetime)
        CourseEntitlementFactory.create_batch(2)

        CourseEntitlementFactory()
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(entitlement.uuid)])

        response = self.client.get(
            url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data
        assert results.get('expired_at')

    def test_entitlements_filter_on_course_uuid_and_expired_at(self):
        """
        Verify that courses filtered properly on list of course_uuids.
        """
        entitlements = CourseEntitlementFactory.create_batch(5, user=self.user, expired_at=datetime.now())
        course_uuids_to_filter_on = {
            str(entitlement.course_uuid)
            for entitlement in entitlements[0:3]
        }
        # Create a 2nd entitlement for one of the ones to filter on
        active_entitlement = CourseEntitlementFactory.create(
            user=self.user,
            course_uuid=str(entitlements[0].course_uuid),
            expired_at=None,
        )

        url = reverse('entitlements_api:v1:entitlements-list')
        url += f'?user={self.user.username}'
        url += f'&course_uuid={",".join(course_uuids_to_filter_on)}'

        response = self.client.get(
            url,
            content_type='application/json',
        )
        assert response.status_code == 200

        results = response.data
        assert results['count'] == 4
        actual_course_uuids = {entitlement['course_uuid'] for entitlement in results['results']}
        assert actual_course_uuids == course_uuids_to_filter_on
        assert len(actual_course_uuids) == 3

        # Now lets confirm we filter out expired entitlements
        url += '&expired_at__isnull=True'
        response = self.client.get(
            url,
            content_type='application/json',
        )
        assert response.status_code == 200
        results = response.data
        assert results['count'] == 1
        assert results['results'][0]['uuid'] == str(active_entitlement.uuid)

    def test_delete_and_revoke_entitlement(self):
        course_entitlement = CourseEntitlementFactory.create()
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(course_entitlement.uuid)])

        response = self.client.delete(
            url,
            content_type='application/json',
        )
        assert response.status_code == 204
        course_entitlement.refresh_from_db()
        assert course_entitlement.expired_at is not None

    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    def test_revoke_unenroll_entitlement(self, mock_course_uuid):
        enrollment = CourseEnrollmentFactory.create(
            user=self.user,
            course_id=self.course.id,  # pylint: disable=no-member
            is_active=True
        )
        course_entitlement = CourseEntitlementFactory.create(user=self.user, enrollment_course_run=enrollment)
        mock_course_uuid.return_value = course_entitlement.course_uuid
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(course_entitlement.uuid)])

        assert course_entitlement.enrollment_course_run is not None

        response = self.client.delete(
            url,
            content_type='application/json',
        )
        assert response.status_code == 204

        course_entitlement.refresh_from_db()
        assert course_entitlement.expired_at is not None
        assert course_entitlement.enrollment_course_run is None

    def test_reinstate_entitlement(self):
        enrollment = CourseEnrollmentFactory(user=self.user, is_active=True)
        expired_entitlement = CourseEntitlementFactory.create(
            user=self.user, enrollment_course_run=enrollment, expired_at=datetime.now()
        )
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(expired_entitlement.uuid)])

        update_data = {
            'expired_at': None,
            'enrollment_course_run': None,
            'support_details': [
                {
                    'unenrolled_run': str(enrollment.course.id),
                    'action': CourseEntitlementSupportDetail.REISSUE,
                    'comments': 'Severe illness.'
                }
            ]
        }

        response = self.client.patch(
            url,
            data=json.dumps(update_data),
            content_type='application/json'
        )
        assert response.status_code == 200

        results = response.data
        reinstated_entitlement = CourseEntitlement.objects.get(
            uuid=expired_entitlement.uuid
        )
        assert results == CourseEntitlementSerializer(reinstated_entitlement).data

    def test_reinstate_refundable_entitlement(self):
        """ Verify that an entitlement that is refundable stays refundable when support reinstates it. """
        enrollment = CourseEnrollmentFactory(user=self.user, is_active=True, course=CourseOverviewFactory(start=now()))
        fulfilled_entitlement = CourseEntitlementFactory.create(
            user=self.user, enrollment_course_run=enrollment
        )
        assert fulfilled_entitlement.is_entitlement_refundable() is True
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(fulfilled_entitlement.uuid)])

        update_data = {
            'expired_at': None,
            'enrollment_course_run': None,
            'support_details': [
                {
                    'unenrolled_run': str(enrollment.course.id),
                    'action': CourseEntitlementSupportDetail.REISSUE,
                    'comments': 'Severe illness.'
                }
            ]
        }

        response = self.client.patch(
            url,
            data=json.dumps(update_data),
            content_type='application/json'
        )
        assert response.status_code == 200

        reinstated_entitlement = CourseEntitlement.objects.get(
            uuid=fulfilled_entitlement.uuid
        )
        assert reinstated_entitlement.refund_locked is False
        assert reinstated_entitlement.is_entitlement_refundable() is True

    def test_reinstate_unrefundable_entitlement(self):
        """ Verify that a no longer refundable entitlement does not become refundable when support reinstates it. """
        enrollment = CourseEnrollmentFactory(user=self.user, is_active=True)
        expired_entitlement = CourseEntitlementFactory.create(
            user=self.user, enrollment_course_run=enrollment, expired_at=datetime.now()
        )
        assert expired_entitlement.is_entitlement_refundable() is False
        url = reverse(self.ENTITLEMENTS_DETAILS_PATH, args=[str(expired_entitlement.uuid)])

        update_data = {
            'expired_at': None,
            'enrollment_course_run': None,
            'support_details': [
                {
                    'unenrolled_run': str(enrollment.course.id),
                    'action': CourseEntitlementSupportDetail.REISSUE,
                    'comments': 'Severe illness.'
                }
            ]
        }

        response = self.client.patch(
            url,
            data=json.dumps(update_data),
            content_type='application/json'
        )
        assert response.status_code == 200

        reinstated_entitlement = CourseEntitlement.objects.get(
            uuid=expired_entitlement.uuid
        )
        assert reinstated_entitlement.refund_locked is True
        assert reinstated_entitlement.is_entitlement_refundable() is False


@skip_unless_lms
class EntitlementEnrollmentViewSetTest(ModuleStoreTestCase):
    """
    Tests for the EntitlementEnrollmentViewSets
    """
    ENTITLEMENTS_ENROLLMENT_NAMESPACE = 'entitlements_api:v1:enrollments'

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        UserFactory(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME, is_staff=True)

        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.course = CourseFactory.create(org='edX', number='DemoX', display_name='Demo_Course')
        self.course2 = CourseFactory.create(org='edX', number='DemoX2', display_name='Demo_Course 2')

        self.course_mode = CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )

        self.course_mode = CourseModeFactory(
            course_id=self.course2.id,
            mode_slug=CourseMode.VERIFIED,
            # This must be in the future to ensure it is returned by downstream code.
            expiration_datetime=now() + timedelta(days=1)
        )

        self.return_values = [
            {'key': str(self.course.id)},
            {'key': str(self.course2.id)}
        ]

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_can_enroll(self, mock_get_course_runs):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values
        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )
        assert course_entitlement.enrollment_course_run is None

        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert course_entitlement.enrollment_course_run is not None

    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_can_unenroll(self, mock_get_course_runs, mock_get_course_uuid):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values
        mock_get_course_uuid.return_value = course_entitlement.course_uuid

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )
        assert course_entitlement.enrollment_course_run is None

        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)

        response = self.client.delete(
            url,
            content_type='application/json',
        )
        assert response.status_code == 204

        course_entitlement.refresh_from_db()
        assert not CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert course_entitlement.enrollment_course_run is None

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_can_switch(self, mock_get_course_runs):
        mock_get_course_runs.return_value = self.return_values
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )
        assert course_entitlement.enrollment_course_run is None

        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)

        data = {
            'course_run_id': str(self.course2.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        assert response.status_code == 201

        course_entitlement.refresh_from_db()
        assert CourseEnrollment.is_enrolled(self.user, self.course2.id)
        assert course_entitlement.enrollment_course_run is not None

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_already_enrolled(self, mock_get_course_runs):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )

        CourseEnrollment.enroll(self.user, self.course.id, mode=course_entitlement.mode)
        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert course_entitlement.enrollment_course_run is not None

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_already_enrolled_course_ended(self, mock_get_course_runs):
        """
        Test that already enrolled user can still select a session while
        course has ended but upgrade deadline is in future.
        """
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values

        # Setup enrollment period to be in the past
        utc_now = datetime.now(ZoneInfo("UTC"))
        self.course.start = utc_now - timedelta(days=15)
        self.course.end = utc_now - timedelta(days=1)
        self.course = self.update_course(self.course, self.user.id)
        CourseOverview.update_select_courses([self.course.id], force_update=True)

        CourseEnrollment.enroll(self.user, self.course.id, mode=CourseMode.AUDIT)

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )

        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        (enrolled_mode, is_active) = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active and (enrolled_mode == course_entitlement.mode)
        assert course_entitlement.enrollment_course_run is not None

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_already_enrolled_in_unpaid_mode(self, mock_get_course_runs):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )

        CourseEnrollment.enroll(self.user, self.course.id, mode=CourseMode.AUDIT)
        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        (enrolled_mode, is_active) = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert is_active and (enrolled_mode == course_entitlement.mode)
        assert course_entitlement.enrollment_course_run is not None

    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_cannot_enroll_in_unknown_course_run_id(self, mock_get_course_runs):
        fake_course_str = str(self.course.id) + 'fake'
        fake_course_key = CourseKey.from_string(fake_course_str)
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )

        data = {
            'course_run_id': str(fake_course_key)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )

        expected_message = 'The Course Run ID is not a match for this Course Entitlement.'
        assert response.status_code == 400
        assert response.data['message'] == expected_message
        assert not CourseEnrollment.is_enrolled(self.user, fake_course_key)

    @patch('common.djangoapps.entitlements.models.refund_entitlement', return_value=True)
    @patch('common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course')
    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    def test_user_can_revoke_and_refund(self, mock_course_uuid, mock_get_course_runs, mock_refund_entitlement):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values
        mock_course_uuid.return_value = course_entitlement.course_uuid

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )
        assert course_entitlement.enrollment_course_run is None

        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)

        # Unenroll with Revoke for refund
        revoke_url = url + '?is_refund=true'
        response = self.client.delete(
            revoke_url,
            content_type='application/json',
        )
        assert response.status_code == 204

        course_entitlement.refresh_from_db()
        assert mock_refund_entitlement.is_called
        assert mock_refund_entitlement.call_args[1]['course_entitlement'] == course_entitlement
        assert not CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert course_entitlement.enrollment_course_run is None
        assert course_entitlement.expired_at is not None

    @patch('common.djangoapps.entitlements.rest_api.v1.views.CourseEntitlement.is_entitlement_refundable', return_value=False)  # lint-amnesty, pylint: disable=line-too-long
    @patch('common.djangoapps.entitlements.models.refund_entitlement', return_value=True)
    @patch('common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course')
    def test_user_can_revoke_and_no_refund_available(
            self,
            mock_get_course_runs,
            mock_refund_entitlement,  # pylint: disable=unused-argument
            mock_is_refundable  # pylint: disable=unused-argument
    ):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )
        assert course_entitlement.enrollment_course_run is None

        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)

        # Unenroll with Revoke for refund
        revoke_url = url + '?is_refund=true'
        response = self.client.delete(
            revoke_url,
            content_type='application/json',
        )
        assert response.status_code == 400

        course_entitlement.refresh_from_db()
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert course_entitlement.enrollment_course_run is not None
        assert course_entitlement.expired_at is None

    @patch('common.djangoapps.entitlements.rest_api.v1.views.CourseEntitlement.is_entitlement_refundable', return_value=True)  # lint-amnesty, pylint: disable=line-too-long
    @patch('common.djangoapps.entitlements.models.refund_entitlement', return_value=False)
    @patch("common.djangoapps.entitlements.rest_api.v1.views.get_course_runs_for_course")
    def test_user_is_not_unenrolled_on_failed_refund(
            self,
            mock_get_course_runs,
            mock_refund_entitlement,  # pylint: disable=unused-argument
            mock_is_refundable  # pylint: disable=unused-argument
    ):
        course_entitlement = CourseEntitlementFactory.create(user=self.user, mode=CourseMode.VERIFIED)
        mock_get_course_runs.return_value = self.return_values

        url = reverse(
            self.ENTITLEMENTS_ENROLLMENT_NAMESPACE,
            args=[str(course_entitlement.uuid)]
        )
        assert course_entitlement.enrollment_course_run is None

        # Enroll the User
        data = {
            'course_run_id': str(self.course.id)
        }
        response = self.client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
        )
        course_entitlement.refresh_from_db()

        assert response.status_code == 201
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)

        # Unenroll with Revoke for refund
        revoke_url = url + '?is_refund=true'
        response = self.client.delete(
            revoke_url,
            content_type='application/json',
        )
        assert response.status_code == 500

        course_entitlement.refresh_from_db()
        assert CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert course_entitlement.enrollment_course_run is not None
        assert course_entitlement.expired_at is None
