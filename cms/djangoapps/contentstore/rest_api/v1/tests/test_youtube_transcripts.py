"""
REST-level tests for the v1 YouTube transcripts endpoints
(``YoutubeTranscriptsViewSet``).

These test the *view* — routing, auth/permission enforcement, and the
legacy-``JsonResponse``-to-DRF-``Response`` wrapping — by mocking the
``check_transcripts`` / ``replace_transcripts`` legacy functions at their
import location in the view module (the same pattern ``test_xblock.py`` uses
for ``handle_xblock``). The underlying business logic (YouTube API calls,
VAL, modulestore) is already covered by
``cms/djangoapps/contentstore/views/tests/test_transcripts.py`` and is not
re-tested here.

``AuthorizeStaffTestCase`` contributes four inherited tests, not two:
``test_student`` / ``test_instructor_in_another_course`` (expect 403, and run
against the real unmocked view since the permission decorator rejects before
the legacy function is ever reached) plus ``test_global_staff`` /
``test_course_instructor`` (expect 200). The latter two call
``self.make_request()`` with no arguments, so — following the precedent in
``rest_api/v0/tests/test_xblock.py`` (``XBlockViewTestCase.make_request`` is
itself decorated with ``@patch(..., return_value=...)``) — ``make_request``
here is decorated with a default-success ``@patch`` on the legacy function so
those inherited 200-expecting tests pass without a real, unmocked round-trip
through modulestore/VAL/YouTube-API code (impractical at this view-level
layer, and already out of scope per the module docstring above). Tests that
need a different mocked return value use their own ``@patch`` on a
differently-named test method instead of relying on the shared default.
"""
import json
from unittest.mock import patch
from urllib.parse import urlencode

from django.http import Http404
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.tests.test_utils import AuthorizeStaffTestCase
from common.djangoapps.util.json_request import JsonResponse
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

VERSION = "v1"

_SUCCESS_CHECK_BODY = {
    "html5_local": [],
    "html5_equal": False,
    "is_youtube_mode": True,
    "youtube_local": True,
    "youtube_server": False,
    "youtube_diff": True,
    "current_item_subs": None,
    "status": "Success",
    "command": "found",
}

_SUCCESS_UPLOAD_BODY = {
    "edx_video_id": "test-edx-video-id",
    "status": "Success",
}

_REQUEST_DATA = {
    "locator": "block-v1:edX+DemoX+Demo_Course+type@video+block@abcd",
    "videos": [{"type": "youtube", "video": "JMD_ifUUfsU", "mode": "youtube"}],
}


class YoutubeTranscriptsViewSetTestBase(AuthorizeStaffTestCase, ModuleStoreTestCase, APITestCase):
    """Shared setup for the check/upload view tests."""

    def get_url(self, url_name, course_id=None):
        return reverse(
            f"cms.djangoapps.contentstore:{VERSION}:{url_name}",
            kwargs={"course_id": course_id or self.get_course_key_string()},
        )

    def login_as_instructor(self):
        self.client.login(username=self.course_instructor.username, password=self.password)


class YoutubeTranscriptCheckViewTest(YoutubeTranscriptsViewSetTestBase):
    """Tests for GET .../youtube_transcripts/{course_id}/check/"""

    @patch(
        "cms.djangoapps.contentstore.rest_api.v1.views.youtube_transcripts.check_transcripts",
        return_value=JsonResponse(_SUCCESS_CHECK_BODY, 200),
    )
    def make_request(self, mock_check_transcripts=None, course_id=None):  # pylint: disable=arguments-differ
        """
        Issue the GET request with the legacy ``check_transcripts`` mocked to
        a default 200/success response.

        Decorating ``make_request`` itself (rather than each test method)
        mirrors ``XBlockViewTestCase.make_request`` in
        ``rest_api/v0/tests/test_xblock.py`` — it is what lets the
        ``AuthorizeStaffTestCase``-inherited ``test_global_staff`` /
        ``test_course_instructor`` (which call ``self.make_request()`` with
        no arguments and expect 200) pass without a real, unmocked
        modulestore/VAL/YouTube-API round trip.
        """
        url = self.get_url("youtube_transcripts_check", course_id=course_id)
        return self.client.get(url, {"data": json.dumps(_REQUEST_DATA)})

    def test_check_success(self):
        """Authenticated course author gets a 200 with the expected response shape."""
        self.login_as_instructor()
        response = self.make_request()  # pylint: disable=no-value-for-parameter

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == _SUCCESS_CHECK_BODY

    @patch(
        "cms.djangoapps.contentstore.rest_api.v1.views.youtube_transcripts.check_transcripts",
        return_value=JsonResponse({"status": "Incoming video data is empty."}, 400),
    )
    def test_check_validation_error(self, mock_check_transcripts):
        """A 400 from the legacy function is surfaced as a standardized 400."""
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_check")
        response = self.client.get(url, {"data": json.dumps(_REQUEST_DATA)})

        mock_check_transcripts.assert_called_once()
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_check_rejects_malformed_data_param(self):
        """
        Malformed ``data`` (not JSON, or JSON that fails the request
        serializer) is rejected with a 400 before the legacy function is
        ever called — this is the real ADR 0025 input-validation layer added
        in front of ``check_transcripts``.
        """
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_check")

        response = self.client.get(url, {"data": "not-json"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self.client.get(url, {"data": json.dumps({"locator": "abc"})})  # missing `videos`
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_check_requires_authentication(self):
        """An unauthenticated request is rejected."""
        response = self.client.get(
            self.get_url("youtube_transcripts_check"), {"data": json.dumps(_REQUEST_DATA)}
        )
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # test_student and test_instructor_in_another_course (both expecting 403)
    # are inherited from AuthorizeStaffTestCase and exercise `make_request()`
    # above directly against the real (unmocked) permission decorator — the
    # `@course_author_access_required` permission check runs and rejects
    # before the legacy function is ever called, so the class-level
    # `check_transcripts` mock on `make_request` is never reached for those
    # two. test_global_staff and test_course_instructor (both expecting 200)
    # are also inherited and rely on that same mock to succeed.

    @patch(
        "cms.djangoapps.contentstore.rest_api.v1.views.youtube_transcripts.check_transcripts",
        side_effect=Http404("No item found for the given locator."),
    )
    def test_check_not_found_for_missing_item(self, mock_check_transcripts):
        """
        A course author hits a real 404 when the referenced video item does
        not exist. ``check_transcripts`` itself never raises ``Http404`` today
        (validation failures become a 400 ``status`` field instead — see the
        module docstring's ADR 0029 note), so this exercises the view's
        ``StandardizedErrorMixin`` ``Http404`` -> DRF 404 path in case a
        future revision of the legacy function (or a library-content path
        through ``_get_item``) raises it directly. This test documents a
        path that cannot currently occur through the real legacy function;
        it is kept as forward-looking coverage of the view's own exception
        handling rather than removed.
        """
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_check")
        response = self.client.get(url, {"data": json.dumps(_REQUEST_DATA)})

        mock_check_transcripts.assert_called_once()
        assert response.status_code == status.HTTP_404_NOT_FOUND


class YoutubeTranscriptUploadViewTest(YoutubeTranscriptsViewSetTestBase):
    """Tests for POST .../youtube_transcripts/{course_id}/upload/"""

    @patch(
        "cms.djangoapps.contentstore.rest_api.v1.views.youtube_transcripts.replace_transcripts",
        return_value=JsonResponse(_SUCCESS_UPLOAD_BODY, 200),
    )
    def make_request(self, mock_replace_transcripts=None, course_id=None):  # pylint: disable=arguments-differ
        """
        Issue the POST request with the legacy ``replace_transcripts``
        mocked to a default 200/success response. See
        ``YoutubeTranscriptCheckViewTest.make_request`` above for why
        ``make_request`` itself carries the ``@patch``.

        ``data`` is sent as a query string parameter (``?data=...``), not a
        POST body field: the legacy function only ever reads
        ``request.GET['data']`` regardless of HTTP verb, and the view's
        ``@extend_schema`` now accurately documents ``data`` as a query
        parameter rather than a JSON request body (see the view's ADR 0025
        finding-3 fix). Sending it as a POST body field instead (as an
        earlier version of this test did) would put it in
        ``request.POST``/``request.data``, which ``request.GET`` never sees
        — that earlier test only passed because the mocked
        ``replace_transcripts`` never actually looked at the request body.
        """
        url = self.get_url("youtube_transcripts_upload", course_id=course_id)
        return self.client.post(f"{url}?{urlencode({'data': json.dumps(_REQUEST_DATA)})}")

    def test_upload_success(self):
        """Authenticated course author gets a 200 with the expected response shape."""
        self.login_as_instructor()
        response = self.make_request()  # pylint: disable=no-value-for-parameter

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == _SUCCESS_UPLOAD_BODY

    @patch(
        "cms.djangoapps.contentstore.rest_api.v1.views.youtube_transcripts.replace_transcripts",
        return_value=JsonResponse({"status": "YouTube ID is required."}, 400),
    )
    def test_upload_validation_error(self, mock_replace_transcripts):
        """A 400 from the legacy function is surfaced as a standardized 400."""
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_upload")
        response = self.client.post(f"{url}?{urlencode({'data': json.dumps(_REQUEST_DATA)})}")

        mock_replace_transcripts.assert_called_once()
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_rejects_malformed_data_param(self):
        """
        Malformed ``data`` is rejected with a 400 before
        ``replace_transcripts`` (and its YouTube download / VAL write /
        modulestore write) is ever called.
        """
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_upload")

        response = self.client.post(f"{url}?{urlencode({'data': 'not-json'})}")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self.client.post(f"{url}?{urlencode({'data': json.dumps({'locator': 'abc'})})}")  # missing `videos`
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_requires_authentication(self):
        """An unauthenticated request is rejected."""
        url = self.get_url("youtube_transcripts_upload")
        response = self.client.post(f"{url}?{urlencode({'data': json.dumps(_REQUEST_DATA)})}")
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    # test_student and test_instructor_in_another_course (both expecting 403)
    # and test_global_staff / test_course_instructor (both expecting 200) are
    # inherited from AuthorizeStaffTestCase — see the note in
    # YoutubeTranscriptCheckViewTest above; the same reasoning applies here
    # against the class-level `replace_transcripts` mock on `make_request`.

    @patch(
        "cms.djangoapps.contentstore.rest_api.v1.views.youtube_transcripts.replace_transcripts",
        side_effect=Http404("No item found for the given locator."),
    )
    def test_upload_not_found_for_missing_item(self, mock_replace_transcripts):
        """
        A course author hits a real 404 when the referenced video item does
        not exist. As with ``check``, ``replace_transcripts`` never actually
        raises ``Http404`` today — this is forward-looking coverage of the
        view's own exception handling, kept intentionally (see the note on
        ``test_check_not_found_for_missing_item`` above).
        """
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_upload")
        response = self.client.post(f"{url}?{urlencode({'data': json.dumps(_REQUEST_DATA)})}")

        mock_replace_transcripts.assert_called_once()
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_upload_rejects_get(self):
        """GET is no longer accepted on the upload endpoint (ADR 0030 fix)."""
        self.login_as_instructor()
        url = self.get_url("youtube_transcripts_upload")
        response = self.client.get(url, {"data": json.dumps(_REQUEST_DATA)})
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
