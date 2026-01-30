"""
Utility library for working with the edx-milestones app
"""
from django.conf import settings
from django.utils.translation import gettext as _
from edx_toggles.toggles import SettingToggle
from milestones import api as milestones_api
from milestones.exceptions import InvalidMilestoneRelationshipTypeException, InvalidUserException
from milestones.models import MilestoneRelationshipType
from milestones.services import MilestonesService
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.cache_utils import get_cache
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order


NAMESPACE_CHOICES = {
    'ENTRANCE_EXAM': 'entrance_exams'
}

REQUEST_CACHE_NAME = "milestones"

# TODO this should be moved to edx/edx-milestones
# .. toggle_name: MILESTONES_APP
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Enable the milestones application, which manages significant Course and/or Student events in
#   the Open edX platform. (see https://github.com/openedx/edx-milestones) Note that this feature is required to enable
#   course pre-requisites.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-11-21
ENABLE_MILESTONES_APP = SettingToggle("MILESTONES_APP", default=False, module_name=__name__)


def get_namespace_choices():
    """
    Return the enum to the caller
    """
    return NAMESPACE_CHOICES


def is_prerequisite_courses_enabled():
    """
    Returns boolean indicating prerequisite courses enabled system wide or not.
    """
    return settings.FEATURES.get('ENABLE_PREREQUISITE_COURSES') and ENABLE_MILESTONES_APP.is_enabled()


def add_prerequisite_course(course_key, prerequisite_course_key):
    """
    It would create a milestone, then it would set newly created
    milestones as requirement for course referred by `course_key`
    and it would set newly created milestone as fulfillment
    milestone for course referred by `prerequisite_course_key`.
    """
    if not is_prerequisite_courses_enabled():
        return None
    milestone_name = _('Course {course_id} requires {prerequisite_course_id}').format(
        course_id=str(course_key),
        prerequisite_course_id=str(prerequisite_course_key)
    )
    milestone = milestones_api.add_milestone({
        'name': milestone_name,
        'namespace': str(prerequisite_course_key),
        'description': _('System defined milestone'),
    })
    # add requirement course milestone
    milestones_api.add_course_milestone(course_key, 'requires', milestone)

    # add fulfillment course milestone
    milestones_api.add_course_milestone(prerequisite_course_key, 'fulfills', milestone)


def remove_prerequisite_course(course_key, milestone):
    """
    It would remove pre-requisite course milestone for course
    referred by `course_key`.
    """
    if not is_prerequisite_courses_enabled():
        return None
    milestones_api.remove_course_milestone(
        course_key,
        milestone,
    )


def set_prerequisite_courses(course_key, prerequisite_course_keys):
    """
    It would remove any existing requirement milestones for the given `course_key`
    and create new milestones for each pre-requisite course in `prerequisite_course_keys`.
    To only remove course milestones pass `course_key` and empty list or
    None as `prerequisite_course_keys` .
    """
    if not is_prerequisite_courses_enabled():
        return None
    #remove any existing requirement milestones with this pre-requisite course as requirement
    course_milestones = milestones_api.get_course_milestones(course_key=course_key, relationship="requires")
    if course_milestones:
        for milestone in course_milestones:
            remove_prerequisite_course(course_key, milestone)

    # add milestones if pre-requisite course is selected
    if prerequisite_course_keys:
        for prerequisite_course_key_string in prerequisite_course_keys:
            prerequisite_course_key = CourseKey.from_string(prerequisite_course_key_string)
            add_prerequisite_course(course_key, prerequisite_course_key)


def get_pre_requisite_courses_not_completed(user, enrolled_courses):
    """
    Makes a dict mapping courses to their unfulfilled milestones using the
    fulfillment API of the milestones app.

    Arguments:
        user (User): the user for whom we are checking prerequisites.
        enrolled_courses (CourseKey): a list of keys for the courses to be
            checked. The given user must be enrolled in all of these courses.

    Returns:
        dict[CourseKey: dict[
            'courses': list[dict['key': CourseKey, 'display': str]]
        ]]
        If a course has no incomplete prerequisites, it will be excluded from the
        dictionary.
    """
    if not is_prerequisite_courses_enabled():
        return {}

    pre_requisite_courses = {}

    for course_key in enrolled_courses:
        required_courses = []
        fulfillment_paths = milestones_api.get_course_milestones_fulfillment_paths(course_key, {'id': user.id})
        for __, milestone_value in fulfillment_paths.items():
            for key, value in milestone_value.items():
                if key == 'courses' and value:
                    for required_course in value:
                        required_course_key = CourseKey.from_string(required_course)
                        required_course_overview = CourseOverview.get_from_id(required_course_key)
                        required_courses.append({
                            'key': required_course_key,
                            'display': get_course_display_string(required_course_overview)
                        })
        # If there are required courses, add them to the result dict.
        if required_courses:
            pre_requisite_courses[course_key] = {'courses': required_courses}

    return pre_requisite_courses


def get_prerequisite_courses_display(course_block):
    """
    It would retrieve pre-requisite courses, make display strings
    and return list of dictionary with course key as 'key' field
    and course display name as `display` field.
    """
    pre_requisite_courses = []
    if is_prerequisite_courses_enabled() and course_block.pre_requisite_courses:
        for course_id in course_block.pre_requisite_courses:
            course_key = CourseKey.from_string(course_id)
            required_course_block = modulestore().get_course(course_key)
            prc = {
                'key': course_key,
                'display': get_course_display_string(required_course_block)
            }
            pre_requisite_courses.append(prc)
    return pre_requisite_courses


def get_course_display_string(block):
    """
    Returns a string to display for a course or course overview.

    Arguments:
        block (CourseBlock|CourseOverview): a course or course overview.
    """
    return ' '.join([
        block.display_org_with_default,
        block.display_number_with_default
    ])


def fulfill_course_milestone(course_key, user):
    """
    Marks the course specified by the given course_key as complete for the given user.
    If any other courses require this course as a prerequisite, their milestones will be appropriately updated.
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    try:
        course_milestones = milestones_api.get_course_milestones(course_key=course_key, relationship="fulfills")
    except InvalidMilestoneRelationshipTypeException:
        # we have not seeded milestone relationship types
        seed_milestone_relationship_types()
        course_milestones = milestones_api.get_course_milestones(course_key=course_key, relationship="fulfills")
    for milestone in course_milestones:
        milestones_api.add_user_milestone({'id': user.id}, milestone)


def remove_course_milestones(course_key, user, relationship):
    """
    Remove all user milestones for the course specified by course_key.
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    course_milestones = milestones_api.get_course_milestones(course_key=course_key, relationship=relationship)
    for milestone in course_milestones:
        milestones_api.remove_user_milestone({'id': user.id}, milestone)


def get_required_content(course_key, user):
    """
    Queries milestones subsystem to see if the specified course is gated on one or more milestones,
    and if those milestones can be fulfilled via completion of a particular course content module
    """
    required_content = []
    if ENABLE_MILESTONES_APP.is_enabled():
        course_run_id = str(course_key)

        if user.is_authenticated:
            # Get all of the outstanding milestones for this course, for this user
            try:

                milestone_paths = get_course_milestones_fulfillment_paths(
                    course_run_id,
                    serialize_user(user)
                )
            except InvalidMilestoneRelationshipTypeException:
                return required_content

            # For each outstanding milestone, see if this content is one of its fulfillment paths
            for path_key in milestone_paths:
                milestone_path = milestone_paths[path_key]
                if milestone_path.get('content') and len(milestone_path['content']):  # lint-amnesty, pylint: disable=len-as-condition
                    for content in milestone_path['content']:
                        required_content.append(content)
        else:
            if get_course_milestones(course_run_id):
                # NOTE (CCB): The initial version of anonymous courseware access is very simple. We avoid accidentally
                # exposing locked content by simply avoiding anonymous access altogether for courses runs with
                # milestones.
                raise InvalidUserException('Anonymous access is not allowed for course runs with milestones set.')

    return required_content


def milestones_achieved_by_user(user, namespace):
    """
    It would fetch list of milestones completed by user
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.get_user_milestones({'id': user.id}, namespace)


def is_valid_course_key(key):
    """
    validates course key. returns True if valid else False.
    """
    try:
        course_key = CourseKey.from_string(key)
    except InvalidKeyError:
        course_key = key
    return isinstance(course_key, CourseKey)


def seed_milestone_relationship_types():
    """
    Helper method to pre-populate MRTs so the tests can run
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    MilestoneRelationshipType.objects.create(name='requires')
    MilestoneRelationshipType.objects.create(name='fulfills')


def generate_milestone_namespace(namespace, course_key=None):
    """
    Returns a specifically-formatted namespace string for the specified type
    """
    if namespace in list(NAMESPACE_CHOICES.values()):
        if namespace == 'entrance_exams':
            return '{}.{}'.format(str(course_key), NAMESPACE_CHOICES['ENTRANCE_EXAM'])


def serialize_user(user):
    """
    Returns a milestones-friendly representation of a user object
    """
    return {
        'id': user.id,
    }


def add_milestone(milestone_data):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.add_milestone(milestone_data)


def get_milestones(namespace):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return []
    return milestones_api.get_milestones(namespace)


def get_milestone_relationship_types():
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return {}
    return milestones_api.get_milestone_relationship_types()


def add_course_milestone(course_id, relationship, milestone):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.add_course_milestone(course_id, relationship, milestone)


def get_course_milestones(course_id):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return []
    return milestones_api.get_course_milestones(course_id)


def add_course_content_milestone(course_id, content_id, relationship, milestone):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.add_course_content_milestone(course_id, content_id, relationship, milestone)


def get_course_content_milestones(course_id, content_id=None, relationship='requires', user_id=None):
    """
    Client API operation adapter/wrapper
    Uses the request cache to store all of a user's
    milestones

    Returns all content blocks in a course if content_id is None, otherwise it just returns that
    specific content block.
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return []

    if user_id is None:
        return milestones_api.get_course_content_milestones(course_id, content_id, relationship)

    request_cache_dict = get_cache(REQUEST_CACHE_NAME)
    if user_id not in request_cache_dict:
        request_cache_dict[user_id] = {}

    if relationship not in request_cache_dict[user_id]:
        request_cache_dict[user_id][relationship] = milestones_api.get_course_content_milestones(
            course_key=course_id,
            relationship=relationship,
            user={"id": user_id}
        )

    if content_id is None:
        return request_cache_dict[user_id][relationship]

    return [m for m in request_cache_dict[user_id][relationship] if m['content_id'] == str(content_id)]


def remove_course_content_user_milestones(course_key, content_key, user, relationship):
    """
    Removes the specified User-Milestone link from the system for the specified course content module.
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return []

    course_content_milestones = milestones_api.get_course_content_milestones(course_key, content_key, relationship)
    for milestone in course_content_milestones:
        milestones_api.remove_user_milestone({'id': user.id}, milestone)


def remove_content_references(content_id):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.remove_content_references(content_id)


def any_unfulfilled_milestones(course_id, user_id):
    """ Returns a boolean if user has any unfulfilled milestones """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return False

    user_id = None if user_id is None else int(user_id)
    fulfillment_paths = milestones_api.get_course_milestones_fulfillment_paths(course_id, {'id': user_id})

    # Returns True if any of the milestones is unfulfilled. False if
    # values is empty or all values are.
    return any(fulfillment_paths.values())


def get_course_milestones_fulfillment_paths(course_id, user_id):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.get_course_milestones_fulfillment_paths(
        course_id,
        user_id
    )


def add_user_milestone(user, milestone):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.add_user_milestone(user, milestone)


def remove_user_milestone(user, milestone):
    """
    Client API operation adapter/wrapper
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return milestones_api.remove_user_milestone(user, milestone)


def get_service():
    """
    Returns MilestonesService instance if feature flag enabled;
    else returns None.

    Note: MilestonesService only has access to the functions
    explicitly requested in the MilestonesServices class
    """
    if not ENABLE_MILESTONES_APP.is_enabled():
        return None
    return MilestonesService()
