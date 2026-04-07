"""
This is where Studio interacts with the learning_sequences application, which
is responsible for holding course outline data. Studio _pushes_ that data into
learning_sequences at publish time.
"""
from datetime import timezone
from typing import List, Tuple

from edx_django_utils.monitoring import function_trace, set_custom_attribute

from openedx.core.djangoapps.content.learning_sequences.api import replace_course_outline
from openedx.core.djangoapps.content.learning_sequences.data import (
    ContentErrorData,
    CourseLearningSequenceData,
    CourseOutlineData,
    CourseSectionData,
    CourseVisibility,
    ExamData,
    VisibilityData,
)
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order


def _remove_version_info(usage_key):
    """
    When we ask modulestore for the published branch in the Studio process
    after catching a publish signal, the items that have been changed will
    return UsageKeys that have full version information in their attached
    CourseKeys. This makes them hash and serialize differently. We want to
    strip this information and have everything use a CourseKey with no
    version information attached.

    The fact that this versioned CourseKey appears is likely an unintended
    side-effect, rather than an intentional part of the API contract. It
    also likely doesn't happen when the modulestore is being processed from
    a different process than the one doing the writing (e.g. a celery task
    running on any environment other than devstack). But stripping this
    version information out is necessary to make devstack and tests work
    properly.
    """
    unversioned_course_key = usage_key.course_key.replace(branch=None, version_guid=None)
    return usage_key.map_into_course(unversioned_course_key)


def _error_for_not_section(not_section):
    """
    ContentErrorData when we run into a child of <course> that's not a Section.

    Has to be phrased in a way that makes sense to course teams.
    """
    return ContentErrorData(
        message=(
            f'<course> contains a <{not_section.location.block_type}> tag with '
            f'url_name="{not_section.location.block_id}" and '
            f'display_name="{getattr(not_section, "display_name", "")}". '
            f'Expected <chapter> tag instead.'
        ),
        usage_key=_remove_version_info(not_section.location),
    )


def _error_for_not_sequence(section, not_sequence):
    """
    ContentErrorData when we run into a child of Section that's not a Sequence.

    Has to be phrased in a way that makes sense to course teams.
    """
    return ContentErrorData(
        message=(
            f'<chapter> with url_name="{section.location.block_id}" and '
            f'display_name="{section.display_name}" contains a '
            f'<{not_sequence.location.block_type}> tag with '
            f'url_name="{not_sequence.location.block_id}" and '
            f'display_name="{getattr(not_sequence, "display_name", "")}". '
            f'Expected a <sequential> tag.'
        ),
        usage_key=_remove_version_info(not_sequence.location),
    )


def _error_for_duplicate_child(section, duplicate_child, original_section):
    """
    ContentErrorData when we run into a child of Section that's defined in a
    previous section.

    Has to be phrased in a way that makes sense to course teams.
    """
    return ContentErrorData(
        message=(
            f'<chapter> with url_name="{section.location.block_id}" and '
            f'display_name="{section.display_name}" contains a '
            f'<{duplicate_child.location.block_type}> tag with '
            f'url_name="{duplicate_child.location.block_id}" and '
            f'display_name="{getattr(duplicate_child, "display_name", "")}" '
            f'that is defined in another section with '
            f'url_name="{original_section.location.block_id}" and '
            f'display_name="{original_section.display_name}". Expected a '
            f'unique <sequential> tag instead.'

        ),
        usage_key=_remove_version_info(duplicate_child.location),
    )


def _bubbled_up_groups_from_units(group_access_from_units):
    """
    Return {user_partition_id: [group_ids]} to bubble up from Units to Sequence.

    This is to handle a special case: If *all* of the Units in a sequence have
    the exact same group for a given user partition, bubble that value up to the
    Sequence as a whole. For example, say that every Unit in a Sequence has a
    group_access that looks like: { ENROLLMENT: [MASTERS] } (where both
    constants are ints). In this case, an Audit user has nothing to see in the
    Sequence at all, and it's not useful to give them an empty shell. So we'll
    act as if the Sequence as a whole had that group setting. Note that there is
    currently no way to set the group_access setting at the sequence level in
    Studio, so course teams can only manipulate it for individual Units.
    """
    # If there are no Units, there's nothing to bubble up.
    if not group_access_from_units:
        return {}

    def _normalize_group_access_dict(group_access):
        return {
            user_partition_id: sorted(group_ids)  # sorted for easier comparison
            for user_partition_id, group_ids in group_access.items()
            if group_ids  # Ignore empty groups
        }

    normalized_group_access_dicts = [
        _normalize_group_access_dict(group_access) for group_access in group_access_from_units
    ]
    first_unit_group_access = normalized_group_access_dicts[0]
    rest_of_seq_group_access_list = normalized_group_access_dicts[1:]

    # If there's only a single Unit, bubble up its group_access.
    if not rest_of_seq_group_access_list:
        return first_unit_group_access

    # Otherwise, go through the user partitions and groups in our first unit
    # and compare them to all the other group_access dicts from the units in the
    # rest of the sequence. Only keep the ones that match exactly and do not
    # have empty groups.
    common_group_access = {
        user_partition_id: group_ids
        for user_partition_id, group_ids in first_unit_group_access.items()
        if group_ids and all(
            group_ids == group_access.get(user_partition_id)
            for group_access in rest_of_seq_group_access_list
        )
    }
    return common_group_access


def _make_user_partition_groups(usage_key, group_access):
    """
    Return a (Dict, Optional[ContentErrorData]) of user partition groups.

    The Dict is a mapping of user partition ID to list of group IDs. If any
    empty groups are encountered, we create a ContentErrorData about that. If
    there are no empty groups, we pass back (Dict, None).
    """
    empty_partitions = sorted(
        part_id for part_id, group_ids in group_access.items() if not group_ids
    )
    empty_partitions_txt = ", ".join([str(part_id) for part_id in empty_partitions])
    if empty_partitions:
        error = ContentErrorData(
            message=(
                f'<{usage_key.block_type}> with url_name="{usage_key.block_id}"'
                f' has the following empty group_access user partitions: '
                f'{empty_partitions_txt}. This would make this content '
                f'unavailable to anyone except course staff. Ignoring these '
                f'group_access settings when building outline.'
            ),
            usage_key=_remove_version_info(usage_key),
        )
    else:
        error = None

    user_partition_groups = {
        part_id: group_ids for part_id, group_ids in group_access.items() if group_ids
    }
    return user_partition_groups, error


def _make_bubbled_up_error(seq_usage_key, user_partition_id, group_ids):
    return ContentErrorData(
        message=(
            f'<{seq_usage_key.block_type}> with url_name="{seq_usage_key.block_id}"'
            f' was assigned group_ids {group_ids} for user_partition_id '
            f'{user_partition_id} because all of its child Units had that '
            f'group_access setting. This is permitted, but is an unusual usage '
            f'that may cause unexpected behavior while browsing the course.'
        ),
        usage_key=_remove_version_info(seq_usage_key),
    )


def _make_not_bubbled_up_error(seq_usage_key, seq_group_access, user_partition_id, group_ids):
    return ContentErrorData(
        message=(
            f'<{seq_usage_key.block_type}> with url_name="{seq_usage_key.block_id}" '
            f'has children with only group_ids {group_ids} for user_partition_id '
            f'{user_partition_id}, but its own group_access setting is '
            f'{seq_group_access}, which takes precedence. This is permitted, '
            f'but probably not intended, since it means that the content is '
            f'effectively unusable by anyone except staff.'
        ),
        usage_key=_remove_version_info(seq_usage_key),
    )


def _make_section_data(section, unique_sequences):
    """
    Return a (CourseSectionData, List[ContentDataError]) from a SectionBlock.

    Can return None for CourseSectionData if it's not really a SectionBlock that
    was passed in.

    This method does a lot of the work to convert modulestore fields to an input
    that the learning_sequences app expects. OLX import permits structures that
    are much less constrained than Studio's UI allows for, so whenever we run
    into something that does not meet our Course -> Section -> Subsection
    hierarchy expectations, we add a support-team-readable error message to our
    list of ContentDataErrors to pass back.

    At this point in the code, everything has already been deserialized into
    SectionBlocks and SequenceBlocks, but we're going to phrase our messages in
    ways that would make sense to someone looking at the import OLX, since that
    is the layer that the course teams and support teams are working with.
    """
    section_errors = []

    # First check if it's not a section at all, and short circuit if it isn't.
    if section.location.block_type != 'chapter':
        section_errors.append(_error_for_not_section(section))
        return (None, section_errors, unique_sequences)

    section_user_partition_groups, error = _make_user_partition_groups(
        section.location, section.group_access
    )
    # Invalid user partition errors aren't fatal. Just log and continue on.
    if error:
        section_errors.append(error)

    valid_sequence_tags = ['sequential']
    sequences_data = []

    for sequence in section.get_children():
        if sequence.location.block_type not in valid_sequence_tags:
            section_errors.append(_error_for_not_sequence(section, sequence))
            continue
        # We need to check if there are duplicate sequences. If there are
        # duplicate sequences the course outline generation will fail. We ignore
        # the duplicated sequences, so they will not be sent to
        # learning_sequences.
        if sequence.location in unique_sequences:
            original_section = unique_sequences[sequence.location]
            section_errors.append(_error_for_duplicate_child(section, sequence, original_section))
            continue
        else:
            unique_sequences[sequence.location] = section
        seq_user_partition_groups, error = _make_user_partition_groups(
            sequence.location, sequence.group_access
        )
        if error:
            section_errors.append(error)

        # Bubble up User Partition Group settings from Units if appropriate.
        sequence_upg_from_units = _bubbled_up_groups_from_units(
            [unit.group_access for unit in sequence.get_children()]
        )
        for user_partition_id, group_ids in sequence_upg_from_units.items():
            # If there's an existing user partition ID set at the sequence
            # level, we respect it, even if it seems nonsensical. The hack of
            # bubbling things up from the Unit level is only done if there's
            # no conflicting value set at the Sequence level.
            if user_partition_id not in seq_user_partition_groups:
                section_errors.append(
                    _make_bubbled_up_error(sequence.location, user_partition_id, group_ids)
                )
                seq_user_partition_groups[user_partition_id] = group_ids
            else:
                section_errors.append(
                    _make_not_bubbled_up_error(
                        sequence.location, sequence.group_access, user_partition_id, group_ids
                    )
                )

        sequences_data.append(
            CourseLearningSequenceData(
                usage_key=_remove_version_info(sequence.location),
                title=sequence.display_name_with_default,
                inaccessible_after_due=sequence.hide_after_due,
                exam=ExamData(
                    is_practice_exam=sequence.is_practice_exam,
                    is_proctored_enabled=sequence.is_proctored_enabled,
                    is_time_limited=sequence.is_time_limited,
                ),
                visibility=VisibilityData(
                    hide_from_toc=sequence.hide_from_toc,
                    visible_to_staff_only=sequence.visible_to_staff_only,
                ),
                user_partition_groups=seq_user_partition_groups,
            )
        )

    section_data = CourseSectionData(
        usage_key=_remove_version_info(section.location),
        title=section.display_name_with_default,
        sequences=sequences_data,
        visibility=VisibilityData(
            hide_from_toc=section.hide_from_toc,
            visible_to_staff_only=section.visible_to_staff_only,
        ),
        user_partition_groups=section_user_partition_groups,
    )
    return section_data, section_errors, unique_sequences


@function_trace('get_outline_from_modulestore')
def get_outline_from_modulestore(course_key) -> Tuple[CourseOutlineData, List[ContentErrorData]]:
    """
    Return a CourseOutlineData and list of ContentErrorData for param:course_key

    This function does not write any data as a side-effect. It generates a
    CourseOutlineData by inspecting the contents in the modulestore, but does
    not push that data anywhere. This function only operates on the published
    branch, and will not work on Old Mongo courses.
    """
    store = modulestore()
    content_errors = []

    with store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
        # Pull course with depth=3 so we prefetch Section -> Sequence -> Unit
        course = store.get_course(course_key, depth=3)
        sections_data = []
        unique_sequences = {}
        for section in course.get_children():
            section_data, section_errors, unique_sequences = _make_section_data(section, unique_sequences)
            if section_data:
                sections_data.append(section_data)
            content_errors.extend(section_errors)

        course_outline_data = CourseOutlineData(
            course_key=course_key,
            title=course.display_name_with_default,

            # subtree_edited_on has a tzinfo of bson.tz_util.FixedOffset (which
            # maps to UTC), but for consistency, we're going to use the standard
            # python timezone.utc (which is what the learning_sequence app will
            # return from MySQL). They will compare as equal.
            published_at=course.subtree_edited_on.replace(tzinfo=timezone.utc),

            # .course_version is a BSON obj, so we convert to str (MongoDB-
            # specific objects don't go into CourseOutlineData).
            published_version=str(course.course_version),

            entrance_exam_id=course.entrance_exam_id,
            days_early_for_beta=course.days_early_for_beta,
            sections=sections_data,
            self_paced=course.self_paced,
            course_visibility=CourseVisibility(course.course_visibility),
        )

    return (course_outline_data, content_errors)


def update_outline_from_modulestore(course_key):
    """
    Update the CourseOutlineData for course_key in the learning_sequences with
    ModuleStore data (i.e. what was most recently published in Studio).
    """
    # Set the course_id attribute first so that if getting the information
    # from the modulestore errors out, we still have the course_key reported in
    # New Relic for easier trace debugging.
    set_custom_attribute('course_id', str(course_key))

    course_outline_data, content_errors = get_outline_from_modulestore(course_key)
    set_custom_attribute('num_sequences', len(course_outline_data.sequences))
    set_custom_attribute('num_content_errors', len(content_errors))

    replace_course_outline(course_outline_data, content_errors=content_errors)
