"""
Views for Enhanced Staff Grader
"""
# NOTE: we intentionally do broad exception checking to return a clean error shape
# pylint: disable=broad-except

# NOTE: we intentionally add extra args using @require_params
# pylint: disable=arguments-differ
import logging

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from openassessment.xblock.config_mixin import ENHANCED_STAFF_GRADER, WAFFLE_NAMESPACE
from rest_framework.decorators import action
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from lms.djangoapps.ora_staff_grader.constants import PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID
from lms.djangoapps.ora_staff_grader.errors import (
    BadOraLocationResponse,
    GradeContestedResponse,
    InternalErrorResponse,
    LockContestedError,
    LockContestedResponse,
    MissingParamResponse,
    UnknownErrorResponse,
    XBlockInternalError
)
from lms.djangoapps.ora_staff_grader.ora_api import (
    batch_delete_submission_locks,
    check_submission_lock,
    claim_submission_lock,
    delete_submission_lock,
    get_assessment_info,
    get_assessments,
    get_submission_info,
    get_submissions,
    submit_grade
)
from lms.djangoapps.ora_staff_grader.serializers import (
    AssessmentFeedbackSerializer,
    FileListSerializer,
    InitializeSerializer,
    LockStatusSerializer,
    StaffAssessSerializer,
    SubmissionFetchSerializer,
    SubmissionStatusFetchSerializer
)
from lms.djangoapps.ora_staff_grader.utils import require_params
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

log = logging.getLogger(__name__)


class StaffGraderBaseView(RetrieveAPIView):
    """
    Base view for common auth/permission setup used across ESG views.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    permission_classes = (IsAuthenticated,)


class InitializeView(StaffGraderBaseView):
    """
    GET course metadata

    Response: {
        courseMetadata
        oraMetadata
        submissions
        isEnabled
    }

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - BadOraLocationResponse (HTTP 400) for bad ORA location
    - XBlockInternalError (HTTP 500) for an issue with ORA
    - UnknownError (HTTP 500) for other errors
    """

    def _is_staff_grader_enabled(self, course_key):
        """ Helper to evaluate if the staff grader flag / overrides are enabled """
        # This toggle is documented on the edx-ora2 repo in openassessment/xblock/config_mixin.py
        # Note: Do not copy this practice of directly using a toggle from a library.
        #  Instead, see docs for exposing a wrapper api:
        #  https://docs.openedx.org/projects/edx-toggles/en/latest/how_to/implement_the_right_toggle_type.html#using-other-toggles pylint: disable=line-too-long
        # pylint: disable=toggle-missing-annotation
        enhanced_staff_grader_flag = CourseWaffleFlag(
            f"{WAFFLE_NAMESPACE}.{ENHANCED_STAFF_GRADER}",
            module_name='openassessment.xblock.config_mixin'
        )
        return enhanced_staff_grader_flag.is_enabled(course_key)

    @require_params([PARAM_ORA_LOCATION])
    def get(self, request, ora_location, *args, **kwargs):
        try:
            init_data = {}

            # Get ORA block and config (incl. rubric)
            ora_usage_key = UsageKey.from_string(ora_location)
            init_data["oraMetadata"] = modulestore().get_item(ora_usage_key)

            # Get course metadata
            course_id = str(ora_usage_key.course_key)
            init_data["courseMetadata"] = get_course_overview_or_none(course_id)

            # Get list of submissions for this ORA
            init_data["submissions"] = get_submissions(request, ora_location)

            # Is the Staff Grader enabled for this course?
            init_data["isEnabled"] = self._is_staff_grader_enabled(ora_usage_key.course_key)

            response_data = InitializeSerializer(init_data).data
            log.info(response_data)
            return Response(response_data)

        # Catch bad ORA location
        except (InvalidKeyError, ItemNotFoundError):
            log.error(f"Bad ORA location provided: {ora_location}")
            return BadOraLocationResponse()

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling in case something blows up
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class AssessmentFeedbackView(StaffGraderBaseView, ViewSet):
    """
    View for fetching assessment feedback for a submission.

    **Methods**

    * (GET) `api/ora_staff_grader/assessments/feedback/from`
        List all assessments received by a user (according to
        their submissionUUID) in an ORA assignment.

    * (GET) `api/ora_staff_grader/assessments/feedback/to`
        List all assessments given by a user (according to
        their submissionUUID) in an ORA assignment.

    **Query Params**:

    * `oraLocation` (str): ORA location for XBlock handling
    * `submissionUUID` (str): The ORA submission UUID

    **Response**:

        {
            assessments (List[dict]): [
                {
                    "assessment_id: (str) Assessment id
                    "scorer_name: (str) Scorer name
                    "scorer_username: (str) Scorer username
                    "scorer_email: (str) Scorer email
                    "assessment_date: (str) Assessment date
                    "assessment_scores (List[dict]) [
                        {
                            "criterion_name: (str) Criterion name
                            "score_earned: (int) Score earned
                            "score_type: (str) Score type
                        }
                    ]
                    "problem_step (str) Problem step (Self, Peer, or Staff)
                    "feedback: (str) Feedback of the assessment
                }
            ]
        }

    **Errors**:

    * `MissingParamResponse` (HTTP 400) for missing params
    * `BadOraLocationResponse` (HTTP 400) for bad ORA location
    * `XBlockInternalError` (HTTP 500) for an issue with ORA
    * `UnknownError` (HTTP 500) for other errors
    """
    @action(methods=["get"], detail=False, url_path="from")
    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def get_from(self, request: Request, ora_location: str, submission_uuid: str, *args, **kwargs):
        return self._get_assessments(request, ora_location, "list_assessments_from", submission_uuid)

    @action(methods=["get"], detail=False, url_path="to")
    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def get_to(self, request: Request, ora_location: str, submission_uuid: str, *args, **kwargs):
        return self._get_assessments(request, ora_location, "list_assessments_to", submission_uuid)

    def _get_assessments(
        self, request: Request, ora_location: str, handler_name: str, submission_uuid: str
    ):
        """
        Fetches assessment data using the given handler name.

        Args:
            request (Request): The Django request object.
            ora_location (str): The ORA location for XBlock handling.
            handler_name (str): The name of the XBlock handler to use.
            submission_uuid (str): The ORA submission UUID.

        Returns:
            A Django response object containing serialized assessment data or an error response.
        """
        try:
            assessments_data = {
                "assessments": get_assessments(
                    request, ora_location, handler_name, submission_uuid
                )
            }
            response_data = AssessmentFeedbackSerializer(assessments_data).data
            return Response(response_data)

        except (InvalidKeyError, ItemNotFoundError):
            log.error(f"Bad ORA location provided: {ora_location}")
            return BadOraLocationResponse()

        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class SubmissionFetchView(StaffGraderBaseView):
    """
    GET submission contents and assessment info, if any

    Response: {
        gradeData: {
            score: (dict or None) {
                pointsEarned: (int) earned points
                pointsPossible: (int) possible points
            }
            overallFeedback: (string) overall feedback
            criteria: (list of dict) [{
                name: (str) name of criterion
                feedback: (str) feedback for criterion
                points: (int) points of selected option or None if feedback-only criterion
                selectedOption: (str) name of selected option or None if feedback-only criterion
            }]
        }
        response: {
            text: (list of string), [the html content of text responses]
            files: (list of dict) [{
                downloadUrl: (string) file download url
                description: (string) file description
                name: (string) filename
            }]
        }
    }

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - XBlockInternalError (HTTP 500) for an issue with ORA
    - UnknownError (HTTP 500) for other errors
    """

    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def get(self, request, ora_location, submission_uuid, *args, **kwargs):
        try:
            submission_info = get_submission_info(
                request, ora_location, submission_uuid
            )
            assessment_info = get_assessment_info(
                request, ora_location, submission_uuid
            )
            lock_info = check_submission_lock(request, ora_location, submission_uuid)

            response_data = SubmissionFetchSerializer(
                {
                    "submission_info": submission_info,
                    "assessment_info": assessment_info,
                    "lock_info": lock_info,
                }
            ).data

            log.info(response_data)
            return Response(response_data)

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling in case something blows up
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class SubmissionStatusFetchView(StaffGraderBaseView):
    """
    GET submission grade status, lock status, and grade data

    Response: {
        gradeStatus: (str) one of [graded, ungraded]
        lockStatus: (str) one of [locked, unlocked, in-progress]
        gradeData: {
            score: (dict or None) {
                pointsEarned: (int) earned points
                pointsPossible: (int) possible points
            }
            overallFeedback: (string) overall feedback
            criteria: (list of dict) [{
                name: (str) name of criterion
                feedback: (str) feedback for criterion
                points: (int) points of selected option or None if feedback-only criterion
                selectedOption: (str) name of selected option or None if feedback-only criterion
            }]
        }
    }

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - XBlockInternalError (HTTP 500) for an issue with ORA
    - UnknownError (HTTP 500) for other errors
    """

    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def get(self, request, ora_location, submission_uuid, *args, **kwargs):
        try:
            assessment_info = get_assessment_info(
                request, ora_location, submission_uuid
            )
            lock_info = check_submission_lock(request, ora_location, submission_uuid)

            response_data = SubmissionStatusFetchSerializer(
                {
                    "assessment_info": assessment_info,
                    "lock_info": lock_info,
                }
            ).data

            log.info(response_data)
            return Response(response_data)

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling in case something blows up
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class SubmissionFilesFetchView(StaffGraderBaseView):
    """
    GET file metadata for a submission.

    Used to get updated file download links to avoid signed download link expiration
    issues.

    Response: {
        files: [
            downloadUrl (url),
            description (string),
            name (string),
            size (bytes),
        ]
    }

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - XBlockInternalError (HTTP 500) for an issue with ORA
    - UnknownError (HTTP 500) for other errors
    """

    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def get(self, request, ora_location, submission_uuid, *args, **kwargs):
        try:
            submission_info = get_submission_info(
                request, ora_location, submission_uuid
            )

            response_data = FileListSerializer(submission_info).data

            log.info(response_data)
            return Response(response_data)

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling in case something blows up
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class UpdateGradeView(StaffGraderBaseView):
    """
    POST submit a grade for a submission

    Body: {
        overallFeedback: (string) overall feedback
        criteria: [
            {
                name: (string) name of criterion
                feedback: (string, optional) feedback for criterion
                selectedOption: (string) name of selected option or None if feedback-only criterion
            },
            ... (one per criteria)
        ]
    }

    Response: {
        gradeStatus: (string) - One of ['graded', 'ungraded']
        lockStatus: (string) - One of ['unlocked', 'locked', 'in-progress']
        gradeData: {
            score: (dict or None) {
                pointsEarned: (int) earned points
                pointsPossible: (int) possible points
            }
            overallFeedback: (string) overall feedback
            criteria: (list of dict) [{
                name: (str) name of criterion
                feedback: (str) feedback for criterion
                selectedOption: (str) name of selected option or None if feedback-only criterion
            }]
        }
    }

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - GradeContestedResponse (HTTP 409) for trying to submit a grade for a submission you don't have an active lock for
    - XBlockInternalError (HTTP 500) for an issue with ORA
    - UnknownError (HTTP 500) for other errors
    """

    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def post(self, request, ora_location, submission_uuid, *args, **kwargs):
        """Update a grade"""
        try:
            # Reassert that we have ownership of the submission lock
            lock_info = check_submission_lock(request, ora_location, submission_uuid)
            if not lock_info.get("lock_status") == "in-progress":
                assessment_info = get_assessment_info(
                    request, ora_location, submission_uuid
                )
                submission_status = SubmissionStatusFetchSerializer(
                    {
                        "assessment_info": assessment_info,
                        "lock_info": lock_info,
                    }
                ).data
                log.error(f"Grade contested for submission: {submission_uuid}")
                return GradeContestedResponse(context=submission_status)

            # Transform grade data and submit assessment, raises on failure
            context = {"submission_uuid": submission_uuid}
            grade_data = StaffAssessSerializer(request.data, context=context).data
            submit_grade(request, ora_location, grade_data)

            # Clear the lock on the graded submission
            delete_submission_lock(request, ora_location, submission_uuid)

            # Return submission status info to frontend
            assessment_info = get_assessment_info(
                request, ora_location, submission_uuid
            )
            lock_info = check_submission_lock(request, ora_location, submission_uuid)
            response_data = SubmissionStatusFetchSerializer(
                {
                    "assessment_info": assessment_info,
                    "lock_info": lock_info,
                }
            ).data

            log.info(response_data)
            return Response(response_data)

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling in case something blows up
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class SubmissionLockView(StaffGraderBaseView):
    """
    POST claim a submission lock for grading
    DELETE release a submission lock

    Params:
    - ora_location (str/UsageID): ORA location for XBlock handling
    - submissionUUID (UUID): A submission to lock/unlock

    Response: {
        lockStatus
    }

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - LockContestedResponse (HTTP 409) for contested lock
    - XBlockInternalError (HTTP 500) for an issue with ORA
    - UnknownError (HTTP 500) for other errors
    """

    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def post(self, request, ora_location, submission_uuid, *args, **kwargs):
        """Claim a submission lock"""
        try:
            # Validate ORA location
            UsageKey.from_string(ora_location)
            lock_info = claim_submission_lock(request, ora_location, submission_uuid)

            response_data = LockStatusSerializer(lock_info).data
            log.info(response_data)
            return Response(response_data)

        # Catch bad ORA location
        except (InvalidKeyError, ItemNotFoundError):
            log.error(f"Bad ORA location provided: {ora_location}")
            return BadOraLocationResponse()

        # Return updated lock info on error
        except LockContestedError:
            lock_info = check_submission_lock(request, ora_location, submission_uuid)
            lock_status = LockStatusSerializer(lock_info).data
            log.error(f"Lock contested for submission: {submission_uuid}")
            return LockContestedResponse(context=lock_status)

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()

    @require_params([PARAM_ORA_LOCATION, PARAM_SUBMISSION_ID])
    def delete(self, request, ora_location, submission_uuid, *args, **kwargs):
        """Clear a submission lock"""
        try:
            # Validate ORA location
            UsageKey.from_string(ora_location)
            lock_info = delete_submission_lock(request, ora_location, submission_uuid)

            response_data = LockStatusSerializer(lock_info).data
            log.info(response_data)
            return Response(response_data)

        # Catch bad ORA location
        except (InvalidKeyError, ItemNotFoundError):
            log.error(f"Bad ORA location provided: {ora_location}")
            return BadOraLocationResponse()

        # Return updated lock info on error
        except LockContestedError:
            lock_info = check_submission_lock(request, ora_location, submission_uuid)
            lock_status = LockStatusSerializer(lock_info).data
            return LockContestedResponse(context=lock_status)

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling in case something blows up
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()


class SubmissionBatchUnlockView(StaffGraderBaseView):
    """
    POST delete a group of submission locks, limited to just those in the list that the user owns.

    Params:
    - ora_location (str/UsageID): ORA location for XBlock handling

    Body:
    - submissionUUIDs (UUID): A list of submission/team submission UUIDS to lock/unlock

    Response: None

    Errors:
    - MissingParamResponse (HTTP 400) for missing params
    - XBlockInternalError (HTTP 500) for an issue within ORA
    """

    @require_params([PARAM_ORA_LOCATION])
    def post(self, request, ora_location, *args, **kwargs):
        """Batch delete submission locks"""
        try:
            # Validate ORA location
            UsageKey.from_string(ora_location)

            # Pull submission UUIDs list from request body
            submission_uuids = request.data.get('submissionUUIDs')
            if not isinstance(submission_uuids, list):
                return MissingParamResponse()
            batch_delete_submission_locks(request, ora_location, submission_uuids)

            # Return empty response
            return Response({})

        # Catch bad ORA location
        except (InvalidKeyError, ItemNotFoundError):
            log.error(f"Bad ORA location provided: {ora_location}")
            return BadOraLocationResponse()

        # Issues with the XBlock handlers
        except XBlockInternalError as ex:
            log.error(ex)
            return InternalErrorResponse(context=ex.context)

        # Blanket exception handling
        except Exception as ex:
            log.exception(ex)
            return UnknownErrorResponse()
