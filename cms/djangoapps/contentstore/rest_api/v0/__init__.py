"""
Views for v0 contentstore API.
"""

from cms.djangoapps.contentstore.rest_api.v0.views.assets import (  # noqa: I001
    AssetsCreateRetrieveView,
    AssetsUpdateDestroyView
)
from cms.djangoapps.contentstore.rest_api.v0.views.xblock import (
    XblockView,
    XblockCreateView
)
