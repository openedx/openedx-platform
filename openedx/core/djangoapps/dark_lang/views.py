"""
Views file for the Darklang Django App
"""


from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from web_fragments.fragment import Fragment

from openedx.core.djangoapps.dark_lang import DARK_LANGUAGE_KEY
from openedx.core.djangoapps.dark_lang.models import DarkLangConfig
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from openedx.core.djangoapps.user_api.preferences.api import delete_user_preference, set_user_preference
from openedx.core.djangoapps.util.user_messages import PageLevelMessages

LANGUAGE_INPUT_FIELD = 'preview_language'


class PreviewLanguageFragmentView(EdxFragmentView):
    """
    View used when a user is attempting to change the preview language using Darklang.

    Expected Behavior:
    GET - returns a form for setting/resetting the user's dark language
    POST - updates or clears the setting to the given dark language
    """

    def render_to_fragment(self, request, course_id=None, **kwargs):  # pylint: disable=arguments-differ, unused-argument
        """
        Renders the language preview view as a fragment.
        """
        html = render_to_string('dark_lang/preview-language-fragment.html', {})
        return Fragment(html)

    def create_base_standalone_context(self, request, fragment, **kwargs):
        """
        Creates the base context for rendering a fragment as a standalone page.
        """
        return {
            'uses_bootstrap': True,
        }

    def standalone_page_title(self, request, fragment, **kwargs):
        """
        Returns the page title for the standalone update page.
        """
        return _('Preview Language Administration')

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        """
        Renders the fragment to control the preview language.
        """
        if not self._user_can_preview_languages(request.user):
            raise Http404
        return super().get(request, *args, **kwargs)

    @method_decorator(login_required)
    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        """
        Accept requests to update the preview language.
        """
        if not self._user_can_preview_languages(request.user):
            raise Http404
        action = request.POST.get('action', None)
        if action == 'set_preview_language':
            self._set_preview_language(request)
        elif action == 'reset_preview_language':
            self._clear_preview_language(request)
        return redirect(request.path)

    def _user_can_preview_languages(self, user):
        """
        Returns true if the specified user can preview languages.
        """
        if not DarkLangConfig.current().enabled:
            return False
        return user and not user.is_anonymous

    def _set_preview_language(self, request):
        """
        Sets the preview language for the current user.
        """
        preview_language = request.POST.get(LANGUAGE_INPUT_FIELD, '')
        if not preview_language.strip():
            PageLevelMessages.register_error_message(request, _('Language not provided'))
            return

        set_user_preference(request.user, DARK_LANGUAGE_KEY, preview_language)
        PageLevelMessages.register_success_message(
            request,
            _('Language set to {preview_language}').format(
                preview_language=preview_language
            )
        )

    def _clear_preview_language(self, request):
        """
        Clears the preview language for the current user.
        """
        delete_user_preference(request.user, DARK_LANGUAGE_KEY)
        PageLevelMessages.register_success_message(
            request,
            _('Language reset to the default')
        )
