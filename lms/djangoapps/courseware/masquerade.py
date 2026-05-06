'''
---------------------------------------- Masquerade ----------------------------------------
Allow course staff to see a student or staff view of courseware.
Which kind of view has been selected is stored in the session state.
'''

import logging
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from opaque_keys.edx.keys import CourseKey
from pytz import utc
from web_fragments.fragment import Fragment
from xblock.runtime import KeyValueStore

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.role_helpers import has_staff_roles
from common.djangoapps.util.json_request import JsonResponse, expect_json
from openedx.core.djangoapps.util.user_messages import PageLevelMessages
from openedx.core.djangolib.markup import HTML
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID, FULL_ACCESS, LIMITED_ACCESS
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.partitions.partitions import (
    ENROLLMENT_TRACK_PARTITION_ID,  # pylint: disable=wrong-import-order
    NoSuchUserPartitionGroupError,  # pylint: disable=wrong-import-order
)
from xmodule.partitions.partitions_service import (
    get_all_partitions_for_course,  # pylint: disable=wrong-import-order
)

log = logging.getLogger(__name__)

# The key used to store a user's course-level masquerade information in the Django session.
# The value is a dict from course keys to CourseMasquerade objects.
MASQUERADE_SETTINGS_KEY = 'masquerade_settings'

# The key used to store temporary XBlock field data in the Django session.  This is where field
# data is stored to avoid modifying the state of the user we are masquerading as.
MASQUERADE_DATA_KEY = 'masquerade_data'


class CourseMasquerade:
    """
    Masquerade settings for a particular course.
    """

    def __init__(self, course_key, role='student', user_partition_id=None, group_id=None, user_name=None):
        # All parameters to this function must be named identically to the corresponding attribute.
        # If you remove or rename an attribute, also update the __setstate__() method to migrate
        # old data from users' sessions.
        self.course_key = course_key
        self.role = role
        self.user_partition_id = user_partition_id
        self.group_id = group_id
        self.user_name = user_name

    def __setstate__(self, state):
        """
        Ensure that all attributes are initialised when unpickling CourseMasquerade objects.

        Users might still have CourseMasquerade objects from older versions of the code in their
        session.  These old objects might not have all attributes set, possibly resulting in
        AttributeErrors.
        """
        self.__init__(**state)

    def get_active_group_name(self, available):
        """
        Lookup the active group name, from available options

        Returns: the corresponding group name, if exists,
            else, return None
        """
        if not (self.group_id and self.user_partition_id):
            return None
        for group in available:
            if (
                self.group_id == group.get('group_id') and
                self.user_partition_id == group.get('user_partition_id')
            ):
                return group.get('name')
        return None


@method_decorator(login_required, name='dispatch')
class MasqueradeView(View):
    """
    Create an HTTP endpoint to manage masquerade settings
    """

    def get(self, request, course_key_string):
        """
        Retrieve data on the active and available masquerade options
        """
        course_key = CourseKey.from_string(course_key_string)
        is_staff = has_staff_roles(request.user, course_key)
        if not is_staff:
            return JsonResponse({
                'success': False,
            })
        masquerade_settings = request.session.get(MASQUERADE_SETTINGS_KEY, {})
        course = masquerade_settings.get(course_key, None)
        course = course or CourseMasquerade(
            course_key,
            role='staff',
            user_partition_id=None,
            group_id=None,
            user_name=None,
        )
        block = modulestore().get_course(course_key)
        partitions = get_all_partitions_for_course(block, active_only=True)
        data = {
            'success': True,
            'active': {
                'course_key': course_key_string,
                'group_id': course.group_id,
                'role': course.role,
                'user_name': course.user_name or None,
                'user_partition_id': course.user_partition_id,
            },
            'available': [
                {
                    'name': 'Staff',
                    'role': 'staff',
                },
            ],
        }
        if len(partitions) == 0:
            data['available'].append({
                'name': 'Learner',
                'role': 'student',
            })

        data['available'].append({
            'name': 'Specific Student...',
            'role': 'student',
            'user_name': course.user_name or '',
        })
        for partition in partitions:
            # "random" scheme implies a split_test content group, not a cohort
            # and masquerading only cares about user cohorts
            if partition.active and partition.scheme.name != "random":
                data['available'].extend([
                    {
                        'group_id': group.id,
                        'name': group.name,
                        'role': 'student',
                        'user_partition_id': partition.id,
                    }
                    for group in partition.groups
                ])
        data['active']['group_name'] = course.get_active_group_name(data['available'])
        return JsonResponse(data)

    @method_decorator(expect_json)
    def post(self, request, course_key_string):
        """
        Handle AJAX posts to update the current user's masquerade for the specified course.
        The masquerade settings are stored in the Django session as a dict from course keys
        to CourseMasquerade objects.
        """
        course_key = CourseKey.from_string(course_key_string)
        is_staff = has_staff_roles(request.user, course_key)
        if not is_staff:
            return JsonResponse({
                'success': False,
            })
        masquerade_settings = request.session.get(MASQUERADE_SETTINGS_KEY, {})
        request_json = request.json
        role = request_json.get('role', 'student')
        group_id = request_json.get('group_id', None)
        user_partition_id = request_json.get('user_partition_id', None) if group_id is not None else None
        user_name = request_json.get('user_name', None)
        found_user_name = None
        if user_name:
            users_in_course = CourseEnrollment.objects.users_enrolled_in(course_key)
            try:
                found_user_name = users_in_course.get(Q(email=user_name) | Q(username=user_name)).username
            except User.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': _(
                        'There is no user with the username or email address "{user_identifier}" '
                        'enrolled in this course.'
                    ).format(
                        user_identifier=user_name,
                    ),
                })
        masquerade_settings[course_key] = CourseMasquerade(
            course_key,
            role=role,
            user_partition_id=user_partition_id,
            group_id=group_id,
            user_name=found_user_name,
        )
        request.session[MASQUERADE_SETTINGS_KEY] = masquerade_settings
        return JsonResponse({'success': True})


def setup_masquerade(request, course_key, staff_access=False, reset_masquerade_data=False):
    """
    Sets up masquerading for the current user within the current request. The request's user is
    updated to have a 'masquerade_settings' attribute with the dict of all masqueraded settings if
    called from within a request context. The function then returns a pair (CourseMasquerade, User)
    with the masquerade settings for the specified course key or None if there isn't one, and the
    user we are masquerading as or request.user if masquerading as a specific user is not active.

    If the reset_masquerade_data flag is set, the field data stored in the session will be cleared.
    """
    if (
        request.user is None or
        not settings.FEATURES.get('ENABLE_MASQUERADE', False) or
        not staff_access
    ):
        return None, request.user
    if reset_masquerade_data:
        request.session.pop(MASQUERADE_DATA_KEY, None)
    masquerade_settings = request.session.setdefault(MASQUERADE_SETTINGS_KEY, {})
    # Store the masquerade settings on the user so it can be accessed without the request
    request.user.masquerade_settings = masquerade_settings
    course_masquerade = masquerade_settings.get(course_key, None)
    masquerade_user = None
    if course_masquerade and course_masquerade.user_name:
        try:
            masquerade_user = CourseEnrollment.objects.users_enrolled_in(course_key).get(
                username=course_masquerade.user_name
            )
        except User.DoesNotExist:
            # This can only happen if the user was unenrolled from the course since masquerading
            # was enabled.  We silently reset the masquerading configuration in this case.
            course_masquerade = None
            del masquerade_settings[course_key]
            request.session.modified = True
        else:
            # Store the masquerading settings on the masquerade_user as well, since this user will
            # be used in some places instead of request.user.
            masquerade_user.masquerade_settings = request.user.masquerade_settings
            masquerade_user.real_user = request.user
    return course_masquerade, masquerade_user or request.user


def get_course_masquerade(user, course_key):
    """
    Returns the masquerade for the current user for the specified course. If no masquerade has
    been installed, then a default no-op masquerade is returned.
    """
    masquerade_settings = getattr(user, 'masquerade_settings', {})
    return masquerade_settings.get(course_key, None)


def get_masquerade_role(user, course_key):
    """
    Returns the role that the user is masquerading as, or None if no masquerade is in effect.
    """
    course_masquerade = get_course_masquerade(user, course_key)
    return course_masquerade.role if course_masquerade else None


def _get_masquerade_group_id(target_user_partition_id, user, course_key, course_masquerade=None):
    """
    Return the masqueraded track's group ID
    if it's in the specified user partition,
    otherwise, return None
    """
    course_masquerade = course_masquerade or get_course_masquerade(user, course_key)
    if course_masquerade is not None:
        user_partition_id = course_masquerade.user_partition_id
        if user_partition_id == target_user_partition_id:
            group_id = course_masquerade.group_id
            if group_id:
                return group_id
    return None


def is_masquerading(user, course_key, course_masquerade=None):
    """
    Return if the user is masquerading at all
    """
    course_masquerade = course_masquerade or get_course_masquerade(user, course_key)
    _is_masquerading = course_masquerade is not None
    return _is_masquerading


def is_masquerading_as_non_audit_enrollment(user, course_key, course_masquerade=None):
    """
    Return if the user is a staff member masquerading as a user
    in _any_ enrollment track _except_ audit
    """
    group_id = _get_masquerade_group_id(ENROLLMENT_TRACK_PARTITION_ID, user, course_key, course_masquerade)
    audit_mode_id = settings.COURSE_ENROLLMENT_MODES.get(CourseMode.AUDIT, {}).get('id')
    if group_id is not None:
        if group_id != audit_mode_id:
            return True
    return False


def is_masquerading_as_audit_enrollment(user, course_key, course_masquerade=None):
    """
    Return if the user is a staff member masquerading as a user
    in the audit enrollment track
    """
    group_id = _get_masquerade_group_id(ENROLLMENT_TRACK_PARTITION_ID, user, course_key, course_masquerade)
    audit_mode_id = settings.COURSE_ENROLLMENT_MODES.get(CourseMode.AUDIT, {}).get('id')
    _is_masquerading = group_id == audit_mode_id
    return _is_masquerading


def is_masquerading_as_full_access(user, course_key, course_masquerade=None):
    """
    Return if the user is a staff member masquerading as a user
    in the Full-Access track
    """
    group_id = _get_masquerade_group_id(CONTENT_GATING_PARTITION_ID, user, course_key, course_masquerade)
    _is_masquerading = group_id == FULL_ACCESS.id
    return _is_masquerading


def is_masquerading_as_limited_access(user, course_key, course_masquerade=None):
    """
    Return if the user is a staff member masquerading as a user
    in the Limited-Access track
    """
    group_id = _get_masquerade_group_id(CONTENT_GATING_PARTITION_ID, user, course_key, course_masquerade)
    _is_masquerading = group_id == LIMITED_ACCESS.id
    return _is_masquerading


def is_masquerading_as_staff(user, course_key):
    """
    Return if the user is a staff member masquerading as user
    that is itself a staff user
    """
    return get_masquerade_role(user, course_key) == 'staff'


def is_masquerading_as_student(user, course_key):
    """
    Returns true if the user is a staff member masquerading as a student.
    """
    return get_masquerade_role(user, course_key) == 'student'


def is_masquerading_as_specific_student(user, course_key):
    """
    Returns whether the user is a staff member masquerading as a specific student.
    """
    course_masquerade = get_course_masquerade(user, course_key)
    return bool(course_masquerade and course_masquerade.user_name)


def get_specific_masquerading_user(user, course_key):
    """
    Return the specific user that a staff member is masquerading as, or None if they aren't.
    """
    course_masquerade = get_course_masquerade(user, course_key)
    is_specific_user = bool(course_masquerade and course_masquerade.user_name)
    if is_specific_user:
        return User.objects.get(username=course_masquerade.user_name)
    else:
        return None


def get_masquerading_user_group(course_key, user, user_partition):
    """
    If the current user is masquerading as a generic learner in a specific group, return that group.
    If the user is not masquerading as a group, then None is returned.
    """
    course_masquerade = get_course_masquerade(user, course_key)
    if course_masquerade:
        if course_masquerade.user_partition_id == user_partition.id and course_masquerade.group_id is not None:
            try:
                return user_partition.get_group(course_masquerade.group_id)
            except NoSuchUserPartitionGroupError:
                return None
    # The user is masquerading as a generic student or not masquerading as a group return None
    return None


def check_content_start_date_for_masquerade_user(course_key, user, request, course_start,
                                                 chapter_start=None, section_start=None):
    """
    Add a warning message if the masquerade user would not have access to this content
    due to the content start date being in the future.
    """
    now = datetime.now(utc)
    most_future_date = course_start
    if chapter_start and section_start:
        most_future_date = max(course_start, chapter_start, section_start)
    _is_masquerading = get_course_masquerade(user, course_key)
    if now < most_future_date and _is_masquerading:
        group_masquerade = is_masquerading_as_student(user, course_key)
        specific_student_masquerade = is_masquerading_as_specific_student(user, course_key)
        is_staff = has_staff_roles(user, course_key)
        if group_masquerade or (specific_student_masquerade and not is_staff):
            PageLevelMessages.register_warning_message(
                request,
                HTML(_('This user does not have access to this content because \
                        the content start date is in the future')),
                once_only=True
            )


# Sentinel object to mark deleted objects in the session cache
_DELETED_SENTINEL = object()


class MasqueradingKeyValueStore(KeyValueStore):
    """
    A `KeyValueStore` to avoid affecting the user state when masquerading.

    This `KeyValueStore` wraps an underlying `KeyValueStore`.  Reads are forwarded to the underlying
    store, but writes go to a Django session (or other dictionary-like object).
    """

    def __init__(self, kvs, session):  # pylint: disable=super-init-not-called
        """
        Arguments:
          kvs: The KeyValueStore to wrap.
          session: The Django session used to store temporary data in.
        """
        self.kvs = kvs
        self.session = session
        self.session_data = session.setdefault(MASQUERADE_DATA_KEY, {})

    def _serialize_key(self, key):
        """
        Convert the key of Type KeyValueStore.Key to a string.

        Keys are not JSON-serializable, so we can't use them as keys for the Django session.
        The implementation is taken from cms/djangoapps/contentstore/views/session_kv_store.py.
        """
        return repr(tuple(key))

    def get(self, key):
        key_str = self._serialize_key(key)
        try:
            value = self.session_data[key_str]
        except KeyError:
            return self.kvs.get(key)
        else:
            if value is _DELETED_SENTINEL:
                raise KeyError(key_str)
            return value

    def set(self, key, value):
        self.session_data[self._serialize_key(key)] = value
        self.session.modified = True

    def delete(self, key):
        # We can't simply delete the key from the session, since it might still exist in the kvs,
        # which we are not allowed to modify, so we mark it as deleted by setting it to
        # _DELETED_SENTINEL in the session.
        self.set(key, _DELETED_SENTINEL)

    def has(self, key):
        try:
            value = self.session_data[self._serialize_key(key)]
        except KeyError:
            return self.kvs.has(key)
        else:
            return value != _DELETED_SENTINEL


def filter_displayed_blocks(block, unused_view, frag, unused_context):  # pylint: disable=unused-argument
    """
    A wrapper to only show XBlocks that set `show_in_read_only_mode` when masquerading as a specific user.

    We don't want to modify the state of the user we are masquerading as, so we can't show XBlocks
    that store information outside of the XBlock fields API.
    """
    if getattr(block, 'show_in_read_only_mode', False):
        return frag
    return Fragment(
        _('This type of component cannot be shown while viewing the course as a specific student.')
    )
