"""
Specific overrides to the base prod settings for a docker production deployment.
"""

from openedx.core.lib.logsettings import get_docker_logger_config

from .production import *  # pylint: disable=wildcard-import, unused-wildcard-import

LOGGING = get_docker_logger_config()
