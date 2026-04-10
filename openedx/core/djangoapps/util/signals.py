"""
Signal handler for exceptions.
"""
# pylint: disable=unused-argument


import logging

from celery.signals import task_postrun
from django.conf import settings
from django.core.signals import got_request_exception
from django.dispatch import receiver
from edx_django_utils.cache import RequestCache


@receiver(got_request_exception)
def record_request_exception(sender, **kwargs):
    """
    Logs the stack trace whenever an exception
    occurs in processing a request.
    """
    logging.exception("Uncaught exception from {sender}".format(  # noqa: UP032
        sender=sender
    ))


@task_postrun.connect
def _clear_request_cache(**kwargs):
    """
    Once a celery task completes, clear the request cache to
    prevent memory leaks.
    """
    if getattr(settings, 'CLEAR_REQUEST_CACHE_ON_TASK_COMPLETION', True):
        RequestCache.clear_all_namespaces()
