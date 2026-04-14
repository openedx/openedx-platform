"""
Defines the URL routes for this app.
"""

from django.urls import path

from . import views

app_name = 'catalog'
urlpatterns = [
    path('management/cache_programs/', views.cache_programs, name='cache_programs'),
]
