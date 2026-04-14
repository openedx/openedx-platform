"""
:mod:`cache_toolbox` --- Non-magical object caching tools for Django
====================================================================

Introduction
------------

``cache_toolbox`` is intended to be a lightweight series of independent tools
to leverage caching within Django projects.

The tools are deliberately `non-magical`. That is to say, instances are never
retrieved from caches behind your back and regular Django ``.filter()`` /
``.get()`` queries continue to work exactly as before.

Because of this, you can introduce ``cache_toolbox`` into your project slowly
when needed rather than "switching" to it with invasive changes.

Links
-----

View/download code
  https://github.com/playfire/django-cache-toolbox

File a bug
  https://github.com/playfire/django-cache-toolbox/issues
"""

from .model import cache_model  # noqa: F401
