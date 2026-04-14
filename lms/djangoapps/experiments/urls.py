"""
Experimentation URLs
"""

from django.conf import settings
from django.urls import include, path, re_path

from . import routers, views, views_custom

router = routers.DefaultRouter()
router.register(r'data', views.ExperimentDataViewSet, basename='data')
router.register(r'key-value', views.ExperimentKeyValueViewSet, basename='key_value')

urlpatterns = [
    path('v0/custom/REV-934/', views_custom.Rev934.as_view(), name='rev_934'),
    path('v0/', include((router.urls, "api"), namespace='v0')),
    re_path(r'^v0/custom/userMetadata/{username},{course_key}$'.format(  # noqa: UP032
        username=settings.USERNAME_PATTERN,
        course_key=settings.COURSE_ID_PATTERN), views.UserMetaDataView.as_view(), name='user_metadata'),
]
