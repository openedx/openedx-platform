"""
:class:`~django_require.staticstorage.OptimizedCachedRequireJsStorage`
"""

from pipeline.storage import PipelineManifestStorage
from require.storage import OptimizedFilesMixin

from openedx.core.storage import PipelineForgivingMixin


class OptimizedCachedRequireJsStorage(OptimizedFilesMixin, PipelineForgivingMixin, PipelineManifestStorage):
    """
    Custom storage backend that is used by Django-require.
    """
    pass  # pylint: disable=unnecessary-pass
