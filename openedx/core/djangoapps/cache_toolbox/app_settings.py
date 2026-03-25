"""
Settings for cache_toolbox.
"""


from django.conf import settings  # noqa: I001

# Default cache timeout
CACHE_TOOLBOX_DEFAULT_TIMEOUT = getattr(
    settings,
    'CACHE_TOOLBOX_DEFAULT_TIMEOUT',
    60 * 60 * 24 * 3,
)
