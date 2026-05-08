"""
Constants used by django app plugins
"""

# expose constants from edx-django-utils so plugins using these continue to work
from edx_django_utils.plugins import (  # pylint: disable=unused-import
    PluginContexts,  # noqa: F401
    PluginSettings,  # noqa: F401
    PluginSignals,  # noqa: F401
    PluginURLs,  # noqa: F401
)


class ProjectType():  # noqa: UP039
    """
    The ProjectType enum defines the possible values for the Django Projects
    that are available in the edx-platform. Plugin apps use these values to
    declare explicitly which projects they are extending.
    """

    LMS = 'lms.djangoapp'
    CMS = 'cms.djangoapp'


class SettingsType():  # noqa: UP039
    """
    The SettingsType enum defines the possible values for the settings files
    that are available for extension in the edx-platform. Plugin apps use these
    values (in addition to ProjectType) to declare explicitly which settings
    (in the specified project) they are extending.

    See https://github.com/openedx/edx-platform/master/lms/envs/docs/README.rst for
    further information on each Settings Type.
    """

    PRODUCTION = 'production'
    COMMON = 'common'
    DEVSTACK = 'devstack'
    TEST = 'test'


# Locale root for IDA plugins for LMS and CMS, relative to settings.REPO_ROOT
plugins_locale_root = 'conf/plugins-locale/plugins'
