"""
Code for serializing a modulestore XBlock to OLX.
"""
from __future__ import annotations
import logging
import os

from lxml import etree
from opaque_keys.edx.locator import LibraryLocatorV2

from openedx.core.djangoapps.content_tagging.api import get_all_object_tags, TagValuesByObjectIdDict
from xmodule.xml_block import serialize_field

from .data import StaticFile
from . import utils

log = logging.getLogger(__name__)


class XBlockSerializer:
    """
    A class that can serialize an XBlock to OLX.
    """
    static_files: list[StaticFile]
    tags: TagValuesByObjectIdDict
    olx_node: etree.Element
    olx_str: str

    def __init__(self, block, write_url_name=True, fetch_asset_data=False, write_copied_from=True):
        """
        Serialize an XBlock to an OLX string + supporting files, and store the
        resulting data in this object.
        """
        self.write_url_name = write_url_name
        self.write_copied_from = write_copied_from

        self.orig_block_key = block.scope_ids.usage_id
        self.static_files = []
        self.tags = {}
        self.olx_node = self._serialize_block(block)

        self.olx_str = etree.tostring(self.olx_node, encoding="unicode", pretty_print=True)

        course_key = self.orig_block_key.course_key
        # Search the OLX for references to files stored in the course's
        # "Files & Uploads" (contentstore):
        self.olx_str = utils.rewrite_absolute_static_urls(self.olx_str, course_key)

        runtime_supports_explicit_assets = hasattr(block.runtime, 'get_block_assets')
        if runtime_supports_explicit_assets:
            # If a block supports explicitly tracked assets, things are simple.
            # openedx_content (v2 Content Libraries) currently supports this.
            self.static_files.extend(
                block.runtime.get_block_assets(block, fetch_asset_data)
            )
        else:
            # Otherwise, we have to scan the content to extract associated asset
            # by inference. This is what we have to do for Modulestore-backed
            # courses, which store files a course-global "Files and Uploads".
            for asset in utils.collect_assets_from_text(self.olx_str, course_key):
                path = asset['path']
                if path not in [sf.name for sf in self.static_files]:
                    self.static_files.append(StaticFile(name=path, url=asset['url'], data=None))

            if block.scope_ids.usage_id.block_type in ['problem', 'vertical']:
                py_lib_zip_file = utils.get_python_lib_zip_if_using(self.olx_str, course_key)
                if py_lib_zip_file:
                    self.static_files.append(py_lib_zip_file)

                js_input_files = utils.get_js_input_files_if_using(self.olx_str, course_key)
                for js_input_file in js_input_files:
                    self.static_files.append(js_input_file)

    def _serialize_block(self, block) -> etree.Element:
        """ Serialize an XBlock to OLX/XML. """
        if block.scope_ids.usage_id.block_type == 'html':
            olx = self._serialize_html_block(block)
        else:
            olx = self._serialize_normal_block(block)

        if self.write_url_name:
            # Handles a weird case where url_name is not part of olx.attrib even if it is
            # set in block. Known case is with openassessment blocks.
            if "url_name" not in olx.attrib and hasattr(block, "url_name"):
                olx.attrib['url_name'] = block.url_name
        else:
            # The url_name attribute can come either because it was already in the
            # block's field data, or because this class adds it in the calls above.
            # However it gets set though, we can remove it here:
            olx.attrib.pop("url_name", None)

        # Add copied_from_block and copied_from_version attribute the XBlock's OLX node, to help identify the source of
        # this block. This is used for tagging and linking back to the source library block, if applicable.
        if self.write_copied_from:
            olx.attrib["copied_from_block"] = str(block.usage_key)
            if isinstance(block.usage_key.context_key, LibraryLocatorV2):
                olx.attrib["copied_from_version"] = (
                    str(block.runtime.get_component_version_from_block(block).version_num)
                )

        # Store the block's tags
        block_key = block.scope_ids.usage_id
        block_id = str(block_key)
        object_tags, _ = get_all_object_tags(content_key=block_key)
        self.tags[block_id] = object_tags.get(block_id, {})

        return olx

    def _serialize_normal_block(self, block) -> etree.Element:
        """
        Serialize an XBlock to XML.

        This method is used for every block type except HTML, which uses
        serialize_html_block() instead.
        """
        # Create an XML node to hold the exported data
        olx_node = etree.Element("root")  # The node name doesn't matter: add_xml_to_node will change it
        # ^ Note: We could pass nsmap=xblock.core.XML_NAMESPACES here, but the
        # resulting XML namespace attributes don't seem that useful?
        with utils.override_export_fs(block) as filesystem:  # Needed for XBlocks that inherit XModuleDescriptor
            # Tell the block to serialize itself as XML/OLX:
            if not block.has_children:
                block.add_xml_to_node(olx_node)
            else:
                # We don't want the children serialized at this time, because
                # otherwise we can't tell which files in 'filesystem' belong to
                # this block and which belong to its children. So, temporarily
                # disable any children:
                children = block.children
                block.children = []
                block.add_xml_to_node(olx_node)
                block.children = children

            # Now the block may have exported addtional data as files in
            # 'filesystem'. If so, store them:
            for item in filesystem.walk():  # pylint: disable=not-callable
                for unit_file in item.files:
                    file_path = os.path.join(item.path, unit_file.name)
                    with filesystem.open(file_path, 'rb') as fh:
                        data = fh.read()
                    self.static_files.append(StaticFile(name=unit_file.name, data=data, url=None))

        if block.has_children:
            self._serialize_children(block, olx_node)

        if "top_level_downstream_parent_key" in block.fields \
                and block.fields["top_level_downstream_parent_key"].is_set_on(block):
            olx_node.attrib["top_level_downstream_parent_key"] = serialize_field(block.top_level_downstream_parent_key)

        return olx_node

    def _serialize_children(self, block, parent_olx_node) -> None:
        """
        Recursively serialize the children of XBlock 'block'.
        Subclasses may override this.
        """
        for child in block.get_children():
            child_node = self._serialize_block(child)
            parent_olx_node.append(child_node)

    def _serialize_html_block(self, block) -> etree.Element:
        """
        Special case handling for HTML blocks
        """
        olx_node = etree.Element("html")
        olx_node.attrib["url_name"] = block.scope_ids.usage_id.block_id
        if block.display_name:
            olx_node.attrib["display_name"] = block.display_name
        if block.fields["editor"].is_set_on(block):
            olx_node.attrib["editor"] = block.editor
        if block.use_latex_compiler:
            olx_node.attrib["use_latex_compiler"] = "true"
        for field_name in block.fields:
            if (
                field_name.startswith(("upstream", "downstream")) or field_name == "top_level_downstream_parent_key"
            ) and block.fields[field_name].is_set_on(block):
                olx_node.attrib[field_name] = serialize_field(getattr(block, field_name))

        # Escape any CDATA special chars
        escaped_block_data = block.data.replace("]]>", "]]&gt;")
        olx_node.text = etree.CDATA(escaped_block_data)
        return olx_node
