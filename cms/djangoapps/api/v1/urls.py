"""
URLs for the Studio API [Course Run]
"""


from rest_framework.routers import DefaultRouter  # noqa: I001

from .views.course_runs import CourseRunViewSet

app_name = 'cms.djangoapps.api.v1'

router = DefaultRouter()
router.register(r'course_runs', CourseRunViewSet, basename='course_run')
urlpatterns = router.urls
