# pylint: disable=missing-module-docstring

import decimal
import json
from functools import wraps

from django.core.serializers import serialize
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.query import QuerySet
from django.http import HttpResponse, HttpResponseBadRequest


class EDXJSONEncoder(DjangoJSONEncoder):
    """
    Encoder for Decimal object, other objects will be encoded as per DjangoJSONEncoder default implementation.

    NOTE:
        Please see https://docs.djangoproject.com/en/1.8/releases/1.5/#system-version-of-simplejson-no-longer-used
        DjangoJSONEncoder will now use the Python's json module but Python's json module don't know about how to
        encode Decimal object, so as per default implementation Decimal objects will be encoded to `str` which we don't
        want and also this is different from Django 1.4, In Django 1.4 if Decimal object has zeros after the decimal
        point then object will be serialized as `int` else `float`, so we are keeping this behavior.
    """

    def default(self, o):  # pylint: disable=method-hidden
        """
        Encode Decimal objects. If decimal object has zeros after the
        decimal point then object will be serialized as `int` else `float`
        """
        if isinstance(o, decimal.Decimal):
            if o == o.to_integral():
                return int(o)
            return float(o)
        else:
            return super().default(o)


def expect_json(view_function):
    """
    View decorator for simplifying handing of requests that expect json.  If the request's
    CONTENT_TYPE is application/json, parses the json dict from request.body, and updates
    request.POST with the contents.
    """

    @wraps(view_function)
    def parse_json_into_request(request, *args, **kwargs):
        # cdodge: fix postback errors in CMS. The POST 'content-type' header can include additional information
        # e.g. 'charset', so we can't do a direct string compare
        if "application/json" in request.META.get("CONTENT_TYPE", "") and request.body:
            try:
                request.json = json.loads(request.body.decode("utf8"))
            except ValueError:
                return JsonResponseBadRequest({"error": "Invalid JSON"})
        else:
            request.json = {}

        return view_function(request, *args, **kwargs)
    return parse_json_into_request


def expect_json_in_class_view(view):
    """
    Class-based View decorator for simplifying handing of requests that expect json.  If the request's
    CONTENT_TYPE is application/json, parses the json dict from request.body, and updates
    request.POST with the contents.
    """

    def _wrapper_view(self, request, *args, **kwargs):
        if "application/json" in request.META.get("CONTENT_TYPE", "") and request.body:
            try:
                request.json = json.loads(request.body.decode("utf8"))
            except ValueError:
                return JsonResponseBadRequest({"error": "Invalid JSON"})
        else:
            request.json = {}
        return view(self, request, *args, **kwargs)

    return _wrapper_view


class JsonResponse(HttpResponse):
    """
    Django HttpResponse subclass that has sensible defaults for outputting JSON.
    """

    def __init__(  # pylint: disable=keyword-arg-before-vararg
        self,
        resp_obj=None,
        status=None,
        encoder=EDXJSONEncoder,
        *args,
        **kwargs
    ):
        if resp_obj in (None, ""):
            content = ""
            status = status or 204
        elif isinstance(resp_obj, QuerySet):
            content = serialize("json", resp_obj)
        else:
            content = json.dumps(resp_obj, cls=encoder, indent=2, ensure_ascii=True)
        kwargs.setdefault("content_type", "application/json")
        if status:
            kwargs["status"] = status
        super().__init__(content, *args, **kwargs)


class JsonResponseBadRequest(HttpResponseBadRequest):
    """
    Subclass of HttpResponseBadRequest that defaults to outputting JSON.
    Use this to send BadRequestResponse & some Json object along with it.

    Defaults:
        dictionary: empty dictionary
        status: 400
        encoder: DjangoJSONEncoder
    """

    def __init__(
        self, obj=None, status=400, encoder=DjangoJSONEncoder, *args, **kwargs
    ):  # pylint: disable=keyword-arg-before-vararg
        if obj in (None, ""):
            content = ""
        else:
            content = json.dumps(obj, cls=encoder, indent=2, ensure_ascii=False)
        kwargs.setdefault("content_type", "application/json")
        kwargs["status"] = status
        super().__init__(content, *args, **kwargs)
