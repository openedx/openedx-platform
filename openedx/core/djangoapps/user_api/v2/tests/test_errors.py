"""Tests for the error bodies this version of the User API writes."""

from django.test import SimpleTestCase
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from openedx.core.djangoapps.user_api.v2.errors import ReadableErrorDetailMixin


class _RefusingView(StandardizedErrorMixin, ReadableErrorDetailMixin, APIView):
    """Refuses every request with the refusal it is built with."""

    permission_classes = (AllowAny,)
    refusal = None

    def post(self, request):  # pylint: disable=unused-argument
        raise ValidationError(self.refusal)


def _refused(refusal):
    """Return the body a view refusing a request this way answers with."""
    view = _RefusingView.as_view(refusal=refusal)
    return view(APIRequestFactory().post("/refused/", {}, format="json")).data


class NestedRefusalTests(SimpleTestCase):
    """A refusal of one item of a list, however deep, is listed under the name of that item."""

    def test_a_property_of_one_object_in_a_list_is_named_by_position_and_property(self):
        body = _refused({"username_mappings": {1: {"c": ["Not a name."]}, 0: ["Names several."]}})
        assert body["errors"] == {
            "username_mappings[1].c": ["Not a name."],
            "username_mappings[0]": ["Names several."],
        }
        assert body["detail"] == "username_mappings[1].c: Not a name. username_mappings[0]: Names several."

    def test_a_list_of_objects_names_only_the_objects_refused(self):
        body = _refused({"items": [{}, {"name": ["This field is required."]}]})
        assert body["errors"] == {"items[1].name": ["This field is required."]}
        assert body["detail"] == "items[1].name: This field is required."

    def test_a_refusal_of_an_object_as_a_whole_keeps_the_name_of_the_object(self):
        body = _refused({"profile": {"non_field_errors": ["The two values disagree."]}, "items": {0: "Not text."}})
        assert body["errors"] == {"profile": ["The two values disagree."], "items[0]": ["Not text."]}
        assert body["detail"] == "profile: The two values disagree. items[0]: Not text."

    def test_a_refusal_naming_only_parameters_is_left_as_it_was(self):
        body = _refused({"limit": ["Not a whole number."], "non_field_errors": ["Nothing was changed."]})
        assert body["errors"] == {"limit": ["Not a whole number."], "non_field_errors": ["Nothing was changed."]}
        assert body["detail"] == "limit: Not a whole number. Nothing was changed."

    def test_a_refusal_naming_nothing_in_particular_is_left_as_it_was(self):
        body = _refused(["Nothing may be sent here."])
        assert body["errors"] == {"non_field_errors": ["Nothing may be sent here."]}
        assert body["detail"] == "Nothing may be sent here."
