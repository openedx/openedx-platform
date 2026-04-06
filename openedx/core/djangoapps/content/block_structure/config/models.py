"""
Models for configuration of Block Structures.
"""


from config_models.models import ConfigurationModel
from django.db.models import IntegerField


class BlockStructureConfiguration(ConfigurationModel):
    """
    Configuration model for Block Structures.

    .. no_pii:
    """
    DEFAULT_PRUNE_KEEP_COUNT = 5
    DEFAULT_CACHE_TIMEOUT_IN_SECONDS = 60 * 60 * 24  # 24 hours

    class Meta:
        app_label = 'block_structure'
        db_table = 'block_structure_config'

    num_versions_to_keep = IntegerField(blank=True, null=True, default=DEFAULT_PRUNE_KEEP_COUNT)
    cache_timeout_in_seconds = IntegerField(blank=True, null=True, default=DEFAULT_CACHE_TIMEOUT_IN_SECONDS)

    def __str__(self):
        return "BlockStructureConfiguration: num_versions_to_keep: {}, cache_timeout_in_seconds: {}".format(  # noqa: UP032
            self.num_versions_to_keep,
            self.cache_timeout_in_seconds,
        )
