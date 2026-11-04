"""
Test that testing utils do what they say.
"""

from crum import set_current_request
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http.request import HttpRequest
from django.test import SimpleTestCase, TestCase

from ..utils import get_mock_request, unresolved_references

USER_MODEL = get_user_model()


class TestGetMockRequest(TestCase):
    """
    Validate the behavior of get_mock_request
    """
    def setUp(self):  # pylint: disable=super-method-not-called
        self.addCleanup(set_current_request, None)

    def test_mock_request_is_request(self):
        request = get_mock_request(USER_MODEL())
        assert isinstance(request, HttpRequest)

    def test_user_is_attached_to_mock_request(self):
        user = USER_MODEL()
        request = get_mock_request(user)
        assert request.user is user

    def test_mock_request_without_user(self):
        request = get_mock_request()
        assert isinstance(request.user, AnonymousUser)


class TestUnresolvedReferences(SimpleTestCase):
    """
    Tests for the check that every reference in an OpenAPI document names a component it defines.
    """

    def test_a_reference_to_a_missing_component_is_reported(self):
        document = {
            "paths": {"/x/": {"get": {"responses": {"200": {"$ref": "#/components/responses/Gone"}}}}},
            "components": {"schemas": {
                "Kept": {"properties": {"a": {"allOf": [
                    {"$ref": "#/components/schemas/Present"}, {"$ref": "#/components/schemas/Missing"},
                ]}}},
                "Present": {},
            }},
        }
        assert unresolved_references(document) == ["#/components/responses/Gone", "#/components/schemas/Missing"]

    def test_a_document_whose_references_all_resolve_reports_none(self):
        document = {"components": {"schemas": {"A": {"items": {"$ref": "#/components/schemas/B"}}, "B": {}}}}
        assert not unresolved_references(document)
