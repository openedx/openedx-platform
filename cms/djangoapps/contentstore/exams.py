"""
Code related to working with the exam service
"""

import logging

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from edx_rest_api_client.auth import SuppliedJwtAuth

from openedx.core.djangoapps.course_apps.toggles import exams_ida_enabled
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from .helpers import is_item_in_course_tree

log = logging.getLogger(__name__)
User = get_user_model()


def register_exams(course_key):
    """
    This is typically called on a course published signal. The course is examined for sequences
    that are marked as timed exams. Then these are registered with the exams service.
    Likewise, if formerly registered exams are not included in the payload they will
    be marked inactive by the exam service.
    """
    if not settings.FEATURES.get('ENABLE_SPECIAL_EXAMS') or not exams_ida_enabled(course_key):
        # if feature is not enabled then do a quick exit
        return

    store = modulestore()

    course = store.get_course(course_key)
    if course is None:
        raise ItemNotFoundError("Course {} does not exist", str(course_key))  # lint-amnesty, pylint: disable=raising-format-tuple

    # get all sequences, since they can be marked as timed/proctored exams
    _timed_exams = store.get_items(
        course_key,
        qualifiers={
            'category': 'sequential',
        },
        settings={
            'is_time_limited': True,
        }
    )

    # filter out any potential dangling sequences
    timed_exams = [
        timed_exam
        for timed_exam in _timed_exams
        if is_item_in_course_tree(timed_exam)
    ]

    exams_list = []
    locations = []
    for timed_exam in timed_exams:
        location = str(timed_exam.location)
        msg = (
            'Found {location} as an exam in course structure.'.format(
                location=location
            )
        )
        log.info(msg)
        locations.append(location)

        exam_type = get_exam_type(
            timed_exam.is_proctored_exam,
            timed_exam.is_practice_exam,
            timed_exam.is_onboarding_exam
        )

        # Exams in courses not using an LTI based proctoring provider should use the original definition of due_date
        # from contentstore/proctoring.py. These exams are powered by the edx-proctoring plugin and not the edx-exams
        # microservice.
        is_instructor_paced = not course.self_paced
        if course.proctoring_provider == 'lti_external':
            due_date_source = timed_exam.due if is_instructor_paced else course.end
        else:
            due_date_source = timed_exam.due if is_instructor_paced else None

        due_date = due_date_source.isoformat() if due_date_source else None

        exams_list.append({
            'course_id': str(course_key),
            'content_id': str(timed_exam.location),
            'exam_name': timed_exam.display_name,
            'time_limit_mins': timed_exam.default_time_limit_minutes,
            # If the subsection has no due date, then infer a due date from the course end date. This behavior is a
            # departure from the legacy register_exams function used by the edx-proctoring plugin because
            # edx-proctoring makes a direct call to edx-when API when computing an exam's due date.
            # By sending the course end date when registering exams, we can avoid calling to the platform from the
            # exam service. Also note that we no longer consider the pacing type of the course - this applies to both
            # self-paced and indstructor-paced courses. Therefore, this effectively opts out exams powered by edx-exams
            # from personalized learner schedules/relative dates.
            'due_date': due_date,
            'exam_type': exam_type,
            'is_active': True,
            'hide_after_due': timed_exam.hide_after_due,
            # backend is only required for continued edx-proctoring support
            'backend': course.proctoring_provider,
        })

    try:
        _patch_course_exams(exams_list, str(course_key))
        log.info(f'Successfully registered {locations} with exam service')
    # pylint: disable=broad-except
    except Exception as ex:
        log.exception('Failed to register exams with exam API', exc_info=True)
        raise ex


def get_exam_type(is_proctored, is_practice, is_onboarding):
    """
    Get the exam type string based on the proctored, practice and onboarding
    attributes.
    """
    if is_proctored:
        if is_onboarding:
            exam_type = 'onboarding'
        elif is_practice:
            exam_type = 'practice'
        else:
            exam_type = 'proctored'
    else:
        exam_type = 'timed'

    return exam_type


def _get_exams_api_client():
    """
    Returns an API client which can be used to make Exams API requests.
    """
    user = User.objects.get(username=settings.EXAMS_SERVICE_USERNAME)
    jwt = create_jwt_for_user(user)
    client = requests.Session()
    client.auth = SuppliedJwtAuth(jwt)

    return client


def _patch_course_exams(exams_list, course_id):
    """
    Make a PATCH request to update course exams
    """
    url = f'{settings.EXAMS_SERVICE_URL}/exams/course_id/{course_id}/'
    api_client = _get_exams_api_client()

    response = api_client.patch(url, json=exams_list)
    response.raise_for_status()
    response = response.json()
    return response
