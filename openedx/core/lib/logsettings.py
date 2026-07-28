"""Get log settings."""


import logging
import platform
import sys
import warnings
from logging.handlers import SysLogHandler

LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']


def get_logger_config(log_dir,  # pylint: disable=unused-argument
                      logging_env="no_env",
                      local_loglevel='INFO',
                      service_variant=""):
    """

    Return the appropriate logging config dictionary. You should assign the
    result of this to the LOGGING var in your settings. The reason it's done
    this way instead of registering directly is because I didn't want to worry
    about resetting the logging state if this is called multiple times when
    settings are extended.
    """

    # Revert to INFO if an invalid string is passed in
    if local_loglevel not in LOG_LEVELS:
        local_loglevel = 'INFO'

    hostname = platform.node().split(".")[0]
    syslog_format = ("[service_variant={service_variant}]"  # noqa: UP032
                     "[%(name)s][env:{logging_env}] %(levelname)s "
                     "[{hostname}  %(process)d] [user %(userid)s] [ip %(remoteip)s] [%(filename)s:%(lineno)d] "
                     "- %(message)s").format(service_variant=service_variant,
                                             logging_env=logging_env,
                                             hostname=hostname)

    logger_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s %(levelname)s %(process)d '
                          '[%(name)s] [user %(userid)s] [ip %(remoteip)s] %(filename)s:%(lineno)d - %(message)s',
            },
            'syslog_format': {'format': syslog_format},
            'raw': {'format': '%(message)s'},
        },
        'filters': {
            'require_debug_false': {
                '()': 'django.utils.log.RequireDebugFalse',
            },
            'userid_context': {
                '()': 'edx_django_utils.logging.UserIdFilter',
            },
            'remoteip_context': {
                '()': 'edx_django_utils.logging.RemoteIpFilter',
            }
        },
        'handlers': {
            'console': {
                'level': 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
                'filters': ['userid_context', 'remoteip_context'],
                'stream': sys.stderr,
            },
            'mail_admins': {
                'level': 'ERROR',
                'filters': ['require_debug_false'],
                'class': 'django.utils.log.AdminEmailHandler'
            },
            'local': {
                'level': local_loglevel,
                'class': 'logging.handlers.SysLogHandler',
                'address': '/dev/log',
                'formatter': 'syslog_format',
                'filters': ['userid_context', 'remoteip_context'],
                'facility': SysLogHandler.LOG_LOCAL0,
            },
            'tracking': {
                'level': 'DEBUG',
                'class': 'logging.handlers.SysLogHandler',
                'address': '/dev/log',
                'facility': SysLogHandler.LOG_LOCAL1,
                'formatter': 'raw',
            },
        },
        'loggers': {
            'tracking': {
                'handlers': ['tracking'],
                'level': 'DEBUG',
                'propagate': False,
            },
            '': {
                'handlers': ['console', 'local'],
                'level': 'INFO',
                'propagate': False
            },
            'django.request': {
                'handlers': ['mail_admins'],
                'level': 'ERROR',
                'propagate': True,
            },
            # requests is so loud at INFO (logs every connection) that we
            # force it to warn by default.
            'requests.packages.urllib3': {
                'level': 'WARN'
            }
        }
    }

    return logger_config


def log_python_warnings():
    """
    Stop ignoring DeprecationWarning, ImportWarning, and PendingDeprecationWarning;
    log all Python warnings to the main log file.

    Not used in test runs, so pytest can collect the warnings triggered for
    each test case.
    """
    warnings.simplefilter('default')
    warnings.filterwarnings('ignore', 'Not importing directory ')
    warnings.filterwarnings('ignore', 'Setting _field_data is deprecated')
    warnings.filterwarnings('ignore', 'Setting _field_data via the constructor is deprecated')
    warnings.filterwarnings('ignore', '.*unclosed.*', category=ResourceWarning)
    # Remove default_app_config warning after updating Django to 4.2
    warnings.filterwarnings(
        'ignore',
        '.*You can remove default_app_config.*',
        category=PendingDeprecationWarning
    )
    warnings.filterwarnings(
        'ignore',
        'Instead access HTTPResponse.headers directly.*',
        category=DeprecationWarning,
        module='elasticsearch'
    )
    warnings.filterwarnings(
        'ignore',
        'Using or importing the ABCs from \'collections\' instead of from \'collections.abc\' is deprecated.*',
        category=DeprecationWarning,
        module="sass",
    )
    warnings.filterwarnings(
        'ignore', "'etree' is deprecated. Use 'xml.etree.ElementTree' instead.",
        category=DeprecationWarning, module='wiki'
    )
    # try:
    #     # There are far too many of these deprecation warnings in startup to output for every management command;
    #     # suppress them until we've fixed at least the most common ones as reported by the test suite
    #     from django.utils.deprecation import RemovedInDjango50Warning, RemovedInDjango51Warning
    #     warnings.simplefilter('ignore', RemovedInDjango50Warning)
    #     warnings.simplefilter('ignore', RemovedInDjango51Warning)
    # except ImportError:
    #     pass
    logging.captureWarnings(True)


def get_docker_logger_config(log_dir='/var/tmp',
                             logging_env="no_env",
                             edx_filename="edx.log",
                             dev_env=False,
                             debug=False,
                             service_variant='lms'):
    """
    Return the appropriate logging config dictionary for a docker based setup.
    You should assign the result of this to the LOGGING var in your settings.
    """

    hostname = platform.node().split(".")[0]
    syslog_format = (  # noqa: UP032
        "[service_variant={service_variant}]"
        "[%(name)s][env:{logging_env}] %(levelname)s "
        "[{hostname}  %(process)d] [%(filename)s:%(lineno)d] "
        "- %(message)s"
    ).format(
        service_variant=service_variant,
        logging_env=logging_env, hostname=hostname
    )

    handlers = ['console']

    logger_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s %(levelname)s %(process)d '
                          '[%(name)s] %(filename)s:%(lineno)d - %(message)s',
            },
            'syslog_format': {'format': syslog_format},
            'raw': {'format': '%(message)s'},
        },
        'filters': {
            'require_debug_false': {
                '()': 'django.utils.log.RequireDebugFalse',
            },
            'userid_context': {
                '()': 'edx_django_utils.logging.UserIdFilter',
            },
            'remoteip_context': {
                '()': 'edx_django_utils.logging.RemoteIpFilter',
            }
        },
        'handlers': {
            'console': {
                'level': 'DEBUG' if debug else 'INFO',
                'class': 'logging.StreamHandler',
                'formatter': 'standard',
                'filters': ['userid_context', 'remoteip_context'],
                'stream': sys.stderr,
            },
            'tracking': {
                'level': 'DEBUG',
                'class': 'logging.handlers.RotatingFileHandler',
                'filename': '/var/tmp/tracking_logs.log',
                'backupCount': 5,
                'formatter': 'raw',
                'maxBytes': 10485760
            }
        },
        'loggers': {
            'django': {
                'handlers': handlers,
                'propagate': True,
                'level': 'INFO'
            },
            'tracking': {
                'handlers': ['tracking'],
                'level': 'DEBUG',
                'propagate': False,
            },
            'requests': {
                'handlers': handlers,
                'propagate': True,
                'level': 'WARNING'
            },
            'factory': {
                'handlers': handlers,
                'propagate': True,
                'level': 'WARNING'
            },
            'django.request': {
                'handlers': handlers,
                'propagate': True,
                'level': 'ERROR'
            },
            '': {
                'handlers': handlers,
                'level': 'INFO',
                'propagate': False
            },
        }
    }

    return logger_config
