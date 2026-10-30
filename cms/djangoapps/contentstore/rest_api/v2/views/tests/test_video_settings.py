"""Tests for the v2 course video settings endpoint."""

import itertools
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import ddt
from ccx_keys.locator import CCXLocator
from django.db import connection
from django.db.models import signals as model_signals
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles import WaffleSwitch
from edx_toggles.toggles.testutils import override_waffle_switch
from edxval.api import (
    create_or_update_transcript_preferences,
    create_profile,
    create_video,
    get_3rd_party_transcription_plans,
    get_transcript_credentials_state_for_org,
    get_transcript_preferences,
)
from edxval.models import Video
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_STAFF
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.videos import CourseVideosView, VideoDownloadView, VideoUsageView
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import (
    PASSWORD,
    SignalRecorder,
    generate_cms_schema,
    logged_in_client,
    writes,
)
from cms.djangoapps.contentstore.rest_api.v2.views.video_settings import CourseVideoSettingsViewSet
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import get_course_video_settings_context, reverse_course_url
from cms.djangoapps.contentstore.video_storage_handlers import send_video_status_update
from common.djangoapps.student.roles import CourseInstructorRole, CourseLimitedStaffRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.tests.factories import CourseFactory

URL_NAME = "authoring_v2:course_video_settings"
HANDLERS = "cms.djangoapps.contentstore.video_storage_handlers"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:course_videos"
MISSING_COURSE_KEY = "course-v1:NoSuch+Course+Run"
TRANSCRIPTS_FLAG = "openedx.core.djangoapps.video_config.models.VideoTranscriptEnabledFlag.feature_enabled"
XPERT_FLAG = "openedx.core.djangoapps.video_config.toggles.XPERT_TRANSLATIONS_UI.is_enabled"
IMAGE_UPLOAD_SWITCH = WaffleSwitch(  # pylint: disable=toggle-missing-annotation
    "videos.video_image_upload_enabled", __name__
)
SETTINGS_KEYS = {
    "image_upload_url", "video_handler_url", "encodings_download_url", "default_video_image_url",
    "concurrent_upload_limit", "video_supported_file_formats", "video_upload_max_file_size",
    "video_image_settings", "is_video_transcript_enabled", "is_ai_translations_enabled",
    "active_transcript_preferences", "transcript_credentials", "transcript_available_languages",
    "video_transcript_settings",
}


def typed(value):
    """Return ``value`` with every leaf paired with its JSON type, so ``1`` and ``1.0`` differ."""
    if isinstance(value, dict):
        return {key: typed(item) for key, item in value.items()}
    if isinstance(value, list):
        return [typed(item) for item in value]
    return (type(value).__name__, value)


def body(response):
    """Return the decoded JSON body of ``response``."""
    return json.loads(response.content)


def legacy_as_new(legacy):
    """
    Return the legacy body with each declared difference applied, asserting each one occurs.

    The new representation drops the always-empty ``pagination_context``,
    publishes the maximum upload size as an integer, spells the transcript
    download format key correctly, and never embeds the uploaded videos.
    """
    expected = dict(legacy)
    assert "previous_uploads" in expected
    expected.pop("previous_uploads")
    assert expected.pop("pagination_context") == {}
    assert expected["video_upload_max_file_size"] == "5"
    expected["video_upload_max_file_size"] = 5
    transcript_settings = dict(expected["video_transcript_settings"])
    assert transcript_settings["trancript_download_file_format"] == "srt"
    transcript_settings["transcript_download_file_format"] = transcript_settings.pop("trancript_download_file_format")
    expected["video_transcript_settings"] = transcript_settings
    return expected


class VideoSettingsTestBase(CourseTestCase):
    """A course, its callers, and helpers for creating its uploaded videos."""

    def setUp(self):
        super().setUp()
        self.now = datetime.now(UTC)
        self.other_course = CourseFactory.create(org="OtherX", course="Other", run="Run")

        self.no_role_user = UserFactory(password=PASSWORD)
        self.course_staff = UserFactory(password=PASSWORD)
        CourseStaffRole(self.course.id).add_users(self.course_staff)
        self.course_instructor = UserFactory(password=PASSWORD)
        CourseInstructorRole(self.course.id).add_users(self.course_instructor)
        self.limited_staff = UserFactory(password=PASSWORD)
        CourseLimitedStaffRole(self.course.id).add_users(self.limited_staff)
        self.other_course_staff = UserFactory(password=PASSWORD)
        CourseStaffRole(self.other_course.id).add_users(self.other_course_staff)
        self.other_course_instructor = UserFactory(password=PASSWORD)
        CourseInstructorRole(self.other_course.id).add_users(self.other_course_instructor)
        self.global_staff = UserFactory(is_staff=True, password=PASSWORD)
        self.superuser = UserFactory(is_superuser=True, password=PASSWORD)
        # Role checks create the authorization engine's cache-version row on
        # first use; create it here so request-level measurements exclude it.
        PolicyCacheControl.get()
        create_profile("desktop_mp4")
        create_profile("mobile_low")

    def add_video(self, edx_video_id, status, hours_old=1, course=None, image=None, encodings=(), **fields):
        """Create a VAL video attached to ``course`` (default: this course), ``hours_old`` hours ago."""
        course_id = str((course or self.course).id)
        create_video({
            "edx_video_id": edx_video_id,
            "client_video_id": f"{edx_video_id}.mp4",
            "duration": 12.5,
            "status": status,
            "courses": [{course_id: image}] if image else [course_id],
            "encoded_videos": list(encodings),
            **fields,
        })
        Video.objects.filter(edx_video_id=edx_video_id).update(created=self.now - timedelta(hours=hours_old))

    def url(self, course_key=None):
        return reverse(URL_NAME, kwargs={"course_key": str(course_key or self.course.id)})

    def legacy_url(self, course_key=None):
        return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key or self.course.id)})

    def get(self, user, course_key=None, method="get", **params):
        client = logged_in_client(user) if user is not None else APIClient()
        return getattr(client, method)(self.url(course_key), params)

    def get_legacy(self, user, course_key=None):
        client = logged_in_client(user) if user is not None else APIClient()
        return client.get(self.legacy_url(course_key))


@ddt.ddt
class CourseVideoSettingsAccessTest(VideoSettingsTestBase):
    """Who may read a course's video settings."""

    @ddt.data("course_staff", "course_instructor", "global_staff", "superuser")
    def test_allowed_callers_are_served(self, caller):
        for method in ("get", "head"):
            response = self.get(getattr(self, caller), method=method)
            assert response.status_code == 200, (caller, method)
        assert set(self.get(getattr(self, caller)).data) == SETTINGS_KEYS

    def test_anonymous_is_refused_with_a_challenge(self):
        response = self.get(None)
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert response["WWW-Authenticate"] == 'JWT realm="api"'

    @ddt.data("no_role_user", "limited_staff", "other_course_staff", "other_course_instructor")
    def test_callers_without_studio_access_to_this_course_are_refused(self, caller):
        for method in ("get", "head"):
            response = self.get(getattr(self, caller), method=method)
            assert response.status_code == 403, (caller, method)
        assert_error_envelope(self.get(getattr(self, caller)), expected_status=403, expected_type_slug="authz")
        assert self.get_legacy(getattr(self, caller)).status_code == 403

    def test_a_missing_course_is_not_found_for_a_privileged_caller(self):
        response = self.get(self.global_staff, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert response.data["detail"] == "The requested course does not exist."

    def test_a_missing_course_is_refused_not_revealed_to_a_caller_without_access(self):
        response = self.get(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")
        legacy = self.get_legacy(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert legacy.status_code == 404
        assert legacy.data["error_code"] == "course_does_not_exist"

    def test_a_course_missing_from_the_modulestore_is_not_found(self):
        client = Client(raise_request_exception=False)
        assert client.login(username=self.course_staff.username, password=PASSWORD)
        with patch("xmodule.modulestore.mixed.MixedModuleStore.get_course", return_value=None):
            response = self.get(self.course_staff)
            legacy = client.get(self.legacy_url())
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert legacy.status_code == 500

    def test_a_ccx_course_is_refused_even_to_global_staff(self):
        ccx_key = CCXLocator.from_course_locator(self.course.id, "1")
        assert_error_envelope(self.get(self.global_staff, course_key=ccx_key), expected_status=403,
                              expected_type_slug="authz")

    def test_an_inactive_course_author_is_authenticated_then_refused_as_on_legacy(self):
        inactive = UserFactory(is_active=False, password=PASSWORD)
        CourseStaffRole(self.course.id).add_users(inactive)
        assert_error_envelope(self.get(inactive), expected_status=403, expected_type_slug="authz")
        assert self.get_legacy(inactive).status_code == 403

    def test_a_jwt_caller_is_served(self):
        header = f"JWT {create_jwt_for_user(self.course_staff)}"
        assert APIClient().get(self.url(), HTTP_AUTHORIZATION=header).status_code == 200

    def test_a_bearer_token_is_not_accepted_where_legacy_accepts_it(self):
        token = AccessTokenFactory(user=self.course_staff, application=ApplicationFactory()).token
        response = APIClient().get(self.url(), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert APIClient().get(self.legacy_url(), HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200

    def test_uploaded_videos_are_never_embedded(self):
        """Legacy's previous_uploads is not offered, whatever the query string asks for."""
        self.add_video("encoded", "file_complete")
        for params in ({}, {"view": "full"}):
            assert set(self.get(self.course_staff, **params).data) == SETTINGS_KEYS, params


class CourseVideoSettingsAuthzEnabledTest(CourseAuthoringAuthzTestMixin, VideoSettingsTestBase):
    """Access when course-authoring policy is enabled: the same callers as legacy, and no others."""

    def test_a_policy_auditor_is_refused_as_on_legacy(self):
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, COURSE_AUDITOR.external_key, self.course.id)
        assert_error_envelope(self.get(user), expected_status=403, expected_type_slug="authz")
        assert self.get_legacy(user).status_code == 403

    def test_a_policy_staff_role_is_served_as_on_legacy(self):
        """Studio read access resolves the course staff role through its policy assignment."""
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, COURSE_STAFF.external_key, self.course.id)
        assert self.get(user).status_code == 200
        assert self.get_legacy(user).status_code == 200

    def test_a_legacy_course_role_is_still_served(self):
        assert self.get(self.course_staff).status_code == 200
        assert self.get_legacy(self.course_staff).status_code == 200


@ddt.ddt
class CourseVideoSettingsParityTest(VideoSettingsTestBase):
    """The settings match legacy's, apart from the declared differences, under every flag."""

    def setUp(self):
        super().setUp()
        create_or_update_transcript_preferences(
            str(self.course.id), provider="Cielo24", cielo24_fidelity="PROFESSIONAL",
            cielo24_turnaround="PRIORITY", preferred_languages=["en"], video_source_language="en",
        )
        self.add_video("encoded", "file_complete")

    @ddt.data(*itertools.product((False, True), (False, True), (False, True)))
    @ddt.unpack
    def test_settings_match_legacy(self, transcripts, xpert, image_upload):
        with patch(TRANSCRIPTS_FLAG, return_value=transcripts), patch(XPERT_FLAG, return_value=xpert), \
                override_waffle_switch(IMAGE_UPLOAD_SWITCH, image_upload):
            full = body(self.get(self.course_staff))
            legacy = body(self.get_legacy(self.course_staff))

        assert "pagination_context" not in full
        assert "trancript_download_file_format" not in full["video_transcript_settings"]
        assert typed(full) == typed(legacy_as_new(legacy))

        assert full["is_video_transcript_enabled"] is transcripts
        assert full["is_ai_translations_enabled"] is xpert
        assert full["video_image_settings"]["video_image_upload_enabled"] is image_upload
        transcript_settings = full["video_transcript_settings"]
        if transcripts:
            assert full["active_transcript_preferences"] == get_transcript_preferences(str(self.course.id))
            assert full["transcript_credentials"] == get_transcript_credentials_state_for_org(self.course.id.org)
            assert transcript_settings["transcription_plans"] == get_3rd_party_transcription_plans()
            assert transcript_settings["transcript_preferences_handler_url"] == reverse_course_url(
                "transcript_preferences_handler", str(self.course.id)
            )
        else:
            assert full["active_transcript_preferences"] is None
            assert full["transcript_credentials"] is None
            assert transcript_settings["transcription_plans"] is None
            assert transcript_settings["transcript_preferences_handler_url"] is None

    def test_the_settings_come_from_the_same_helper_as_legacy(self):
        video_settings = get_course_video_settings_context(self.course)
        assert set(video_settings) == SETTINGS_KEYS
        assert video_settings["video_upload_max_file_size"] == 5


class CourseVideoSettingsReadOnlyTest(VideoSettingsTestBase):
    """Reading the settings changes nothing, even when an upload has gone stale."""

    def setUp(self):
        super().setUp()
        self.add_video("stale", "upload", hours_old=30)
        self.add_video("fresh", "upload", hours_old=2)
        self.saved = []
        model_signals.post_save.connect(self._record_save, sender=Video)
        self.addCleanup(model_signals.post_save.disconnect, self._record_save, sender=Video)

    def _record_save(self, sender, instance, **kwargs):  # pylint: disable=unused-argument
        self.saved.append(instance.edx_video_id)

    def _observe(self, client, url, method="get", **params):
        """Make one request; return it with the writes, signals and status-update calls it caused."""
        send_update = patch(f"{HANDLERS}.send_video_status_update", wraps=send_video_status_update)
        with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals, send_update as sent:
            response = getattr(client, method)(url, params)
        return response, writes(queries.captured_queries), signals.unexpected(), sent.call_count

    def test_get_and_head_write_nothing(self):
        client = logged_in_client(self.course_staff)
        for method in ("get", "head"):
            response, statements, signals, sent = self._observe(client, self.url(), method)
            assert response.status_code == 200, method
            assert statements == [], method
            assert signals == [], method
            assert sent == 0, method
            assert not self.saved, method
            assert Video.objects.get(edx_video_id="stale").status == "upload", method

    def test_a_jwt_request_writes_nothing(self):
        header = f"JWT {create_jwt_for_user(self.course_staff)}"
        with CaptureQueriesContext(connection) as queries:
            response = APIClient().get(self.url(), HTTP_AUTHORIZATION=header)
        assert response.status_code == 200
        assert writes(queries.captured_queries) == []
        assert Video.objects.get(edx_video_id="stale").status == "upload"

    def test_the_legacy_route_still_marks_the_stale_upload_failed(self):
        """The same observation made on the legacy route sees its write, so the harness above can fail."""
        response, statements, signals, sent = self._observe(logged_in_client(self.course_staff), self.legacy_url())
        assert response.status_code == 200
        assert Video.objects.get(edx_video_id="stale").status == "upload_failed"
        assert Video.objects.get(edx_video_id="fresh").status == "upload"
        assert any(sql.startswith('UPDATE "edxval_video"') for sql in statements)
        assert any(signal is model_signals.post_save for _, signal in signals)
        assert sent == 1
        assert self.saved == ["stale"]

    @override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=True)
    def test_the_studio_video_listing_still_marks_the_stale_upload_failed(self):
        self.course.video_upload_pipeline = {"course_video_upload_token": "course-token"}
        self.save_course()
        response = self.client.get(reverse_course_url("videos_handler", self.course.id), HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert Video.objects.get(edx_video_id="stale").status == "upload_failed"


class CourseVideoSettingsQueryCountTest(VideoSettingsTestBase):
    """The settings cost a fixed number of queries, however many videos the course has."""

    @override_settings(ENABLE_VIDEO_UPLOAD_PIPELINE=True)
    def test_the_query_count_does_not_grow_with_uploads(self):
        client = logged_in_client(self.course_staff)
        client.get(self.url())
        with self.assertNumQueries(13, using="default"):
            assert client.get(self.url()).status_code == 200
        for index in range(3):
            self.add_video(f"video-{index}", "file_complete", hours_old=index + 1)
        with self.assertNumQueries(13, using="default"):
            assert client.get(self.url()).status_code == 200


class CourseVideoSettingsUrlTest(VideoSettingsTestBase):
    """The address, its name, and how malformed requests are answered."""

    def test_reverse_literal(self):
        key = "course-v1:edX+Demo+2026"
        assert reverse(URL_NAME, kwargs={"course_key": key}) == f"/api/authoring/v2/courses/{key}/video_settings/"

    def test_each_address_resolves_to_its_own_view(self):
        assert resolve(self.url()).func.cls is CourseVideoSettingsViewSet
        assert resolve(self.legacy_url()).func.view_class is CourseVideosView
        assert self.legacy_url() == f"/api/contentstore/v1/videos/{self.course.id}"

    def test_malformed_and_deprecated_keys_do_not_route(self):
        client = logged_in_client(self.global_staff)
        for key in ("not-a-key", "edX/DemoX/Demo"):
            response = client.get(f"/api/authoring/v2/courses/{key}/video_settings/")
            assert response.status_code == 404, key
            # The service's HTML not-found page answers here: no JSON catch-all
            # exists under this mount.
            assert response["Content-Type"].startswith("text/html"), key

    def test_the_legacy_route_still_serves_a_deprecated_key(self):
        assert resolve("/api/contentstore/v1/videos/edX/DemoX/Demo").func.view_class is CourseVideosView

    def test_a_slashless_address_redirects(self):
        response = logged_in_client(self.global_staff).get(self.url().rstrip("/"))
        assert response.status_code == 301
        assert response["Location"] == self.url()

    def test_writes_are_not_allowed(self):
        client = logged_in_client(self.global_staff)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(self.url())
            assert_error_envelope(response, expected_status=405)
            assert response["Content-Type"] == "application/json"

    def test_an_html_only_client_is_answered_not_acceptable_in_json(self):
        response = logged_in_client(self.global_staff).get(self.url(), HTTP_ACCEPT="text/html")
        assert_error_envelope(response, expected_status=406)
        assert response["Content-Type"] == "application/json"


VIDEO_SETTINGS_PATH = "/api/authoring/v2/courses/{course_key}/video_settings/"
LEGACY_VIDEOS_PATH = "/api/contentstore/v1/videos/{course_id}"


class CourseVideoSettingsSchemaTest(CourseTestCase):
    """What the published schema says about the endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = generate_cms_schema()
        cls.components = cls.schema["components"]["schemas"]

    def _operation(self):
        return self.schema["paths"][VIDEO_SETTINGS_PATH]["get"]

    def test_the_path_is_published_at_its_full_address(self):
        assert set(self.schema["paths"][VIDEO_SETTINGS_PATH]) == {"get"}
        assert self._operation()["tags"] == ["openedx-platform-sdk"]

    def test_the_success_body_is_the_settings(self):
        success = self._operation()["responses"]["200"]["content"]["application/json"]["schema"]
        assert success == {"$ref": "#/components/schemas/CourseVideoSettings"}
        assert set(self.components["CourseVideoSettings"]["properties"]) == SETTINGS_KEYS

    def test_only_the_course_key_is_a_parameter(self):
        assert [p["name"] for p in self._operation()["parameters"]] == ["course_key"]

    def test_field_types_match_what_is_returned(self):
        settings = self.components["CourseVideoSettings"]["properties"]
        assert settings["video_upload_max_file_size"]["type"] == "integer"
        assert settings["transcript_credentials"]["nullable"] is True
        transcript = self.components["VideoSettingsTranscript"]["properties"]
        assert "transcript_download_file_format" in transcript
        assert "trancript_download_file_format" not in transcript

    def test_every_error_references_the_shared_envelope(self):
        responses = self._operation()["responses"]
        assert set(responses) == {"200", "401", "403", "404"}
        for code in ("401", "403", "404"):
            assert responses[code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }, code

    def test_the_legacy_video_operations_are_unchanged(self):
        operation = self.schema["paths"][LEGACY_VIDEOS_PATH]["get"]
        assert operation["operationId"] == "v1_videos_retrieve"
        assert "deprecated" not in operation
        for path, view in self._legacy_sibling_paths():
            for operation in self.schema["paths"][path].values():
                assert "deprecated" not in operation, (path, view)

    def _legacy_sibling_paths(self):
        """Return the legacy usage and download paths published beside the legacy videos path."""
        paths = [(p, v) for p in self.schema["paths"] for v in (VideoUsageView, VideoDownloadView)
                 if p.startswith("/api/contentstore/v1/videos/{course_id}/") and
                 p.endswith("/usage" if v is VideoUsageView else "/download")]
        assert len(paths) == 2
        return paths
