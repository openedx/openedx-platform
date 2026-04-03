"""
Views file for theming administration.
"""


from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from web_fragments.fragment import Fragment

from common.djangoapps.student.roles import GlobalStaff
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from openedx.core.djangoapps.user_api.preferences.api import (
    delete_user_preference,
    get_user_preference,
    set_user_preference,
)
from openedx.core.djangoapps.util.user_messages import PageLevelMessages

from .helpers import theme_exists
from .helpers_static import get_static_file_url
from .models import SiteTheme

PREVIEW_SITE_THEME_PREFERENCE_KEY = 'preview-site-theme'
PREVIEW_THEME_FIELD = 'preview_theme'


def user_can_preview_themes(user):
    """
    Returns true if the specified user is allowed to preview themes.
    """
    if not user or user.is_anonymous:
        return False

    # In development mode, all users can preview themes
    if settings.DEBUG:
        return True

    # Otherwise, only global staff can preview themes
    return GlobalStaff().has_user(user)


def get_user_preview_site_theme(request):
    """
    Returns the preview site for the current user, or None if not set.
    """
    user = request.user
    if not user or user.is_anonymous:
        return None
    preview_site_name = get_user_preference(user, PREVIEW_SITE_THEME_PREFERENCE_KEY)
    if not preview_site_name:
        return None
    return SiteTheme(site=request.site, theme_dir_name=preview_site_name)


def set_user_preview_site_theme(request, preview_site_theme):
    """
    Sets the current user's preferred preview site theme.

    Args:
        request: the current request
        preview_site_theme (str or SiteTheme): the preview site theme or theme name.
          None can be specified to remove the preview site theme.
    """
    if preview_site_theme:
        if isinstance(preview_site_theme, SiteTheme):
            preview_site_theme_name = preview_site_theme.theme_dir_name
        else:
            preview_site_theme_name = preview_site_theme
        if theme_exists(preview_site_theme_name):
            set_user_preference(request.user, PREVIEW_SITE_THEME_PREFERENCE_KEY, preview_site_theme_name)
            PageLevelMessages.register_success_message(
                request,
                _('Site theme changed to {site_theme}').format(site_theme=preview_site_theme_name)
            )
        else:
            PageLevelMessages.register_error_message(
                request,
                _('Theme {site_theme} does not exist').format(site_theme=preview_site_theme_name)
            )
    else:
        delete_user_preference(request.user, PREVIEW_SITE_THEME_PREFERENCE_KEY)
        PageLevelMessages.register_success_message(request, _('Site theme reverted to the default'))


class ThemingAdministrationFragmentView(EdxFragmentView):
    """
    Fragment view to allow a user to administer theming.
    """

    def render_to_fragment(self, request, course_id=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ, unused-argument
        """
        Renders the theming administration view as a fragment.
        """
        html = render_to_string('theming/theming-admin-fragment.html', {})
        return Fragment(html)

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        """
        Renders the theming admin fragment to authorized users.
        """
        if not user_can_preview_themes(request.user):
            raise Http404
        return super().get(request, *args, **kwargs)

    @method_decorator(login_required)
    def post(self, request, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Accept requests to update the theme preview.
        """
        if not user_can_preview_themes(request.user):
            raise Http404
        action = request.POST.get('action', None)
        if action == 'set_preview_theme':
            preview_theme_name = request.POST.get(PREVIEW_THEME_FIELD, '')
            set_user_preview_site_theme(request, preview_theme_name)
        elif action == 'reset_preview_theme':
            set_user_preview_site_theme(request, None)
        return redirect(request.path)

    def create_base_standalone_context(self, request, fragment, **kwargs):
        """
        Creates the context to use when rendering a standalone page.
        """
        return {
            'uses_bootstrap': True,
        }

    def standalone_page_title(self, request, fragment, **kwargs):
        """
        Returns the page title for the standalone update page.
        """
        return _('Theming Administration')


def themed_asset(request, path):
    """
    Redirect to themed asset.

    This view makes it easy to link to theme assets without knowing what is the
    currently enabled theme. For instance, applications outside of the LMS may
    want to link to the LMS logo.

    Note that the redirect is not permanent because the theme may change from
    one run to the next.
    """
    themed_url = get_static_file_url(path)
    return redirect(themed_url, permanent=False)
