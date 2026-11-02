""" Generate .ics files from a user schedule """

from datetime import datetime, timedelta

from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils.translation import gettext as _
from icalendar import Calendar, Event, vCalAddress, vText

from lms.djangoapps.courseware.courses import get_course_assignments
from openedx.core.djangoapps.site_configuration.helpers import get_value
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.core.djangolib.markup import HTML

from . import get_calendar_event_id


def generate_ics_for_event(uid, title, course_name, now, start, organizer_name, organizer_email, config):
    """
    Generates an ics-formatted bytestring for the given assignment information.

    To pretty-print the bytestring, do: `ics.decode('utf8').replace('\r\n', '\n')`
    """
    # icalendar library: https://icalendar.readthedocs.io/en/latest/
    # ics format spec: https://tools.ietf.org/html/rfc2445
    # ics conventions spec: https://tools.ietf.org/html/rfc5546

    organizer = vCalAddress('mailto:' + organizer_email)
    organizer.params['cn'] = vText(organizer_name)

    event = Event()
    event.add('uid', uid)
    event.add('dtstamp', now)
    event.add('organizer', organizer, encode=0)
    event.add('summary', title)
    event.add('description', HTML(_('{assignment} is due for {course}.')).format(assignment=title, course=course_name))
    event.add('dtstart', start)
    event.add('duration', timedelta(0))
    event.add('transp', 'TRANSPARENT')  # available, rather than busy
    event.add('sequence', config.ics_sequence)

    cal = Calendar()
    cal.add('prodid', '-//Open edX//calendar_sync//EN')
    cal.add('version', '2.0')
    cal.add('method', 'REQUEST')
    cal.add_component(event)

    return cal.to_ical()


def generate_ics_files_for_user_course(course, user, user_calendar_sync_config_instance):
    """
    Generates ics-formatted bytestrings of all assignments for a given course and user.

    To pretty-print each bytestring, do: `ics.decode('utf8').replace('\r\n', '\n')`

    Returns a dictionary of ics files, each one representing an assignment.
    """
    assignments = get_course_assignments(course.id, user)
    platform_name = get_value('platform_name', settings.PLATFORM_NAME)
    platform_email = get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
    now = datetime.now(ZoneInfo("UTC"))
    site_config = SiteConfiguration.get_configuration_for_org(course.org)

    ics_files = {}
    for assignment in assignments:
        ics_files[assignment.title] = generate_ics_for_event(
            now=now,
            organizer_name=platform_name,
            organizer_email=platform_email,
            start=assignment.date,
            title=assignment.title,
            course_name=course.display_name_with_default,
            uid=get_calendar_event_id(user, str(assignment.block_key), 'due', site_config.site.domain),
            config=user_calendar_sync_config_instance,
        )

    return ics_files
