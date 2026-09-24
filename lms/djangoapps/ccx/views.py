"""
Views related to the Custom Courses feature.
"""
import csv
import datetime
import functools
import json
import logging

from ccx_keys.locator import CCXLocator
from django.contrib import messages
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from opaque_keys.edx.keys import CourseKey
from six import StringIO

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseCcxCoachRole
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.overrides import (
    get_override_for_ccx,
    override_field_for_ccx,
)
from lms.djangoapps.ccx.permissions import VIEW_CCX_COACH_DASHBOARD
from lms.djangoapps.ccx.utils import (
    assign_staff_role_to_ccx,
    ccx_course,
    ccx_students_enrolling_center,
    create_ccx_course,
    get_ccx_by_ccx_id,
    get_ccx_creation_dict,
    get_ccx_for_coach,
    get_ccx_schedule,
    get_enrollment_action_and_identifiers,
    save_ccx_schedule,
)
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor.enrollment import get_email_params
from lms.djangoapps.instructor.views.gradebook_api import get_grade_book_page
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import SignalHandler  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)
TODAY = datetime.datetime.today  # for patching in tests


def coach_dashboard(view):
    """
    View decorator which enforces that the user have the CCX coach role on the
    given course and goes ahead and translates the course_id from the Django
    route into a course object.
    """
    @functools.wraps(view)
    def wrapper(request, course_id):
        """
        Wraps the view function, performing access check, loading the course,
        and modifying the view's call signature.
        """
        course_key = CourseKey.from_string(course_id)
        ccx = None
        if isinstance(course_key, CCXLocator):
            ccx_id = course_key.ccx
            try:
                ccx = CustomCourseForEdX.objects.get(pk=ccx_id)
            except CustomCourseForEdX.DoesNotExist:
                raise Http404  # pylint: disable=raise-missing-from  # noqa: B904

        if ccx:
            course_key = ccx.course_id
        course = get_course_by_id(course_key, depth=None)

        if not course.enable_ccx:  # pylint: disable=no-else-raise
            raise Http404
        else:
            if bool(request.user.has_perm(VIEW_CCX_COACH_DASHBOARD, course)):
                # if user is staff or instructor then he can view ccx coach dashboard.
                return view(request, course, ccx)
            else:
                # if there is a ccx, we must validate that it is the ccx for this coach
                role = CourseCcxCoachRole(course_key)
                if not role.has_user(request.user):
                    return HttpResponseForbidden(_('You must be a CCX Coach to access this view.'))
                elif ccx is not None:
                    coach_ccx = get_ccx_by_ccx_id(course, request.user, ccx.id)
                    if coach_ccx is None:
                        return HttpResponseForbidden(
                            _('You must be the coach for this ccx to access this view')
                        )

        return view(request, course, ccx)
    return wrapper


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def dashboard(request, course, ccx=None):
    """
    Display the CCX Coach Dashboard.
    """
    # right now, we can only have one ccx per user and course
    # so, if no ccx is passed in, we can sefely redirect to that
    if ccx is None:
        ccx = get_ccx_for_coach(course, request.user)
        if ccx:
            url = reverse(
                'ccx_coach_dashboard',
                kwargs={'course_id': CCXLocator.from_course_locator(course.id, str(ccx.id))}
            )
            return redirect(url)

    context = {
        'course': course,
        'ccx': ccx,
    }
    context.update(get_ccx_creation_dict(course))

    if ccx:
        ccx_locator = CCXLocator.from_course_locator(course.id, str(ccx.id))
        # At this point we are done with verification that current user is ccx coach.
        assign_staff_role_to_ccx(ccx_locator, request.user, course.id)
        schedule = get_ccx_schedule(course, ccx)
        grading_policy = get_override_for_ccx(
            ccx, course, 'grading_policy', course.grading_policy)
        context['schedule'] = json.dumps(schedule, indent=4)
        context['save_url'] = reverse(
            'save_ccx', kwargs={'course_id': ccx_locator})
        context['ccx_members'] = CourseEnrollment.objects.filter(course_id=ccx_locator, is_active=True)
        context['gradebook_url'] = reverse(
            'ccx_gradebook', kwargs={'course_id': ccx_locator})
        context['grades_csv_url'] = reverse(
            'ccx_grades_csv', kwargs={'course_id': ccx_locator})
        context['grading_policy'] = json.dumps(grading_policy, indent=4)
        context['grading_policy_url'] = reverse(
            'ccx_set_grading_policy', kwargs={'course_id': ccx_locator})

        with ccx_course(ccx_locator) as course:  # pylint: disable=redefined-argument-from-local
            context['course'] = course

    else:
        context['create_ccx_url'] = reverse(
            'create_ccx', kwargs={'course_id': course.id})
    return render_to_response('ccx/coach_dashboard.html', context)


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def create_ccx(request, course, ccx=None):
    """
    Create a new CCX
    """
    name = request.POST.get('name')

    if hasattr(course, 'ccx_connector') and course.ccx_connector:
        # if ccx connector url is set in course settings then inform user that he can
        # only create ccx by using ccx connector url.
        context = get_ccx_creation_dict(course)
        messages.error(request, context['use_ccx_con_error_message'])
        return render_to_response('ccx/coach_dashboard.html', context)

    # prevent CCX objects from being created for deprecated course ids.
    if course.id.deprecated:
        messages.error(request, _(
            "You cannot create a CCX from a course using a deprecated id. "
            "Please create a rerun of this course in the studio to allow "
            "this action."))
        url = reverse('ccx_coach_dashboard', kwargs={'course_id': course.id})
        return redirect(url)

    ccx = create_ccx_course(course, request.user, name)

    ccx_id = CCXLocator.from_course_locator(course.id, str(ccx.id))
    url = reverse('ccx_coach_dashboard', kwargs={'course_id': ccx_id})

    return redirect(url)


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def save_ccx(request, course, ccx=None):
    """
    Save changes to CCX.
    """
    if not ccx:
        raise Http404

    schedule, policy = save_ccx_schedule(course, ccx, json.loads(request.body.decode('utf8')))

    return HttpResponse(  # pylint: disable=http-response-with-content-type-json, http-response-with-json-dumps
        json.dumps({
            'schedule': schedule,
            'grading_policy': json.dumps(policy, indent=4)}),
        content_type='application/json',
    )


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def set_grading_policy(request, course, ccx=None):
    """
    Set grading policy for the CCX.
    """
    if not ccx:
        raise Http404

    override_field_for_ccx(
        ccx, course, 'grading_policy', json.loads(request.POST['policy']))

    # using CCX object as sender here.
    responses = SignalHandler.course_published.send(
        sender=ccx,
        course_key=CCXLocator.from_course_locator(course.id, str(ccx.id))
    )
    for rec, response in responses:
        log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)

    url = reverse(
        'ccx_coach_dashboard',
        kwargs={'course_id': CCXLocator.from_course_locator(course.id, str(ccx.id))}
    )
    return redirect(url)


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def ccx_schedule(request, course, ccx=None):
    """
    get json representation of ccx schedule
    """
    if not ccx:
        raise Http404

    schedule = get_ccx_schedule(course, ccx)
    json_schedule = json.dumps(schedule, indent=4)
    return HttpResponse(json_schedule, content_type='application/json')  # pylint: disable=http-response-with-content-type-json


@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def ccx_students_management(request, course, ccx=None):
    """
    Manage the enrollment of the students in a CCX
    """
    if not ccx:
        raise Http404

    action, identifiers = get_enrollment_action_and_identifiers(request)
    email_students = 'email-students' in request.POST
    course_key = CCXLocator.from_course_locator(course.id, str(ccx.id))
    email_params = get_email_params(course, auto_enroll=True, course_key=course_key, display_name=ccx.display_name)

    errors = ccx_students_enrolling_center(action, identifiers, email_students, course_key, email_params, ccx.coach)

    for error_message in errors:
        messages.error(request, error_message)

    url = reverse('ccx_coach_dashboard', kwargs={'course_id': course_key})
    return redirect(url)


# Grades can potentially be written - if so, let grading manage the transaction.
@transaction.non_atomic_requests
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def ccx_gradebook(request, course, ccx=None):
    """
    Show the gradebook for this CCX.
    """
    if not ccx:
        raise Http404

    ccx_key = CCXLocator.from_course_locator(course.id, str(ccx.id))
    with ccx_course(ccx_key) as course:  # pylint: disable=redefined-argument-from-local
        student_info, page = get_grade_book_page(request, course, course_key=ccx_key)

        return render_to_response('courseware/gradebook.html', {
            'page': page,
            'page_url': reverse('ccx_gradebook', kwargs={'course_id': ccx_key}),
            'students': student_info,
            'course': course,
            'course_id': course.id,
            'staff_access': request.user.is_staff,
            'ordered_grades': sorted(
                list(course.grade_cutoffs.items()), key=lambda i: i[1], reverse=True),
        })


# Grades can potentially be written - if so, let grading manage the transaction.
@transaction.non_atomic_requests
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@coach_dashboard
def ccx_grades_csv(request, course, ccx=None):
    """
    Download grades as CSV.
    """
    if not ccx:
        raise Http404

    ccx_key = CCXLocator.from_course_locator(course.id, str(ccx.id))
    with ccx_course(ccx_key) as course:  # pylint: disable=redefined-argument-from-local

        enrolled_students = User.objects.filter(
            courseenrollment__course_id=ccx_key,
            courseenrollment__is_active=1
        ).order_by('username').select_related("profile")
        grades = CourseGradeFactory().iter(enrolled_students, course)

        header = None
        rows = []
        for student, course_grade, __ in grades:
            if course_grade:
                # We were able to successfully grade this student for this
                # course.
                if not header:
                    # Encode the header row in utf-8 encoding in case there are
                    # unicode characters
                    header = [section['label'] for section in course_grade.summary['section_breakdown']]
                    rows.append(["id", "email", "username", "grade"] + header)

                percents = {
                    section['label']: section.get('percent', 0.0)
                    for section in course_grade.summary['section_breakdown']
                    if 'label' in section
                }

                row_percents = [percents.get(label, 0.0) for label in header]
                rows.append([student.id, student.email.encode('utf-8'),
                             student.username.encode('utf-8'),
                             course_grade.percent] + row_percents)

        buf = StringIO()
        writer = csv.writer(buf)
        for row in rows:
            writer.writerow(row)

        response = HttpResponse(buf.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment'

        return response
