"""
URLs for the rss_proxy djangoapp.
"""


from django.urls import path  # noqa: I001

from .views import proxy

app_name = 'rss_proxy'
urlpatterns = [
    path('', proxy, name='proxy'),
]
