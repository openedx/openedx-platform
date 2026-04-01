"""
Celery task for course enrollment email
"""
import logging
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.track import segment
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.helpers import (
    get_course_dates_for_email,
    get_instructors,
)
from lms.djangoapps.utils import get_email_client
from openedx.core.djangoapps.catalog.utils import (
    get_course_uuid_for_course,
    get_owners_for_course,
    get_course_run_details,
)
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.course_experience import ENABLE_COURSE_GOALS

User = get_user_model()
log = logging.getLogger(__name__)

MAX_RETRIES = 3


@shared_task(bind=True, ignore_result=True)
@set_code_owner_attribute
def send_course_enrollment_email(
    self, user_id, course_id, course_title, short_description, course_ended, pacing_type, track_mode
):
    """
    Send course enrollment email using Braze API.

    Email is configured as Braze canvas message. We get the canvas properties for the
    email from course discovery service.
    In case the course run call to discovery fails, we use the course details sent
    to the celery task in our email.
    """
    course_date_blocks, course_key, is_course_run_missing, is_course_date_missing = [], None, False, False
    course_run_fields = [
        "key",
        "title",
        "short_description",
        "marketing_url",
        "pacing_type",
        "min_effort",
        "max_effort",
        "weeks_to_complete",
        "enrollment_count",
        "image",
        "staff",
    ]
    canvas_entry_properties = {
        "course_title": course_title,
        "short_description": short_description,
        "pacing_type": pacing_type,
        "course_run_key": course_id,
        "course_price": CourseMode.min_course_price_for_currency(
            course_id=course_id, currency="USD"
        ),
        "lms_base_url": configuration_helpers.get_value(
            "LMS_ROOT_URL", settings.LMS_ROOT_URL
        ),
        "learning_base_url": configuration_helpers.get_value(
            "LEARNING_MICROFRONTEND_URL",
            settings.LEARNING_MICROFRONTEND_URL,
        ),
        "track_mode": track_mode
    }

    try:
        user = User.objects.get(id=user_id)
        course_key = CourseKey.from_string(course_id)
        if not course_ended:
            course_date_blocks = get_course_dates_for_email(user, course_key, request=None)
    except Exception as err:  # pylint: disable=broad-except
        log.exception(f'[Enrollment email] Failed to get course dates with error: {err}')
        is_course_date_missing = True

    canvas_entry_properties.update(
        {
            "course_date_blocks": course_date_blocks,
            "goals_enabled": ENABLE_COURSE_GOALS.is_enabled(course_key),
        }
    )

    try:
        course_uuid = get_course_uuid_for_course(course_id)
        if course_uuid is None:
            raise ValueError('Missing course_uuid')

        owners = get_owners_for_course(course_uuid=course_uuid)
        course_run = get_course_run_details(course_id, course_run_fields)
        if not course_run:
            raise ValueError('Missing course_run')

        marketing_root_url = settings.MKTG_URLS.get("ROOT")
        instructors = get_instructors(course_run, marketing_root_url)
        enrollment_count = int(course_run.get("enrollment_count")) if course_run.get("enrollment_count") else 0
        canvas_entry_properties.update(
            {
                "instructors": instructors,
                "instructors_count": "even" if len(instructors) % 2 == 0 else "odd",
                "min_effort": course_run.get("min_effort"),
                "max_effort": course_run.get("max_effort"),
                "weeks_to_complete": course_run.get("weeks_to_complete"),
                "learners_count": "{:,}".format(enrollment_count) if enrollment_count > 100 else "",
                "banner_image_url": course_run.get("image").get("src", "") if course_run.get("image") else "",
                "course_title": course_run.get("title"),
                "short_description": course_run.get("short_description"),
                "pacing_type": course_run.get("pacing_type"),
                "partner_image_url": owners[0].get("logo_image_url") if owners else "",
                "org_name": owners[0].get("name") if owners else "",
            }
        )
    except Exception as err:  # pylint: disable=broad-except
        is_course_run_missing = True
        log.warning(f"[Course Enrollment] Course run call failed for user: {user_id} course: {course_id} error: {err}")

    if is_course_run_missing or is_course_date_missing:
        segment_properties = {
            'course_key': course_id,
            'is_course_run_missing': is_course_run_missing,
            'is_course_date_missing': is_course_date_missing,
        }
        segment.track(user_id, 'edx.course.enrollment.email.missingdata', segment_properties)

    try:
        recipients = [{"external_user_id": user_id}]
        email_client = get_email_client()
        if email_client:
            email_client.send_canvas_message(
                canvas_id=settings.BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
                recipients=recipients,
                canvas_entry_properties=canvas_entry_properties,
            )
    except Exception as exc:  # pylint: disable=broad-except
        log.error(f"[Course Enrollment] Email sending failed with exception: {exc}")
        countdown = 60 * (self.request.retries + 1)
        raise self.retry(exc=exc, countdown=countdown, max_retries=MAX_RETRIES)
