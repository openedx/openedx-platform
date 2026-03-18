"""
URLs for the Agreements API
"""

from django.conf import settings
from django.urls import path, re_path, include
from rest_framework.routers import DefaultRouter

from openedx.core.djangoapps.agreements.views import (
    IntegritySignatureView,
    LTIPIISignatureView,
    UserAgreementRecordsView,
    UserAgreementsViewSet,
)

router = DefaultRouter()
router.register(r"agreement", UserAgreementsViewSet, basename="user_agreements")

urlpatterns = [
    re_path(r'^integrity_signature/{course_id}$'.format(
        course_id=settings.COURSE_ID_PATTERN
    ), IntegritySignatureView.as_view(), name='integrity_signature'),
    re_path(r'^lti_pii_signature/{course_id}$'.format(
        course_id=settings.COURSE_ID_PATTERN
    ), LTIPIISignatureView.as_view(), name='lti_pii_signature'),
    path('agreement_record/<slug:agreement_type>', UserAgreementRecordsView.as_view(), name='user_agreement_record'),
    path('', include(router.urls)),
]
