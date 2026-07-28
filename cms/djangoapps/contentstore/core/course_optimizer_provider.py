"""
Logic for handling actions in Studio related to Course Optimizer.
"""
import json

from opaque_keys.edx.keys import CourseKey
from user_tasks.conf import settings as user_tasks_settings
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from cms.djangoapps.contentstore.tasks import (
    CourseLinkCheckTask,
    CourseLinkUpdateTask,
    LinkState,
    extract_content_URLs_from_course,
)
from cms.djangoapps.contentstore.utils import create_course_info_usage_key, get_previous_run_course_key
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import get_xblock
from cms.djangoapps.contentstore.xblock_storage_handlers.xblock_helpers import usage_key_with_run
from openedx.core.lib.xblock_utils import get_course_update_items
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.tabs import StaticTab

# Restricts status in the REST API to only those which the requesting user has permission to view.
#   These can be overwritten in django settings.
#   By default, these should be the UserTaskStatus statuses:
#   'Pending', 'In Progress', 'Succeeded', 'Failed', 'Canceled', 'Retrying'
STATUS_FILTERS = user_tasks_settings.USER_TASKS_STATUS_FILTERS


def get_link_check_data(request, course_id):
    """
    Retrives data and formats it for the link check get request.
    """
    course_key = CourseKey.from_string(course_id)
    task_status = _latest_task_status(request, course_id)
    status = None
    created_at = None
    broken_links_dto = None
    error = None
    if task_status is None:
        # The task hasn't been initialized yet; did we store info in the session already?
        try:
            session_status = request.session['link_check_status']
            status = session_status[course_id]
        except KeyError:
            status = 'Uninitiated'
    else:
        status = task_status.state
        created_at = task_status.created
        if task_status.state == UserTaskStatus.SUCCEEDED:
            artifact = UserTaskArtifact.objects.get(status=task_status, name='BrokenLinks')
            with artifact.file as file:
                content = file.read()
                json_content = json.loads(content)
                broken_links_dto = generate_broken_links_descriptor(json_content, request.user, course_key)
        elif task_status.state in (UserTaskStatus.FAILED, UserTaskStatus.CANCELED):
            errors = UserTaskArtifact.objects.filter(status=task_status, name='Error')
            if errors:
                error = errors[0].text
                try:
                    error = json.loads(error)
                except ValueError:
                    # Wasn't JSON, just use the value as a string
                    pass
    data = {
        'LinkCheckStatus': status,
        **({'LinkCheckCreatedAt': created_at} if created_at else {}),
        **({'LinkCheckOutput': broken_links_dto} if broken_links_dto else {}),
        **({'LinkCheckError': error} if error else {})
    }
    return data


def _latest_task_status(request, course_key_string, view_func=None):
    """
    Get the most recent link check status update for the specified course
    key.
    """
    args = {'course_key_string': course_key_string}
    name = CourseLinkCheckTask.generate_name(args)
    task_status = UserTaskStatus.objects.filter(name=name)
    for status_filter in STATUS_FILTERS:
        task_status = status_filter().filter_queryset(request, task_status, view_func)
    return task_status.order_by('-created').first()


def generate_broken_links_descriptor(json_content, request_user, course_key):
    """
    Returns a Data Transfer Object for frontend given a list of broken links.
    Includes all link types: broken, locked, external-forbidden, and previous run links,
    as well as links found in course updates, handouts, and custom pages.

    ** Example json_content structure **
        Note: link_state is locked if the link is a studio link and returns 403
              link_state is external-forbidden if the link is not a studio link and returns 403
              link_state is previous-run if the link points to a previous course run
    [
        ['block_id_1', 'link_1', link_state],
        ['block_id_1', 'link_2', link_state],
        ['block_id_2', 'link_3', link_state],
        ...
    ]

    ** Example DTO structure **
    {
        'sections': [
            {
                'id': 'section_id',
                'displayName': 'section name',
                'subsections': [
                    {
                        'id': 'subsection_id',
                        'displayName': 'subsection name',
                        'units': [
                            {
                                'id': 'unit_id',
                                'displayName': 'unit name',
                                'blocks': [
                                    {
                                        'id': 'block_id',
                                        'displayName': 'block name',
                                        'url': 'url/to/block',
                                        'brokenLinks: [],
                                        'lockedLinks: [],
                                        'previousRunLinks: [
                                            {
                                                'originalLink': 'http://...',
                                                'isUpdated': true,
                                                'updatedLink': 'http://...'
                                            }
                                        ]
                                    },
                                    ...,
                                ]
                            },
                            ...,
                        ]
                    },
                    ...,
                ]
            },
            ...,
        ],
        'course_updates': [
            {
                'name': 'published_date',
                'url': 'url',
                'brokenLinks': [],
                'lockedLinks': [],
                'externalForbiddenLinks': [],
                'previousRunLinks': [
                    {
                        'originalLink': 'http://...',
                        'isUpdated': true,
                        'updatedLink': 'http://...'
                    }
                ]
            },
            ...
            {
                'name': 'handouts',
                'url': 'url',
                'brokenLinks': [],
                'lockedLinks': [],
                'externalForbiddenLinks': [],
                'previousRunLinks': [
                    {
                        'originalLink': 'http://...',
                        'isUpdated': true,
                        'updatedLink': 'http://...'
                    }
                ]
            }
        ],
        'custom_pages': [
            {
                'name': 'page_name',
                'url': 'url',
                'brokenLinks': [],
                'lockedLinks': [],
                'externalForbiddenLinks': [],
                'previousRunLinks': [
                    {
                        'originalLink': 'http://...',
                        'isUpdated': true,
                        'updatedLink': 'http://...'
                    }
                ]
            },
            ...
        ]
    }
    """
    return _generate_enhanced_links_descriptor(json_content, request_user, course_key)


def _update_node_tree_and_dictionary(block, link, link_state, node_tree, dictionary, course_key=None):
    """
    Inserts a block into the node tree and add its attributes to the dictionary.

    ** Example node tree structure **
    {
        'section_id1': {
            'subsection_id1': {
                'unit_id1': {
                    'block_id1': {},
                    'block_id2': {},
                    ...,
                },
                'unit_id2': {
                    'block_id3': {},
                    ...,
                },
                ...,
            },
            ...,
        },
        ...,
    }

    ** Example dictionary structure **
    {
        'xblock_id: {
            'display_name': 'xblock name',
            'category': 'chapter'
        },
        'html_block_id': {
            'display_name': 'xblock name',
            'category': 'chapter',
            'url': 'url_1',
            'locked_links': [...],
            'broken_links': [...],
            'external_forbidden_links': [...],
        }
        ...,
    }
    """
    updated_tree, updated_dictionary = node_tree, dictionary

    path = _get_node_path(block)
    current_node = updated_tree
    xblock_id = ''

    # Traverse the path and build the tree structure
    for xblock in path:
        xblock_id = xblock.location
        updated_dictionary.setdefault(
            xblock_id,
            {
                'display_name': xblock.display_name,
                'category': getattr(xblock, 'category', ''),
            }
        )
        # Sets new current node and creates the node if it doesn't exist
        current_node = current_node.setdefault(xblock_id, {})

    # Add block-level details for the last xblock in the path (URL and broken/locked links)
    updated_dictionary[xblock_id].setdefault(
        'url',
        f'/course/{block.course_id}/editor/{block.category}/{block.location}'
    )

    # The link_state == True condition is maintained for backward compatibility.
    # Previously, the is_locked attribute was used instead of link_state.
    # If is_locked is True, it indicates that the link is locked.
    if link_state is True or link_state == LinkState.LOCKED:
        updated_dictionary[xblock_id].setdefault('locked_links', []).append(link)
    elif link_state == LinkState.EXTERNAL_FORBIDDEN:
        updated_dictionary[xblock_id].setdefault('external_forbidden_links', []).append(link)
    elif link_state == LinkState.PREVIOUS_RUN:
        _add_previous_run_link(updated_dictionary, xblock_id, link, course_key)
    else:
        updated_dictionary[xblock_id].setdefault('broken_links', []).append(link)

    return updated_tree, updated_dictionary


def _get_node_path(block):
    """
    Retrieves the path from the course root node to a specific block, excluding the root.

    ** Example Path structure **
    [chapter_node, sequential_node, vertical_node, html_node]
    """
    path = []
    current_node = block

    while current_node.get_parent():
        path.append(current_node)
        current_node = current_node.get_parent()

    return list(reversed(path))


CATEGORY_TO_LEVEL_MAP = {
    "chapter": "sections",
    "sequential": "subsections",
    "vertical": "units"
}


def _create_dto_recursive(xblock_node, xblock_dictionary, parent_id=None):
    """
    Recursively build the Data Transfer Object by using
    the structure from the node tree and data from the dictionary.
    """
    # Exit condition when there are no more child nodes (at block level)
    if not xblock_node:
        return None

    level = None
    xblock_children = []

    for xblock_id, node in xblock_node.items():
        child_blocks = _create_dto_recursive(node, xblock_dictionary, parent_id=xblock_id)
        xblock_data = xblock_dictionary.get(xblock_id, {})

        xblock_entry = {
            'id': xblock_id,
            'displayName': xblock_data.get('display_name', ''),
        }
        if child_blocks is None:    # Leaf node
            level = 'blocks'
            xblock_entry.update({
                'url': xblock_data.get('url', ''),
                'brokenLinks': xblock_data.get('broken_links', []),
                'lockedLinks': xblock_data.get('locked_links', []),
                'externalForbiddenLinks': xblock_data.get('external_forbidden_links', []),
                'previousRunLinks': xblock_data.get('previous_run_links', [])
            })
        else:   # Non-leaf node
            category = xblock_data.get('category', None)
            # If parent and child has same IDs and level is 'sections', change it to 'subsections'
            # And if parent and child has same IDs and level is 'subsections', change it to 'units'
            if xblock_id == parent_id:
                if category == "chapter":
                    category = "sequential"
                elif category == "sequential":
                    category = "vertical"
            level = CATEGORY_TO_LEVEL_MAP.get(category, None)
            xblock_entry.update(child_blocks)

        xblock_children.append(xblock_entry)

    return {level: xblock_children} if level else None


def sort_course_sections(course_key, data):
    """Retrieve and sort course sections based on the published course structure."""
    course_blocks = modulestore().get_items(
        course_key,
        qualifiers={'category': 'course'},
        revision=ModuleStoreEnum.RevisionOption.published_only
    )

    # Return unchanged data if course_blocks or required keys are missing
    if not course_blocks or 'LinkCheckOutput' not in data or 'sections' not in data['LinkCheckOutput']:
        return data

    sorted_section_ids = [section.location for section in course_blocks[0].get_children()]
    sections_map = {section['id']: section for section in data['LinkCheckOutput']['sections']}
    data['LinkCheckOutput']['sections'] = [
        sections_map[section_id]
        for section_id in sorted_section_ids
        if section_id in sections_map
    ]

    return data


def _generate_links_descriptor_for_content(json_content, request_user, course_key=None):
    """
    Creates a content tree of all links in a course and their states
    Returns a structure containing all broken links and locked links for a course.
    """
    xblock_node_tree = {}
    xblock_dictionary = {}

    for item in json_content:
        block_id, link, *rest = item
        if rest:
            link_state = rest[0]
        else:
            link_state = ""

        usage_key = usage_key_with_run(block_id)
        block = get_xblock(usage_key, request_user)
        xblock_node_tree, xblock_dictionary = _update_node_tree_and_dictionary(
            block=block,
            link=link,
            link_state=link_state,
            node_tree=xblock_node_tree,
            dictionary=xblock_dictionary,
            course_key=course_key,
        )

    result = _create_dto_recursive(xblock_node_tree, xblock_dictionary)
    # Ensure we always return a valid structure with sections
    if not isinstance(result, dict):
        result = {"sections": []}

    return result


def _generate_enhanced_links_descriptor(json_content, request_user, course_key):
    """
    Generate enhanced link descriptor that includes course updates, handouts, and custom pages.
    """

    content_links = []
    course_updates_links = []
    handouts_links = []
    custom_pages_links = []
    course = modulestore().get_course(course_key)

    for item in json_content:
        block_id, link, *rest = item
        if isinstance(block_id, int):
            course_updates_links.append(item)
        elif "course_info" in block_id and "handouts" in block_id:
            handouts_links.append(item)
        elif "static_tab" in block_id:
            custom_pages_links.append(item)
        else:
            content_links.append(item)

    try:
        main_content = _generate_links_descriptor_for_content(content_links, request_user, course_key)
    except Exception:   # pylint: disable=broad-exception-caught
        main_content = {"sections": []}

    course_updates_data = (
        _generate_enhanced_content_structure(course, course_updates_links, "updates", course_key)
        if course_updates_links and course else []
    )

    handouts_data = (
        _generate_enhanced_content_structure(course, handouts_links, "handouts", course_key)
        if handouts_links and course else []
    )

    custom_pages_data = (
        _generate_enhanced_content_structure(course, custom_pages_links, "custom_pages", course_key)
        if custom_pages_links and course else []
    )

    result = main_content.copy()
    result["course_updates"] = course_updates_data + handouts_data
    result["custom_pages"] = custom_pages_data
    return result


def _generate_enhanced_content_structure(course, content_links, content_type, course_key=None):
    """
    Unified function to generate structure for enhanced content (updates, handouts, custom pages).

    Args:
        course: Course object
        content_links: List of link items for this content type
        content_type: 'updates', 'handouts', or 'custom_pages'
        course_key: Course key to check for link updates (optional)

    Returns:
        List of content items with categorized links
    """
    generators = {
        "custom_pages": _generate_custom_pages_content,
        "updates": _generate_course_updates_content,
        "handouts": _generate_handouts_content,
    }

    generator = generators.get(content_type)
    if generator:
        return generator(course, content_links, course_key)

    return []


def _generate_course_updates_content(course, updates_links, course_key=None):
    """Generate course updates content with categorized links."""
    store = modulestore()
    usage_key = create_course_info_usage_key(course, "updates")
    updates_block = store.get_item(usage_key)
    course_updates = []

    if not (updates_block and hasattr(updates_block, "data")):
        return course_updates

    update_items = get_course_update_items(updates_block)
    if not update_items:
        return course_updates

    # Group links by update ID and categorize them
    links_by_update = {}
    for item in updates_links:
        if len(item) >= 2:
            update_id, link = item[0], item[1]
            link_state = item[2] if len(item) >= 3 else LinkState.BROKEN
            links_by_update.setdefault(update_id, _create_empty_links_data())
            _categorize_link_by_state(link, link_state, links_by_update[update_id], course_key)

    for update in update_items:
        if update.get("status") != "deleted":
            update_id = update.get("id")
            course_updates.append(
                {
                    "id": str(update_id),
                    "displayName": update.get("date", "Unknown Date"),
                    "url": f"/course/{str(course.id)}/course_info",
                    **links_by_update.get(update_id, _create_empty_links_data()),
                }
            )

    return course_updates


def _generate_handouts_content(course, handouts_links, course_key=None):
    """Generate handouts content with categorized links."""
    store = modulestore()
    usage_key = create_course_info_usage_key(course, "handouts")
    handouts_block = store.get_item(usage_key)
    course_handouts = []

    if not (
        handouts_block
        and hasattr(handouts_block, "data")
        and handouts_block.data
    ):
        return course_handouts

    # Group links by block_id and categorize them
    links_by_handout = {}
    for item in handouts_links:
        if len(item) >= 2:
            block_id, link = item[0], item[1]
            link_state = item[2] if len(item) >= 3 else LinkState.BROKEN
            links_by_handout.setdefault(block_id, _create_empty_links_data())
            _categorize_link_by_state(link, link_state, links_by_handout[block_id], course_key)

    course_handouts = [
        {
            "id": str(usage_key),
            "displayName": "handouts",
            "url": f"/course/{str(course.id)}/course_info",
            **links_by_handout.get(str(usage_key), _create_empty_links_data()),
        }
    ]
    return course_handouts


def _generate_custom_pages_content(course, custom_pages_links, course_key=None):
    """Generate custom pages content with categorized links."""
    custom_pages = []

    if not course or not hasattr(course, "tabs"):
        return custom_pages

    # Group links by block_id and categorize them
    links_by_page = {}
    for item in custom_pages_links:
        if len(item) >= 2:
            block_id, link = item[0], item[1]
            link_state = item[2] if len(item) >= 3 else LinkState.BROKEN
            links_by_page.setdefault(block_id, _create_empty_links_data())
            _categorize_link_by_state(link, link_state, links_by_page[block_id], course_key)

    # Process static tabs and add their pages
    for tab in course.tabs:
        if isinstance(tab, StaticTab):
            block_id = str(course.id.make_usage_key("static_tab", tab.url_slug))
            custom_pages.append({
                "id": block_id,
                "displayName": tab.name,
                "url": f"/course/{str(course.id)}/custom-pages",
                **links_by_page.get(block_id, _create_empty_links_data()),
            })

    return custom_pages


def _categorize_link_by_state(link, link_state, links_data, course_key=None):
    """
    Helper function to categorize a link into the appropriate list based on its state.

    Args:
        link (str): The URL link to categorize
        link_state (str): The state of the link (broken, locked, external-forbidden, previous-run)
        links_data (dict): Dictionary containing the categorized link lists
        course_key: Course key to check for link updates (optional)
    """
    state_to_key = {
        LinkState.BROKEN: "brokenLinks",
        LinkState.LOCKED: "lockedLinks",
        LinkState.EXTERNAL_FORBIDDEN: "externalForbiddenLinks",
        LinkState.PREVIOUS_RUN: "previousRunLinks"
    }

    key = state_to_key.get(link_state)
    if key:
        if key == "previousRunLinks":
            data = _generate_link_update_info(link, course_key)
            links_data[key].append(data)
        else:
            links_data[key].append(link)


def _create_empty_links_data():
    """
    Helper function to create an empty links data structure.

    Returns:
        dict: Dictionary with empty lists for each link type
    """
    return {
        "brokenLinks": [],
        "lockedLinks": [],
        "externalForbiddenLinks": [],
        "previousRunLinks": [],
    }


def get_course_link_update_data(request, course_id):
    """
    Retrieves data and formats it for the course link update status request.
    """
    status = None
    results = []
    task_status = _latest_course_link_update_task_status(request, course_id)

    if task_status is None:
        status = "uninitiated"
    else:
        status = task_status.state

        if task_status.state == UserTaskStatus.SUCCEEDED:
            try:
                artifact = UserTaskArtifact.objects.get(
                    status=task_status, name="LinkUpdateResults"
                )
                with artifact.file as file:
                    content = file.read()
                    results = json.loads(content)
            except (UserTaskArtifact.DoesNotExist, ValueError):
                # If no artifact found or invalid JSON, just return empty results
                results = []

    data = {
        "status": status,
        **({"results": results}),
    }
    return data


def _latest_course_link_update_task_status(request, course_id, view_func=None):
    """
    Get the most recent course link update status for the specified course key.
    """

    args = {"course_id": course_id}
    name = CourseLinkUpdateTask.generate_name(args)
    task_status = UserTaskStatus.objects.filter(name=name)
    for status_filter in STATUS_FILTERS:
        task_status = status_filter().filter_queryset(request, task_status, view_func)
    return task_status.order_by("-created").first()


def _get_link_update_status(original_url, course_key):
    """
    Check whether a given link has been updated based on the latest link update results.

    Args:
        original_url (str): The original URL to check
        course_key: The course key

    Returns:
        dict: Dictionary with 'originalLink', 'isUpdated', and 'updatedLink' keys
    """
    def _create_response(original_link, is_updated, updated_link=None):
        """Helper to create consistent response format."""
        return {
            "originalLink": original_link,
            "isUpdated": is_updated,
            "updatedLink": updated_link,
        }

    try:
        # Check if URL contains current course key (indicates it's been updated)
        current_course_str = str(course_key)
        if current_course_str in original_url:
            prev_run_key = get_previous_run_course_key(course_key)
            if prev_run_key:
                reconstructed_original = original_url.replace(current_course_str, str(prev_run_key))
                return _create_response(reconstructed_original, True, original_url)
            return _create_response(original_url, True, original_url)

        update_results = _get_update_results(course_key)
        if not update_results:
            return _create_response(original_url, False, None)

        for result in update_results:
            if not result.get("success", False):
                continue

            result_original = result.get("original_url", "")
            result_new = result.get("new_url", "")

            # Direct match with original URL
            if result_original == original_url:
                return _create_response(original_url, True, result_new)

            # Check if current URL is an updated URL
            if result_new == original_url:
                return _create_response(result_original, True, original_url)

            # Check if URLs match through reconstruction
            if _urls_match_through_reconstruction(original_url, result_new, course_key):
                return _create_response(original_url, True, result_new)

        return _create_response(original_url, False, None)

    except Exception:  # pylint: disable=broad-except
        return _create_response(original_url, False, None)


def _get_update_results(course_key):
    """
    Helper function to get update results from the latest link update task.

    Returns:
        list: Update results or empty list if not found
    """
    try:
        task_status = _latest_course_link_update_task_status(None, str(course_key))

        if not task_status or task_status.state != UserTaskStatus.SUCCEEDED:
            return []

        artifact = UserTaskArtifact.objects.get(
            status=task_status, name="LinkUpdateResults"
        )
        with artifact.file as file:
            content = file.read()
            return json.loads(content)

    except (UserTaskArtifact.DoesNotExist, ValueError, json.JSONDecodeError):
        return []


def _is_previous_run_link(link, course_key):
    """
    Check if a link is a previous run link by checking if it contains a previous course key
    or if it has update results indicating it was updated.

    Args:
        link: The URL to check
        course_key: The current course key

    Returns:
        bool: True if the link appears to be a previous run link
    """
    try:
        if str(course_key) in link:
            return True

        prev_run_key = get_previous_run_course_key(course_key)
        if prev_run_key and str(prev_run_key) in link:
            return True

        update_results = _get_update_results(course_key)
        for result in update_results:
            if not result.get("success", False):
                continue
            if link in [result.get("original_url", ""), result.get("new_url", "")]:
                return True

        return False
    except Exception:  # pylint: disable=broad-except
        return False


def _urls_match_through_reconstruction(original_url, new_url, course_key):
    """
    Check if an original URL matches a new URL through course key reconstruction.

    Args:
        original_url (str): The original URL from broken links
        new_url (str): The new URL from update results
        course_key: The current course key

    Returns:
        bool: True if they match through reconstruction
    """
    try:
        prev_run_key = get_previous_run_course_key(course_key)
        if not prev_run_key:
            return False

        # Reconstruct what the original URL would have been
        reconstructed_original = new_url.replace(str(course_key), str(prev_run_key))
        return reconstructed_original == original_url

    except Exception:  # pylint: disable=broad-except
        return False


def _process_content_links(content_text, all_links, course_key=None):
    """
    Helper function to process links in content and categorize them by state.

    Args:
        content_text: The text content to extract links from
        all_links: List of tuples containing (url, state) or (url, state, extra_info)
        course_key: Course key to check for link updates (optional)

    Returns:
        dict: Categorized link data
    """
    if not content_text:
        return _create_empty_links_data()

    content_links = extract_content_URLs_from_course(content_text)
    if not content_links:
        return _create_empty_links_data()

    # Create link state mapping
    link_state_map = {
        item[1]: item[2] if len(item) >= 3 else LinkState.BROKEN
        for item in all_links if len(item) >= 2
    }

    # Categorize links by state
    link_data = _create_empty_links_data()
    for link in content_links:
        link_state = link_state_map.get(link)
        if link_state is not None:
            _categorize_link_by_state(link, link_state, link_data, course_key)
        else:
            # Check if this link is a previous run link that might have been updated
            if course_key and _is_previous_run_link(link, course_key):
                _categorize_link_by_state(link, LinkState.PREVIOUS_RUN, link_data, course_key)

    return link_data


def _generate_link_update_info(link, course_key=None):
    """
    Create a previous run link data with appropriate update status.

    Args:
        link: The link URL
        course_key: Course key to check for updates (optional)

    Returns:
        dict: Previous run link data with originalLink, isUpdated, and updatedLink
    """
    if course_key:
        updated_info = _get_link_update_status(link, course_key)
        if updated_info:
            return {
                'originalLink': updated_info['originalLink'],
                'isUpdated': updated_info['isUpdated'],
                'updatedLink': updated_info['updatedLink']
            }

    return {
        'originalLink': link,
        'isUpdated': False,
        'updatedLink': None
    }


def _add_previous_run_link(dictionary, xblock_id, link, course_key):
    """
    Helper function to add a previous run link with appropriate update status.

    Args:
        dictionary: The xblock dictionary to update
        xblock_id: The ID of the xblock
        link: The link URL
        course_key: Course key to check for updates (optional)
    """
    data = _generate_link_update_info(link, course_key)
    dictionary[xblock_id].setdefault('previous_run_links', []).append(data)
