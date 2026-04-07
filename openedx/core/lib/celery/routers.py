"""
Custom routers used by both lms and cms when routing tasks to worker queues.

For more, see https://celery.readthedocs.io/en/latest/userguide/routing.html#routers
"""

import logging

from django.conf import settings

log = logging.getLogger(__name__)


def route_task(name, args, kwargs, options, task=None, **kw):  # pylint: disable=unused-argument
    """
    Celery-defined method allowing for custom routing logic.

    If None is returned from this method, default routing logic is used.
    """
    if name in settings.EXPLICIT_QUEUES:
        return settings.EXPLICIT_QUEUES[name]

    alternate_env = settings.ALTERNATE_ENV_TASKS.get(name, None)
    if alternate_env:
        return ensure_queue_env(alternate_env)


def ensure_queue_env(desired_env):
    """
    Helper method to get the desired type of queue.

    If no such queue is defined, default routing logic is used.
    """
    queues = getattr(settings, 'CELERY_QUEUES', None)
    return next(
        (
            queue
            for queue in queues
            if f'.{desired_env}.' in queue
        ),
        None
    )
