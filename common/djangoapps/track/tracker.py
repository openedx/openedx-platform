"""
WARNING! track.tracker module is deprecated please use eventtracking app to track events
Module that tracks analytics events by sending them to different
configurable backends.

The backends can be configured using Django settings as the example
below::

  TRACKING_BACKENDS = {
      'tracker_name': {
          'ENGINE': 'class.name.for.backend',
          'OPTIONS': {
              'host': ... ,
              'port': ... ,
              ...
          }
      }
  }

"""


import inspect
import warnings
from importlib import import_module

import six  # pylint: disable=unused-import  # noqa: F401
from django.conf import settings

from common.djangoapps.track.backends import BaseBackend

__all__ = ['send']


backends = {}


def _initialize_backends_from_django_settings():
    """
    Initialize the event tracking backends according to the
    configuration in django settings

    """
    backends.clear()

    config = getattr(settings, 'TRACKING_BACKENDS', {})

    for name, values in config.items():
        # Ignore empty values to turn-off default tracker backends
        if values:
            engine = values['ENGINE']
            options = values.get('OPTIONS', {})
            backends[name] = _instantiate_backend_from_name(engine, options)


def _instantiate_backend_from_name(name, options):
    """
    Instantiate an event tracker backend from the full module path to
    the backend class. Useful when setting backends from configuration
    files.

    """
    # Parse backend name

    try:
        parts = name.split('.')
        module_name = '.'.join(parts[:-1])
        class_name = parts[-1]
    except IndexError:
        raise ValueError('Invalid event track backend %s' % name)  # pylint: disable=raise-missing-from  # noqa: B904, UP031

    # Get and verify the backend class

    try:
        module = import_module(module_name)
        cls = getattr(module, class_name)
        if not inspect.isclass(cls) or not issubclass(cls, BaseBackend):
            raise TypeError
    except (ValueError, AttributeError, TypeError, ImportError):
        raise ValueError('Cannot find event track backend %s' % name)  # pylint: disable=raise-missing-from  # noqa: B904, UP031

    backend = cls(**options)

    return backend


def send(event):
    """
    Send an event object to all the initialized backends.

    """
    warnings.warn(  # noqa: B028
        'track.tracker module is deprecated. Please use eventtracking to send events.', DeprecationWarning
    )
    for name, backend in backends.items():  # pylint: disable=unused-variable  # noqa: B007
        backend.send(event)


_initialize_backends_from_django_settings()
