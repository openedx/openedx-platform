"""
Celery tasks for Content Staging.
"""
from __future__ import annotations  # for list[int] type

import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from edx_django_utils.monitoring import set_code_owner_attribute

from .data import CLIPBOARD_PURPOSE
from .models import StagedContent

log = logging.getLogger(__name__)


@shared_task(base=LoggedTask)
@set_code_owner_attribute
def delete_expired_clipboards(staged_content_ids: list[int]):
    """
    A Celery task to delete StagedContent clipboard entries that are no longer
    relevant.
    """
    for pk in staged_content_ids:
        # Due to signal handlers deleting asset file objects from S3 or similar,
        # this may be "slow" relative to database speed.
        StagedContent.objects.get(purpose=CLIPBOARD_PURPOSE, pk=pk).delete()
    log.info(f"Successfully deleted StagedContent entries ({','.join(str(x) for x in staged_content_ids)})")
