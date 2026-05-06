# pylint: disable=missing-module-docstring
import json
import logging

from django.utils.deprecation import MiddlewareMixin

from lms.djangoapps.discussion.django_comment_client.utils import JsonError
from openedx.core.djangoapps.django_comment_common.comment_client import CommentClientRequestError

log = logging.getLogger(__name__)


class AjaxExceptionMiddleware(MiddlewareMixin):
    """
    Middleware that captures CommentClientRequestErrors during ajax requests
    and tranforms them into json responses
    """
    def process_exception(self, request, exception):
        """
        Processes CommentClientRequestErrors in ajax requests. If the request is an ajax request,
        returns a http response that encodes the error as json
        """
        if isinstance(exception, CommentClientRequestError)\
                and request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                return JsonError(json.loads(str(exception)), exception.status_code)
            except ValueError:
                return JsonError(str(exception), exception.status_code)
        return None
