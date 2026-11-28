"""
Celery needs to be loaded when the lms modules are so that task
registration and discovery can work correctly.
"""

# This will make sure the app is always imported when Django starts so
# that shared_task will use this app, and also ensures that the celery
# singleton is always configured for the LMS.
from .celery import APP as CELERY_APP  # noqa: F401
