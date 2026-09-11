"""
API Views for YouTube transcript check/upload — v1.

Standardizes the v0 ``YoutubeTranscriptCheckView`` + ``YoutubeTranscriptUploadView``
pair (``cms/djangoapps/contentstore/rest_api/v0/views/transcripts.py``) into a
single ``YoutubeTranscriptsViewSet`` applying the FC-0118 ADRs. The v0 views,
serializers, and URLs are untouched (ADR 0037) — this is a new, additive v1
surface for the same resource. Only the two YouTube transcript endpoints are
migrated here; the sibling ``TranscriptView`` (``/video_transcripts/...``) is
a different resource and is out of scope for this change.

ADR compliance:
  * ADR 0025 - ``serializer_class`` is declared as a class attribute (the
    ADR 0025 checklist requirement — schema generation and any
    ``getattr(view, 'serializer_class')`` caller depend on it existing even
    when a view overrides per-action selection), and additionally
    per-action via ``get_serializer_class`` (the two actions have different
    response shapes), plus a ``get_serializer`` helper since plain
    ``viewsets.ViewSet`` has none. Response bodies are now actually built
    through ``YoutubeTranscriptCheckSerializer`` / ``YoutubeTranscriptUploadSerializer``
    instead of being declared-but-unused as in v0. Request bodies are also now
    actually validated: both actions parse the ``data`` query parameter
    themselves and run it through ``YoutubeTranscriptCheckRequestSerializer`` /
    ``YoutubeTranscriptUploadRequestSerializer`` via ``is_valid(raise_exception=True)``
    *before* calling the legacy function, so a malformed ``data`` payload now
    gets a real structured 400 instead of only ever validating a response
    body. (A prior version of this view declared the request serializers only
    inside ``@extend_schema`` and never instantiated them — that was a real
    ADR 0025 violation, caught in review; see the ``check``/``upload``
    docstrings for the fix and reasoning.)
  * ADR 0026 - explicit ``authentication_classes`` + ``permission_classes``
    declared on the viewset (no reliance on project defaults).
  * ADR 0027 - ``drf_spectacular`` ``@extend_schema`` on both actions
    (v0 had no schema annotation at all).
  * ADR 0028 - both endpoints act on the same resource (a course's YouTube
    transcript state) and neither is a real ORM-backed model, so this is a
    plain ``viewsets.ViewSet`` (not ``ModelViewSet``), with ``check`` and
    ``upload`` as its two action methods. Routing is wired via explicit
    ``re_path`` entries calling ``YoutubeTranscriptsViewSet.as_view({'get':
    'check'})`` / ``as_view({'post': 'upload'})`` in ``v1/urls.py`` rather
    than ``DefaultRouter`` dynamic ``@action`` discovery — the course_id path
    parameter here is not a router "detail" lookup on this resource's own
    identity (the viewset has no ``list``/``retrieve``/collection of its
    own), and explicit registration keeps the URL shape unambiguous and
    independently reviewable while the view class itself remains a standard
    DRF ``ViewSet``, satisfying the ADR's "migrate away from ad-hoc
    ``APIView``/legacy dispatch" intent. Query-count discipline: both actions
    delegate to the existing ``check_transcripts`` / ``replace_transcripts``
    legacy functions unchanged, so this migration introduces no new N+1s. No
    ``select_related``/``prefetch_related`` opportunity exists here — the
    data path is modulestore/contentstore/VAL/YouTube-API calls, not a
    Django ORM queryset, so that MUST doesn't apply to this resource (see
    Enrollment v2's ``viewsets.ViewSet`` precedent for a non-ORM data path).
  * ADR 0029 - ``StandardizedErrorMixin`` provides the standardized error
    envelope. The legacy functions return a raw ``JsonResponse`` with a
    ``status`` field carrying the error message on failure (not the DRF
    standard ``developer_message`` shape) — this view parses that JsonResponse
    and re-raises as a DRF ``ValidationError`` so error responses go through
    the standardized envelope. This is a deliberate shape change on the error
    path only; the success-path response body is unchanged (see docstring on
    each action). The ``ValidationError`` is raised with the *full* legacy
    error body merged with an explicit ``error_code`` key (e.g.
    ``youtube_transcript_check_failed`` / ``youtube_transcript_upload_failed``)
    — an earlier version of this view raised ``ValidationError`` with only
    the truncated ``status`` string, silently discarding the rest of the
    legacy ``transcripts_presence`` dict (``html5_local``, ``youtube_diff``,
    etc., which are also present on the error path since ``error_response()``
    only overwrites the ``status`` key on the full dict) and had no
    ``error_code`` at all, inconsistent with the 403 path's
    ``error_code='user_permissions'``. Fixed here as a low-risk change (it
    only affects what is included in an already-thrown exception's detail).
  * ADR 0030 - ``check`` remains ``GET`` (already idempotent - read-only
    status probe, no writes). ``upload`` is changed from ``GET`` to ``POST``:
    the v0 endpoint used GET for an operation that downloads transcripts from
    YouTube and writes to VAL + modulestore, which is exactly the GET-mutates
    violation this ADR targets. v1 fixes it: the operation is now a POST. The
    ``/upload`` URL segment is kept (arguably a verb - see ADR 0038 note
    below) because this is a real, non-resource-shaped operation ("perform an
    upload/replace"), not a CRUD create of an addressable sub-resource; POST
    to a stable noun path is the ADR 0038 rule 10 escape hatch for exactly
    this case, and it must be flagged as such in the OpenAPI description,
    which is done below.
  * ADR 0031 - considered merging check (read) and upload (write) into one
    action selected by a ``mode``/``action`` field, per the ADR's merge test:
    "share the same resource domain and differ only in the operation
    applied". Decision: kept as **two separate actions** on one viewset
    rather than fused into a single endpoint. Reasoning: the ADR's merge
    target is endpoints that differ only in *operation* on an otherwise
    identical request/response contract (e.g. generate/regenerate/toggle a
    certificate, all POST, all returning a task handle). Check and upload
    differ in HTTP semantics (GET vs POST), side effects (none vs
    YouTube-download + VAL-write + modulestore-write), and response shape
    (``html5_local``/``youtube_diff``/... vs ``edx_video_id``/``status``) -
    merging them behind one ``mode`` field would force a GET-shaped read and
    a POST-shaped write through one verb-agnostic entry point, which is a
    worse fit than the ADR's own certificate-task example (three POSTs that
    already shared one shape). They *do* still get the boilerplate-sharing
    benefit of ADR 0031 by living on the same ``YoutubeTranscriptsViewSet``
    with one class-level ``authentication_classes``/``permission_classes``
    declaration, without forcing an artificial shared contract. Both actions
    keep their own coarse (``@course_author_access_required`` on the URL's
    ``course_id``) plus specific (``has_course_author_access`` inside the
    legacy ``_get_item`` against the *item's actual* course_key - relevant
    for library content) permission layers, per the ADR's "do not flatten
    authorization" requirement.
  * ADR 0032 - out of scope. Neither action returns a list/collection.
  * ADR 0033 - out of scope. Neither action takes filter/sort parameters.
  * ADR 0034 - already compliant. ``authentication_classes`` is
    ``(JwtAuthentication, SessionAuthenticationAllowInactiveUser)`` - no
    ``BearerAuthentication``/``BearerAuthenticationAllowInactiveUser`` to
    remove (v0 carried ``BearerAuthenticationAllowInactiveUser`` via
    ``@view_auth_classes()``; that is dropped here per the deprecation
    policy). ``SessionAuthenticationAllowInactiveUser`` is kept explicitly so
    inactive Studio authors can still reach the endpoint.
  * ADR 0035 - out of scope. Not an MFE configuration endpoint.
  * ADR 0036 - out of scope. Both response bodies are flat, small, fixed-key
    objects (9 and 2 top-level fields respectively) with no nested
    sub-objects or tree shape to collapse.
  * ADR 0037 - this is a new v1 surface. The v0
    ``YoutubeTranscriptCheckView``/``YoutubeTranscriptUploadView``, their
    serializers, and their URL entries are untouched and continue to serve
    ``GET`` on the old paths exactly as before.
  * ADR 0038 - URLs are
    ``/api/contentstore/v1/youtube_transcripts/{course_id}/check/`` (GET) and
    ``/api/contentstore/v1/youtube_transcripts/{course_id}/upload/`` (POST),
    replacing v0's ``.../youtube_transcripts/{course_id}/check?`` (optional
    trailing ``?`` regex anti-pattern flagged by the survey). One level of
    nesting (course -> check|upload) is used because neither ``check`` nor
    ``upload`` is an independently addressable resource with its own opaque
    key - both only make sense scoped to a course, satisfying rule 8. Trailing
    slash is now mandatory (rule 6). ``check``/``upload`` are technically verb
    segments (rule 10); they are kept because this is the ADR-0031-considered
    "genuine non-resource operation" case the rule explicitly carves out, and
    each is marked as such via its ``@extend_schema`` description. ``api_name``
    stays ``contentstore`` here (not renamed to ``authoring``) to stay
    consistent with the rest of this same v1 mount (``xblock``, etc.) - a
    platform-wide ``contentstore`` -> ``authoring`` rename is a bigger,
    cross-viewset migration out of scope for this issue.
"""
import json
import logging

from django.http import JsonResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from cms.djangoapps.contentstore.api.views.utils import course_author_access_required
from cms.djangoapps.contentstore.rest_api.v1.serializers.youtube_transcripts import (
    YoutubeTranscriptCheckRequestSerializer,
    YoutubeTranscriptCheckSerializer,
    YoutubeTranscriptUploadRequestSerializer,
    YoutubeTranscriptUploadSerializer,
)
from cms.djangoapps.contentstore.views.transcripts_ajax import check_transcripts, replace_transcripts

log = logging.getLogger(__name__)


_COURSE_ID_PARAMETER = OpenApiParameter(
    name="course_id",
    description="Course key string (e.g. course-v1:org+course+run) that owns the video.",
    required=True,
    type=str,
    location=OpenApiParameter.PATH,
)

_COMMON_ERROR_RESPONSES = {
    401: OpenApiResponse(description="The requester is not authenticated."),
    403: OpenApiResponse(description="The requester does not have course author permissions."),
    404: OpenApiResponse(description="The referenced video item does not exist."),
}

_DATA_QUERY_PARAMETER_DESCRIPTION = (
    "JSON-encoded object (as a query string value, not a request body — the "
    "underlying legacy implementation reads this from the query string "
    "regardless of HTTP verb) with keys `locator` (usage key string of the "
    "video xblock) and `videos` (list of video descriptors, e.g. "
    "[{'type': 'youtube', 'video': 'abc123', 'mode': 'youtube'}])."
)

_DATA_QUERY_PARAMETER = OpenApiParameter(
    name="data",
    description=_DATA_QUERY_PARAMETER_DESCRIPTION,
    required=True,
    type=str,
    location=OpenApiParameter.QUERY,
)


def _parse_legacy_json_response(response: JsonResponse) -> tuple[dict, int]:
    """
    Parse a legacy ``JsonResponse`` (returned by ``check_transcripts`` /
    ``replace_transcripts``) into a ``(body_dict, status_code)`` pair.

    Both legacy functions return a raw ``JsonResponse`` rather than a DRF
    ``Response`` — this is the seam where that gets bridged into the
    standardized DRF response path.
    """
    body = json.loads(response.content.decode("utf-8") or "{}")
    return body, response.status_code


def _validate_incoming_data(request: Request, request_serializer_class) -> None:
    """
    Parse and validate the ``data`` query parameter both legacy functions
    read (``check_transcripts`` / ``replace_transcripts`` both call
    ``request.GET.get('data', '{}')`` internally, regardless of HTTP verb —
    see ``transcripts_ajax.py:518,559``).

    This is the real ADR 0025 input-validation layer: it runs *before* the
    legacy function so a malformed ``data`` payload gets a structured 400
    (with field-level errors) before any legacy side effects (modulestore
    reads, YouTube API calls, VAL/modulestore writes) run. It is
    intentionally a validation layer bolted on in front of the legacy
    function, not a replacement for it — the legacy function still parses
    and validates the same query param again internally. That duplication
    is accepted here: it is not a behavior change, and reimplementing
    ``check_transcripts``/``replace_transcripts``'s own parsing to avoid it
    is out of scope (touching legacy business logic is explicitly what this
    migration avoids).

    Malformed JSON in ``data`` itself (not just a schema mismatch once
    parsed) is also surfaced as a DRF ``ValidationError`` here, rather than
    falling through to the legacy function's own "Incoming video data is
    empty."/500 handling of bad JSON.
    """
    raw_data = request.GET.get("data", "{}")
    try:
        parsed = json.loads(raw_data)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"data": ["Must be a JSON object string."]}) from exc
    if not isinstance(parsed, dict):
        raise ValidationError({"data": ["Must be a JSON object."]})
    serializer = request_serializer_class(data=parsed)
    serializer.is_valid(raise_exception=True)


@extend_schema(tags=["openedx-platform-sdk"])
class YoutubeTranscriptsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    ViewSet for YouTube transcript check/upload operations (v1 — ADR 0028).

    URLs (explicitly registered in ``v1/urls.py``, see module docstring)::

        GET  /api/contentstore/v1/youtube_transcripts/{course_id}/check/   → check
        POST /api/contentstore/v1/youtube_transcripts/{course_id}/upload/  → upload

    Supersedes the v0 ``YoutubeTranscriptCheckView`` (GET .../check) and
    ``YoutubeTranscriptUploadView`` (GET .../upload) — both left untouched at
    ``/api/contentstore/v0/youtube_transcripts/{course_id}/...`` (ADR 0037).

    See the module docstring for the full per-ADR compliance summary,
    including the ADR 0031 merge analysis (kept as two actions, not merged)
    and the ADR 0030 GET→POST fix for ``upload``.
    """

    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)

    # ADR 0025: a plain ``viewsets.ViewSet`` has no ``serializer_class``
    # machinery of its own. This class attribute is the declared default (the
    # ADR 0025 checklist item — schema generation and any
    # ``getattr(view, 'serializer_class')`` caller depend on it existing),
    # while ``get_serializer_class`` below overrides it per-action since
    # ``check`` and ``upload`` have different response shapes.
    serializer_class = YoutubeTranscriptCheckSerializer

    def get_serializer_class(self):
        """Return the response serializer for the current action."""
        if self.action == "upload":
            return YoutubeTranscriptUploadSerializer
        return YoutubeTranscriptCheckSerializer

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the action-appropriate serializer class."""
        return self.get_serializer_class()(*args, **kwargs)

    @extend_schema(
        summary="Check YouTube transcript availability",
        description=(
            "Read-only status check: reports whether local/HTML5 and YouTube "
            "transcripts exist and whether they differ, for the video "
            "identified in the `data` query parameter. Performs no writes. "
            "`check` is a verb segment, not a resource CRUD noun — it is the "
            "ADR 0038 rule 10 'genuine non-resource operation' exception, "
            "same as `upload` below."
        ),
        parameters=[_COURSE_ID_PARAMETER, _DATA_QUERY_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=YoutubeTranscriptCheckSerializer,
                description="Transcript presence/status report.",
            ),
            400: OpenApiResponse(description="The `data` query parameter is missing or invalid."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @course_author_access_required
    def check(self, request: Request, course_key):
        """
        Get the status of YouTube transcripts for a given video.

        **Example Request**

            GET /api/contentstore/v1/youtube_transcripts/{course_id}/check/?data=%7B...%7D

        Coarse permission check: ``@course_author_access_required`` verifies
        the caller has author access to ``course_id`` (the URL-level course).
        A specific permission check also runs inside the legacy
        ``check_transcripts`` → ``_get_item`` call, against the *actual*
        course_key of the referenced item (relevant when the item lives in a
        content library rather than the URL's course) — both checks are
        preserved per ADR 0031.

        Input validation (ADR 0025): the ``data`` query parameter (the only
        way the legacy function accepts input — see
        ``transcripts_ajax.py:518``) is parsed as JSON and validated through
        ``YoutubeTranscriptCheckRequestSerializer`` *before*
        ``check_transcripts`` is called, so malformed input gets a real 400
        with field errors instead of the legacy "Incoming video data is
        empty." string or a 500. This replaces an earlier version of this
        view where the request serializer was declared only for
        ``@extend_schema`` and never actually run — caught in review as an
        ADR 0025 violation, since a client following the generated schema
        (which previously advertised a JSON POST body) would get a spurious
        400 either way. The schema above now accurately documents ``data`` as
        a query parameter, matching what the legacy function actually reads,
        rather than describing an unenforced request body shape.

        Delegates to the existing ``check_transcripts()`` legacy function
        unchanged (same modulestore/contentstore/VAL/YouTube-API calls as
        v0 — no new queries introduced). Its ``JsonResponse`` is parsed and
        re-wrapped: a 200 body is validated and re-serialized through
        ``YoutubeTranscriptCheckSerializer`` for a standardized response type
        parity with the documented v0 response shape (``html5_local``,
        ``html5_equal``, ``is_youtube_mode``, ``youtube_local``,
        ``youtube_server``, ``youtube_diff``, ``current_item_subs``,
        ``status``, ``command``); a non-2xx body is raised as a DRF
        ``ValidationError`` carrying the *full* legacy body (not just the
        truncated ``status`` message — the legacy body's other keys, e.g.
        ``html5_local``/``youtube_diff``, are also present and were
        previously discarded) plus an explicit ``error_code`` key, so it
        flows through ``StandardizedErrorMixin`` instead of the legacy
        ad-hoc envelope without losing information (ADR 0029).
        """
        _validate_incoming_data(request, YoutubeTranscriptCheckRequestSerializer)
        legacy_response = check_transcripts(request)
        body, status_code = _parse_legacy_json_response(legacy_response)
        if status_code >= 400:
            raise ValidationError({**body, "error_code": "youtube_transcript_check_failed"})
        serializer = self.get_serializer(data=body)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status_code)

    @extend_schema(
        summary="Upload/replace YouTube transcripts",
        description=(
            "Downloads the transcript from YouTube for the video identified "
            "in the `data` query parameter and replaces the existing edX "
            "transcript(s) in VAL / modulestore with it. This is a write "
            "operation — changed from GET (v0) to POST in v1 per ADR 0030 "
            "(GET must be idempotent). The ``upload`` path segment names a "
            "genuine non-resource action rather than an addressable CRUD "
            "sub-resource (ADR 0038 rule 10 exception)."
        ),
        parameters=[_COURSE_ID_PARAMETER, _DATA_QUERY_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=YoutubeTranscriptUploadSerializer,
                description="The transcript was downloaded from YouTube and saved.",
            ),
            400: OpenApiResponse(description="The `data` query parameter is missing or invalid."),
            **_COMMON_ERROR_RESPONSES,
        },
    )
    @course_author_access_required
    def upload(self, request: Request, course_key):
        """
        Download the YouTube transcript for a video and replace the existing
        edX transcript(s) with it.

        **Example Request**

            POST /api/contentstore/v1/youtube_transcripts/{course_id}/upload/?data=%7B...%7D

        Coarse permission check: ``@course_author_access_required`` verifies
        the caller has author access to ``course_id``. A specific permission
        check also runs inside the legacy ``replace_transcripts`` →
        ``_get_item`` call against the item's actual course_key — both
        checks are preserved per ADR 0031.

        Input validation (ADR 0025): same as ``check`` above — the ``data``
        query parameter is parsed as JSON and validated through
        ``YoutubeTranscriptUploadRequestSerializer`` *before*
        ``replace_transcripts`` is called (and before any YouTube
        download / VAL write / modulestore write can happen), so a malformed
        payload never reaches the legacy function's side effects.

        Delegates to the existing ``replace_transcripts()`` legacy function
        unchanged (same YouTube download + VAL write + modulestore save as
        v0). Its ``JsonResponse`` is parsed and re-wrapped the same way as
        ``check`` above, preserving the documented v0 response shape
        (``edx_video_id``, ``status``) on success.

        Note: the legacy function reads its payload from
        ``request.GET['data']`` (a query parameter), not from the POST body,
        regardless of HTTP verb. This is unchanged here to avoid touching
        ``replace_transcripts``'s parsing/business logic (ADR 0037-style
        caution against modifying legacy behavior mid-migration) — the fix
        for the "schema says POST body, only query param actually works"
        defect flagged in review is on the *validation* side (see above):
        the ``@extend_schema`` `request=` body declaration was removed in
        favor of an honest ``data`` ``OpenApiParameter`` (query), and that
        same ``data`` param is now what's actually validated up front. A
        client must still pass ``data`` as a query parameter (e.g.
        ``POST .../upload/?data=%7B...%7D``) — that is not a workaround, it
        is what is now accurately documented.
        """
        _validate_incoming_data(request, YoutubeTranscriptUploadRequestSerializer)
        legacy_response = replace_transcripts(request)
        body, status_code = _parse_legacy_json_response(legacy_response)
        if status_code >= 400:
            raise ValidationError({**body, "error_code": "youtube_transcript_upload_failed"})
        serializer = self.get_serializer(data=body)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status_code)
