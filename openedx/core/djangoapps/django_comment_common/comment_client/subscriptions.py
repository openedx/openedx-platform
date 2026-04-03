"""
Subscription model is used to get users who are subscribed to the main thread/post i.e.
"""
import logging

from forum import api as forum_api
from forum.backend import get_backend

from . import models, settings, utils

log = logging.getLogger(__name__)


class Subscription(models.Model):
    """
    Subscription model is used to get users who are subscribed to the main thread/post i.e.
    """
    # accessible_fields can be set and retrieved on the model
    accessible_fields = [
        '_id', 'subscriber_id', "source_id", "source_type"
    ]

    type = 'subscriber'
    base_url = f"{settings.PREFIX}/threads"

    @classmethod
    def fetch(cls, thread_id, course_id, query_params):
        """
        Fetches the subscriptions for a given thread_id
        """
        params = {
            'page': query_params.get('page', 1),
            'per_page': query_params.get('per_page', 20),
            'id': thread_id
        }
        params.update(
            utils.strip_blank(utils.strip_none(query_params))
        )
        course_key = utils.get_course_key(course_id)
        response = forum_api.get_thread_subscriptions(
            thread_id=thread_id,
            page=params["page"],
            per_page=params["per_page"],
            course_id=str(course_key)
        )
        return utils.SubscriptionsPaginatedResult(
            collection=response.get('collection', []),
            page=response.get('page', 1),
            num_pages=response.get('num_pages', 1),
            subscriptions_count=response.get('subscriptions_count', 0),
            corrected_text=response.get('corrected_text', None)
        )

    @staticmethod
    def is_user_subscribed_to_thread(user_id, thread_id, course_id):
        """
        Check if a user is subscribed to a thread
        """
        backend = get_backend(course_id)()
        subscription = backend.get_subscription(
            subscriber_id=user_id,
            source_id=thread_id,
            source_type="CommentThread"
        )
        return subscription is not None
