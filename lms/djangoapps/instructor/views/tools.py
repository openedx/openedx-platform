"""
Tools for the instructor dashboard
"""


import json
import operator

import dateutil
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.http import HttpResponseBadRequest
from django.utils.translation import gettext as _
from edx_when import api
from pytz import UTC

from common.djangoapps.student.models import CourseEnrollment, get_user_by_username_or_email
from openedx.core.djangoapps.schedules.models import Schedule


class DashboardError(Exception):
    """
    Errors arising from use of the instructor dashboard.
    """
    def response(self):
        """
        Generate an instance of HttpResponseBadRequest for this error.
        """
        error = str(self)
        return HttpResponseBadRequest(json.dumps({'error': error}))


def handle_dashboard_error(view):
    """
    Decorator which adds seamless DashboardError handling to a view.  If a
    DashboardError is raised during view processing, an HttpResponseBadRequest
    is sent back to the client with JSON data about the error.
    """
    def wrapper(request, course_id):
        """
        Wrap the view.
        """
        try:
            return view(request, course_id=course_id)
        except DashboardError as error:
            return error.response()

    return wrapper


def strip_if_string(value):
    if isinstance(value, str):
        return value.strip()
    return value


def get_student_from_identifier(unique_student_identifier):
    """
    Gets a student object using either an email address or username.

    Returns the student object associated with `unique_student_identifier`

    Raises User.DoesNotExist if no user object can be found, the user was
    retired, or the user is in the process of being retired.

    DEPRECATED: use student.models.get_user_by_username_or_email instead.
    """
    return get_user_by_username_or_email(unique_student_identifier)


def require_student_from_identifier(unique_student_identifier):
    """
    Same as get_student_from_identifier() but will raise a DashboardError if
    the student does not exist.
    """
    try:
        return get_student_from_identifier(unique_student_identifier)
    except User.DoesNotExist:
        raise DashboardError(  # lint-amnesty, pylint: disable=raise-missing-from
            _("Could not find student matching identifier: {student_identifier}").format(
                student_identifier=unique_student_identifier
            )
        )


def parse_datetime(datestr):
    """
    Convert user input date string into an instance of `datetime.datetime` in
    UTC.
    """
    try:
        return dateutil.parser.parse(datestr).replace(tzinfo=UTC)
    except ValueError:
        raise DashboardError(_("Unable to parse date: ") + datestr)  # lint-amnesty, pylint: disable=raise-missing-from


def find_unit(course, url):
    """
    Finds the unit/block with the given url in the course tree and returns the unit.
    Raises DashboardError if no unit is found.
    """
    def find(node, url):
        """
        Find node in course tree for url.
        """
        if str(node.location) == url:
            return node
        for child in node.get_children():
            found = find(child, url)
            if found:
                return found
        return None

    unit = find(course, url)
    if unit is None:
        raise DashboardError(_("Couldn't find module for url: {0}").format(url))
    return unit


def get_units_with_due_date(course):
    """
    Returns all top level units which have due dates.  Does not return
    descendents of those nodes.
    """
    units = []

    version = getattr(course, 'course_version', None)

    # Pass in a schedule here so that we get back any relative dates in the course, but actual value
    # doesn't matter, since we don't care about the dates themselves, just whether they exist.
    # Thus we don't save or care about this temporary schedule object.
    schedule = Schedule(start_date=course.start)
    course_dates = api.get_dates_for_course(course.id, schedule=schedule, published_version=version)

    def visit(node):
        """
        Visit a node.  Checks to see if node has a due date and appends to
        `units` if it does.  Otherwise recurses into children to search for
        nodes with due dates.
        """
        if (node.location, 'due') in course_dates:
            units.append(node)
        else:
            for child in node.get_children():
                visit(child)
    visit(course)
    #units.sort(key=_title_or_url)
    return units


def title_or_url(node):
    """
    Returns the `display_name` attribute of the passed in node of the course
    tree, if it has one.  Otherwise returns the node's url.
    """
    title = getattr(node, 'display_name', None)
    if not title:
        title = str(node.location)
    return title


def set_due_date_extension(course, unit, student, due_date, actor=None, reason=''):
    """
    Sets a due date extension.

    Raises:
        DashboardError if the unit or extended, due date is invalid or user is
        not enrolled in the course.
    """
    mode, __ = CourseEnrollment.enrollment_mode_for_user(user=student, course_id=str(course.id))
    if not mode:
        raise DashboardError(_("Could not find student enrollment in the course."))

    # We normally set dates at the subsection level. But technically dates can be anywhere down the tree (and
    # usually are in self paced courses, where the subsection date gets propagated down).
    # So find all children that we need to set the date on, then set those dates.
    version = getattr(course, 'course_version', None)
    course_dates = api.get_dates_for_course(course.id, user=student, published_version=version)
    blocks_to_set = {unit}  # always include the requested unit, even if it doesn't appear to have a due date now

    def visit(node):
        """
        Visit a node.  Checks to see if node has a due date and appends to
        `blocks_to_set` if it does.  And recurses into children to search for
        nodes with due dates.
        """
        if (node.location, 'due') in course_dates:
            blocks_to_set.add(node)
        for child in node.get_children():
            visit(child)
    visit(unit)

    for block in blocks_to_set:
        if due_date:
            try:
                api.set_date_for_block(
                    course.id, block.location, 'due', due_date, user=student, reason=reason, actor=actor
                )
            except api.MissingDateError as ex:
                raise DashboardError(_("Unit {0} has no due date to extend.").format(unit.location)) from ex
            except api.InvalidDateError as ex:
                raise DashboardError(_("An extended due date must be later than the original due date.")) from ex
        else:
            api.set_date_for_block(course.id, block.location, 'due', None, user=student, reason=reason, actor=actor)

    # edx-proctoring is checking cached course dates, so the overrides made above will not be enforced until the
    # TieredCache is reloaded. This can lead to situations when a student's extension is revoked, but they can still
    # complete an exam for some time. The instructors don't have a way of checking whether the cache has been
    # regenerated because the Instructor Dashboard simply lists all extensions. Therefore, to avoid having a confusing
    # user experience, we want trigger cache regeneration after changing the due date.
    api.get_dates_for_course(course.id, user=student, published_version=version, use_cached=False)
    if version:
        # edx-proctoring is not using the course version while checking its dates.
        api.get_dates_for_course(course.id, user=student, use_cached=False)


def dump_block_extensions(course, unit):
    """
    Dumps data about students with due date extensions for a particular block,
    specified by 'url', in a particular course.
    """
    header = [_("Username"), _("Full Name"), _("Extended Due Date")]
    data = []
    for username, fullname, due_date, *unused in api.get_overrides_for_block(course.id, unit.location):
        due_date = due_date.strftime('%Y-%m-%d %H:%M')
        data.append(dict(list(zip(header, (username, fullname, due_date)))))
    data.sort(key=operator.itemgetter(_("Username")))
    return {
        "header": header,
        "title": _("Users with due date extensions for {0}").format(
            title_or_url(unit)),
        "data": data
    }


def dump_student_extensions(course, student):
    """
    Dumps data about the due date extensions granted for a particular student
    in a particular course.
    """
    data = []
    header = [_("Unit"), _("Extended Due Date")]
    units = get_units_with_due_date(course)
    units = {u.location: u for u in units}
    query = api.get_overrides_for_user(course.id, student)
    for override in query:
        location = override['location'].replace(course_key=course.id)
        if location not in units:
            continue
        due = override['actual_date']
        due = due.strftime("%Y-%m-%d %H:%M")
        title = title_or_url(units[location])
        data.append(dict(list(zip(header, (title, due)))))
    data.sort(key=operator.itemgetter(_("Unit")))
    return {
        "header": header,
        "title": _("Due date extensions for {0} {1} ({2})").format(
            student.first_name, student.last_name, student.username),
        "data": data}


def keep_field_private(query_features, field_name):
    '''
    Utility to remove a field from a list of field names requested of a report
    Keeps the specified field_name private (excluded from report)
    '''
    if (query_features is None) or (field_name is None):
        raise DashboardError("Missing private field specification")

    try:
        query_features.remove(field_name)
    except ValueError:
        pass
