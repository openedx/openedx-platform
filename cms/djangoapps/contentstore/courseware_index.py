""" Code to allow module store to interface with courseware index """

import logging
import re
from abc import ABCMeta, abstractmethod
from datetime import timedelta

from django.conf import settings
from django.urls import resolve
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from eventtracking import tracker
from search.search_engine_base import SearchEngine

from cms.djangoapps.contentstore.course_group_config import GroupConfiguration
from common.djangoapps.course_modes.models import CourseMode
from openedx.core.lib.courses import course_image_url, course_organization_image_url
from xmodule.annotator_mixin import html_to_text  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.library_tools import normalize_key_for_search  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order

# REINDEX_AGE is the default amount of time that we look back for changes
# that might have happened. If we are provided with a time at which the
# indexing is triggered, then we know it is safe to only index items
# recently changed at that time. This is the time period that represents
# how far back from the trigger point to look back in order to index
REINDEX_AGE = timedelta(0, 60)  # 60 seconds

# INDEXING_REQUEST_TIMEOUT is the number of seconds before a request is considered
# timed out for courseware indexing.
INDEXING_REQUEST_TIMEOUT = 60

log = logging.getLogger('edx.modulestore')


def strip_html_content_to_text(html_content):
    """ Gets only the textual part for html content - useful for building text to be searched """
    # Removing HTML-encoded non-breaking space characters
    text_content = re.sub(r"(\s|&nbsp;|//)+", " ", html_to_text(html_content))
    # Removing HTML CDATA
    text_content = re.sub(r"<!\[CDATA\[.*\]\]>", "", text_content)
    # Removing HTML comments
    text_content = re.sub(r"<!--.*-->", "", text_content)

    return text_content


def indexing_is_enabled():
    """
    Checks to see if the indexing feature is enabled
    """
    return settings.FEATURES.get('ENABLE_COURSEWARE_INDEX', False)


class SearchIndexingError(Exception):
    """ Indicates some error(s) occured during indexing """

    def __init__(self, message, error_list):
        super().__init__(message)
        self.error_list = error_list


class SearchIndexerBase(metaclass=ABCMeta):
    """
    Base class to perform indexing for courseware or library search from different modulestores
    """

    INDEX_NAME = None
    ENABLE_INDEXING_KEY = None

    INDEX_EVENT = {
        'name': None,
        'category': None
    }

    @classmethod
    def indexing_is_enabled(cls):
        """
        Checks to see if the indexing feature is enabled
        """
        return settings.FEATURES.get(cls.ENABLE_INDEXING_KEY, False)

    @classmethod
    @abstractmethod
    def normalize_structure_key(cls, structure_key):
        """ Normalizes structure key for use in indexing """

    @classmethod
    @abstractmethod
    def _fetch_top_level(cls, modulestore, structure_key):
        """ Fetch the item from the modulestore location """

    @classmethod
    @abstractmethod
    def _get_location_info(cls, normalized_structure_key):
        """ Builds location info dictionary """

    @classmethod
    def _id_modifier(cls, usage_id):
        """ Modifies usage_id to submit to index """
        return usage_id

    @classmethod
    def remove_deleted_items(cls, searcher, structure_key, exclude_items):
        """
        remove any item that is present in the search index that is not present in updated list of indexed items
        as we find items we can shorten the set of items to keep
        """
        response = searcher.search(
            field_dictionary=cls._get_location_info(structure_key),
            exclude_dictionary={"id": list(exclude_items)}
        )
        result_ids = [result["data"]["id"] for result in response["results"]]
        searcher.remove(result_ids)

    @classmethod
    def index(cls, modulestore, structure_key, triggered_at=None, reindex_age=REINDEX_AGE, timeout=INDEXING_REQUEST_TIMEOUT):  # lint-amnesty, pylint: disable=line-too-long, too-many-statements
        """
        Process course for indexing

        Arguments:
        modulestore - modulestore object to use for operations

        structure_key (CourseKey|LibraryKey) - course or library identifier

        triggered_at (datetime) - provides time at which indexing was triggered;
            useful for index updates - only things changed recently from that date
            (within REINDEX_AGE above ^^) will have their index updated, others skip
            updating their index but are still walked through in order to identify
            which items may need to be removed from the index
            If None, then a full reindex takes place

        Returns:
        Number of items that have been added to the index
        """
        error_list = []
        searcher = SearchEngine.get_search_engine(cls.INDEX_NAME)
        if not searcher:
            return

        structure_key = cls.normalize_structure_key(structure_key)
        location_info = cls._get_location_info(structure_key)

        # Wrap counter in dictionary - otherwise we seem to lose scope inside the embedded function `prepare_item_index`
        indexed_count = {
            "count": 0
        }

        # indexed_items is a list of all the items that we wish to remain in the
        # index, whether or not we are planning to actually update their index.
        # This is used in order to build a query to remove those items not in this
        # list - those are ready to be destroyed
        indexed_items = set()

        # items_index is a list of all the items index dictionaries.
        # it is used to collect all indexes and index them using bulk API,
        # instead of per item index API call.
        items_index = []

        def get_item_location(item):
            """
            Gets the version agnostic item location
            """
            return item.location.version_agnostic().replace(branch=None)

        def prepare_item_index(item, skip_index=False, groups_usage_info=None):
            """
            Add this item to the items_index and indexed_items list

            Arguments:
            item - item to add to index, its children will be processed recursively

            skip_index - simply walk the children in the tree, the content change is
                older than the REINDEX_AGE window and would have been already indexed.
                This should really only be passed from the recursive child calls when
                this method has determined that it is safe to do so

            Returns:
            item_content_groups - content groups assigned to indexed item
            """
            item_index_dictionary = item.index_dictionary()
            # if it's not indexable and it does not have children, then ignore
            if not item_index_dictionary and not item.has_children:
                return

            item_content_groups = None

            if item.category == "split_test":  # lint-amnesty, pylint: disable=too-many-nested-blocks
                split_partition = item.get_selected_partition()
                # Only process split_test groups if groups_usage_info is available
                if groups_usage_info is not None:
                    for split_test_child in item.get_children():
                        if split_partition:
                            for group in split_partition.groups:
                                group_id = str(group.id)
                                child_location = item.group_id_to_child.get(group_id, None)
                                if child_location == split_test_child.location:
                                    groups_usage_info.update({
                                        str(get_item_location(split_test_child)): [group_id],
                                    })
                                    for component in split_test_child.get_children():
                                        groups_usage_info.update({
                                            str(get_item_location(component)): [group_id]
                                        })

            if groups_usage_info:
                item_location = get_item_location(item)
                item_content_groups = groups_usage_info.get(str(item_location), None)

            item_id = str(cls._id_modifier(item.scope_ids.usage_id))
            indexed_items.add(item_id)
            if item.has_children:
                # determine if it's okay to skip adding the children herein based upon how recently any may have changed
                skip_child_index = skip_index or \
                    (triggered_at is not None and (triggered_at - item.subtree_edited_on) > reindex_age)
                children_groups_usage = []
                for child_item in item.get_children():
                    if modulestore.has_published_version(child_item):
                        children_groups_usage.append(
                            prepare_item_index(
                                child_item,
                                skip_index=skip_child_index,
                                groups_usage_info=groups_usage_info
                            )
                        )
                if None in children_groups_usage:
                    item_content_groups = None

            if skip_index or not item_index_dictionary:
                return

            item_index = {}
            # if it has something to add to the index, then add it
            try:
                item_index.update(location_info)
                item_index.update(item_index_dictionary)
                item_index['id'] = item_id
                if item.start:
                    item_index['start_date'] = item.start
                item_index['content_groups'] = item_content_groups if item_content_groups else None
                item_index.update(cls.supplemental_fields(item))
                items_index.append(item_index)
                indexed_count["count"] += 1
                return item_content_groups
            except Exception as err:  # pylint: disable=broad-except
                # broad exception so that index operation does not fail on one item of many
                log.warning('Could not index item: %s - %r', item.location, err)
                error_list.append(_('Could not index item: {}').format(item.location))

        try:
            with modulestore.branch_setting(ModuleStoreEnum.RevisionOption.published_only):
                structure = cls._fetch_top_level(modulestore, structure_key)
                if structure is None:
                    log.warning(
                        "Course structure not found for indexing %s - skipping index",
                        structure_key
                    )
                    return 0
                try:
                    groups_usage_info = cls.fetch_group_usage(modulestore, structure)
                except Exception as group_err:  # pylint: disable=broad-except
                    log.warning(
                        "Could not fetch group usage info for course %s - continuing without groups: %r",
                        structure_key,
                        group_err
                    )
                    groups_usage_info = None

                # First perform any additional indexing from the structure object
                # Wrap in try-except to handle demo courses that might have missing metadata
                try:
                    cls.supplemental_index_information(modulestore, structure)
                except Exception as supp_err:  # pylint: disable=broad-except
                    log.warning(
                        "Supplemental indexing failed for %s - continuing with content indexing: %r",
                        structure_key,
                        supp_err
                    )

                # Now index the content
                # Handle cases where get_children() might fail or return None
                try:
                    children = structure.get_children()
                    if children is not None:
                        for item in children:
                            prepare_item_index(item, groups_usage_info=groups_usage_info)
                except Exception as children_err:  # pylint: disable=broad-except
                    log.warning(
                        "Error getting children for course %s - some content may not be indexed: %r",
                        structure_key,
                        children_err
                    )

                if items_index:
                    searcher.index(items_index, request_timeout=timeout)
                cls.remove_deleted_items(searcher, structure_key, indexed_items)
        except Exception as err:  # pylint: disable=broad-except
            # broad exception so that index operation does not prevent the rest of the application from working
            log.exception(
                "Indexing error encountered, courseware index may be out of date %s - %r",
                structure_key,
                err
            )
            error_list.append(_('General indexing error occurred'))

        if error_list:
            raise SearchIndexingError('Error(s) present during indexing', error_list)

        return indexed_count["count"]

    @classmethod
    def _do_reindex(cls, modulestore, structure_key):
        """
        (Re)index all content within the given structure (course or library),
        tracking the fact that a full reindex has taken place
        """
        indexed_count = cls.index(modulestore, structure_key, timeout=180)
        if indexed_count:
            cls._track_index_request(cls.INDEX_EVENT['name'], cls.INDEX_EVENT['category'], indexed_count)
        return indexed_count

    @classmethod
    def _track_index_request(cls, event_name, category, indexed_count):
        """Track content index requests.

        Arguments:
            event_name (str):  Name of the event to be logged.
            category (str): category of indexed items
            indexed_count (int): number of indexed items
        Returns:
            None

        """
        data = {
            "indexed_count": indexed_count,
            'category': category,
        }

        tracker.emit(
            event_name,
            data
        )

    @classmethod
    def fetch_group_usage(cls, modulestore, structure):  # pylint: disable=unused-argument
        """
        Base implementation of fetch group usage on course/library.
        """
        return None

    @classmethod
    def supplemental_index_information(cls, modulestore, structure):
        """
        Perform any supplemental indexing given that the structure object has
        already been loaded. Base implementation performs no operation.

        Arguments:
            modulestore - modulestore object used during the indexing operation
            structure - structure object loaded during the indexing job

        Returns:
            None
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @classmethod
    def supplemental_fields(cls, item):  # pylint: disable=unused-argument
        """
        Any supplemental fields that get added to the index for the specified
        item. Base implementation returns an empty dictionary
        """
        return {}


class CoursewareSearchIndexer(SearchIndexerBase):
    """
    Class to perform indexing for courseware search from different modulestores
    """
    INDEX_NAME = "courseware_content"
    ENABLE_INDEXING_KEY = 'ENABLE_COURSEWARE_INDEX'

    INDEX_EVENT = {
        'name': 'edx.course.index.reindexed',
        'category': 'courseware_index'
    }

    UNNAMED_MODULE_NAME = gettext_lazy("(Unnamed)")

    @classmethod
    def normalize_structure_key(cls, structure_key):
        """ Normalizes structure key for use in indexing """
        return structure_key

    @classmethod
    def _fetch_top_level(cls, modulestore, structure_key):
        """ Fetch the item from the modulestore location """
        return modulestore.get_course(structure_key, depth=None)

    @classmethod
    def _get_location_info(cls, normalized_structure_key):
        """ Builds location info dictionary """
        return {"course": str(normalized_structure_key), "org": normalized_structure_key.org}

    @classmethod
    def do_course_reindex(cls, modulestore, course_key):
        """
        (Re)index all content within the given course, tracking the fact that a full reindex has taken place
        """
        return cls._do_reindex(modulestore, course_key)

    @classmethod
    def _do_reindex(cls, modulestore, structure_key):
        """
        (Re)index course content within the given structure.

        The course_info index is indexed with the courseware_content index. This method
        helps to track the fact that course_info reindex has taken place.
        """
        indexed_count = super()._do_reindex(modulestore, structure_key)
        if indexed_count:
            course_about = CourseAboutSearchIndexer
            cls._track_index_request(
                course_about.INDEX_EVENT['name'],
                course_about.INDEX_EVENT['category'],
                indexed_count
            )
        return indexed_count

    @classmethod
    def fetch_group_usage(cls, modulestore, structure):
        groups_usage_dict = {}
        partitions_info = GroupConfiguration.get_partitions_usage_info(modulestore, structure)
        content_group_info = GroupConfiguration.get_content_groups_items_usage_info(
            modulestore,
            structure
        )
        for group_info in (partitions_info, content_group_info):
            for groups in group_info.values():
                for name, group in groups.items():
                    for module in group:
                        view, args, kwargs = resolve(module['url'])  # pylint: disable=unused-variable
                        usage_key_string = str(kwargs['usage_key_string'])
                        if groups_usage_dict.get(usage_key_string, None):
                            groups_usage_dict[usage_key_string].append(name)
                        else:
                            groups_usage_dict[usage_key_string] = [name]
        return groups_usage_dict

    @classmethod
    def supplemental_index_information(cls, modulestore, structure):
        """
        Perform additional indexing from loaded structure object
        """
        CourseAboutSearchIndexer.index_about_information(modulestore, structure)

    @classmethod
    def supplemental_fields(cls, item):
        """
        Add location path to the item object

        Once we've established the path of names, the first name is the course
        name, and the next 3 names are the navigable path within the edx
        application. Notice that we stop at that level because a full path to
        deep children would be confusing.
        """
        location_path = []
        parent = item
        while parent is not None:
            path_component_name = parent.display_name
            if not path_component_name:
                path_component_name = str(cls.UNNAMED_MODULE_NAME)
            location_path.append(path_component_name)
            parent = parent.get_parent()
        location_path.reverse()
        return {
            "course_name": location_path[0],
            "location": location_path[1:4]
        }


class LibrarySearchIndexer(SearchIndexerBase):
    """
    Base class to perform indexing for library search from different modulestores
    """
    INDEX_NAME = "library_index"
    ENABLE_INDEXING_KEY = 'ENABLE_LIBRARY_INDEX'

    INDEX_EVENT = {
        'name': 'edx.library.index.reindexed',
        'category': 'library_index'
    }

    @classmethod
    def normalize_structure_key(cls, structure_key):
        """ Normalizes structure key for use in indexing """
        return normalize_key_for_search(structure_key)

    @classmethod
    def _fetch_top_level(cls, modulestore, structure_key):
        """ Fetch the item from the modulestore location """
        return modulestore.get_library(structure_key, depth=None)

    @classmethod
    def _get_location_info(cls, normalized_structure_key):
        """ Builds location info dictionary """
        return {"library": str(normalized_structure_key)}

    @classmethod
    def _id_modifier(cls, usage_id):
        """ Modifies usage_id to submit to index """
        return usage_id.replace(library_key=(usage_id.library_key.replace(version_guid=None, branch=None)))

    @classmethod
    def do_library_reindex(cls, modulestore, library_key):
        """
        (Re)index all content within the given library, tracking the fact that a full reindex has taken place
        """
        return cls._do_reindex(modulestore, library_key)


class AboutInfo:
    """ About info structure to contain
       1) Property name to use
       2) Where to add in the index (using flags above)
       3) Where to source the properties value
    """
    # Bitwise Flags for where to index the information
    #
    # ANALYSE - states that the property text contains content that we wish to be able to find matched within
    #   e.g. "joe" should yield a result for "I'd like to drink a cup of joe"
    #
    # PROPERTY - states that the property text should be a property of the indexed document, to be returned with the
    # results: search matches will only be made on exact string matches
    #   e.g. "joe" will only match on "joe"
    #
    # We are using bitwise flags because one may want to add the property to EITHER or BOTH parts of the index
    #   e.g. university name is desired to be analysed, so that a search on "Oxford" will match
    #   property values "University of Oxford" and "Oxford Brookes University",
    #   but it is also a useful property, because within a (future) filtered search a user
    #   may have chosen to filter courses from "University of Oxford"
    #
    # see https://wiki.python.org/moin/BitwiseOperators for information about bitwise shift operator used below
    #
    ANALYSE = 1 << 0  # Add the information to the analysed content of the index
    PROPERTY = 1 << 1  # Add the information as a property of the object being indexed (not analysed)

    def __init__(self, property_name, index_flags, source_from):
        self.property_name = property_name
        self.index_flags = index_flags
        self.source_from = source_from

    def get_value(self, **kwargs):
        """ get the value for this piece of information, using the correct source """
        return self.source_from(self, **kwargs)

    def from_about_dictionary(self, **kwargs):
        """ gets the value from the kwargs provided 'about_dictionary' """
        about_dictionary = kwargs.get('about_dictionary', None)
        if not about_dictionary:
            raise ValueError("Context dictionary does not contain expected argument 'about_dictionary'")

        return about_dictionary.get(self.property_name, None)

    def from_course_property(self, **kwargs):
        """ gets the value from the kwargs provided 'course' """
        course = kwargs.get('course', None)
        if not course:
            raise ValueError("Context dictionary does not contain expected argument 'course'")

        return getattr(course, self.property_name, None)

    def from_course_mode(self, **kwargs):
        """ fetches the available course modes from the CourseMode model """
        course = kwargs.get('course', None)
        if not course:
            raise ValueError("Context dictionary does not contain expected argument 'course'")

        return [mode.slug for mode in CourseMode.modes_for_course(course.id)]

    # Source location options - either from the course or the about info
    FROM_ABOUT_INFO = from_about_dictionary
    FROM_COURSE_PROPERTY = from_course_property
    FROM_COURSE_MODE = from_course_mode


class CourseAboutSearchIndexer(CoursewareSearchIndexer):
    """
    Class to perform indexing of about information from course object
    """
    INDEX_NAME = "course_info"

    INDEX_EVENT = {
        'name': 'edx.course_info.index.reindexed',
        'category': 'course_info'
    }

    # List of properties to add to the index - each item in the list is an instance of AboutInfo object
    ABOUT_INFORMATION_TO_INCLUDE = [
        AboutInfo("advertised_start", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("announcement", AboutInfo.PROPERTY, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("start", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("end", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("effort", AboutInfo.PROPERTY, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("display_name", AboutInfo.ANALYSE, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("overview", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("title", AboutInfo.ANALYSE | AboutInfo.PROPERTY, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("university", AboutInfo.ANALYSE | AboutInfo.PROPERTY, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("number", AboutInfo.ANALYSE | AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("short_description", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("description", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("key_dates", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("video", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("course_staff_short", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("course_staff_extended", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("requirements", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("syllabus", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("textbook", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("faq", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("more_info", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("ocw_links", AboutInfo.ANALYSE, AboutInfo.FROM_ABOUT_INFO),
        AboutInfo("enrollment_start", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("enrollment_end", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("org", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("modes", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_MODE),
        AboutInfo("language", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("invitation_only", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
        AboutInfo("catalog_visibility", AboutInfo.PROPERTY, AboutInfo.FROM_COURSE_PROPERTY),
    ]

    @classmethod
    def index_about_information(cls, modulestore, course):
        """
        Add the given course to the course discovery index

        Arguments:
        modulestore - modulestore object to use for operations

        course - course object from which to take properties, locate about information
        """
        searcher = SearchEngine.get_search_engine(cls.INDEX_NAME)
        if not searcher:
            return

        course_id = str(course.id)
        course_info = {
            'id': course_id,
            'course': course_id,
            'content': {},
            'image_url': course_image_url(course),
            'org_image_url': course_organization_image_url(course),
        }

        # load data for all of the 'about' blocks for this course into a dictionary
        # Handle demo courses that might not have about blocks yet
        try:
            about_items = modulestore.get_items(course.id, qualifiers={"category": "about"})
            about_dictionary = {
                item.location.block_id: item.data
                for item in about_items
            }
        except Exception as about_err:  # pylint: disable=broad-except
            # Demo courses may not have about blocks initialized yet
            log.warning(
                "Could not load about blocks for course %s - continuing with empty about dictionary: %r",
                course_id,
                about_err
            )
            about_dictionary = {}

        about_context = {
            "course": course,
            "about_dictionary": about_dictionary,
        }

        for about_information in cls.ABOUT_INFORMATION_TO_INCLUDE:
            # Broad exception handler so that a single bad property does not scupper the collection of others
            try:
                section_content = about_information.get_value(**about_context)
            except:  # pylint: disable=bare-except
                section_content = None
                log.warning(
                    "Course discovery could not collect property %s for course %s",
                    about_information.property_name,
                    course_id,
                    exc_info=True,
                )

            if section_content:
                if about_information.index_flags & AboutInfo.ANALYSE:
                    analyse_content = section_content
                    if isinstance(section_content, str):
                        analyse_content = strip_html_content_to_text(section_content)
                    course_info['content'][about_information.property_name] = analyse_content
                if about_information.index_flags & AboutInfo.PROPERTY:
                    course_info[about_information.property_name] = section_content

        # Broad exception handler to protect around and report problems with indexing
        try:
            searcher.index([course_info])
        except:
            log.exception(
                "Course discovery indexing error encountered, course discovery index may be out of date %s",
                course_id,
            )
            raise

        log.debug(
            "Successfully added %s course to the course discovery index",
            course_id
        )

    @classmethod
    def _get_location_info(cls, normalized_structure_key):
        """ Builds location info dictionary """
        return {"course": str(normalized_structure_key), "org": normalized_structure_key.org}

    @classmethod
    def remove_deleted_items(cls, structure_key):  # lint-amnesty, pylint: disable=arguments-differ
        """ Remove item from Course About Search_index """
        searcher = SearchEngine.get_search_engine(cls.INDEX_NAME)
        if not searcher:
            return

        response = searcher.search(field_dictionary=cls._get_location_info(structure_key))
        result_ids = [result["data"]["id"] for result in response["results"]]
        searcher.remove(result_ids)
