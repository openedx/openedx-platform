"""
Contains code related to computing content gating course duration limits
and course access based on these limits.
"""

from django.utils import timezone
from django.utils.translation import gettext as _
from edx_django_utils.cache import RequestCache
from web_fragments.fragment import Fragment

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.date_utils import strftime_localized, strftime_localized_html
from lms.djangoapps.courseware.access_response import AccessError
from lms.djangoapps.courseware.access_utils import ACCESS_GRANTED
from lms.djangoapps.courseware.masquerade import get_course_masquerade, is_masquerading_as_specific_student
from lms.djangoapps.courseware.utils import verified_upgrade_deadline_link
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_date_signals.utils import get_expected_duration
from openedx.core.djangolib.markup import HTML
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig


class AuditExpiredError(AccessError):
    """
    Access denied because the user's audit timespan has expired
    """
    def __init__(self, user, course, expiration_date):
        error_code = 'audit_expired'
        developer_message = f'User {user} had access to {course} until {expiration_date}'
        expiration_date = strftime_localized(expiration_date, 'SHORT_DATE')
        user_message = _('Access expired on {expiration_date}').format(expiration_date=expiration_date)
        try:
            course_name = course.display_name_with_default
            additional_context_user_message = _('Access to {course_name} expired on {expiration_date}').format(
                course_name=course_name,
                expiration_date=expiration_date
            )
        except CourseOverview.DoesNotExist:
            additional_context_user_message = _('Access to the course you were looking'
                                                ' for expired on {expiration_date}').format(
                expiration_date=expiration_date
            )

        # pylint: disable=super-with-arguments
        super().__init__(error_code, developer_message, user_message, additional_context_user_message)


def get_user_course_duration(user, course):
    """
    Return a timedelta measuring the duration of the course for a particular user.

    Business Logic:
      - Course access duration is bounded by the min and max duration.
      - If course fields are missing, default course access duration to MIN_DURATION.
    """
    if not CourseDurationLimitConfig.enabled_for_enrollment(user, course):
        return None

    enrollment = CourseEnrollment.get_enrollment(user, course.id)
    if enrollment is None or enrollment.mode != CourseMode.AUDIT:
        return None

    verified_mode = CourseMode.verified_mode_for_course(course=course, include_expired=True)
    if not verified_mode:
        return None

    return get_expected_duration(course.id)


def get_user_course_expiration_date(user, course, enrollment=None):
    """
    Return expiration date for given user course pair.
    Return None if the course does not expire.

    Business Logic:
      - Course access duration is bounded by the min and max duration.
      - If course fields are missing, default course access duration to MIN_DURATION.
    """
    access_duration = get_user_course_duration(user, course)
    if access_duration is None:
        return None

    enrollment = enrollment or CourseEnrollment.get_enrollment(user, course.id)
    if enrollment is None or enrollment.mode != CourseMode.AUDIT:
        return None

    # We reset schedule.start in order to change a user's computed deadlines.
    # But their expiration date shouldn't change when we adjust their schedule (they don't
    # get additional time), so we need to based the expiration date on a fixed start date.
    content_availability_date = max(enrollment.created, course.start)

    return content_availability_date + access_duration


def check_course_expired(user, course):
    """
    Check if the course expired for the user.
    """
    # masquerading course staff should always have access
    if get_course_masquerade(user, course.id):
        return ACCESS_GRANTED

    expiration_date = get_user_course_expiration_date(user, course)
    if expiration_date and timezone.now() > expiration_date:
        return AuditExpiredError(user, course, expiration_date)

    return ACCESS_GRANTED


def get_access_expiration_data(user, course):
    """
    Create a dictionary of information about the access expiration for this user & course.

    Used by serializers to pass onto frontends and by the LMS locally to generate HTML for template rendering.

    Returns a dictionary of data, or None if no expiration is applicable.
    """
    expiration_date = get_user_course_expiration_date(user, course)
    if not expiration_date:
        return None

    enrollment = CourseEnrollment.get_enrollment(user, course.id)
    if enrollment is None:
        return None

    now = timezone.now()
    upgrade_deadline = enrollment.upgrade_deadline
    if not upgrade_deadline or upgrade_deadline < now:
        upgrade_deadline = enrollment.course_upgrade_deadline
    if upgrade_deadline and upgrade_deadline < now:
        upgrade_deadline = None

    masquerading_expired_course = is_masquerading_as_specific_student(user, course.id) and expiration_date < now

    return {
        'expiration_date': expiration_date,
        'masquerading_expired_course': masquerading_expired_course,
        'upgrade_deadline': upgrade_deadline,
        'upgrade_url': verified_upgrade_deadline_link(user, course=course) if upgrade_deadline else None,
    }


def generate_course_expired_message(user, course):
    """
    Generate the message for the user course expiration date if it exists.
    """
    expiration_data = get_access_expiration_data(user, course)
    if not expiration_data:
        return

    expiration_date = expiration_data['expiration_date']
    masquerading_expired_course = expiration_data['masquerading_expired_course']
    upgrade_deadline = expiration_data['upgrade_deadline']
    upgrade_url = expiration_data['upgrade_url']

    if masquerading_expired_course:
        upgrade_message = _('This learner does not have access to this course. '
                            'Their access expired on {expiration_date}.')
        return HTML(upgrade_message).format(
            expiration_date=strftime_localized_html(expiration_date, 'SHORT_DATE')
        )
    else:
        expiration_message = _('{strong_open}Audit Access Expires {expiration_date}{strong_close}'
                               '{line_break}You lose all access to this course, including your progress, on '
                               '{expiration_date}.')
        upgrade_deadline_message = _('{line_break}Upgrade by {upgrade_deadline} to get unlimited access to the course '
                                     'as long as it exists on the site. {a_open}Upgrade now{sronly_span_open} to '
                                     'retain access past {expiration_date}{span_close}{a_close}')
        full_message = expiration_message
        if upgrade_deadline and upgrade_url:
            full_message += upgrade_deadline_message
            using_upgrade_messaging = True
        else:
            using_upgrade_messaging = False

        formatted_expiration_date = strftime_localized_html(expiration_date, 'SHORT_DATE')
        if using_upgrade_messaging:
            formatted_upgrade_deadline = strftime_localized_html(upgrade_deadline, 'SHORT_DATE')

            return HTML(full_message).format(
                a_open=HTML('<a id="FBE_banner" href="{upgrade_link}">').format(upgrade_link=upgrade_url),
                sronly_span_open=HTML('<span class="sr-only">'),
                span_close=HTML('</span>'),
                a_close=HTML('</a>'),
                expiration_date=HTML(formatted_expiration_date),
                strong_open=HTML('<strong>'),
                strong_close=HTML('</strong>'),
                line_break=HTML('<br>'),
                upgrade_deadline=HTML(formatted_upgrade_deadline)
            )

        else:
            return HTML(full_message).format(
                span_close=HTML('</span>'),
                expiration_date=HTML(formatted_expiration_date),
                strong_open=HTML('<strong>'),
                strong_close=HTML('</strong>'),
                line_break=HTML('<br>'),
            )


def generate_course_expired_fragment(user, course):
    message = generate_course_expired_message(user, course)
    if message:
        return generate_fragment_from_message(message)


def generate_fragment_from_message(message):
    return Fragment(HTML('<div class="course-expiration-message">{}</div>').format(message))


def generate_course_expired_fragment_from_key(user, course_key):
    """
    Like `generate_course_expired_fragment`, but using a CourseKey instead of
    a CourseOverview and using request-level caching.

    Either returns WebFragment to inject XBlock content into, or None if we
    shouldn't show a course expired message for this user.
    """
    request_cache = RequestCache('generate_course_expired_fragment_from_key')
    cache_key = f'message:{user.id},{course_key}'
    cache_response = request_cache.get_cached_response(cache_key)
    if cache_response.is_found:
        cached_message = cache_response.value
        # In this case, there is no message to display.
        if cached_message is None:
            return None
        return generate_fragment_from_message(cached_message)

    course = CourseOverview.get_from_id(course_key)
    message = generate_course_expired_message(user, course)
    request_cache.set(cache_key, message)
    if message is None:
        return None

    return generate_fragment_from_message(message)


def course_expiration_wrapper(user, block, view, frag, context):  # pylint: disable=W0613
    """
    An XBlock wrapper that prepends a message to the beginning of a vertical if
    a user's course is about to expire.
    """
    if context.get('is_mobile_app'):
        return frag

    if block.category != 'vertical':
        return frag

    course_expiration_fragment = generate_course_expired_fragment_from_key(
        user, block.scope_ids.usage_id.context_key
    )
    if not course_expiration_fragment:
        return frag

    # Course content must be escaped to render correctly due to the way the
    # way the XBlock rendering works. Transforming the safe markup to unicode
    # escapes correctly.
    course_expiration_fragment.content = str(course_expiration_fragment.content)

    course_expiration_fragment.add_content(frag.content)
    course_expiration_fragment.add_fragment_resources(frag)

    return course_expiration_fragment
