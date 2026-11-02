# lint-amnesty, pylint: disable=missing-module-docstring

import glob
import hashlib
import itertools
import json
import logging
import os
import re
import warnings
import sys
from collections import defaultdict
from contextlib import contextmanager
from importlib import import_module

from fs.osfs import OSFS
from lxml import etree
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator, LibraryLocator
from path import Path as path
from xblock.core import XBlockAside
from xblock.field_data import DictFieldData
from xblock.fields import (
    Reference,
    ReferenceList,
    ReferenceValueDict,
    ScopeIds,
)
from xblock.runtime import DictKeyValueStore

from common.djangoapps.util.monitoring import monitor_import_failure
from xmodule.error_block import ErrorBlock
from xmodule.errortracker import exc_info_to_str, make_error_tracker
from xmodule.modulestore import COURSE_ROOT, LIBRARY_ROOT, ModuleStoreEnum, ModuleStoreReadBase
from xmodule.modulestore.xml_exporter import DEFAULT_CONTENT_FIELDS
from xmodule.tabs import CourseTabList
from xmodule.x_module import (
    AsideKeyGenerator,
    OpaqueKeyReader,
    ModuleStoreRuntime,
    policy_key
)

from .exceptions import ItemNotFoundError
from .inheritance import compute_inherited_metadata, inheriting_field_data

edx_xml_parser = etree.XMLParser(dtd_validation=False, load_dtd=False, remove_blank_text=True)

etree.set_default_parser(edx_xml_parser)

log = logging.getLogger(__name__)


class XMLParsingModuleStoreRuntime(ModuleStoreRuntime):
    """
    ModuleStoreRuntime with some tweaks for XML processing.
    """
    def __init__(self, process_xml, **kwargs):
        """
        process_xml: Takes an xml string, and returns a XModuleDescriptor
            created from that xml
        """

        super().__init__(**kwargs)
        self.process_xml = process_xml

    def _usage_id_from_node(self, node, parent_id):
        """Create a new usage id from an XML dom node.

        Args:
            node (lxml.etree.Element): The DOM node to interpret.
            parent_id: The usage ID of the parent block
        Returns:
            UsageKey: the usage key for the new xblock
        """
        return self.xblock_from_node(node, parent_id, self.id_generator).scope_ids.usage_id

    def xblock_from_node(self, node, parent_id, id_generator=None):
        """
        Create an XBlock instance from XML data.

        Args:
            id_generator (IdGenerator): An :class:`~xblock.runtime.IdGenerator` that
                will be used to construct the usage_id and definition_id for the block.

        Returns:
            XBlock: The fully instantiated :class:`~xblock.core.XBlock`.

        """
        id_generator = id_generator or self.id_generator
        # leave next line commented out - useful for low-level debugging
        # log.debug('[_usage_id_from_node] tag=%s, class=%s' % (node.tag, xblock_class))

        block_type = node.tag
        # remove xblock-family from elements
        node.attrib.pop('xblock-family', None)

        url_name = node.get('url_name')  # difference from XBlock.runtime
        def_id = id_generator.create_definition(block_type, url_name)
        usage_id = id_generator.create_usage(def_id)

        keys = ScopeIds(None, block_type, def_id, usage_id)
        block_class = self.mixologist.mix(self.load_block_type(block_type))

        aside_children = self.parse_asides(node, def_id, usage_id, id_generator)
        asides_tags = [x.tag for x in aside_children]

        block = block_class.parse_xml(node, self, keys)
        self._convert_reference_fields_to_keys(block)  # difference from XBlock.runtime
        block.parent = parent_id
        block.save()

        asides = self.get_asides(block)
        for asd in asides:
            if asd.scope_ids.block_type in asides_tags:
                block.add_aside(asd)

        return block

    def parse_asides(self, node, def_id, usage_id, id_generator):
        """pull the asides out of the xml payload and instantiate them"""
        aside_children = []
        for child in node.iterchildren():
            # get xblock-family from node
            xblock_family = child.attrib.pop('xblock-family', None)
            if xblock_family:
                xblock_family = self._family_id_to_superclass(xblock_family)
                if issubclass(xblock_family, XBlockAside):
                    aside_children.append(child)
        # now process them & remove them from the xml payload
        for child in aside_children:
            self._aside_from_xml(child, def_id, usage_id)
            node.remove(child)
        return aside_children

    def _make_usage_key(self, course_key, value):
        """
        Makes value into a UsageKey inside the specified course.
        If value is already a UsageKey, returns that.
        """
        if isinstance(value, UsageKey):
            return value
        usage_key = UsageKey.from_string(value)
        return usage_key.map_into_course(course_key)

    def _convert_reference_fields_to_keys(self, xblock):
        """
        Find all fields of type reference and convert the payload into UsageKeys
        """
        course_key = xblock.scope_ids.usage_id.course_key

        for field in xblock.fields.values():
            if field.is_set_on(xblock):
                field_value = getattr(xblock, field.name)
                if field_value is None:
                    continue
                elif isinstance(field, Reference):
                    setattr(xblock, field.name, self._make_usage_key(course_key, field_value))
                elif isinstance(field, ReferenceList):
                    setattr(xblock, field.name, [self._make_usage_key(course_key, ele) for ele in field_value])
                elif isinstance(field, ReferenceValueDict):
                    for key, subvalue in field_value.items():
                        assert isinstance(subvalue, str)
                        field_value[key] = self._make_usage_key(course_key, subvalue)
                    setattr(xblock, field.name, field_value)


class XMLImportingModuleStoreRuntime(XMLParsingModuleStoreRuntime):  # pylint: disable=abstract-method
    """
    A runtime for importing OLX into ModuleStore.
    """
    def __init__(self, xmlstore, course_id, course_dir,  # lint-amnesty, pylint: disable=too-many-statements
                 error_tracker,
                 load_error_blocks=True, target_course_id=None, **kwargs):
        """
        A class that handles loading from xml to ModuleStore.  Does some munging to ensure that
        all elements have unique slugs.

        xmlstore: the XMLModuleStore to store the loaded blocks in
        """
        self.unnamed = defaultdict(int)  # category -> num of new url_names for that category
        self.used_names = defaultdict(set)  # category -> set of used url_names

        # Adding the course_id as passed in for later reference rather than
        # having to recombine the org/course/url_name
        self.course_id = course_id
        self.load_error_blocks = load_error_blocks
        self.modulestore = xmlstore

        def process_xml(xml):  # lint-amnesty, pylint: disable=too-many-statements
            """Takes an xml string, and returns a XBlock created from
            that xml.
            """

            def make_name_unique(xml_data):
                """
                Make sure that the url_name of xml_data is unique.  If a previously loaded
                unnamed descriptor stole this element's url_name, create a new one.

                Removes 'slug' attribute if present, and adds or overwrites the 'url_name' attribute.
                """
                # VS[compat]. Take this out once course conversion is done (perhaps leave the uniqueness check)

                # tags that really need unique names--they store (or should store) state.
                need_uniq_names = ('problem', 'sequential', 'video', 'course', 'chapter',
                                   'poll_question', 'vertical')

                attr = xml_data.attrib
                tag = xml_data.tag
                id = lambda x: x  # lint-amnesty, pylint: disable=redefined-builtin
                # Things to try to get a name, in order  (key, cleaning function, remove key after reading?)
                lookups = [('url_name', id, False),
                           ('slug', id, True),
                           ('name', BlockUsageLocator.clean, False),
                           ('display_name', BlockUsageLocator.clean, False)]

                url_name = None
                for key, clean, remove in lookups:
                    if key in attr:
                        url_name = clean(attr[key])
                        if remove:
                            del attr[key]
                        break

                def looks_like_fallback(url_name):
                    """Does this look like something that came from fallback_name()?"""
                    return (url_name is not None
                            and url_name.startswith(tag)
                            and re.search('[0-9a-fA-F]{12}$', url_name))

                def fallback_name(orig_name=None):
                    """Return the fallback name for this block.  This is a function instead of a variable
                    because we want it to be lazy."""
                    if looks_like_fallback(orig_name):
                        # We're about to re-hash, in case something changed, so get rid of the tag_ and hash
                        orig_name = orig_name[len(tag) + 1:-12]
                    # append the hash of the content--the first 12 bytes should be plenty.
                    orig_name = "_" + orig_name if orig_name not in (None, "") else ""
                    xml_bytes = xml if isinstance(xml, bytes) else xml.encode('utf-8')
                    return tag + orig_name + "_" + hashlib.sha1(xml_bytes).hexdigest()[:12]

                # Fallback if there was nothing we could use:
                if url_name is None or url_name == "":
                    url_name = fallback_name()
                    # Don't log a warning--we don't need this in the log.  Do
                    # put it in the error tracker--content folks need to see it.

                    if tag in need_uniq_names:
                        error_tracker("PROBLEM: no name of any kind specified for {tag}.  Student "
                                      "state will not be properly tracked for this block.  Problem xml:"
                                      " '{xml}...'".format(tag=tag, xml=xml[:100]))
                    else:
                        # TODO (vshnayder): We may want to enable this once course repos are cleaned up.
                        # (or we may want to give up on the requirement for non-state-relevant issues...)
                        # error_tracker("WARNING: no name specified for block. xml='{0}...'".format(xml[:100]))
                        pass

                # Make sure everything is unique
                if url_name in self.used_names[tag]:
                    # Always complain about blocks that store state.  If it
                    # doesn't store state, don't complain about things that are
                    # hashed.
                    if tag in need_uniq_names:
                        msg = ("Non-unique url_name in xml.  This may break state tracking for content."
                               "  url_name={}.  Content={}".format(url_name, xml[:100]))
                        error_tracker("PROBLEM: " + msg)
                        log.warning(msg)
                        # Just set name to fallback_name--if there are multiple things with the same fallback name,
                        # they are actually identical, so it's fragile, but not immediately broken.

                        # TODO (vshnayder): if the tag is a pointer tag, this will
                        # break the content because we won't have the right link.
                        # That's also a legitimate attempt to reuse the same content
                        # from multiple places.  Once we actually allow that, we'll
                        # need to update this to complain about non-unique names for
                        # definitions, but allow multiple uses.
                        url_name = fallback_name(url_name)

                self.used_names[tag].add(url_name)
                xml_data.set('url_name', url_name)

            try:
                xml_data = etree.fromstring(xml)
                make_name_unique(xml_data)
                block = self.xblock_from_node(
                    xml_data,
                    None,  # parent_id
                    id_manager,
                )
            except Exception as err:  # pylint: disable=broad-except
                if not self.load_error_blocks:
                    raise

                # Didn't load properly.  Fall back on loading as an error
                # block.  This should never error due to formatting.

                msg = "Error loading from xml. %s"
                log.warning(
                    msg,
                    str(err)[:200],
                    # Normally, we don't want lots of exception traces in our logs from common
                    # content problems.  But if you're debugging the xml loading code itself,
                    # uncomment the next line.
                    # exc_info=True
                )

                msg = msg % (str(err)[:200])

                self.error_tracker(msg)
                err_msg = msg + "\n" + exc_info_to_str(sys.exc_info())
                block = ErrorBlock.from_xml(
                    xml,
                    self,
                    id_manager,
                    err_msg
                )

            block.data_dir = course_dir

            if block.scope_ids.usage_id in xmlstore.modules[course_id]:
                # keep the parent pointer if any but allow everything else to overwrite
                other_copy = xmlstore.modules[course_id][block.scope_ids.usage_id]
                block.parent = other_copy.parent
                if block != other_copy:
                    log.warning("%s has more than one definition", block.scope_ids.usage_id)
            xmlstore.modules[course_id][block.scope_ids.usage_id] = block

            if block.has_children:
                for child in block.get_children():
                    # parent is alphabetically least
                    if child.parent is None or child.parent > block.scope_ids.usage_id:
                        child.parent = block.location
                        child.save()

            # After setting up the block, save any changes that we have
            # made to attributes on the block to the underlying KeyValueStore.
            block.save()
            return block

        render_template = lambda template, context: ''

        # TODO (vshnayder): we are somewhat architecturally confused in the loading code:
        # load_item should actually be get_instance, because it expects the course-specific
        # policy to be loaded.  For now, just add the course_id here...
        def load_item(usage_key, for_parent=None):
            """Return the XBlock for the specified location"""
            return xmlstore.get_item(usage_key, for_parent=for_parent)

        resources_fs = OSFS(xmlstore.data_dir / course_dir)

        id_manager = CourseImportLocationManager(course_id, target_course_id)

        super().__init__(
            load_item=load_item,
            resources_fs=resources_fs,
            render_template=render_template,
            error_tracker=error_tracker,
            process_xml=process_xml,
            id_generator=id_manager,
            id_reader=id_manager,
            **kwargs
        )

    # pylint: disable=keyword-arg-before-vararg
    def construct_xblock_from_class(self, cls, scope_ids, field_data=None, *args, **kwargs):
        """
        Construct a new xblock of type cls, mixing in the mixins
        defined for this application.
        """
        if field_data:
            # Currently, *some* XBlocks (those with XmlMixin) use XmlMixin.parse_xml() which instantiates
            # its own key value store for field data. That is something which should be left to the runtime, for
            # consistent behavior across all XBlocks, not controlled by individual XBlock implementations.
            # We cannot just ignore field_data here though, because parse_xml may have pre-loaded data into it that we
            # would otherwise lose.
            warnings.warn(
                'XBlocks should not instantiate their own field_data store during parse_xml()',
                DeprecationWarning, stacklevel=2,
            )
        return super().construct_xblock_from_class(cls, scope_ids, field_data, *args, **kwargs)

    # id_generator is ignored, because each XMLImportingModuleStoreRuntime is already local to
    # a course, and has it's own id_generator already in place
    def add_node_as_child(self, block, node):  # lint-amnesty, pylint: disable=signature-differs
        child_block = self.process_xml(etree.tostring(node))
        block.children.append(child_block.scope_ids.usage_id)


class CourseLocationManager(OpaqueKeyReader, AsideKeyGenerator):
    """
    IdGenerator for Location-based definition ids and usage ids
    based within a course
    """
    def __init__(self, course_id):
        super().__init__()
        self.course_id = course_id
        self.autogen_ids = itertools.count(0)

    def create_usage(self, def_id):
        return def_id

    def create_definition(self, block_type, slug=None):
        assert block_type is not None
        if slug is None:
            slug = f'autogen_{block_type}_{next(self.autogen_ids)}'
        return self.course_id.make_usage_key(block_type, slug)

    def get_definition_id(self, usage_id):
        """Retrieve the definition that a usage is derived from.

        Args:
            usage_id: The id of the usage to query

        Returns:
            The `definition_id` the usage is derived from
        """
        return usage_id


class CourseImportLocationManager(CourseLocationManager):
    """
    IdGenerator for Location-based definition ids and usage ids
    based within a course, for use during course import.

    In addition to the functionality provided by CourseLocationManager,
    this class also contains the target_course_id for the course import
    process.

    Note: This is a temporary solution to workaround the fact that
    the from_xml method is passed the source course_id instead of the
    target course_id in the import process. For a more ideal solution,
    see https://openedx.atlassian.net/browse/MA-417 as a pending TODO.
    """
    def __init__(self, course_id, target_course_id):
        super().__init__(course_id=course_id)
        self.target_course_id = target_course_id


class XMLModuleStore(ModuleStoreReadBase):
    """
    An XML backed ModuleStore
    """
    parent_xml = COURSE_ROOT

    def __init__(
            self, data_dir, default_class=None, source_dirs=None, course_ids=None,
            load_error_blocks=True, i18n_service=None, fs_service=None, user_service=None,
            signal_handler=None, target_course_id=None, **kwargs   # pylint: disable=unused-argument
    ):
        """
        Initialize an XMLModuleStore from data_dir

        Args:
            data_dir (str): path to data directory containing the course directories

            default_class (str): dot-separated string defining the default block
                class to use if none is specified in entry_points

            source_dirs or course_ids (list of str): If specified, the list of source_dirs or course_ids to load.
                Otherwise, load all courses. Note, providing both
        """
        super().__init__(**kwargs)

        self.data_dir = path(data_dir)
        self.modules = defaultdict(dict)  # course_id -> dict(location -> XBlock)
        self.courses = {}  # course_dir -> XBlock for the course
        self.errored_courses = {}  # course_dir -> errorlog, for dirs that failed to load

        if course_ids is not None:
            course_ids = [CourseKey.from_string(course_id) for course_id in course_ids]

        self.load_error_blocks = load_error_blocks

        if default_class is None:
            self.default_class = None
        else:
            module_path, _, class_name = default_class.rpartition('.')
            try:
                class_ = getattr(import_module(module_path), class_name)
            except (ImportError, AttributeError):
                fallback_module_path = "xmodule.hidden_block"
                fallback_class_name = "HiddenBlock"
                log.exception(
                    "Failed to import the default store class. "
                    f"Falling back to {fallback_module_path}.{fallback_class_name}"
                )
                class_ = getattr(import_module(fallback_module_path), fallback_class_name)
            self.default_class = class_

        # All field data will be stored in an inheriting field data.
        self.field_data = inheriting_field_data(kvs=DictKeyValueStore())

        self.i18n_service = i18n_service
        self.fs_service = fs_service
        self.user_service = user_service

        # If we are specifically asked for missing courses, that should
        # be an error.  If we are asked for "all" courses, find the ones
        # that have a course.xml. We sort the dirs in alpha order so we always
        # read things in the same order (OS differences in load order have
        # bitten us in the past.)

        if source_dirs is None:
            source_dirs = sorted([d for d in os.listdir(self.data_dir) if
                                  os.path.exists(self.data_dir / d / self.parent_xml)])
        for course_dir in source_dirs:
            self.try_load_course(course_dir, course_ids, target_course_id)

    def try_load_course(self, course_dir, course_ids=None, target_course_id=None):
        '''
        Load a course, keeping track of errors as we go along. If course_ids is not None,
        then reject the course unless its id is in course_ids.
        '''
        # Special-case code here, since we don't have a location for the
        # course before it loads.
        # So, make a tracker to track load-time errors, then put in the right
        # place after the course loads and we have its location
        errorlog = make_error_tracker()
        course_block = None
        try:
            course_block = self.load_course(course_dir, course_ids, errorlog.tracker, target_course_id)
        except Exception as exc:  # pylint: disable=broad-except
            msg = f'Course import {target_course_id}: ERROR: Failed to load courselike "{course_dir}": {str(exc)}'
            log.exception(msg)
            errorlog.tracker(msg)
            self.errored_courses[course_dir] = errorlog
            monitor_import_failure(target_course_id, 'Updating', exception=exc)
            raise exc
        finally:
            if course_block is None:
                pass
            elif isinstance(course_block, ErrorBlock):
                # Didn't load course.  Instead, save the errors elsewhere.
                self.errored_courses[course_dir] = errorlog
            else:
                self.courses[course_dir] = course_block
                course_block.parent = None
                course_id = self.id_from_block(course_block)
                self._course_errors[course_id] = errorlog

    def __str__(self):
        '''
        String representation - for debugging
        '''
        return '<%s data_dir=%r, %d courselikes, %d modules>' % (  # xss-lint: disable=python-interpolate-html
            self.__class__.__name__, self.data_dir, len(self.courses), len(self.modules)
        )

    @staticmethod
    def id_from_block(block):
        """
        Grab the course ID from the block
        """
        return block.id

    def load_policy(self, policy_path, tracker):
        """
        Attempt to read a course policy from policy_path.  If the file
        exists, but is invalid, log an error and return {}.

        If the policy loads correctly, returns the deserialized version.
        """
        if not os.path.exists(policy_path):
            return {}
        try:
            with open(policy_path) as f:
                return json.load(f)
        except (OSError, ValueError) as err:
            msg = f"ERROR: loading courselike policy from {policy_path}"
            tracker(msg)
            log.warning(msg + " " + str(err))  # lint-amnesty, pylint: disable=logging-not-lazy
        return {}

    def load_course(self, course_dir, course_ids, tracker, target_course_id=None):
        """
        Load a course into this module store
        course_path: Course directory name

        returns a CourseBlock for the course
        """
        log.info(f'Course import {target_course_id}: Starting courselike import from {course_dir}')
        with open(self.data_dir / course_dir / self.parent_xml) as course_file:
            course_data = etree.parse(course_file, parser=edx_xml_parser).getroot()

            org = course_data.get('org')

            if org is None:
                msg = ("No 'org' attribute set for courselike in {dir}. "
                       "Using default 'edx'".format(dir=course_dir))
                log.warning(msg)
                tracker(msg)
                org = 'edx'

            # Parent XML should be something like 'library.xml' or 'course.xml'
            courselike_label = self.parent_xml.split('.', maxsplit=1)[0]

            course = course_data.get(courselike_label)

            if course is None:
                msg = (
                    "No '{courselike_label}' attribute set for course in {dir}."
                    " Using default '{default}'".format(
                        courselike_label=courselike_label,
                        dir=course_dir,
                        default=course_dir
                    )
                )
                log.warning(msg)
                tracker(msg)
                course = course_dir

            url_name = course_data.get('url_name', course_data.get('slug'))

            if url_name:
                policy_dir = self.data_dir / course_dir / 'policies' / url_name
                policy_path = policy_dir / 'policy.json'

                policy = self.load_policy(policy_path, tracker)

                # VS[compat]: remove once courses use the policy dirs.
                if policy == {}:
                    old_policy_path = self.data_dir / course_dir / 'policies' / f'{url_name}.json'
                    policy = self.load_policy(old_policy_path, tracker)
            else:
                policy = {}
                # VS[compat] : 'name' is deprecated, but support it for now...
                if course_data.get('name'):
                    url_name = BlockUsageLocator.clean(course_data.get('name'))
                    tracker("'name' is deprecated for block xml.  Please use "
                            "display_name and url_name.")
                else:
                    url_name = None

            course_id = self.get_id(org, course, url_name)

            if course_ids is not None and course_id not in course_ids:
                return None

            def get_policy(usage_id):
                """
                Return the policy dictionary to be applied to the specified XBlock usage
                """
                return policy.get(policy_key(usage_id), {})

            services = {
                'field-data': self.field_data
            }
            if self.i18n_service:
                services['i18n'] = self.i18n_service

            if self.fs_service:
                services['fs'] = self.fs_service

            if self.user_service:
                services['user'] = self.user_service

            system = XMLImportingModuleStoreRuntime(
                xmlstore=self,
                course_id=course_id,
                course_dir=course_dir,
                error_tracker=tracker,
                load_error_blocks=self.load_error_blocks,
                get_policy=get_policy,
                mixins=self.xblock_mixins,
                default_class=self.default_class,
                select=self.xblock_select,
                services=services,
                target_course_id=target_course_id,
            )
            course_block = system.process_xml(etree.tostring(course_data, encoding='unicode'))
            # If we fail to load the course, then skip the rest of the loading steps
            if isinstance(course_block, ErrorBlock):
                return course_block

            self.content_importers(system, course_block, course_dir, url_name)

            log.info(f'Course import {target_course_id}: Done with courselike import from {course_dir}')
            return course_block

    def content_importers(self, system, course_block, course_dir, url_name):
        """
        Load all extra non-course content, and calculate metadata inheritance.
        """
        # NOTE: The blocks end up loading somewhat bottom up, which
        # breaks metadata inheritance via get_children().  Instead
        # (actually, in addition to, for now), we do a final inheritance pass
        # after we have the course block.
        compute_inherited_metadata(course_block)

        # now import all pieces of course_info which is expected to be stored
        # in <content_dir>/info or <content_dir>/info/<url_name>
        self.load_extra_content(
            system, course_block, 'course_info',
            self.data_dir / course_dir / 'info',
            course_dir, url_name
        )

        # now import all static tabs which are expected to be stored in
        # in <content_dir>/tabs or <content_dir>/tabs/<url_name>
        self.load_extra_content(
            system, course_block, 'static_tab',
            self.data_dir / course_dir / 'tabs',
            course_dir, url_name
        )

        self.load_extra_content(
            system, course_block, 'custom_tag_template',
            self.data_dir / course_dir / 'custom_tags',
            course_dir, url_name
        )

        self.load_extra_content(
            system, course_block, 'about',
            self.data_dir / course_dir / 'about',
            course_dir, url_name
        )

    @staticmethod
    def get_id(org, course, url_name):
        """
        Validate and return an ID for a course if given org, course, and url_name.
        """
        if not url_name:
            raise ValueError("Can't load a course without a 'url_name' "
                             "(or 'name') set.  Set url_name.")
        # Have to use older key format here because it makes sure the same format is
        # always used, preventing duplicate keys.
        return CourseKey.from_string('/'.join([org, course, url_name]))

    def load_extra_content(self, system, course_block, category, base_dir, course_dir, url_name):  # lint-amnesty, pylint: disable=missing-function-docstring
        self._load_extra_content(system, course_block, category, base_dir, course_dir)

        # then look in a override folder based on the course run
        if os.path.isdir(base_dir / url_name):
            self._load_extra_content(system, course_block, category, base_dir / url_name, course_dir)

    def _import_field_content(self, course_block, category, file_path):
        """
        Import field data content for field other than 'data' or 'metadata' form json file and
        return field data content as dictionary
        """
        slug, location, data_content = None, None, None
        try:
            # try to read json file
            # file_path format: {dirname}.{field_name}.json
            dirname, field, file_suffix = file_path.split('/')[-1].split('.')
            if file_suffix == 'json' and field not in DEFAULT_CONTENT_FIELDS:
                slug = os.path.splitext(os.path.basename(dirname))[0]
                location = course_block.scope_ids.usage_id.replace(category=category, name=slug)
                with open(file_path) as field_content_file:
                    field_data = json.load(field_content_file)
                    data_content = {field: field_data}
        except (OSError, ValueError):
            # ignore this exception
            # only new exported courses which use content fields other than 'metadata' and 'data'
            # will have this file '{dirname}.{field_name}.json'
            data_content = None

        return slug, location, data_content

    def _load_extra_content(self, system, course_block, category, content_path, course_dir):
        """
        Import fields data content from files
        """
        for filepath in glob.glob(content_path / '*'):
            if not os.path.isfile(filepath):
                continue

            if filepath.endswith('~'):  # skip *~ files
                continue

            with open(filepath) as f:
                try:
                    if filepath.find('.json') != -1:
                        # json file with json data content
                        slug, loc, data_content = self._import_field_content(course_block, category, filepath)
                        if data_content is None:
                            continue
                        else:
                            try:
                                # get and update data field in xblock runtime
                                block = system.get_block(loc)
                                for key, value in data_content.items():
                                    setattr(block, key, value)
                                block.save()
                            except ItemNotFoundError:
                                block = None
                                data_content['location'] = loc
                                data_content['category'] = category
                    else:
                        slug = os.path.splitext(os.path.basename(filepath))[0]
                        loc = course_block.scope_ids.usage_id.replace(category=category, name=slug)
                        # html file with html data content
                        html = f.read()
                        try:
                            block = system.get_block(loc)
                            block.data = html
                            block.save()
                        except ItemNotFoundError:
                            block = None
                            data_content = {'data': html, 'location': loc, 'category': category}

                    if block is None:
                        block = system.construct_xblock(
                            category,
                            # We're loading a block, so student_id is meaningless
                            # We also don't have separate notions of definition and usage ids yet,
                            # so we use the location for both
                            ScopeIds(None, category, loc, loc),
                            DictFieldData(data_content),
                        )
                        # VS[compat]:
                        # Hack because we need to pull in the 'display_name' for static tabs (because we need to edit them)  # lint-amnesty, pylint: disable=line-too-long
                        # from the course policy
                        if category == "static_tab":
                            tab = CourseTabList.get_tab_by_slug(tab_list=course_block.tabs, url_slug=slug)
                            if tab:
                                block.display_name = tab.name
                                block.course_staff_only = tab.course_staff_only
                        block.data_dir = course_dir
                        block.save()

                        self.modules[course_block.id][block.scope_ids.usage_id] = block
                except Exception as exc:  # pylint: disable=broad-except
                    logging.exception("Failed to load %s. Skipping... \
                            Exception: %s", filepath, str(exc))
                    system.error_tracker("ERROR: " + str(exc))

    def has_item(self, usage_key):
        """
        Returns True if location exists in this ModuleStore.
        """
        return usage_key in self.modules[usage_key.course_key]

    def get_item(self, usage_key, depth=0, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Returns an XBlock instance for the item for this UsageKey.

        If any segment of the location is None except revision, raises
            xmodule.modulestore.exceptions.InsufficientSpecificationError

        If no object is found at that location, raises
            xmodule.modulestore.exceptions.ItemNotFoundError

        usage_key: a UsageKey that matches the block we are looking for.
        """
        try:
            return self.modules[usage_key.course_key][usage_key]
        except KeyError:
            raise ItemNotFoundError(usage_key)  # lint-amnesty, pylint: disable=raise-missing-from

    def get_items(self, course_id, settings=None, content=None, revision=None, qualifiers=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Returns:
            list of XBlock instances for the matching items within the course with
            the given course_id

        NOTE: don't use this to look for courses
        as the course_id is required. Use get_courses.

        Args:
            course_id (CourseKey): the course identifier
            settings (dict): fields to look for which have settings scope. Follows same syntax
                and rules as qualifiers below
            content (dict): fields to look for which have content scope. Follows same syntax and
                rules as qualifiers below.
            qualifiers (dict): what to look for within the course.
                Common qualifiers are ``category`` or any field name. if the target field is a list,
                then it searches for the given value in the list not list equivalence.
                Substring matching pass a regex object.
                For this modulestore, ``name`` is another commonly provided key (Location based stores)
                (but not revision!)
                For this modulestore,
                you can search dates by providing either a datetime for == (probably
                useless) or a tuple (">"|"<" datetime) for after or before, etc.
        """
        if revision == ModuleStoreEnum.RevisionOption.draft_only:
            return []

        blocks = []

        qualifiers = qualifiers.copy() if qualifiers else {}  # copy the qualifiers (destructively manipulated here)
        category = qualifiers.pop('category', None)
        name = qualifiers.pop('name', None)

        def _block_matches_all(block_loc, block):
            if category and block_loc.category != category:
                return False
            if name:
                if isinstance(name, list):
                    # Support for passing a list as the name qualifier
                    if block_loc.name not in name:
                        return False
                elif block_loc.name != name:
                    return False
            return all(
                self._block_matches(block, fields or {})
                for fields in [settings, content, qualifiers]
            )

        for block_loc, block in self.modules[course_id].items():
            if _block_matches_all(block_loc, block):
                blocks.append(block)

        return blocks

    def make_course_key(self, org, course, run):
        """
        Return a valid :class:`~opaque_keys.edx.locator.CourseLocator` for this modulestore
        that matches the supplied `org`, `course`, and `run`.

        This key may represent a course that doesn't exist in this modulestore.
        """
        return CourseLocator(org, course, run, deprecated=True)

    def make_course_usage_key(self, course_key):
        """
        Return a valid :class:`~opaque_keys.edx.keys.UsageKey` for this modulestore
        that matches the supplied course_key.
        """
        return BlockUsageLocator(course_key, 'course', course_key.run)

    def get_courses(self, **kwargs):
        """
        Returns a list of course blocks.  If there were errors on loading,
        some of these may be ErrorBlock instead.
        """
        return list(self.courses.values())

    def get_course_summaries(self, **kwargs):
        """
        Returns `self.get_courses()`. Use to list courses to the global staff user.
        """
        return self.get_courses(**kwargs)

    def get_errored_courses(self):
        """
        Return a dictionary of course_dir -> [(msg, exception_str)], for each
        course_dir where course loading failed.
        """
        return {k: self.errored_courses[k].errors for k in self.errored_courses}

    def get_orphans(self, course_key, **kwargs):
        """
        Get all of the xblocks in the given course which have no parents and are not of types which are
        usually orphaned. NOTE: may include xblocks which still have references via xblocks which don't
        use children to point to their dependents.
        """
        # here just to quell the abstractmethod. someone could write the impl if needed
        raise NotImplementedError

    def get_parent_location(self, location, **kwargs):
        '''Find the location that is the parent of this location in this
        course.  Needed for path_to_location().
        '''
        block = self.get_item(location, 0)
        return block.parent

    def get_modulestore_type(self, course_key=None):  # lint-amnesty, pylint: disable=arguments-differ, unused-argument
        """
        Returns an enumeration-like type reflecting the type of this modulestore, per ModuleStoreEnum.Type
        Args:
            course_key: just for signature compatibility
        """
        # return ModuleStoreEnum.Type.xml
        return None

    def get_courses_for_wiki(self, wiki_slug, **kwargs):
        """
        Return the list of courses which use this wiki_slug
        :param wiki_slug: the course wiki root slug
        :return: list of course locations
        """
        courses = self.get_courses()
        return [course.location.course_key for course in courses if course.wiki_slug == wiki_slug]

    def heartbeat(self):
        """
        Ensure that every known course is loaded and ready to go. Really, just return b/c
        if this gets called the __init__ finished which means the courses are loaded.

        Returns the course count
        """
        return {'xml': True}

    @contextmanager
    def branch_setting(self, branch_setting, course_id=None):  # lint-amnesty, pylint: disable=unused-argument
        """
        A context manager for temporarily setting the branch value for the store to the given branch_setting.
        """
        if branch_setting != ModuleStoreEnum.Branch.published_only:
            raise ValueError(f"Cannot set branch setting to {branch_setting} on a ReadOnly store")
        yield

    def _find_course_asset(self, asset_key):
        """
        For now this is not implemented, but others should feel free to implement using the asset.json
        which export produces.
        """
        log.warning("_find_course_asset request of XML modulestore - not implemented.")
        return (None, None)

    def find_asset_metadata(self, asset_key, **kwargs):  # lint-amnesty, pylint: disable=useless-return
        """
        For now this is not implemented, but others should feel free to implement using the asset.json
        which export produces.
        """
        log.warning("find_asset_metadata request of XML modulestore - not implemented.")
        return None

    def get_all_asset_metadata(self, course_key, asset_type, start=0, maxresults=-1, sort=None, **kwargs):
        """
        For now this is not implemented, but others should feel free to implement using the asset.json
        which export produces.
        """
        log.warning("get_all_asset_metadata request of XML modulestore - not implemented.")
        return []

    def fill_in_run(self, course_key):
        """
        A no-op.

        Added to simplify tests which use the XML-store directly.
        """
        return course_key


class LibraryXMLModuleStore(XMLModuleStore):
    """
    A modulestore for importing Libraries from XML.
    """
    parent_xml = LIBRARY_ROOT

    @staticmethod
    def get_id(org, library, url_name):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Create a LibraryLocator given an org and library. url_name is ignored, but left in
        for compatibility with the parent signature.
        """
        return LibraryLocator(org=org, library=library)

    def content_importers(self, system, course_block, course_dir, url_name):
        """
        Handle Metadata inheritance for Libraries.
        """
        compute_inherited_metadata(course_block)

    def get_library(self, library_id, depth=0, **kwargs):  # pylint: disable=unused-argument
        """
        Get a library from this modulestore or return None if it does not exist.
        """
        assert isinstance(library_id, LibraryLocator)
        for library in self.get_courses(**kwargs):
            if library.location.library_key == library_id:
                return library
        return None

    @staticmethod
    def id_from_block(block):
        """
        Get the Library Key from the Library block.
        """
        return block.location.library_key

    def get_orphans(self, course_key, **kwargs):
        """
        Get all of the xblocks in the given course which have no parents and are not of types which are
        usually orphaned. NOTE: may include xblocks which still have references via xblocks which don't
        use children to point to their dependents.
        """
        # here just to quell the abstractmethod. someone could write the impl if needed
        raise NotImplementedError
