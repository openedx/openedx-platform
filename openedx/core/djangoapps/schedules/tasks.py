# pylint: disable=missing-module-docstring

import datetime
import logging
from importlib import import_module

from celery import current_app, shared_task
from celery_utils.logged_task import LoggedTask
from celery_utils.persist_on_failure import LoggedPersistOnFailureTask
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db.utils import DatabaseError
from edx_ace import ace
from edx_ace.message import Message
from edx_ace.utils.date import deserialize, serialize
from edx_django_utils.monitoring import (
    set_code_owner_attribute,
    set_code_owner_attribute_from_module,
    set_custom_attribute,
)
from eventtracking import tracker
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.track import segment
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.schedules import message_types, resolvers
from openedx.core.djangoapps.schedules.models import Schedule, ScheduleConfig
from openedx.core.lib.celery.task_utils import emulate_http_request

LOG = logging.getLogger(__name__)


ROUTING_KEY = getattr(settings, 'ACE_ROUTING_KEY', None)
KNOWN_RETRY_ERRORS = (  # Errors we expect occasionally that could resolve on retry
    DatabaseError,
    ValidationError,
)


RECURRING_NUDGE_LOG_PREFIX = 'Recurring Nudge'
UPGRADE_REMINDER_LOG_PREFIX = 'Upgrade Reminder'
COURSE_UPDATE_LOG_PREFIX = 'Course Update'
COURSE_NEXT_SECTION_UPDATE_LOG_PREFIX = 'Course Next Section Update'


@shared_task(base=LoggedPersistOnFailureTask, bind=True, default_retry_delay=30)
@set_code_owner_attribute
def update_course_schedules(self, **kwargs):  # pylint: disable=missing-function-docstring
    course_key = CourseKey.from_string(kwargs['course_id'])
    new_start_date = deserialize(kwargs['new_start_date_str'])
    new_upgrade_deadline = deserialize(kwargs['new_upgrade_deadline_str'])

    try:
        Schedule.objects.filter(enrollment__course_id=course_key).update(
            start_date=new_start_date,
            upgrade_deadline=new_upgrade_deadline
        )
    except Exception as exc:
        if not isinstance(exc, KNOWN_RETRY_ERRORS):
            LOG.exception(f"Unexpected failure: task id: {self.request.id}, kwargs={kwargs}")
        raise self.retry(kwargs=kwargs, exc=exc)  # noqa: B904


class ScheduleMessageBaseTask(LoggedTask):  # pylint: disable=abstract-method
    """
    Base class for top-level Schedule tasks that create subtasks.
    """
    ignore_result = True
    routing_key = ROUTING_KEY
    enqueue_config_var = None  # define in subclass
    log_prefix = None
    resolver = None  # define in subclass
    async_send_task = None  # define in subclass

    @classmethod
    def log_debug(cls, message, *args, **kwargs):
        """
        Wrapper around LOG.debug that prefixes the message.
        """
        LOG.debug(cls.log_prefix + ': ' + message, *args, **kwargs)

    @classmethod
    def log_info(cls, message, *args, **kwargs):
        """
        Wrapper around LOG.info that prefixes the message.
        """
        LOG.info(cls.log_prefix + ': ' + message, *args, **kwargs)

    @classmethod
    def is_enqueue_enabled(cls, site):
        if cls.enqueue_config_var:
            return getattr(ScheduleConfig.current(site), cls.enqueue_config_var)
        return False


class BinnedScheduleMessageBaseTask(ScheduleMessageBaseTask):
    """
    Base class for top-level Schedule tasks that create subtasks
    for each Bin.
    """
    num_bins = resolvers.DEFAULT_NUM_BINS
    task_instance = None

    @classmethod
    def enqueue(cls, site, current_date, day_offset, override_recipient_email=None, override_middlewares=None):  # pylint: disable=missing-function-docstring
        set_code_owner_attribute_from_module(__name__)
        current_date = resolvers._get_datetime_beginning_of_day(current_date)  # pylint: disable=protected-access

        if not cls.is_enqueue_enabled(site):
            cls.log_info('Message queuing disabled for site %s', site.domain)
            return

        target_date = current_date + datetime.timedelta(days=day_offset)
        cls.log_info('Target date = %s', target_date.isoformat())
        for bin in range(cls.num_bins):  # pylint: disable=redefined-builtin
            task_args = (
                site.id,
                serialize(target_date),
                day_offset,
                bin,
                override_recipient_email,
                override_middlewares,
            )
            cls.log_info('Launching task with args = %r', task_args)
            cls.task_instance.apply_async(
                task_args,
                retry=False,
            )

    def run(  # pylint: disable=arguments-differ
        self, site_id, target_day_str, day_offset, bin_num, override_recipient_email=None, override_middlewares=None,
    ):
        set_code_owner_attribute_from_module(__name__)
        site = Site.objects.select_related('configuration').get(id=site_id)
        middlewares = [self.class_from_classpath(cls) for cls in override_middlewares] if override_middlewares else None
        with emulate_http_request(site=site, middleware_classes=middlewares) as request:
            msg_type = self.make_message_type(day_offset)
            _annotate_for_monitoring(msg_type, request.site, bin_num, target_day_str, day_offset)
            return self.resolver(  # pylint: disable=not-callable
                self.async_send_task,
                request.site,
                deserialize(target_day_str),
                day_offset,
                bin_num,
                override_recipient_email=override_recipient_email,
            ).send(msg_type)

    def make_message_type(self, day_offset):
        raise NotImplementedError

    def class_from_classpath(self, class_path):
        module_name, klass = class_path.rsplit('.', 1)
        module = import_module(module_name)
        return getattr(module, klass)


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def _recurring_nudge_schedule_send(site_id, msg_str):
    _schedule_send(
        msg_str,
        site_id,
        'deliver_recurring_nudge',
        RECURRING_NUDGE_LOG_PREFIX,
    )


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def _upgrade_reminder_schedule_send(site_id, msg_str):
    _schedule_send(
        msg_str,
        site_id,
        'deliver_upgrade_reminder',
        UPGRADE_REMINDER_LOG_PREFIX,
    )


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def _course_update_schedule_send(site_id, msg_str):
    _schedule_send(
        msg_str,
        site_id,
        'deliver_course_update',
        COURSE_UPDATE_LOG_PREFIX,
    )


class ScheduleRecurringNudge(BinnedScheduleMessageBaseTask):  # pylint: disable=missing-class-docstring
    num_bins = resolvers.RECURRING_NUDGE_NUM_BINS
    enqueue_config_var = 'enqueue_recurring_nudge'
    log_prefix = RECURRING_NUDGE_LOG_PREFIX
    resolver = resolvers.RecurringNudgeResolver
    async_send_task = _recurring_nudge_schedule_send

    def make_message_type(self, day_offset):
        return message_types.RecurringNudge(abs(day_offset))
# Save the task instance on the class object so that it's accessible via the cls argument to enqueue
ScheduleRecurringNudge.task_instance = current_app.register_task(ScheduleRecurringNudge())
ScheduleRecurringNudge = ScheduleRecurringNudge.task_instance


class ScheduleUpgradeReminder(BinnedScheduleMessageBaseTask):  # pylint: disable=missing-class-docstring
    num_bins = resolvers.UPGRADE_REMINDER_NUM_BINS
    enqueue_config_var = 'enqueue_upgrade_reminder'
    log_prefix = UPGRADE_REMINDER_LOG_PREFIX
    resolver = resolvers.UpgradeReminderResolver
    async_send_task = _upgrade_reminder_schedule_send

    def make_message_type(self, day_offset):
        return message_types.UpgradeReminder()
# Save the task instance on the class object so that it's accessible via the cls argument to enqueue
ScheduleUpgradeReminder.task_instance = current_app.register_task(ScheduleUpgradeReminder())
ScheduleUpgradeReminder = ScheduleUpgradeReminder.task_instance


class ScheduleCourseUpdate(BinnedScheduleMessageBaseTask):  # pylint: disable=missing-class-docstring
    num_bins = resolvers.COURSE_UPDATE_NUM_BINS
    enqueue_config_var = 'enqueue_course_update'
    log_prefix = COURSE_UPDATE_LOG_PREFIX
    resolver = resolvers.CourseUpdateResolver
    async_send_task = _course_update_schedule_send

    def make_message_type(self, day_offset):
        return message_types.CourseUpdate()
# Save the task instance on the class object so that it's accessible via the cls argument to enqueue
ScheduleCourseUpdate.task_instance = current_app.register_task(ScheduleCourseUpdate())
ScheduleCourseUpdate = ScheduleCourseUpdate.task_instance


class ScheduleCourseNextSectionUpdate(ScheduleMessageBaseTask):  # pylint: disable=missing-class-docstring
    enqueue_config_var = 'enqueue_course_update'
    log_prefix = COURSE_NEXT_SECTION_UPDATE_LOG_PREFIX
    resolver = resolvers.CourseNextSectionUpdate
    async_send_task = _course_update_schedule_send
    task_instance = None

    @classmethod
    def enqueue(cls, site, current_date, day_offset, override_recipient_email=None):  # pylint: disable=missing-function-docstring
        set_code_owner_attribute_from_module(__name__)
        target_datetime = (current_date - datetime.timedelta(days=day_offset))

        if not cls.is_enqueue_enabled(site):
            cls.log_info('Message queuing disabled for site %s', site.domain)
            return

        cls.log_info('Target date = %s', target_datetime.date().isoformat())
        for course_key in CourseOverview.get_all_course_keys():
            task_args = (
                site.id,
                serialize(target_datetime),  # Need to leave as a datetime for serialization purposes here
                str(course_key),  # Needs to be a string for celery to properly process
                override_recipient_email,
            )
            cls.log_info('Launching task with args = %r', task_args)
            cls.task_instance.apply_async(
                task_args,
                retry=False,
            )

    def run(self, site_id, target_day_str, course_key, override_recipient_email=None):  # pylint: disable=arguments-differ
        set_code_owner_attribute_from_module(__name__)
        site = Site.objects.select_related('configuration').get(id=site_id)
        with emulate_http_request(site=site):
            _annotate_for_monitoring(message_types.CourseUpdate(), site, 0, target_day_str, -1)
            return self.resolver(
                self.async_send_task,
                site,
                deserialize(target_day_str),
                str(course_key),
                override_recipient_email,
            ).send()
# Save the task instance on the class object so that it's accessible via the cls argument to enqueue
ScheduleCourseNextSectionUpdate.task_instance = current_app.register_task(ScheduleCourseNextSectionUpdate())
ScheduleCourseNextSectionUpdate = ScheduleCourseNextSectionUpdate.task_instance


def _schedule_send(msg_str, site_id, delivery_config_var, log_prefix):  # pylint: disable=missing-function-docstring
    site = Site.objects.select_related('configuration').get(pk=site_id)
    if _is_delivery_enabled(site, delivery_config_var, log_prefix):
        msg = Message.from_string(msg_str)
        msg.options['skip_disable_user_policy'] = True

        user = User.objects.get(id=msg.recipient.lms_user_id)
        if not user.has_usable_password():
            LOG.info(f'{delivery_config_var} Scheduled email User is disabled {user.username}')
            return
        with emulate_http_request(site=site, user=user):
            _annonate_send_task_for_monitoring(msg)
            LOG.debug('%s: Sending message = %s', log_prefix, msg_str)
            ace.send(msg)
            _track_message_sent(site, user, msg)


def _track_message_sent(site, user, msg):  # pylint: disable=missing-function-docstring
    properties = {
        'site': site.domain,
        'app_label': msg.app_label,
        'name': msg.name,
        'language': msg.language,
        'uuid': str(msg.uuid),
        'send_uuid': str(msg.send_uuid),
        'nonInteraction': 1,
    }
    course_ids = msg.context.get('course_ids', [])
    properties['num_courses'] = len(course_ids)
    if len(course_ids) > 0:
        properties['course_ids'] = course_ids[:10]
        properties['primary_course_id'] = course_ids[0]

    tracking_context = {
        'host': site.domain,
        'path': '/',  # make up a value, in order to allow the host to be passed along.
    }
    # I wonder if the user of this event should be the recipient, as they are not the ones
    # who took an action.  Rather, the system is acting, and they are the object.
    # Admittedly that may be what 'nonInteraction' is meant to address.  But sessionization may
    # get confused by these events if they're attributed in this way, because there's no way for
    # this event to get context that would match with what the user might be doing at the moment.
    # But the events do show up in GA being joined up with existing sessions (i.e. within a half
    # hour in the past), so they don't always break sessions.  Not sure what happens after these.
    # We can put the recipient_user_id into the properties, and then export as a custom dimension.
    with tracker.get_tracker().context(msg.app_label, tracking_context):
        segment.track(
            user_id=user.id,
            event_name='edx.bi.email.sent',
            properties=properties,
        )


def _is_delivery_enabled(site, delivery_config_var, log_prefix):
    if getattr(ScheduleConfig.current(site), delivery_config_var, False):
        return True
    else:
        LOG.info('%s: Message delivery disabled for site %s', log_prefix, site.domain)


def _annotate_for_monitoring(message_type, site, bin_num=None, target_day_str=None, day_offset=None, course_key=None):  # pylint: disable=missing-function-docstring
    # This identifies the type of message being sent, for example: schedules.recurring_nudge3.
    set_custom_attribute('message_name', f'{message_type.app_label}.{message_type.name}')
    # The domain name of the site we are sending the message for.
    set_custom_attribute('site', site.domain)
    # This is the "bin" of data being processed. We divide up the work into chunks so that we don't tie up celery
    # workers for too long. This could help us identify particular bins that are problematic.
    if bin_num:
        set_custom_attribute('bin', bin_num)
    # The date we are processing data for.
    if target_day_str:
        set_custom_attribute('target_day', target_day_str)
    # The number of days relative to the current date to process data for.
    if day_offset:
        set_custom_attribute('day_offset', day_offset)
    # If we're processing these according to a course_key rather than bin we can use this to identify problematic keys.
    if course_key:
        set_custom_attribute('course_key', course_key)
    # A unique identifier for this batch of messages being sent.
    set_custom_attribute('send_uuid', message_type.uuid)


def _annonate_send_task_for_monitoring(msg):
    # A unique identifier for this batch of messages being sent.
    set_custom_attribute('send_uuid', msg.send_uuid)
    # A unique identifier for this particular message.
    set_custom_attribute('uuid', msg.uuid)
