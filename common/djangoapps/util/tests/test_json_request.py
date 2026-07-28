"""
Test for JsonResponse and JsonResponseBadRequest util classes.
"""


import json
import unittest
from unittest import mock

from django.http import HttpResponse, HttpResponseBadRequest

from common.djangoapps.util.json_request import JsonResponse, JsonResponseBadRequest


class JsonResponseTestCase(unittest.TestCase):
    """
    A set of tests to make sure that JsonResponse Class works correctly.
    """
    def test_empty(self):
        resp = JsonResponse()
        assert isinstance(resp, HttpResponse)
        assert resp.content.decode('utf-8') == ''
        assert resp.status_code == 204
        assert resp['content-type'] == 'application/json'

    def test_empty_string(self):
        resp = JsonResponse("")
        assert isinstance(resp, HttpResponse)
        assert resp.content.decode('utf-8') == ''
        assert resp.status_code == 204
        assert resp['content-type'] == 'application/json'

    def test_string(self):
        resp = JsonResponse("foo")
        assert resp.content.decode('utf-8') == '"foo"'
        assert resp.status_code == 200
        assert resp['content-type'] == 'application/json'

    def test_dict(self):
        obj = {"foo": "bar"}
        resp = JsonResponse(obj)
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        assert resp.status_code == 200
        assert resp['content-type'] == 'application/json'

    def test_set_status_kwarg(self):
        obj = {"error": "resource not found"}
        resp = JsonResponse(obj, status=404)
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        assert resp.status_code == 404
        assert resp['content-type'] == 'application/json'

    def test_set_status_arg(self):
        obj = {"error": "resource not found"}
        resp = JsonResponse(obj, 404)
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        assert resp.status_code == 404
        assert resp['content-type'] == 'application/json'

    def test_encoder(self):
        obj = [1, 2, 3]
        encoder = object()
        with mock.patch.object(json, "dumps", return_value="[1,2,3]") as dumps:
            resp = JsonResponse(obj, encoder=encoder)
        assert resp.status_code == 200
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        kwargs = dumps.call_args[1]
        assert kwargs['cls'] is encoder


class JsonResponseBadRequestTestCase(unittest.TestCase):
    """
    A set of tests to make sure that the JsonResponseBadRequest wrapper class
    works as intended.
    """

    def test_empty(self):
        resp = JsonResponseBadRequest()
        assert isinstance(resp, HttpResponseBadRequest)
        assert resp.content.decode('utf-8') == ''
        assert resp.status_code == 400
        assert resp['content-type'] == 'application/json'

    def test_empty_string(self):
        resp = JsonResponseBadRequest("")
        assert isinstance(resp, HttpResponse)
        assert resp.content.decode('utf-8') == ''
        assert resp.status_code == 400
        assert resp['content-type'] == 'application/json'

    def test_dict(self):
        obj = {"foo": "bar"}
        resp = JsonResponseBadRequest(obj)
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        assert resp.status_code == 400
        assert resp['content-type'] == 'application/json'

    def test_set_status_kwarg(self):
        obj = {"error": "resource not found"}
        resp = JsonResponseBadRequest(obj, status=404)
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        assert resp.status_code == 404
        assert resp['content-type'] == 'application/json'

    def test_set_status_arg(self):
        obj = {"error": "resource not found"}
        resp = JsonResponseBadRequest(obj, 404)
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        assert resp.status_code == 404
        assert resp['content-type'] == 'application/json'

    def test_encoder(self):
        obj = [1, 2, 3]
        encoder = object()
        with mock.patch.object(json, "dumps", return_value="[1,2,3]") as dumps:
            resp = JsonResponseBadRequest(obj, encoder=encoder)
        assert resp.status_code == 400
        compare = json.loads(resp.content.decode('utf-8'))
        assert obj == compare
        kwargs = dumps.call_args[1]
        assert kwargs['cls'] is encoder
