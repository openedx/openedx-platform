"""
Very old Asset Manager API.

Has not done anything other than wrap ``contentstore().find(...)`` since 2015.

Deprecated: use ``xmodule.contentstore.django.contentstore().find()`` instead.
"""

from xmodule.contentstore.django import contentstore
from xmodule.util.deprecation import deprecated

DEPRECATION_MESSAGE = (
    "AssetManager is deprecated and will be removed. "
    "Use xmodule.contentstore.django.contentstore().find() instead."
)


@deprecated(DEPRECATION_MESSAGE)
class AssetManager:
    """
    Manager for saving/loading course assets.
    """
    @staticmethod
    @deprecated(DEPRECATION_MESSAGE)  # The class decorator only warns on instantiation, so also warn here.
    def find(asset_key, throw_on_not_found=True, as_stream=False):
        """
        Finds course asset in the deprecated contentstore.
        This method was previously searching for the course asset in the assetstore first, then in the deprecated
        contentstore. However, the asset was never found in the assetstore since an asset's metadata is
        not yet stored there.(removed calls to modulestore().find_asset_metadata(asset_key))
        The assetstore search was removed due to performance issues caused by each call unpickling the pickled and
        compressed course structure from the structure cache.
        """
        return contentstore().find(asset_key, throw_on_not_found, as_stream)
