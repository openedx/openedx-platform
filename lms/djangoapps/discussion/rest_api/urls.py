# pylint: skip-file
"""
Discussion API URLs
"""

from django.conf import settings
from django.urls import include, path, re_path
from rest_framework.routers import SimpleRouter

from lms.djangoapps.discussion.rest_api.views import (
    BulkDeleteUserPosts,
    CommentViewSet,
    CourseActivityStatsView,
    CourseDiscussionRolesAPIView,
    CourseDiscussionSettingsAPIView,
    CourseTopicsView,
    CourseTopicsViewV2,
    CourseTopicsViewV3,
    CourseView,
    CourseViewV2,
    LearnerThreadView,
    ReplaceUsernamesView,
    RetireUserView,
    ThreadViewSet,
    UploadFileView,
)

ROUTER = SimpleRouter()
ROUTER.register("threads", ThreadViewSet, basename="thread")
ROUTER.register("comments", CommentViewSet, basename="comment")

urlpatterns = [
    re_path(
        r"^v1/courses/{}/settings$".format(
            settings.COURSE_ID_PATTERN
        ),
        CourseDiscussionSettingsAPIView.as_view(),
        name="discussion_course_settings",
    ),
    re_path(
        r"^v1/courses/{}/learner/$".format(
            settings.COURSE_ID_PATTERN
        ),
        LearnerThreadView.as_view(),
        name="discussion_learner_threads",
    ),
    re_path(
        fr"^v1/courses/{settings.COURSE_KEY_PATTERN}/activity_stats",
        CourseActivityStatsView.as_view(),
        name="discussion_course_activity_stats",
    ),
    re_path(
        fr"^v1/courses/{settings.COURSE_ID_PATTERN}/upload$",
        UploadFileView.as_view(),
        name="upload_file",
    ),
    re_path(
        r"^v1/courses/{}/roles/(?P<rolename>[A-Za-z0-9+ _-]+)/?$".format(
            settings.COURSE_ID_PATTERN
        ),
        CourseDiscussionRolesAPIView.as_view(),
        name="discussion_course_roles",
    ),
    re_path(
        fr"^v1/courses/{settings.COURSE_ID_PATTERN}",
        CourseView.as_view(),
        name="discussion_course"
    ),
    re_path(
        fr"^v2/courses/{settings.COURSE_ID_PATTERN}",
        CourseViewV2.as_view(),
        name="discussion_course_v2"
    ),
    re_path(r'^v1/accounts/retire_forum/?$', RetireUserView.as_view(), name="retire_discussion_user"),
    path('v1/accounts/replace_username', ReplaceUsernamesView.as_view(), name="replace_discussion_username"),
    re_path(
        fr"^v1/course_topics/{settings.COURSE_ID_PATTERN}",
        CourseTopicsView.as_view(),
        name="course_topics"
    ),
    re_path(
        fr"^v2/course_topics/{settings.COURSE_ID_PATTERN}",
        CourseTopicsViewV2.as_view(),
        name="course_topics_v2"
    ),
    re_path(
        fr"^v3/course_topics/{settings.COURSE_ID_PATTERN}",
        CourseTopicsViewV3.as_view(),
        name="course_topics_v3"
    ),
    re_path(
        fr"^v1/bulk_delete_user_posts/{settings.COURSE_ID_PATTERN}",
        BulkDeleteUserPosts.as_view(),
        name="bulk_delete_user_posts"
    ),
    path('v1/', include(ROUTER.urls)),
]
