"""
Serializer classes for containers
"""
from lxml import etree

from openedx_content import api as content_api

from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.lib.xblock_serializer.api import StaticFile, XBlockSerializer
from openedx.core.djangoapps.content_tagging.api import TagValuesByObjectIdDict, get_all_object_tags

from . import containers as container_api


class ContainerSerializer:
    """
    Serializes a container (a Section, Subsection, or Unit) to OLX.
    """
    static_files: list[StaticFile]
    tags: TagValuesByObjectIdDict

    def __init__(self, container_metadata: container_api.ContainerMetadata):
        self.container_metadata = container_metadata
        self.static_files = []
        self.tags = {}
        olx_node = self._serialize_container(container_metadata)

        self.olx_str = etree.tostring(olx_node, encoding="unicode", pretty_print=True)

    def _serialize_container(self, container_metadata: container_api.ContainerMetadata) -> etree.Element:
        """
        Serialize the given container to OLX.
        """
        # Create an XML node to hold the exported data
        container_cls = content_api.get_container_subclass(container_metadata.container_type_code)
        container_key = container_metadata.container_key

        olx = etree.Element(container_cls.olx_tag_name)

        olx.attrib["copied_from_block"] = str(container_key)
        olx.attrib["copied_from_version"] = str(container_metadata.draft_version_num)

        # Serialize the container's metadata
        olx.attrib["display_name"] = container_metadata.display_name
        container_tags, _ = get_all_object_tags(content_key=container_key)
        self.tags.update(container_tags)

        children = container_api.get_container_children(container_metadata.container_key)
        for child in children:
            if isinstance(child, container_api.ContainerMetadata):
                # If the child is a container, serialize it recursively
                child_node = self._serialize_container(child)
                olx.append(child_node)
            elif isinstance(child, container_api.LibraryXBlockMetadata):
                xblock = xblock_api.load_block(
                    child.usage_key,
                    user=None,
                )
                xblock_serializer = XBlockSerializer(
                    xblock,
                    fetch_asset_data=True,
                )
                olx.append(xblock_serializer.olx_node)
                self.static_files.extend(xblock_serializer.static_files)
                self.tags.update(xblock_serializer.tags)

        return olx
