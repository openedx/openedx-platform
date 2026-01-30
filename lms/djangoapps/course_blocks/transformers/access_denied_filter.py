"""
Access Denied Message Filter Transformer implementation.
"""
# TODO: Remove this file after REVE-52 lands and old-mobile-app traffic falls to < 5% of mobile traffic


from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer


class AccessDeniedMessageFilterTransformer(BlockStructureTransformer):
    """
    A transformer that removes any block from the course that has an
    authorization_denial_reason or an authorization_denial_message.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return "access_denied_message_filter"

    @classmethod
    def collect(cls, block_structure):
        """
        Collects any information that's necessary to execute this
        transformer's transform method.
        """
        block_structure.request_xblock_fields('authorization_denial_reason', 'authorization_denial_message')

    def transform(self, usage_info, block_structure):
        def _filter(block_key):
            reason = block_structure.get_xblock_field(block_key, 'authorization_denial_reason')
            message = block_structure.get_xblock_field(block_key, 'authorization_denial_message')
            return reason and message

        block_structure.remove_block_traversal(_filter)
