''' useful functions for finding content and its position '''


from logging import getLogger

from common.djangoapps.student.roles import GlobalStaff  # pylint: disable=unused-import  # noqa: F401
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.masquerade import MASQUERADE_SETTINGS_KEY

from .exceptions import ItemNotFoundError, NoPathToItem

LOGGER = getLogger(__name__)


def path_to_location(modulestore, usage_key, request=None, full_path=False, branch_type=None):
    '''
    Try to find a course_id/chapter/section[/position] path to location in
    modulestore.  The courseware insists that the first level in the course is
    chapter, but any kind of block can be a "section".

    Args:
        modulestore: which store holds the relevant objects
        usage_key: :class:`UsageKey` the id of the location to which to generate the path
        request: Request object containing information about user and masquerade settings, Default is None
        full_path: :class:`Bool` if True, return the full path to location. Default is False.

    Raises
        ItemNotFoundError if the location doesn't exist.
        NoPathToItem if the location exists, but isn't accessible via
            a chapter/section path in the course(s) being searched.

    Returns:
        a tuple (course_id, chapter, section, position) suitable for the
        courseware index view.

    If the section is a sequential or vertical, position will be the children index
    of this location under that sequence.
    '''

    def flatten(xs):
        '''Convert lisp-style (a, (b, (c, ()))) list into a python list.
        Not a general flatten function. '''
        p = []
        while xs != ():
            p.append(xs[0])
            xs = xs[1]
        return p

    def find_path_to_course():
        '''Find a path up the location graph to a node with the
        specified category.

        If no path exists, return None.

        If a path exists, return it as a tuple with root location first, and
        the target location last.
        '''
        # Standard DFS

        # To keep track of where we came from, the work queue has
        # tuples (location, path-so-far).  To avoid lots of
        # copying, the path-so-far is stored as a lisp-style
        # list--nested hd::tl tuples, and flattened at the end.
        queue = [(usage_key, ())]
        while len(queue) > 0:
            (next_usage, path) = queue.pop()  # Takes from the end

            # get_parent_location raises ItemNotFoundError if location isn't found
            parent = modulestore.get_parent_location(next_usage)

            # print 'Processing loc={0}, path={1}'.format(next_usage, path)
            if next_usage.block_type == "course":
                # Found it!
                path = (next_usage, path)
                return flatten(path)
            elif parent is None:
                # Orphaned item.
                return None

            # otherwise, add parent locations at the end
            newpath = (next_usage, path)
            queue.append((parent, newpath))

    with modulestore.bulk_operations(usage_key.course_key):
        with modulestore.branch_setting(branch_type, usage_key.course_key):
            if not modulestore.has_item(usage_key):
                raise ItemNotFoundError(usage_key)

            path = find_path_to_course()
            if path is None:
                raise NoPathToItem(usage_key)

            if full_path:
                return path

            n = len(path)
            course_id = path[0].course_key
            # pull out the location names
            chapter = path[1].block_id if n > 1 else None
            section = path[2].block_id if n > 2 else None
            vertical = path[3].block_id if n > 3 else None
            # Figure out the position
            position = None

            # This block of code will find the position of a block within a nested tree
            # of blocks. If a problem is on tab 2 of a sequence that's on tab 3 of a
            # sequence, the resulting position is 3_2. However, no positional blocks
            # (e.g. sequential) currently deal with this form of representing nested
            # positions. This needs to happen before jumping to a block nested in more
            # than one positional block will work.

            if n > 3:
                position_list = []
                for path_index in range(2, n - 1):
                    category = path[path_index].block_type
                    if category == 'sequential':
                        section_desc = modulestore.get_item(path[path_index])
                        # this calls get_children rather than just children b/c old mongo includes private children
                        # in children but not in get_children
                        child_locs = get_child_locations(section_desc, request, course_id)
                        # positions are 1-indexed, and should be strings to be consistent with
                        # url parsing.
                        if path[path_index + 1] in child_locs:
                            position_list.append(str(child_locs.index(path[path_index + 1]) + 1))
                position = "_".join(position_list)

    return (course_id, chapter, section, vertical, position, path[-1])


def get_child_locations(section_desc, request, course_id):
    """
    Returns all child locations for a section. If user is learner or masquerading as learner,
    staff only blocks are excluded.
    """
    is_staff_user = has_access(request.user, 'staff', course_id).has_access if request else False

    def is_masquerading_as_student():
        """
        Return True if user is masquerading as learner.
        """
        masquerade_settings = request.session.get(MASQUERADE_SETTINGS_KEY, {})
        course_info = masquerade_settings.get(course_id)
        return masquerade_settings and course_info and getattr(course_info, 'role', '') == 'student'

    def is_user_staff_and_not_masquerading_learner():
        """
        Return True if user is staff and not masquerading as learner.
        """
        return is_staff_user and not is_masquerading_as_student()

    def is_child_appendable(child_instance):
        """
        Return True if child is appendable based on request and request's user type.
        """
        return (request and is_user_staff_and_not_masquerading_learner()) or not child_instance.visible_to_staff_only  # pylint: disable=consider-using-ternary

    child_locs = []
    for child in section_desc.get_children():
        if not is_child_appendable(child):
            continue
        child_locs.append(child.location)
    return child_locs


def navigation_index(position):
    """
    Get the navigation index from the position argument (where the position argument was received from a call to
    path_to_location)

    Argument:
    position - result of position returned from call to path_to_location. This is an underscore (_) separated string of
    vertical 1-indexed positions. If the course is built in Studio then you'll never see verticals as children of
    verticals, and so extremely often one will only see the first vertical as an integer position. This specific action
    is to allow navigation / breadcrumbs to locate the topmost item because this is the location actually required by
    the LMS code

    Returns:
    1-based integer of the position of the desired item within the vertical
    """
    if position is None:
        return None

    try:
        navigation_position = int(position.split('_', 1)[0])
    except (ValueError, TypeError):
        LOGGER.exception('Bad position %r passed to navigation_index, will assume first position', position)
        navigation_position = 1

    return navigation_position
