"""
Many apps in the LMS maintain their own optimized data structures that they
update whenever a course publish is detected. To do this, they listen for the
SignalHandler.course_published signal. Sometimes we want to rebuild the data on
these apps regardless of an actual change in course content, either to recover
from a bug or to bootstrap a new app we're rolling out for the first time. To
date, each app has implemented its own management command for this kind of
bootstrapping work (e.g. generate_course_overviews, generate_course_blocks).

This management command will emit the SignalHandler.course_published signal for
some subset of courses and signal listeners, and then rely on existing listener
behavior to trigger the necessary data updates.
"""


import copy
import logging
import sys
import textwrap
import time

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx.core.djangoapps.content.course_overviews.models import SimulateCoursePublishConfig
from lms.djangoapps.ccx.tasks import course_published_handler as ccx_receiver_fn
from xmodule.modulestore.django import SignalHandler, modulestore  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger('simulate_publish')


class Command(BaseCommand):
    """
    Example usage:

    # Send the course_published signal to all listeners and courses with 10
    # seconds between courses. We might use a delay like this to make sure we
    # don't flood the queue and unnecessarily delay normal publishing via
    # Studio.
    $ ./manage.py lms --settings=devstack_docker simulate_publish --delay 10

    # Find all available listeners
    $ ./manage.py lms --settings=devstack_docker simulate_publish --show_receivers

    # Send the publish signal to two courses and two listeners
    $ ./manage.py lms --settings=devstack_docker simulate_publish --receivers \
    openedx.core.djangoapps.content.course_overviews.signals._listen_for_course_publish \
    openedx.core.djangoapps.bookmarks.signals.trigger_update_xblocks_cache_task \
    --courses course-v1:edX+DemoX+Demo_Course edX/MODULESTORE_100/2018

    A Dry Run will produce output that looks like:

        DRY-RUN: This command would have sent course_published to...
        1 Receivers:
            openedx.core.djangoapps.content.course_overviews.signals._listen_for_course_publish
        27 Courses:
            course-v1:DEV_153+A2E_CHINESE+JAN2018
            course-v1:edX+100+MITPhysics
            course-v1:edX+DemoX+Demo_Course
            course-v1:edX+E2E-101+course
            course-v1:edX+MEMORY+2018
            course-v1:edX+MK101+2018
            edX/MODULESTORE_100/2018_1
            edX/MODULESTORE_100/2018_2
            edX/MODULESTORE_100/2018_3
            edX/MODULESTORE_100/2018_4
            (+ 17 more)
    """
    help = (
        "Simulate course publish signals without actually modifying course "
        "content. This command is useful for triggering various async tasks "
        "that listen for course_published signals."
    )

    # Having this be a class attribute makes it easier to substitute during
    # tests, and thereby avoid global side-effects that will mysteriously fail
    # tests that need signal handling later on.
    course_published_signal = copy.copy(SignalHandler.course_published)

    def add_arguments(self, parser):
        # pylint: disable=expression-not-assigned
        parser.add_argument(
            '--show-receivers',
            dest='show_receivers',
            action='store_true',
            help=('Display the list of possible receiver functions and exit.')
        )
        parser.add_argument(
            '--dry-run',
            dest='dry_run',
            action='store_true',
            help=(
                "Just show a preview of what would happen. This may make an "
                "expensive modulestore query to find courses, but it will "
                "not emit any signals."
            )
        )
        parser.add_argument(
            '--receivers',
            dest='receivers',
            action='store',
            nargs='+',
            help=(
                'Send course_published to specific receivers. If this flag is '
                'not present, course_published will be sent to all receivers. '
                'The CCX receiver is always included unless --skip-ccx is '
                'explicitly passed (otherwise CCX courses would never get '
                'called for any signal).'
            )
        )
        parser.add_argument(
            '--courses',
            dest='courses',
            action='store',
            nargs='+',
            help=(
                'Send course_published for specific courses. If this flag is '
                'not present, course_published will be sent to all courses.'
            )
        )
        parser.add_argument(
            '--delay',
            dest='delay',
            action='store',
            type=int,
            default=0,
            help=(
                "Number of seconds to sleep between emitting course_published "
                "signals, so that we don't flood our queues."
            )
        )
        parser.add_argument(
            '--force-lms',
            dest='force_lms',
            action='store_true',
            help=(
                "This command should be run under cms (Studio), not LMS. "
                "Regular publishes happen via Studio, and this script will "
                "exit with an error if you attempt to run it in an LMS "
                "process. However, if you know what you're doing and need to "
                "override that behavior, use this flag."
            )
        )
        parser.add_argument(
            '--skip-ccx',
            dest='skip_ccx',
            action='store_true',
            help=(
                "CCX receivers are special echoing receivers that relay "
                "the course_published signal to all CCX courses derived from "
                "a modulestore-stored course. That means we almost always "
                "want to emit to them (even when using --receivers), or none "
                "of our signals will reach any CCX derived courses. However, "
                "if you know what you're doing, you can disable this behavior "
                "with this flag, so that CCX receivers are omitted."
            )
        )
        parser.add_argument(
            '--args-from-database',
            action='store_true',
            help='Use arguments from the SimulateCoursePublishConfig model instead of the command line.',
        )

    def get_args_from_database(self):
        """ Returns an options dictionary from the current SimulateCoursePublishConfig model. """

        config = SimulateCoursePublishConfig.current()
        if not config.enabled:
            raise CommandError('SimulateCourseConfigPublish is disabled, but --args-from-database was requested.')

        # We don't need fancy shell-style whitespace/quote handling - none of our arguments are complicated
        argv = config.arguments.split()

        parser = self.create_parser('manage.py', 'simulate_publish')
        return parser.parse_args(argv).__dict__   # we want a dictionary, not a non-iterable Namespace object

    def handle(self, *args, **options):

        if options['args_from_database']:
            options = self.get_args_from_database()

        if options['show_receivers']:
            return self.print_show_receivers()

        log.info(
            "simulate_publish starting, dry-run=%s, delay=%d seconds",
            options['dry_run'],
            options['delay']
        )

        if settings.SERVICE_VARIANT == 'lms':
            if options['force_lms']:
                log.info("Forcing simulate_publish to run in LMS process.")
            else:
                log.fatal(  # lint-amnesty, pylint: disable=logging-not-lazy
                    "simulate_publish should be run as a CMS (Studio) " +
                    "command, not %s (override with --force-lms).",
                    settings.SERVICE_VARIANT
                )
                sys.exit(1)

        if options['receivers']:
            self.modify_receivers(options['receivers'], options['skip_ccx'])
        elif options['skip_ccx']:
            log.info("Disconnecting CCX handler (--skip-ccx is True)")
            self.course_published_signal.disconnect(ccx_receiver_fn)

        course_keys = self.get_course_keys(options['courses'])

        if options['dry_run']:
            return self.print_dry_run(course_keys)

        # Now that our signal receivers and courses are set up properly, do the
        # actual work of emitting signals.
        for i, course_key in enumerate(course_keys, start=1):
            log.info(
                "Emitting course_published signal (%d of %d) for course %s",
                i, len(course_keys), course_key
            )
            if options['delay']:
                time.sleep(options['delay'])
            self.course_published_signal.send_robust(sender=self, course_key=course_key)

    def modify_receivers(self, receiver_names, skip_ccx):
        """
        Modify our signal to only have the user-specified receivers.

        This method modifies the process global SignalHandler.course_published
        to disconnect any receivers that were not in the `receiver_names` list.
        If any of the receiver_names is not found (i.e. is not in the list of
        receivers printed in self.print_show_receivers), it is a fatal error and
        we will exit the process.
        """
        all_receiver_names = get_receiver_names()
        unknown_receiver_names = set(receiver_names) - all_receiver_names
        if unknown_receiver_names:
            log.fatal(
                "The following receivers were specified but not recognized: %s",
                ", ".join(sorted(unknown_receiver_names))
            )
            log.fatal("Known receivers: %s", ", ".join(sorted(all_receiver_names)))
            sys.exit(1)
        log.info("%d receivers specified: %s", len(receiver_names), ", ".join(receiver_names))
        receiver_names_set = set(receiver_names)
        for receiver_fn in get_receiver_fns():
            if receiver_fn == ccx_receiver_fn and not skip_ccx:  # lint-amnesty, pylint: disable=comparison-with-callable
                continue
            fn_name = name_from_fn(receiver_fn)
            if fn_name not in receiver_names_set:
                log.info("Disconnecting %s", fn_name)
                self.course_published_signal.disconnect(receiver_fn)

    def get_course_keys(self, courses):
        """
        Return a list of CourseKeys that we will emit signals to.

        `courses` is an optional list of strings that can be parsed into
        CourseKeys. If `courses` is empty or None, we will default to returning
        all courses in the modulestore (which can be very expensive). If one of
        the strings passed in the list for `courses` does not parse correctly,
        it is a fatal error and will cause us to exit the entire process.
        """
        # Use specific courses if specified, but fall back to all courses.
        course_keys = []
        if courses:
            log.info("%d courses specified: %s", len(courses), ", ".join(courses))
            for course_id in courses:
                try:
                    course_keys.append(CourseKey.from_string(course_id))
                except InvalidKeyError:
                    log.fatal("%s is not a parseable CourseKey", course_id)
                    sys.exit(1)
        else:
            log.info("No courses specified, reading all courses from modulestore...")
            course_keys = sorted(
                (course.id for course in modulestore().get_course_summaries()),
                key=str  # Different types of CourseKeys can't be compared without this.
            )
            log.info("%d courses read from modulestore.", len(course_keys))

        return course_keys

    def print_show_receivers(self):
        """Print receivers with accompanying docstrings for context."""
        receivers = {name_from_fn(fn): fn for fn in get_receiver_fns()}
        print(len(receivers), "receivers found:")
        for receiver_name, receiver_fn in sorted(receivers.items()):
            print("  ", receiver_name)
            docstring = textwrap.dedent(receiver_fn.__doc__ or "[No docstring]").strip()
            for line in docstring.split('\n'):
                print("      ", line)

    def print_dry_run(self, course_keys):
        """Give a preview of what courses and signals we will emit to."""
        print("DRY-RUN: This command would have sent course_published to...")
        dry_run_reveiver_names = sorted(get_receiver_names())
        print(len(dry_run_reveiver_names), "Receivers:")
        for name in dry_run_reveiver_names:
            if name == name_from_fn(ccx_receiver_fn):
                print("   ", name, "(automatically added, use --skip-ccx to omit)")
            else:
                print("   ", name)
        COURSES_TO_SHOW = 10
        print(len(course_keys), "Courses:")
        for course_key in course_keys[:COURSES_TO_SHOW]:
            print("   ", course_key)
        if len(course_keys) > COURSES_TO_SHOW:
            print(f"    (+ {len(course_keys) - COURSES_TO_SHOW} more)")


def get_receiver_names():
    """Return an unordered set of receiver names (full.module.path.function)"""
    return {
        name_from_fn(fn_ref())
        for _, fn_ref, *_ in Command.course_published_signal.receivers
    }


def get_receiver_fns():
    """Return the list of active receiver functions."""
    return [
        fn_ref()  # fn_ref is a weakref to a function, fn_ref() gives us the function
        for _, fn_ref, *_ in Command.course_published_signal.receivers
    ]


def name_from_fn(fn):
    """Human readable module.function name."""
    return f"{fn.__module__}.{fn.__name__}"
