"""
error_tracker: A hook for tracking errors in loading XBlocks.

Used for example to get a list of all non-fatal problems on course
load, and display them to the user.

Patterns for using the error handler:
    try:
        x = access_some_resource()
        check_some_format(x)
    except SomeProblem as err:
        msg = 'Grommet {0} is broken: {1}'.format(x, str(err))
        log.warning(msg)  # don't rely on tracker to log
        # NOTE: we generally don't want content errors logged as errors
        error_tracker = self.runtime.service(self, 'error_tracker')
        if error_tracker:
            error_tracker(msg)
        # work around
        return 'Oops, couldn't load grommet'

    OR, if not in an exception context:

    if not check_something(thingy):
        msg = "thingy {0} is broken".format(thingy)
        log.critical(msg)
        error_tracker = self.runtime.service(self, 'error_tracker')
        if error_tracker:
            error_tracker(msg)

    NOTE: To avoid duplication, do not call the tracker on errors
    that you're about to re-raise---let the caller track them.
"""


import logging
import sys
import traceback
from collections import namedtuple

log = logging.getLogger(__name__)

ErrorLog = namedtuple('ErrorLog', 'tracker errors')


def exc_info_to_str(exc_info):
    """Given some exception info, convert it into a string using
    the traceback.format_exception() function.
    """
    return ''.join(traceback.format_exception(*exc_info))


def in_exception_handler():
    '''Is there an active exception?'''
    return sys.exc_info() != (None, None, None)


def make_error_tracker():
    '''Return an ErrorLog (named tuple), with fields (tracker, errors), where
    the logger appends a tuple (message, exception_str) to the errors on every
    call.  exception_str is in the format returned by traceback.format_exception.

    error_list is a simple list.  If the caller modifies it, info
    will be lost.
    '''
    errors = []

    def error_tracker(msg):
        '''Log errors'''
        exc_str = ''
        if in_exception_handler():
            exc_str = exc_info_to_str(sys.exc_info())

            # don't display irrelevant gunicorn sync error
            if (('python2.7/site-packages/gunicorn/workers/sync.py' in exc_str) and
                    ('[Errno 11] Resource temporarily unavailable' in exc_str)):
                exc_str = ''

        errors.append((msg, exc_str))

    return ErrorLog(error_tracker, errors)


def null_error_tracker(msg):  # pylint: disable=unused-argument
    '''A dummy error tracker that just ignores the messages'''
    pass  # pylint: disable=unnecessary-pass
