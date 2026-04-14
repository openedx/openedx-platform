""" URLs for User Tours. """

from django.conf import settings
from django.urls import re_path

from lms.djangoapps.user_tours.v1.views import UserDiscussionsToursView, UserTourView

urlpatterns = [
    re_path(fr'^v1/{settings.USERNAME_PATTERN}$', UserTourView.as_view(), name='user-tours'),
    re_path(r'^discussion_tours/(?P<tour_id>\d+)?/?$', UserDiscussionsToursView.as_view(), name='discussion-tours'),
]
