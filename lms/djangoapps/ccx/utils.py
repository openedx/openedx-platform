"""
CCX Enrollment operations for use by Coach APIs.

Does not include any access control, be sure to check access before calling.
"""


import datetime
import logging
from contextlib import contextmanager
from copy import deepcopy
from smtplib import SMTPException

import pytz
from ccx_keys.locator import CCXLocator
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.urls import reverse
from django.utils.translation import gettext as _

from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentException
from common.djangoapps.student.roles import CourseCcxCoachRole, CourseInstructorRole, CourseStaffRole
from lms.djangoapps.ccx.custom_exception import CCXUserValidationException
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.overrides import (
    bulk_delete_ccx_override_fields,
    clear_ccx_field_info_from_ccx_map,
    clear_override_for_ccx,
    get_override_for_ccx,
    override_field_for_ccx,
)
from lms.djangoapps.courseware.field_overrides import disable_overrides
from lms.djangoapps.instructor.access import allow_access, list_with_level, revoke_access
from lms.djangoapps.instructor.enrollment import enroll_email, get_email_params, unenroll_email
from lms.djangoapps.instructor.views.api import _split_input_list
from lms.djangoapps.instructor.views.tools import get_student_from_identifier
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_ADMINISTRATOR, assign_role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import SignalHandler

log = logging.getLogger("edx.ccx")
TODAY = datetime.datetime.today  # alias for patching in tests


def get_ccx_creation_dict(course):
    """
    Return dict of rendering create ccx form.

    Arguments:
        course (CourseBlockWithMixins): An edx course

    Returns:
        dict: A attribute dict for view rendering
    """
    context = {
        'course': course,
        'create_ccx_url': reverse('create_ccx', kwargs={'course_id': course.id}),
        'has_ccx_connector': "true" if hasattr(course, 'ccx_connector') and course.ccx_connector else "false",
        'use_ccx_con_error_message': _(
            "A CCX can only be created on this course through an external service."
            " Contact a course admin to give you access."
        )
    }
    return context


def get_ccx_from_ccx_locator(course_id):
    """ helper function to allow querying ccx fields from templates """
    ccx_id = getattr(course_id, 'ccx', None)
    ccx = None
    if ccx_id:
        ccx = CustomCourseForEdX.objects.filter(id=ccx_id)
    if not ccx:
        log.warning(
            "CCX does not exist for course with id %s",
            course_id
        )
        return None
    return ccx[0]


def get_date(ccx, node, date_type=None, parent_node=None):
    """
    This returns override or master date for section, subsection or a unit.

    :param ccx: ccx instance
    :param node: chapter, subsection or unit
    :param date_type: start or due
    :param parent_node: parent of node
    :return: start or due date
    """
    date = get_override_for_ccx(ccx, node, date_type, None)
    if date_type == "start":
        master_date = node.start
    else:
        master_date = node.due

    if date is not None:
        # Setting override date [start or due]
        date = date.strftime('%Y-%m-%d %H:%M')
    elif not parent_node and master_date is not None:
        # Setting date from master course
        date = master_date.strftime('%Y-%m-%d %H:%M')
    elif parent_node is not None:
        # Set parent date (vertical has same dates as subsections)
        date = get_date(ccx, node=parent_node, date_type=date_type)

    return date


def get_enrollment_action_and_identifiers(request):
    """
    Extracts action type and student identifiers from the request
    on Enrollment tab of CCX Dashboard.
    """
    action, identifiers = None, None
    student_action = request.POST.get('student-action', None)
    batch_action = request.POST.get('enrollment-button', None)

    if student_action:
        action = student_action
        student_id = request.POST.get('student-id', '')
        identifiers = [student_id]
    elif batch_action:
        action = batch_action
        identifiers_raw = request.POST.get('student-ids')
        identifiers = _split_input_list(identifiers_raw)

    return action, identifiers


def validate_date(year, month, day, hour, minute):
    """
    avoid corrupting db if bad dates come in
    """
    valid = True
    if year < 0:
        valid = False
    if month < 1 or month > 12:
        valid = False
    if day < 1 or day > 31:
        valid = False
    if hour < 0 or hour > 23:
        valid = False
    if minute < 0 or minute > 59:
        valid = False
    return valid


def parse_date(datestring):
    """
    Generate a UTC datetime.datetime object from a string of the form
    'YYYY-MM-DD HH:MM'.  If string is empty or `None`, returns `None`.
    """
    if datestring:
        date, time = datestring.split(' ')
        year, month, day = list(map(int, date.split('-')))
        hour, minute = list(map(int, time.split(':')))
        if validate_date(year, month, day, hour, minute):
            return datetime.datetime(
                year, month, day, hour, minute, tzinfo=pytz.UTC)

    return None


def get_ccx_for_coach(course, coach):
    """
    Looks to see if user is coach of a CCX for this course.  Returns the CCX or
    None.
    """
    ccxs = CustomCourseForEdX.objects.filter(
        course_id=course.id,
        coach=coach
    )
    # XXX: In the future, it would be nice to support more than one ccx per
    # coach per course.  This is a place where that might happen.
    if ccxs.exists():
        return ccxs[0]
    return None


def get_ccx_by_ccx_id(course, coach, ccx_id):
    """
    Finds a CCX of given coach on given master course.

    Arguments:
        course (CourseBlock): Master course
        coach (User): Coach to ccx
        ccx_id (long): Id of ccx

    Returns:
     ccx (CustomCourseForEdX): Instance of CCX.
    """
    try:
        ccx = CustomCourseForEdX.objects.get(
            id=ccx_id,
            course_id=course.id,
            coach=coach
        )
    except CustomCourseForEdX.DoesNotExist:
        return None

    return ccx


def get_valid_student_with_email(identifier):
    """
    Helper function to get an user email from an identifier and validate it.

    In the UI a Coach can enroll users using both an email and an username.
    This function takes care of:
    - in case the identifier is an username, extracting the user object from
        the DB and then the associated email
    - validating the email

    Arguments:
        identifier (str): Username or email of the user to enroll

    Returns:
        (tuple): tuple containing:
            email (str): A validated email for the user to enroll.
            user (User): A valid User object or None.

    Raises:
        CCXUserValidationException: if the username is not found or the email
            is not valid.
    """
    user = email = None
    try:
        user = get_student_from_identifier(identifier)
    except User.DoesNotExist:
        email = identifier
    else:
        email = user.email
    try:
        validate_email(email)
    except ValidationError:
        raise CCXUserValidationException(f'Could not find a user with name or email "{identifier}" ')  # pylint: disable=raise-missing-from  # noqa: B904
    return email, user


def ccx_students_enrolling_center(action, identifiers, email_students, course_key, email_params, coach):
    """
    Function to enroll or unenroll/revoke students.

    Arguments:
        action (str): type of action to perform (Enroll, Unenroll/revoke)
        identifiers (list): list of students username/email
        email_students (bool): Flag to send an email to students
        course_key (CCXLocator): a CCX course key
        email_params (dict): dictionary of settings for the email to be sent
        coach (User): ccx coach

    Returns:
        list: list of error
    """
    errors = []

    if action == 'Enroll':
        ccx_course_overview = CourseOverview.get_from_id(course_key)
        course_locator = course_key.to_course_locator()
        staff = CourseStaffRole(course_locator).users_with_role()
        admins = CourseInstructorRole(course_locator).users_with_role()

        for identifier in identifiers:
            must_enroll = False
            try:
                email, student = get_valid_student_with_email(identifier)
                if student:
                    must_enroll = student in staff or student in admins or student == coach
            except CCXUserValidationException as exp:
                log.info("%s", exp)
                errors.append(f"{exp}")
                continue

            if CourseEnrollment.objects.is_course_full(ccx_course_overview) and not must_enroll:
                error = _('The course is full: the limit is {max_student_enrollments_allowed}').format(
                    max_student_enrollments_allowed=ccx_course_overview.max_student_enrollments_allowed)
                log.info("%s", error)
                errors.append(error)
                break
            enroll_email(
                course_key,
                email,
                auto_enroll=True,
                message_students=email_students,
                message_params=email_params
            )
    elif action == 'Unenroll' or action == 'revoke':  # pylint: disable=consider-using-in
        for identifier in identifiers:
            try:
                email, __ = get_valid_student_with_email(identifier)
            except CCXUserValidationException as exp:
                log.info("%s", exp)
                errors.append(f"{exp}")
                continue
            unenroll_email(course_key, email, message_students=email_students, message_params=email_params)
    return errors


@contextmanager
def ccx_course(ccx_locator):
    """Create a context in which the course identified by course_locator exists
    """
    course = get_course_by_id(ccx_locator)
    yield course


def assign_staff_role_to_ccx(ccx_locator, user, master_course_id):
    """
    Check if user has ccx_coach role on master course then assign them staff role on ccx only
    if role is not already assigned. Because of this coach can open dashboard from master course
    as well as ccx.
    :param ccx_locator: CCX key
    :param user: User to whom we want to assign role.
    :param master_course_id: Master course key
    """
    coach_role_on_master_course = CourseCcxCoachRole(master_course_id)
    # check if user has coach role on master course
    if coach_role_on_master_course.has_user(user):
        # Check if user has staff role on ccx.
        role = CourseStaffRole(ccx_locator)
        if not role.has_user(user):
            # assign user the staff role on ccx
            with ccx_course(ccx_locator) as course:
                allow_access(course, user, "staff", send_email=False)


def is_email(identifier):
    """
    Checks if an `identifier` string is a valid email
    """
    try:
        validate_email(identifier)
    except ValidationError:
        return False
    return True


def add_master_course_staff_to_ccx(master_course, ccx_key, display_name, send_email=True):
    """
    Add staff and instructor roles on ccx to all the staff and instructors members of master course.

    Arguments:
        master_course (CourseBlockWithMixins): Master course instance.
        ccx_key (CCXLocator): CCX course key.
        display_name (str): ccx display name for email.
        send_email (bool): flag to switch on or off email to the users on access grant.

    """
    list_staff = list_with_level(master_course.id, 'staff')
    list_instructor = list_with_level(master_course.id, 'instructor')

    with ccx_course(ccx_key) as course_ccx:
        email_params = get_email_params(course_ccx, auto_enroll=True, course_key=ccx_key, display_name=display_name)
        list_staff_ccx = list_with_level(course_ccx.id, 'staff')
        list_instructor_ccx = list_with_level(course_ccx.id, 'instructor')
        for staff in list_staff:
            # this call should be idempotent
            if staff not in list_staff_ccx:
                try:
                    # Enroll the staff in the ccx
                    enroll_email(
                        course_id=ccx_key,
                        student_email=staff.email,
                        auto_enroll=True,
                        message_students=send_email,
                        message_params=email_params,
                    )

                    # allow 'staff' access on ccx to staff of master course
                    allow_access(course_ccx, staff, 'staff')
                except CourseEnrollmentException:
                    log.warning(
                        "Unable to enroll staff %s to course with id %s",
                        staff.email,
                        ccx_key
                    )
                    continue
                except SMTPException:
                    continue

        for instructor in list_instructor:
            # this call should be idempotent
            if instructor not in list_instructor_ccx:
                try:
                    # Enroll the instructor in the ccx
                    enroll_email(
                        course_id=ccx_key,
                        student_email=instructor.email,
                        auto_enroll=True,
                        message_students=send_email,
                        message_params=email_params,
                    )

                    # allow 'instructor' access on ccx to instructor of master course
                    allow_access(course_ccx, instructor, 'instructor')
                except CourseEnrollmentException:
                    log.warning(
                        "Unable to enroll instructor %s to course with id %s",
                        instructor.email,
                        ccx_key
                    )
                    continue
                except SMTPException:
                    continue


def remove_master_course_staff_from_ccx(master_course, ccx_key, display_name, send_email=True):
    """
    Remove staff and instructor roles on ccx to all the staff and instructors members of master course.

    Arguments:
        master_course (CourseBlockWithMixins): Master course instance.
        ccx_key (CCXLocator): CCX course key.
        display_name (str): ccx display name for email.
        send_email (bool): flag to switch on or off email to the users on revoke access.

    """
    list_staff = list_with_level(master_course.id, 'staff')
    list_instructor = list_with_level(master_course.id, 'instructor')

    with ccx_course(ccx_key) as course_ccx:
        list_staff_ccx = list_with_level(course_ccx.id, 'staff')
        list_instructor_ccx = list_with_level(course_ccx.id, 'instructor')
        email_params = get_email_params(course_ccx, auto_enroll=True, course_key=ccx_key, display_name=display_name)
        for staff in list_staff:
            if staff in list_staff_ccx:
                # revoke 'staff' access on ccx.
                revoke_access(course_ccx, staff, 'staff')

                # Unenroll the staff on ccx.
                unenroll_email(
                    course_id=ccx_key,
                    student_email=staff.email,
                    message_students=send_email,
                    message_params=email_params,
                )

        for instructor in list_instructor:
            if instructor in list_instructor_ccx:
                # revoke 'instructor' access on ccx.
                revoke_access(course_ccx, instructor, 'instructor')

                # Unenroll the instructor on ccx.
                unenroll_email(
                    course_id=ccx_key,
                    student_email=instructor.email,
                    message_students=send_email,
                    message_params=email_params,
                )


def create_ccx_course(course, coach, display_name):
    """
    Create and persist a CustomCourseForEdX for `course` owned by `coach`.

    Arguments:
        course (CourseBlock): the master course the CCX is derived from.
        coach (User): the user who will own/coach the CCX.
        display_name (str): the CCX display name.

    Returns:
        CustomCourseForEdX: the newly created CCX instance.
    """
    ccx = CustomCourseForEdX(
        course_id=course.id,
        coach=coach,
        display_name=display_name,
    )
    ccx.save()

    # Make sure start/due are overridden for entire course
    start = TODAY().replace(tzinfo=pytz.UTC)
    override_field_for_ccx(ccx, course, 'start', start)
    override_field_for_ccx(ccx, course, 'due', None)

    # Enforce a static limit for the maximum number of students that can be enrolled
    override_field_for_ccx(ccx, course, 'max_student_enrollments_allowed', settings.CCX_MAX_STUDENTS_ALLOWED)
    # Save display name explicitly
    override_field_for_ccx(ccx, course, 'display_name', display_name)

    # Hide anything that can show up in the schedule
    hidden = 'visible_to_staff_only'
    for chapter in course.get_children():
        override_field_for_ccx(ccx, chapter, hidden, True)
        for sequential in chapter.get_children():
            override_field_for_ccx(ccx, sequential, hidden, True)
            for vertical in sequential.get_children():
                override_field_for_ccx(ccx, vertical, hidden, True)

    ccx_id = CCXLocator.from_course_locator(course.id, str(ccx.id))

    # Create forum roles
    seed_permissions_roles(ccx_id)
    # Assign administrator forum role to CCX coach
    assign_role(ccx_id, coach, FORUM_ROLE_ADMINISTRATOR)

    # Enroll the coach in the course
    email_params = get_email_params(course, auto_enroll=True, course_key=ccx_id, display_name=ccx.display_name)
    enroll_email(
        course_id=ccx_id,
        student_email=coach.email,
        auto_enroll=True,
        message_students=True,
        message_params=email_params,
    )

    assign_staff_role_to_ccx(ccx_id, coach, course.id)
    add_master_course_staff_to_ccx(course, ccx_id, ccx.display_name)

    # using CCX object as sender here.
    responses = SignalHandler.course_published.send(
        sender=ccx,
        course_key=CCXLocator.from_course_locator(course.id, str(ccx.id)),
    )
    for rec, response in responses:
        log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)

    return ccx


def get_ccx_schedule(course, ccx):
    """
    Generate a JSON serializable CCX schedule.

    Visits student-visible nodes only; children of hidden nodes are skipped.
    Dates are converted to strings for the JS date widgets. Only start dates
    apply to sections; subsections have both start and due; units inherit their
    subsection's dates when not overridden.
    """
    def visit(node, depth=1):
        """
        Recursive generator function which yields CCX schedule nodes.
        """
        for child in node.get_children():
            # in case the children are visible to staff only, skip them
            if child.visible_to_staff_only:
                continue

            hidden = get_override_for_ccx(
                ccx, child, 'visible_to_staff_only',
                child.visible_to_staff_only)

            start = get_date(ccx, child, 'start')
            if depth > 1:
                # Subsection has both start and due dates and unit inherit dates from their subsections
                if depth == 2:
                    due = get_date(ccx, child, 'due')
                elif depth == 3:
                    # Get start and due date of subsection in case unit has not override dates.
                    due = get_date(ccx, child, 'due', node)
                    start = get_date(ccx, child, 'start', node)

                visited = {
                    'location': str(child.location),
                    'display_name': child.display_name,
                    'category': child.category,
                    'start': start,
                    'due': due,
                    'hidden': hidden,
                }
            else:
                visited = {
                    'location': str(child.location),
                    'display_name': child.display_name,
                    'category': child.category,
                    'start': start,
                    'hidden': hidden,
                }
            if depth < 3:
                children = tuple(visit(child, depth + 1))
                if children:
                    visited['children'] = children
                    yield visited
            else:
                yield visited

    with disable_overrides():
        return tuple(visit(course))


def save_ccx_schedule(course, ccx, schedule):  # pylint: disable=too-many-statements
    """
    Apply an edited CCX `schedule` tree to the CCX and republish it.

    Recursively overrides the `visible_to_staff_only`, `start` and `due`
    fields for units in the course from the supplied schedule data, adjusts the
    grading policy's `min_count` values when graded sections were hidden, and
    fires the `course_published` signal.

    This is the shared logic behind the legacy `save_ccx` view and the CCX
    Coach v2 save-schedule endpoint. Callers are responsible for access control.

    Arguments:
        course (CourseBlock): the master course.
        ccx (CustomCourseForEdX): the CCX being edited.
        schedule (list): the schedule tree (list of block dicts with
            `location`, `hidden`, `start`, optional `due` and
            `children`).

    Returns:
        tuple: `(schedule, grading_policy)` where `schedule` is the
        regenerated schedule (see :func:`get_ccx_schedule`) and
        `grading_policy` is the (possibly adjusted) grading policy dict.
    """
    def override_fields(parent, data, graded, earliest=None, ccx_ids_to_delete=None):
        """
        Recursively apply CCX schedule data to CCX by overriding the
        `visible_to_staff_only`, `start` and `due` fields for units in the
        course.
        """
        if ccx_ids_to_delete is None:
            ccx_ids_to_delete = []
        blocks = {
            str(child.location): child
            for child in parent.get_children()}

        for unit in data:
            block = blocks[unit['location']]
            override_field_for_ccx(
                ccx, block, 'visible_to_staff_only', unit['hidden'])

            start = parse_date(unit['start'])
            if start:
                if not earliest or start < earliest:
                    earliest = start
                override_field_for_ccx(ccx, block, 'start', start)
            else:
                ccx_ids_to_delete.append(get_override_for_ccx(ccx, block, 'start_id'))
                clear_ccx_field_info_from_ccx_map(ccx, block, 'start')

            # Only subsection (aka sequential) and unit (aka vertical) have due dates.
            if 'due' in unit:  # checking that the key (due) exist in dict (unit).
                due = parse_date(unit['due'])
                if due:
                    override_field_for_ccx(ccx, block, 'due', due)
                else:
                    ccx_ids_to_delete.append(get_override_for_ccx(ccx, block, 'due_id'))
                    clear_ccx_field_info_from_ccx_map(ccx, block, 'due')
            else:
                # In case of section aka chapter we do not have due date.
                ccx_ids_to_delete.append(get_override_for_ccx(ccx, block, 'due_id'))
                clear_ccx_field_info_from_ccx_map(ccx, block, 'due')

            if not unit['hidden'] and block.graded:
                graded[block.format] = graded.get(block.format, 0) + 1

            children = unit.get('children', None)
            # For a vertical, override start and due dates of all its problems.
            if unit.get('category', None) == 'vertical':
                for component in block.get_children():
                    # override start and due date of problem (Copy dates of vertical into problems)
                    if start:
                        override_field_for_ccx(ccx, component, 'start', start)

                    if due:
                        override_field_for_ccx(ccx, component, 'due', due)

            if children:
                override_fields(block, children, graded, earliest, ccx_ids_to_delete)
        return earliest, ccx_ids_to_delete

    graded = {}
    earliest, ccx_ids_to_delete = override_fields(course, schedule, graded, [])
    bulk_delete_ccx_override_fields(ccx, ccx_ids_to_delete)
    if earliest:
        override_field_for_ccx(ccx, course, 'start', earliest)

    # Attempt to automatically adjust grading policy
    changed = False
    policy = get_override_for_ccx(
        ccx, course, 'grading_policy', course.grading_policy
    )
    policy = deepcopy(policy)
    grader = policy['GRADER']
    for section in grader:
        count = graded.get(section.get('type'), 0)
        if count < section.get('min_count', 0):
            changed = True
            section['min_count'] = count
    if changed:
        override_field_for_ccx(ccx, course, 'grading_policy', policy)

    # using CCX object as sender here.
    responses = SignalHandler.course_published.send(
        sender=ccx,
        course_key=CCXLocator.from_course_locator(course.id, str(ccx.id))
    )
    for rec, response in responses:
        log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)

    return get_ccx_schedule(course, ccx), policy


def remove_block_from_ccx_schedule(ccx, course, location):
    """
    Remove a block (and its descendants) from the CCX schedule.

    Hides the block identified by `location` and all of its descendants from
    learners (`visible_to_staff_only=True`) and clears any CCX start/due date
    overrides on them, then republishes the CCX. This is the inverse of adding
    a block to the schedule and mirrors what the legacy `save_ccx` flow does
    when a unit is hidden.

    Arguments:
        ccx (CustomCourseForEdX): the CCX being edited.
        course (CourseBlock): the master course.
        location (str): the usage-key string of the block to remove.

    Returns:
        list: the regenerated schedule (see :func:`get_ccx_schedule`).

    Raises:
        ValueError: if `location` does not identify a block in the course.
    """
    def find_block(node):
        """Depth-first search for the block whose location matches `location`."""
        for child in node.get_children():
            if str(child.location) == location:
                return child
            found = find_block(child)
            if found is not None:
                return found
        return None

    def hide(block):
        """Hide `block` and its descendants and clear their date overrides."""
        override_field_for_ccx(ccx, block, 'visible_to_staff_only', True)
        clear_override_for_ccx(ccx, block, 'start')
        clear_override_for_ccx(ccx, block, 'due')
        for child in block.get_children():
            hide(child)

    block = find_block(course)
    if block is None:
        raise ValueError(f'Block "{location}" is not part of course "{course.id}"')

    hide(block)

    # using CCX object as sender here.
    responses = SignalHandler.course_published.send(
        sender=ccx,
        course_key=CCXLocator.from_course_locator(course.id, str(ccx.id))
    )
    for rec, response in responses:
        log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)

    return get_ccx_schedule(course, ccx)
