"""
URL configuration for the new XBlock API
"""
from django.urls import include, path, re_path, register_converter

from . import url_converters, views

# Note that the exact same API URLs are used in Studio and the LMS, but the API
# may act a bit differently in each (e.g. Studio stores user state ephemerally).
# If necessary at some point in the future, these URLs could be duplicated into
# urls_studio and urls_lms, and/or the views could be likewise duplicated.
app_name = 'openedx.core.djangoapps.xblock.rest_api'

register_converter(url_converters.UsageKeyV2Converter, "usage_v2")
register_converter(url_converters.VersionConverter, "block_version")

block_endpoints = [
    # get metadata about an XBlock:
    path('', views.block_metadata),
    # get/post full json fields of an XBlock:
    path('fields/', views.BlockFieldsView.as_view()),
    # Get the OLX source code of the specified block
    path('olx/', views.get_block_olx_view),
    # render one of this XBlock's views (e.g. student_view)
    path('view/<str:view_name>/', views.render_block_view),
    # get the URL needed to call this XBlock's handlers
    path('handler_url/<str:handler_name>/', views.get_handler_url),
    # call one of this block's handlers
    re_path(
        r'^handler/(?P<user_id>\w+)-(?P<secure_token>\w+)/(?P<handler_name>[\w\-]+)/(?P<suffix>.+)?$',
        views.xblock_handler,
        name='xblock_handler',
    ),
    # API endpoints related to a specific version of this XBlock:
]

urlpatterns = [
    path('api/xblock/v2/', include([
        path(r'xblocks/<usage_v2:usage_key>/', include(block_endpoints)),
        path(r'xblocks/<usage_v2:usage_key>@<block_version:version>/', include(block_endpoints)),
    ])),
    # Non-API views (these return HTML, not JSON):
    path('xblocks/v2/<usage_v2:usage_key>/', include([
        # render one of this XBlock's views (e.g. student_view) for embedding in an iframe
        # NOTE: this endpoint is **unstable** and subject to changes after Sumac
        path('embed/<str:view_name>/', views.embed_block_view),
    ])),
]
