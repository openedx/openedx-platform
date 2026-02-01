"""
Enrollment operations for use by instructor APIs.

Does not include any access control, be sure to check access before calling.
"""

import json
import logging
from contextlib import ExitStack, contextmanager
from datetime import datetime

import pytz
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import override as override_language
from edx_ace import ace
from edx_ace.recipient import Recipient
from eventtracking import tracker
from openedx.core.lib.celery.task_utils import emulate_http_request
from submissions import api as sub_api  # installed from the edx-submissions repository
from submissions.models import score_set, score_reset

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import (  # lint-amnesty, pylint: disable=line-too-long
    CourseEnrollment,
    CourseEnrollmentAllowed,
    anonymous_id_for_user,
    is_email_retired
)
from common.djangoapps.track.event_transaction_utils import (
    create_new_event_transaction_id,
    get_event_transaction_id,
    set_event_transaction_type
)
from lms.djangoapps.branding.api import get_logo_url_for_email
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.grades.api import constants as grades_constants
from lms.djangoapps.grades.api import disconnect_submissions_signal_receiver
from lms.djangoapps.grades.api import events as grades_events
from lms.djangoapps.grades.api import signals as grades_signals
from lms.djangoapps.instructor.message_types import (
    AccountCreationAndEnrollment,
    AddBetaTester,
    AllowedEnroll,
    AllowedUnenroll,
    EnrolledUnenroll,
    EnrollEnrolled,
    RemoveBetaTester
)
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming.helpers import get_current_site
from crum import get_current_request
from openedx.core.djangoapps.user_api.models import UserPreference
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


class EmailEnrollmentState:
    """ Store the complete enrollment state of an email in a class """

    def __init__(self, course_id, email):
        # N.B. retired users are not a concern here because they should be
        # handled at a higher level (i.e. in enroll_email).  Besides, this
        # class creates readonly objects.
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = None
        if user is not None:
            mode, is_active = CourseEnrollment.enrollment_mode_for_user(user, course_id)
            # is_active is `None` if the user is not enrolled in the course
            exists_ce = is_active is not None and is_active
            full_name = user.profile.name
            ceas = CourseEnrollmentAllowed.for_user(user).filter(course_id=course_id).all()
        else:
            mode = None
            exists_ce = False
            full_name = None
            ceas = CourseEnrollmentAllowed.objects.filter(email=email, course_id=course_id).all()
        exists_allowed = ceas.exists()
        state_auto_enroll = exists_allowed and ceas[0].auto_enroll

        self.user = user
        self.enrollment = exists_ce
        self.allowed = exists_allowed
        self.auto_enroll = bool(state_auto_enroll)
        self.full_name = full_name
        self.mode = mode

    def __repr__(self):
        return "{}(user={}, enrollment={}, allowed={}, auto_enroll={})".format(
            self.__class__.__name__,
            bool(self.user),
            self.enrollment,
            self.allowed,
            self.auto_enroll,
        )

    def to_dict(self):
        """
        example: {
            'user': False,
            'enrollment': False,
            'allowed': True,
            'auto_enroll': True,
        }
        """
        return {
            'user': bool(self.user),
            'enrollment': self.enrollment,
            'allowed': self.allowed,
            'auto_enroll': self.auto_enroll,
        }


def get_user_email_language(user):
    """
    Return the language most appropriate for writing emails to user. Returns
    None if the preference has not been set, or if the user does not exist.
    """
    # Calling UserPreference directly instead of get_user_preference because the user requesting the
    # information is not "user" and also may not have is_staff access.
    return UserPreference.get_value(user, LANGUAGE_KEY)


def enroll_email(
    course_id,
    student_email,
    auto_enroll=False,
    message_students=False,
    message_params=None,
    language=None
):
    """
    Enroll a student by email.

    `student_email` is student's emails e.g. "foo@bar.com"
    `auto_enroll` determines what is put in CourseEnrollmentAllowed.auto_enroll
        if auto_enroll is set, then when the email registers, they will be
        enrolled in the course automatically.
    `message_students` determines if student should be notified of action by email or push message.
    `message_params` parameters used while parsing message templates (a `dict`).
    `language` is the language used to render the email.

    returns two EmailEnrollmentState's
        representing state before and after the action.
    """
    previous_state = EmailEnrollmentState(course_id, student_email)
    enrollment_obj = None
    if message_params:
        message_params.update({
            'app_label': 'instructor',
            'push_notification_extra_context': {
                'notification_type': 'enroll',
                'course_id': str(course_id),
            },
        })
    else:
        message_params = {}
    if previous_state.user and previous_state.user.is_active:
        # if the student is currently unenrolled, don't enroll them in their
        # previous mode

        # for now, White Labels use the
        # "honor" course_mode. Given the change to use "audit" as the default
        # course_mode in Open edX, we need to be backwards compatible with
        # how White Labels approach enrollment modes.
        if CourseMode.is_white_label(course_id):
            course_mode = CourseMode.HONOR
        else:
            course_mode = None

        if previous_state.enrollment:
            course_mode = previous_state.mode

        enrollment_obj = CourseEnrollment.enroll_by_email(student_email, course_id, course_mode)
        if message_students:
            message_params['message_type'] = 'enrolled_enroll'
            message_params['email_address'] = student_email
            message_params['user_id'] = previous_state.user.id
            message_params['full_name'] = previous_state.full_name
            send_mail_to_student(student_email, message_params, language=language)

    elif not is_email_retired(student_email):
        cea, _ = CourseEnrollmentAllowed.objects.get_or_create(course_id=course_id, email=student_email)
        cea.auto_enroll = auto_enroll
        cea.save()
        if message_students:
            message_params['message_type'] = 'allowed_enroll'
            message_params['email_address'] = student_email
            if previous_state.user:
                message_params['user_id'] = previous_state.user.id
            send_mail_to_student(student_email, message_params, language=language)

    after_state = EmailEnrollmentState(course_id, student_email)

    return previous_state, after_state, enrollment_obj


def unenroll_email(course_id, student_email, message_students=False, message_params=None, language=None):
    """
    Unenroll a student by email.

    `student_email` is student's emails e.g. "foo@bar.com"
    `message_students` determines if student should be notified of action by email or push message.
    `message_params` parameters used while parsing email templates (a `dict`).
    `language` is the language used to render the email.

    returns two EmailEnrollmentState's
        representing state before and after the action.
    """
    previous_state = EmailEnrollmentState(course_id, student_email)
    if message_params:
        message_params.update({
            'app_label': 'instructor',
            'push_notification_extra_context': {
                'notification_type': 'unenroll',
            },
        })
    else:
        message_params = {}
    if previous_state.enrollment:
        CourseEnrollment.unenroll_by_email(student_email, course_id)
        if message_students:
            message_params['message_type'] = 'enrolled_unenroll'
            message_params['email_address'] = student_email
            if previous_state.user:
                message_params['user_id'] = previous_state.user.id
            message_params['full_name'] = previous_state.full_name
            send_mail_to_student(student_email, message_params, language=language)

    if previous_state.allowed:
        CourseEnrollmentAllowed.objects.get(course_id=course_id, email=student_email).delete()
        if message_students:
            message_params['message_type'] = 'allowed_unenroll'
            message_params['email_address'] = student_email
            if previous_state.user:
                message_params['user_id'] = previous_state.user.id
            # Since no User object exists for this student there is no "full_name" available.
            send_mail_to_student(student_email, message_params, language=language)

    after_state = EmailEnrollmentState(course_id, student_email)

    return previous_state, after_state


def send_beta_role_email(action, user, message_params):
    """
    Send an email to a user added or removed as a beta tester.

    `action` is one of 'add' or 'remove'
    `user` is the User affected
    `message_params` parameters used while parsing email templates (a `dict`).
    """
    if action in ('add', 'remove'):
        message_params['message_type'] = '%s_beta_tester' % action
        message_params['email_address'] = user.email
        message_params['user_id'] = user.id
        message_params['full_name'] = user.profile.name
        message_params['app_label'] = 'instructor'
        message_params['push_notification_extra_context'] = {
            'notification_type': message_params['message_type'],
            'course_id': str(getattr(message_params.get('course'), 'id', '')),
        }
    else:
        raise ValueError(f"Unexpected action received '{action}' - expected 'add' or 'remove'")
    trying_to_add_inactive_user = not user.is_active and action == 'add'
    if not trying_to_add_inactive_user:
        send_mail_to_student(user.email, message_params, language=get_user_email_language(user))


@contextmanager
def _conditionally_disconnect_submissions_signal_recievers(emit_score_reset):
    """
    Context manager helper.
    - Disconnects the score_set signal on enter, reconnects on exit
    - If emit_score_reset, disconnects score_reset signal on enter, reconnects on exit
    """
    with ExitStack() as context_stack:
        context_stack.enter_context(disconnect_submissions_signal_receiver(score_set))
        if not emit_score_reset:
            context_stack.enter_context(disconnect_submissions_signal_receiver(score_reset))
        yield


def reset_student_attempts(
    course_id,
    student,
    module_state_key,
    requesting_user,
    delete_module=False,
    emit_signals_and_events=True,
):
    """
    Reset student attempts for a problem. Optionally deletes all student state for the specified problem.

    In the previous instructor dashboard it was possible to modify/delete
    modules that were not problems. That has been disabled for safety.

    `student` is a User
    `problem_to_reset` is the name of a problem e.g. 'L2Node1'.
    To build the module_state_key 'problem/' and course information will be appended to `problem_to_reset`.
    `delete_module`: Instead of resetting attempts, delete the learner's StudentModule
    `emit_signals_and_events`: If this is False, don't fire django signals or emit events. This is intended for
                               the case where we are calling this function many times, and want to handle the signalling
                               and eventing at a bulk level rather than firing every individual call to this function

    Raises:
        ValueError: `problem_state` is invalid JSON.
        StudentModule.DoesNotExist: could not load the student module.
        submissions.SubmissionError: unexpected error occurred while resetting the score in the submissions API.

    """
    user_id = anonymous_id_for_user(student, course_id)
    requesting_user_id = anonymous_id_for_user(requesting_user, course_id)
    submission_cleared = False
    teams_enabled = False
    selected_teamset_id = None
    try:
        # A block may have children. Clear state on children first.
        block = modulestore().get_item(module_state_key)
        if block.has_children:
            for child in block.children:
                try:
                    reset_student_attempts(course_id, student, child, requesting_user, delete_module=delete_module)
                except StudentModule.DoesNotExist:
                    # If a particular child doesn't have any state, no big deal, as long as the parent does.
                    pass
        if delete_module:
            # Some blocks (openassessment) use StudentModule data as a key for internal submission data.
            # Inform these blocks of the reset and allow them to handle their data.
            clear_student_state = getattr(block, "clear_student_state", None)
            if callable(clear_student_state):
                with _conditionally_disconnect_submissions_signal_recievers(emit_signals_and_events):
                    clear_student_state(
                        user_id=user_id,
                        course_id=str(course_id),
                        item_id=str(module_state_key),
                        requesting_user_id=requesting_user_id
                    )
                submission_cleared = True
        teams_enabled = getattr(block, 'teams_enabled', False)
        if teams_enabled:
            selected_teamset_id = getattr(block, 'selected_teamset_id', None)
    except ItemNotFoundError:
        block = None
        log.warning("Could not find %s in modulestore when attempting to reset attempts.", module_state_key)

    # Reset the student's score in the submissions API, if xblock.clear_student_state has not done so already.
    # We need to do this before retrieving the `StudentModule` model, because a score may exist with no student module.

    # TODO: Should the LMS know about sub_api and call this reset, or should it generically call it on all of its
    # xblock services as well?  See JIRA ARCH-26.
    if delete_module and not submission_cleared:
        sub_api.reset_score(
            user_id,
            str(course_id),
            str(module_state_key),
            emit_signal=emit_signals_and_events,
        )

    def _reset_or_delete_module(studentmodule):
        if delete_module:
            studentmodule.delete()
            if not emit_signals_and_events:
                return

            create_new_event_transaction_id()
            set_event_transaction_type(grades_events.STATE_DELETED_EVENT_TYPE)
            tracker.emit(
                str(grades_events.STATE_DELETED_EVENT_TYPE),
                {
                    'user_id': str(student.id),
                    'course_id': str(course_id),
                    'problem_id': str(module_state_key),
                    'instructor_id': str(requesting_user.id),
                    'event_transaction_id': str(get_event_transaction_id()),
                    'event_transaction_type': str(grades_events.STATE_DELETED_EVENT_TYPE),
                }
            )
            if not submission_cleared:
                _fire_score_changed_for_block(
                    course_id,
                    student,
                    block,
                    module_state_key,
                )
        else:
            _reset_module_attempts(studentmodule)

    team = None
    if teams_enabled:
        from lms.djangoapps.teams.api import get_team_for_user_course_topic
        team = get_team_for_user_course_topic(student, str(course_id), selected_teamset_id)
    if team:
        modules_to_reset = StudentModule.objects.filter(
            student__teams=team,
            course_id=course_id,
            module_state_key=module_state_key
        )
        for module_to_reset in modules_to_reset:
            _reset_or_delete_module(module_to_reset)
        return
    else:
        # Teams are not enabled or the user does not have a team
        module_to_reset = StudentModule.objects.get(
            student_id=student.id,
            course_id=course_id,
            module_state_key=module_state_key
        )
        _reset_or_delete_module(module_to_reset)


def _reset_module_attempts(studentmodule):
    """
    Reset the number of attempts on a studentmodule.

    Throws ValueError if `problem_state` is invalid JSON.
    """
    # load the state json
    problem_state = json.loads(studentmodule.state)
    # old_number_of_attempts = problem_state["attempts"]
    problem_state["attempts"] = 0
    problem_state["score_history"] = []
    problem_state["correct_map_history"] = []
    problem_state["student_answers_history"] = []

    # save
    studentmodule.state = json.dumps(problem_state)
    studentmodule.save()


def _fire_score_changed_for_block(
    course_id,
    student,
    block,
    module_state_key,
):
    """
    Fires a PROBLEM_RAW_SCORE_CHANGED event for the given module.
    The earned points are always zero. We must retrieve the possible points
    from the XModule, as noted below. The effective time is now().
    """
    if block and block.has_score:
        max_score = block.max_score()
        if max_score is not None:
            grades_signals.PROBLEM_RAW_SCORE_CHANGED.send(
                sender=None,
                raw_earned=0,
                raw_possible=max_score,
                weight=getattr(block, 'weight', None),
                user_id=student.id,
                course_id=str(course_id),
                usage_id=str(module_state_key),
                score_deleted=True,
                only_if_higher=False,
                modified=datetime.now().replace(tzinfo=pytz.UTC),
                score_db_table=grades_constants.ScoreDatabaseTableEnum.courseware_student_module,
            )


def get_email_params(course, auto_enroll, secure=True, course_key=None, display_name=None):
    """
    Generate parameters used when parsing email templates.

    `auto_enroll` is a flag for auto enrolling non-registered students: (a `boolean`)
    Returns a dict of parameters
    """

    protocol = 'https' if secure else 'http'
    course_key = course_key or str(course.id)
    display_name = display_name or course.display_name_with_default

    stripped_site_name = configuration_helpers.get_value(
        'SITE_NAME',
        settings.SITE_NAME
    )
    # TODO: Use request.build_absolute_uri rather than '{proto}://{site}{path}'.format
    # and check with the Services team that this works well with microsites
    registration_url = '{proto}://{site}{path}'.format(
        proto=protocol,
        site=stripped_site_name,
        path=reverse('register_user')
    )
    course_url = '{proto}://{site}{path}'.format(
        proto=protocol,
        site=stripped_site_name,
        path=reverse('course_root', kwargs={'course_id': course_key})
    )

    # We can't get the url to the course's About page if the marketing site is enabled.
    course_about_url = None
    if not settings.FEATURES.get('ENABLE_MKTG_SITE', False):
        course_about_url = '{proto}://{site}{path}'.format(
            proto=protocol,
            site=stripped_site_name,
            path=reverse('about_course', kwargs={'course_id': course_key})
        )

    is_shib_course = uses_shib(course)

    # Collect mailing address and platform name to pass as context
    contact_mailing_address = configuration_helpers.get_value(
        'contact_mailing_address',
        settings.CONTACT_MAILING_ADDRESS
    )
    platform_name = configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)

    # Composition of email
    email_params = {
        'site_name': stripped_site_name,
        'registration_url': registration_url,
        'course': course,
        'display_name': display_name,
        'auto_enroll': auto_enroll,
        'course_url': course_url,
        'course_about_url': course_about_url,
        'is_shib_course': is_shib_course,
        'contact_mailing_address': contact_mailing_address,
        'platform_name': platform_name,
        'site_configuration_values': configuration_helpers.get_current_site_configuration_values(),
        'logo_url': get_logo_url_for_email(),
    }
    return email_params


def send_mail_to_student(student, param_dict, language=None):
    """
    Construct the email using templates and then send it.
    `student` is the student's email address (a `str`),

    `param_dict` is a `dict` with keys
    [
        `site_name`: name given to edX instance (a `str`)
        `registration_url`: url for registration (a `str`)
        `display_name` : display name of a course (a `str`)
        `course_id`: id of course (a `str`)
        `auto_enroll`: user input option (a `str`)
        `course_url`: url of course (a `str`)
        `user_id`: LMS user ID of student (an `int`) - None if unknown
        `email_address`: email of student (a `str`)
        `full_name`: student full name (a `str`)
        `message_type`: type of email to send and template to use (a `str`)
        `is_shib_course`: (a `boolean`)
    ]

    `language` is the language used to render the email. If None the language
    of the currently-logged in user (that is, the user sending the email) will
    be used.

    Returns a boolean indicating whether the email was sent successfully.
    """

    # Add some helpers and microconfig subsitutions
    if 'display_name' in param_dict:
        param_dict['course_name'] = param_dict['display_name']
    elif 'course' in param_dict:
        param_dict['course_name'] = param_dict['course'].display_name_with_default

    param_dict['site_name'] = configuration_helpers.get_value(
        'SITE_NAME',
        param_dict['site_name']
    )

    # Extract an LMS user ID for the student, if possible.
    # ACE needs the user ID to be able to send email via Braze.
    try:
        lms_user_id = User.objects.get(email=student).id
    except User.DoesNotExist:
        lms_user_id = 0

    # see if there is an activation email template definition available as configuration,
    # if so, then render that
    message_type = param_dict['message_type']

    ace_emails_dict = {
        'account_creation_and_enrollment': AccountCreationAndEnrollment,
        'add_beta_tester': AddBetaTester,
        'allowed_enroll': AllowedEnroll,
        'allowed_unenroll': AllowedUnenroll,
        'enrolled_enroll': EnrollEnrolled,
        'enrolled_unenroll': EnrolledUnenroll,
        'remove_beta_tester': RemoveBetaTester,
    }

    message_class = ace_emails_dict[message_type]
    message = message_class().personalize(
        recipient=Recipient(lms_user_id=lms_user_id, email_address=student),
        language=language,
        user_context=param_dict,
    )

    current_request = get_current_request()

    if current_request is None:
        # We're in a Celery task context, need to emulate HTTP request
        site = get_current_site()
        if not site:
            try:
                site = Site.objects.get(id=settings.SITE_ID)
            except Site.DoesNotExist:
                try:
                    site = Site.objects.first()
                except Exception:  # pylint: disable=broad-except
                    site = None

        # Get the recipient user for tracking purposes
        user = None
        if lms_user_id and lms_user_id > 0:
            try:
                user = User.objects.get(id=lms_user_id)
            except User.DoesNotExist:
                pass

        # Use emulate_http_request to provide the necessary context for template tags
        # that require a request object, such as google_analytics_tracking_pixel
        with emulate_http_request(site=site, user=user):
            ace.send(message)
    else:
        # We're in a web context, just send the message directly
        # The current request already provides the necessary context
        ace.send(message)


def render_message_to_string(subject_template, message_template, param_dict, language=None):
    """
    Render a mail subject and message templates using the parameters from
    param_dict and the given language. If language is None, the platform
    default language is used.

    Returns two strings that correspond to the rendered, translated email
    subject and message.
    """
    language = language or settings.LANGUAGE_CODE
    with override_language(language):
        return get_subject_and_message(subject_template, message_template, param_dict)


def get_subject_and_message(subject_template, message_template, param_dict):
    """
    Return the rendered subject and message with the appropriate parameters.
    """
    subject = render_to_string(subject_template, param_dict)
    message = render_to_string(message_template, param_dict)
    return subject, message


def uses_shib(course):
    """
    Used to return whether course has Shibboleth as the enrollment domain

    Returns a boolean indicating if Shibboleth authentication is set for this course.
    """
    return bool(course.enrollment_domain and course.enrollment_domain.startswith(settings.SHIBBOLETH_DOMAIN_PREFIX))
