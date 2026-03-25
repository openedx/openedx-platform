""" URLs configuration for the mfe api."""

from django.urls import path  # noqa: I001

from lms.djangoapps.mfe_config_api.views import MFEConfigView

app_name = 'mfe_config_api'
urlpatterns = [
    path('', MFEConfigView.as_view(), name='config'),
]
