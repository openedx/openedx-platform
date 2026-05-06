""" Celery Tasks for the Instructor App. """

import logging

from celery import shared_task
from celery_utils.logged_task import LoggedTask
from django.core.exceptions import ObjectDoesNotExist
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from xblock.completable import XBlockCompletionMode

from common.djangoapps.student.models import get_user_by_username_or_email
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from lms.djangoapps.courseware.model_data import FieldDataCache
from openedx.core.lib.request_utils import get_request_or_stub
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@shared_task(base=LoggedTask, ignore_result=True)
@set_code_owner_attribute
def update_exam_completion_task(user_identifier: str, content_id: str, completion: float) -> None:
    """
    Marks all completable children of content_id as complete for the user.

    Submits all completable xblocks inside of the content_id block to the
    Completion Service to mark them as complete. One use case of this function is
    for special exams (timed/proctored) where regardless of submission status on
    individual problems, we want to mark the entire exam as complete when the exam
    is finished.

    params:
        user_identifier (str): username or email of a user
        content_id (str): the block key for a piece of content
        completion (float): the completion percentage to send to the Completion service (either 1.0 or 0.0)
    """
    err_msg_prefix = (
        'Error occurred while attempting to complete student attempt for user '
        f'{user_identifier} for content_id {content_id}. '
    )
    err_msg = None
    try:
        user = get_user_by_username_or_email(user_identifier)
        block_key = UsageKey.from_string(content_id)
        root_block = modulestore().get_item(block_key)
    except ObjectDoesNotExist:
        err_msg = err_msg_prefix + 'User does not exist!'
    except InvalidKeyError:
        err_msg = err_msg_prefix + 'Invalid content_id!'
    except ItemNotFoundError:
        err_msg = err_msg_prefix + 'Block not found in the modulestore!'
    if err_msg:
        log.error(err_msg)
        return

    # This logic has been copied over from openedx/core/djangoapps/schedules/content_highlights.py
    # in the _get_course_block function.
    # I'm not sure if this is an anti-pattern or not, so if you can avoid re-copying this, please do.
    # We are using it here because we ran into issues with the User service being undefined when we
    # encountered a split_test xblock.

    # Fake a request to fool parts of the courseware that want to inspect it.
    request = get_request_or_stub()
    request.user = user

    # Now evil modulestore magic to inflate our block with user state and
    # permissions checks.
    field_data_cache = FieldDataCache.cache_for_block_descendents(
        root_block.scope_ids.usage_id.context_key, user, root_block, read_only=True,
    )
    root_block = get_block_for_descriptor(
        user, request, root_block, field_data_cache, root_block.scope_ids.usage_id.context_key,
    )
    if not root_block:
        err_msg = err_msg_prefix + 'Block unable to be created from descriptor!'
        log.error(err_msg)
        return

    def _submit_completions(block, user, completion):
        """
        Recursively submits the children for completion to the Completion Service
        """
        mode = XBlockCompletionMode.get_mode(block)
        if mode == XBlockCompletionMode.COMPLETABLE:
            block.runtime.publish(block, 'completion', {'completion': completion, 'user_id': user.id})
        elif mode == XBlockCompletionMode.AGGREGATOR:
            # I know this looks weird, but at the time of writing at least, there isn't a good
            # single way to get the children assigned for a partcular user. Some blocks define the
            # child blocks method, but others don't and with blocks like Randomized Content
            # (Library Content), the get_children method returns all children and not just assigned
            # children. So this is our way around situations like that. See also Split Test Block
            # for another use case where user state has to be taken into account via get_child_blocks
            block_children = ((hasattr(block, 'get_child_blocks') and block.get_child_blocks())
                              or (hasattr(block, 'get_children') and block.get_children())
                              or [])
            for child in block_children:
                _submit_completions(child, user, completion)

    _submit_completions(root_block, user, completion)
