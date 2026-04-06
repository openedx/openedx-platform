"""
Tests for ESG views
"""
import json
from unittest.mock import Mock, patch
from uuid import uuid4

import ddt
from django.http import QueryDict
from django.urls import reverse
from rest_framework.test import APITestCase

import lms.djangoapps.ora_staff_grader.tests.test_data as test_data
from common.djangoapps.student.tests.factories import StaffFactory
from lms.djangoapps.ora_staff_grader.constants import (
    ERR_BAD_ORA_LOCATION,
    ERR_GRADE_CONTESTED,
    ERR_INTERNAL,
    ERR_LOCK_CONTESTED,
    ERR_MISSING_PARAM,
    ERR_UNKNOWN,
    PARAM_ORA_LOCATION,
    PARAM_SUBMISSION_ID,
)
from lms.djangoapps.ora_staff_grader.errors import LockContestedError, XBlockInternalError
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class BaseViewTest(SharedModuleStoreTestCase, APITestCase):
    """Base class for shared test utils and setup"""

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.api_url = reverse(cls.view_name)

        cls.course = CourseFactory.create()
        cls.course_key = cls.course.location.course_key

        cls.ora_block = BlockFactory.create(
            category="openassessment",
            parent_location=cls.course.location,
            display_name="test",
        )
        cls.ora_usage_key = str(cls.ora_block.location)

        cls.password = "password"
        cls.staff = StaffFactory(course_key=cls.course_key, password=cls.password)

    def log_in(self):
        """Log in as staff"""
        self.client.login(username=self.staff.username, password=self.password)

    def url_with_params(self, params):
        """For DRF client.posts, you can't add query params easily. This helper adds it to the request URL"""
        query_dictionary = QueryDict("", mutable=True)
        query_dictionary.update(params)

        return "{base_url}?{querystring}".format(
            base_url=reverse(self.view_name), querystring=query_dictionary.urlencode()
        )


@ddt.ddt
class TestInitializeView(BaseViewTest):
    """
    Tests for the /initialize view, creating setup data for ESG
    """

    view_name = "ora-staff-grader:initialize"

    def setUp(self):
        super().setUp()
        self.log_in()

    @ddt.data({}, {PARAM_ORA_LOCATION: ""})
    def test_missing_param(self, query_params):
        """Missing ORA location param should return 400 and error message"""
        response = self.client.get(self.api_url, query_params)

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_MISSING_PARAM}

    def test_bad_ora_location(self):
        """Bad ORA location should return a 400 and error message"""
        response = self.client.get(
            self.api_url, {PARAM_ORA_LOCATION: "not_a_real_location"}
        )

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_BAD_ORA_LOCATION}

    @patch("lms.djangoapps.ora_staff_grader.views.get_submissions")
    @patch("lms.djangoapps.ora_staff_grader.views.get_course_overview_or_none")
    def test_init(self, mock_get_course_overview, mock_get_submissions):
        """Any failure to fetch info returns an unknown error response"""
        mock_course_overview = CourseOverviewFactory.create()
        mock_get_course_overview.return_value = mock_course_overview
        mock_get_submissions.return_value = test_data.example_submission_list

        response = self.client.get(
            self.api_url, {PARAM_ORA_LOCATION: self.ora_usage_key}
        )

        expected_keys = set(["courseMetadata", "oraMetadata", "submissions", "isEnabled"])
        assert response.status_code == 200
        assert response.data.keys() == expected_keys

    @patch("lms.djangoapps.ora_staff_grader.views.get_submissions")
    @patch("lms.djangoapps.ora_staff_grader.views.get_course_overview_or_none")
    def test_init_xblock_exception(
        self, mock_get_course_overview, mock_get_submissions
    ):
        """If one of the XBlock handlers fails, the exception should be caught"""
        mock_course_overview = CourseOverviewFactory.create()
        mock_get_course_overview.return_value = mock_course_overview
        # Mock an error getting submissions
        mock_get_submissions.side_effect = XBlockInternalError(
            context={"handler": "list_staff_workflows"}
        )

        response = self.client.get(
            self.api_url, {PARAM_ORA_LOCATION: self.ora_usage_key}
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "list_staff_workflows",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.get_submissions")
    @patch("lms.djangoapps.ora_staff_grader.views.get_course_overview_or_none")
    def test_init_generic_exception(
        self, mock_get_course_overview, mock_get_submissions
    ):
        """If something else strange fails (e.g. bad data shape), an "unknown" error should be surfaced"""
        mock_course_overview = CourseOverviewFactory.create()
        mock_get_course_overview.return_value = mock_course_overview
        # Mock a bad returned data shape which would break serialization
        mock_get_submissions.return_value = {"bad": "wolf"}

        response = self.client.get(
            self.api_url, {PARAM_ORA_LOCATION: self.ora_usage_key}
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}


@ddt.ddt
class TestFetchSubmissionView(BaseViewTest):
    """
    Tests for the submission fetch view
    """

    view_name = "ora-staff-grader:fetch-submission"

    def setUp(self):
        super().setUp()
        self.log_in()

    @ddt.data({}, {PARAM_ORA_LOCATION: "", PARAM_SUBMISSION_ID: ""})
    def test_missing_params(self, query_params):
        """Missing or blank params should return 400 and error message"""
        response = self.client.get(self.api_url, query_params)

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_MISSING_PARAM}

    @ddt.data(True, False)
    @patch("lms.djangoapps.ora_staff_grader.views.get_submission_info")
    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    def test_fetch_submission(
        self,
        has_assessment,
        mock_check_submission_lock,
        mock_get_assessment_info,
        mock_get_submission_info,
    ):
        """Successfull submission fetch status returns submission, lock, and grade data"""
        mock_get_submission_info.return_value = test_data.example_submission
        mock_get_assessment_info.return_value = (
            {} if not has_assessment else test_data.example_assessment
        )
        mock_check_submission_lock.return_value = {"lock_status": "unlocked"}

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 200
        assert response.data.keys() == set(
            ["gradeData", "response", "gradeStatus", "lockStatus"]
        )
        assert response.data["response"].keys() == set(["files", "text"])
        expected_assessment_keys = (
            set(["score", "overallFeedback", "criteria"]) if has_assessment else set()
        )
        assert response.data["gradeData"].keys() == expected_assessment_keys

    @patch("lms.djangoapps.ora_staff_grader.views.get_submission_info")
    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    def test_fetch_submission_xblock_exception(
        self,
        mock_check_submission_lock,
        mock_get_assessment_info,
        mock_get_submission_info,
    ):
        """An exception in any XBlock handler returns an error response"""
        mock_get_submission_info.return_value = test_data.example_submission
        # Mock an error in getting the assessment info
        mock_get_assessment_info.side_effect = XBlockInternalError(
            context={"handler": "get_assessment_info"}
        )
        mock_check_submission_lock.return_value = {"lock_status": "unlocked"}

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "get_assessment_info",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.get_submission_info")
    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    def test_fetch_submission_generic_exception(
        self,
        mock_check_submission_lock,
        mock_get_assessment_info,
        mock_get_submission_info,
    ):
        """Other generic exceptions should return the "unknown" error response"""
        mock_get_submission_info.return_value = test_data.example_submission
        mock_get_assessment_info.return_value = test_data.example_assessment
        # Mock a bad data shape to break serialization
        mock_check_submission_lock.return_value = {"mad": "hatter"}

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}


@ddt.ddt
class TestFilesFetchView(BaseViewTest):
    """
    Tests for the SubmissionFilesFetchView
    """

    view_name = "ora-staff-grader:fetch-files"

    def setUp(self):
        super().setUp()
        self.log_in()

    @ddt.data({}, {PARAM_ORA_LOCATION: "", PARAM_SUBMISSION_ID: ""})
    def test_missing_params(self, query_params):
        """Missing or blank params should return 400 and error message"""
        response = self.client.get(self.api_url, query_params)

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_MISSING_PARAM}

    @patch("lms.djangoapps.ora_staff_grader.views.get_submission_info")
    def test_fetch_files(self, mock_get_submission_info):
        """Successfull file fetch returns the list of files for a submission"""
        mock_get_submission_info.return_value = test_data.example_submission

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 200
        assert response.data.keys() == set(["files"])
        assert len(test_data.example_submission["files"]) == len(response.data['files'])

    @patch("lms.djangoapps.ora_staff_grader.views.get_submission_info")
    def test_fetch_files_generic_exception(self, mock_get_submission_info):
        """Other generic exceptions should return the "unknown" error response"""
        mock_get_submission_info.side_effect = Exception()

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}

    @patch("lms.djangoapps.ora_staff_grader.views.get_submission_info")
    def test_fetch_files_xblock_exception(self, mock_get_submission_info):
        """An exception in any XBlock handler returns an error response"""
        mock_get_submission_info.side_effect = XBlockInternalError(
            context={"handler": "get_submission_info"}
        )

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "get_submission_info",
        }


@ddt.ddt
class TestFetchSubmissionStatusView(BaseViewTest):
    """
    Tests for the submission fetch view
    """

    view_name = "ora-staff-grader:fetch-submission-status"

    def setUp(self):
        super().setUp()
        self.log_in()

    @ddt.data(
        {},
        {PARAM_ORA_LOCATION: "", PARAM_SUBMISSION_ID: Mock()},
        {PARAM_ORA_LOCATION: Mock(), PARAM_SUBMISSION_ID: ""},
    )
    def test_missing_param(self, query_params):
        """Missing ORA location or submission ID param should return 400 and error message"""
        response = self.client.get(self.api_url, query_params)

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_MISSING_PARAM}

    @ddt.data(True, False)
    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    def test_fetch_submission_status(
        self,
        has_assessment,
        mock_check_submission_lock,
        mock_get_assessment_info,
    ):
        """Successful fetch submission returns submission and related lock/assessment info"""
        mock_get_assessment_info.return_value = (
            {} if not has_assessment else test_data.example_assessment
        )
        mock_check_submission_lock.return_value = {"lock_status": "in-progress"}

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 200
        actual = response.json()
        expected = {
            "gradeStatus": "graded" if has_assessment else "ungraded",
            "lockStatus": mock_check_submission_lock.return_value["lock_status"],
            "gradeData": {}
            if not has_assessment
            else {
                "score": test_data.example_assessment["score"],
                "overallFeedback": test_data.example_assessment["feedback"],
                "criteria": [
                    {
                        "name": "Criterion 1",
                        "selectedOption": "Three",
                        "points": 3,
                        "feedback": "Feedback 1",
                    },
                ],
            },
        }
        assert actual == expected

    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    def test_fetch_submission_status_xblock_exception(
        self, mock_check_submission_lock, mock_get_assessment_info
    ):
        """Exceptions within an XBlock return an internal error response"""
        mock_get_assessment_info.return_value = {}
        mock_check_submission_lock.side_effect = XBlockInternalError(
            context={"handler": "claim_submission_lock"}
        )

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "claim_submission_lock",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    def test_fetch_submission_status_generic_exception(
        self, mock_check_submission_lock, mock_get_assessment_info
    ):
        """Exceptions outside of an XBlock return a generic error response"""
        mock_get_assessment_info.return_value = {}
        # Mock a bad data shape to throw a serializer exception
        mock_check_submission_lock.return_value = {"jekyll", "hyde"}

        ora_location, submission_uuid = Mock(), Mock()
        response = self.client.get(
            self.api_url,
            {PARAM_ORA_LOCATION: ora_location, PARAM_SUBMISSION_ID: submission_uuid},
        )

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}


class TestSubmissionLockView(BaseViewTest):
    """
    Tests for the /lock view, locking or unlocking a submission for grading
    """

    view_name = "ora-staff-grader:lock"

    test_submission_uuid = str(uuid4())
    test_anon_user_id = "anon-user-id"
    test_other_anon_user_id = "anon-user-id-2"
    test_timestamp = "2020-08-29T02:14:00-04:00"

    def setUp(self):
        super().setUp()

        # Lock requests must include ORA location and submission UUID
        self.test_lock_params = {
            PARAM_ORA_LOCATION: self.ora_usage_key,
            PARAM_SUBMISSION_ID: self.test_submission_uuid,
        }

        self.log_in()

    def claim_lock(self, params):
        """Wrapper for easier calling of 'claim_submission_lock'"""
        return self.client.post(self.url_with_params(params))

    def delete_lock(self, params):
        """Wrapper for easier calling of 'delete_submission_lock'"""
        return self.client.delete(self.url_with_params(params))

    # Tests for claiming a lock (POST)

    def test_claim_lock_invalid_ora(self):
        """An invalid ORA returns a 400"""
        self.test_lock_params[PARAM_ORA_LOCATION] = "not_a_real_location"

        response = self.claim_lock(self.test_lock_params)

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_BAD_ORA_LOCATION}

    @patch("lms.djangoapps.ora_staff_grader.views.claim_submission_lock")
    def test_claim_lock(self, mock_claim_lock):
        """POST tries to claim a submission lock. Success returns lock status 'in-progress'."""
        mock_return_data = {
            "submission_uuid": self.test_submission_uuid,
            "owner_id": self.test_anon_user_id,
            "created_at": self.test_timestamp,
            "lock_status": "in-progress",
        }
        mock_claim_lock.return_value = mock_return_data

        response = self.claim_lock(self.test_lock_params)

        expected_value = {"lockStatus": "in-progress"}
        assert response.status_code == 200
        assert json.loads(response.content) == expected_value

    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.claim_submission_lock")
    def test_claim_lock_contested(self, mock_claim_lock, mock_check_lock):
        """Attempting to claim a lock owned by another user returns a 403 - forbidden and passes error code."""
        mock_claim_lock.side_effect = LockContestedError()
        mock_check_lock.return_value = {
            "submission_uuid": self.test_submission_uuid,
            "owner_id": self.test_other_anon_user_id,
            "created_at": self.test_timestamp,
            "lock_status": "locked",
        }

        response = self.claim_lock(self.test_lock_params)

        expected_value = {"error": ERR_LOCK_CONTESTED, "lockStatus": "locked"}
        assert response.status_code == 409
        assert json.loads(response.content) == expected_value

    @patch("lms.djangoapps.ora_staff_grader.views.claim_submission_lock")
    def test_claim_lock_xblock_exception(
        self,
        mock_claim_lock,
    ):
        """In the unlikely event of an error, the exits are to your left and behind you"""
        mock_claim_lock.side_effect = XBlockInternalError(
            context={"handler": "claim_submission_lock"}
        )

        response = self.claim_lock(self.test_lock_params)

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "claim_submission_lock",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.claim_submission_lock")
    def test_claim_lock_generic_exception(
        self,
        mock_claim_lock,
    ):
        """In the even more unlikely event of an unhandled error, shrug exuberantly"""
        # Mock a bad data shape to break serialization and raise a generic exception
        mock_claim_lock.return_value = {"android": "Rachel"}

        response = self.claim_lock(self.test_lock_params)

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}

    # Tests for deleting a lock (DELETE)

    @patch("lms.djangoapps.ora_staff_grader.views.delete_submission_lock")
    def test_delete_lock(self, mock_delete_lock):
        """DELETE indicates to clear submission lock. Success returns lock status 'unlocked'."""
        mock_delete_lock.return_value = {"lock_status": "unlocked"}

        response = self.delete_lock(self.test_lock_params)

        expected_value = {"lockStatus": "unlocked"}
        assert response.status_code == 200
        assert json.loads(response.content) == expected_value

    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.delete_submission_lock")
    def test_delete_lock_contested(self, mock_delete_lock, mock_check_lock):
        """Attempting to delete a lock owned by another user returns a 403 - forbidden and passes error code."""
        mock_delete_lock.side_effect = LockContestedError()
        mock_check_lock.return_value = {
            "submission_uuid": self.test_submission_uuid,
            "owner_id": self.test_other_anon_user_id,
            "created_at": self.test_timestamp,
            "lock_status": "locked",
        }

        response = self.delete_lock(self.test_lock_params)

        expected_value = {"error": ERR_LOCK_CONTESTED, "lockStatus": "locked"}
        assert response.status_code == 409
        assert json.loads(response.content) == expected_value

    @patch("lms.djangoapps.ora_staff_grader.views.delete_submission_lock")
    def test_delete_lock_xblock_exception(self, mock_delete_lock):
        """In the unlikely event of an error, the exits are to your left and behind you"""
        mock_delete_lock.side_effect = XBlockInternalError(
            context={"handler": "delete_submission_lock"}
        )

        response = self.delete_lock(self.test_lock_params)

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "delete_submission_lock",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.delete_submission_lock")
    def test_delete_lock_generic_exception(self, mock_delete_lock):
        """In the even more unlikely event of an unhandled error, shrug exuberantly"""
        # Mock a bad data shape to break serialization and raise a generic exception
        mock_delete_lock.return_value = {"android": "Roy Batty"}

        response = self.delete_lock(self.test_lock_params)

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}


class TestBatchSubmissionLockView(BaseViewTest):
    """
    Tests for the /lock view, locking or unlocking a submission for grading
    """

    view_name = "ora-staff-grader:batch-unlock"

    test_submission_uuids = [str(uuid4()) for _ in range(3)]
    test_anon_user_id = "anon-user-id"
    test_other_anon_user_id = "anon-user-id-2"
    test_timestamp = "2020-08-29T02:14:00-04:00"

    def setUp(self):
        super().setUp()

        # Batch unlock includes the ORA location in the params...
        self.test_request_params = {
            PARAM_ORA_LOCATION: self.ora_usage_key,
        }

        # and a list of submission UUIDs in the body
        self.test_request_body = {
            "submissionUUIDs": self.test_submission_uuids
        }

        self.log_in()

    def batch_unlock(self, params, body):
        """Wrapper for easier calling of 'batch_unlock'"""
        return self.client.post(self.url_with_params(params), body, format="json")

    @patch("lms.djangoapps.ora_staff_grader.views.batch_delete_submission_locks")
    def test_batch_unlock_invalid_ora(self, mock_batch_delete):
        """An invalid ORA returns a 400"""
        self.test_request_params[PARAM_ORA_LOCATION] = "not_a_real_location"

        response = self.batch_unlock(self.test_request_params, self.test_request_body)

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_BAD_ORA_LOCATION}
        mock_batch_delete.assert_not_called()

    @patch("lms.djangoapps.ora_staff_grader.views.batch_delete_submission_locks")
    def test_batch_unlock_missing_submission_list(self, mock_batch_delete):
        """An invalid ORA returns a 400"""

        response = self.batch_unlock(self.test_request_params, {})

        assert response.status_code == 400
        assert json.loads(response.content) == {"error": ERR_MISSING_PARAM}
        mock_batch_delete.assert_not_called()

    @patch("lms.djangoapps.ora_staff_grader.views.batch_delete_submission_locks")
    def test_batch_unlock(self, mock_batch_delete):
        """POST tries to delete a group of submission locks. Success returns empty 200"""
        mock_batch_delete.return_value = None

        response = self.batch_unlock(self.test_request_params, self.test_request_body)

        assert response.status_code == 200
        assert json.loads(response.content) == {}
        mock_batch_delete.assert_called()

    @patch("lms.djangoapps.ora_staff_grader.views.batch_delete_submission_locks")
    def test_batch_unlock_internal_error(self, mock_batch_delete):
        """Any internal errors to this API get surfaced as an internal error"""
        mock_batch_delete.side_effect = XBlockInternalError(
            context={"handler": "batch_delete_submission_locks"}
        )

        response = self.batch_unlock(self.test_request_params, self.test_request_body)

        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "batch_delete_submission_locks",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.batch_delete_submission_locks")
    def test_batch_unlock_generic_exception(
        self,
        mock_batch_delete,
    ):
        """In the even more unlikely event of an unhandled error, shrug exuberantly"""
        # Mock a generic error inside the API
        mock_batch_delete.side_effect = Exception()

        response = self.batch_unlock(self.test_request_params, self.test_request_body)

        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}


class TestUpdateGradeView(BaseViewTest):
    """
    Tests for updating a grade for a submission
    """

    view_name = "ora-staff-grader:update-grade"

    submission_uuid = str(uuid4())
    ora_location = Mock()
    test_anon_user_id = "anon-user-id"
    test_timestamp = "2020-08-29T02:14:00-04:00"

    def setUp(self):
        super().setUp()
        self.log_in()

    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.submit_grade")
    def test_submit_grade_xblock_exception(self, mock_submit_grade, mock_check_lock):
        """A handled ORA failure to submit a grade returns a server error"""
        mock_check_lock.return_value = {"lock_status": "in-progress"}
        mock_submit_grade.side_effect = XBlockInternalError(
            context={"handler": "submit_staff_assessment", "msg": "Danger, Will Robinson!"}
        )
        url = self.url_with_params(
            {
                PARAM_ORA_LOCATION: self.ora_location,
                PARAM_SUBMISSION_ID: self.submission_uuid,
            }
        )
        data = test_data.example_grade_data

        response = self.client.post(url, data, format="json")
        assert response.status_code == 500
        assert json.loads(response.content) == {
            "error": ERR_INTERNAL,
            "handler": "submit_staff_assessment",
            "msg": "Danger, Will Robinson!",
        }

    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.submit_grade")
    def test_submit_grade_generic_exception(self, mock_submit_grade, mock_check_lock):
        """A fall-through failure returns an unknown error"""
        mock_check_lock.return_value = {"lock_status": "in-progress"}
        mock_submit_grade.return_value = {"error": "time paradox encountered"}
        url = self.url_with_params(
            {
                PARAM_ORA_LOCATION: self.ora_location,
                PARAM_SUBMISSION_ID: self.submission_uuid,
            }
        )
        data = test_data.example_grade_data

        response = self.client.post(url, data, format="json")
        assert response.status_code == 500
        assert json.loads(response.content) == {"error": ERR_UNKNOWN}

    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.delete_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.submit_grade")
    def test_submit_grade_success(
        self, mock_submit_grade, mock_delete_lock, mock_get_info, mock_check_lock
    ):
        """A grade update success should clear the submission lock and return submission meta"""
        mock_check_lock.side_effect = [
            {"lock_status": "in-progress"},
            {"lock_status": "unlocked"},
        ]
        mock_submit_grade.return_value = {"success": True, "msg": ""}
        mock_get_info.return_value = test_data.example_assessment

        url = self.url_with_params(
            {
                PARAM_ORA_LOCATION: self.ora_location,
                PARAM_SUBMISSION_ID: self.submission_uuid,
            }
        )
        data = test_data.example_grade_data

        response = self.client.post(url, data, format="json")

        expected_response = {
            "gradeStatus": "graded",
            "lockStatus": "unlocked",
            "gradeData": {
                "score": test_data.example_assessment["score"],
                "overallFeedback": test_data.example_assessment["feedback"],
                "criteria": [
                    {
                        "name": "Criterion 1",
                        "selectedOption": "Three",
                        "points": 3,
                        "feedback": "Feedback 1",
                    },
                ],
            },
        }

        assert response.status_code == 200
        assert json.loads(response.content) == expected_response

        # Verify that clear lock was called
        mock_delete_lock.assert_called_once()

    @patch("lms.djangoapps.ora_staff_grader.views.check_submission_lock")
    @patch("lms.djangoapps.ora_staff_grader.views.get_assessment_info")
    @patch("lms.djangoapps.ora_staff_grader.views.submit_grade")
    def test_submit_grade_contested(
        self, mock_submit_grade, mock_get_info, mock_check_lock
    ):
        """Submitting a grade should be blocked if someone else has obtained the lock"""
        mock_check_lock.side_effect = [{"lock_status": "unlocked"}]
        mock_get_info.return_value = test_data.example_assessment

        url = self.url_with_params(
            {
                PARAM_ORA_LOCATION: self.ora_location,
                PARAM_SUBMISSION_ID: self.submission_uuid,
            }
        )
        data = test_data.example_grade_data

        response = self.client.post(url, data, format="json")

        assert response.status_code == 409
        assert json.loads(response.content) == {
            "error": ERR_GRADE_CONTESTED,
            "gradeStatus": "graded",
            "lockStatus": "unlocked",
            "gradeData": {
                "score": test_data.example_assessment["score"],
                "overallFeedback": test_data.example_assessment["feedback"],
                "criteria": [
                    {
                        "name": "Criterion 1",
                        "selectedOption": "Three",
                        "points": 3,
                        "feedback": "Feedback 1",
                    },
                ],
            },
        }

        # Verify that submit grade was not called
        mock_submit_grade.assert_not_called()
