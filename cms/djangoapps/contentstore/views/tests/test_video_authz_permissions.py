"""
Tests for the permissions enforced by the Studio video endpoints.

With AuthZ enabled for the course, listing videos requires the view-files permission, uploading
requires create-files, changing transcript preferences requires edit-files and deleting requires
delete-files. With AuthZ disabled, Studio read access is required, as it was before AuthZ.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import patch

import ddt
from django.http import HttpResponse
from django.test import Client
from django.test.utils import override_settings
from edxval.api import create_video
from openedx_authz.authz import (
    COURSES_CREATE_FILES,
    COURSES_DELETE_FILES,
    COURSES_EDIT_FILES,
    COURSES_VIEW_FILES,
)

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from cms.djangoapps.contentstore.views.tests.test_videos import setup_s3_mocks
from openedx.core import toggles as core_toggles

VIDEO_PIPELINE = {"BUCKET": "test_bucket", "ROOT_PATH": "test_root", "VEM_S3_BUCKET": "vem_test_bucket"}
IS_ALLOWED = "openedx.core.djangoapps.authz.decorators.authz_api.is_user_allowed"


def _granted(*permissions: object) -> Callable[[str, str, str], bool]:
    """Build an ``is_user_allowed`` replacement that only grants ``permissions``."""
    granted = {permission.identifier for permission in permissions}
    return lambda _username, permission, _scope: permission in granted


@contextmanager
def authz_enabled(*permissions: object) -> Iterator[None]:
    """Enable AuthZ for the course, granting only ``permissions``."""
    with patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, "is_enabled", return_value=True):
        with patch(IS_ALLOWED, side_effect=_granted(*permissions)):
            yield


@ddt.ddt
@override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=True, VIDEO_UPLOAD_PIPELINE=VIDEO_PIPELINE)
class VideoEndpointsAuthzPermissionsTest(CourseTestCase):
    """
    Permission checks of the video endpoints, for AuthZ-enabled and legacy courses.
    """

    def setUp(self) -> None:
        super().setUp()
        self.course.video_upload_pipeline = {"course_video_upload_token": "test_token"}
        self.save_course()
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()
        patcher = patch("cms.djangoapps.contentstore.video_storage_handlers.boto3.resource")
        s3_client, *_ = setup_s3_mocks(patcher.start())
        s3_client.generate_presigned_url.return_value = "http://example.com/put_video"
        self.addCleanup(patcher.stop)
        create_video(
            {
                "edx_video_id": "some-video",
                "status": "upload",
                "client_video_id": "a.mp4",
                "duration": 0,
                "encoded_videos": [],
                "courses": [str(self.course.id)],
            }
        )

    def url(self, view_name: str, **kwargs: str) -> str:
        """URL of the given video view for the test course."""
        return reverse_course_url(view_name, self.course.id, kwargs or None)

    def request(self, client: Client, method: str, view_name: str, **kwargs: str) -> HttpResponse:
        """Send a request of the given kind to a video view and return the response."""
        url = self.url(view_name, **kwargs)
        if method == "GET":
            return client.get(url, HTTP_ACCEPT="application/json")
        if method == "DELETE":
            return client.delete(url)
        body = {"files": [{"file_name": "a.mp4", "content_type": "video/mp4"}]}
        return client.post(url, data=json.dumps(body), content_type="application/json")

    # (method, view, kwargs, permission required beyond viewing, or None for read-only operations)
    OPERATIONS = (
        ("GET", "videos_handler", {}, None),
        ("GET", "video_encodings_download", {}, None),
        ("GET", "youtube_edx_video_ids", {}, None),
        ("POST", "videos_handler", {}, COURSES_CREATE_FILES),
        ("POST", "generate_video_upload_link", {}, COURSES_CREATE_FILES),
        ("DELETE", "videos_handler", {"edx_video_id": "some-video"}, COURSES_DELETE_FILES),
    )

    # --- AuthZ enabled ---

    @ddt.data(*OPERATIONS)
    @ddt.unpack
    def test_authz_without_view_permission_forbidden(self, method, view_name, kwargs, *_) -> None:
        """A user with no permission in an AuthZ-enabled course is denied on every video endpoint."""
        with authz_enabled():
            response = self.request(self.client, method, view_name, **kwargs)
        assert response.status_code == 403

    @ddt.data(*OPERATIONS)
    @ddt.unpack
    def test_authz_view_only_forbidden_for_write_operations(self, method, view_name, kwargs, extra) -> None:
        """An auditor (view only) can read but cannot upload, change or delete."""
        with authz_enabled(COURSES_VIEW_FILES):
            response = self.request(self.client, method, view_name, **kwargs)
        if extra is None:
            assert response.status_code != 403
        else:
            assert response.status_code == 403

    @ddt.data(*(op for op in OPERATIONS if op[3] is not None))
    @ddt.unpack
    def test_authz_with_matching_permission_allowed(self, method, view_name, kwargs, extra) -> None:
        """A user holding the permission an operation requires is not denied."""
        with authz_enabled(extra):
            response = self.request(self.client, method, view_name, **kwargs)
        assert response.status_code != 403

    @ddt.data(
        (COURSES_VIEW_FILES, 403),
        (COURSES_CREATE_FILES, 403),
        (COURSES_EDIT_FILES, 200),
    )
    @ddt.unpack
    def test_authz_transcript_preferences_requires_edit(self, extra, expected_status) -> None:
        """Without the transcript feature the allowed case yields 404, so only check 403 vs not."""
        with authz_enabled(extra):
            response = self.client.delete(self.url("transcript_preferences_handler"))
        if expected_status == 403:
            assert response.status_code == 403
        else:
            assert response.status_code != 403

    def test_authz_video_images_requires_create(self) -> None:
        """Uploading a video image needs the create permission; the edit permission is not enough."""
        url = self.url("video_images_handler", edx_video_id="some-video")
        with authz_enabled(COURSES_EDIT_FILES):
            assert self.client.post(url).status_code == 403
        with authz_enabled(COURSES_CREATE_FILES):
            assert self.client.post(url).status_code != 403

    def test_authz_pagination_preference_needs_only_view(self) -> None:
        """Changing the page size stores a session preference; it must not need create permission."""
        with authz_enabled(COURSES_VIEW_FILES):
            response = self.client.post(self.url("videos_handler"), {"id": "videos_per_page", "value": "10"})
        assert response.status_code != 403

    # --- Legacy (AuthZ disabled): same Studio read access check as before ---

    @ddt.data(*OPERATIONS)
    @ddt.unpack
    def test_legacy_non_staff_forbidden(self, method, view_name, kwargs, *_) -> None:
        """With AuthZ disabled, a user without Studio access to the course is denied."""
        response = self.request(self.non_staff_client, method, view_name, **kwargs)
        assert response.status_code == 403

    @ddt.data(
        ("POST", "video_images_handler", {"edx_video_id": "some-video"}),
        ("POST", "transcript_preferences_handler", {}),
    )
    @ddt.unpack
    def test_legacy_previously_unchecked_endpoints_forbidden_without_access(self, method, view_name, kwargs) -> None:
        """With AuthZ disabled, endpoints that used to check no course access now deny users without access."""
        response = self.request(self.non_staff_client, method, view_name, **kwargs)
        assert response.status_code == 403

    @ddt.data(*OPERATIONS)
    @ddt.unpack
    def test_legacy_staff_not_forbidden(self, method, view_name, kwargs, *_) -> None:
        """With AuthZ disabled, a user with Studio access to the course is not denied."""
        response = self.request(self.client, method, view_name, **kwargs)
        assert response.status_code != 403
