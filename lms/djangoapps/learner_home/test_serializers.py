"""
Tests for serializers for the Learner Home
"""

from datetime import date, datetime, timedelta, timezone
from itertools import product
from random import randint
from unittest import mock
from uuid import uuid4

import ddt
from django.conf import settings
from django.urls import reverse
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey


from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentFactory,
    UserFactory,
)
from openedx.core.djangoapps.catalog.tests.factories import (
    CourseRunFactory as CatalogCourseRunFactory,
    ProgramFactory,
)
from openedx.core.djangoapps.content.course_overviews.tests.factories import (
    CourseOverviewFactory,
)
from lms.djangoapps.learner_home.serializers import (
    CertificateSerializer,
    CourseProviderSerializer,
    CourseRunSerializer,
    CourseSerializer,
    CreditSerializer,
    EmailConfirmationSerializer,
    EnrollmentSerializer,
    EnterpriseDashboardSerializer,
    EntitlementSerializer,
    GradeDataSerializer,
    CoursewareAccessSerializer,
    LearnerEnrollmentSerializer,
    PlatformSettingsSerializer,
    ProgramsSerializer,
    LearnerDashboardSerializer,
    RelatedProgramSerializer,
    SocialMediaSiteSettingsSerializer,
    SocialShareSettingsSerializer,
    SuggestedCourseSerializer,
    UnfulfilledEntitlementSerializer,
)
from lms.djangoapps.learner_home.utils import course_progress_url
from lms.djangoapps.learner_home.test_utils import (
    datetime_to_django_format,
    random_bool,
    random_date,
    random_string,
    random_url,
)
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class LearnerDashboardBaseTest(SharedModuleStoreTestCase):
    """Base class for common setup"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory()

    def create_test_enrollment(self, course_mode=CourseMode.AUDIT):
        """Create a test user, course, and enrollment. Return the enrollment."""
        course = CourseFactory(
            self_paced=True,
            advertised_start="Winter 2015",
        )
        CourseModeFactory(
            course_id=course.id,
            mode_slug=course_mode,
        )

        test_enrollment = CourseEnrollmentFactory(course_id=course.id, mode=course_mode)

        # Add extra info to exercise serialization
        test_enrollment.course_overview.marketing_url = random_url()
        test_enrollment.course_overview.end = random_date()
        test_enrollment.course_overview.certificate_available_date = random_date()

        return test_enrollment

    def create_test_entitlement_and_sessions(self):
        """
        Create a test entitlement

        Returns: (unfulfilled_entitlement, pseudo_sessions, available_sessions)
        """
        unfulfilled_entitlement = CourseEntitlementFactory.create()

        # Create pseudo-sessions
        pseudo_sessions = {
            str(unfulfilled_entitlement.uuid): CatalogCourseRunFactory.create()
        }

        # Create available sessions
        available_sessions = {
            str(unfulfilled_entitlement.uuid): CatalogCourseRunFactory.create_batch(3)
        }

        # Create related course overviews
        course_key_str = pseudo_sessions[str(unfulfilled_entitlement.uuid)]["key"]
        course_key = CourseKey.from_string(course_key_str)
        course_overview = CourseOverviewFactory.create(id=course_key)

        return unfulfilled_entitlement, pseudo_sessions, available_sessions

    def _assert_all_keys_equal(self, dicts):
        element_0 = dicts[0]
        for element in dicts[1:]:
            assert element_0.keys() == element.keys()


class TestPlatformSettingsSerializer(TestCase):
    """Tests for the PlatformSettingsSerializer"""

    @classmethod
    def generate_test_platform_settings(cls):
        """Util to generate test platform settings data"""
        return {
            "feedbackEmail": f"{uuid4()}@example.com",
            "supportEmail": f"{uuid4()}@example.com",
            "billingEmail": f"{uuid4()}@example.com",
            "courseSearchUrl": f"{uuid4()}.example.com/search",
        }

    def test_happy_path(self):
        input_data = self.generate_test_platform_settings()
        output_data = PlatformSettingsSerializer(input_data).data

        assert output_data == {
            "supportEmail": input_data["supportEmail"],
            "billingEmail": input_data["billingEmail"],
            "courseSearchUrl": input_data["courseSearchUrl"],
        }


class TestCourseProviderSerializer(LearnerDashboardBaseTest):
    """Tests for the CourseProviderSerializer"""

    @classmethod
    def generate_test_provider_info(cls):
        """Util to generate test provider info"""
        return {
            "name": f"{uuid4()}",
        }

    def test_happy_path(self):
        test_enrollment = self.create_test_enrollment()

        input_data = test_enrollment.course_overview
        output_data = CourseProviderSerializer(input_data).data

        self.assertEqual(output_data["name"], test_enrollment.course_overview.org)


class TestCourseSerializer(LearnerDashboardBaseTest):
    """Tests for the CourseSerializer"""

    def create_test_context(self, course_id):
        return {"course_share_urls": {course_id: random_url()}}

    def test_happy_path(self):
        test_enrollment = self.create_test_enrollment()
        course_id = test_enrollment.course_overview.id
        test_context = self.create_test_context(course_id)

        input_data = test_enrollment.course_overview
        output_data = CourseSerializer(input_data, context=test_context).data

        assert output_data == {
            "bannerImgSrc": test_enrollment.course_overview.banner_image_url,
            "courseName": test_enrollment.course_overview.display_name_with_default,
            "courseNumber": test_enrollment.course_overview.display_number_with_default,
            "socialShareUrl": test_context["course_share_urls"][course_id],
        }


class TestCourseRunSerializer(LearnerDashboardBaseTest):
    """Tests for the CourseRunSerializer"""

    def create_test_context(self, course_id):
        return {
            "resume_course_urls": {course_id: random_url()},
            "ecommerce_payment_page": random_url(),
            "course_mode_info": {
                course_id: {
                    "verified_sku": str(uuid4()),
                    "days_for_upsell": randint(0, 14),
                }
            },
        }

    def test_with_data(self):
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course.id)

        output_data = CourseRunSerializer(input_data, context=input_context).data

        # Serialization set up so all fields will have values to make testing easy
        for key in output_data:
            assert output_data[key] is not None

    def test_missing_resume_url(self):
        # Given a course run
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course.id)

        # ... where a user hasn't started
        input_context["resume_course_urls"][input_data.course.id] = None

        # When I serialize
        output_data = CourseRunSerializer(input_data, context=input_context).data

        # Then the resumeUrl is None, which is allowed
        self.assertIsNone(output_data["resumeUrl"])

    def is_progress_url_matching_course_home_mfe_progress_tab_is_active(self):
        """
        Compares the progress URL generated by CourseRunSerializer to the expected progress URL.

        :return: True if the generated progress URL matches the expected, False otherwise.
        """
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course.id)
        output_data = CourseRunSerializer(input_data, context=input_context).data
        return output_data['progressUrl'] == course_progress_url(input_data.course.id)

    @mock.patch('lms.djangoapps.learner_home.utils.course_home_mfe_progress_tab_is_active')
    def test_progress_url(self, mock_course_home_mfe_progress_tab_is_active):
        """
        Tests the progress URL generated by the CourseRunSerializer. When course_home_mfe_progress_tab_is_active
        is true, the generated progress URL must point to the progress page of the course home (learning) MFE.
        Otherwise, it must point to the legacy progress page.
        """
        mock_course_home_mfe_progress_tab_is_active.return_value = True
        self.assertTrue(self.is_progress_url_matching_course_home_mfe_progress_tab_is_active())

        mock_course_home_mfe_progress_tab_is_active.return_value = False
        self.assertTrue(self.is_progress_url_matching_course_home_mfe_progress_tab_is_active())


@ddt.ddt
class TestCoursewareAccessSerializer(LearnerDashboardBaseTest):
    """Tests for the CoursewareAccessSerializer"""

    def create_test_context(self, course):
        return {
            "course_access_checks": {
                course.id: {
                    "has_unmet_prerequisites": False,
                    "is_too_early_to_view": False,
                    "user_has_staff_access": False,
                }
            }
        }

    @ddt.data(True, False)
    def test_unmet_prerequisites(self, has_unmet_prerequisites):
        # Given an enrollment
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        # ... without unmet prerequisites
        if has_unmet_prerequisites:
            # ... or with unmet prerequisites
            prerequisite_course = CourseFactory()
            input_context.update(
                {
                    "course_access_checks": {
                        input_data.course.id: {
                            "has_unmet_prerequisites": has_unmet_prerequisites,
                        }
                    }
                }
            )

        # When I serialize
        output_data = CoursewareAccessSerializer(input_data, context=input_context).data

        # Then "hasUnmetPrerequisites" is outputs correctly
        self.assertEqual(output_data["hasUnmetPrerequisites"], has_unmet_prerequisites)

    @ddt.data(True, False)
    def test_is_staff(self, is_staff):
        # Given an enrollment
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        # Where user has/hasn't staff access
        input_context.update(
            {
                "course_access_checks": {
                    input_data.course.id: {
                        "user_has_staff_access": is_staff,
                    }
                }
            }
        )

        # When I serialize
        output_data = CoursewareAccessSerializer(input_data, context=input_context).data

        # Then "isStaff" serializes properly
        self.assertEqual(output_data["isStaff"], is_staff)

    @ddt.data(True, False)
    def test_is_too_early(self, is_too_early):
        # Given an enrollment
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        # Where the course is/n't yet open for a learner
        input_context.update(
            {
                "course_access_checks": {
                    input_data.course.id: {
                        "is_too_early_to_view": is_too_early,
                    }
                }
            }
        )

        # When I serialize
        output_data = CoursewareAccessSerializer(input_data, context=input_context).data

        # Then "isTooEarly" serializes properly
        self.assertEqual(output_data["isTooEarly"], is_too_early)

    @mock.patch(
        "lms.djangoapps.learner_home.serializers.get_pre_requisite_courses_not_completed"
    )
    def test_has_unmet_prerequisites_list_empty(self, mock_get_prereqs):
        """Serializer should emit an empty list when no prerequisites are returned."""
        mock_get_prereqs.return_value = {}
        enrollment = self.create_test_enrollment()
        context = self.create_test_context(enrollment.course)

        output_data = CoursewareAccessSerializer(enrollment, context=context).data

        self.assertEqual(output_data["hasUnmetPrerequisitesList"], [])

    @mock.patch(
        "lms.djangoapps.learner_home.serializers.CoursewareAccessSerializer._get_course_about_url_for_key"
    )
    @mock.patch("lms.djangoapps.learner_home.serializers.course_home_url")
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.get_pre_requisite_courses_not_completed"
    )
    def test_has_unmet_prerequisites_list_serialization(
        self,
        mock_get_prereqs,
        mock_course_home_url,
        mock_get_about_url,
    ):
        """Serializer should include display, course home, and about URLs for each prerequisite."""
        enrollment = self.create_test_enrollment()
        context = self.create_test_context(enrollment.course)
        prerequisite_course = CourseFactory()
        expected_display = "Prerequisite Course"
        mock_get_prereqs.return_value = {
            enrollment.course_id: {
                "courses": [
                    {
                        "display": expected_display,
                        "key": prerequisite_course.id,
                    }
                ]
            }
        }
        mock_course_home_url.return_value = "https://example.com/home"
        mock_get_about_url.return_value = "https://example.com/about"

        output_data = CoursewareAccessSerializer(enrollment, context=context).data

        self.assertEqual(len(output_data["hasUnmetPrerequisitesList"]), 1)
        self.assertDictEqual(
            output_data["hasUnmetPrerequisitesList"][0],
            {
                "display": expected_display,
                "url": "https://example.com/home",
                "about_url": "https://example.com/about",
            },
        )


@ddt.ddt
class TestEnrollmentSerializer(LearnerDashboardBaseTest):
    """Tests for the EnrollmentSerializer"""

    def create_test_context(self, course):
        """Get a test context object"""
        return {
            "audit_access_deadlines": {course.id: random_date()},
            "course_mode_info": {
                course.id: {
                    "show_upsell": True,
                }
            },
            "course_optouts": [],
            "show_email_settings_for": [course.id],
            "resume_course_urls": {course.id: "some_url"},
            "ecommerce_payment_page": random_url(),
        }

    def serialize_test_enrollment(self):
        """
        Create a test enrollment, pass it to EnrollmentSerializer, and return the serialized data
        """
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        serializer = EnrollmentSerializer(input_data, context=input_context)
        return serializer.data

    def test_with_data(self):
        output = self.serialize_test_enrollment()
        # Serialization set up so all fields will have values to make testing easy
        for key in output:
            assert output[key] is not None

    @ddt.data(
        (None, False),  # No expiration date, allowed for non-audit, non-expired.
        (datetime.max, False),  # Expiration in the far future. Shouldn't be expired.
        (datetime.min, True),  # Expiration in the far past. Should be expired.
    )
    @ddt.unpack
    def test_audit_access_expired(self, expiration_datetime, should_be_expired):
        # Given an enrollment
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        # With/out an expiration date (made timezone aware, if it exists)
        expiration_datetime = (
            expiration_datetime.replace(tzinfo=timezone.utc)
            if expiration_datetime
            else None
        )
        input_context.update(
            {
                "audit_access_deadlines": {input_data.course.id: expiration_datetime},
            }
        )

        # When I serialize
        output = EnrollmentSerializer(input_data, context=input_context).data

        self.assertEqual(output["isAuditAccessExpired"], should_be_expired)

    @ddt.data(
        (random_url(), True, uuid4(), True),
        (None, True, uuid4(), False),
        (random_url(), False, uuid4(), False),
        (random_url(), True, None, False),
    )
    @ddt.unpack
    def test_user_can_upgrade(
        self, mock_payment_url, mock_show_upsell, mock_sku, expected_can_upgrade
    ):
        # Given a test enrollment
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        # ... with payment page, upsell, and SKU info
        input_context.update(
            {
                "ecommerce_payment_page": mock_payment_url,
                "course_mode_info": {
                    input_data.course.id: {
                        "show_upsell": mock_show_upsell,
                        "verified_sku": mock_sku,
                    }
                },
            }
        )

        # When I serialize
        output = EnrollmentSerializer(input_data, context=input_context).data

        # Then I correctly return whether or not the user can upgrade
        # (If any of the payment page, upsell, or sku aren't provided, this is False)
        self.assertEqual(output["canUpgrade"], expected_can_upgrade)

    @ddt.data(None, "", "some_url")
    def test_has_started(self, resume_url):
        # Given the presence or lack of a resume_course_url
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course)

        input_context.update(
            {
                "resume_course_urls": {
                    input_data.course.id: resume_url,
                }
            }
        )

        # When I get "hasStarted"
        output = EnrollmentSerializer(input_data, context=input_context).data

        # If I have a resume URL, "hasStarted" should be True, otherwise False
        if resume_url:
            self.assertTrue(output["hasStarted"])
        else:
            self.assertFalse(output["hasStarted"])


@ddt.ddt
class TestGradeDataSerializer(LearnerDashboardBaseTest):
    """Tests for the GradeDataSerializer"""

    def create_test_context(self, course, is_passing):
        """Get a test context object"""
        return {"grade_statuses": {course.id: is_passing}}

    @ddt.data(True, False, None)
    def test_happy_path(self, is_passing):
        # Given a course where I am/not passing
        input_data = self.create_test_enrollment()
        input_context = self.create_test_context(input_data.course, is_passing)

        # When I serialize grade data
        output_data = GradeDataSerializer(input_data, context=input_context).data

        # Then I get the correct data shape out
        self.assertDictEqual(
            output_data,
            {
                "isPassing": is_passing,
            },
        )


@ddt.ddt
class TestCertificateSerializer(LearnerDashboardBaseTest):
    """Tests for the CertificateSerializer"""

    @classmethod
    def generate_test_certificate_info(cls):
        """Util to generate test certificate info"""
        return {
            "availableDate": random_date(allow_null=True),
            "isRestricted": random_bool(),
            "isAvailable": random_bool(),
            "isEarned": random_bool(),
            "isDownloadable": random_bool(),
            "certPreviewUrl": random_url(allow_null=True),
            "certDownloadUrl": random_url(allow_null=True),
            "honorCertDownloadUrl": random_url(allow_null=True),
        }

    def create_test_context(self, course):
        """Get a test context object with an available certificate"""
        return {
            "cert_statuses": {
                course.id: {
                    "cert_web_view_url": random_url(),
                    "status": "downloadable",
                    "show_cert_web_view": True,
                }
            }
        }

    def test_with_data(self):
        """Simple mappings test for a course with an available certificate"""
        # Given a verified enrollment
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)

        # ... with a certificate
        input_context = self.create_test_context(input_data.course)

        # ... and some data preemptively gathered, including a certificate display behavior
        available_date = random_date()
        input_data.course.certificate_available_date = available_date
        input_data.course.certificates_display_behavior = (
            CertificatesDisplayBehaviors.END_WITH_DATE
        )
        cert_url = input_context["cert_statuses"][input_data.course.id][
            "cert_web_view_url"
        ]

        # When I get certificate info
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then all the info is provided correctly
        self.assertDictEqual(
            output_data,
            {
                "availableDate": datetime_to_django_format(available_date),
                "isRestricted": False,
                "isEarned": True,
                "isDownloadable": True,
                "certPreviewUrl": cert_url,
            },
        )

    def test_available_date_course_end(self):
        # Given new cert display settings are enabled
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)
        input_context = self.create_test_context(input_data.course)

        # ... and certificate display behavior is set to the course end date
        input_data.course.certificates_display_behavior = (
            CertificatesDisplayBehaviors.END
        )

        # When I try to get cert available date
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then the available date is the course end date
        expected_available_date = datetime_to_django_format(input_data.course.end)
        self.assertEqual(output_data["availableDate"], expected_available_date)

    def test_available_date_specific_end(self):
        # Given new cert display settings are enabled
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)
        input_context = self.create_test_context(input_data.course)

        # ... and certificate display behavior is set to a specified date
        input_data.course.certificate_available_date = random_date()
        input_data.course.certificates_display_behavior = (
            CertificatesDisplayBehaviors.END_WITH_DATE
        )

        # When I try to get cert available date
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then the available date is the course end date
        expected_available_date = datetime_to_django_format(
            input_data.course.certificate_available_date
        )
        self.assertEqual(output_data["availableDate"], expected_available_date)

    @ddt.data(
        ("downloadable", False),
        ("notpassing", False),
        ("restricted", True),
        ("auditing", False),
    )
    @ddt.unpack
    def test_is_restricted(self, cert_status, is_restricted_expected):
        """Test for isRestricted field"""
        # Given a verified enrollment with a certificate
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)
        input_context = self.create_test_context(input_data.course)

        # ... and a cert status {cert_status}
        input_context["cert_statuses"][input_data.course.id]["status"] = cert_status

        # When I get certificate info
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then isRestricted should be calculated correctly
        self.assertEqual(output_data["isRestricted"], is_restricted_expected)

    @ddt.data(
        ("downloadable", True),
        ("notpassing", False),
        ("restricted", False),
        ("auditing", False),
        ("certificate_earned_but_not_available", True),
    )
    @ddt.unpack
    def test_is_earned(self, cert_status, is_earned_expected):
        """Test for isEarned field"""
        # Given a verified enrollment with a certificate
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)
        input_context = self.create_test_context(input_data.course)

        # ... and a cert status {cert_status}
        input_context["cert_statuses"][input_data.course.id]["status"] = cert_status

        # When I get certificate info
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then isEarned should be calculated correctly
        self.assertEqual(output_data["isEarned"], is_earned_expected)

    @ddt.data(
        ("downloadable", True),
        ("notpassing", False),
        ("restricted", False),
        ("auditing", False),
        ("certificate_earned_but_not_available", False),
    )
    @ddt.unpack
    def test_is_downloadable(self, cert_status, is_downloadable_expected):
        """Test for isDownloadable field"""
        # Given a verified enrollment with a certificate
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)
        input_context = self.create_test_context(input_data.course)

        # ... and a cert status {cert_status}
        input_context["cert_statuses"][input_data.course.id]["status"] = cert_status

        # When I get certificate info
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then isDownloadable should be calculated correctly
        self.assertEqual(output_data["isDownloadable"], is_downloadable_expected)

    @ddt.data(
        (True, random_url()),
        (False, random_url()),
        (True, None),
        (False, None),
    )
    @ddt.unpack
    def test_cert_preview_url(self, show_cert_web_view, cert_web_view_url):
        """Test for certPreviewUrl field"""
        # Given a verified enrollment with a certificate
        input_data = self.create_test_enrollment(course_mode=CourseMode.VERIFIED)
        input_context = self.create_test_context(input_data.course)

        # ... and settings show_cert_web_view and cert_web_view_url
        input_context["cert_statuses"][input_data.course.id][
            "show_cert_web_view"
        ] = show_cert_web_view
        input_context["cert_statuses"][input_data.course.id][
            "cert_web_view_url"
        ] = cert_web_view_url

        # When I get certificate info
        output_data = CertificateSerializer(input_data, context=input_context).data

        # Then certPreviewUrl should be calculated correctly
        self.assertEqual(
            output_data["certPreviewUrl"],
            cert_web_view_url if show_cert_web_view else None,
        )


@ddt.ddt
class TestEntitlementSerializer(TestCase):
    """Tests for the EntitlementSerializer"""

    def _assert_available_sessions(self, input_sessions, output_sessions):
        assert len(output_sessions) == len(input_sessions)
        for input_session, output_session in zip(input_sessions, output_sessions):
            assert output_session == {
                "startDate": input_session["start"],
                "endDate": input_session["end"],
                "courseId": input_session["key"],
            }

    @ddt.unpack
    @ddt.idata(product([True, False], repeat=2))
    def test_serialize_entitlement(self, isExpired, isEnrolled):
        entitlement_kwargs = {}
        if isExpired:
            entitlement_kwargs["expired_at"] = datetime.now()
        if isEnrolled:
            entitlement_kwargs[
                "enrollment_course_run"
            ] = CourseEnrollmentFactory.create()
        entitlement = CourseEntitlementFactory.create(**entitlement_kwargs)
        available_sessions = CatalogCourseRunFactory.create_batch(4)
        course_entitlement_available_sessions = {
            str(entitlement.uuid): available_sessions
        }

        output_data = EntitlementSerializer(
            entitlement,
            context={
                "course_entitlement_available_sessions": course_entitlement_available_sessions
            },
        ).data

        output_sessions = output_data.pop("availableSessions")
        self._assert_available_sessions(available_sessions, output_sessions)

        if isExpired:
            expected_expiration_date = entitlement.expired_at
        else:
            expected_expiration_date = date.today() + timedelta(
                days=entitlement.get_days_until_expiration()
            )

        assert output_data == {
            "isRefundable": entitlement.is_entitlement_refundable(),
            "isFulfilled": bool(entitlement.enrollment_course_run),
            "changeDeadline": expected_expiration_date,
            "isExpired": bool(entitlement.expired_at),
            "expirationDate": expected_expiration_date,
            "uuid": str(entitlement.uuid),
        }


class TestProgramsSerializer(TestCase):
    """Tests for the ProgramsSerializer and RelatedProgramsSerializer"""

    @classmethod
    def generate_test_related_program(cls):
        """Generate a program with random test data"""
        return ProgramFactory()

    @classmethod
    def generate_test_programs_info(cls):
        """Util to generate test programs info"""
        return {
            "relatedPrograms": [cls.generate_test_related_program() for _ in range(3)],
        }

    def test_related_program_serializer(self):
        """Test the RelatedProgramSerializer"""
        # Given a program
        input_data = self.generate_test_related_program()
        # When I serialize it
        output_data = RelatedProgramSerializer(input_data).data
        # Then the output should map with the input
        self.assertEqual(
            output_data,
            {
                "bannerImgSrc": input_data["banner_image"]["small"]["url"],
                "logoImgSrc": input_data["authoring_organizations"][0][
                    "logo_image_url"
                ],
                "numberOfCourses": len(input_data["courses"]),
                "programType": input_data["type"],
                "programUrl": settings.LMS_ROOT_URL
                + reverse(
                    "program_details_view", kwargs={"program_uuid": input_data["uuid"]}
                ),
                "provider": input_data["authoring_organizations"][0]["name"],
                "title": input_data["title"],
            },
        )

    def test_programs_serializer(self):
        """Test the ProgramsSerializer"""
        # Given a program with random test data
        input_data = self.generate_test_programs_info()

        # When I serialize the program
        output_data = ProgramsSerializer(input_data).data

        # Test the output
        assert output_data["relatedPrograms"]
        assert len(output_data["relatedPrograms"]) == len(input_data["relatedPrograms"])
        self.assertEqual(
            output_data,
            {
                "relatedPrograms": RelatedProgramSerializer(
                    input_data["relatedPrograms"], many=True
                ).data
            },
        )

    def test_empty_source_programs_serializer(self):
        """Test the ProgramsSerializer with empty data"""
        # Given a program with empty test data
        input_data = self.generate_test_related_program()

        input_data["banner_image"] = None
        input_data["title"] = None
        input_data["type"] = None

        # When I serialize the program
        output_data = RelatedProgramSerializer(input_data).data

        # Test the output
        self.assertEqual(output_data["bannerImgSrc"], None)

    def test_empty_sessions(self):
        input_data = {"relatedPrograms": []}
        output_data = ProgramsSerializer(input_data).data

        assert output_data == {"relatedPrograms": []}


class TestCreditSerializer(LearnerDashboardBaseTest):
    """Tests for the CreditSerializer"""

    @classmethod
    def create_test_data(cls, enrollment):
        """Mock data following the shape of credit_statuses"""

        return {
            "course_key": str(enrollment.course.id),
            "eligible": True,
            "deadline": random_date(),
            "purchased": False,
            "provider_name": "Hogwarts",
            "provider_status_url": "http://example.com/status",
            "provider_id": "HSWW",
            "request_status": "pending",
            "error": False,
        }

    @classmethod
    def create_test_context(cls, enrollment):
        """Credit data, packaged as it would be for serialization context"""

        return {enrollment.course_id: {**cls.create_test_data(enrollment)}}

    def test_serialize_credit(self):
        # Given an enrollment and a course with ability to purchase credit
        enrollment = self.create_test_enrollment()
        credit_data = self.create_test_data(enrollment)

        # When I serialize
        output_data = CreditSerializer(credit_data).data

        # Then I get the appropriate data shape
        self.assertDictEqual(
            output_data,
            {
                "providerStatusUrl": credit_data["provider_status_url"],
                "providerName": credit_data["provider_name"],
                "providerId": credit_data["provider_id"],
                "error": credit_data["error"],
                "purchased": credit_data["purchased"],
                "requestStatus": credit_data["request_status"],
            },
        )


class TestLearnerEnrollmentsSerializer(LearnerDashboardBaseTest):
    """High-level tests for LearnerEnrollmentsSerializer"""

    @classmethod
    def create_test_context(cls, enrollment):
        """Create context that is expected to be required / common across tests"""
        return {
            "resume_course_urls": {enrollment.course.id: random_url()},
            "ecommerce_payment_page": random_url(),
            "course_mode_info": {
                enrollment.course.id: {
                    "verified_sku": str(uuid4()),
                    "days_for_upsell": randint(0, 14),
                }
            },
            "fulfilled_entitlements": {},
            "unfulfilled_entitlement_pseudo_sessions": {},
            "programs": {},
            "credit_statuses": TestCreditSerializer.create_test_context(enrollment),
        }

    def test_happy_path(self):
        """Test that nothing breaks and the output fields look correct"""

        enrollment = self.create_test_enrollment()
        input_data = enrollment
        input_context = self.create_test_context(enrollment)

        output = LearnerEnrollmentSerializer(input_data, context=input_context).data

        expected_keys = [
            "courseProvider",
            "course",
            "courseRun",
            "enrollment",
            "gradeData",
            "certificate",
            "entitlement",
            "programs",
            "credit",
        ]

        # Verify we have all the expected keys in our output
        self.assertEqual(output.keys(), set(expected_keys))

        # Entitlements should be the only empty field for an enrollment
        entitlement = output.pop("entitlement")
        self.assertDictEqual(entitlement, {})

        # All other keys should have some basic info, unless we broke something
        for key in output.keys():
            self.assertNotEqual(output[key], {})

    def test_credit_no_credit_option(self):
        # Given an enrollment
        enrollment = self.create_test_enrollment()
        input_data = enrollment
        input_context = self.create_test_context(enrollment)

        # Given where the course does not offer the ability to purchase credit
        input_context["credit_statuses"] = {}

        # When I serialize
        output = LearnerEnrollmentSerializer(input_data, context=input_context).data

        # Then I return empty credit info
        self.assertDictEqual(output["credit"], {})


class TestUnfulfilledEntitlementSerializer(LearnerDashboardBaseTest):
    """High-level tests for UnfulfilledEntitlementSerializer"""

    def make_unfulfilled_entitlement(self):
        """Create an unfulflled entitlement, along with a pseudo session and available sessions"""
        unfulfilled_entitlement = CourseEntitlementFactory.create()
        pseudo_sessions = {
            str(unfulfilled_entitlement.uuid): CatalogCourseRunFactory.create()
        }
        available_sessions = {
            str(unfulfilled_entitlement.uuid): CatalogCourseRunFactory.create_batch(3)
        }
        return unfulfilled_entitlement, pseudo_sessions, available_sessions

    def make_pseudo_session_course_overviews(
        self, unfulfilled_entitlement, pseudo_sessions
    ):
        """Create course overview for course provider info"""
        course_key_str = pseudo_sessions[str(unfulfilled_entitlement.uuid)]["key"]
        course_key = CourseKey.from_string(course_key_str)
        course_overview = CourseOverviewFactory.create(id=course_key)
        return {course_key: course_overview}

    def test_happy_path(self):
        """Test that nothing breaks and the output fields look correct"""
        (
            unfulfilled_entitlement,
            pseudo_sessions,
            available_sessions,
        ) = self.make_unfulfilled_entitlement()
        pseudo_session_course_overviews = self.make_pseudo_session_course_overviews(
            unfulfilled_entitlement, pseudo_sessions
        )
        context = {
            "unfulfilled_entitlement_pseudo_sessions": pseudo_sessions,
            "course_entitlement_available_sessions": available_sessions,
            "pseudo_session_course_overviews": pseudo_session_course_overviews,
            "programs": {},
        }

        output_data = UnfulfilledEntitlementSerializer(
            unfulfilled_entitlement, context=context
        ).data

        expected_keys = [
            "courseProvider",
            "course",
            "entitlement",
            "programs",
            "courseRun",
            "gradeData",
            "certificate",
            "enrollment",
            "credit",
        ]

        assert output_data.keys() == set(expected_keys)
        assert output_data["courseProvider"] is not None
        assert output_data["courseRun"] is None
        assert output_data["gradeData"] is None
        assert output_data["certificate"] is None
        assert (
            output_data["enrollment"]
            == UnfulfilledEntitlementSerializer.STATIC_ENTITLEMENT_ENROLLMENT_DATA
        )
        assert (
            output_data["course"]
            == CourseSerializer(pseudo_session_course_overviews.popitem()[1]).data
        )
        assert output_data["courseProvider"] is not None
        assert output_data["programs"] == {"relatedPrograms": []}

    def test_programs(self):
        (
            unfulfilled_entitlement,
            pseudo_sessions,
            available_sessions,
        ) = self.make_unfulfilled_entitlement()
        pseudo_session_course_overviews = self.make_pseudo_session_course_overviews(
            unfulfilled_entitlement, pseudo_sessions
        )
        related_programs = ProgramFactory.create_batch(3)
        programs = {str(unfulfilled_entitlement.course_uuid): related_programs}

        context = {
            "unfulfilled_entitlement_pseudo_sessions": pseudo_sessions,
            "course_entitlement_available_sessions": available_sessions,
            "pseudo_session_course_overviews": pseudo_session_course_overviews,
            "programs": programs,
        }

        output_data = UnfulfilledEntitlementSerializer(
            unfulfilled_entitlement, context=context
        ).data

        assert (
            output_data["programs"]
            == ProgramsSerializer({"relatedPrograms": related_programs}).data
        )

    def test_static_enrollment_data(self):
        """
        For an unfulfilled entitlement's "enrollment" data, we're returning a static dict.
        This test is to ensure that that dict has the same keys as returned by the LearnerEnrollmentSerializer
        """
        output_data = TestEnrollmentSerializer().serialize_test_enrollment()
        expected_keys = (
            UnfulfilledEntitlementSerializer.STATIC_ENTITLEMENT_ENROLLMENT_DATA.keys()
        )
        actual_keys = output_data.keys()
        assert expected_keys == actual_keys


class TestSuggestedCourseSerializer(TestCase):
    """High-level tests for SuggestedCourseSerializer"""

    @classmethod
    def mock_suggested_courses(cls, courses_count=5):
        """
        Sample return data from general recommendations
        """
        suggested_courses = {
            "courses": [],
            "is_personalized_recommendation": False,
        }

        for i in range(courses_count):
            suggested_courses["courses"].append(
                {
                    "course_key": uuid4(),
                    "logo_image_url": random_url(),
                    "marketing_url": random_url(),
                    "title": str(uuid4()),
                },
            )

        return suggested_courses

    def test_happy_path(self):
        """Test that data serializes correctly"""

        input_data = self.mock_suggested_courses(courses_count=1)["courses"][0]

        output_data = SuggestedCourseSerializer(input_data).data

        self.assertDictEqual(
            output_data,
            {
                "bannerImgSrc": input_data["logo_image_url"],
                "logoImgSrc": None,
                "courseName": input_data["title"],
                "courseUrl": input_data["marketing_url"],
            },
        )


class TestEmailConfirmationSerializer(TestCase):
    """High-level tests for EmailConfirmationSerializer"""

    @classmethod
    def generate_test_data(cls):
        return {
            "isNeeded": random_bool(),
            "sendEmailUrl": random_url(),
        }

    def test_structure(self):
        """Test that nothing breaks and the output fields look correct"""
        input_data = self.generate_test_data()

        output_data = EmailConfirmationSerializer(input_data).data

        expected_keys = [
            "isNeeded",
            "sendEmailUrl",
        ]
        assert output_data.keys() == set(expected_keys)

    def test_happy_path(self):
        """Test that data serializes correctly"""

        input_data = self.generate_test_data()

        output_data = EmailConfirmationSerializer(input_data).data

        self.assertDictEqual(
            output_data,
            {
                "isNeeded": input_data["isNeeded"],
                "sendEmailUrl": input_data["sendEmailUrl"],
            },
        )


class TestEnterpriseDashboardSerializer(TestCase):
    """High-level tests for EnterpriseDashboardSerializer"""

    @classmethod
    def generate_test_enterprise_customer(cls):
        return {
            "name": random_string(),
            "slug": str(uuid4()),
            "enable_learner_portal": True,
            "uuid": str(uuid4()),
            "auth_org_id": str(uuid4()),
        }

    def test_structure(self):
        """Test that nothing breaks and the output fields look correct"""
        input_data = self.generate_test_enterprise_customer()

        output_data = EnterpriseDashboardSerializer(input_data).data

        expected_keys = ["label", "url", "uuid", "isLearnerPortalEnabled", "authOrgId"]
        self.assertEqual(output_data.keys(), set(expected_keys))

    def test_happy_path(self):
        """Test that data serializes correctly"""

        input_data = self.generate_test_enterprise_customer()

        output_data = EnterpriseDashboardSerializer(input_data).data

        self.assertDictEqual(
            output_data,
            {
                "label": input_data["name"],
                "url": settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL
                + "/"
                + input_data["slug"],
                "uuid": input_data["uuid"],
                "isLearnerPortalEnabled": input_data["enable_learner_portal"],
                "authOrgId": input_data["auth_org_id"],
            },
        )

    def test_no_auth_org_id(self):
        """ Test for missing auth_org_id """
        input_data = self.generate_test_enterprise_customer()
        del input_data['auth_org_id']
        self.assertIsNone(EnterpriseDashboardSerializer(input_data).data['authOrgId'])


class TestSocialMediaSettingsSiteSerializer(TestCase):
    """Tests for the SocialMediaSiteSettingsSerializer"""

    @classmethod
    def generate_test_social_media_settings(cls):
        return {
            "is_enabled": random_bool(),
            "brand": random_string(),
            "utm_params": random_string(),
        }

    def test_structure(self):
        """Test that nothing breaks and the output fields look correct"""
        input_data = self.generate_test_social_media_settings()

        output_data = SocialMediaSiteSettingsSerializer(input_data).data

        expected_keys = [
            "isEnabled",
            "socialBrand",
            "utmParams",
        ]
        self.assertEqual(output_data.keys(), set(expected_keys))


class TestSocialShareSettingsSerializer(TestCase):
    """Tests for the SocialShareSettingsSerializer"""

    @classmethod
    def generate_test_social_share_settings(cls):
        return {
            "twitter": TestSocialMediaSettingsSiteSerializer.generate_test_social_media_settings(),
            "facebook": TestSocialMediaSettingsSiteSerializer.generate_test_social_media_settings(),
        }

    def test_structure(self):
        """Test that nothing breaks and the output fields look correct"""
        input_data = self.generate_test_social_share_settings()

        output_data = SocialShareSettingsSerializer(input_data).data

        expected_keys = ["twitter", "facebook"]
        self.assertEqual(output_data.keys(), set(expected_keys))


class TestLearnerDashboardSerializer(LearnerDashboardBaseTest):
    """High-level tests for Learner Dashboard serialization"""

    # Show full diff for serialization issues
    maxDiff = None

    def make_test_context(
        self,
        enrollments=None,
        enrollments_with_entitlements=None,
        unfulfilled_entitlements=None,
        has_programs=False,
    ):
        """
        Given enrollments and entitlements, generate a matching serializer context
        """
        enrollments = enrollments or []
        enrollments_with_entitlements = enrollments_with_entitlements or []
        unfulfilled_entitlements = unfulfilled_entitlements or []

        resume_course_urls = {
            enrollment.course.id: random_url() for enrollment in enrollments
        }
        course_mode_info = {
            enrollment.course.id: {
                "verified_sku": str(uuid4()),
                "days_for_upsell": randint(0, 14),
            }
            for enrollment in enrollments
            if enrollment.mode == "audit"
        }

        all_entitlements = list(unfulfilled_entitlements)
        fulfilled_entitlements = {}
        for enrollment in enrollments_with_entitlements:
            entitlement = CourseEntitlementFactory.create(
                enrollment_course_run=enrollment
            )
            all_entitlements.append(entitlement)
            fulfilled_entitlements[str(enrollment.course_id)] = entitlement

        unfulfilled_entitlement_pseudo_sessions = {
            str(unfulfilled_entitlement.uuid): CatalogCourseRunFactory.create()
            for unfulfilled_entitlement in unfulfilled_entitlements
        }

        course_entitlement_available_sessions = {
            str(entitlement.uuid): CatalogCourseRunFactory.create_batch(3)
            for entitlement in all_entitlements
        }

        # Create related course overviews for entitlement pseudo sessions
        pseudo_session_course_overviews = {}
        for unfulfilled_entitlement in unfulfilled_entitlement_pseudo_sessions:
            course_key_str = unfulfilled_entitlement_pseudo_sessions[
                unfulfilled_entitlement
            ]["key"]
            course_key = CourseKey.from_string(course_key_str)
            course_overview = CourseOverviewFactory.create(id=course_key)

            pseudo_session_course_overviews[course_key] = course_overview
        programs = (
            {
                str(enrollment.course.id): ProgramFactory.create_batch(3)
                for enrollment in enrollments
            }
            if has_programs
            else {}
        )

        input_context = {
            "resume_course_urls": resume_course_urls,
            "ecommerce_payment_page": random_url(),
            "course_mode_info": course_mode_info,
            "credit_statuses": {},
            "fulfilled_entitlements": fulfilled_entitlements,
            "unfulfilled_entitlement_pseudo_sessions": unfulfilled_entitlement_pseudo_sessions,
            "course_entitlement_available_sessions": course_entitlement_available_sessions,
            "pseudo_session_course_overviews": pseudo_session_course_overviews,
            "programs": programs,
        }
        return input_context

    def test_empty(self):
        """Test that empty inputs return the right keys"""

        input_data = {
            "emailConfirmation": None,
            "enterpriseDashboard": None,
            "platformSettings": None,
            "enrollments": [],
            "unfulfilledEntitlements": [],
            "socialShareSettings": None,
            "suggestedCourses": [],
        }
        output_data = LearnerDashboardSerializer(input_data).data

        self.assertDictEqual(
            output_data,
            {
                "emailConfirmation": None,
                "enterpriseDashboard": None,
                "platformSettings": None,
                "courses": [],
                "socialShareSettings": None,
                "suggestedCourses": [],
            },
        )

    def test_enrollments(self):
        """Test that enrollments-related info is linked and serialized correctly"""

        enrollments = [self.create_test_enrollment()]

        input_context = self.make_test_context(
            enrollments=enrollments,
        )

        input_data = {
            "emailConfirmation": None,
            "enterpriseDashboard": None,
            "platformSettings": None,
            "enrollments": enrollments,
            "unfulfilledEntitlements": [],
            "socialShareSettings": None,
            "suggestedCourses": [],
        }

        output_data = LearnerDashboardSerializer(input_data, context=input_context).data

        # Right now just make sure nothing broke
        courses = output_data.pop("courses")
        assert courses is not None

    def test_entitlements(self):
        # One standard enrollment, one fulfilled entitlement, one unfulfilled enrollment
        enrollments = [self.create_test_enrollment(), self.create_test_enrollment()]
        unfulfilled_entitlements = [CourseEntitlementFactory.create()]

        input_context = self.make_test_context(
            enrollments=enrollments,
            enrollments_with_entitlements=[enrollments[1]],
            unfulfilled_entitlements=unfulfilled_entitlements,
            has_programs=True,
        )

        input_data = {
            "emailConfirmation": None,
            "enterpriseDashboards": None,
            "platformSettings": None,
            "enrollments": enrollments,
            "unfulfilledEntitlements": unfulfilled_entitlements,
            "socialShareSettings": None,
            "suggestedCourses": [],
        }

        output_data = LearnerDashboardSerializer(input_data, context=input_context).data

        courses = output_data.pop("courses")
        # We should have three dicts with identical keys for the course card elements
        assert len(courses) == 3
        self._assert_all_keys_equal(courses)
        # Non-entitlement enrollment should have no entitlement info
        assert not courses[0]["entitlement"]
        # Fulfilled and Unfulfilled entitlement should have identical keys
        fulfilled_entitlement = courses[1]["entitlement"]
        unfulfilled_entitlement = courses[2]["entitlement"]
        assert fulfilled_entitlement
        assert unfulfilled_entitlement
        assert fulfilled_entitlement.keys() == unfulfilled_entitlement.keys()

        # test programs
        assert courses[0]["programs"]
        assert len(courses[0]["programs"]["relatedPrograms"]) == 3

    def test_suggested_courses(self):

        suggested_courses = TestSuggestedCourseSerializer.mock_suggested_courses()[
            "courses"
        ]

        input_data = {
            "emailConfirmation": None,
            "enterpriseDashboard": None,
            "platformSettings": None,
            "enrollments": [],
            "unfulfilledEntitlements": [],
            "socialShareSettings": None,
            "suggestedCourses": suggested_courses,
        }
        output_data = LearnerDashboardSerializer(input_data).data

        output_suggested_courses = output_data.pop("suggestedCourses")

        self.assertEqual(len(suggested_courses), len(output_suggested_courses))

    @mock.patch(
        "lms.djangoapps.learner_home.serializers.SuggestedCourseSerializer.to_representation"
    )
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.SocialShareSettingsSerializer.to_representation"
    )
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.UnfulfilledEntitlementSerializer.data"
    )
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.LearnerEnrollmentSerializer.data"
    )
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.PlatformSettingsSerializer.to_representation"
    )
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.EnterpriseDashboardSerializer.to_representation"
    )
    @mock.patch(
        "lms.djangoapps.learner_home.serializers.EmailConfirmationSerializer.to_representation"
    )
    def test_linkage(
        self,
        mock_email_confirmation_serializer,
        mock_enterprise_dashboard_serializer,
        mock_platform_settings_serializer,
        mock_learner_enrollment_serializer,
        mock_entitlement_serializer,
        mock_social_settings_serializer,
        mock_suggestions_serializer,
    ):
        mock_email_confirmation_serializer.return_value = (
            mock_email_confirmation_serializer
        )
        mock_enterprise_dashboard_serializer.return_value = (
            mock_enterprise_dashboard_serializer
        )
        mock_platform_settings_serializer.return_value = (
            mock_platform_settings_serializer
        )
        mock_learner_enrollment_serializer.return_value = (
            mock_learner_enrollment_serializer
        )
        mock_entitlement_serializer.return_value = mock_entitlement_serializer
        mock_social_settings_serializer.return_value = mock_social_settings_serializer
        mock_suggestions_serializer.return_value = mock_suggestions_serializer

        input_data = {
            "emailConfirmation": {},
            "enterpriseDashboard": {},
            "platformSettings": {},
            "enrollments": [{}],
            "unfulfilledEntitlements": [{}],
            "socialShareSettings": {},
            "suggestedCourses": [{}],
        }
        output_data = LearnerDashboardSerializer(input_data).data

        self.assertDictEqual(
            output_data,
            {
                "emailConfirmation": mock_email_confirmation_serializer,
                "enterpriseDashboard": mock_enterprise_dashboard_serializer,
                "platformSettings": mock_platform_settings_serializer,
                "courses": [
                    mock_learner_enrollment_serializer,
                    mock_entitlement_serializer,
                ],
                "socialShareSettings": mock_social_settings_serializer,
                "suggestedCourses": [mock_suggestions_serializer],
            },
        )
