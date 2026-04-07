"""URLs configuration for the mfe api."""

from django.urls import path

from lms.djangoapps.mfe_config_api.views import FrontendSiteConfigView, MFEConfigView

app_name = "mfe_config_api"

mfe_config_urls = [
    path("", MFEConfigView.as_view(), name="config"),
]

frontend_site_config_urls = [
    path("", FrontendSiteConfigView.as_view(), name="frontend_site_config"),
]
