"""
Views for v0 contentstore API.
"""

from cms.djangoapps.contentstore.rest_api.v0.views.assets import (  # noqa: F401
    AssetsCreateRetrieveView,
    AssetsUpdateDestroyView,
)
from cms.djangoapps.contentstore.rest_api.v0.views.xblock import XblockCreateView, XblockView  # noqa: F401
