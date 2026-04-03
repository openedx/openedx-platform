"""Learner dashboard URL routing configuration"""

from django.urls import path, re_path

from lms.djangoapps.learner_dashboard import program_views, programs

urlpatterns = [
    path('programs/', program_views.program_listing, name='program_listing_view'),
    re_path(r'^programs/(?P<program_uuid>[0-9a-f-]+)/$', program_views.program_details, name='program_details_view'),
    re_path(r'^programs/(?P<program_uuid>[0-9a-f-]+)/discussion/$', program_views.ProgramDiscussionIframeView.as_view(),
            name='program_discussion'),
    re_path(r'^programs/(?P<program_uuid>[0-9a-f-]+)/live/$', program_views.ProgramLiveIframeView.as_view(),
            name='program_live'),
    path('programs_fragment/', programs.ProgramsFragmentView.as_view(), name='program_listing_fragment_view'),
    re_path(r'^programs/(?P<program_uuid>[0-9a-f-]+)/details_fragment/$', programs.ProgramDetailsFragmentView.as_view(),
            name='program_details_fragment_view'),
]
