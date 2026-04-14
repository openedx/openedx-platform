"""
Various utility methods used by support app views.
"""
import csv
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.program_enrollments.api import link_program_enrollments
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig


def get_course_duration_info(course_key):
    """
    Fetch course duration information from database.
    """
    try:
        key = CourseKey.from_string(course_key)
        course = CourseOverview.objects.values('display_name').get(id=key)
        duration_config = CourseDurationLimitConfig.current(course_key=key)
        gating_config = ContentTypeGatingConfig.current(course_key=key)
        duration_enabled = CourseDurationLimitConfig.enabled_for_course(course_key=key)
        gating_enabled = ContentTypeGatingConfig.enabled_for_course(course_key=key)

        gating_dict = {
            'enabled': gating_enabled,
            'enabled_as_of': str(gating_config.enabled_as_of) if gating_config.enabled_as_of else 'N/A',
            'reason': gating_config.provenances['enabled'].value
        }
        duration_dict = {
            'enabled': duration_enabled,
            'enabled_as_of': str(duration_config.enabled_as_of) if duration_config.enabled_as_of else 'N/A',
            'reason': duration_config.provenances['enabled'].value
        }

        return {
            'course_id': course_key,
            'course_name': course.get('display_name'),
            'gating_config': gating_dict,
            'duration_config': duration_dict,
        }

    except (ObjectDoesNotExist, InvalidKeyError):
        return {}


def validate_and_link_program_enrollments(program_uuid_string, linkage_text):
    """
    Validate arguments, and if valid, call `link_program_enrollments`.

    Returns: (successes, errors)
        where successes and errors are both list[str]
    """
    if not (program_uuid_string and linkage_text):
        error = (
            "You must provide both a program uuid "
            "and a series of lines with the format "
            "'external_user_key,lms_username'."
        )
        return [], [error]
    try:
        program_uuid = UUID(program_uuid_string)
    except ValueError:
        return [], [
            f"Supplied program UUID '{program_uuid_string}' is not a valid UUID."
        ]
    reader = csv.DictReader(
        linkage_text.splitlines(), fieldnames=('external_key', 'username')
    )
    ext_key_to_username = {
        (item.get('external_key') or '').strip(): (item['username'] or '').strip()
        for item in reader
    }
    if not (all(ext_key_to_username.keys()) and all(ext_key_to_username.values())):  # lint-amnesty, pylint: disable=consider-iterating-dictionary
        return [], [
            "All linking lines must be in the format 'external_user_key,lms_username'"
        ]
    link_errors = link_program_enrollments(
        program_uuid, ext_key_to_username
    )
    successes = [
        str(item)
        for item in ext_key_to_username.items()
        if item[0] not in link_errors.keys()  # lint-amnesty, pylint: disable=consider-iterating-dictionary
    ]
    errors = [message for message in link_errors.values()]  # lint-amnesty, pylint: disable=unnecessary-comprehension
    return successes, errors
