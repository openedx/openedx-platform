"""
Views for serving static textbooks.
"""


from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.views.decorators.clickjacking import xframe_options_exempt
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.static_replace import replace_static_urls
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_with_access


@login_required
def index(request, course_id, book_index, page=None):
    """
    Serve static image-based textbooks.
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    staff_access = bool(has_access(request.user, 'staff', course))

    book_index = int(book_index)
    if book_index < 0 or book_index >= len(course.textbooks):
        raise Http404(f"Invalid book index value: {book_index}")
    textbook = course.textbooks[book_index]
    table_of_contents = textbook.table_of_contents

    if page is None:
        page = textbook.start_page

    return render_to_response(
        'staticbook.html',
        {
            'book_index': book_index, 'page': int(page),
            'course': course,
            'book_url': textbook.book_url,
            'table_of_contents': table_of_contents,
            'start_page': textbook.start_page,
            'end_page': textbook.end_page,
            'staff_access': staff_access,
        },
    )


def remap_static_url(original_url, course):
    """Remap a URL in the ways the course requires."""
    # Ick: this should be possible without having to quote and unquote the URL...
    input_url = "'" + original_url + "'"
    output_url = replace_static_urls(
        input_url,
        getattr(course, 'data_dir', None),
        course_id=course.id,
        static_asset_path=course.static_asset_path
    )
    # strip off the quotes again...
    return output_url[1:-1]


@login_required
@xframe_options_exempt
def pdf_index(request, course_id, book_index, chapter=None, page=None):
    """
    Display a PDF textbook.

    course_id: course for which to display text.  The course should have
      "pdf_textbooks" property defined.

    book index:  zero-based index of which PDF textbook to display.

    chapter:  (optional) one-based index into the chapter array of textbook PDFs to display.
        Defaults to first chapter.  Specifying this assumes that there are separate PDFs for
        each chapter in a textbook.

    page:  (optional) one-based page number to display within the PDF.  Defaults to first page.
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    staff_access = bool(has_access(request.user, 'staff', course))

    book_index = int(book_index)
    if book_index < 0 or book_index >= len(course.pdf_textbooks):
        raise Http404(f"Invalid book index value: {book_index}")
    textbook = course.pdf_textbooks[book_index]

    viewer_params = ''
    current_url = ''

    if 'url' in textbook:
        textbook['url'] = remap_static_url(textbook['url'], course)
        viewer_params += textbook['url']
        current_url = textbook['url']

    # then remap all the chapter URLs as well, if they are provided.
    current_chapter = None
    if 'chapters' in textbook:
        for entry in textbook['chapters']:
            entry['url'] = remap_static_url(entry['url'], course)
            # Security: Validate chapter URL doesn't contain dangerous schemes
            if entry['url'].lower().startswith(('javascript:', 'data:', 'vbscript:', 'file:')):
                entry['url'] = ''  # Sanitize dangerous URLs
        if chapter is not None and int(chapter) <= (len(textbook['chapters'])):
            current_chapter = textbook['chapters'][int(chapter) - 1]
        else:
            current_chapter = textbook['chapters'][0]
        viewer_params += current_chapter['url']
        current_url = current_chapter['url']

    viewer_params += '#zoom=page-fit&disableRange=true'
    if page is not None:
        viewer_params += f'&page={page}'

    if request.GET.get('viewer', '') == 'true':
        template = 'pdf_viewer.html'
    else:
        template = 'static_pdfbook.html'

    return render_to_response(
        template,
        {
            'book_index': book_index,
            'course': course,
            'textbook': textbook,
            'chapter': chapter,
            'page': page,
            'viewer_params': viewer_params,
            'current_chapter': current_chapter,
            'staff_access': staff_access,
            'current_url': current_url,
        },
    )


@login_required
def html_index(request, course_id, book_index, chapter=None):
    """
    Display an HTML textbook.

    course_id: course for which to display text.  The course should have
      "html_textbooks" property defined.

    book index:  zero-based index of which HTML textbook to display.

    chapter:  (optional) one-based index into the chapter array of textbook HTML files to display.
        Defaults to first chapter.  Specifying this assumes that there are separate HTML files for
        each chapter in a textbook.
    """
    course_key = CourseKey.from_string(course_id)
    course = get_course_with_access(request.user, 'load', course_key)
    staff_access = bool(has_access(request.user, 'staff', course))

    book_index = int(book_index)
    if book_index < 0 or book_index >= len(course.html_textbooks):
        raise Http404(f"Invalid book index value: {book_index}")
    textbook = course.html_textbooks[book_index]

    if 'url' in textbook:
        textbook['url'] = remap_static_url(textbook['url'], course)
    # then remap all the chapter URLs as well, if they are provided.
    if 'chapters' in textbook:
        for entry in textbook['chapters']:
            entry['url'] = remap_static_url(entry['url'], course)

    student = request.user
    return render_to_response(
        'static_htmlbook.html',
        {
            'book_index': book_index,
            'course': course,
            'textbook': textbook,
            'chapter': chapter,
            'student': student,
            'staff_access': staff_access,
        },
    )
