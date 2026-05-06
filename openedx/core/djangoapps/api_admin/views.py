"""
Views for API management.
"""


import logging
from functools import cached_property
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site
from django.http.response import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import View
from django.views.generic.base import TemplateView
from django.views.generic.edit import CreateView
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import get_application_model
from oauth2_provider.views import ApplicationRegistration
from requests.exceptions import HTTPError

from common.djangoapps.edxmako.shortcuts import render_to_response
from openedx.core.djangoapps.api_admin.decorators import require_api_access
from openedx.core.djangoapps.api_admin.forms import ApiAccessRequestForm, CatalogForm
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest, Catalog
from openedx.core.djangoapps.catalog.utils import get_catalog_api_base_url
from openedx.core.djangoapps.catalog.utils import get_catalog_api_client as create_catalog_api_client

log = logging.getLogger(__name__)

Application = get_application_model()  # pylint: disable=invalid-name


class ApiRequestView(CreateView):
    """Form view for requesting API access."""
    form_class = ApiAccessRequestForm
    template_name = 'api_admin/api_access_request_form.html'
    success_url = reverse_lazy('api_admin:api-status')

    def get(self, request):  # pylint: disable=arguments-differ
        """
        If the requesting user has already requested API access, redirect
        them to the client creation page.
        """
        if ApiAccessRequest.api_access_status(request.user) is not None:
            return redirect(reverse('api_admin:api-status'))
        return super().get(request)

    def form_valid(self, form):
        form.instance.user = self.request.user
        form.instance.site = get_current_site(self.request)
        return super().form_valid(form)


class ApiRequestStatusView(ApplicationRegistration):
    """View for confirming our receipt of an API request."""

    success_url = reverse_lazy('api_admin:api-status')

    def get(self, request, form=None):  # pylint: disable=arguments-differ
        """
        If the user has not created an API request, redirect them to the
        request form. Otherwise, display the status of their API
        request. We take `form` as an optional argument so that we can
        display validation errors correctly on the page.
        """
        if form is None:
            form = self.get_form_class()()

        user = request.user
        try:
            api_request = ApiAccessRequest.objects.get(user=user)
        except ApiAccessRequest.DoesNotExist:
            return redirect(reverse('api_admin:api-request'))
        try:
            application = Application.objects.get(user=user)
        except Application.DoesNotExist:
            application = None

        # We want to fill in a few fields ourselves, so remove them
        # from the form so that the user doesn't see them.
        for field in ('client_type', 'client_secret', 'client_id', 'authorization_grant_type'):
            form.fields.pop(field)

        return render_to_response('api_admin/status.html', {
            'status': api_request.status,
            'api_support_link': settings.API_DOCUMENTATION_URL,
            'api_support_email': settings.API_ACCESS_MANAGER_EMAIL,
            'form': form,
            'application': application,
        })

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Copy the data, since it's an immutable QueryDict.
        copied_data = form.data.copy()
        # Now set the fields that were removed earlier. We give them
        # confidential client credentials, and generate their client
        # ID and secret.
        copied_data.update({
            'authorization_grant_type': Application.GRANT_CLIENT_CREDENTIALS,
            'client_type': Application.CLIENT_CONFIDENTIAL,
            'client_secret': generate_client_secret(),
            'client_id': generate_client_id(),
        })
        form.data = copied_data
        return form

    def form_valid(self, form):
        # Delete any existing applications if the user has decided to regenerate their credentials
        Application.objects.filter(user=self.request.user).delete()
        return super().form_valid(form)

    def form_invalid(self, form):
        return self.get(self.request, form)

    @require_api_access
    def post(self, request):  # pylint: disable=arguments-differ
        return super().post(request)


class ApiTosView(TemplateView):
    """View to show the API Terms of Service."""

    template_name = 'api_admin/terms_of_service.html'


class CatalogApiMixin:
    """
    Helpers for work with Catalog API.
    """
    def get_catalog_api_client(self, user):
        """
        Returns catalog API client.
        """
        return create_catalog_api_client(user)

    @cached_property
    def catalogs_api_url(self):
        """
        Returns the catalogs URL for the catalog API.
        """
        return urljoin(f"{get_catalog_api_base_url()}/", "catalogs/")

    @cached_property
    def courses_api_url(self):
        """
        Returns the courses URL for the catalog API.
        """
        return urljoin(f"{get_catalog_api_base_url()}/", "courses/")


class CatalogSearchView(View):
    """View to search for catalogs belonging to a user."""

    def get(self, request):
        """Display a form to search for catalogs belonging to a user."""
        return render_to_response('api_admin/catalogs/search.html')

    def post(self, request):
        """Redirect to the list view for the given user."""
        username = request.POST.get('username')
        # If no username is provided, bounce back to this page.
        if not username:
            return redirect(reverse('api_admin:catalog-search'))
        return redirect(reverse('api_admin:catalog-list', kwargs={'username': username}))


class CatalogListView(CatalogApiMixin, View):
    """
    View to list existing catalogs and create new ones.
    """
    template = 'api_admin/catalogs/list.html'

    def _get_catalogs(self, client, username):
        """
        Retrieve catalogs for a user. Returns the empty list if none are found.
        """
        try:
            response = client.get(self.catalogs_api_url, params={"username": username})
            response.raise_for_status()
            return [Catalog(attributes=catalog) for catalog in response.json()['results']]
        except HTTPError as err:
            if err.response.status_code == 404:
                return []
            else:
                raise

    def get_context_data(self, client, username, form):
        """
        Retrieve context data for the template.
        """
        return {
            'username': username,
            'catalogs': self._get_catalogs(client, username),
            'form': form,
            'preview_url': reverse('api_admin:catalog-preview'),
            'catalog_api_catalog_endpoint': self.catalogs_api_url,
            'catalog_api_url': self.courses_api_url,
        }

    def get(self, request, username):
        """
        Display a list of a user's catalogs.
        """
        client = self.get_catalog_api_client(request.user)
        form = CatalogForm(initial={'viewers': [username]})
        return render_to_response(self.template, self.get_context_data(client, username, form))

    def post(self, request, username):
        """
        Create a new catalog for a user.
        """
        form = CatalogForm(request.POST)
        client = self.get_catalog_api_client(request.user)
        if not form.is_valid():
            return render_to_response(self.template, self.get_context_data(client, username, form), status=400)

        attrs = form.cleaned_data
        response = client.post(self.catalogs_api_url, data=attrs)
        response.raise_for_status()
        catalog = response.json()
        return redirect(reverse('api_admin:catalog-edit', kwargs={'catalog_id': catalog['id']}))


class CatalogEditView(CatalogApiMixin, View):
    """
    View to edit an individual catalog.
    """
    template_name = 'api_admin/catalogs/edit.html'

    def get_context_data(self, catalog, form):
        """
        Retrieve context data for the template.
        """
        return {
            'catalog': catalog,
            'form': form,
            'preview_url': reverse('api_admin:catalog-preview'),
            'catalog_api_url': self.catalogs_api_url,
            'catalog_api_catalog_endpoint': self.courses_api_url,
        }

    def get(self, request, catalog_id):
        """
        Display a form to edit this catalog.
        """
        client = self.get_catalog_api_client(request.user)
        response = client.get(urljoin(f"{self.catalogs_api_url}/", f"{catalog_id}/"))
        response.raise_for_status()
        catalog = Catalog(attributes=response.json())
        form = CatalogForm(instance=catalog)
        return render_to_response(self.template_name, self.get_context_data(catalog, form))

    def post(self, request, catalog_id):
        """
        Update or delete this catalog.
        """
        client = self.get_catalog_api_client(request.user)
        if request.POST.get('delete-catalog') == 'on':
            response = client.delete(urljoin(f"{self.catalogs_api_url}/", f"{catalog_id}/"))
            response.raise_for_status()
            return redirect(reverse('api_admin:catalog-search'))
        form = CatalogForm(request.POST)
        if not form.is_valid():
            response = client.get(urljoin(f"{self.catalogs_api_url}/", f"{catalog_id}/"))
            response.raise_for_status()
            catalog = Catalog(attributes=response.json())
            return render_to_response(self.template_name, self.get_context_data(catalog, form), status=400)
        catalog_response = client.patch(urljoin(f"{self.catalogs_api_url}/", f"{catalog_id}/"), data=form.cleaned_data)
        catalog_response.raise_for_status()
        return redirect(reverse('api_admin:catalog-edit', kwargs={'catalog_id': catalog_response.json()['id']}))


class CatalogPreviewView(CatalogApiMixin, View):
    """
    Endpoint to preview courses for a query.
    """

    def get(self, request):
        """
        Return the results of a query against the course catalog API. If no
        query parameter is given, returns an empty result set.
        """
        client = self.get_catalog_api_client(request.user)
        # Just pass along the request params including limit/offset pagination
        if 'q' in request.GET:
            response = client.get(self.courses_api_url, params=request.GET)
            response.raise_for_status()
            results = response.json()
        # Ensure that we don't just return all the courses if no query is given
        else:
            results = {'count': 0, 'results': [], 'next': None, 'prev': None}
        return JsonResponse(results)
