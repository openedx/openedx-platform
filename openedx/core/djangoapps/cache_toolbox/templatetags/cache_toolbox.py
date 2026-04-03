"""
Implementation of custom django template tags for
automatically caching template fragments.
"""


from django import template
from django.core.cache import cache
from django.template import resolve_variable  # lint-amnesty, pylint: disable=no-name-in-module
from django.template import Node, TemplateSyntaxError, Variable

register = template.Library()  # pylint: disable=invalid-name


class CacheNode(Node):
    """
    Subclass of django's template Node class that
    caches the rendered value of a template fragment. This is a
    simpler implementation of django.templatetags.cache.CacheNode.
    """
    def __init__(self, nodelist, expire_time, key):
        self.nodelist = nodelist
        self.expire_time = Variable(expire_time)
        self.key = key

    def render(self, context):
        key = resolve_variable(self.key, context)
        expire_time = int(self.expire_time.resolve(context))

        value = cache.get(key)
        if value is None:
            value = self.nodelist.render(context)
            cache.set(key, value, expire_time)
        return value


@register.tag
def cachedeterministic(parser, token):
    """
    This will cache the contents of a template fragment for a given amount of
    time, just like {% cache .. %} except that the key is deterministic and not
    mangled or run through MD5.

    Usage::

        {% cachedeterministic [expire_time] [key] %}
            .. some expensive processing ..
        {% endcachedeterministic %}

    """
    nodelist = parser.parse(('endcachedeterministic',))
    parser.delete_first_token()
    tokens = token.contents.split()
    if len(tokens) != 3:
        raise TemplateSyntaxError("'%r' tag requires 2 arguments." % tokens[0])
    return CacheNode(nodelist, tokens[1], tokens[2])


class ShowIfCachedNode(Node):
    """
    Subclass of django's template Node class that
    renders the cached value for the given key, only
    if already cached.
    """
    def __init__(self, key):
        self.key = key

    def render(self, context):
        key = resolve_variable(self.key, context)
        return cache.get(key) or ''


@register.tag
def showifcached(parser, token):  # pylint: disable=unused-argument
    """
    Show content if it exists in the cache, otherwise display nothing.

    The key is entirely deterministic and not mangled or run through MD5 (cf.
    {% cache %})

    Usage::

        {% showifcached [key] %}

    """
    tokens = token.contents.split()
    if len(tokens) != 2:
        raise TemplateSyntaxError("'%r' tag requires 1 argument." % tokens[0])
    return ShowIfCachedNode(tokens[1])
