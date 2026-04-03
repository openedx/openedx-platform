"""
Contains the logic to manage survey report model.
"""
import requests
from django.conf import settings
from django.forms.models import model_to_dict

from openedx.features.survey_report.models import (
    SURVEY_REPORT_ERROR,
    SURVEY_REPORT_GENERATED,
    SurveyReport,
    SurveyReportAnonymousSiteID,
    SurveyReportUpload,
)
from openedx.features.survey_report.queries import (
    get_course_enrollments,
    get_generated_certificates,
    get_recently_active_users,
    get_registered_learners,
    get_unique_courses_offered,
)

MAX_WEEKS_SINCE_LAST_LOGIN: int = 4


def get_report_data() -> dict:
    """ Get data from database to generate a new report."""
    courses_offered = get_unique_courses_offered()
    learners = get_recently_active_users(weeks=MAX_WEEKS_SINCE_LAST_LOGIN)
    registered_learners = get_registered_learners()
    certificates = get_generated_certificates()
    enrollments = get_course_enrollments()
    extra_data = settings.SURVEY_REPORT_EXTRA_DATA

    return {
        "courses_offered": courses_offered,
        "learners": learners,
        "registered_learners": registered_learners,
        "generated_certificates": certificates,
        "enrollments": enrollments,
        "extra_data": extra_data,
    }


def generate_report() -> None:
    """ Generate a report with relevant data."""
    if not settings.SURVEY_REPORT_ENABLE:
        raise Exception("Survey report generation is not enabled")
    data = {}
    survey_report = SurveyReport(**data)
    survey_report.save()

    try:
        data = get_report_data()
        data["state"] = SURVEY_REPORT_GENERATED
        update_report(survey_report.id, data)
        send_report_to_external_api(survey_report.id)
    except (Exception, ) as update_report_error:
        update_report(survey_report.id, {"state": SURVEY_REPORT_ERROR})
        raise Exception(update_report_error) from update_report_error
    return survey_report.id


def get_id() -> str:
    """ Generate id for the survey report."""
    if not settings.ANONYMOUS_SURVEY_REPORT:
        return settings.LMS_BASE
    return str(SurveyReportAnonymousSiteID.objects.get_or_create()[0].id)


def send_report_to_external_api(report_id: int) -> None:
    """
    Send a report to Openedx endpoint and save the response in the SurveyReportUpload model.

    endpoint: The value of the setting SURVEY_REPORT_ENDPOINT

    content_type: JSON

    payload:
    - courses_offered: Total number of active unique courses.
    - learner: Recently active users with login in some weeks.
    - registered_learners: Total number of users ever registered in the platform.
    - enrollments: Total number of active enrollments in the platform.
    - generated_certificates: Total number of generated certificates.
    - extra_data: Extra information that will be saved in the report, E.g: site_name, openedx-release.
    - created_at: Date when the report was generated, this date will send with format '%m-%d-%Y %H:%M:%S'
    """
    report = SurveyReport.objects.get(id=report_id)

    fields = [
        "courses_offered",
        "learners",
        "registered_learners",
        "generated_certificates",
        "enrollments",
    ]

    data = model_to_dict(report, fields=fields)
    data["id"] = get_id()
    data["extra_data"] = report.extra_data
    data["created_at"] = report.created_at.strftime("%m-%d-%Y %H:%M:%S")

    request = requests.post(settings.SURVEY_REPORT_ENDPOINT, json=data)

    request.raise_for_status()

    SurveyReportUpload.objects.create(
        report=report,
        status_code=request.status_code,
        request_details=request.content
    )


def update_report(survey_report_id: int, data: dict) -> None:
    SurveyReport.objects.filter(id=survey_report_id).update(**data)
