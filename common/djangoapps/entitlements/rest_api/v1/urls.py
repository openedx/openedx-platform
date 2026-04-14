"""
URLs for the V1 of the Entitlements API.
"""

from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter

from .views import EntitlementEnrollmentViewSet, EntitlementViewSet

router = DefaultRouter()
router.register(r'entitlements', EntitlementViewSet, basename='entitlements')

ENROLLMENTS_VIEW = EntitlementEnrollmentViewSet.as_view({
    'post': 'create',
    'delete': 'destroy',
})

app_name = 'v1'
urlpatterns = [
    path('', include(router.urls)),
    re_path(
        fr'entitlements/(?P<uuid>{EntitlementViewSet.ENTITLEMENT_UUID4_REGEX})/enrollments$',
        ENROLLMENTS_VIEW,
        name='enrollments'
    ),
]
