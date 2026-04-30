# pylint: disable=missing-docstring,protected-access


import logging

from eventtracking import tracker
from forum import api as forum_api

from . import models, settings, utils

log = logging.getLogger(__name__)


class Thread(models.Model):
    # accessible_fields can be set and retrieved on the model
    accessible_fields = [
        'id', 'title', 'body', 'anonymous', 'anonymous_to_peers', 'course_id',
        'closed', 'tags', 'votes', 'commentable_id', 'username', 'user_id',
        'created_at', 'updated_at', 'comments_count', 'unread_comments_count',
        'at_position_list', 'children', 'type', 'highlighted_title',
        'highlighted_body', 'endorsed', 'read', 'group_id', 'group_name', 'pinned',
        'abuse_flaggers', 'resp_skip', 'resp_limit', 'resp_total', 'thread_type',
        'endorsed_responses', 'non_endorsed_responses', 'non_endorsed_resp_total',
        'context', 'last_activity_at', 'closed_by', 'close_reason_code', 'edit_history',
    ]

    # updateable_fields are sent in PUT requests
    updatable_fields = [
        'title', 'body', 'anonymous', 'anonymous_to_peers', 'course_id', 'read',
        'closed', 'user_id', 'commentable_id', 'group_id', 'group_name', 'pinned', 'thread_type',
        'close_reason_code', 'edit_reason_code', 'closing_user_id', 'editing_user_id',
    ]

    # initializable_fields are sent in POST requests
    initializable_fields = updatable_fields + ['thread_type', 'context']

    base_url = f"{settings.PREFIX}/threads"
    default_retrieve_params = {'recursive': False}
    type = 'thread'

    @classmethod
    def search(cls, query_params):

        # NOTE: Params 'recursive' and 'with_responses' are currently not used by
        # either the 'search' or 'get_all' actions below.  Both already use
        # with_responses=False internally in the comment service, so no additional
        # optimization is required.
        params = {
            'page': 1,
            'per_page': 20,
            'course_id': query_params['course_id'],
        }
        params.update(
            utils.strip_blank(utils.strip_none(query_params))
        )

        # Convert user_id and author_id to strings if present
        for field in ['user_id', 'author_id']:
            if value := params.get(field):
                params[field] = str(value)

        # Handle commentable_ids/commentable_id conversion
        if commentable_ids := params.get('commentable_ids'):
            params['commentable_ids'] = commentable_ids.split(',')
        elif commentable_id := params.get('commentable_id'):
            params['commentable_ids'] = [commentable_id]
            params.pop('commentable_id', None)

        params = utils.clean_forum_params(params)
        if query_params.get('text'):                    # Handle group_ids/group_id conversion
            if group_ids := params.get('group_ids'):
                params['group_ids'] = [int(group_id) for group_id in group_ids.split(',')]
            elif group_id := params.get('group_id'):
                params['group_ids'] = [int(group_id)]
                params.pop('group_id', None)
            response = forum_api.search_threads(**params)
        else:
            response = forum_api.get_user_threads(**params)

        if query_params.get('text'):
            search_query = query_params['text']
            course_id = query_params['course_id']
            group_id = query_params['group_id'] if 'group_id' in query_params else None
            requested_page = params['page']
            total_results = response.get('total_results')
            corrected_text = response.get('corrected_text')
            # Record search result metric to allow search quality analysis.
            # course_id is already included in the context for the event tracker
            tracker.emit(
                'edx.forum.searched',
                {
                    'query': search_query,
                    'search_type': 'Content',
                    'corrected_text': corrected_text,
                    'group_id': group_id,
                    'page': requested_page,
                    'total_results': total_results,
                }
            )
            log.info(
                'forum_text_search query="{search_query}" corrected_text="{corrected_text}" course_id={course_id} '  # noqa: UP032  # pylint: disable=line-too-long
                'group_id={group_id} page={requested_page} total_results={total_results}'.format(
                    search_query=search_query,
                    corrected_text=corrected_text,
                    course_id=course_id,
                    group_id=group_id,
                    requested_page=requested_page,
                    total_results=total_results
                )
            )
        return utils.CommentClientPaginatedResult(
            collection=response.get('collection', []),
            page=response.get('page', 1),
            num_pages=response.get('num_pages', 1),
            thread_count=response.get('thread_count', 0),
            corrected_text=response.get('corrected_text', None)
        )

    @classmethod
    def url_for_threads(cls, params=None):
        if params and params.get('commentable_id'):
            return "{prefix}/{commentable_id}/threads".format(
                prefix=settings.PREFIX,
                commentable_id=params['commentable_id'],
            )
        else:
            return f"{settings.PREFIX}/threads"

    @classmethod
    def url_for_search_threads(cls):
        return f"{settings.PREFIX}/search/threads"

    @classmethod
    def url(cls, action, params=None):
        if params is None:
            params = {}
        if action in ['get_all', 'post']:
            return cls.url_for_threads(params)
        elif action == 'search':
            return cls.url_for_search_threads()
        else:
            return super().url(action, params)

    # TODO: This is currently overriding Model._retrieve only to add parameters
    # for the request. Model._retrieve should be modified to handle this such
    # that subclasses don't need to override for this.
    def _retrieve(self, *args, **kwargs):
        request_params = {
            'recursive': kwargs.get('recursive'),
            'with_responses': kwargs.get('with_responses', False),
            'user_id': kwargs.get('user_id'),
            'mark_as_read': kwargs.get('mark_as_read', True),
            'resp_skip': kwargs.get('response_skip'),
            'resp_limit': kwargs.get('response_limit'),
            'reverse_order': kwargs.get('reverse_order', False),
            'merge_question_type_responses': kwargs.get('merge_question_type_responses', False)
        }
        request_params = utils.clean_forum_params(request_params)
        course_id = kwargs.get("course_id")
        if not course_id:
            course_id = forum_api.get_course_id_by_thread(self.id)
        if user_id := request_params.get('user_id'):
            request_params['user_id'] = str(user_id)
        response = forum_api.get_thread(
            thread_id=self.id,
            params=request_params,
            course_id=course_id,
        )
        self._update_from_response(response)

    def flagAbuse(self, user, voteable, course_id=None):
        if voteable.type != 'thread':
            raise utils.CommentClientRequestError("Can only flag threads")

        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        response = forum_api.update_thread_flag(
            thread_id=voteable.id,
            action="flag",
            user_id=str(user.id),
            course_id=str(course_key)
        )
        voteable._update_from_response(response)

    def unFlagAbuse(self, user, voteable, removeAll, course_id=None):
        if voteable.type != 'thread':
            raise utils.CommentClientRequestError("Can only unflag threads")

        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        response = forum_api.update_thread_flag(
            thread_id=voteable.id,
            action="unflag",
            user_id=user.id,
            update_all=bool(removeAll),
            course_id=str(course_key)
        )

        voteable._update_from_response(response)

    def pin(self, user, thread_id, course_id=None):
        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        response = forum_api.pin_thread(
            user_id=user.id,
            thread_id=thread_id,
            course_id=str(course_key)
        )
        self._update_from_response(response)

    def un_pin(self, user, thread_id, course_id=None):
        course_key = utils.get_course_key(self.attributes.get("course_id") or course_id)
        response = forum_api.unpin_thread(
            user_id=user.id,
            thread_id=thread_id,
            course_id=str(course_key)
        )
        self._update_from_response(response)



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
