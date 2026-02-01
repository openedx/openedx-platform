"""
Create a user with GDPR P1 PII for manual testing.
Enrolls the user in the DemoX course.
Optionally takes in username, email, and course UUID arguments.
"""


from datetime import datetime
from textwrap import dedent
from uuid import uuid4

from consent.models import DataSharingConsent
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.conf import settings
from django.core.management.base import BaseCommand
from enterprise.models import (
    EnterpriseCourseEnrollment,
    EnterpriseCustomer,
    EnterpriseCustomerUser,
    PendingEnterpriseCustomerUser
)
from opaque_keys.edx.keys import CourseKey
from zoneinfo import ZoneInfo

from common.djangoapps.entitlements.models import CourseEntitlement, CourseEntitlementSupportDetail
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification
from openedx.core.djangoapps.course_groups.models import CourseUserGroup, UnregisteredLearnerCohortAssignments
from openedx.core.djangoapps.profile_images.images import create_profile_images
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed, PendingEmailChange, UserProfile

from ...models import UserOrgTag

# This is a temporary import path while we transition from integrated_channels to channel_integrations
if getattr(settings, 'ENABLE_LEGACY_INTEGRATED_CHANNELS', True):
    from integrated_channels.sap_success_factors.models import SapSuccessFactorsLearnerDataTransmissionAudit
else:
    from channel_integrations.sap_success_factors.models import SapSuccessFactorsLearnerDataTransmissionAudit


class Command(BaseCommand):
    """
    Create a user with GDPR P1 PII for manual testing.
    Enrolls the user in the DemoX course.
    Optionally takes in username, email, and course UUID arguments.
    """
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument(
            '-u', '--username',
            required=False,
            help='Username'
        )
        parser.add_argument(
            '-e', '--email',
            required=False,
            help='Email'
        )
        parser.add_argument(
            '-c', '--course',
            required=False,
            help='Course UUID'
        )

    def handle(self, *args, **options):
        """
        Execute the command.
        """

        username = options['username'] if options['username'] else 'gdpr_test_user'
        email = options['email'] if options['email'] else 'gdpr_test_user@example.com'
        course_uuid = options['course'] if options['course'] else uuid4().hex

        user, __ = User.objects.get_or_create(
            username=username,
            email=email
        )
        user_info = {
            'email': email,
            'first_name': "GDPR",
            'last_name': "Test",
            'is_active': True
        }
        for field, value in user_info.items():
            setattr(user, field, value)
        user.set_password('gdpr test password')
        user.save()

        # UserProfile
        profile_image_uploaded_date = datetime(2018, 5, 3, tzinfo=ZoneInfo("UTC"))
        user_profile, __ = UserProfile.objects.get_or_create(
            user=user
        )
        user_profile_info = {
            'name': 'gdpr test name',
            'meta': '{}',
            'location': 'gdpr test location',
            'year_of_birth': 1950,
            'gender': 'gdpr test gender',
            'mailing_address': 'gdpr test mailing address',
            'city': 'Boston',
            'country': 'US',
            'bio': 'gdpr test bio',
            'profile_image_uploaded_at': profile_image_uploaded_date
        }
        for field, value in user_profile_info.items():
            setattr(user_profile, field, value)
        user_profile.save()

        # Profile images
        with make_image_file() as image_file:
            create_profile_images(
                image_file,
                {10: "ten.jpg"}
            )

        # DataSharingConsent
        enterprise_customer, __ = EnterpriseCustomer.objects.get_or_create(  # pylint: disable=no-member
            name='test gdpr enterprise customer',
            active=True,
            branding_configuration=None,
            catalog=None,
            enable_audit_enrollment=False,
            enable_data_sharing_consent=False,
            enforce_data_sharing_consent='at_enrollment',
            replace_sensitive_sso_username=True,
            site_id=1
        )

        DataSharingConsent.objects.get_or_create(
            username=username,
            enterprise_customer_id=enterprise_customer.uuid
        )

        # Sapsf data transmission
        enterprise_customer_user, __ = EnterpriseCustomerUser.objects.get_or_create(
            user_id=user.id,
            enterprise_customer_id=enterprise_customer.uuid
        )
        audit, __ = EnterpriseCourseEnrollment.objects.get_or_create(
            enterprise_customer_user=enterprise_customer_user
        )
        SapSuccessFactorsLearnerDataTransmissionAudit.objects.get_or_create(
            enterprise_course_enrollment_id=audit.id,
        )

        # PendingEnterpriseCustomerUser
        PendingEnterpriseCustomerUser.objects.get_or_create(
            user_email=user.email,
            enterprise_customer_id=enterprise_customer.uuid
        )

        # EntitlementSupportDetail
        course_entitlement, __ = CourseEntitlement.objects.get_or_create(
            user_id=user.id,
            course_uuid=course_uuid
        )
        CourseEntitlementSupportDetail.objects.get_or_create(
            support_user=user,
            comments='test comments',
            entitlement_id=course_entitlement.id
        )

        # Misc. models that may contain PII of this user
        SoftwareSecurePhotoVerification.objects.get_or_create(
            user=user,
            name='gdpr test',
            face_image_url='https://fake_image_url.com',
            photo_id_image_url='gdpr_test',
            photo_id_key='gdpr_test'
        )
        PendingEmailChange.objects.get_or_create(
            user=user,
            activation_key=uuid4().hex
        )
        UserOrgTag.objects.get_or_create(
            user=user
        )

        course_id = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
        # Objects linked to the user via their original email
        CourseEnrollmentAllowed.objects.get_or_create(
            email=user.email
        )
        course_user_group, __ = CourseUserGroup.objects.get_or_create(
            name='test course user group',
            course_id=course_id
        )
        UnregisteredLearnerCohortAssignments.objects.get_or_create(
            email=user.email,
            course_user_group_id=course_user_group.id
        )

        # Enroll the user in a course
        CourseEnrollment.objects.get_or_create(
            course_id=course_id,
            user_id=user.id,
        )
