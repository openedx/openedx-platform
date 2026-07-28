# pylint: disable=missing-docstring,unused-argument


import logging

from forum import api as forum_api

from .utils import CommentClientRequestError, extract, get_course_key

log = logging.getLogger(__name__)


class Model:

    accessible_fields = ['id']
    updatable_fields = ['id']
    initializable_fields = ['id']
    base_url = None
    default_retrieve_params = {}
    metric_tag_fields = []

    DEFAULT_ACTIONS_WITH_ID = ['get', 'put', 'delete']
    DEFAULT_ACTIONS_WITHOUT_ID = ['get_all', 'post']
    DEFAULT_ACTIONS = DEFAULT_ACTIONS_WITH_ID + DEFAULT_ACTIONS_WITHOUT_ID

    def __init__(self, *args, **kwargs):
        self.attributes = extract(kwargs, self.accessible_fields)
        self.retrieved = False

    def __getattr__(self, name):
        if name == 'id':
            return self.attributes.get('id', None)
        try:
            return self.attributes[name]
        except KeyError:
            if self.retrieved or self.id is None:
                raise AttributeError(f"Field {name} does not exist")  # pylint: disable=raise-missing-from  # noqa: B904
            self.retrieve()
            return self.__getattr__(name)

    def __setattr__(self, name, value):
        if name == 'attributes' or name not in self.accessible_fields + self.updatable_fields:
            super().__setattr__(name, value)
        else:
            self.attributes[name] = value

    def __getitem__(self, key):
        if key not in self.accessible_fields:
            raise KeyError(f"Field {key} does not exist")
        return self.attributes.get(key)

    def __setitem__(self, key, value):
        if key not in self.accessible_fields + self.updatable_fields:
            raise KeyError(f"Field {key} does not exist")
        self.attributes.__setitem__(key, value)

    def items(self, *args, **kwargs):
        return self.attributes.items(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.attributes.get(*args, **kwargs)

    def to_dict(self, course_key=None):
        self.retrieve(course_key=course_key)
        return self.attributes

    def retrieve(self, *args, **kwargs):
        if not self.retrieved:
            self._retrieve(*args, **kwargs)
            self.retrieved = True
        return self

    def _retrieve(self, *args, **kwargs):
        course_id = self.attributes.get("course_id") or kwargs.get("course_key")
        if not course_id:
            course_id = forum_api.get_course_id_by_comment(self.id)
        response = None
        if self.type == "comment":
            response = forum_api.get_parent_comment(comment_id=self.attributes["id"], course_id=course_id)
        if response is None:
            raise CommentClientRequestError("Forum v2 API call is missing")
        self._update_from_response(response)

    @property
    def _metric_tags(self):
        """
        Returns a list of tags to be used when recording metrics about this model.

        Each field named in ``self.metric_tag_fields`` is used as a tag value,
        under the key ``<class>.<metric_field>``. The tag model_class is used to
        record the class name of the model.
        """
        tags = [
            f'{self.__class__.__name__}.{attr}:{self[attr]}'
            for attr in self.metric_tag_fields
            if attr in self.attributes
        ]
        tags.append(f'model_class:{self.__class__.__name__}')
        return tags

    @classmethod
    def find(cls, id):  # pylint: disable=redefined-builtin
        return cls(id=id)

    def _update_from_response(self, response_data):
        for k, v in response_data.items():
            if k in self.accessible_fields:
                setattr(self, k, v)
            else:
                log.warning(
                    "Unexpected field {field_name} in model {model_name}".format(  # noqa: UP032
                        field_name=k,
                        model_name=self.__class__.__name__
                    )
                )

    def updatable_attributes(self):
        return extract(self.attributes, self.updatable_fields)

    def initializable_attributes(self):
        return extract(self.attributes, self.initializable_fields)

    @classmethod
    def before_save(cls, instance):
        pass

    @classmethod
    def after_save(cls, instance):
        pass

    def save(self, params=None):
        """
        Invokes Forum's POST/PUT service to create/update thread
        """
        self.before_save(self)
        if self.id:   # if we have id already, treat this as an update
            response = self.handle_update(params)
        else:  # otherwise, treat this as an insert
            response = self.handle_create(params)
        self.retrieved = True
        self._update_from_response(response)
        self.after_save(self)

    def delete(self, course_id=None):
        course_key = get_course_key(self.attributes.get("course_id") or course_id)
        response = None
        if self.type == "comment":
            response = forum_api.delete_comment(comment_id=self.attributes["id"], course_id=str(course_key))
        elif self.type == "thread":
            response = forum_api.delete_thread(thread_id=self.attributes["id"], course_id=str(course_key))
        if response is None:
            raise CommentClientRequestError("Forum v2 API call is missing")
        self.retrieved = True
        self._update_from_response(response)

    @classmethod
    def url_with_id(cls, params=None):
        if params is None:
            params = {}
        return cls.base_url + '/' + str(params['id'])

    @classmethod
    def url_without_id(cls, params=None):
        return cls.base_url

    @classmethod
    def url(cls, action, params=None):
        if params is None:
            params = {}
        if cls.base_url is None:
            raise CommentClientRequestError("Must provide base_url when using default url function")
        if action not in cls.DEFAULT_ACTIONS:  # pylint: disable=no-else-raise
            raise ValueError(
                f"Invalid action {action}. The supported action must be in {str(cls.DEFAULT_ACTIONS)}"
            )
        elif action in cls.DEFAULT_ACTIONS_WITH_ID:
            try:
                return cls.url_with_id(params)
            except KeyError:
                raise CommentClientRequestError(f"Cannot perform action {action} without id")  # pylint: disable=raise-missing-from  # noqa: B904
        else:   # action must be in DEFAULT_ACTIONS_WITHOUT_ID now
            return cls.url_without_id()

    def handle_update(self, params=None):
        request_params = self.updatable_attributes()
        if params:
            request_params.update(params)
        course_id = self.attributes.get("course_id") or request_params.get("course_id")
        course_key = get_course_key(course_id)
        response = None
        if self.type == "comment":
            response = self.handle_update_comment(request_params, str(course_key))
        elif self.type == "thread":
            response = self.handle_update_thread(request_params, str(course_key))
        elif self.type == "user":
            response = self.handle_update_user(request_params, str(course_key))
        if response is None:
            raise CommentClientRequestError("Forum v2 API call is missing")
        return response

    def handle_update_user(self, request_params, course_id):
        try:
            username = request_params["username"]
            external_id = str(request_params["external_id"])
        except KeyError as e:
            raise e
        response = forum_api.update_user(
            external_id,
            username=username,
            course_id=course_id,
        )
        return response

    def handle_update_comment(self, request_params, course_id):
        request_data = {
            "comment_id": self.attributes["id"],
            "body": request_params.get("body"),
            "course_id": request_params.get("course_id") or course_id,
            "user_id": request_params.get("user_id"),
            "anonymous": request_params.get("anonymous"),
            "anonymous_to_peers": request_params.get("anonymous_to_peers"),
            "endorsed": request_params.get("endorsed"),
            "closed": request_params.get("closed"),
            "editing_user_id": request_params.get("editing_user_id"),
            "edit_reason_code": request_params.get("edit_reason_code"),
            "endorsement_user_id": request_params.get("endorsement_user_id"),
        }
        request_data = {k: v for k, v in request_data.items() if v is not None}
        response = forum_api.update_comment(**request_data)
        return response

    def handle_update_thread(self, request_params, course_id):
        request_data = {
            "thread_id": self.attributes["id"],
            "title": request_params.get("title"),
            "body": request_params.get("body"),
            "course_id": request_params.get("course_id") or course_id,
            "anonymous": request_params.get("anonymous"),
            "anonymous_to_peers": request_params.get("anonymous_to_peers"),
            "closed": request_params.get("closed"),
            "commentable_id": request_params.get("commentable_id"),
            "user_id": request_params.get("user_id"),
            "editing_user_id": request_params.get("editing_user_id"),
            "pinned": request_params.get("pinned"),
            "thread_type": request_params.get("thread_type"),
            "edit_reason_code": request_params.get("edit_reason_code"),
            "close_reason_code": request_params.get("close_reason_code"),
            "closing_user_id": request_params.get("closing_user_id"),
            "endorsed": request_params.get("endorsed"),
        }
        request_data = {k: v for k, v in request_data.items() if v is not None}
        response = forum_api.update_thread(**request_data)
        return response

    def handle_create(self, params=None):
        course_id = self.attributes.get("course_id") or params.get("course_id")
        course_key = str(get_course_key(course_id))
        handlers = {
            "comment": self.handle_create_comment,
            "thread": self.handle_create_thread,
        }

        try:
            return handlers[self.type](course_key)
        except KeyError as exc:
            raise CommentClientRequestError(f"Unsupported type: {self.type}") from exc

    def handle_create_comment(self, course_id):
        request_data = self.initializable_attributes()
        course_id = course_id or str(request_data["course_id"])
        params = {
            "body": request_data["body"],
            "user_id": str(request_data["user_id"]),
            "course_id": course_id,
            "anonymous": request_data.get("anonymous", False),
            "anonymous_to_peers": request_data.get("anonymous_to_peers", False),
        }
        if 'endorsed' in request_data:
            params['endorsed'] = request_data['endorsed']
        if parent_id := self.attributes.get("parent_id"):
            params["parent_comment_id"] = parent_id
            response = forum_api.create_child_comment(**params)
        else:
            params["thread_id"] = self.attributes["thread_id"]
            response = forum_api.create_parent_comment(**params)
        return response

    def handle_create_thread(self, course_id):
        request_data = self.initializable_attributes()
        params = {
            "title": request_data["title"],
            "body": request_data["body"],
            "course_id": course_id or str(request_data["course_id"]),
            "user_id": str(request_data["user_id"]),
            "anonymous": request_data.get("anonymous", False),
            "anonymous_to_peers": request_data.get("anonymous_to_peers", False),
            "commentable_id": request_data.get("commentable_id", "course"),
            "thread_type": request_data.get("thread_type", "discussion"),
        }
        if group_id := request_data.get("group_id"):
            params["group_id"] = group_id
        if context := request_data.get("context"):
            params["context"] = context

        response = forum_api.create_thread(**params)
        return response
