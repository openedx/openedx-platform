"""
Contains all the URLs for the Dark Language Support App
"""

from django.urls import path

from openedx.core.djangoapps.dark_lang import views

app_name = 'dark_lang'
urlpatterns = [
    path('', views.PreviewLanguageFragmentView.as_view(), name='preview_lang'),
]
