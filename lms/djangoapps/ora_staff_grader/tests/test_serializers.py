"""
Tests for ESG Serializers
"""
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urljoin

import ddt
from django.conf import settings
from django.test import TestCase

from lms.djangoapps.ora_staff_grader.errors import ERR_UNKNOWN, ErrorSerializer
from lms.djangoapps.ora_staff_grader.serializers import (
    AssessmentCriteriaSerializer,
    CourseMetadataSerializer,
    FileListSerializer,
    GradeDataSerializer,
    InitializeSerializer,
    LockStatusField,
    LockStatusSerializer,
    OpenResponseMetadataSerializer,
    ResponseSerializer,
    RubricConfigSerializer,
    ScoreField,
    ScoreSerializer,
    StaffAssessSerializer,
    SubmissionFetchSerializer,
    SubmissionMetadataSerializer,
    SubmissionStatusFetchSerializer,
    UploadedFileSerializer,
)
from lms.djangoapps.ora_staff_grader.tests import test_data
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase


class TestErrorSerializer(TestCase):
    """Tests for error serialization"""

    def test_no_error_code(self):
        # If no error code is provided, fall back to an unknown code
        input_data = {}
        data = ErrorSerializer(input_data).data

        assert data == {"error": ERR_UNKNOWN}

    def test_no_context(self):
        # The serializer may return just the error info
        input_data = {"error": "ERR_CODE"}
        data = ErrorSerializer(input_data).data

        assert data == {"error": "ERR_CODE"}

    def test_added_context(self):
        # The serializer may also add context which gets unpacked into the output
        input_data = {"error": "ERR_CODE"}
        added_context = {"a": "b", "c": {"d": ["e", "f"]}}
        data = ErrorSerializer(input_data, context=added_context).data

        # Extra context should be added to the output
        assert data == {"error": "ERR_CODE", "a": "b", "c": {"d": ["e", "f"]}}


class TestCourseMetadataSerializer(SharedModuleStoreTestCase):
    """
    Tests for CourseMetadataSerializer
    """

    course_data = {
        "org": "Oxford",
        "display_name": "Introduction to Time Travel",
        "display_number_with_default": "TT101",
        "run": "2054",
    }

    course_id = "course-v1:Oxford+TT101+2054"

    def setUp(self):
        super().setUp()

        self.course_overview = CourseOverviewFactory.create(**self.course_data)

    def test_course_serialize(self):
        data = CourseMetadataSerializer(self.course_overview).data

        assert data == {
            "title": self.course_data["display_name"],
            "org": self.course_data["org"],
            "number": self.course_data["display_number_with_default"],
            "courseId": self.course_id,
        }


@ddt.ddt
class TestOpenResponseMetadataSerializer(TestCase):
    """
    Tests for OpenResponseMetadataSerializer
    """

    def setUp(self):
        super().setUp()

        self.ora_data = {
            "display_name": "Week 1: Time Travel Paradoxes",
            "prompts": [
                "<p>In your own words, explain a famous time travel paradox</p>"
            ],
            "teams_enabled": False,
            "text_response": None,
            "text_response_editor": "text",
            "file_upload_response": None,
            **test_data.example_rubric,
        }

        self.mock_ora_instance = Mock(name="openassessment-block", **self.ora_data)

    def test_individual_ora(self):
        # An ORA with teams disabled should have type "individual"
        data = OpenResponseMetadataSerializer(self.mock_ora_instance).data

        assert data == {
            "name": self.ora_data["display_name"],
            "prompts": self.ora_data["prompts"],
            "type": "individual",
            "textResponseConfig": "none",
            "textResponseEditor": "text",
            "fileUploadResponseConfig": "none",
            "rubricConfig": {
                "feedbackPrompt": "How did this student do?",
                "criteria": [
                    {
                        "orderNum": 0,
                        "name": "potions",
                        "label": "Potions",
                        "prompt": "How did this student perform in the Potions exam",
                        "feedback": "optional",
                        "options": test_data.example_rubric_options_serialized,
                    },
                    {
                        "orderNum": 1,
                        "name": "charms",
                        "label": "Charms",
                        "prompt": "How did this student perform in the Charms exam",
                        "feedback": "disabled",
                        "options": test_data.example_rubric_options_serialized,
                    },
                ],
            },
        }

    def test_team_ora(self):
        # An ORA with teams enabled should have type "team"
        self.mock_ora_instance.teams_enabled = True
        data = OpenResponseMetadataSerializer(self.mock_ora_instance).data

        assert data == {
            "name": self.ora_data["display_name"],
            "prompts": self.ora_data["prompts"],
            "type": "team",
            "textResponseConfig": "none",
            "textResponseEditor": "text",
            "fileUploadResponseConfig": "none",
            "rubricConfig": {
                "feedbackPrompt": "How did this student do?",
                "criteria": [
                    {
                        "orderNum": 0,
                        "name": "potions",
                        "label": "Potions",
                        "prompt": "How did this student perform in the Potions exam",
                        "feedback": "optional",
                        "options": test_data.example_rubric_options_serialized,
                    },
                    {
                        "orderNum": 1,
                        "name": "charms",
                        "label": "Charms",
                        "prompt": "How did this student perform in the Charms exam",
                        "feedback": "disabled",
                        "options": test_data.example_rubric_options_serialized,
                    },
                ],
            },
        }

    @ddt.unpack
    @ddt.data(("optional", "optional"), ("required", "required"))
    def test_response_config(self, text_response, file_upload_response):
        self.mock_ora_instance.text_response = text_response
        self.mock_ora_instance.file_upload_response = file_upload_response

        data = OpenResponseMetadataSerializer(self.mock_ora_instance).data

        assert data["textResponseConfig"] == text_response
        assert data["fileUploadResponseConfig"] == file_upload_response

    def test_response_config_none(self):
        self.mock_ora_instance.text_response = None
        self.mock_ora_instance.file_upload_response = None

        data = OpenResponseMetadataSerializer(self.mock_ora_instance).data

        assert data["textResponseConfig"] == "none"
        assert data["fileUploadResponseConfig"] == "none"

    @ddt.data("text", "tinymce")
    def test_text_response_editor_config(self, text_response_editor):
        # Currently allowed types are "text" and "tinymce" for student text responses.
        # Text is set as default
        self.mock_ora_instance.text_response_editor = text_response_editor

        data = OpenResponseMetadataSerializer(self.mock_ora_instance).data

        assert data["textResponseEditor"] == text_response_editor


class TestSubmissionMetadataSerializer(TestCase):
    """
    Tests for SubmissionMetadataSerializer. Implicitly, this also exercises ScoreSerializer.
    SubmissionMetadata comes from the ORA list_staff_workflows XBlock.json_handler and has the shape:
    "<submission_uuid>": {
        "submissionUuid": "<submission_uuid>",
        "username": "<username/empty>",
        "teamName": "<team_name/empty>",
        "dateSubmitted": "<yyyy-mm-dd HH:MM:SS>",
        "dateGraded": "<yyyy-mm-dd HH:MM:SS/None>",
        "gradedBy": "<username/empty>",
        "gradingStatus": "<ungraded/graded>",
        "lockStatus": "<locked/unlocked/in-progress>",
        "score": {
            "pointsEarned": <num>,
            "pointsPossible": <num>
        }
    }
    Right now, this is just passed through with only one name transform
    """

    submission_data = {
        "a": {
            "submissionUuid": "a",
            "username": "foo",
            "teamName": "",
            'email': "jondoes@example.com",
            'fullname': "",
            "dateSubmitted": "1969-07-16 13:32:00",
            "dateGraded": "None",
            "gradedBy": "",
            "gradingStatus": "ungraded",
            "lockStatus": "unlocked",
            "score": {"pointsEarned": 0, "pointsPossible": 10},
        },
        "b": {
            "submissionUuid": "b",
            "username": "",
            'email': "jondoes@example.com",
            'fullname': "Jhon Does",
            "teamName": "bar",
            "dateSubmitted": "1969-07-20 20:17:40",
            "dateGraded": "None",
            "gradedBy": "",
            "gradingStatus": "ungraded",
            "lockStatus": "in-progress",
            "score": {"pointsEarned": 0, "pointsPossible": 10},
        },
        "c": {
            "submissionUuid": "c",
            "username": "baz",
            'email': "jondoes@example.com",
            'fullname': "Jhon Does",
            "teamName": "",
            "dateSubmitted": "1969-07-21 21:35:00",
            "dateGraded": "1969-07-24 16:44:00",
            "gradedBy": "buz",
            "gradingStatus": "graded",
            "lockStatus": "unlocked",
            "score": {"pointsEarned": 9, "pointsPossible": 10},
        },
    }

    def test_submission_serialize(self):
        for submission_id, submission_data in self.submission_data.items():
            data = SubmissionMetadataSerializer(submission_data).data

            # For each submission, there are only a few transforms:
            # 1) "submissionUuid" to "submissionUUID"
            # 2) "gradingStatus" to "gradeStatus"
            # Create that "expected" object here by updating the key name
            expected_data = self.submission_data[submission_id].copy()
            expected_data["submissionUUID"] = expected_data.pop("submissionUuid")
            expected_data["gradeStatus"] = expected_data.pop("gradingStatus")

            assert data == expected_data

    def test_empty_score(self):
        """
        An empty score dict should be serialized as None
        """
        submission = {
            "submissionUuid": "empty-score",
            "username": "WOPR",
            'email': "jhondoes@example.com",
            'fullname': "",
            "dateSubmitted": "1983-06-03 00:00:00",
            "dateGraded": None,
            "gradedBy": None,
            "gradingStatus": "ungraded",
            "lockStatus": "unlocked",
            "score": {},
        }

        expected_output = {
            "submissionUUID": "empty-score",
            "username": "WOPR",
            'email': "jhondoes@example.com",
            'fullname': "",
            "teamName": None,
            "dateSubmitted": "1983-06-03 00:00:00",
            "dateGraded": None,
            "gradedBy": None,
            "gradeStatus": "ungraded",
            "lockStatus": "unlocked",
            "score": None,
        }

        data = SubmissionMetadataSerializer(submission).data

        assert data == expected_output


class TestInitializeSerializer(TestCase):
    """
    Tests for InitializeSerializer
    """

    def set_up_ora(self):
        """Create a mock Open Response Assessment for serialization"""
        ora_data = {
            "display_name": "Week 1: Time Travel Paradoxes",
            "prompts": [
                "<p>In your own words, explain a famous time travel paradox</p>"
            ],
            "teams_enabled": False,
        }

        # Add rubric data here for succinctness
        ora_data.update(test_data.example_rubric)
        return Mock(name="openassessment-block", **ora_data)

    def set_up_course_metadata(self):
        """Create mock course metadata for serialization"""
        course_org = "Oxford"
        course_name = "Introduction to Time Travel"
        course_number = "TT101"
        course_run = "2054"

        return CourseOverviewFactory.create(
            org=course_org,
            display_name=course_name,
            display_number_with_default=course_number,
            run=course_run,
        )

    def setUp(self):
        super().setUp()

        self.mock_ora_instance = self.set_up_ora()
        self.mock_course_metadata = self.set_up_course_metadata()
        self.mock_submissions_data = test_data.example_submission_list.copy()

    def test_serializer_output(self):
        input_data = {
            "courseMetadata": self.mock_course_metadata,
            "oraMetadata": self.mock_ora_instance,
            "submissions": self.mock_submissions_data,
            "isEnabled": True,
        }

        output_data = InitializeSerializer(input_data).data

        expected_course_data = CourseMetadataSerializer(self.mock_course_metadata).data
        expected_ora_data = OpenResponseMetadataSerializer(self.mock_ora_instance).data
        expected_submissions_data = {}

        # There's a level of unpacking that happens in the serializer, perform that here
        for submission_id, submission_data in self.mock_submissions_data.items():
            serialized_data = SubmissionMetadataSerializer(submission_data).data
            expected_submissions_data[submission_id] = serialized_data

        # Check that each of the sub-serializers assembles data correctly
        assert output_data["courseMetadata"] == expected_course_data
        assert output_data["oraMetadata"] == expected_ora_data
        assert output_data["submissions"] == expected_submissions_data
        assert output_data["isEnabled"] is True


class TestRubricConfigSerializer(TestCase):
    """Tests for RubricConfigSerializer"""

    def basic_test_case(self):
        """Basic test for complex rubric"""
        assert RubricConfigSerializer(test_data.example_rubric).data == {
            "feedbackPrompt": "How did this student do?",
            "criteria": [
                {
                    "orderNum": 0,
                    "name": "potions",
                    "label": "Potions",
                    "prompt": "How did this student perform in the Potions exam",
                    "feedback": "optional",
                    "options": test_data.example_rubric_options_serialized,
                },
                {
                    "order_num": 1,
                    "name": "charms",
                    "label": "Charms",
                    "prompt": "How did this student perform in the Charms exam",
                    "feedback": "disabled",
                    "options": test_data.example_rubric_options_serialized,
                },
            ],
        }


@ddt.ddt
class TestScoreFieldAndSerializer(TestCase):
    """Tests for ScoreField and ScoreSerializer"""

    def test_field_no_values(self):
        """An empty dict passed to the field should return None"""
        assert ScoreField().to_representation({}) is None

    @ddt.data("pointsEarned", "pointsPossible")
    def test_field_missing(self, missing_field):
        """Missing fields should just be ignored"""
        value = {"pointsEarned": 30, "pointsPossible": 50}
        del value[missing_field]

        assert ScoreField().to_representation(value) == value

    def test_field(self):
        """Base serialization behavior for ScoreField"""
        data = {"pointsEarned": 20, "pointsPossible": 40}
        representation = ScoreField().to_representation(data)
        assert representation == data

    def test_serializer_no_values(self):
        """Passing the ScoreSerializer an empty dict should result in an empty serializer"""
        # pylint: disable=use-implicit-booleaness-not-comparison
        assert ScoreSerializer({}).data == {}

    def test_serialier(self):
        """Base serialization behavior for ScoreSerializer"""
        input_data = {"pointsEarned": 10, "pointsPossible": 200}
        data = ScoreSerializer(input_data).data
        assert data == input_data

    @ddt.data("pointsEarned", "pointsPossible")
    def test_serializer_missing_field(self, missing_field):
        """Missing fields should just be ignored"""
        value = {"pointsEarned": 30, "pointsPossible": 50}
        del value[missing_field]

        assert ScoreSerializer(value).data == value


class TestUploadedFileSerializer(TestCase):
    """Tests for UploadedFileSerializer"""

    def test_uploaded_file_serializer(self):
        """Base serialization behavior"""
        input_data = {
            "download_url": "/test.txt",
            "description": "Test description",
            "name": "Test name",
            "size": 89111,
        }
        data = UploadedFileSerializer(input_data).data

        expected_value = {
            "downloadUrl": f'{settings.LMS_ROOT_URL}{input_data["download_url"]}',
            "description": input_data["description"],
            "name": input_data["name"],
            "size": input_data["size"],
        }
        assert data == expected_value

    def test_uploaded_file_serializer_with_full_url(self):
        """Test UploadedFileSerializer with a full download URL"""
        input_data = {
            "download_url": f"{settings.LMS_ROOT_URL}/test.txt",
            "description": "Test description",
            "name": "Test name",
            "size": 78222,
        }
        data = UploadedFileSerializer(input_data).data

        expected_value = {
            "downloadUrl": input_data["download_url"],
            "description": input_data["description"],
            "name": input_data["name"],
            "size": input_data["size"],
        }
        assert data == expected_value

    def test_no_download_url(self):
        """Test UploadedFileSerializer in the case where a URL was not found"""
        input_data = {
            "download_url": "",
            "description": "Test description",
            "name": "Test name",
            "size": 78222,
        }

        data = UploadedFileSerializer(input_data).data
        expected_value = {
            "downloadUrl": "",
            "description": input_data["description"],
            "name": input_data["name"],
            "size": input_data["size"],
        }
        assert data == expected_value


@ddt.ddt
class TestResponseSerializer(TestCase):
    """Tests for ResponseSerializer"""

    def test_response_serializer__empty(self):
        """Empty fields should be allowed"""
        input_data = {"files": [], "text": []}
        assert ResponseSerializer(input_data).data == input_data

    @ddt.unpack
    @ddt.data((True, True), (True, False), (False, True), (False, False))
    def test_response_serializer(self, has_text, has_files):
        """Base serialization behavior"""
        input_data = MagicMock()
        if has_files:
            input_data.files = [
                {"size": 111, "download_url": "/file1.txt", "description": Mock(), "name": Mock()},
                {"size": 222, "download_url": "/file2.txt", "description": Mock(), "name": Mock()},
                {"size": 333, "download_url": "/file3.txt", "description": Mock(), "name": Mock()},
            ]
        if has_text:
            input_data.text = [Mock(), Mock(), Mock()]

        data = ResponseSerializer(input_data).data
        expected_value = {
            "files": [
                UploadedFileSerializer(mock_file).data for mock_file in input_data.files
            ]
            if has_files
            else [],
            "text": [str(mock_text) for mock_text in input_data.text]
            if has_text
            else [],
        }
        assert data == expected_value


@ddt.ddt
class TestFileListSerializer(TestCase):
    """
    Tests for FileListSerializer - this is basically a stripped down ResponseSerializer
    """

    def test_file_list_serializer__empty(self):
        """Empty fields should be allowed"""
        input_data = {"files": [], "text": []}
        expected_output = {"files": []}
        assert FileListSerializer(input_data).data == expected_output

    def test_file_list_serializer(self):
        """Base serialization behavior"""
        input_data = {
            "files": [{
                "name": Mock(),
                "description": Mock(),
                "download_url": f"{settings.LMS_ROOT_URL}/test-1.png",
                "size": 12345,
            }, {
                "name": Mock(),
                "description": Mock(),
                "download_url": "/test-2.png",
                "size": 54321,
            }],
            "text": "",
        }

        output_data = FileListSerializer(input_data).data
        assert output_data.keys() == set(["files"])

        for i, input_file in enumerate(input_data["files"]):
            output_file = output_data["files"][i]
            assert output_file.keys() == set(["name", "description", "downloadUrl", "size"])

            assert output_file["name"] == str(input_file["name"])
            assert output_file["description"] == str(input_file["description"])
            assert output_file["downloadUrl"] == urljoin(settings.LMS_ROOT_URL, str(input_file["download_url"]))
            assert output_file["size"] == input_file["size"]


class TestAssessmentCriteriaSerializer(TestCase):
    """Tests for AssessmentCriteriaSerializer"""

    def test_assessment_criteria_serializer(self):
        """Base serialization behavior"""
        input_data = Mock(points=595)
        data = AssessmentCriteriaSerializer(input_data).data

        expected_value = {
            "name": str(input_data.name),
            "feedback": str(input_data.feedback),
            "points": input_data.points,
            "selectedOption": str(input_data.option),
        }
        assert data == expected_value

    def test_assessment_criteria_serializer__feedback_only(self):
        """Test for serialization behavior of a feedback-only criterion"""
        input_data = {
            "name": "SomeCriterion",
            "feedback": "Pathetic Effort",
            "points": None,
            "option": None,
        }
        data = AssessmentCriteriaSerializer(input_data).data

        expected_value = dict(input_data)
        expected_value["selectedOption"] = expected_value["option"]
        del expected_value["option"]

        assert data == expected_value


@ddt.ddt
class TestGradeDataSerializer(TestCase):
    """Tests for GradeDataSerializer"""

    def test_grade_data_serializer__no_assessment(self):
        """Passing an empty dict should result in an empty dict"""
        # pylint: disable=use-implicit-booleaness-not-comparison
        assert GradeDataSerializer({}).data == {}

    @ddt.data(True, False)
    def test_grade_data_serializer__assessment(self, has_criteria):
        """Base serialization behavior, with and without criteria"""
        input_data = MagicMock()
        if has_criteria:
            input_data.criteria = [Mock(points=123), Mock(points=11), Mock(points=22)]
        data = GradeDataSerializer(input_data).data

        expected_value = {
            "score": ScoreField().to_representation(input_data.score),
            "overallFeedback": str(input_data.feedback),
        }
        if has_criteria:
            expected_value["criteria"] = [
                AssessmentCriteriaSerializer(criterion).data
                for criterion in input_data.criteria
            ]
        else:
            expected_value["criteria"] = []
        assert data == expected_value


@ddt.ddt
class TestSubmissionStatusFetchSerializer(TestCase):
    """Tests for SubmissionStatusFetchSerializer"""

    def test_submission_status_fetch_serializer(self):
        """Base serialization behavior"""
        input_data = MagicMock()
        serializer = SubmissionStatusFetchSerializer(input_data)
        with patch.object(serializer, "get_gradeStatus") as mock_get_grade_status:
            data = serializer.data

        expected_value = {
            "gradeData": GradeDataSerializer(input_data.assessment_info).data,
            "gradeStatus": mock_get_grade_status.return_value,
            "lockStatus": LockStatusField().to_representation(
                input_data.lock_info.lock_status
            ),
        }
        mock_get_grade_status.assert_called_once_with(input_data)
        assert data == expected_value

    @ddt.data(True, False)
    def test_get__gradeStatus(self, has_assessment):
        """Unit test for get_gradeStatus"""
        assessment = {"somekey": "somevalue"} if has_assessment else {}
        input_data = {"assessment_info": assessment}
        value = SubmissionStatusFetchSerializer().get_gradeStatus(input_data)
        expected = "graded" if has_assessment else "ungraded"
        assert value == expected


class TestSubmissionFetchSerializer(TestCase):
    """Tests for the SubmissionFetchSerializer"""

    def test_submission_fetch_serializer(self):
        """Base serialization behavior"""
        input_data = MagicMock()
        serializer = SubmissionFetchSerializer(input_data)
        with patch.object(serializer, "get_gradeStatus") as mock_get_grade_status:
            data = serializer.data

        expected_value = {
            "gradeData": GradeDataSerializer(input_data.assessment_info).data,
            "gradeStatus": mock_get_grade_status.return_value,
            "lockStatus": LockStatusField().to_representation(
                input_data.lock_info.lock_status
            ),
            "response": ResponseSerializer(input_data.submission_info).data,
        }
        mock_get_grade_status.assert_called_once_with(input_data)
        assert data == expected_value


class TestLockStatusSerializer(SharedModuleStoreTestCase):
    """
    Tests for LockStatusSerializer
    """

    lock_in_progress = {
        "submission_uuid": "e34ef789-a4b1-48cf-b1bc-b3edacfd4eb2",
        "owner_id": "10ab03f1b75b4f9d9ab13a1fd1dccca1",
        "created_at": "2021-09-21T21:54:09.901221Z",
        "lock_status": "in-progress",
    }

    lock_in_progress_expected = {"lockStatus": "in-progress"}

    lock_owned_by_other_user = {
        "submission_uuid": "e34ef789-a4b1-48cf-b1bc-b3edacfd4eb2",
        "owner_id": "10ab03f1b75b4f9d9ab13a1fd1dccca1",
        "created_at": "2021-09-21T21:54:09.901221Z",
        "lock_status": "locked",
    }

    lock_owned_by_other_user_expected = {"lockStatus": "locked"}

    course_id = "course-v1:Oxford+TT101+2054"

    def test_happy_path(self):
        """For simple cases, lock status is passed through directly"""
        data = LockStatusSerializer(self.lock_in_progress).data
        assert data == self.lock_in_progress_expected

        data = LockStatusSerializer(self.lock_owned_by_other_user).data
        assert data == self.lock_owned_by_other_user_expected


class TestStaffAssessSerializer(TestCase):
    """Tests for StaffAssessSerializer"""

    grade_data = {
        "overallFeedback": "was pretty good",
        "criteria": [
            {
                "name": "firstCriterion",
                "feedback": "did alright",
                "selectedOption": "good",
            },
            {"name": "secondCriterion", "selectedOption": "fair"},
        ],
    }

    grade_data_no_feedback = {
        "overallFeedback": "",
        "criteria": [
            {"name": "firstCriterion", "selectedOption": "good"},
            {"name": "secondCriterion", "selectedOption": "fair"},
        ],
    }

    grade_data_no_selected_option = {
        "overallFeedback": "was pretty good",
        "criteria": [
            {"name": "firstCriterion", "selectedOption": None},
        ],
    }

    submission_uuid = "foo"

    def test_staff_assess_serializer(self):
        """Base serialization behavior"""
        context = {"submission_uuid": self.submission_uuid}
        serializer = StaffAssessSerializer(self.grade_data, context=context)

        expected_value = {
            "options_selected": {
                "firstCriterion": "good",
                "secondCriterion": "fair",
            },
            "criterion_feedback": {
                "firstCriterion": "did alright",
            },
            "overall_feedback": "was pretty good",
            "submission_uuid": self.submission_uuid,
            "assess_type": "full-grade",
        }

        assert serializer.data == expected_value

    def test_staff_assess_no_feedback(self):
        """Verify that empty feedback returns a reasonable shape"""
        context = {"submission_uuid": self.submission_uuid}
        serializer = StaffAssessSerializer(self.grade_data_no_feedback, context=context)

        expected_value = {
            "options_selected": {
                "firstCriterion": "good",
                "secondCriterion": "fair",
            },
            "criterion_feedback": {},
            "overall_feedback": "",
            "submission_uuid": self.submission_uuid,
            "assess_type": "full-grade",
        }

        assert serializer.data == expected_value

    def test_staff_assess_no_selected_option(self):
        """When selected option is None, it should be ignored"""
        context = {"submission_uuid": self.submission_uuid}
        serializer = StaffAssessSerializer(self.grade_data_no_selected_option, context=context)

        expected_value = {
            "options_selected": {},
            "criterion_feedback": {},
            "overall_feedback": "was pretty good",
            "submission_uuid": self.submission_uuid,
            "assess_type": "full-grade",
        }

        assert serializer.data == expected_value
