"""
Certificates API

This provides APIs for generating course certificates asynchronously.

Other Django apps should use the API functions defined here in this module; other apps should not import the
certificates models or any other certificates modules.
"""

import logging
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from eventtracking import tracker
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.keys import CourseKey
from organizations.api import get_course_organization_id
from pytz import UTC

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.api import is_user_enrolled_in_course
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.branding import api as branding_api
from lms.djangoapps.certificates.config import AUTO_CERTIFICATE_GENERATION as _AUTO_CERTIFICATE_GENERATION
from lms.djangoapps.certificates.data import CertificateStatuses, GeneratedCertificateData
from lms.djangoapps.certificates.generation_handler import generate_certificate_task as _generate_certificate_task
from lms.djangoapps.certificates.generation_handler import is_on_certificate_allowlist as _is_on_certificate_allowlist
from lms.djangoapps.certificates.models import (
    CertificateAllowlist,
    CertificateDateOverride,
    CertificateGenerationConfiguration,
    CertificateGenerationCourseSetting,
    CertificateGenerationHistory,
    CertificateInvalidation,
    CertificateTemplate,
    CertificateTemplateAsset,
    ExampleCertificateSet,
    GeneratedCertificate,
)
from lms.djangoapps.certificates.utils import certificate_status as _certificate_status
from lms.djangoapps.certificates.utils import certificate_status_for_student as _certificate_status_for_student
from lms.djangoapps.certificates.utils import get_certificate_url as _get_certificate_url
from lms.djangoapps.certificates.utils import has_html_certificates_enabled as _has_html_certificates_enabled
from lms.djangoapps.certificates.utils import should_certificate_be_visible as _should_certificate_be_visible
from lms.djangoapps.instructor import access
from lms.djangoapps.utils import _get_key
from openedx.core.djangoapps.content.course_overviews.api import get_course_overview_or_none
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.data import CertificatesDisplayBehaviors  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger("edx.certificate")
User = get_user_model()
MODES = GeneratedCertificate.MODES


def _format_certificate_for_user(username, cert):
    """
    Helper function to serialize a user certificate.

    Arguments:
        username (unicode): The identifier of the user.
        cert (GeneratedCertificate): a user certificate

    Returns: dict
    """
    course_overview = get_course_overview_or_none(cert.course_id)
    if cert.download_url or course_overview:
        return {
            "username": username,
            "course_key": cert.course_id,
            "type": cert.mode,
            "status": cert.status,
            "grade": cert.grade,
            "created": cert.created_date,
            "modified": cert.modified_date,
            "is_passing": CertificateStatuses.is_passing_status(cert.status),
            "is_pdf_certificate": bool(cert.download_url),
            "download_url": (
                cert.download_url
                or get_certificate_url(cert.user.id, cert.course_id, uuid=cert.verify_uuid, user_certificate=cert)
                if cert.status == CertificateStatuses.downloadable
                else None
            ),
            "uuid": cert.verify_uuid,
        }

    return None


def get_certificates_for_user(username):
    """
    Retrieve certificate information for a particular user.

    Arguments:
        username (unicode): The identifier of the user.

    Returns: list

    Example Usage:
    >>> get_certificates_for_user("bob")
    [
        {
            "username": "bob",
            "course_key": CourseLocator('edX', 'DemoX', 'Demo_Course', None, None),
            "type": "verified",
            "status": "downloadable",
            "download_url": "https://www.example.com/cert.pdf",
            "grade": "0.98",
            "created": 2015-07-31T00:00:00Z,
            "modified": 2015-07-31T00:00:00Z
        }
    ]

    """
    certs = []
    # Checks if certificates are not None before adding them to list
    for cert in GeneratedCertificate.eligible_certificates.filter(user__username=username).order_by("course_id"):
        formatted_cert = _format_certificate_for_user(username, cert)
        if formatted_cert:
            certs.append(formatted_cert)
    return certs


def get_certificate_for_user(username, course_key, format_results=True):
    """
    Retrieve certificate information for a particular user for a specific course.

    Arguments:
        username (unicode): The identifier of the user.
        course_key (CourseKey): A Course Key.
        format_results (boolean): Default True.  If False, return the GeneratedCertificate object
            instead of the serialized dict representation.
    Returns:
        A dict containing a serialized representation of the certificate or, optionally,
        the GeneratedCertificate object itself.
        If there is no GeneratedCertificate object for the user and course combination, returns None.
    """

    cert = GeneratedCertificate.eligible_certificates.filter(user__username=username, course_id=course_key).first()
    return _format_certificate_for_user(username, cert) if cert and format_results else cert


def get_certificate_for_user_id(user, course_id):
    """
    Retrieve certificate information for a user in a specific course.

    Arguments:
        user (User): A Django User.
        course_id (CourseKey): Course ID
    Returns:
        A GeneratedCertificate object.
    """
    return GeneratedCertificate.certificate_for_student(user, course_id)


def get_certificates_for_user_by_course_keys(user, course_keys):
    """
    Retrieve certificate information for a particular user for a set of courses.

    Arguments:
        user (User)
        course_keys (set[CourseKey])

    Returns: dict[CourseKey: dict]
        Mapping from course keys to dict of certificate data.
        Course keys for courses for which the user does not have a certificate
        will be omitted.
    """
    certs = GeneratedCertificate.eligible_certificates.filter(user=user, course_id__in=course_keys)
    return {cert.course_id: _format_certificate_for_user(user.username, cert) for cert in certs}


def get_recently_modified_certificates(course_keys=None, start_date=None, end_date=None, user_ids=None):
    """
    Returns a QuerySet of GeneratedCertificate objects filtered by the input
    parameters and ordered by modified_date.
    """
    cert_filter_args = {}

    if course_keys:
        cert_filter_args["course_id__in"] = course_keys

    if start_date:
        cert_filter_args["modified_date__gte"] = start_date

    if end_date:
        cert_filter_args["modified_date__lte"] = end_date

    if user_ids:
        cert_filter_args["user__id__in"] = user_ids

    # Include certificates with a CertificateDateOverride modified within the
    # given time range.
    if start_date or end_date:
        certs_with_modified_overrides = get_certs_with_modified_overrides(course_keys, start_date, end_date, user_ids)
        return (
            GeneratedCertificate.objects.filter(**cert_filter_args)
            .union(certs_with_modified_overrides)
            .order_by("modified_date")
        )

    return GeneratedCertificate.objects.filter(**cert_filter_args).order_by("modified_date")


def get_certs_with_modified_overrides(course_keys=None, start_date=None, end_date=None, user_ids=None):
    """
    Returns a QuerySet of certificates with a CertificateDateOverride modified
    within the given start_date and end_date. Uses the history table to catch
    deleted overrides too.
    """
    override_filter_args = {}
    if start_date:
        override_filter_args["history_date__gte"] = start_date
    if end_date:
        override_filter_args["history_date__lte"] = end_date

    # Get the HistoricalCertificateDateOverrides that have entries within the
    # given date range. We check the history table to catch deleted overrides
    # along with those created / updated
    overrides = CertificateDateOverride.history.filter(**override_filter_args)

    # Get the associated GeneratedCertificate ids.
    override_cert_ids = overrides.values_list("generated_certificate", flat=True)

    # Build the args for the GeneratedCertificate query. First, filter by all
    # certs identified in override_cert_ids; then by the other arguments passed,
    # if present.
    cert_filter_args = {"pk__in": override_cert_ids}
    if course_keys:
        cert_filter_args["course_id__in"] = course_keys
    if user_ids:
        cert_filter_args["user__id__in"] = user_ids

    return GeneratedCertificate.objects.filter(**cert_filter_args)


def generate_certificate_task(user, course_key, generation_mode=None):
    """
    Create a task to generate a certificate for this user in this course run, if the user is eligible and a certificate
    can be generated.

    If the allowlist is enabled for this course run and the user is on the allowlist, the allowlist logic will be used.
    Otherwise, the regular course certificate generation logic will be used.

    Args:
        user: user for whom to generate a certificate
        course_key: course run key for which to generate a certificate
        generation_mode: Used when emitting an events. Options are "self" (implying the user generated the cert
            themself) and "batch" for everything else.
    """
    return _generate_certificate_task(user, course_key, generation_mode)


def certificate_downloadable_status(student, course_key):
    """
    Check the student existing certificates against a given course.
    if status is not generating and not downloadable or error then user can view the generate button.

    Args:
        student (user object): logged-in user
        course_key (CourseKey): ID associated with the course

    Returns:
        Dict containing student passed status also download url, uuid for cert if available
    """
    current_status = _certificate_status_for_student(student, course_key)

    # If the certificate status is an error user should view that status is "generating".
    # On the back-end, need to monitor those errors and re-submit the task.

    # pylint: disable=simplifiable-if-expression
    response_data = {
        "is_downloadable": False,
        "is_generating": (
            True if current_status["status"] in [CertificateStatuses.generating, CertificateStatuses.error] else False
        ),
        "is_unverified": (True if current_status["status"] == CertificateStatuses.unverified else False),
        "download_url": None,
        "uuid": None,
    }
    # pylint: enable=simplifiable-if-expression

    course_overview = get_course_overview_or_none(course_key)

    display_behavior_is_valid = (
        course_overview.certificates_display_behavior == CertificatesDisplayBehaviors.END_WITH_DATE
    )

    if (
        not certificates_viewable_for_course(course_overview)
        and CertificateStatuses.is_passing_status(current_status["status"])
        and display_behavior_is_valid
        and course_overview.certificate_available_date
    ):
        response_data["earned_but_not_available"] = True
        response_data["certificate_available_date"] = course_overview.certificate_available_date

    if (
        not certificates_viewable_for_course(course_overview)
        and not CertificateStatuses.is_passing_status(current_status["status"])
        and display_behavior_is_valid
        and course_overview.certificate_available_date
    ):
        response_data["not_earned_but_available_date"] = True
        response_data["certificate_available_date"] = course_overview.certificate_available_date

    may_view_certificate = _should_certificate_be_visible(
        course_overview.certificates_display_behavior,
        course_overview.certificates_show_before_end,
        course_overview.has_ended(),
        course_overview.certificate_available_date,
        course_overview.self_paced,
    )
    if current_status["status"] == CertificateStatuses.downloadable and may_view_certificate:
        response_data["is_downloadable"] = True
        response_data["download_url"] = current_status["download_url"] or get_certificate_url(
            student.id, course_key, current_status["uuid"]
        )
        response_data["is_pdf_certificate"] = bool(current_status["download_url"])
        response_data["uuid"] = current_status["uuid"]

    return response_data


def set_cert_generation_enabled(course_key, is_enabled):
    """Enable or disable self-generated certificates for a course.

    There are two "switches" that control whether self-generated certificates
    are enabled for a course:

    1) Whether the self-generated certificates feature is enabled.
    2) Whether self-generated certificates have been enabled for this particular course.

    The second flag should be enabled *only* when someone has successfully
    generated example certificates for the course.  This helps avoid
    configuration errors (for example, not having a template configured
    for the course installed on the workers).  The UI for the instructor
    dashboard enforces this constraint.

    Arguments:
        course_key (CourseKey): The course identifier.

    Keyword Arguments:
        is_enabled (boolean): If provided, enable/disable self-generated
            certificates for this course.

    """
    CertificateGenerationCourseSetting.set_self_generation_enabled_for_course(course_key, is_enabled)
    cert_event_type = "enabled" if is_enabled else "disabled"
    event_name = ".".join(["edx", "certificate", "generation", cert_event_type])
    tracker.emit(
        event_name,
        {
            "course_id": str(course_key),
        },
    )
    if is_enabled:
        log.info("Enabled self-generated certificates for course '%s'.", str(course_key))
    else:
        log.info("Disabled self-generated certificates for course '%s'.", str(course_key))


def is_certificate_invalidated(student, course_key):
    """Check whether the certificate belonging to the given student (in given course) has been invalidated.

    Arguments:
        student (user object): logged-in user
        course_key (CourseKey): The course identifier.

    Returns:
        Boolean denoting whether the certificate has been invalidated for this learner.
    """
    log.info(f"Checking if student {student.id} has an invalidated certificate in course {course_key}.")

    certificate = GeneratedCertificate.certificate_for_student(student, course_key)
    if certificate:
        return CertificateInvalidation.has_certificate_invalidation(student, course_key)

    return False


def has_self_generated_certificates_enabled(course_key):
    """Check whether the self-generated certificates feature is enabled for a given course.

    There are two "switches" that control whether self-generated certificates
    are enabled for a course:

    1) Whether the self-generated certificates feature is enabled.
    2) Whether self-generated certificates have been enabled for this particular course.

    Certificates are enabled for a course only when both switches
    are set to True.

    Arguments:
        course_key (CourseKey): The course identifier.

    Returns:
        boolean: Whether self-generated certificates are enabled
            for the course.

    """
    return (
        CertificateGenerationConfiguration.current().enabled
        and CertificateGenerationCourseSetting.is_self_generation_enabled_for_course(course_key)
    )


def example_certificates_status(course_key):
    """Check the status of example certificates for a course.

    This will check the *latest* example certificate task.
    This is generally what we care about in terms of enabling/disabling
    self-generated certificates for a course.

    Arguments:
        course_key (CourseKey): The course identifier.

    Returns:
        list

    Example Usage:

        >>> from lms.djangoapps.certificates import api as certs_api
        >>> certs_api.example_certificates_status(course_key)
        [
            {
                'description': 'honor',
                'status': 'success',
                'download_url': 'https://www.example.com/abcd/honor_cert.pdf'
            },
            {
                'description': 'verified',
                'status': 'error',
                'error_reason': 'No template found!'
            }
        ]

    """
    return ExampleCertificateSet.latest_status(course_key)


def has_html_certificates_enabled(course_overview):
    return _has_html_certificates_enabled(course_overview)


def get_certificate_url(user_id=None, course_id=None, uuid=None, user_certificate=None):
    return _get_certificate_url(user_id, course_id, uuid, user_certificate)


def get_active_web_certificate(course, is_preview_mode=None):
    """
    Retrieves the active web certificate configuration for the specified course
    """
    certificates = getattr(course, "certificates", {})
    configurations = certificates.get("certificates", [])
    for config in configurations:
        if config.get("is_active") or is_preview_mode:
            return config
    return None


def get_certificate_template(course_key, mode, language):
    """
    Retrieves the custom certificate template based on course_key, mode, and language.
    """
    template = None
    # fetch organization of the course
    org_id = get_course_organization_id(course_key)

    # only consider active templates
    active_templates = CertificateTemplate.objects.filter(is_active=True)

    if org_id and mode:  # get template by org, mode, and key
        org_mode_and_key_templates = active_templates.filter(organization_id=org_id, mode=mode, course_key=course_key)
        template = _get_language_specific_template_or_default(language, org_mode_and_key_templates)

    # since no template matched that course_key, only consider templates with empty course_key
    empty_course_key_templates = active_templates.filter(course_key=CourseKeyField.Empty)
    if not template and org_id and mode:  # get template by org and mode
        org_and_mode_templates = empty_course_key_templates.filter(organization_id=org_id, mode=mode)
        template = _get_language_specific_template_or_default(language, org_and_mode_templates)
    if not template and org_id:  # get template by only org
        org_templates = empty_course_key_templates.filter(organization_id=org_id, mode=None)
        template = _get_language_specific_template_or_default(language, org_templates)
    if not template and mode:  # get template by only mode
        mode_templates = empty_course_key_templates.filter(organization_id=None, mode=mode)
        template = _get_language_specific_template_or_default(language, mode_templates)
    return template if template else None


def _get_language_specific_template_or_default(language, templates):
    """
    Returns templates that match passed in language.
    Returns default templates If no language matches, or language passed is None
    """
    two_letter_language = _get_two_letter_language_code(language)

    language_or_default_templates = list(
        templates.filter(Q(language=two_letter_language) | Q(language=None) | Q(language=""))
    )
    language_specific_template = _get_language_specific_template(two_letter_language, language_or_default_templates)
    if language_specific_template:
        return language_specific_template
    else:
        return _get_all_languages_or_default_template(language_or_default_templates)


def _get_language_specific_template(language, templates):
    for template in templates:
        if template.language == language:
            return template
    return None


def _get_all_languages_or_default_template(templates):
    """
    Returns the first template that isn't language specific
    """
    for template in templates:
        if template.language == "":
            return template

    return templates[0] if templates else None


def _get_two_letter_language_code(language_code):
    """
    Shortens language to only first two characters (e.g. es-419 becomes es)
    This is needed because Catalog returns locale language which is not always a 2 letter code.
    """
    if language_code is None:
        return None
    elif language_code == "":
        return ""
    else:
        return language_code[:2]


def get_asset_url_by_slug(asset_slug):
    """
    Returns certificate template asset url for given asset_slug.
    """
    asset_url = ""
    try:
        template_asset = CertificateTemplateAsset.objects.get(asset_slug=asset_slug)
        asset_url = template_asset.asset.url
    except CertificateTemplateAsset.DoesNotExist:
        pass
    return asset_url


def get_certificate_header_context(is_secure=True):
    """
    Return data to be used in Certificate Header,
    data returned should be customized according to the site configuration.
    """
    data = dict(
        logo_src=branding_api.get_logo_url(is_secure),
        logo_url=branding_api.get_base_url(is_secure),
    )

    return data


def get_certificate_footer_context():
    """
    Return data to be used in Certificate Footer,
    data returned should be customized according to the site configuration.
    """
    data = {}

    # get Terms of Service and Honor Code page url
    terms_of_service_and_honor_code = branding_api.get_tos_and_honor_code_url()
    if terms_of_service_and_honor_code != branding_api.EMPTY_URL:
        data.update({"company_tos_url": terms_of_service_and_honor_code})

    # get Privacy Policy page url
    privacy_policy = branding_api.get_privacy_url()
    if privacy_policy != branding_api.EMPTY_URL:
        data.update({"company_privacy_url": privacy_policy})

    # get About page url
    about = branding_api.get_about_url()
    if about != branding_api.EMPTY_URL:
        data.update({"company_about_url": about})

    return data


def certificates_viewable_for_course(course):
    """
    Returns True if certificates are viewable for any student enrolled in the course, False otherwise.

    Arguments:
        course (CourseOverview or course block): The course to check if certificates are viewable

    Returns:
        boolean: whether the certificates are viewable or not
    """

    # The CourseOverview contains validation logic on the certificates_display_behavior and certificate_available_date
    # fields. Thus, we prefer to use the CourseOverview, but will fall back to the course in case the CourseOverview is
    # not available.
    if not isinstance(course, CourseOverview):
        course_overview = get_course_overview_or_none(course.id)
        if course_overview:
            course = course_overview

    return _should_certificate_be_visible(
        course.certificates_display_behavior,
        course.certificates_show_before_end,
        course.has_ended(),
        course.certificate_available_date,
        course.self_paced,
    )


def get_allowlisted_users(course_key):
    """
    Return the users who are on the allowlist for this course run
    """
    return User.objects.filter(certificateallowlist__course_id=course_key, certificateallowlist__allowlist=True)


def create_or_update_certificate_allowlist_entry(user, course_key, notes, enabled=True):
    """
    Update-or-create an allowlist entry for a student in a given course-run.
    """
    certificate_allowlist, created = CertificateAllowlist.objects.update_or_create(
        user=user,
        course_id=course_key,
        defaults={
            "allowlist": enabled,
            "notes": notes,
        },
    )

    log.info(f"Updated the allowlist of course {course_key} with student {user.id} and enabled={enabled}")

    return certificate_allowlist, created


def remove_allowlist_entry(user, course_key):
    """
    Removes an allowlist entry for a user in a given course-run. If a certificate exists for the student in the
    course-run we will also attempt to invalidate the it before removing them from the allowlist.

    Returns the result of the removal operation as a bool.
    """
    log.info(f"Removing student {user.id} from the allowlist in course {course_key}")
    deleted = False

    allowlist_entry = get_allowlist_entry(user, course_key)
    if allowlist_entry:
        certificate = get_certificate_for_user(user.username, course_key, False)
        if certificate:
            log.info(f"Invalidating certificate for student {user.id} in course {course_key} before allowlist removal.")
            certificate.invalidate(source="allowlist_removal")

        log.info(f"Removing student {user.id} from the allowlist in course {course_key}.")
        allowlist_entry.delete()
        deleted = True

    return deleted


def get_allowlist_entry(user, course_key):
    """
    Retrieves and returns an allowlist entry for a given learner and course-run.
    """
    log.info(f"Attempting to retrieve an allowlist entry for student {user.id} in course {course_key}.")
    try:
        allowlist_entry = CertificateAllowlist.objects.get(user=user, course_id=course_key)
    except ObjectDoesNotExist:
        log.warning(f"No allowlist entry found for student {user.id} in course {course_key}.")
        return None

    return allowlist_entry


def is_on_allowlist(user, course_key):
    """
    Determines if a learner has an active allowlist entry for a given course-run.
    """
    log.info(f"Checking if student {user.id} is on the allowlist in course {course_key}")
    return _is_on_certificate_allowlist(user, course_key)


def can_be_added_to_allowlist(user, course_key):
    """
    Determines if a student is able to be added to the allowlist in a given course-run.
    """
    log.info(f"Checking if student {user.id} in course {course_key} can be added to the allowlist.")

    if not is_user_enrolled_in_course(user, course_key):
        log.info(f"Student {user.id} is not enrolled in course {course_key}")
        return False

    if is_certificate_invalidated(user, course_key):
        log.info(f"Student {user.id} is on the certificate invalidation list for course {course_key}")
        return False

    if is_on_allowlist(user, course_key):
        log.info(f"Student {user.id} already appears on allowlist in course {course_key}")
        return False

    return True


def create_certificate_invalidation_entry(certificate, user_requesting_invalidation, notes):
    """
    Invalidates a certificate with the given certificate id.
    """
    log.info(f"Creating a certificate invalidation entry linked to certificate with id {certificate.id}.")
    certificate_invalidation, __ = CertificateInvalidation.objects.update_or_create(
        generated_certificate=certificate,
        defaults={
            "active": True,
            "invalidated_by": user_requesting_invalidation,
            "notes": notes,
        },
    )

    return certificate_invalidation


def get_certificate_invalidation_entry(generated_certificate, invalidated_by=None, notes=None, active=None):
    """
    Retrieve a certificate invalidation entry for a specified certificate.

    Optionally filter the invalidation entry by who invalidated it, notes, and whether the invalidation is active.

    Args:
        generated_certificate (GeneratedCertificate): The certificate to find the invalidation entry for.
        invalidated_by (User, optional): User who invalidated the certificate. Defaults to None.
        notes (str, optional): Notes associated with the invalidation. Defaults to None.
        active (bool, optional): Whether the invalidation entry is currently active. Defaults to None.

    Returns:
        CertificateInvalidationEntry or None: The matching invalidation entry if found, else None.
    """
    log.info(
        f"Attempting to retrieve certificate invalidation entry "
        f"for certificate with id {generated_certificate.id}."
    )

    cert_filter_args = {
        "generated_certificate": generated_certificate
    }

    if invalidated_by is not None:
        cert_filter_args["invalidated_by"] = invalidated_by

    if notes is not None:
        cert_filter_args["notes"] = notes

    if active is not None:
        cert_filter_args["active"] = active

    certificate_invalidation_entry = CertificateInvalidation.objects.filter(**cert_filter_args).first()

    # If object does not exist, add a warning to the logs
    if certificate_invalidation_entry is None:
        log.warning(f"No certificate invalidation found linked to certificate with id {generated_certificate.id}.")

    return certificate_invalidation_entry


def get_allowlist(course_key):
    """
    Return the certificate allowlist for the given course run
    """
    return CertificateAllowlist.get_certificate_allowlist(course_key)


def get_enrolled_allowlisted_users(course_key):
    """
    Get all users who:
    - are enrolled in this course run
    - are allowlisted in this course run
    """
    users = CourseEnrollment.objects.users_enrolled_in(course_key)
    return users.filter(certificateallowlist__course_id=course_key, certificateallowlist__allowlist=True)


def get_enrolled_allowlisted_not_passing_users(course_key):
    """
    Get all users who:
    - are enrolled in this course run
    - are allowlisted in this course run
    - do not have a course certificate with a passing status
    """
    users = get_enrolled_allowlisted_users(course_key)
    return users.exclude(
        generatedcertificate__course_id=course_key, generatedcertificate__status__in=CertificateStatuses.PASSED_STATUSES
    )


def certificate_info_for_user(user, course_id, grade, user_is_allowlisted, user_certificate):
    """
    Returns the certificate info for a user for grade report.
    """
    certificate_is_delivered = "N"
    certificate_type = "N/A"
    status = _certificate_status(user_certificate)
    certificate_generated = status["status"] == CertificateStatuses.downloadable
    course_overview = get_course_overview_or_none(course_id)
    if not course_overview:
        return None
    can_have_certificate = _should_certificate_be_visible(
        course_overview.certificates_display_behavior,
        course_overview.certificates_show_before_end,
        course_overview.has_ended(),
        course_overview.certificate_available_date,
        course_overview.self_paced,
    )
    enrollment_mode, __ = CourseEnrollment.enrollment_mode_for_user(user, course_id)
    mode_is_verified = enrollment_mode in CourseMode.VERIFIED_MODES
    user_is_verified = grade is not None and mode_is_verified

    eligible_for_certificate = "Y" if (user_is_allowlisted or user_is_verified or certificate_generated) else "N"

    if certificate_generated and can_have_certificate:
        certificate_is_delivered = "Y"
        certificate_type = status["mode"]

    return [eligible_for_certificate, certificate_is_delivered, certificate_type]


def certificate_status_for_student(student, course_id):
    """This returns a dictionary with a key for status, and other information."""
    return _certificate_status_for_student(student, course_id)


def auto_certificate_generation_enabled():
    return _AUTO_CERTIFICATE_GENERATION.is_enabled()


def _enabled_and_instructor_paced(course):
    if auto_certificate_generation_enabled():
        return not course.self_paced
    return False


def can_show_certificate_available_date_field(course):
    return _enabled_and_instructor_paced(course)


def can_show_certificate_message(course, student, course_grade, certificates_enabled_for_course):
    """
    Returns True if a course certificate message can be shown
    """
    auto_cert_gen_enabled = auto_certificate_generation_enabled()
    has_active_enrollment = CourseEnrollment.is_enrolled(student, course.id)
    certificates_are_viewable = certificates_viewable_for_course(course)
    is_beta_tester = access.is_beta_tester(student, course.id)
    has_passed_or_is_allowlisted = _has_passed_or_is_allowlisted(course, student, course_grade)

    return (
        (auto_cert_gen_enabled or certificates_enabled_for_course)
        and has_active_enrollment
        and certificates_are_viewable
        and has_passed_or_is_allowlisted
        and (not is_beta_tester)
    )


def _course_uses_available_date(course):
    """Returns if the course has an certificate_available_date set and that it should be used"""
    display_behavior_is_valid = course.certificates_display_behavior == CertificatesDisplayBehaviors.END_WITH_DATE

    return (
        can_show_certificate_available_date_field(course)
        and course.certificate_available_date
        and display_behavior_is_valid
    )


def available_date_for_certificate(course, certificate) -> datetime:
    """
    Returns the available date to use with a certificate

    Arguments:
        course (CourseOverview or course block): The course we're checking
        certificate (GeneratedCertificate): The certificate we're getting the date for
    """
    if _course_uses_available_date(course):
        return course.certificate_available_date
    return certificate.modified_date


def display_date_for_certificate(course, certificate):
    """
    Returns the date that should be displayed on a certificate when rendered.

    If the certificate has a certificate date override associated with it, display the override date.

    Otherwise, if the course has a display behavior of "END_WITH_DATE", display the associated certificate available
    date. If the course has a display behavior of "END", we should display the end date of the course. Lastly, when the
    display behavior is "EARLY_NO_INFO" or when the course run is self-paced, we display the modified date of the
    certificate instance.

    Arguments:
        course (CourseOverview or course block): The course we're getting the date for
    Returns:
        datetime.date
    """
    try:
        if certificate.date_override:
            return certificate.date_override.date
    except ObjectDoesNotExist:
        pass

    if _course_uses_available_date(course) and course.certificate_available_date < datetime.now(UTC):
        return course.certificate_available_date
    # It is possible for a self-paced course run to end up configured with a display behavior of "END" even though it
    # shouldn't be a valid option. We must check if the course is instructor-paced here to ensure that we are selecting
    # the correct date to display.
    elif (
        not course.self_paced
        and course.certificates_display_behavior == CertificatesDisplayBehaviors.END
        and course.end
    ):
        return course.end
    else:
        return certificate.modified_date


def is_valid_pdf_certificate(cert_data):
    return cert_data.cert_status == CertificateStatuses.downloadable and cert_data.download_url


def _has_passed_or_is_allowlisted(course, student, course_grade):
    """
    Returns True if the student has passed this course run, or is on the allowlist for this course run
    """
    is_allowlisted = is_on_allowlist(student, course.id)
    has_passed = course_grade and course_grade.passed

    return has_passed or is_allowlisted


def invalidate_certificate(user_id, course_key_or_id, source):
    """
    Invalidate the user certificate in a given course if it exists and the user is not on the allowlist for this
    course run.

    This function is called in services.py and signals.py within the certificates folder. As of now,
    The call in services.py occurs when an exam attempt is rejected in the legacy exams backend, edx-proctoring.
    The call in signals.py is occurs when an exam attempt is rejected in the newer exams backend, edx-exams.
    """
    course_key = _get_key(course_key_or_id, CourseKey)
    if _is_on_certificate_allowlist(user_id, course_key):
        log.info(f"User {user_id} is on the allowlist for {course_key}. The certificate will not be invalidated.")
        return False

    try:
        generated_certificate = GeneratedCertificate.objects.get(user=user_id, course_id=course_key)
        generated_certificate.invalidate(source=source)
    except ObjectDoesNotExist:
        log.warning(
            "Invalidation failed because a certificate for user %d in course %s does not exist.", user_id, course_key
        )
        return False

    return True


def clear_pii_from_certificate_records_for_user(user):
    """
    Utility function to remove PII from certificate records when a learner's account is being retired. Used by the
    `AccountRetirementView` in the `user_api` Django app (invoked by the /api/user/v1/accounts/retire endpoint).

    The update is performed using a bulk SQL update via the Django ORM. This will not trigger the GeneratedCertificate
    model's custom `save()` function, nor fire any Django signals (which is desired at the time of writing). There is
    nothing to update in our external systems by this update.

    Args:
        user (User): The User instance of the learner actively being retired.

    Returns:
        None
    """
    GeneratedCertificate.objects.filter(user=user).update(name="")


def get_cert_history_for_course_id(course_id):
    """
    Retrieve all certificate generation history records for the specified course.

    Args:
        course_id (CourseLocator | CourseKey): The unique identifier for the course.

    Returns:
        QuerySet[CertificateGenerationHistory]: A queryset of all certificate generation history records
        associated with the specified course.
    """
    return CertificateGenerationHistory.objects.filter(course_id=course_id)


def is_certificate_generation_enabled():
    """
    Checks if certificate generation is currently enabled.

    This function queries the `CertificateGenerationConfiguration` model to retrieve the
    current configuration and returns whether certificate generation is enabled or not.

    Returns:
        bool: True if certificate generation is enabled, False otherwise.
    """
    return CertificateGenerationConfiguration.current().enabled


def set_certificate_generation_config(enabled=True):
    """
    Configures the certificate generation settings.

    Args:
        enabled (bool): If True, enables certificate generation; if False, disables it. Default is True.

    Returns:
        CertificateGenerationConfiguration: The created or updated certificate generation configuration object.
    """
    return CertificateGenerationConfiguration.objects.create(enabled=enabled)


def get_certificate_generation_history(course_id=None, generated_by=None, instructor_task=None, is_regeneration=None):
    """
    Retrieves a queryset of `CertificateGenerationHistory` records, filtered by the provided criteria.

    Args:
        course_id (Optional[CourseKey]): The unique ID of the course (if filtering by course).
        generated_by (Optional[User]): The user who generated the certificate (if filtering by user).
        instructor_task (Optional[bool]): Whether the certificate generation was triggered by an instructor task.
        is_regeneration (Optional[bool]): Whether the certificate was regenerated.

    Returns:
        QuerySet[CertificateGenerationHistory]: A queryset of filtered `CertificateGenerationHistory` records.
    """
    cert_filter_args = {}

    # Only add the filter if the corresponding argument is provided
    if course_id is not None:
        cert_filter_args["course_id"] = course_id

    if generated_by is not None:
        cert_filter_args["generated_by"] = generated_by

    if instructor_task is not None:
        cert_filter_args["instructor_task"] = instructor_task

    if is_regeneration is not None:
        cert_filter_args["is_regeneration"] = is_regeneration

    res = CertificateGenerationHistory.objects.filter(**cert_filter_args)

    return res


def create_or_update_certificate_generation_history(course_id, generated_by, instructor_task, is_regeneration):
    """
    Creates a new certificate generation history record or updates an existing one.

    Args:
        course_id (CourseKey): The unique identifier for the course run.
        generated_by (User): The user (typically an instructor or admin) who initiated the certificate generation.
        instructor_task (str): A descriptor or task identifier for the instructor-related task.
        is_regeneration (bool): A flag indicating whether the certificate is being
        regenerated (True) or newly generated (False).

    Returns:
        CertificateGenerationHistory: The created or updated CertificateGenerationHistory instance.
    """
    cert_history, created = CertificateGenerationHistory.objects.update_or_create(
        course_id=course_id,
        generated_by=generated_by,
        instructor_task=instructor_task,
        is_regeneration=is_regeneration
    )

    return cert_history


def create_or_update_eligible_certificate_for_user(user, course_id, status):
    """
    Create or update an eligible GeneratedCertificate for a user in a specific course.

    Args:
        user (User): The user for whom the certificate is being created or updated.
        course_id (CourseKey): The unique identifier for the course the certificate applies to.
        status (str): The status of the certificate (e.g., "downloadable", "issued").

    Returns:
        tuple: A tuple containing:
            - `GeneratedCertificate`: The created or updated certificate.
            - `bool`: A boolean indicating whether the certificate was created (True) or updated (False).
    """

    cert, created = GeneratedCertificate.eligible_certificates.update_or_create(
        user=user,
        course_id=course_id,
        status=status
    )

    return cert, created


def get_cert_invalidations_for_course(course_key):
    """
    Retrieves all certificate invalidations associated with the specified course.

    Args:
        course_key (CourseKey): The unique identifier for the course run.

    Returns:
        QuerySet[CertificateInvalidation]: A queryset containing all invalidations for the specified course.
    """
    return CertificateInvalidation.get_certificate_invalidations(course_key)


def get_certificates_for_course_and_users(course_id, users):
    """
    Retrieves all GeneratedCertificate records for a specific course and a list of users.

    Args:
        course_id (CourseKey): The unique identifier for the course run.
        users (Iterable[User]): A list or queryset of User instances to filter certificates by.

    Returns:
        QuerySet[GeneratedCertificate]: A queryset containing the matching certificate records.
    """
    return GeneratedCertificate.objects.filter(course_id=course_id, user__in=users)


def get_course_ids_from_certs_for_user(username):
    """
    Retrieves a list of course IDs for which the given user has generated certificates.

    Args:
        username (str): The username of the user whose course IDs are being retrieved.

    Returns:
        list: A list of course IDs for which the user has generated certificates.
              If no certificates are found, an empty list is returned.
    """
    course_ids_with_certificates = GeneratedCertificate.objects.filter(
        user__username=username
    ).values_list('course_id', flat=True)

    return list(course_ids_with_certificates)


def get_generated_certificate(user, course_id):
    """
    Retrieves the GeneratedCertificateData for the given user and course.

    Args:
        user (User): The user for whom the certificate is being fetched.
        course_id (CourseKey): The unique identifier for the course.

    Returns:
        Optional[GeneratedCertificateData]: A `GeneratedCertificateData` object if found, otherwise None.
    """
    try:
        cert = GeneratedCertificate.objects.get(user=user, course_id=course_id)
        return GeneratedCertificateData(
            user=cert.user,
            course_id=cert.course_id
        )
    except GeneratedCertificate.DoesNotExist:
        return None


def create_generated_certificate(cert_args):
    """
    Creates a new `GeneratedCertificate` object using the provided arguments.

    Args:
        cert_args (dict): A dictionary containing the certificate's attributes, such as
        "user", "course_id", "status", etc.

    Returns:
        GeneratedCertificate: The newly created `GeneratedCertificate` object.

    Raises:
        ValueError: If any required fields ("user", "course_id") are missing from `cert_args`.
    """

    required_fields = {"user", "course_id"}

    # Check if all required fields are present
    if not all(field in cert_args for field in required_fields):
        raise ValueError(f"Missing required fields: {required_fields - cert_args.keys()}")

    # Create and return the GeneratedCertificate object
    return GeneratedCertificate.objects.create(**cert_args)


def get_eligible_certificate(user, course_id):
    """
    Retrieves the eligible GeneratedCertificate for a given user and course.

    Args:
        user (User): The user object for whom the certificate is being retrieved.
        course_id (CourseKey): The unique identifier for the course run.

    Returns:
        GeneratedCertificate or None: The eligible certificate instance if one exists; otherwise, None.
    """
    try:
        return GeneratedCertificate.eligible_certificates.get(
            user=user.id,
            course_id=course_id
        )
    except GeneratedCertificate.DoesNotExist:
        return None


def get_eligible_and_available_certificates(user):
    """
    Retrieves all eligible and available certificates for the specified user.

    Args:
        user (User): The user whose eligible and available certificates are being retrieved.

    Returns:
        QuerySet[GeneratedCertificate]: A queryset containing all eligible and available certificates
        for the specified user.
    """
    return GeneratedCertificate.eligible_available_certificates.filter(user=user)


def get_certificates_by_course_and_status(course_id, status):
    """
    Retrieves all eligible certificates for a specific course and status.

    Args:
        course_id (CourseKey): The unique identifier for the course run.
        status (str): The status of the certificates to filter by (e.g., "downloadable", "issued").

    Returns:
        QuerySet[GeneratedCertificate]: A queryset containing the eligible certificates
        matching the provided course ID and status.
    """

    return GeneratedCertificate.eligible_certificates.filter(
        course_id=course_id,
        status=status
    )


def get_unique_certificate_statuses(course_key):
    """
    Retrieves the unique certificate statuses for the specified course.

    Args:
        course_key (CourseKey): The unique identifier for the course run.

    Returns:
        QuerySet[str]: A queryset containing the unique certificate statuses for the specified course.
    """
    return GeneratedCertificate.get_unique_statuses(course_key=course_key)
