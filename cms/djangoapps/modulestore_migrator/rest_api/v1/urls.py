"""
Course to Library Import API v1 URLs.
"""
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    BlockMigrationInfo,
    BulkMigrationViewSet,
    LibraryCourseMigrationViewSet,
    MigrationInfoViewSet,
    MigrationViewSet,
    PreviewMigration,
)

ROUTER = SimpleRouter()
ROUTER.register(r'migrations', MigrationViewSet, basename='migrations')
ROUTER.register(r'bulk_migration', BulkMigrationViewSet, basename='bulk-migration')
ROUTER.register(
    r'library/(?P<lib_key_str>[^/.]+)/migrations/courses',
    LibraryCourseMigrationViewSet,
    basename='library-migrations',
)

urlpatterns = [
    path('', include(ROUTER.urls)),
    path('migration_info/', MigrationInfoViewSet.as_view(), name='migration-info'),
    path('migration_blocks/', BlockMigrationInfo.as_view(), name='migration-blocks'),
    path('migration_preview/', PreviewMigration.as_view(), name='migration-preview'),
]
