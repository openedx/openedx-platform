"""
Mock views for ESG
"""

from rest_framework.generics import RetrieveAPIView

# pylint: disable=arguments-differ
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.ora_staff_grader.mock.utils import (
    fetch_response,
    fetch_submission,
    get_course_metadata,
    get_ora_metadata,
    get_submissions,
    save_submission_update,
)

PARAM_ORA_LOCATION = "oraLocation"
PRAM_SUBMISSION_ID = "submissionUUID"


class InitializeView(RetrieveAPIView):
    """Returns initial app state"""

    def get(self, request):
        ora_location = request.query_params[PARAM_ORA_LOCATION]

        return Response(
            {
                "courseMetadata": get_course_metadata(ora_location),
                "oraMetadata": get_ora_metadata(ora_location),
                "submissions": get_submissions(ora_location),
            }
        )


class SubmissionFetchView(RetrieveAPIView):
    """Get a submission"""

    def get(self, request):
        ora_location = request.query_params[PARAM_ORA_LOCATION]
        submission_id = request.query_params[PRAM_SUBMISSION_ID]

        submission = fetch_submission(ora_location, submission_id)
        response = fetch_response(submission_id)

        return Response(
            {
                "gradeData": submission["gradeData"],
                "response": response,
                "gradeStatus": submission["gradeStatus"],
                "lockStatus": submission["lockStatus"],
            }
        )


class SubmissionStatusFetchView(RetrieveAPIView):
    """Get a submission status, leaving out the response"""

    def get(self, request):
        ora_location = request.query_params[PARAM_ORA_LOCATION]
        submission_id = request.query_params[PRAM_SUBMISSION_ID]

        submission = fetch_submission(ora_location, submission_id)

        return Response(
            {
                "gradeStatus": submission["gradeStatus"],
                "lockStatus": submission["lockStatus"],
                "gradeData": submission["gradeData"],
            }
        )


class SubmissionLockView(APIView):
    """Lock a submission for grading"""

    def post(self, request):
        """Claim a submission lock, updating lock status"""
        ora_location = request.query_params[PARAM_ORA_LOCATION]
        submission_id = request.query_params[PRAM_SUBMISSION_ID]

        submission = fetch_submission(ora_location, submission_id)
        submission["lockStatus"] = "in-progress"

        save_submission_update(ora_location, submission)

        return Response({"lockStatus": submission["lockStatus"]})

    def delete(self, request):
        """Delete a submission lock, updating lock status"""
        ora_location = request.query_params[PARAM_ORA_LOCATION]
        submission_id = request.query_params[PRAM_SUBMISSION_ID]

        submission = fetch_submission(ora_location, submission_id)
        submission["lockStatus"] = "unlocked"

        save_submission_update(ora_location, submission)

        return Response({"lockStatus": submission["lockStatus"]})


class UpdateGradeView(RetrieveAPIView):
    """Submit a grade"""

    def update_grade_data(self, submission, grade_data):
        """Mutate grade shape and add a mock score"""
        submission["gradeData"] = grade_data
        submission["gradeStatus"] = "graded"
        submission["lockStatus"] = "unlocked"
        submission["score"] = {
            "pointsEarned": 70,
            "pointsPossible": 100,
        }

    def post(self, request):
        """Save a grade update to the data store"""
        ora_location = request.query_params[PARAM_ORA_LOCATION]
        submission_id = request.query_params[PRAM_SUBMISSION_ID]
        grade_data = request.data

        # this is static test data
        grade_data["score"] = {"pointsEarned": 70, "pointsPossible": 100}

        submission = fetch_submission(ora_location, submission_id)
        self.update_grade_data(submission, grade_data)
        save_submission_update(ora_location, submission)
        return Response(
            {
                "gradeStatus": submission["gradeStatus"],
                "lockStatus": submission["lockStatus"],
                "gradeData": grade_data,
            }
        )
