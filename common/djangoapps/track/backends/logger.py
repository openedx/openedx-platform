"""Event tracker backend that saves events to a python logger."""


import json
import logging

from django.conf import settings

from common.djangoapps.track.backends import BaseBackend
from common.djangoapps.track.utils import DateTimeJSONEncoder

log = logging.getLogger('common.djangoapps.track.backends.logger')
application_log = logging.getLogger('common.djangoapps.track.backends.application_log')  # pylint: disable=invalid-name


class LoggerBackend(BaseBackend):
    """Event tracker backend that uses a python logger.

    Events are logged to the INFO level as JSON strings.

    """

    def __init__(self, name, **kwargs):
        """Event tracker backend that uses a python logger.

        :Parameters:
          - `name`: identifier of the logger, which should have
            been configured using the default python mechanisms.

        """
        super().__init__(**kwargs)  # pylint: disable=super-with-arguments

        self.event_logger = logging.getLogger(name)

    def send(self, event):
        try:
            event_str = json.dumps(event, cls=DateTimeJSONEncoder)
        except UnicodeDecodeError:
            application_log.exception(
                "UnicodeDecodeError Event_data: %r", event
            )
            raise

        # TODO: remove trucation of the serialized event, either at a
        # higher level during the emittion of the event, or by
        # providing warnings when the events exceed certain size.
        event_str = event_str[:settings.TRACK_MAX_EVENT]

        self.event_logger.info(event_str)
