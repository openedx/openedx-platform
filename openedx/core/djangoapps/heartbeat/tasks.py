"""
A trivial task for health checks
"""


from celery import shared_task


@shared_task
def sample_task():
    return True
