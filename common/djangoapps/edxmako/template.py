#   Copyright (c) 2008 Mikeal Rogers  # lint-amnesty, pylint: disable=missing-module-docstring
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.


from django.conf import settings
from django.template import Context, Origin, engines
from edx_django_utils.cache import RequestCache
from mako.template import Template as MakoTemplate

from . import LOOKUP, Engines
from .request_context import get_template_request_context
from .shortcuts import is_any_marketing_link_set, is_marketing_link_set, marketing_link

KEY_CSRF_TOKENS = ('csrf_token', 'csrf')

UNKNOWN_SOURCE = '<unknown source>'


class Template:
    """
    This bridges the gap between a Mako template and a Django template. It can
    be rendered like it is a Django template because the arguments are transformed
    in a way that MakoTemplate can understand.
    """

    def __init__(self, *args, **kwargs):
        """Overrides base __init__ to provide django variable overrides"""
        self.engine = kwargs.pop('engine', engines[Engines.MAKO])
        if kwargs.get('origin') is None:
            self.origin = Origin(UNKNOWN_SOURCE)
        if len(args) and isinstance(args[0], MakoTemplate):  # lint-amnesty, pylint: disable=len-as-condition
            self.mako_template = args[0]
        else:
            kwargs['lookup'] = LOOKUP['main']
            self.mako_template = MakoTemplate(*args, **kwargs)

    def render(self, context=None, request=None):
        """
        This takes a render call with a context (from Django) and translates
        it to a render call on the mako template.

        When rendering a large sequence of XBlocks, we may end up rendering
        hundreds of small templates. Even if context processors aren't very
        expensive individually, they will quickly add up in that situation. To
        help guard against this, we do context processing once for a given
        request and then cache it.
        """
        context_object = self._get_context_object(request)

        request_cache = RequestCache('context_processors')
        cache_response = request_cache.get_cached_response('cp_output')
        if cache_response.is_found:
            context_dictionary = dict(cache_response.value)
        else:
            context_dictionary = self._get_context_processors_output_dict(context_object)
            # The context_dictionary is later updated with template specific
            # variables. There are potentially hundreds of calls to templates
            # rendering and we don't want them to interfere with each other, so
            # we make a copy from the output of the context processors and then
            # recreate a new dict every time we pull from the cache.
            request_cache.set('cp_output', dict(context_dictionary))

        if isinstance(context, Context):
            context_dictionary.update(context.flatten())
        elif context is not None:
            context_dictionary.update(context)

        self._add_core_context(context_dictionary)
        self._evaluate_lazy_csrf_tokens(context_dictionary)

        return self.mako_template.render_unicode(**context_dictionary)

    @staticmethod
    def _get_context_object(request):
        """
        Get a Django RequestContext or Context, as appropriate for the situation.
        In some tests, there might not be a current request.
        """
        request_context = get_template_request_context(request)
        if request_context is not None:
            return request_context
        else:
            return Context({})

    def _get_context_processors_output_dict(self, context_object):
        """
        Run the context processors for the given context and get the output as a new dictionary.
        """
        with context_object.bind_template(self):
            return context_object.flatten()

    @staticmethod
    def _add_core_context(context_dictionary):
        """
        Add to the given dictionary context variables which should always be
        present, even when context processors aren't run during tests.  Using
        a context processor should almost always be preferred to adding more
        variables here.
        """
        context_dictionary['settings'] = settings
        context_dictionary['EDX_ROOT_URL'] = settings.EDX_ROOT_URL
        context_dictionary['marketing_link'] = marketing_link
        context_dictionary['is_any_marketing_link_set'] = is_any_marketing_link_set
        context_dictionary['is_marketing_link_set'] = is_marketing_link_set

    @staticmethod
    def _evaluate_lazy_csrf_tokens(context_dictionary):
        """
        Evaluate any lazily-evaluated CSRF tokens in the given context.
        """
        for key in KEY_CSRF_TOKENS:
            if key in context_dictionary:
                context_dictionary[key] = str(context_dictionary[key])
