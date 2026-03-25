"""
Grades API URLs.
"""


from django.urls import include, path  # noqa: I001

app_name = 'lms.djangoapps.grades'

urlpatterns = [
    path('v1/', include('lms.djangoapps.grades.rest_api.v1.urls', namespace='v1'))
]
