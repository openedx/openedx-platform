"""
Celery tasks for Content Staging.
"""
from __future__ import annotations  # for list[int] type

import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from django.db import transaction

from .data import CLIPBOARD_PURPOSE, StagedContentStatus
from .models import StagedContent, UserClipboard

log = logging.getLogger(__name__)


@shared_task(base=LoggedTask)
def delete_expired_clipboards(staged_content_ids: list[StagedContent.ID]):
    """
    Delete retired clipboard staging entries without touching selected content.

    The task is deliberately idempotent because Celery delivery may be retried.
    """
    deleted_ids = []
    for pk in staged_content_ids:
        with transaction.atomic():
            owner_id = StagedContent.objects.filter(purpose=CLIPBOARD_PURPOSE, pk=pk).values_list(
                "user_id", flat=True
            ).first()
            if owner_id is None:
                continue
            # Match publication's PTR -> content lock order.
            UserClipboard.objects.select_for_update().filter(user_id=owner_id).first()
            # Hold the referenced row lock through the selection check and
            # deletion. A foreign-key update selecting it must wait for this
            # transaction, closing the check-then-delete race.
            content = StagedContent.objects.select_for_update().filter(
                purpose=CLIPBOARD_PURPOSE,
                pk=pk,
            ).first()
            if content is None or content.status != StagedContentStatus.EXPIRED:
                continue
            if UserClipboard.objects.filter(content_id=pk).exists():
                log.warning("Skipping StagedContent %s cleanup because it is still selected by a clipboard", pk)
                continue

            # Due to signal handlers deleting asset file objects from S3 or
            # similar, this may be slow relative to database speed.
            content.delete()
            deleted_ids.append(pk)

    log.info("Deleted expired StagedContent entries (%s)", ','.join(str(x) for x in deleted_ids))
