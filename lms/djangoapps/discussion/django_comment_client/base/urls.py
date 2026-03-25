"""
Base urls for the django_comment_client.
"""


from django.urls import path, re_path  # noqa: I001

from lms.djangoapps.discussion.django_comment_client.base import views

urlpatterns = [
    path('upload', views.upload, name='upload'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/update$', views.update_thread, name='update_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/reply$', views.create_comment, name='create_comment'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/delete', views.delete_thread, name='delete_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/upvote$', views.vote_for_thread, {'value': 'up'}, name='upvote_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/downvote$', views.vote_for_thread, {'value': 'down'},
            name='downvote_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/flagAbuse$', views.flag_abuse_for_thread, name='flag_abuse_for_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/unFlagAbuse$', views.un_flag_abuse_for_thread,
            name='un_flag_abuse_for_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/unvote$', views.undo_vote_for_thread, name='undo_vote_for_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/pin$', views.pin_thread, name='pin_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/unpin$', views.un_pin_thread, name='un_pin_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/follow$', views.follow_thread, name='follow_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/unfollow$', views.unfollow_thread, name='unfollow_thread'),
    re_path(r'^threads/(?P<thread_id>[\w\-]+)/close$', views.openclose_thread, name='openclose_thread'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/update$', views.update_comment, name='update_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/endorse$', views.endorse_comment, name='endorse_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/reply$', views.create_sub_comment, name='create_sub_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/delete$', views.delete_comment, name='delete_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/upvote$', views.vote_for_comment,
            {'value': 'up'}, name='upvote_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/downvote$', views.vote_for_comment, {'value': 'down'},
            name='downvote_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/unvote$', views.undo_vote_for_comment, name='undo_vote_for_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/flagAbuse$', views.flag_abuse_for_comment,
            name='flag_abuse_for_comment'),
    re_path(r'^comments/(?P<comment_id>[\w\-]+)/unFlagAbuse$', views.un_flag_abuse_for_comment,
            name='un_flag_abuse_for_comment'),
    re_path(r'^(?P<commentable_id>[\w\-.]+)/threads/create$', views.create_thread, name='create_thread'),
    re_path(r'^(?P<commentable_id>[\w\-.]+)/follow$', views.follow_commentable, name='follow_commentable'),
    re_path(r'^(?P<commentable_id>[\w\-.]+)/unfollow$', views.unfollow_commentable, name='unfollow_commentable'),
    path('users', views.users, name='users'),
]
