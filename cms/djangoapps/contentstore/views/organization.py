"""Organizations views for use with Studio."""


from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.generic import View
from organizations.api import get_organizations

from openedx.core.djangolib.js_utils import dump_js_escaped_json


class OrganizationListView(View):
    """View rendering organization list as json.

    This view renders organization list json which is used in org
    autocomplete while creating new course.
    """

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """Returns organization list as json."""
        organizations = get_organizations()
        org_names_list = [(org["short_name"]) for org in organizations]
        return HttpResponse(dump_js_escaped_json(org_names_list), content_type='application/json; charset=utf-8')  # pylint: disable=http-response-with-content-type-json
