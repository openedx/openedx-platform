"""
Studio URL configuration for Content Staging (& Clipboard)
"""
from django.urls import include, path

from . import views

urlpatterns = [
    path('api/content-staging/v1/', include([
        path('staged-content/<int:id>/olx', views.StagedContentOLXEndpoint.as_view(), name="staged-content-olx"),
        path('clipboard/', views.ClipboardEndpoint.as_view()),
    ])),
]
