"""
Celery needs to be loaded when the cms modules are so that task
registration and discovery can work correctly.
"""

# We monkey patch Kombu's entrypoints listing because scanning through this
# accounts for the majority of LMS/Studio startup time for tests, and we don't
# use custom Kombu serializers (which is what this is for). Still, this is
# pretty evil, and should be taken out when we update Celery to the next version
# where it looks like this method of custom serialization has been removed.
#
# FWIW, this is identical behavior to what happens in Kombu if pkg_resources
# isn't available.
import kombu.utils

kombu.utils.entrypoints = lambda namespace: iter([])

# This will make sure the app is always imported when Django starts so
# that shared_task will use this app, and also ensures that the celery
# singleton is always configured for the LMS.
from .celery import APP as CELERY_APP  # lint-amnesty, pylint: disable=wrong-import-position
