"""
URL routes for the bookmarks app.
"""


from django.conf import settings
from django.urls import path, re_path

from .views import BookmarksDetailView, BookmarksListView

urlpatterns = [
    path('v1/bookmarks/', BookmarksListView.as_view(),
         name='bookmarks'
         ),
    re_path(
        r'^v1/bookmarks/{username},{usage_key}/$'.format(  # noqa: UP032
            username=settings.USERNAME_PATTERN,
            usage_key=settings.USAGE_ID_PATTERN
        ),
        BookmarksDetailView.as_view(),
        name='bookmarks_detail'
    ),
]
