"""
Specific overrides to the base prod settings for a docker production deployment.
"""

from .production import *  # pylint: disable=wildcard-import, unused-wildcard-import  # noqa: I001

from openedx.core.lib.logsettings import get_docker_logger_config

LOGGING = get_docker_logger_config()
