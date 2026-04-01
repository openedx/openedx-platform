"""
Public python API for serializing XBlocks to OLX
"""
# pylint: disable=unused-import
from .block_serializer import StaticFile, XBlockSerializer


def serialize_xblock_to_olx(block):
    """
    This class will serialize an XBlock, producing:
        (1) an XML string defining the XBlock and all of its children (inline)
        (2) a list of any static files required by the XBlock and their URL

    This calls XBlockSerializer with all default options. To actually tweak the
    output, instantiate XBlockSerializer directly.
    """
    return XBlockSerializer(block)


def serialize_modulestore_block_for_openedx_content(block):
    """
    This class will serialize an XBlock, producing:
        (1) A new definition ID for use in openedx_content
        (2) an XML string defining the XBlock and referencing the IDs of its
            children using <xblock-include /> syntax (which doesn't actually
            contain the OLX of its children, just refers to them, so you have to
            separately serialize them.)
        (3) a list of any static files required by the XBlock and their URL

    TODO: We should deprecate this in favor of a new openedx_content implementation.
    We've left it as-is for now partly because there are bigger questions that
    we have around how we should rewrite this (e.g. are we going to
    remove <xblock-include>?).
    """
    return XBlockSerializer(block, write_url_name=False, write_copied_from=False)
