"""Generates common contexts"""


import logging

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, LearningContextKey

from openedx.core.lib.request_utils import COURSE_REGEX

log = logging.getLogger(__name__)


def course_context_from_url(url, course_id_string=None):
    """
    If course_id_string string is not present, extracts it from the given `url`. Either way, then passes
    it on to `course_context_from_course_id()`.
    """
    url = url or ''
    course_id = None

    if not course_id_string:
        match = COURSE_REGEX.match(url)
        if match:
            course_id_string = match.group('course_id')
    if not course_id_string:
        log.debug(
            'no course_id found in "{url}"'.format(
                url=str(url)[0:256]
            ),
            exc_info=True
        )
    else:
        try:
            course_id = CourseKey.from_string(course_id_string)
        except InvalidKeyError:
            log.warning(
                'unable to parse course_id "{course_id}"'.format(
                    course_id=str(course_id_string)[0:256]
                ),
                exc_info=True
            )

    return course_context_from_course_id(course_id)


def course_context_from_course_id(course_id):
    """
    Creates a course context from a `course_id`.

    For newer parts of the system (i.e. openedx_content-based libraries/courses/etc.)
    use context_dict_for_learning_context instead of this method.

    Example Returned Context::

        {
            'course_id': 'org/course/run',
            'org_id': 'org'
        }
    """
    context_dict = context_dict_for_learning_context(course_id)
    # Remove the newer 'context_id' field for now in this method so we're not
    # adding a new field to the course tracking logs
    del context_dict['context_id']
    return context_dict


def context_dict_for_learning_context(context_key):
    """
    Creates a tracking log context dictionary for the given learning context
    key, which may be None, a CourseKey, a content library key, or any other
    type of LearningContextKey.

    Example Returned Context Dict::

        {
            'context_id': 'course-v1:org+course+run',
            'course_id': 'course-v1:org+course+run',
            'org_id': 'org',
            'enterprise_uuid': 'enterprise_customer_uuid'
        }

    Example 2::

        {
            'context_id': 'lib:edX:a-content-library',
            'course_id': '',
            'org_id': 'edX',
            'enterprise_uuid': '1a0fbcbe-49e5-42f1-8e83-4cddfa592f22'
        }

    """
    context_dict = {
        'context_id': str(context_key) if context_key else '',
        'course_id': '',
        'org_id': '',
        'enterprise_uuid': '',
    }
    if context_key is not None:
        assert isinstance(context_key, LearningContextKey)
        if context_key.is_course:
            context_dict['course_id'] = str(context_key)
        if hasattr(context_key, 'org'):
            context_dict['org_id'] = context_key.org
        if hasattr(context_key, 'enterprise_uuid'):
            context_dict['enterprise_uuid'] = context_key.enterprise_uuid
    return context_dict
