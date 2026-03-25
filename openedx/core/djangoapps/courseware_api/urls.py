"""
Contains all the URLs
"""


from django.conf import settings  # noqa: I001
from django.urls import path, re_path

from openedx.core.djangoapps.courseware_api import views

urlpatterns = [
    re_path(fr'^course/{settings.COURSE_KEY_PATTERN}',
            views.CoursewareInformation.as_view(),
            name="courseware-api"),
    re_path(fr'^sequence/{settings.USAGE_KEY_PATTERN}',
            views.SequenceMetadata.as_view(),
            name="sequence-api"),
    re_path(fr'^resume/{settings.COURSE_KEY_PATTERN}',
            views.Resume.as_view(),
            name="resume-api"),
    re_path(fr'^celebration/{settings.COURSE_KEY_PATTERN}',
            views.Celebration.as_view(),
            name="celebration-api"),
]

if getattr(settings, 'PROVIDER_STATES_URL', None):
    from .tests.pacts.views import provider_state
    urlpatterns.append(path('pact/provider_states/', provider_state,
                            name='provider-state-view'
                            ))
