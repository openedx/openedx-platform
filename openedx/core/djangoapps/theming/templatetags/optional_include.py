"""
The functions in this module are based on the contents of
https://github.com/django/django/blob/1.8.5/django/template/loader_tags.py --
specifically, the do_include function. It has been modified as little as
possible, in order to match the behavior of the {% include %} template tag,
except for making it optional.
"""

# Because we want to match the original loader_tags.py file as closely as
# possible, we should disable pylint so it doesn't complain about the violations
# that are already in that file
# pylint: skip-file


from django.template import Library, TemplateDoesNotExist
from django.template.base import TemplateSyntaxError, token_kwargs
from django.template.loader_tags import IncludeNode

register = Library()


class OptionalIncludeNode(IncludeNode):
    def render(self, context):
        try:
            return super().render(context)
        except TemplateDoesNotExist:
            return ''


@register.tag('optional_include')
def do_include(parser, token):
    """
    Loads a template and renders it with the current context, if it exists.
    You can pass additional context using keyword arguments.

    Example::

        {% optional_include "foo/some_include" %}
        {% optional_include "foo/some_include" with bar="BAZZ!" baz="BING!" %}

    Use the ``only`` argument to exclude the current context when rendering
    the included template::

        {% optional_include "foo/some_include" only %}
        {% optional_include "foo/some_include" with bar="1" only %}
    """
    bits = token.split_contents()
    if len(bits) < 2:
        msg = (  # noqa: UP031
            "%r tag takes at least one argument: the name of the template "
            "to be optionally included."
        ) % bits[0]
        raise TemplateSyntaxError(msg)
    options = {}
    remaining_bits = bits[2:]
    while remaining_bits:
        option = remaining_bits.pop(0)
        if option in options:
            raise TemplateSyntaxError('The %r option was specified more '  # noqa: UP031
                                      'than once.' % option)
        if option == 'with':
            value = token_kwargs(remaining_bits, parser, support_legacy=False)
            if not value:
                raise TemplateSyntaxError('"with" in %r tag needs at least '  # noqa: UP031
                                          'one keyword argument.' % bits[0])
        elif option == 'only':
            value = True
        else:
            raise TemplateSyntaxError('Unknown argument for %r tag: %r.' %  # noqa: UP031
                                      (bits[0], option))
        options[option] = value
    isolated_context = options.get('only', False)
    namemap = options.get('with', {})
    node = OptionalIncludeNode(
        parser.compile_filter(bits[1]),
        extra_context=namemap,
        isolated_context=isolated_context,
    )
    return node
