# lint-amnesty, pylint: disable=missing-module-docstring
import copy
import datetime
import json
import logging
import os

from lxml import etree
from lxml.etree import ElementTree, XMLParser
from xblock.core import XML_NAMESPACES
from xblock.fields import Dict, Scope, ScopeIds
from xblock.runtime import KvsFieldData
from xmodule.modulestore import EdxJSONEncoder
from xmodule.modulestore.inheritance import InheritanceKeyValueStore, own_metadata

log = logging.getLogger(__name__)

# assume all XML files are persisted as utf-8.
EDX_XML_PARSER = XMLParser(dtd_validation=False, load_dtd=False, remove_blank_text=True, encoding='utf-8')


def name_to_pathname(name):
    """
    Convert a location name for use in a path: replace ':' with '/'.
    This allows users of the xml format to organize content into directories
    """
    return name.replace(':', '/')


def is_pointer_tag(xml_obj):
    """
    Check if xml_obj is a pointer tag: <blah url_name="something" />.
    No children, one attribute named url_name, no text.

    Special case for course roots: the pointer is
      <course url_name="something" org="myorg" course="course">

    xml_obj: an etree Element

    Returns a bool.
    """
    if xml_obj.tag != "course":
        expected_attr = {'url_name'}
    else:
        expected_attr = {'url_name', 'course', 'org'}

    actual_attr = set(xml_obj.attrib.keys())

    has_text = xml_obj.text is not None and len(xml_obj.text.strip()) > 0

    return len(xml_obj) == 0 and actual_attr == expected_attr and not has_text


def serialize_field(value):
    """
    Return a string version of the value (where value is the JSON-formatted, internally stored value).

    If the value is a string, then we simply return what was passed in.
    Otherwise, we return json.dumps on the input value.
    """
    if isinstance(value, str):
        return value
    elif isinstance(value, datetime.datetime):
        if value.tzinfo is not None and value.utcoffset() is None:
            return value.isoformat() + 'Z'
        return value.isoformat()

    return json.dumps(value, cls=EdxJSONEncoder)


def deserialize_field(field, value):
    """
    Deserialize the string version to the value stored internally.

    Note that this is not the same as the value returned by from_json, as model types typically store
    their value internally as JSON. By default, this method will return the result of calling json.loads
    on the supplied value, unless json.loads throws a TypeError, or the type of the value returned by json.loads
    is not supported for this class (from_json throws an Error). In either of those cases, this method returns
    the input value.
    """
    try:
        deserialized = json.loads(value)
        if deserialized is None:
            return deserialized
        try:
            field.from_json(deserialized)
            return deserialized
        except (ValueError, TypeError):
            # Support older serialized version, which was just a string, not result of json.dumps.
            # If the deserialized version cannot be converted to the type (via from_json),
            # just return the original value. For example, if a string value of '3.4' was
            # stored for a String field (before we started storing the result of json.dumps),
            # then it would be deserialized as 3.4, but 3.4 is not supported for a String
            # field. Therefore field.from_json(3.4) will throw an Error, and we should
            # actually return the original value of '3.4'.
            return value

    except (ValueError, TypeError):
        # Support older serialized version.
        return value


class XmlMixin:
    """
    Class containing XML parsing functionality shared between XBlock and XModuleDescriptor.
    """
    resources_dir = None

    # Extension to append to filename paths
    filename_extension = 'xml'

    xml_attributes = Dict(help="Map of unhandled xml attributes, used only for storage between import and export",
                          default={}, scope=Scope.settings)

    metadata_to_strip = ('data_dir',
                         'tabs', 'grading_policy',
                         'discussion_blackouts',
                         # VS[compat]
                         # These attributes should have been removed from here once all 2012-fall courses imported into
                         # the CMS and "inline" OLX format deprecated. But, it never got deprecated. Moreover, it's
                         # widely used to this date. So, we still have to strip them. Also, removing of "filename"
                         # changes OLX returned by `/api/olx-export/v1/xblock/{block_id}/`, which indicates that some
                         # places in the platform rely on it.
                         'course', 'org', 'url_name', 'filename',
                         # Used for storing xml attributes between import and export, for roundtrips
                         'xml_attributes',
                         # Used by _import_xml_node_to_parent in cms/djangoapps/contentstore/helpers.py to prevent
                         # XmlMixin from treating some XML nodes as "pointer nodes".
                         "x-is-pointer-node",
                         )

    # This is a categories to fields map that contains the block category specific fields which should not be
    # cleaned and/or override while adding xml to node.
    metadata_to_not_to_clean = {
        # A category `video` having `sub` and `transcripts` fields
        # which should not be cleaned/override in an xml object.
        'video': ('sub', 'transcripts')
    }

    metadata_to_export_to_policy = ('discussion_topics',)

    @staticmethod
    def _get_metadata_from_xml(xml_object, remove=True):
        """
        Extract the metadata from the XML.
        """
        meta = xml_object.find('meta')
        if meta is None:
            return ''
        dmdata = meta.text
        if remove:
            xml_object.remove(meta)
        return dmdata

    @classmethod
    def definition_from_xml(cls, xml_object, system):
        """
        Return the definition to be passed to the newly created block
        during from_xml

        xml_object: An etree Element
        """
        raise NotImplementedError("%s does not implement definition_from_xml" % cls.__name__)

    @classmethod
    def clean_metadata_from_xml(cls, xml_object, excluded_fields=()):
        """
        Remove any attribute named for a field with scope Scope.settings from the supplied
        xml_object
        """
        for field_name, field in cls.fields.items():
            if (field.scope == Scope.settings
                    and field_name not in excluded_fields
                    and xml_object.get(field_name) is not None):
                del xml_object.attrib[field_name]

    @classmethod
    def file_to_xml(cls, file_object):
        """
        Used when this module wants to parse a file object to xml
        that will be converted to the definition.

        Returns an lxml Element
        """
        return etree.parse(file_object, parser=EDX_XML_PARSER).getroot()

    @classmethod
    def load_file(cls, filepath, fs, def_id):  # pylint: disable=invalid-name
        """
        Open the specified file in fs, and call cls.file_to_xml on it,
        returning the lxml object.

        Add details and reraise on error.
        """
        try:
            with fs.open(filepath) as xml_file:
                return cls.file_to_xml(xml_file)
        except Exception as err:  # lint-amnesty, pylint: disable=broad-except
            # Add info about where we are, but keep the traceback
            raise Exception(f'Unable to load file contents at path {filepath} for item {def_id}: {err}') from err

    @classmethod
    def load_definition(cls, xml_object, system, def_id, id_generator):
        """
        Load a block from the specified xml_object.
        Subclasses should not need to override this except in special
        cases (e.g. html block)

        Args:
            xml_object: an lxml.etree._Element containing the definition to load
            system: the modulestore system (aka, runtime) which accesses data and provides access to services
            def_id: the definition id for the block--used to compute the usage id and asides ids
            id_generator: used to generate the usage_id
        """

        # VS[compat]
        # The filename attr should have been removed once all 2012-fall courses imported into the CMS and "inline" OLX
        # format deprecated. This never happened, and `filename` is still used, so we have too keep both formats.
        filename = xml_object.get('filename')
        if filename is None:
            definition_xml = copy.deepcopy(xml_object)
            filepath = ''
            aside_children = []
        else:
            filepath = cls._format_filepath(xml_object.tag, filename)

            # VS[compat]
            # If the file doesn't exist at the right path, give the class a chance to fix it up. The file will be
            # written out again in the correct format. This should have gone away once the CMS became online and had
            # imported all 2012-fall courses from XML.
            if not system.resources_fs.exists(filepath) and hasattr(cls, 'backcompat_paths'):
                candidates = cls.backcompat_paths(filepath)
                for candidate in candidates:
                    if system.resources_fs.exists(candidate):
                        filepath = candidate
                        break

            definition_xml = cls.load_file(filepath, system.resources_fs, def_id)
            usage_id = id_generator.create_usage(def_id)
            aside_children = system.parse_asides(definition_xml, def_id, usage_id, id_generator)

            # Add the attributes from the pointer node
            definition_xml.attrib.update(xml_object.attrib)

        definition_metadata = cls._get_metadata_from_xml(definition_xml)
        cls.clean_metadata_from_xml(definition_xml)
        definition, children = cls.definition_from_xml(definition_xml, system)
        if definition_metadata:
            definition['definition_metadata'] = definition_metadata
        definition['filename'] = [filepath, filename]

        if aside_children:
            definition['aside_children'] = aside_children

        return definition, children

    @classmethod
    def load_metadata(cls, xml_object):
        """
        Read the metadata attributes from this xml_object.

        Returns a dictionary {key: value}.
        """
        metadata = {'xml_attributes': {}}
        for attr, val in xml_object.attrib.items():

            if attr in cls.metadata_to_strip:
                # don't load these
                continue

            if attr not in cls.fields:
                metadata['xml_attributes'][attr] = val
            else:
                metadata[attr] = deserialize_field(cls.fields[attr], val)
        return metadata

    @classmethod
    def apply_policy(cls, metadata, policy):
        """
        Add the keys in policy to metadata, after processing them
        through the attrmap.  Updates the metadata dict in place.
        """
        for attr, value in policy.items():
            if attr not in cls.fields:
                # Store unknown attributes coming from policy.json
                # in such a way that they will export to xml unchanged
                metadata['xml_attributes'][attr] = value
            else:
                metadata[attr] = value

    @classmethod
    def parse_xml(cls, node, runtime, keys):  # pylint: disable=too-many-statements
        """
        Use `node` to construct a new block.

        Arguments:
            node (etree.Element): The xml node to parse into an xblock.

            runtime (:class:`.Runtime`): The runtime to use while parsing.

            keys (:class:`.ScopeIds`): The keys identifying where this block
                will store its data.

        Returns (XBlock): The newly parsed XBlock

        """
        from xmodule.modulestore.xml import XMLImportingModuleStoreRuntime  # done here to avoid circular import

        if keys is None:
            # Passing keys=None is against the XBlock API but some platform tests do it.
            def_id = runtime.id_generator.create_definition(node.tag, node.get('url_name'))
            keys = ScopeIds(None, node.tag, def_id, runtime.id_generator.create_usage(def_id))
        aside_children = []

        # VS[compat]
        # In 2012, when the platform didn't have CMS, and all courses were handwritten XML files, problem tags
        # contained XML problem descriptions withing themselves. Later, when Studio has been created, and "pointer" tags
        # became the preferred problem format, edX has to add this compatibility code to 1) support both pre- and
        # post-Studio course formats simulteneously, and 2) be able to migrate 2012-fall courses to Studio. Old style
        # support supposed to be removed, but the deprecation process have never been initiated, so this
        # compatibility must stay, probably forever.
        if is_pointer_tag(node):
            # new style:
            # read the actual definition file--named using url_name.replace(':','/')
            definition_xml, filepath = cls.load_definition_xml(node, runtime, keys.def_id)
            aside_children = runtime.parse_asides(definition_xml, keys.def_id, keys.usage_id, runtime.id_generator)
        else:
            filepath = None
            definition_xml = node

        # Note: removes metadata.
        definition, children = cls.load_definition(definition_xml, runtime, keys.def_id, runtime.id_generator)

        # VS[compat]
        # Make Ike's github preview links work in both old and new file layouts.
        if is_pointer_tag(node):
            # new style -- contents actually at filepath
            definition['filename'] = [filepath, filepath]

        metadata = cls.load_metadata(definition_xml)

        # move definition metadata into dict
        dmdata = definition.get('definition_metadata', '')
        if dmdata:
            metadata['definition_metadata_raw'] = dmdata
            try:
                metadata.update(json.loads(dmdata))
            except Exception as err:  # lint-amnesty, pylint: disable=broad-except
                log.debug('Error in loading metadata %r', dmdata, exc_info=True)
                metadata['definition_metadata_err'] = str(err)

        definition_aside_children = definition.pop('aside_children', None)
        if definition_aside_children:
            aside_children.extend(definition_aside_children)

        # Set/override any metadata specified by policy
        cls.apply_policy(metadata, runtime.get_policy(keys.usage_id))

        field_data = {**metadata, **definition, "children": children}
        field_data['xml_attributes']['filename'] = definition.get('filename', ['', None])  # for git link
        if "filename" in field_data:
            del field_data["filename"]  # filename should only be in xml_attributes.

        if isinstance(runtime, XMLImportingModuleStoreRuntime):
            # we shouldn't be instantiating our own field data instance here, but there are complex
            # inter-depenencies between this mixin and XMLImportingModuleStoreRuntime that currently
            # seem to require it for proper metadata inheritance.
            kvs = InheritanceKeyValueStore(initial_values=field_data)
            field_data = KvsFieldData(kvs)
            xblock = runtime.construct_xblock_from_class(cls, keys, field_data)
        else:
            # The "normal" / new way to set field data:
            xblock = runtime.construct_xblock_from_class(cls, keys)
            for (key, value_jsonish) in field_data.items():
                if key in cls.fields:
                    setattr(xblock, key, cls.fields[key].from_json(value_jsonish))
                elif key == 'children':
                    xblock.children = value_jsonish
                else:
                    log.warning(
                        "Imported %s XBlock does not have field %s found in XML.", xblock.scope_ids.block_type, key,
                    )

        if aside_children:
            cls.add_applicable_asides_to_block(xblock, runtime, aside_children)

        return xblock

    @classmethod
    def add_applicable_asides_to_block(cls, block, runtime, aside_children):
        """
        Add asides to the block. Moved this out of the parse_xml method to use it in the VideoBlock.parse_xml
        """
        asides_tags = [aside_child.tag for aside_child in aside_children]
        asides = runtime.get_asides(block)
        for aside in asides:
            if aside.scope_ids.block_type in asides_tags:
                block.add_aside(aside)

    @classmethod
    def parse_xml_new_runtime(cls, node, runtime, keys):
        """
        This XML lives within Learning Core and the new runtime doesn't need this
        legacy XModule code. Use the "normal" XBlock parsing code.
        """
        try:
            return super().parse_xml_new_runtime(node, runtime, keys)
        except AttributeError:
            return super().parse_xml(node, runtime, keys)

    @classmethod
    def load_definition_xml(cls, node, runtime, def_id):
        """
        Loads definition_xml stored in a dedicated file
        """
        url_name = node.get('url_name')
        filepath = cls._format_filepath(node.tag, name_to_pathname(url_name))
        definition_xml = cls.load_file(filepath, runtime.resources_fs, def_id)
        return definition_xml, filepath

    @classmethod
    def _format_filepath(cls, category, name):
        return f'{category}/{name}.{cls.filename_extension}'

    def export_to_file(self):
        """If this returns True, write the definition of this block to a separate
        file.

        NOTE: Do not override this without a good reason.  It is here
        specifically for customtag...
        """
        return True

    def add_xml_to_node(self, node):
        """
        For exporting, set data on `node` from ourselves.
        """
        # Get the definition
        xml_object = self.definition_to_xml(self.runtime.export_fs)

        # If xml_object is None, we don't know how to serialize this node, but
        # we shouldn't crash out the whole export for it.
        if xml_object is None:
            return

        for aside in self.runtime.get_asides(self):
            if aside.needs_serialization():
                aside_node = etree.Element("unknown_root", nsmap=XML_NAMESPACES)
                aside.add_xml_to_node(aside_node)
                xml_object.append(aside_node)

        not_to_clean_fields = self.metadata_to_not_to_clean.get(self.category, ())
        self.clean_metadata_from_xml(xml_object, excluded_fields=not_to_clean_fields)

        # Set the tag on both nodes so we get the file path right.
        xml_object.tag = self.category
        node.tag = self.category

        # Add the non-inherited metadata
        for attr in sorted(own_metadata(self)):
            # don't want e.g. data_dir
            if (attr not in self.metadata_to_strip
                    and attr not in self.metadata_to_export_to_policy
                    and attr not in not_to_clean_fields):
                val = serialize_field(self.fields[attr].to_json(getattr(self, attr)))
                try:
                    xml_object.set(attr, val)
                except Exception:  # lint-amnesty, pylint: disable=broad-except
                    logging.exception(
                        'Failed to serialize metadata attribute %s with value %s in module %s. This could mean data loss!!!',  # lint-amnesty, pylint: disable=line-too-long
                        attr, val, self.url_name
                    )

        for key, value in self.xml_attributes.items():
            if key not in self.metadata_to_strip:
                xml_object.set(key, serialize_field(value))

        if self.export_to_file():
            # Write the definition to a file
            url_path = name_to_pathname(self.url_name)
            # if folder is course then create file with name {course_run}.xml
            filepath = self._format_filepath(
                self.category,
                self.location.run if self.category == 'course' else url_path,
            )
            self.runtime.export_fs.makedirs(os.path.dirname(filepath), recreate=True)
            with self.runtime.export_fs.open(filepath, 'wb') as fileobj:
                ElementTree(xml_object).write(fileobj, pretty_print=True, encoding='utf-8')
        else:
            # Write all attributes from xml_object onto node
            node.clear()
            node.tag = xml_object.tag
            node.text = xml_object.text
            node.tail = xml_object.tail
            node.attrib.update(xml_object.attrib)
            node.extend(xml_object)

        # Do not override an existing value for the course.
        if not node.get('url_name'):
            node.set('url_name', self.url_name)

        # Special case for course pointers:
        if self.category == 'course':
            # add org and course attributes on the pointer tag
            node.set('org', self.location.org)
            node.set('course', self.location.course)

    def definition_to_xml(self, resource_fs):
        """
        Return a new etree Element object created from this modules definition.
        """
        raise NotImplementedError(
            "%s does not implement definition_to_xml" % self.__class__.__name__)

    @property
    def non_editable_metadata_fields(self):
        """
        Return a list of all metadata fields that cannot be edited.
        """
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.append(XmlMixin.xml_attributes)
        return non_editable_fields
