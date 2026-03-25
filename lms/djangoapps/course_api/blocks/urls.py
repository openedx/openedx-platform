"""
Course Block API URLs
"""


from django.conf import settings  # noqa: I001
from django.urls import path, re_path

from .views import BlockMetadataView, BlocksInCourseView, BlocksView

urlpatterns = [
    # This endpoint requires the usage_key for the starting block.
    re_path(
        fr'^v1/blocks/{settings.USAGE_KEY_PATTERN}',
        BlocksView.as_view(),
        kwargs={'hide_access_denials': True},
        name="blocks_in_block_tree"
    ),

    # This endpoint is an alternative to the above, but requires course_id as a parameter.
    path(
        'v1/blocks/',
        BlocksInCourseView.as_view(),
        kwargs={'hide_access_denials': True},
        name="blocks_in_course"
    ),
    # This endpoint requires the usage_key
    re_path(
        fr'^v1/block_metadata/{settings.USAGE_KEY_PATTERN}',
        BlockMetadataView.as_view(),
        name="blocks_metadata"
    ),

    # This endpoint requires the usage_key for the starting block.
    re_path(
        fr'^v2/blocks/{settings.USAGE_KEY_PATTERN}',
        BlocksView.as_view(),
        name="blocks_in_block_tree"
    ),

    # This endpoint is an alternative to the above, but requires course_id as a parameter.
    path(
        'v2/blocks/',
        BlocksInCourseView.as_view(),
        name="blocks_in_course"
    ),

    # This endpoint requires the usage_key
    re_path(
        fr'^v2/block_metadata/{settings.USAGE_KEY_PATTERN}',
        BlockMetadataView.as_view(),
        name="blocks_metadata"
    ),
]

if getattr(settings, 'PROVIDER_STATES_URL', None):
    from .tests.pacts.views import provider_state
    urlpatterns.append(path(
        'pact/provider_states/',
        provider_state,
        name='provider-state-view'
    ))
