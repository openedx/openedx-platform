"""
This file contains Celery tasks and utility functions supporting the Credentials IDA.
"""
import math
import time
from urllib.parse import urljoin

from celery import shared_task
from celery.utils.log import get_task_logger
from celery_utils.logged_task import LoggedTask
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from edx_django_utils.monitoring import set_code_owner_attribute
from MySQLdb import OperationalError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.certificates.api import (
    get_generated_certificate,
    get_recently_modified_certificates
)
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.grades.api import CourseGradeFactory, get_recently_modified_grades
from openedx.core.djangoapps.catalog.utils import get_programs
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.credentials.helpers import is_learner_records_enabled_for_org
from openedx.core.djangoapps.credentials.api import is_credentials_enabled
from openedx.core.djangoapps.credentials.utils import get_credentials_api_base_url, get_credentials_api_client
from openedx.core.djangoapps.programs.signals import (
    handle_course_cert_awarded,
    handle_course_cert_changed,
    handle_course_cert_revoked,
)
from openedx.core.djangoapps.programs.tasks import update_certificate_available_date_on_course_update
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration

User = get_user_model()
logger = get_task_logger(__name__)

# "Interesting" means "The Credentials IDA will want to know about it"
INTERESTING_MODES = CourseMode.CERTIFICATE_RELEVANT_MODES
INTERESTING_STATUSES = [
    CertificateStatuses.notpassing,
    CertificateStatuses.downloadable,
]


@shared_task(
    bind=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=10,
    retry_backoff=30,
    retry_backoff_max=600,
    retry_jitter=True,
)
@set_code_owner_attribute
def send_grade_to_credentials(
    self,
    username,
    course_run_key,
    verified,
    letter_grade,
    percent_grade,
    grade_last_updated
):
    """
    Celery task to notify the Credentials IDA of an "interesting" grade change via an API call.

    If an exception occurs when trying to send data to the Credentials IDA, we will retry the task a maximum number of
    11 times (initial attempt + 10 retries). We are relying on built-in functionality of Celery to add a randomized
    jitter to the retries so that the tasks don't retry exactly at the same time.

    Args:
        username (string): The username of the learner we are currently processing
        course_run_key (string): String identifier of the course run associated with the grade update
        verified (bool): Boolean determining if the course run is in a "verified" track
        letter_grade (string): String identifier describing the "letter" grade the learner has earned
        percent_grade (float): Number representing the learner's grade in this course run
        grade_last_updated (string): String describing the last time this grade was modified in the LMS
    """
    data = {
        'username': username,
        'course_run': course_run_key,
        'letter_grade': letter_grade,
        'percent_grade': percent_grade,
        'verified': verified,
        'lms_last_updated_at': grade_last_updated
    }
    logger.info(f"Running task `send_grade_to_credentials` for username {username} with data: {data}")

    course_key = CourseKey.from_string(course_run_key)
    credentials_client = get_credentials_api_client(User.objects.get(username=settings.CREDENTIALS_SERVICE_USERNAME))
    api_url = urljoin(f"{get_credentials_api_base_url(org=course_key.org)}/", "grades/")

    response = credentials_client.post(
        api_url,
        data=data,
    )
    response.raise_for_status()
    logger.info(f"Sent grade for user {username} in course {course_run_key} to Credentials")


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def handle_notify_credentials(options, course_keys):
    """
    Celery task to handle the notify_credentials management command. Finds the relevant cert and grade records, then
    starts other celery tasks to send the data.

    Args:
        options (dict): Dictionary containing settings for this run of the `handle_notify_credentials` task
        course_keys (list[string]): List of course run keys for this run of the `handle_notify_credentials` task
    """
    try:
        site_config = SiteConfiguration.objects.get(site__domain=options['site']) if options['site'] else None
    except SiteConfiguration.DoesNotExist:
        logger.exception('No site configuration found for site %s', options['site'])
        return

    # If a `start_date` or `end_date` are included in the options we should also include certificates with a
    # CertificateDateOveride modified within the time range
    certs = get_recently_modified_certificates(
        course_keys,
        options['start_date'],
        options['end_date'],
        options['user_ids']
    )

    users = None
    if options['user_ids']:
        users = User.objects.filter(id__in=options['user_ids'])

    grades = get_recently_modified_grades(
        course_keys,
        options['start_date'],
        options['end_date'],
        users
    )

    logger.info(
        f"[notify_credentials] Sending notifications for {certs.count()} certificates and {grades.count()} grades"
    )
    if options['dry_run']:
        log_dry_run(certs, grades)
    else:
        send_notifications(
            certs,
            grades,
            site_config=site_config,
            delay=options['delay'],
            page_size=options['page_size'],
            verbose=options['verbose'],
            notify_programs=options['notify_programs'],
            revoke_program_certs=options['revoke_program_certs']
        )


def send_notifications(
    certs,
    grades,
    site_config=None,
    delay=0,
    page_size=100,
    verbose=False,
    notify_programs=False,
    revoke_program_certs=False
):
    """
    Utility function responsible for bootstrapping certificate and grade updates to the Credentials IDA. We do this by
    invoking existing code paths that should force the LMS to (re-)send data to Credentials.

    First, we directly call our Django signal handlers responsible for updating the status of certificates throughout
    the system. This will trigger attempts to send certificate status information to the Credentials IDA.

    Then, we call a utility function (`send_grade_if_interesting`) that is responsible for determining if the
    Credentials IDA cares about this particular grade update for a learner.

    Args:
        certs (QuerySet): A collection of course certificates associated with the users we need to update
        grades (QuerySet): A collection of grades associated with the users we need to update
        site_config (SiteConfiguration): Optional, may be None. The site associated with the users we need to update.
        delay (int): Delay (in seconds) to wait between queries
        page_size (int): Number of records to process from the certs or grades QuerySet at once. Used to handle load.
        verbose (bool): Used to determine level of logging output during grade updates
        notify_programs (bool): Used to determine if an update should be sent to Credentials
        revoke_program_certs (bool): Used to determine if the system should attempt revoking program certificates during
         this run of the `notify_credentials` management command
    """
    course_cert_info = {}
    # First, do certs
    for i, cert in paged_query(certs, delay, page_size):
        if site_config and not site_config.has_org(cert.course_id.org):
            logger.info("Skipping credential changes %d for certificate %s", i, certstr(cert))
            continue

        logger.info(f"Handling credential change {i} for certificate {certstr(cert)}")
        signal_args = {
            'sender': None,
            'user': cert.user,
            'course_key': cert.course_id,
            'mode': cert.mode,
            'status': cert.status,
            'verbose': verbose,
        }

        data = {
            'mode': cert.mode,
            'status': cert.status
        }
        course_cert_info[(cert.user.id, str(cert.course_id))] = data
        # handles awarding course certificates in Credentials
        handle_course_cert_changed(**signal_args)
        # handles awarding program certificates in Credentials
        if notify_programs and CertificateStatuses.is_passing_status(cert.status):
            handle_course_cert_awarded(**signal_args)
        # handles revoking program certificates in Credentials
        if revoke_program_certs and notify_programs and not CertificateStatuses.is_passing_status(cert.status):
            handle_course_cert_revoked(**signal_args)

    # Then do grades
    for i, grade in paged_query(grades, delay, page_size):
        if site_config and not site_config.has_org(grade.course_id.org):
            logger.info(f"Skipping grade change {i} for grade in {gradestr(grade)}")
            continue

        logger.info(f"Handling grade change {i} for grade in {gradestr(grade)}")
        user = User.objects.get(id=grade.user_id)

        # Grab mode/status from cert call
        key = (user.id, str(grade.course_id))
        cert_info = course_cert_info.get(key, {})
        mode = cert_info.get('mode', None)
        status = cert_info.get('status', None)

        send_grade_if_interesting(
            user,
            grade.course_id,
            mode,
            status,
            grade.letter_grade,
            grade.percent_grade,
            grade_last_updated=grade.modified,
            verbose=verbose
        )


def paged_query(queryset, delay, page_size):
    """
    A generator that iterates through a queryset but only resolves chunks of it at once, to avoid overwhelming memory
    with a giant query. Also adds an optional delay between yields, to help with load.
    """
    count = queryset.count()
    pages = int(math.ceil(count / page_size))

    for page in range(pages):
        page_start = page * page_size
        page_end = page_start + page_size
        subquery = queryset[page_start:page_end]

        if delay and page:
            time.sleep(delay)
        index = 0
        try:
            for item in subquery.iterator():
                index += 1
                yield page_start + index, item
        except OperationalError:
            # When running the notify_credentials command locally there is an
            # OperationalError thrown by MySQL when there are no more results
            # available for the queryset iterator. This change catches that exception,
            # checks state, and then logs according to that state. This code runs in
            # production without issue. This changes allows for the code to be run
            # locally without a separate code path.
            if index == count:
                logger.info('OperationalError Exception caught, all known results processed in paged_query')
            else:
                logger.warning('OperationalError Exception caught, it is possible some results were missed')
            continue


def log_dry_run(certs, grades):
    """Give a preview of what certs/grades we will handle."""
    logger.info("DRY-RUN: This task would have handled changes for...")
    ITEMS_TO_SHOW = 10

    logger.info(f"{certs.count()} Certificates:")
    for cert in certs[:ITEMS_TO_SHOW]:
        logger.info(f"\t{certstr(cert)}")
    if certs.count() > ITEMS_TO_SHOW:
        logger.info(f"\t(+ {certs.count() - ITEMS_TO_SHOW} more)")

    logger.info(f"{grades.count()} Grades:")
    for grade in grades[:ITEMS_TO_SHOW]:
        logger.info(f"\t{gradestr(grade)}")
    if grades.count() > ITEMS_TO_SHOW:
        logger.info(f"\t(+ {grades.count() - ITEMS_TO_SHOW} more)")


def certstr(cert):
    return f'{cert.course_id} for user {cert.user.id}'


def gradestr(grade):
    return f'{grade.course_id} for user {grade.user_id}'


def send_grade_if_interesting(
    user,
    course_run_key,
    mode,
    status,
    letter_grade,
    percent_grade,
    grade_last_updated=None,
    verbose=False
):
    """
    Checks if a grade is interesting to Credentials and schedules a Celery task if so. This is Credentials business
    logic that has bled into the LMS. We want to filter here in order to not flood our task queue with a bunch of
    signals, so we put up with it.

    Args:
        user (User): User associated with this grade update
        course_run_key (CourseLocator): The course run key associated with this grade update
        mode (string): The "mode" for the specific course run (e.g. "verified", "audit", etc.)
        status (string): The status of the Certificate associated with this grade update
        letter_grade (string): The letter grade associated with this grade update (e.g. "A", "B", or "pass" or "fail")
        percent_grade (float): A number representing the learner's grade in this course run
        grade_last_updated (DateTime): DateTime object representing the last time the (percent) grade was updated in the
         LMS.
        verbose (bool): A value determining the logging level desired for this grade update
    """
    warning_base = f"Skipping send grade for user {user} in course run {course_run_key}:"

    if verbose:
        logger.info(
            f"Starting send_grade_if_interesting with params: user [{user}], course_run_key [{course_run_key}], mode "
            f"[{mode}], status [{status}], letter_grade [{letter_grade}], percent_grade [{percent_grade}], "
            f"grade_last_updated [{grade_last_updated}, verbose [{verbose}]"
        )

    if not is_credentials_enabled():
        if verbose:
            logger.warning(f"{warning_base} use of the Credentials IDA is disabled by config")
        return

    # avoid scheduling tasks if the learner records feature has been disabled for this org
    if not is_learner_records_enabled_for_org(course_run_key.org):
        if verbose:
            logger.warning(f"{warning_base} the learner records feature is disabled for the org {course_run_key.org}")
        return

    # If we don't have mode and/or status, retrieve them from the learner's certificate record
    if mode is None or status is None:
        cert = get_generated_certificate(user=user, course_id=course_run_key)
        if cert is None:
            if verbose:
                logger.warning(f"{warning_base} no certificate record in the specified course run")
            return

        mode = cert.mode
        status = cert.status

    # Don't worry about the certificate record being in a passing or awarded status. Having a certificate record in any
    # status is good enough to record a verified attempt at a course. The Credentials IDA keeps track of how many times
    # a learner has made an attempt at a course run of a course, so it wants to know about all the learner's efforts.
    # This check is attempt to prevent updates being sent to Credentials that it does not care about (e.g. updates
    # related to a legacy Audit course)
    if (
        mode not in INTERESTING_MODES
        and not CourseMode.is_eligible_for_certificate(mode)
        or status not in INTERESTING_STATUSES
    ):
        if verbose:
            logger.warning(f"{warning_base} mode ({mode}) or status ({status}) is not interesting to Credentials")
        return

    # don't bother sending an update if the course run is not associated with any programs
    if not is_course_run_in_a_program(course_run_key):
        if verbose:
            logger.warning(f"{warning_base} course run is not associated with any programs")
        return

    # grab additional grade data if we don't have it in hand
    if letter_grade is None or percent_grade is None or grade_last_updated is None:
        grade = CourseGradeFactory().read(user, course_key=course_run_key, create_if_needed=False)
        if grade is None:
            if verbose:
                logger.warning(f"{warning_base} no grade found for user in the specified course run")
            return
        letter_grade = grade.letter_grade
        percent_grade = grade.percent
        grade_last_updated = grade.last_updated

    send_grade_to_credentials.delay(
        user.username,
        str(course_run_key),
        True,
        letter_grade,
        percent_grade,
        grade_last_updated
    )


def is_course_run_in_a_program(course_run_key):
    """
    Returns true if the given course key is in any program at all. This functionality depends on data to be present in
    the program cache. We don't have an easy way to determine if a course run is in a program, so we must search through
    each program (on a site-by-site basis).

    Args:
        course_run_key (CourseLocator): The course run key used we are using to verify program membership

    Returns:
        A boolean describing if the course run is part of a program
    """
    sites = Site.objects.all()
    str_key = str(course_run_key)
    for site in sites:
        for program in get_programs(site):
            for course in program['courses']:
                for course_run in course['course_runs']:
                    if str_key == course_run['key']:
                        return True
    return False


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def backfill_date_for_all_course_runs():
    """
    This task enqueues an `update_certificate_available_date_on_course_update` subtask for each course overview in the
    system in order to determine and update the certificate date stored by the Credentials IDA.
    """
    course_overviews = CourseOverview.objects.all()
    for index, course_overview in enumerate(course_overviews):
        logger.info(
            "Enqueueing an `update_certificate_available_date_on_course_update` task for course run "
            f"`{course_overview.id}`, self_paced={course_overview.self_paced}, end={course_overview.end}, "
            f"available_date={course_overview.certificate_available_date}, and "
            f"display_behavior={course_overview.certificates_display_behavior}"
        )
        update_certificate_available_date_on_course_update.delay(str(course_overview.id))

        if index % 10 == 0:
            time.sleep(3)
