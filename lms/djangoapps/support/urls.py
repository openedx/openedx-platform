"""
URLs for the student support app.
"""

from django.conf import settings
from django.urls import path, re_path

from .views.certificate import CertificatesSupportView
from .views.contact_us import ContactUsView
from .views.course_entitlements import EntitlementSupportView
from .views.course_reset import CourseResetAPIView
from .views.enrollments import EnrollmentSupportListView, EnrollmentSupportView
from .views.feature_based_enrollments import FeatureBasedEnrollmentsSupportView, FeatureBasedEnrollmentSupportAPIView
from .views.index import index
from .views.manage_user import ManageUserDetailView, ManageUserSupportView
from .views.onboarding_status import OnboardingView
from .views.program_enrollments import (
    LinkProgramEnrollmentSupportAPIView,
    LinkProgramEnrollmentSupportView,
    ProgramEnrollmentsInspectorAPIView,
    ProgramEnrollmentsInspectorView,
    SAMLProvidersWithOrg
)
from .views.sso_records import SsoView

COURSE_ENTITLEMENTS_VIEW = EntitlementSupportView.as_view()

app_name = 'support'
urlpatterns = [
    path('', index, name="index"),
    re_path(r'^certificates/?$', CertificatesSupportView.as_view(), name="certificates"),
    re_path(r'^enrollment/?$', EnrollmentSupportView.as_view(), name="enrollment"),
    re_path(r'^course_entitlement/?$', COURSE_ENTITLEMENTS_VIEW, name="course_entitlement"),
    re_path(r'^contact_us/?$', ContactUsView.as_view(), name="contact_us"),
    re_path(
        r'^enrollment/(?P<username_or_email>[\w.@+-]+)?$',
        EnrollmentSupportListView.as_view(),
        name="enrollment_list"
    ),
    re_path(r'^manage_user/?$', ManageUserSupportView.as_view(), name="manage_user"),
    re_path(
        r'^manage_user/(?P<username_or_email>[\w.@+-]+)?$',
        ManageUserDetailView.as_view(),
        name="manage_user_detail"
    ),
    re_path(
        r'^feature_based_enrollments/?$',
        FeatureBasedEnrollmentsSupportView.as_view(),
        name="feature_based_enrollments"
    ),
    re_path(
        fr'^feature_based_enrollment_details/{settings.COURSE_ID_PATTERN}$',
        FeatureBasedEnrollmentSupportAPIView.as_view(),
        name="feature_based_enrollment_details"
    ),
    re_path(
        r'link_program_enrollments/?$',
        LinkProgramEnrollmentSupportView.as_view(),
        name='link_program_enrollments'
    ),
    re_path(
        r'link_program_enrollments_details/?$',
        LinkProgramEnrollmentSupportAPIView.as_view(),
        name='link_program_enrollments_details'
    ),
    re_path(
        r'program_enrollments_inspector/?$',
        ProgramEnrollmentsInspectorView.as_view(),
        name='program_enrollments_inspector'
    ),
    re_path(
        r'get_saml_providers/?$',
        SAMLProvidersWithOrg.as_view(),
        name='get_saml_providers'
    ),
    re_path(
        r'program_enrollments_inspector_details/?$',
        ProgramEnrollmentsInspectorAPIView.as_view(),
        name='program_enrollments_inspector_details'
    ),
    re_path(r'sso_records/(?P<username_or_email>[\w.@+-]+)?$', SsoView.as_view(), name='sso_records'),
    re_path(
        r'onboarding_status/(?P<username_or_email>[\w.@+-]+)?$',
        OnboardingView.as_view(), name='onboarding_status'
    ),
    re_path(
        r'course_reset/(?P<username_or_email>[\w.@+-]+)?$',
        CourseResetAPIView.as_view(),
        name='course_reset'
    ),
]
