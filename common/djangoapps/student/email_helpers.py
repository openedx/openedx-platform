"""
Helpers for Student app emails.
"""


from string import capwords

from django.conf import settings

from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.enrollments.api import is_enrollment_valid_for_proctoring
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.theming import helpers as theming_helpers
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order


def generate_activation_email_context(user, registration):
    """
    Constructs a dictionary for use in activation email contexts

    Arguments:
        user (User): Currently logged-in user
        registration (Registration): Registration object for the currently logged-in user
    """
    site = theming_helpers.get_current_site()
    context = get_base_template_context(site)
    context.update({
        'name': user.profile.name,
        'key': registration.activation_key,
        'lms_url': configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL),
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
        'contact_mailing_address': configuration_helpers.get_value(
            'contact_mailing_address',
            settings.CONTACT_MAILING_ADDRESS
        ),
        'support_url': configuration_helpers.get_value(
            'ACTIVATION_EMAIL_SUPPORT_LINK', settings.ACTIVATION_EMAIL_SUPPORT_LINK
        ) or settings.SUPPORT_SITE_LINK,
        'support_email': configuration_helpers.get_value('CONTACT_EMAIL', settings.CONTACT_EMAIL),
        'site_configuration_values': configuration_helpers.get_current_site_configuration_values(),
    })
    return context


def generate_proctoring_requirements_email_context(user, course_id):
    """
    Constructs a dictionary for use in proctoring requirements email context

    Arguments:
        user: Currently logged-in user
        course_id: ID of the proctoring-enabled course the user is enrolled in
    """
    course_block = modulestore().get_course(course_id)
    return {
        'user': user,
        'course_name': course_block.display_name,
        'proctoring_provider': capwords(course_block.proctoring_provider.replace('_', ' ')),
        'proctoring_requirements_url': settings.PROCTORING_SETTINGS.get('LINK_URLS', {}).get('faq', ''),
        'idv_required': not settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE'),
        'id_verification_url': IDVerificationService.get_verify_location(),
    }


def should_send_proctoring_requirements_email(username, course_id):
    """
    Returns a boolean whether a proctoring requirements email should be sent.

    Arguments:
        * username (str): The user associated with the enrollment.
        * course_id (str): The course id associated with the enrollment.
    """
    if not is_enrollment_valid_for_proctoring(username, course_id):
        return False

    # Only send if a proctored exam is found in the course
    timed_exams = modulestore().get_items(
        course_id,
        qualifiers={'category': 'sequential'},
        settings={'is_time_limited': True}
    )

    has_proctored_exam = any(exam.is_proctored_exam for exam in timed_exams)

    return has_proctored_exam
