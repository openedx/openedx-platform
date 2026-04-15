# pylint: disable=missing-docstring,protected-access
""" User model wrapper for comment service"""

from forum import api as forum_api
from forum.utils import ForumV2RequestError, str_to_bool

from . import models, settings, utils


class User(models.Model):

    accessible_fields = [
        'username', 'follower_ids', 'upvoted_ids', 'downvoted_ids',
        'id', 'external_id', 'subscribed_user_ids', 'children', 'course_id',
        'group_id', 'subscribed_thread_ids', 'subscribed_commentable_ids',
        'subscribed_course_ids', 'threads_count', 'comments_count',
        'default_sort_key'
    ]

    updatable_fields = ['username', 'external_id', 'default_sort_key']
    initializable_fields = updatable_fields

    metric_tag_fields = ['course_id']

    base_url = f"{settings.PREFIX}/users"
    default_retrieve_params = {'complete': True}
    type = 'user'

    @classmethod
    def from_django_user(cls, user):
        return cls(id=str(user.id),
                   external_id=str(user.id),
                   username=user.username)

    def read(self, source):
        """
        Calls forum service to mark thread as read for the user
        """
        course_id = self.attributes.get("course_id")
        course_key = utils.get_course_key(course_id)  # noqa: F841
        forum_api.mark_thread_as_read(self.id, source.id, course_id=str(course_id))

    def follow(self, source, course_id=None):
        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        forum_api.create_subscription(
            user_id=self.id,
            source_id=source.id,
            course_id=str(course_key)
        )

    def unfollow(self, source, course_id=None):
        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        forum_api.delete_subscription(
            user_id=self.id,
            source_id=source.id,
            course_id=str(course_key)
        )

    def vote(self, voteable, value, course_id=None):
        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        if voteable.type == 'thread':
            response = forum_api.update_thread_votes(
                thread_id=voteable.id,
                user_id=self.id,
                value=value,
                course_id=str(course_key)
            )
        elif voteable.type == 'comment':
            response = forum_api.update_comment_votes(
                comment_id=voteable.id,
                user_id=self.id,
                value=value,
                course_id=str(course_key)
            )
        else:
            raise utils.CommentClientRequestError("Can only vote / unvote for threads or comments")
        voteable._update_from_response(response)

    def unvote(self, voteable, course_id=None):
        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        if voteable.type == 'thread':
            response = forum_api.delete_thread_vote(
                thread_id=voteable.id,
                user_id=self.id,
                course_id=str(course_key)
            )
        elif voteable.type == 'comment':
            response = forum_api.delete_comment_vote(
                comment_id=voteable.id,
                user_id=self.id,
                course_id=str(course_key)
            )
        else:
            raise utils.CommentClientRequestError("Can only vote / unvote for threads or comments")

        voteable._update_from_response(response)

    def active_threads(self, query_params=None):
        if query_params is None:
            query_params = {}
        if not self.course_id:
            raise utils.CommentClientRequestError("Must provide course_id when retrieving active threads for the user")
        params = {'course_id': str(self.course_id)}
        params.update(query_params)
        course_key = utils.get_course_key(self.attributes.get("course_id"))
        if user_id := params.get("user_id"):
            params["user_id"] = str(user_id)
        if page := params.get("page"):
            params["page"] = int(page)
        if per_page := params.get("per_page"):
            params["per_page"] = int(per_page)
        if count_flagged := params.get("count_flagged", False):
            params["count_flagged"] = str_to_bool(count_flagged)
        if not params.get("course_id"):
            params["course_id"] = str(course_key)
        params = _clean_forum_params(params)
        response = forum_api.get_user_active_threads(**params)
        return response.get('collection', []), response.get('page', 1), response.get('num_pages', 1)

    def subscribed_threads(self, query_params=None):
        if query_params is None:
            query_params = {}
        if not self.course_id:
            raise utils.CommentClientRequestError(
                "Must provide course_id when retrieving subscribed threads for the user",
            )
        params = {'course_id': str(self.course_id)}
        params.update(query_params)
        course_key = utils.get_course_key(self.attributes.get("course_id"))
        if user_id := params.get("user_id"):
            params["user_id"] = str(user_id)
        if page := params.get("page"):
            params["page"] = int(page)
        if per_page := params.get("per_page"):
            params["per_page"] = int(per_page)
        if count_flagged := params.get("count_flagged", False):
            params["count_flagged"] = str_to_bool(count_flagged)
        if not params.get("course_id"):
            params["course_id"] = str(course_key)
        params = _clean_forum_params(params)
        response = forum_api.get_user_subscriptions(**params)
        return utils.CommentClientPaginatedResult(
            collection=response.get('collection', []),
            page=response.get('page', 1),
            num_pages=response.get('num_pages', 1),
            thread_count=response.get('thread_count', 0)
        )

    def _retrieve(self, *args, **kwargs):
        retrieve_params = self.default_retrieve_params.copy()
        retrieve_params.update(kwargs)

        if self.attributes.get('course_id'):
            retrieve_params['course_id'] = str(self.course_id)
        if self.attributes.get('group_id'):
            retrieve_params['group_id'] = self.group_id

        # course key -> id conversation
        course_id = retrieve_params.get('course_id')
        if course_id:
            course_id = str(course_id)
            retrieve_params['course_id'] = course_id

        group_ids = [retrieve_params['group_id']] if 'group_id' in retrieve_params else None
        is_complete = retrieve_params['complete']
        params = _clean_forum_params({
            "user_id": self.attributes["id"],
            "group_ids": group_ids,
            "course_id": course_id,
            "complete": is_complete
        })
        try:
            response = forum_api.get_user(**params)
        except ForumV2RequestError as e:  # noqa: F841
            self.save({"course_id": course_id})
            response = forum_api.get_user(**params)
        self._update_from_response(response)

    def retire(self, retired_username):
        course_key = utils.get_course_key(self.attributes.get("course_id"))
        forum_api.retire_user(user_id=self.id, retired_username=retired_username, course_id=str(course_key))

    def replace_username(self, new_username):
        course_key = utils.get_course_key(self.attributes.get("course_id"))
        forum_api.update_username(user_id=self.id, new_username=new_username, course_id=str(course_key))


def _clean_forum_params(params):
    """Convert string booleans to actual booleans and remove None values from forum parameters."""
    result = {}
    for k, v in params.items():
        if v is not None:
            if isinstance(v, str):
                if v.lower() == 'true':
                    result[k] = True
                elif v.lower() == 'false':
                    result[k] = False
                else:
                    result[k] = v
            else:
                result[k] = v
    return result
