"""
API urls for course app v1 APIs.
"""
from django.urls import include, path

from .v1 import urls as v1_apis

urlpatterns = [
    path("v1/", include(v1_apis, namespace="v1")),
]
