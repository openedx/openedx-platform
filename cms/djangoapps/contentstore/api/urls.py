""" Course API URLs. """


from django.conf import settings
from django.urls import re_path
from django.urls.conf import include, path
from rest_framework.routers import SimpleRouter

from cms.djangoapps.contentstore.api.views import course_import, course_quality, course_validation

app_name = 'contentstore'

ROUTER = SimpleRouter()
ROUTER.register(
    fr'^v1/migrate_legacy_content_blocks/{settings.COURSE_ID_PATTERN}',
    course_validation.CourseLegacyLibraryContentMigratorView,
    basename='course_ready_to_migrate_legacy_blocks'
)

urlpatterns = [
    path('', include(ROUTER.urls)),
    re_path(fr'^v0/import/{settings.COURSE_ID_PATTERN}/$',
            course_import.CourseImportView.as_view(), name='course_import'),
    re_path(fr'^v1/validation/{settings.COURSE_ID_PATTERN}/$',
            course_validation.CourseValidationView.as_view(), name='course_validation'),
    re_path(fr'^v1/quality/{settings.COURSE_ID_PATTERN}/$',
            course_quality.CourseQualityView.as_view(), name='course_quality'),
]
