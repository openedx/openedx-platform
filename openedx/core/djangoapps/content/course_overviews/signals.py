"""
Signal handler for invalidating cached course overviews
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import Signal
from django.dispatch.dispatcher import receiver
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun

from openedx.core.djangoapps.signals.signals import COURSE_CERT_DATE_CHANGE
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore.django import SignalHandler

from .models import CourseOverview

LOG = logging.getLogger(__name__)

# providing_args=["updated_course_overview", "previous_start_date"]
COURSE_START_DATE_CHANGED = Signal()
# providing_args=["updated_course_overview", "previous_self_paced"]
COURSE_PACING_CHANGED = Signal()
# providing_args=["courserun_key"]
IMPORT_COURSE_DETAILS = Signal()
# providing_args=["courserun_key"]
DELETE_COURSE_DETAILS = Signal()


@receiver(SignalHandler.course_published)
def _listen_for_course_publish(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Catches the signal that a course has been published in Studio and updates the corresponding CourseOverview cache
    entry.

    Also sync course data to the openedx_catalog CourseRun model.
    """
    try:
        previous_course_overview = CourseOverview.objects.get(id=course_key)
    except CourseOverview.DoesNotExist:
        previous_course_overview = None
    updated_course_overview = CourseOverview.load_from_module_store(course_key)
    _check_for_course_changes(previous_course_overview, updated_course_overview)

    # Currently, SplitModulestoreCourseIndex is the ultimate source of truth for
    # which courses exist. When a course is published, we sync that data to
    # CourseOverview, and from CourseOverview to CourseRun.

    # In the future, CourseRun will be the "source of truth" and each CourseRun
    # may optionally point to content and get synced to CourseOverview.

    # Ensure a CourseRun exists for this course
    try:
        course_run = catalog_api.get_course_run(course_key)
    except CourseRun.DoesNotExist:
        # Presumably this is a newly-created course. Create the CourseRun.
        course_run = catalog_api.create_course_run_for_modulestore_course_with(
            course_key=course_key,
            title=updated_course_overview.display_name,
            language_short=updated_course_overview.language,
        )

    # Keep the CourseRun up to date as the course is edited:
    if updated_course_overview.display_name != course_run.title:
        catalog_api.sync_course_run_details(course_key, title=updated_course_overview.display_name)
        # If this course is the only run in the CatalogCourse, should we update the title of
        # the CatalogCourse to match the run's new title? Currently the only way to edit the name of
        # a CatalogCourse is via the Django admin. But it's also not used anywhere yet.

    if (
        updated_course_overview.language
        and updated_course_overview.language != course_run.catalog_course.language_short
    ):
        if course_run.catalog_course.runs.count() == 1:
            # This is the only run in this CatalogCourse. Update the language of the CatalogCourse
            catalog_api.update_catalog_course(
                course_run.catalog_course,
                language_short=updated_course_overview.language,
            )
        else:
            LOG.warning(
                'Course run "%s" language "%s" does not match its catalog course language, "%s"',
                str(course_key),
                updated_course_overview.language,
                course_run.catalog_course.language_short,
            )

    # In the future, this will also sync schedule and other metadata to the CourseRun's related models


@receiver(SignalHandler.course_deleted)
def _listen_for_course_delete(sender, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Catches the signal that a course has been deleted from Studio and
    invalidates the corresponding CourseOverview cache entry if one exists.
    """
    CourseOverview.objects.filter(id=course_key).delete()
    courserun_key = str(course_key)
    LOG.info(f'DELETE_COURSE_DETAILS triggered upon course_deleted signal. Key: [{courserun_key}]')
    # This signal will be handled in `federated_content_connector` plugin
    DELETE_COURSE_DETAILS.send(
        sender=None,
        courserun_key=courserun_key,
    )
    # Delete the openedx_catalog CourseRun to keep it in sync:
    try:
        course_run_obj = catalog_api.get_course_run(course_key)
    except CourseRun.DoesNotExist:
        pass
    else:
        catalog_course = course_run_obj.catalog_course
        catalog_api.delete_course_run(course_key)
        if catalog_course.runs.count() == 0:
            catalog_api.delete_catalog_course(catalog_course)


@receiver(post_save, sender=CourseOverview)
def trigger_import_course_details_signal(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """
    Triggers the `IMPORT_COURSE_DETAILS` signal which will be handled in `federated_content_connector` plugin
    """
    if created:
        courserun_key = str(instance.id)
        LOG.info(f'IMPORT_COURSE_DETAILS triggered upon CourseOverview.post_save signal. Key: [{courserun_key}]')
        IMPORT_COURSE_DETAILS.send(
            sender=None,
            courserun_key=courserun_key,
        )


def _check_for_course_changes(previous_course_overview, updated_course_overview):
    """
    Utility function responsible for calling other utility functions that check for specific changes in a course
    overview after a course run has been updated and published.

    Args:
        previous_course_overview (CourseOverview): the current course overview instance for a particular course run
        updated_course_overview (CourseOverview): an updated course overview instance, reflecting the current state of
        data from the modulestore/Mongo

    Returns:
        None
    """
    if previous_course_overview:
        _check_for_course_start_date_changes(previous_course_overview, updated_course_overview)
        _check_for_pacing_changes(previous_course_overview, updated_course_overview)
        _check_for_cert_date_changes(previous_course_overview, updated_course_overview)


def _check_for_course_start_date_changes(previous_course_overview, updated_course_overview):
    """
    Checks if a course run's start date has been updated. If so, we emit the `COURSE_START_DATE_CHANGED` signal to
    ensure other parts of the system are aware of the change.

    Args:
        previous_course_overview (CourseOverview): the current course overview instance for a particular course run
        updated_course_overview (CourseOverview): an updated course overview instance, reflecting the current state of
        data from the modulestore/Mongo

    Returns:
        None
    """
    if previous_course_overview.start != updated_course_overview.start:
        _log_start_date_change(previous_course_overview, updated_course_overview)
        COURSE_START_DATE_CHANGED.send(
            sender=None,
            updated_course_overview=updated_course_overview,
            previous_start_date=previous_course_overview.start,
        )


def _log_start_date_change(previous_course_overview, updated_course_overview):
    """
    Utility function to log a course run's start date when updating a course overview. This log only appears when the
    start date has been changed (see the `_check_for_course_date_changes` function above).

    Args:
        previous_course_overview (CourseOverview): the current course overview instance for a particular course run
        updated_course_overview (CourseOverview): an updated course overview instance, reflecting the current state of
        data from the modulestore/Mongo

    Returns:
        None
    """
    previous_start_str = 'None'
    if previous_course_overview.start is not None:
        previous_start_str = previous_course_overview.start.isoformat()
    new_start_str = 'None'
    if updated_course_overview.start is not None:
        new_start_str = updated_course_overview.start.isoformat()
    LOG.info(
        f"Course start date changed: course={updated_course_overview.id} previous={previous_start_str} "
        f"new={new_start_str}"
    )


def _check_for_pacing_changes(previous_course_overview, updated_course_overview):
    """
    Checks if a course run's pacing has been updated. If so, we emit the `COURSE_PACING_CHANGED` signal to ensure other
    parts of the system are aware of the change. The `programs` and `certificates` apps listen for this signal in
    order to manage certificate generation features in the LMS and certificate visibility settings in the Credentials
    IDA.

    Args:
        previous_course_overview (CourseOverview): the current course overview instance for a particular course run
        updated_course_overview (CourseOverview): an updated course overview instance, reflecting the current state of
        data from the modulestore/Mongo

    Returns:
        None
    """
    if previous_course_overview.self_paced != updated_course_overview.self_paced:
        COURSE_PACING_CHANGED.send(
            sender=None,
            updated_course_overview=updated_course_overview,
            previous_self_paced=previous_course_overview.self_paced,
        )


def _check_for_cert_date_changes(previous_course_overview, updated_course_overview):
    """
    Checks if the certificate available date (CAD) or the certificates display behavior (CDB) of a course run has
    changed during a course overview update. If so, we emit the COURSE_CERT_DATE_CHANGE signal to ensure other parts of
    the system are aware of the change. The `credentials` app listens for this signal in order to keep our certificate
    visibility settings in the Credentials IDA up to date.

    Args:
        previous_course_overview (CourseOverview): the current course overview instance for a particular course run
        updated_course_overview (CourseOverview): an updated course overview instance, reflecting the current state of
            data from the modulestore/Mongo

    Returns:
        None
    """
    def _send_course_cert_date_change_signal():
        """
        A callback used to fire the COURSE_CERT_DATE_CHANGE Django signal *after* the ORM has successfully commited the
        update.
        """
        COURSE_CERT_DATE_CHANGE.send_robust(sender=None, course_key=str(updated_course_overview.id))

    course_run_id = str(updated_course_overview.id)
    prev_available_date = previous_course_overview.certificate_available_date
    prev_display_behavior = previous_course_overview.certificates_display_behavior
    prev_end_date = previous_course_overview.end  # `end_date` is a deprecated field, use `end` instead
    updated_available_date = updated_course_overview.certificate_available_date
    updated_display_behavior = updated_course_overview.certificates_display_behavior
    updated_end_date = updated_course_overview.end  # `end_date` is a deprecated field, use `end` instead
    send_signal = False

    if prev_available_date != updated_available_date:
        LOG.info(
            f"The certificate available date for {course_run_id} has changed from {prev_available_date} to "
            f"{updated_available_date}"
        )
        send_signal = True

    if prev_display_behavior != updated_display_behavior:
        LOG.info(
            f"The certificates display behavior for {course_run_id} has changed from {prev_display_behavior} to "
            f"{updated_display_behavior}"
        )
        send_signal = True

    # edge case -- if a course run with a cert display behavior of "End date of course" has changed its end date, we
    # should fire our signal to ensure visibility of certificates managed by the Credentials IDA are corrected too
    if (updated_display_behavior == CertificatesDisplayBehaviors.END and prev_end_date != updated_end_date):
        LOG.info(
            f"The end date for {course_run_id} has changed from {prev_end_date} to {updated_end_date}."
        )
        send_signal = True

    if send_signal:
        transaction.on_commit(_send_course_cert_date_change_signal)
