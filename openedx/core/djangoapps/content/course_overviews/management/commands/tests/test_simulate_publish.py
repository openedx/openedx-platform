"""
Tests the simulate_publish management command.
"""
from django.core.management import call_command
from django.core.management.base import CommandError
from testfixtures import LogCapture

import lms.djangoapps.ccx.tasks
import openedx.core.djangoapps.content.course_overviews.signals
from openedx.core.djangoapps.content.course_overviews.management.commands.simulate_publish import Command, name_from_fn
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview, SimulateCoursePublishConfig
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import SwitchedSignal  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

LOGGER_NAME = 'simulate_publish'


class TestSimulatePublish(SharedModuleStoreTestCase):
    """Test simulate_publish, our fake course-publish signal command."""

    @classmethod
    def setUpClass(cls):
        """
        Create courses in modulestore.

        Modulestore signals are suppressed by ModuleStoreIsolationMixin, so this
        method should not trigger things like CourseOverview creation.
        """
        super().setUpClass()
        cls.command = Command()
        # course-v1:org.1+course_1+Run_1
        cls.course_key_1 = CourseFactory.create(default_store=ModuleStoreEnum.Type.split).id
        # course-v1:org.2+course_2+Run_2
        cls.course_key_2 = CourseFactory.create(default_store=ModuleStoreEnum.Type.split).id

    def setUp(self):
        """
        Most of this is isolating and re-initializing our signal handler. It
        might look like you can move this to setUpClass, but be very careful if
        doing so, to make sure side-effects don't leak out between tests.
        """
        super().setUp()

        # Instead of using the process global SignalHandler.course_published, we
        # create our own SwitchedSignal to manually send to.
        Command.course_published_signal = SwitchedSignal('test_course_publish')

        # Course Overviews Handler
        # pylint: disable=protected-access
        Command.course_published_signal.connect(
            openedx.core.djangoapps.content.course_overviews.signals._listen_for_course_publish
        )
        # CCX Handler
        Command.course_published_signal.connect(
            lms.djangoapps.ccx.tasks.course_published_handler
        )
        Command.course_published_signal.connect(self.sample_receiver_1)
        Command.course_published_signal.connect(self.sample_receiver_2)

        self.received_1 = []
        self.received_2 = []

    def tearDown(self):
        """Cleap up our signals."""
        # pylint: disable=protected-access
        Command.course_published_signal.disconnect(
            openedx.core.djangoapps.content.course_overviews.signals._listen_for_course_publish
        )
        Command.course_published_signal.disconnect(
            lms.djangoapps.ccx.tasks.course_published_handler
        )
        Command.course_published_signal.disconnect(self.sample_receiver_1)
        Command.course_published_signal.disconnect(self.sample_receiver_2)
        super().tearDown()

    def options(self, **kwargs):
        """
        Return an options dict that can be passed to self.command.handle()

        Passed in **kwargs will override existing defaults. Most defaults are
        the same as they are for running the management command manually (e.g.
        dry_run is False, show_receivers is False), except that the list of
        receivers is by default limited to the two that exist in this test
        class. We do this to keep these tests faster and more self contained.
        """
        default_receivers = [
            name_from_fn(self.sample_receiver_1),
            name_from_fn(self.sample_receiver_2),
        ]
        default_options = dict(
            show_receivers=False,
            dry_run=False,
            receivers=default_receivers,
            courses=None,
            delay=0,
            force_lms=True,
            skip_ccx=False,
            args_from_database=False
        )
        default_options.update(kwargs)
        return default_options

    def test_specific_courses(self):
        """Test sending only to specific courses."""
        self.command.handle(
            **self.options(
                courses=[str(self.course_key_1)]
            )
        )
        assert self.course_key_1 in self.received_1
        assert self.course_key_2 not in self.received_1
        assert self.received_1 == self.received_2

    def test_specific_receivers(self):
        """Test sending only to specific receivers."""
        self.command.handle(
            **self.options(
                receivers=[name_from_fn(self.sample_receiver_1)]
            )
        )
        assert self.course_key_1 in self.received_1
        assert self.course_key_2 in self.received_1
        assert not self.received_2

    def test_course_overviews(self):
        """Integration test with CourseOverviews."""
        assert CourseOverview.objects.all().count() == 0
        # pylint: disable=protected-access
        self.command.handle(
            **self.options(
                receivers=[
                    name_from_fn(openedx.core.djangoapps.content.course_overviews.signals._listen_for_course_publish)
                ]
            )
        )
        assert CourseOverview.objects.all().count() == 2
        assert not self.received_1
        assert not self.received_2

    def sample_receiver_1(self, sender, course_key, **kwargs):  # pylint: disable=unused-argument
        """Custom receiver for testing."""
        self.received_1.append(course_key)

    def sample_receiver_2(self, sender, course_key, **kwargs):  # pylint: disable=unused-argument
        """Custom receiver for testing."""
        self.received_2.append(course_key)

    def test_args_from_database(self):
        """Test management command arguments injected from config model."""
        # Nothing in the database, should default to disabled
        with self.assertRaisesRegex(CommandError, 'SimulateCourseConfigPublish is disabled.*'):
            call_command('simulate_publish', '--args-from-database')

        # Add a config
        config = SimulateCoursePublishConfig.current()
        config.arguments = '--delay 20 --dry-run --force-lms '
        config.enabled = True
        config.save()

        with LogCapture(LOGGER_NAME) as log:
            call_command('simulate_publish', '--force-lms')

            log.check_present(
                (
                    LOGGER_NAME, 'INFO',
                    "simulate_publish starting, dry-run={}, delay={} seconds".format('False', '0')
                ),
            )

        with LogCapture(LOGGER_NAME) as log:
            call_command('simulate_publish', '--args-from-database')

            log.check_present(
                (
                    LOGGER_NAME, 'INFO',
                    "simulate_publish starting, dry-run={}, delay={} seconds".format('True', '20')
                ),
            )
