"""
URL mappings for the Survey feature
"""


from django.urls import re_path  # noqa: I001

from lms.djangoapps.survey import views

urlpatterns = [
    re_path(r'^(?P<survey_name>[0-9A-Za-z]+)/$', views.view_survey, name='view_survey'),
    re_path(r'^(?P<survey_name>[0-9A-Za-z]+)/answers/$', views.submit_answers, name='submit_answers'),
]
