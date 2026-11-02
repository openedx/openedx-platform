"""
Block that get shown to the users when an error has occurred while
loading or rendering other blocks
"""


import hashlib
import json
import logging
import sys

from lxml import etree
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.field_data import DictFieldData
from xblock.fields import Scope, ScopeIds, String

from xmodule.errortracker import exc_info_to_str
from xmodule.modulestore import EdxJSONEncoder
from xmodule.x_module import (
    ResourceTemplates,
    XModuleMixin,
    XModuleToXBlockMixin,
)

log = logging.getLogger(__name__)

# NOTE: This is not the most beautiful design in the world, but there's no good
# way to tell if the block is being used in a staff context or not.  Errors that get discovered
# at course load time are turned into ErrorBlock objects, and automatically hidden from students.
# Unfortunately, we can also have errors when loading blocks mid-request, and then we need to decide
# what to show, and the logic for that belongs in the LMS (e.g. in get_block), so the error handler
# decides whether to create a staff or not-staff block.


class ErrorFields:
    """
    XBlock fields used by the ErrorBlocks
    """
    contents = String(scope=Scope.content)
    error_msg = String(scope=Scope.content)
    display_name = String(scope=Scope.settings)


@XBlock.needs('mako')
class ErrorBlock(
    ErrorFields,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
):  # pylint: disable=abstract-method
    """
    Block that gets shown to staff when there has been an error while
    loading or rendering other blocks
    """

    resources_dir = None

    def student_view(self, _context):
        """
        Return a fragment that contains the html for the student view.
        """
        fragment = Fragment(self.runtime.service(self, 'mako').render_lms_template('module-error.html', {
            'staff_access': True,
            'data': self.contents,
            'error': self.error_msg,
        }))
        return fragment

    def studio_view(self, _context):
        """
        Show empty edit view since this is not editable.
        """
        return Fragment('')

    @classmethod
    def _construct(cls, system, contents, error_msg, location, for_parent=None):
        """
        Build a new ErrorBlock using ``system``.

        Arguments:
            system (:class:`ModuleStoreRuntime`): The :class:`ModuleStoreRuntime` used
                to construct the XBlock that had an error.
            contents (unicode): An encoding of the content of the xblock that had an error.
            error_msg (unicode): A message describing the error.
            location (:class:`UsageKey`): The usage key of the XBlock that had an error.
            for_parent (:class:`XBlock`): Optional. The parent of this error block.
        """

        if error_msg is None:
            # this string is not marked for translation because we don't have
            # access to the user context, and this will only be seen by staff
            error_msg = 'Error not available'

        if location.block_type == 'error':
            location = location.replace(
                # Pick a unique url_name -- the sha1 hash of the contents.
                # NOTE: We could try to pull out the url_name of the errored block,
                # but url_names aren't guaranteed to be unique between block types,
                # and ErrorBlock can wrap any type.  When the wrapped block is fixed,
                # it will be written out with the original url_name.
                name=hashlib.sha1(contents.encode('utf8')).hexdigest()
            )

        # real metadata stays in the content, but add a display name
        field_data = DictFieldData({
            'error_msg': str(error_msg),
            'contents': contents,
            'location': location,
            'category': 'error'
        })
        return system.construct_xblock_from_class(
            cls,
            # The error block doesn't use scoped data, and thus doesn't need
            # real scope keys
            ScopeIds(None, 'error', location, location),
            field_data,
            for_parent=for_parent,
        )

    def get_context(self):
        return {
            'module': self,
            'data': self.contents,
        }

    @classmethod
    def from_json(cls, json_data, system, location, error_msg='Error not available'):  # lint-amnesty, pylint: disable=missing-function-docstring
        try:
            json_string = json.dumps(json_data, skipkeys=False, indent=4, cls=EdxJSONEncoder)
        except:  # pylint: disable=bare-except
            json_string = repr(json_data)

        return cls._construct(
            system,
            json_string,
            error_msg,
            location=location
        )

    @classmethod
    def from_block(cls, block, error_msg=None):
        return cls._construct(
            block.runtime,
            str(block),
            error_msg,
            location=block.location,
            for_parent=block.get_parent() if block.has_cached_parent else None
        )

    @classmethod
    def from_xml(cls, xml_data, system, id_generator,  # pylint: disable=arguments-differ
                 error_msg=None):
        '''Create an instance of this block from the supplied data.

        Does not require that xml_data be parseable--just stores it and exports
        as-is if not.

        Takes an extra, optional, parameter--the error that caused an
        issue.  (should be a string, or convert usefully into one).
        '''

        try:
            # If this is already an error tag, don't want to re-wrap it.
            xml_obj = etree.fromstring(xml_data)
            if xml_obj.tag == 'error':
                xml_data = xml_obj.text
                error_node = xml_obj.find('error_msg')
                if error_node is not None:
                    error_msg = error_node.text
                else:
                    error_msg = None

        except etree.XMLSyntaxError:
            # Save the error to display later--overrides other problems
            error_msg = exc_info_to_str(sys.exc_info())

        return cls._construct(system, xml_data, error_msg, location=id_generator.create_definition('error'))

    @classmethod
    def parse_xml(cls, node, runtime, keys):  # lint-amnesty, pylint: disable=unused-argument
        """
        Interpret the parsed XML in `node`, creating an XModuleDescriptor.
        """
        # It'd be great to not reserialize and deserialize the xml
        xml = etree.tostring(node).decode('utf-8')
        block = cls.from_xml(xml, runtime, runtime.id_generator)
        return block

    def export_to_xml(self, resource_fs):
        '''
        If the definition data is invalid xml, export it wrapped in an "error"
        tag.  If it is valid, export without the wrapper.

        NOTE: There may still be problems with the valid xml--it could be
        missing required attributes, could have the wrong tags, refer to missing
        files, etc.  That would just get re-wrapped on import.
        '''
        try:
            xml = etree.fromstring(self.contents)
            return etree.tostring(xml, encoding='unicode')
        except etree.XMLSyntaxError:
            # still not valid.
            root = etree.Element('error')
            root.text = self.contents
            err_node = etree.SubElement(root, 'error_msg')
            err_node.text = self.error_msg
            return etree.tostring(root, encoding='unicode')

    def add_xml_to_node(self, node):
        """
        Export this :class:`XModuleDescriptor` as XML, by setting attributes on the provided
        `node`.
        """
        xml_string = self.export_to_xml(self.runtime.export_fs)
        exported_node = etree.fromstring(xml_string)
        node.tag = exported_node.tag
        node.text = exported_node.text
        node.tail = exported_node.tail

        for key, value in exported_node.items():
            if key == 'url_name' and value == 'course' and key in node.attrib:
                # if url_name is set in ExportManager then do not override it here.
                continue
            node.set(key, value)

        node.extend(list(exported_node))
