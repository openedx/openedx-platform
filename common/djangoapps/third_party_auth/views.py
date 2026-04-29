"""
Extra views required for SSO
"""

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.generic.base import View
from edx_django_utils.monitoring import record_exception
from social_core.utils import setting_name
from social_django.models import UserSocialAuth
from social_django.utils import load_backend, load_strategy, psa
from social_django.views import complete

from common.djangoapps import third_party_auth
from common.djangoapps.student.helpers import get_next_url_for_login_page, is_safe_login_or_logout_redirect
from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.views import compose_and_send_activation_email
from common.djangoapps.third_party_auth import pipeline, provider

from .models import SAMLConfiguration, SAMLProviderConfig

URL_NAMESPACE = getattr(settings, setting_name('URL_NAMESPACE'), None) or 'social'

log = logging.getLogger(__name__)


def inactive_user_view(request):
    """
    A newly or recently registered user has completed the social auth pipeline.
    Their account is not yet activated, but we let them login since the third party auth
    provider is trusted to vouch for them. See details in pipeline.py.

    The reason this view exists is that if we don't define this as the
    SOCIAL_AUTH_INACTIVE_USER_URL, inactive users will get sent to LOGIN_ERROR_URL, which we
    don't want.

    If the third_party_provider.skip_email_verification is set then the user is activated
    and verification email is not sent
    """
    # 'next' may be set to '/account/finish_auth/.../' if this user needs to be auto-enrolled
    # in a course. Otherwise, just redirect them to the dashboard, which displays a message
    # about activating their account.
    user = request.user
    profile = UserProfile.objects.get(user=user)
    activated = user.is_active
    # If the user is registering via 3rd party auth, track which provider they use
    if third_party_auth.is_enabled() and pipeline.running(request):
        running_pipeline = pipeline.get(request)
        third_party_provider = provider.Registry.get_from_pipeline(running_pipeline)
        if third_party_provider and third_party_provider.skip_email_verification and not activated:
            user.is_active = True
            user.save()
            activated = True
    if not activated:
        compose_and_send_activation_email(user, profile)

    request_params = request.GET
    redirect_to = request_params.get('next')

    if redirect_to and is_safe_login_or_logout_redirect(redirect_to=redirect_to, request_host=request.get_host(),
                                                        dot_client_id=request_params.get('client_id'),
                                                        require_https=request.is_secure()):
        return redirect(redirect_to)

    return redirect('dashboard')


def saml_metadata_view(request):
    """
    Get the Service Provider metadata for this edx-platform instance.
    You must send this XML to any Shibboleth Identity Provider that you wish to use.
    """
    idp_slug = request.GET.get('tpa_hint', None)
    saml_config = 'default'
    if idp_slug:
        idp = SAMLProviderConfig.current(idp_slug)
        if idp.saml_configuration:
            saml_config = idp.saml_configuration.slug
    if not SAMLConfiguration.is_enabled(request.site, saml_config):
        raise Http404
    complete_url = reverse('social:complete', args=("tpa-saml", ))
    if settings.APPEND_SLASH and not complete_url.endswith('/'):
        complete_url = complete_url + '/'  # Required for consistency
    saml_backend = load_backend(load_strategy(request), "tpa-saml", redirect_uri=complete_url)
    metadata, errors = saml_backend.generate_metadata_xml(idp_slug)

    if not errors:
        return HttpResponse(content=metadata, content_type='text/xml')
    return HttpResponseServerError(content=', '.join(errors))


@csrf_exempt
@psa(f'{URL_NAMESPACE}:complete')
def lti_login_and_complete_view(request, backend, *args, **kwargs):
    """This is a combination login/complete due to LTI being a one step login"""

    if request.method != 'POST':
        return HttpResponseNotAllowed('POST')

    request.backend.start()
    return complete(request, backend, *args, **kwargs)


def post_to_custom_auth_form(request):
    """
    Redirect to a custom login/register page.

    Since we can't do a redirect-to-POST, this view is used to pass SSO data from
    the third_party_auth pipeline to a custom login/register form (possibly on another server).
    """
    pipeline_data = request.session.pop('tpa_custom_auth_entry_data', None)
    if not pipeline_data:
        raise Http404
    # Verify the format of pipeline_data:
    data = {
        'post_url': pipeline_data['post_url'],
        # data: The provider info and user's name, email, etc. as base64 encoded JSON
        # It's base64 encoded because it's signed cryptographically and we don't want whitespace
        # or ordering issues affecting the hash/signature.
        'data': pipeline_data['data'],
        # The cryptographic hash of user_data:
        'hmac': pipeline_data['hmac'],
    }
    return render(request, 'third_party_auth/post_custom_auth_entry.html', data)


class IdPRedirectView(View):
    """
    Redirect to an IdP's login page if the IdP exists; otherwise, return a 404.

    Example usage:

        GET auth/idp_redirect/saml-default

    """
    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Return either a redirect to the login page of an identity provider that
        corresponds to the provider_slug keyword argument or a 404 if the
        provider_slug does not correspond to an identity provider.

        Args:
            request (HttpRequest)

        Keyword Args:
            provider_slug (str): a slug corresponding to a configured identity provider

        Returns:
            HttpResponse: 302 to a provider's login url if the provider_slug kwarg matches an identity provider
            HttpResponse: 404 if the provider_slug kwarg does not match an identity provider
        """
        # this gets the url to redirect to after login/registration/third_party_auth
        # it also handles checking the safety of the redirect url (next query parameter)
        # it checks against settings.LOGIN_REDIRECT_WHITELIST, so be sure to add the url
        # to this setting
        next_destination_url = get_next_url_for_login_page(request)

        try:
            url = pipeline.get_login_url(kwargs['provider_slug'], pipeline.AUTH_ENTRY_LOGIN, next_destination_url)
            return redirect(url)
        except ValueError:
            return HttpResponseNotFound()


@login_required
@require_http_methods(["POST"])
def disconnect_json_view(request, backend, association_id=None):
    """
    Custom disconnect view that returns JSON response instead of redirecting.
    See https://github.com/python-social-auth/social-app-django/issues/774 for why this is needed.
    """
    user = request.user
    # Check URL parameter first, then POST parameter
    if not association_id:
        association_id = request.POST.get('association_id')
    try:
        # Load the backend strategy and backend instance
        strategy = load_strategy(request)
        backend_instance = load_backend(strategy, backend, redirect_uri=request.build_absolute_uri())
        # Use backend.disconnect method - simplified approach without partial pipeline
        response = backend_instance.disconnect(user=user, association_id=association_id)  # noqa: F841
        # Always return JSON response regardless of what backend.disconnect returns
        return JsonResponse({
            'success': True,
            'message': 'Account successfully disconnected',
            'backend': backend,
            'association_id': association_id
        })
    except UserSocialAuth.DoesNotExist:
        log.warning(
            'Social auth association not found during disconnect: backend=%s, association_id=%s, user_id=%s',
            backend, association_id, user.id
        )
        return JsonResponse({
            'success': False,
            'error': 'Account not found or already disconnected',
            'backend': backend,
            'association_id': association_id
        }, status=404)
    except (ValueError, TypeError) as e:
        log.error(
            'Invalid parameter during social auth disconnect: backend=%s, association_id=%s, user_id=%s, error=%s',
            backend, association_id, user.id, str(e)
        )
        record_exception()
        return JsonResponse({
            'success': False,
            'error': 'Invalid request parameters',
            'backend': backend,
            'association_id': association_id
        }, status=400)
    except DatabaseError as e:
        log.error(
            'Database error during social auth disconnect: backend=%s, association_id=%s, user_id=%s, error=%s',
            backend, association_id, user.id, str(e)
        )
        record_exception()
        return JsonResponse({
            'success': False,
            'error': 'Service temporarily unavailable',
            'backend': backend,
            'association_id': association_id
        }, status=500)
    except ValidationError as e:
        log.error(
            'Validation error during social auth disconnect: backend=%s, association_id=%s, user_id=%s, error=%s',
            backend, association_id, user.id, str(e)
        )
        record_exception()
        return JsonResponse({
            'success': False,
            'error': 'Invalid request data',
            'backend': backend,
            'association_id': association_id
        }, status=400)
    except PermissionDenied as e:
        log.warning(
            'Permission denied during social auth disconnect: backend=%s, association_id=%s, user_id=%s, error=%s',
            backend, association_id, user.id, str(e)
        )
        record_exception()
        return JsonResponse({
            'success': False,
            'error': 'You do not have permission to perform this action',
            'backend': backend,
            'association_id': association_id
        }, status=403)
    except (ImportError, AttributeError, RuntimeError) as e:
        log.error(
            'System error during social auth disconnect: backend=%s, association_id=%s, user_id=%s, error=%s',
            backend, association_id, user.id, str(e)
        )
        record_exception()
        return JsonResponse({
            'success': False,
            'error': 'Service temporarily unavailable',
            'backend': backend,
            'association_id': association_id
        }, status=500)
