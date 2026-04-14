"""URLs for API access management."""


from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.urls import include, path, re_path

from openedx.core.djangoapps.api_admin.decorators import api_access_enabled_or_404
from openedx.core.djangoapps.api_admin.views import (
    ApiRequestStatusView,
    ApiRequestView,
    ApiTosView,
    CatalogEditView,
    CatalogListView,
    CatalogPreviewView,
    CatalogSearchView,
)

app_name = 'api_admin'
urlpatterns = (
    path('status/', api_access_enabled_or_404(login_required(ApiRequestStatusView.as_view())),
         name="api-status"
         ),
    path('terms-of-service/', api_access_enabled_or_404(ApiTosView.as_view()),
         name="api-tos"
         ),
    path('catalogs/preview/', staff_member_required(
        api_access_enabled_or_404(CatalogPreviewView.as_view()),
        login_url='dashboard',
        redirect_field_name=None
    ),
        name='catalog-preview',
    ),
    re_path(
        r'^catalogs/user/(?P<username>[\w.@+-]+)/$',
        staff_member_required(
            api_access_enabled_or_404(CatalogListView.as_view()),
            login_url='dashboard',
            redirect_field_name=None
        ),
        name='catalog-list',
    ),
    path('catalogs/<int:catalog_id>/', staff_member_required(
        api_access_enabled_or_404(CatalogEditView.as_view()),
        login_url='dashboard',
        redirect_field_name=None
    ),
        name='catalog-edit',
    ),
    path('catalogs/', staff_member_required(
        api_access_enabled_or_404(CatalogSearchView.as_view()),
        login_url='dashboard',
        redirect_field_name=None
    ),
        name='catalog-search',
    ),
    path('', api_access_enabled_or_404(login_required(ApiRequestView.as_view())),
         name="api-request"
         ),
    path('api/', include('openedx.core.djangoapps.api_admin.api.urls', namespace='api'),
         ),
)
