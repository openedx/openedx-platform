# pylint: disable=missing-module-docstring

from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings

from common.djangoapps.track import tracker
from common.djangoapps.track.backends import BaseBackend

SIMPLE_SETTINGS = {
    'default': {
        'ENGINE': 'common.djangoapps.track.tests.test_tracker.DummyBackend',
        'OPTIONS': {
            'flag': True
        }
    }
}

MULTI_SETTINGS = {
    'first': {
        'ENGINE': 'common.djangoapps.track.tests.test_tracker.DummyBackend',
    },
    'second': {
        'ENGINE': 'common.djangoapps.track.tests.test_tracker.DummyBackend',
    }
}


class TestTrackerInstantiation(TestCase):
    """Test that a helper function can instantiate backends from their name."""
    def setUp(self):
        # pylint: disable=protected-access
        super().setUp()  # pylint: disable=super-with-arguments
        self.get_backend = tracker._instantiate_backend_from_name

    def test_instatiate_backend(self):
        name = 'common.djangoapps.track.tests.test_tracker.DummyBackend'
        options = {'flag': True}
        backend = self.get_backend(name, options)

        assert isinstance(backend, DummyBackend)
        assert backend.flag

    def test_instatiate_backends_with_invalid_values(self):
        def get_invalid_backend(name, parameters):
            return self.get_backend(name, parameters)

        options = {}
        name = 'common.djangoapps.track.backends.logger'
        self.assertRaises(ValueError, get_invalid_backend, name, options)  # noqa: PT027

        name = 'common.djangoapps.track.backends.logger.Foo'
        self.assertRaises(ValueError, get_invalid_backend, name, options)  # noqa: PT027

        name = 'this.package.does.not.exists'
        self.assertRaises(ValueError, get_invalid_backend, name, options)  # noqa: PT027

        name = 'unittest.TestCase'
        self.assertRaises(ValueError, get_invalid_backend, name, options)  # noqa: PT027


class TestTrackerDjangoInstantiation(TestCase):
    """Test if backends are initialized properly from Django settings."""

    @override_settings(TRACKING_BACKENDS=SIMPLE_SETTINGS.copy())
    def test_django_simple_settings(self):
        """Test configuration of a simple backend"""

        backends = self._reload_backends()

        assert len(backends) == 1

        tracker.send({})

        assert list(backends.values())[0].count == 1

    @override_settings(TRACKING_BACKENDS=MULTI_SETTINGS.copy())
    def test_django_multi_settings(self):
        """Test if multiple backends can be configured properly."""

        backends = list(self._reload_backends().values())

        assert len(backends) == 2

        event_count = 10
        for _ in range(event_count):
            tracker.send({})

        assert backends[0].count == event_count
        assert backends[1].count == event_count

    @override_settings(TRACKING_BACKENDS=MULTI_SETTINGS.copy())
    def test_django_remove_settings(self):
        """Test if a backend can be remove by setting it to None."""

        settings.TRACKING_BACKENDS.update({'second': None})

        backends = self._reload_backends()

        assert len(backends) == 1

    def _reload_backends(self):  # pylint: disable=missing-function-docstring
        # pylint: disable=protected-access

        # Reset backends
        tracker._initialize_backends_from_django_settings()

        return tracker.backends


class DummyBackend(BaseBackend):  # pylint: disable=missing-class-docstring
    def __init__(self, **options):
        super().__init__(**options)  # pylint: disable=super-with-arguments
        self.flag = options.get('flag', False)
        self.count = 0

    def send(self, event):
        self.count += 1
