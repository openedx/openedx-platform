"""
Grade book view for instructor and pagination work (for grade book)
which is currently use by ccx and instructor apps.
"""


import math

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from django.urls import reverse
from django.views.decorators.cache import cache_control
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.edxmako.shortcuts import render_to_response
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor.views.api import require_course_permission
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .. import permissions

# Grade book: max students per page
MAX_STUDENTS_PER_PAGE_GRADE_BOOK = 20


def calculate_page_info(offset, total_students):
    """
    Takes care of sanitizing the offset of current page also calculates offsets for next and previous page
    and information like total number of pages and current page number.

    :param offset: offset for database query
    :return: tuple consist of page number, query offset for next and previous pages and valid offset
    """

    # validate offset.
    if not (isinstance(offset, int) or offset.isdigit()) or int(offset) < 0 or int(offset) >= total_students:
        offset = 0
    else:
        offset = int(offset)

    # calculate offsets for next and previous pages.
    next_offset = offset + MAX_STUDENTS_PER_PAGE_GRADE_BOOK
    previous_offset = offset - MAX_STUDENTS_PER_PAGE_GRADE_BOOK

    # calculate current page number.
    page_num = ((offset / MAX_STUDENTS_PER_PAGE_GRADE_BOOK) + 1)

    # calculate total number of pages.
    total_pages = int(math.ceil(float(total_students) / MAX_STUDENTS_PER_PAGE_GRADE_BOOK)) or 1

    if previous_offset < 0 or offset == 0:
        # We are at first page, so there's no previous page.
        previous_offset = None

    if next_offset >= total_students:
        # We've reached the last page, so there's no next page.
        next_offset = None

    return {
        "previous_offset": previous_offset,
        "next_offset": next_offset,
        "page_num": page_num,
        "offset": offset,
        "total_pages": total_pages
    }


def get_grade_book_page(request, course, course_key):
    """
    Get student records per page along with page information i.e current page, total pages and
    offset information.
    """
    # Unsanitized offset
    current_offset = request.GET.get('offset', 0)
    enrolled_students = User.objects.filter(
        courseenrollment__course_id=course_key,
        courseenrollment__is_active=1
    ).order_by('username').select_related("profile")

    total_students = enrolled_students.count()
    page = calculate_page_info(current_offset, total_students)
    offset = page["offset"]
    total_pages = page["total_pages"]

    if total_pages > 1:
        # Apply limit on queryset only if total number of students are greater then MAX_STUDENTS_PER_PAGE_GRADE_BOOK.
        enrolled_students = enrolled_students[offset: offset + MAX_STUDENTS_PER_PAGE_GRADE_BOOK]

    with modulestore().bulk_operations(course.location.course_key):
        student_info = [
            {
                'username': student.username,
                'id': student.id,
                'email': student.email,
                'grade_summary': CourseGradeFactory().read(student, course).summary
            }
            for student in enrolled_students
        ]
    return student_info, page


@transaction.non_atomic_requests
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.OVERRIDE_GRADES)
def spoc_gradebook(request, course_id):
    """
    Show the gradebook for this course:
    - Only shown for courses with enrollment < settings.FEATURES.get("MAX_ENROLLMENT_INSTR_BUTTONS")
    - Only displayed to course staff
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'staff', course_key, depth=None)
    student_info, page = get_grade_book_page(request, course, course_key)

    return render_to_response('courseware/gradebook.html', {
        'page': page,
        'page_url': reverse('spoc_gradebook', kwargs={'course_id': str(course_key)}),
        'students': student_info,
        'course': course,
        'course_id': course_key,
        # Checked above
        'staff_access': True,
        'ordered_grades': sorted(list(course.grade_cutoffs.items()), key=lambda i: i[1], reverse=True),
    })
