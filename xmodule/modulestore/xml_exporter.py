"""
Methods for exporting course data to XML
"""


import logging
import os
from abc import abstractmethod
from json import dumps

import lxml.etree
from edx_django_utils.monitoring import set_custom_attribute
from fs.osfs import OSFS
from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from xblock.fields import Reference, ReferenceList, ReferenceValueDict, Scope

from openedx.core.djangoapps.content_tagging.api import export_tags_in_csv_file, get_object_tag_counts
from xmodule.assetstore import AssetMetadata
from xmodule.contentstore.content import StaticContent
from xmodule.exceptions import NotFoundError
from xmodule.modulestore import LIBRARY_ROOT, EdxJSONEncoder, ModuleStoreEnum
from xmodule.modulestore.draft_and_published import DIRECT_ONLY_CATEGORIES
from xmodule.modulestore.inheritance import own_metadata
from xmodule.modulestore.store_utilities import draft_node_constructor, get_draft_subtree_roots

DRAFT_DIR = "drafts"
PUBLISHED_DIR = "published"

DEFAULT_CONTENT_FIELDS = ['metadata', 'data']


def _export_drafts(modulestore, course_key, export_fs, xml_centric_course_key):
    """
    Exports course drafts.
    """
    # NOTE: we need to explicitly implement the logic for setting the vertical's parent
    # and index here since the XML modulestore cannot load draft modules
    with modulestore.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
        draft_blocks = modulestore.get_items(
            course_key,
            qualifiers={'category': {'$nin': DIRECT_ONLY_CATEGORIES}},
            revision=ModuleStoreEnum.RevisionOption.draft_only
        )
        # Check to see if the returned draft blocks have changes w.r.t. the published block.
        # Only blocks with changes will be exported into the /drafts directory.
        draft_blocks = [block for block in draft_blocks if modulestore.has_changes(block)]
        if draft_blocks:
            draft_course_dir = export_fs.makedir(DRAFT_DIR, recreate=True)

            # accumulate tuples of draft_blocks and their parents in
            # this list:
            draft_node_list = []

            for draft_block in draft_blocks:
                parent_loc = modulestore.get_parent_location(
                    draft_block.location,
                    revision=ModuleStoreEnum.RevisionOption.draft_preferred
                )

                # if module has no parent, set its parent_url to `None`
                parent_url = None
                if parent_loc is not None:
                    parent_url = str(parent_loc)

                draft_node = draft_node_constructor(
                    draft_block,
                    location=draft_block.location,
                    url=str(draft_block.location),
                    parent_location=parent_loc,
                    parent_url=parent_url,
                )

                draft_node_list.append(draft_node)

            for draft_node in get_draft_subtree_roots(draft_node_list):
                # only export the roots of the draft subtrees
                # since export_from_xml (called by `add_xml_to_node`)
                # exports a whole tree

                # ensure module has "xml_attributes" attr
                if not hasattr(draft_node.module, 'xml_attributes'):
                    draft_node.module.xml_attributes = {}

                # Don't try to export orphaned items
                # and their descendents
                if draft_node.parent_location is None:
                    continue

                logging.debug('parent_loc = %s', draft_node.parent_location)
                draft_node.module.xml_attributes['parent_url'] = draft_node.parent_url
                parent = modulestore.get_item(draft_node.parent_location)

                # Don't try to export orphaned items
                if draft_node.module.location not in parent.children:
                    continue
                index = parent.children.index(draft_node.module.location)
                draft_node.module.xml_attributes['index_in_children_list'] = str(index)

                draft_node.module.runtime.export_fs = draft_course_dir
                adapt_references(draft_node.module, xml_centric_course_key, draft_course_dir)
                node = lxml.etree.Element('unknown')

                draft_node.module.add_xml_to_node(node)


class ExportManager:
    """
    Manages XML exporting for courselike objects.
    """
    def __init__(self, modulestore, contentstore, courselike_key, root_dir, target_dir):
        """
        Export all blocks from `modulestore` and content from `contentstore` as xml to `root_dir`.

        `modulestore`: A `ModuleStore` object that is the source of the blocks to export
        `contentstore`: A `ContentStore` object that is the source of the content to export, can be None
        `courselike_key`: The Locator of the block to export
        `root_dir`: The directory to write the exported xml to
        `target_dir`: The name of the directory inside `root_dir` to write the content to
        """
        self.modulestore = modulestore
        self.contentstore = contentstore
        self.courselike_key = courselike_key
        self.root_dir = root_dir
        self.target_dir = str(target_dir)

    @abstractmethod
    def get_key(self):
        """
        Get the courselike locator key
        """
        raise NotImplementedError

    def process_root(self, root, export_fs):
        """
        Perform any additional tasks to the root XML node.
        """

    def process_extra(self, root, courselike, root_courselike_dir, xml_centric_courselike_key, export_fs):
        """
        Process additional content, like static assets.
        """

    def post_process(self, root, export_fs):
        """
        Perform any final processing after the other export tasks are done.
        """

    @abstractmethod
    def get_courselike(self):
        """
        Get the target courselike object for this export.
        """

    def export(self):
        """
        Perform the export given the parameters handed to this class at init.
        """
        with self.modulestore.bulk_operations(self.courselike_key):

            fsm = OSFS(self.root_dir)
            root = lxml.etree.Element('unknown')

            # export only the published content
            with self.modulestore.branch_setting(ModuleStoreEnum.Branch.published_only, self.courselike_key):
                courselike = self.get_courselike()
                export_fs = courselike.runtime.export_fs = fsm.makedir(self.target_dir, recreate=True)

                # change all of the references inside the course to use the xml expected key type w/o version & branch
                xml_centric_courselike_key = self.get_key()
                adapt_references(courselike, xml_centric_courselike_key, export_fs)
                root.set('url_name', self.courselike_key.run)
                courselike.add_xml_to_node(root)

            # Make any needed adjustments to the root node.
            self.process_root(root, export_fs)

            # Process extra items-- drafts, assets, etc
            root_courselike_dir = self.root_dir + '/' + self.target_dir
            self.process_extra(root, courselike, root_courselike_dir, xml_centric_courselike_key, export_fs)

            # Any last pass adjustments
            self.post_process(root, export_fs)


class CourseExportManager(ExportManager):
    """
    Export manager for courses.
    """
    def get_key(self):
        return CourseLocator(
            self.courselike_key.org, self.courselike_key.course, self.courselike_key.run, deprecated=True
        )

    def get_courselike(self):
        # depth = None: Traverses down the entire course structure.
        # lazy = False: Loads and caches all block definitions during traversal for fast access later
        #               -and- to eliminate many round-trips to read individual definitions.
        # Why these parameters? Because a course export needs to access all the course block information
        # eventually. Accessing it all now at the beginning increases performance of the export.
        return self.modulestore.get_course(self.courselike_key, depth=None, lazy=False)

    def process_root(self, root, export_fs):
        with export_fs.open('course.xml', 'wb') as course_xml:
            lxml.etree.ElementTree(root).write(course_xml, encoding='utf-8')

    def process_extra(self, root, courselike, root_courselike_dir, xml_centric_courselike_key, export_fs):
        # Export the modulestore's asset metadata.
        set_custom_attribute("export_asset_started", str(courselike))
        asset_dir = root_courselike_dir + '/' + AssetMetadata.EXPORTED_ASSET_DIR + '/'
        if not os.path.isdir(asset_dir):
            os.makedirs(asset_dir)
        asset_root = lxml.etree.Element(AssetMetadata.ALL_ASSETS_XML_TAG)
        course_assets = self.modulestore.get_all_asset_metadata(self.courselike_key, None)
        for asset_md in course_assets:
            # All asset types are exported using the "asset" tag - but their asset type is specified in each asset key.
            asset = lxml.etree.SubElement(asset_root, AssetMetadata.ASSET_XML_TAG)
            asset_md.to_xml(asset)
        with OSFS(asset_dir).open(AssetMetadata.EXPORTED_ASSET_FILENAME, 'wb') as asset_xml_file:
            lxml.etree.ElementTree(asset_root).write(asset_xml_file, encoding='utf-8')

        # export the static assets
        set_custom_attribute("export_static_assets_started", str(courselike))
        policies_dir = export_fs.makedir('policies', recreate=True)
        if self.contentstore:
            self.contentstore.export_all_for_course(
                self.courselike_key,
                root_courselike_dir + '/static/',
                root_courselike_dir + '/policies/assets.json',
            )

            # If we are using the default course image, export it to the
            # legacy location to support backwards compatibility.
            if courselike.course_image == courselike.fields['course_image'].default:
                try:
                    course_image = self.contentstore.find(
                        StaticContent.compute_location(
                            courselike.id,
                            courselike.course_image
                        ),
                    )
                except NotFoundError:
                    pass
                else:
                    output_dir = root_courselike_dir + '/static/images/'
                    if not os.path.isdir(output_dir):
                        os.makedirs(output_dir)
                    with OSFS(output_dir).open('course_image.jpg', 'wb') as course_image_file:
                        course_image_file.write(course_image.data)

        # export the static tabs
        set_custom_attribute("export_tabs_started", str(courselike))
        export_extra_content(
            export_fs, self.modulestore, self.courselike_key, xml_centric_courselike_key,
            'static_tab', 'tabs', '.html'
        )

        # export the custom tags
        set_custom_attribute("export_custom_tags_started", str(courselike))
        export_extra_content(
            export_fs, self.modulestore, self.courselike_key, xml_centric_courselike_key,
            'custom_tag_template', 'custom_tags'
        )

        # export the course updates
        set_custom_attribute("export_course_updates_started", str(courselike))
        export_extra_content(
            export_fs, self.modulestore, self.courselike_key, xml_centric_courselike_key,
            'course_info', 'info', '.html'
        )

        # export the 'about' data (e.g. overview, etc.)
        set_custom_attribute("export_about_started", str(courselike))
        export_extra_content(
            export_fs, self.modulestore, self.courselike_key, xml_centric_courselike_key,
            'about', 'about', '.html'
        )

        course_policy_dir_name = courselike.location.run
        course_run_policy_dir = policies_dir.makedir(course_policy_dir_name, recreate=True)

        # export the grading policy
        with course_run_policy_dir.open('grading_policy.json', 'wb') as grading_policy:
            grading_policy.write(dumps(courselike.grading_policy, cls=EdxJSONEncoder,
                                       sort_keys=True, indent=4).encode('utf-8'))

        # export all of the course metadata in policy.json
        set_custom_attribute("export_policy_started", str(courselike))
        with course_run_policy_dir.open('policy.json', 'wb') as course_policy:
            policy = {'course/' + courselike.location.run: own_metadata(courselike)}
            course_policy.write(dumps(policy, cls=EdxJSONEncoder, sort_keys=True, indent=4).encode('utf-8'))

        set_custom_attribute("export_drafts_started", str(courselike))
        _export_drafts(self.modulestore, self.courselike_key, export_fs, xml_centric_courselike_key)

        courselike_key_str = str(self.courselike_key)
        block_id_pattern = f"{courselike_key_str.replace('course-v1:', 'block-v1:', 1)}*"

        tags_count = get_object_tag_counts(block_id_pattern)
        course_tags_count = get_object_tag_counts(courselike_key_str)

        if tags_count or course_tags_count:
            export_tags_in_csv_file(courselike_key_str, export_fs, 'tags.csv')


class LibraryExportManager(ExportManager):
    """
    Export manager for Libraries
    """
    def get_key(self):
        """
        Get the library locator for the current library key.
        """
        return LibraryLocator(
            self.courselike_key.org, self.courselike_key.library
        )

    def get_courselike(self):
        """
        Get the library from the modulestore.
        """
        return self.modulestore.get_library(self.courselike_key, depth=None, lazy=False)

    def process_root(self, root, export_fs):
        """
        Add extra attributes to the root XML file.
        """
        root.set('org', self.courselike_key.org)
        root.set('library', self.courselike_key.library)

    def process_extra(self, root, courselike, root_courselike_dir, xml_centric_courselike_key, export_fs):
        """
        Notionally, libraries may have assets. This is currently unsupported, but the structure is here
        to ease in duck typing during import. This may be expanded as a useful feature eventually.
        """
        # export the static assets
        export_fs.makedir('policies', recreate=True)

        if self.contentstore:
            self.contentstore.export_all_for_course(
                self.courselike_key,
                self.root_dir + '/' + self.target_dir + '/static/',
                self.root_dir + '/' + self.target_dir + '/policies/assets.json',
            )

    def post_process(self, root, export_fs):
        """
        Because Libraries are XBlocks, they aren't exported in the same way Course Blocks
        are, but instead use the standard XBlock serializers. Accordingly, we need to
        create our own index file to act as the equivalent to the root course.xml file,
        called library.xml.
        """
        # Create the Library.xml file, which acts as the index of all library contents.
        xml_file = export_fs.open(LIBRARY_ROOT, 'wb')
        xml_file.write(lxml.etree.tostring(root, pretty_print=True, encoding='utf-8'))
        xml_file.close()


def export_course_to_xml(modulestore, contentstore, course_key, root_dir, course_dir):
    """
    Thin wrapper for the Course Export Manager. See ExportManager for details.
    """
    CourseExportManager(modulestore, contentstore, course_key, root_dir, course_dir).export()


def export_library_to_xml(modulestore, contentstore, library_key, root_dir, library_dir):
    """
    Thin wrapper for the Library Export Manager. See ExportManager for details.
    """
    LibraryExportManager(modulestore, contentstore, library_key, root_dir, library_dir).export()


def adapt_references(subtree, destination_course_key, export_fs):
    """
    Map every reference in the subtree into destination_course_key and set it back into the xblock fields
    """
    subtree.runtime.export_fs = export_fs  # ensure everything knows where it's going!
    for field_name, field in subtree.fields.items():
        if field.is_set_on(subtree):
            if isinstance(field, Reference):
                value = field.read_from(subtree)
                if value is not None:
                    field.write_to(subtree, field.read_from(subtree).map_into_course(destination_course_key))
            elif field_name == 'children':
                # don't change the children field but do recurse over the children
                [adapt_references(child, destination_course_key, export_fs) for child in subtree.get_children()]  # pylint: disable=expression-not-assigned
            elif isinstance(field, ReferenceList):
                field.write_to(
                    subtree,
                    [ele.map_into_course(destination_course_key) for ele in field.read_from(subtree)]
                )
            elif isinstance(field, ReferenceValueDict):
                field.write_to(
                    subtree, {
                        key: ele.map_into_course(destination_course_key) for key, ele in field.read_from(subtree).items()  # pylint: disable=line-too-long
                    }
                )


def _export_field_content(xblock_item, item_dir):
    """
    Export all fields related to 'xblock_item' other than 'metadata' and 'data' to json file in provided directory
    """
    block_data = xblock_item.get_explicitly_set_fields_by_scope(Scope.content)
    if isinstance(block_data, dict):
        for field_name in block_data:
            if field_name not in DEFAULT_CONTENT_FIELDS:
                # filename format: {dirname}.{field_name}.json
                with item_dir.open('{}.{}.{}'.format(xblock_item.location.block_id, field_name, 'json'),
                                   'wb') as field_content_file:
                    field_content_file.write(dumps(block_data.get(field_name, {}), cls=EdxJSONEncoder,
                                                   sort_keys=True, indent=4).encode('utf-8'))


def export_extra_content(export_fs, modulestore, source_course_key, dest_course_key, category_type, dirname, file_suffix=''):  # pylint: disable=line-too-long, missing-function-docstring
    items = modulestore.get_items(source_course_key, qualifiers={'category': category_type})

    if len(items) > 0:
        item_dir = export_fs.makedir(dirname, recreate=True)
        for item in items:
            adapt_references(item, dest_course_key, export_fs)
            with item_dir.open(item.location.block_id + file_suffix, 'wb') as item_file:
                item_file.write(item.data.encode('utf8'))

                # export content fields other then metadata and data in json format in current directory
                _export_field_content(item, item_dir)
