"""
Django Views for service status app
"""


import json
import time

from celery import current_app as celery
from celery.exceptions import TimeoutError  # lint-amnesty, pylint: disable=redefined-builtin
from django.http import HttpResponse

from openedx.core.djangoapps.service_status.tasks import delayed_ping


def index(_):
    """
    An empty view
    """
    return HttpResponse()


def celery_status(_):
    """
    A view that returns Celery stats
    """
    stats = celery.control.inspect().stats() or {}
    return HttpResponse(json.dumps(stats, indent=4),  # lint-amnesty, pylint: disable=http-response-with-content-type-json, http-response-with-json-dumps
                        content_type="application/json")


def celery_ping(_):
    """
    A Simple view that checks if Celery can process a simple task
    """
    start = time.time()
    result = delayed_ping.apply_async(('ping', 0.1))
    task_id = result.id

    # Wait until we get the result
    try:
        value = result.get(timeout=4.0)
        success = True
    except TimeoutError:
        value = None
        success = False

    output = {
        'success': success,
        'task_id': task_id,
        'value': value,
        'time': time.time() - start,
    }

    return HttpResponse(json.dumps(output, indent=4),  # lint-amnesty, pylint: disable=http-response-with-content-type-json, http-response-with-json-dumps
                        content_type="application/json")
