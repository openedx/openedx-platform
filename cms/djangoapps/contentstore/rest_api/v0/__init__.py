"""
Views for v0 contentstore API.
"""

from cms.djangoapps.contentstore.rest_api.v0.views.assets import AssetsCreateRetrieveView, AssetsUpdateDestroyView  # noqa: F401
from cms.djangoapps.contentstore.rest_api.v0.views.xblock import XblockCreateView, XblockView  # noqa: F401
