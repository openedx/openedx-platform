"""
API v0 URLs.
"""


from django.urls import include, path, re_path  # noqa: I001

from . import views

BASKET_URLS = ([
    path('', views.BasketsView.as_view(), name='create'),
    re_path(r'^(?P<basket_id>[\w]+)/order/$', views.BasketOrderView.as_view(), name='retrieve_order'),
], 'baskets')

app_name = 'v0'
urlpatterns = [
    path('baskets/', include(BASKET_URLS)),
]
