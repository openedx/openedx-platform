"""
Serializers for Enhanced Staff Grader (ESG)
"""
# pylint: disable=abstract-method
# pylint: disable=missing-function-docstring

from urllib.parse import urljoin

from django.conf import settings
from rest_framework import serializers

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


class GradeStatusField(serializers.ChoiceField):
    """Field that can have the values ['graded' 'ungraded']"""

    def __init__(self, *args, **kwargs):
        kwargs["choices"] = ["graded", "ungraded"]
        super().__init__(*args, **kwargs)


class LockStatusField(serializers.ChoiceField):
    """Field that can have the values ['unlocked', 'locked', 'in-progress']"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, choices=["unlocked", "locked", "in-progress"])


class CourseMetadataSerializer(serializers.Serializer):
    """
    Serialize top-level info about a course, used for creating header in ESG
    """

    title = serializers.CharField(source="display_name")
    org = serializers.CharField(source="display_org_with_default")
    number = serializers.CharField(source="display_number_with_default")
    courseId = serializers.CharField(source="id")

    class Meta:
        model = CourseOverview

        fields = [
            "title",
            "org",
            "number",
            "courseId",
        ]
        read_only_fields = fields


class RubricCriterionOptionsSerializer(serializers.Serializer):
    """Serializer for selectable options in a rubric criterion"""

    label = serializers.CharField()
    points = serializers.IntegerField()
    explanation = serializers.CharField()
    name = serializers.CharField()
    orderNum = serializers.IntegerField(source="order_num")


class RubricCriterionSerializer(serializers.Serializer):
    """Serializer for individual criteria in a rubric"""

    label = serializers.CharField()
    prompt = serializers.CharField()
    feedback = serializers.ChoiceField(
        required=False, choices=["optional", "disabled", "required"], default="disabled"
    )
    name = serializers.CharField()
    orderNum = serializers.IntegerField(source="order_num")
    options = serializers.ListField(child=RubricCriterionOptionsSerializer())


class RubricConfigSerializer(serializers.Serializer):
    """Serializer for rubric config"""

    feedbackPrompt = serializers.CharField(source="rubric_feedback_prompt")
    criteria = serializers.ListField(
        source="rubric_criteria", child=RubricCriterionSerializer()
    )


class OpenResponseMetadataSerializer(serializers.Serializer):
    """
    Serialize ORA metadata, used for setting up views in ESG
    """

    name = serializers.CharField(source="display_name")
    prompts = serializers.ListField()
    type = serializers.SerializerMethodField()
    textResponseConfig = serializers.SerializerMethodField()
    textResponseEditor = serializers.CharField(source='text_response_editor')
    fileUploadResponseConfig = serializers.SerializerMethodField()
    rubricConfig = RubricConfigSerializer(source="*")

    def get_textResponseConfig(self, instance):
        return instance.text_response or "none"

    def get_fileUploadResponseConfig(self, instance):
        return instance.file_upload_response or "none"

    def get_type(self, instance):
        return "team" if instance.teams_enabled else "individual"

    class Meta:
        fields = [
            "name",
            "prompts",
            "type",
            "textResponseConfig",
            "textResponseEditor",
            "fileUploadResponseConfig",
            "rubricConfig",
        ]
        read_only_fields = fields


class ScoreField(serializers.Field):
    """Returns None if score is not given for a submission"""

    def to_representation(self, value):
        if ("pointsEarned" not in value) and ("pointsPossible" not in value):
            return None
        return ScoreSerializer(value).data


class ScoreSerializer(serializers.Serializer):
    """
    Score (points earned/possible) for use in SubmissionMetadataSerializer
    """

    pointsEarned = serializers.IntegerField(required=False)
    pointsPossible = serializers.IntegerField(required=False)


class SubmissionMetadataSerializer(serializers.Serializer):
    """
    Submission metadata for displaying submissions table in Enhanced Staff Grader (ESG)
    """

    submissionUUID = serializers.CharField(source="submissionUuid")
    username = serializers.CharField(allow_null=True)
    email = serializers.CharField(allow_null=True)
    fullname = serializers.CharField(allow_null=True)
    teamName = serializers.CharField(allow_null=True)
    dateSubmitted = serializers.DateTimeField()
    dateGraded = serializers.DateTimeField(allow_null=True)
    gradedBy = serializers.CharField(allow_null=True)
    gradeStatus = GradeStatusField(source="gradingStatus")
    lockStatus = LockStatusField()
    score = ScoreField()

    class Meta:
        fields = [
            "submissionUUID",
            "username",
            "teamName",
            "dateSubmitted",
            "dateGraded",
            "gradedBy",
            "gradeStatus",
            "lockStatus",
            "score",
            "email",
            "fullname",
        ]
        read_only_fields = fields


class AssessmentScoresSerializer(serializers.Serializer):
    """
    Serializer for score information associated with a specific assessment.

    This serializer is included in the `AssessmentSerializer` as a `ListField`
    """
    criterion_name = serializers.CharField()
    score_earned = serializers.IntegerField()
    score_type = serializers.CharField()

    class Meta:
        fields = [
            "criterion_name",
            "score_earned",
            "score_type",
        ]
        read_only_fields = fields


class AssessmentSerializer(serializers.Serializer):
    """
    Serializer for the each assessment metadata in the response from the assessments
    feedback endpoints (from/to) in Enhanced Staff Grader (ESG)

    This serializer is included in the `AssessmentFeedbackSerializer` as a `ListField`
    """
    assessment_id = serializers.CharField()
    scorer_name = serializers.CharField(allow_null=True)
    scorer_username = serializers.CharField(allow_null=True)
    scorer_email = serializers.CharField(allow_null=True)
    assessment_date = serializers.DateTimeField()
    assessment_scores = serializers.ListField(child=AssessmentScoresSerializer())
    problem_step = serializers.CharField(allow_null=True)
    feedback = serializers.CharField(allow_null=True)

    class Meta:
        fields = [
            "assessment_id",
            "scorer_name",
            "scorer_username",
            "scorer_email",
            "assessment_date",
            "assessment_scores",
            "problem_step",
            "feedback",
        ]
        read_only_fields = fields


class InitializeSerializer(serializers.Serializer):
    """
    Serialize info for the initialize call. Packages ORA, course, submission, and rubric data.
    """

    courseMetadata = CourseMetadataSerializer()
    oraMetadata = OpenResponseMetadataSerializer()
    submissions = serializers.DictField(child=SubmissionMetadataSerializer())
    isEnabled = serializers.SerializerMethodField()

    class Meta:
        fields = [
            "courseMetadata",
            "oraMetadata",
            "submissions",
            "isEnabled"
        ]
        read_only_fields = fields

    def get_isEnabled(self, obj):
        """
        Only enable ESG if the flag is enabled and also this is not a Team ORA
        Revert back to BooleanField in AU-617 when ESG officially supports team ORAs
        """
        return obj['isEnabled'] and not obj['oraMetadata'].teams_enabled


class AssessmentFeedbackSerializer(serializers.Serializer):
    """
    Serializer for a list of assessments for the response from the assessments
    feedback endpoints (from/to) in Enhanced Staff Grader (ESG)
    """

    assessments = serializers.ListField(child=AssessmentSerializer())

    class Meta:
        fields = ["assessments"]
        read_only_fields = fields


class UploadedFileSerializer(serializers.Serializer):
    """Serializer for a file uploaded as a part of a response"""

    downloadUrl = serializers.SerializerMethodField(method_name="get_download_url")
    description = serializers.CharField()
    name = serializers.CharField()
    size = serializers.IntegerField()

    def get_download_url(self, obj):
        """
        Get the representation for SerializerMethodField `downloadUrl`
        """
        if not obj.get("download_url"):
            return ""

        return urljoin(settings.LMS_ROOT_URL, obj.get("download_url"))


class ResponseSerializer(serializers.Serializer):
    """Serializer for the responseData api construct, which represents the contents of a submitted learner response"""

    files = serializers.ListField(child=UploadedFileSerializer(), allow_empty=True)
    text = serializers.ListField(child=serializers.CharField(), allow_empty=True)


class FileListSerializer(serializers.Serializer):
    """Serializer for a list of files in a submission"""

    files = serializers.ListField(child=UploadedFileSerializer(), allow_empty=True)


class AssessmentCriteriaSerializer(serializers.Serializer):
    """Serializer for information about a criterion, in the context of a completed assessment"""

    name = serializers.CharField()
    feedback = serializers.CharField()
    points = serializers.IntegerField()
    selectedOption = serializers.CharField(source="option")


class GradeDataSerializer(serializers.Serializer):
    """Serializer for the `gradeData` api construct, which represents a completed staff assessment"""

    score = ScoreField(required=False)
    overallFeedback = serializers.CharField(source="feedback", required=False)
    criteria = serializers.ListField(
        child=AssessmentCriteriaSerializer(), allow_empty=True, required=False
    )


class SubmissionStatusFetchSerializer(serializers.Serializer):
    """Serializer for the response from the submission status fetch endpoint"""

    gradeData = GradeDataSerializer(source="assessment_info")
    gradeStatus = serializers.SerializerMethodField()
    lockStatus = LockStatusField(source="lock_info.lock_status")

    def get_gradeStatus(self, obj):
        if not obj.get("assessment_info", {}) == {}:
            return "graded"
        else:
            return "ungraded"


class SubmissionFetchSerializer(SubmissionStatusFetchSerializer):
    """
    Serializer for the response from the submission fetch endpoint
    Same as the SubmissionStatusFetchSerializer with an added submission_info field
    """

    response = ResponseSerializer(source="submission_info")


class LockStatusSerializer(serializers.Serializer):
    """
    Info about the status of a submission lock, with extra metadata stripped out.
    """

    lockStatus = LockStatusField(source="lock_status")

    class Meta:
        fields = ["lockStatus"]
        read_only_fields = fields


class StaffAssessSerializer(serializers.Serializer):
    """
    Converts grade data to the format used for doing staff assessments

    From: {
        "overallFeedback": "was pretty good",
        "criteria": [
            {
                "name": "<criterion_name_1>",
                "feedback": (string),
                "selectedOption": <selected_option_name>
            }
        ]
    }

    To: {
        'options_selected': {
            '<criterion_name_1>': <selected_option_name>,
            '<criterion_name_2>': <selected_option_name>,
        },
        'criterion_feedback': {
            '<criterion_label_1>': (string)
        },
        'overall_feedback': (string)
        'submission_uuid': (string)
        'assess_type': (string) one of ['regrade', full-grade']
    }
    """

    # Context should include 'submission_uuid' for serialization
    requires_context = True

    options_selected = serializers.SerializerMethodField()
    criterion_feedback = serializers.SerializerMethodField()
    overall_feedback = serializers.CharField(source="overallFeedback", allow_null=True)
    submission_uuid = serializers.SerializerMethodField()
    assess_type = serializers.CharField(default="full-grade")

    def get_options_selected(self, instance):
        options_selected = {}
        for criterion in instance.get("criteria"):
            if criterion["selectedOption"]:
                options_selected[criterion["name"]] = criterion["selectedOption"]

        return options_selected

    def get_criterion_feedback(self, instance):
        criterion_feedback = {}
        for criterion in instance.get("criteria"):
            if criterion.get("feedback"):
                criterion_feedback[criterion["name"]] = criterion["feedback"]

        return criterion_feedback

    def get_submission_uuid(self, instance):  # pylint: disable=unused-argument
        return self.context.get("submission_uuid")
