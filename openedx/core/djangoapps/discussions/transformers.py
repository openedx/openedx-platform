"""
Discussions Topic Link Transformer
"""

from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from openedx.core.djangoapps.discussions.models import DiscussionTopicLink, DiscussionsConfiguration
from openedx.core.djangoapps.discussions.url_helpers import get_discussions_mfe_topic_url


class DiscussionsTopicLinkTransformer(BlockStructureTransformer):
    """
    A transformer that adds discussion topic context to the xblock.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1
    EXTERNAL_ID = "discussions_id"
    EMBED_URL = "discussions_url"

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "discussions_link"

    def transform(self, usage_info, block_structure):
        """
        loads override data into blocks
        """
        provider_type = DiscussionsConfiguration.get(usage_info.course_key).provider_type
        topic_links = DiscussionTopicLink.objects.filter(
            context_key=usage_info.course_key,
            provider_id=provider_type,
            enabled_in_context=True,
        )
        for topic_link in topic_links:
            block_structure.override_xblock_field(
                topic_link.usage_key,
                DiscussionsTopicLinkTransformer.EXTERNAL_ID,
                topic_link.external_id,
            )
            mfe_embed_link = get_discussions_mfe_topic_url(usage_info.course_key, topic_link.external_id)
            if mfe_embed_link:
                block_structure.override_xblock_field(
                    topic_link.usage_key,
                    DiscussionsTopicLinkTransformer.EMBED_URL,
                    mfe_embed_link,
                )
