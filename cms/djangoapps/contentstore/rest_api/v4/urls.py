"""Contentstore API v4 URLs."""

from rest_framework.routers import DefaultRouter

from cms.djangoapps.contentstore.rest_api.v4.views import home

app_name = "v4"

# ADR 0028: HomeCoursesViewSet registered via DefaultRouter.
# Generates: GET home/courses/ → name: home-courses-list
router = DefaultRouter()
router.register(r'home/courses', home.HomeCoursesViewSet, basename='home-courses')

urlpatterns = router.urls
