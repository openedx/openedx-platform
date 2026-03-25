"""
Forum urls for the django_comment_client.
"""


from django.urls import path, re_path  # noqa: I001

from . import views

urlpatterns = [
    re_path(r'users/(?P<user_id>\w+)/followed$', views.followed_threads, name='followed_threads'),
    re_path(r'users/(?P<user_id>\w+)$', views.user_profile, name='user_profile'),
    re_path(r'^(?P<discussion_id>[\w\-.]+)/threads/(?P<thread_id>\w+)$', views.single_thread, name='single_thread'),
    re_path(r'^(?P<discussion_id>[\w\-.]+)/inline$', views.inline_discussion, name='inline_discussion'),
    path(
        'discussion_board_fragment_view',
        views.DiscussionBoardFragmentView.as_view(),
        name='discussion_board_fragment_view'
    ),
    re_path('', views.forum_form_discussion, name='forum_form_discussion'),
]
