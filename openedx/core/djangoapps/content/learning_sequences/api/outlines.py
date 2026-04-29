"""
All Course Outline related business logic. Do not import from this module
directly. Use openedx.core.djangoapps.content.learning_sequences.api -- that
__init__.py imports from here, and is a more stable place to import from.
"""
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, FrozenSet, List, Optional, Union  # noqa: UP035

from django.db import transaction
from django.db.models.query import QuerySet
from edx_django_utils.cache import TieredCache
from edx_django_utils.monitoring import function_trace, set_custom_attribute
from opaque_keys import OpaqueKey
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator

from openedx.core import types
from openedx.core.djangoapps.content.learning_sequences.api.processors.team_partition_groups import (
    TeamPartitionGroupsOutlineProcessor,
)

from ..data import (
    ContentErrorData,
    CourseLearningSequenceData,
    CourseOutlineData,
    CourseSectionData,
    CourseVisibility,
    ExamData,
    UserCourseOutlineData,
    UserCourseOutlineDetailsData,
    VisibilityData,
)
from ..models import (
    ContentError,
    CourseContext,
    CourseSection,
    CourseSectionSequence,
    CourseSequenceExam,
    LearningContext,
    LearningSequence,
    PublishReport,
    UserPartitionGroup,
)
from .permissions import can_see_all_content
from .processors.cohort_partition_groups import CohortPartitionGroupsOutlineProcessor
from .processors.content_gating import ContentGatingOutlineProcessor
from .processors.enrollment import EnrollmentOutlineProcessor
from .processors.enrollment_track_partition_groups import EnrollmentTrackPartitionGroupsOutlineProcessor
from .processors.milestones import MilestonesOutlineProcessor
from .processors.schedule import ScheduleOutlineProcessor
from .processors.special_exams import SpecialExamsOutlineProcessor
from .processors.visibility import VisibilityOutlineProcessor

log = logging.getLogger(__name__)

# Public API...
__all__ = [
    'get_content_errors',
    'get_course_keys_with_outlines',
    'get_course_outline',
    'get_user_course_outline',
    'get_user_course_outline_details',
    'key_supports_outlines',
    'replace_course_outline',
]


def key_supports_outlines(opaque_key: OpaqueKey) -> bool:
    """
    Does this key-type support outlines?

    Allow all non-deprecated CourseKeys except for v1 Libraries (which subclass
    CourseKey but shouldn't). So our normal SplitMongo courses (CourseLocator)
    will work, as will CCX courses. But libraries, pathways, and Old Mongo
    courses will not.
    """
    # Get LibraryLocators out of the way first because they subclass CourseKey.
    if isinstance(opaque_key, LibraryLocator):
        return False

    # All other CourseKey types are acceptable if they're not deprecated. There
    # are only two at the moment though, course-v1: and ccx-v1:. The old slash-
    # separated course IDs (Org/Course/Run) are not supported.
    if isinstance(opaque_key, CourseKey):
        return not opaque_key.deprecated

    return False


@function_trace('learning_sequences.api.get_course_keys_with_outlines')
def get_course_keys_with_outlines() -> QuerySet:
    """
    Queryset of ContextKeys, iterable as a flat list.

    The function_trace time here is a little misleading because querysets are
    lazily evaluated. It's mostly there to get information about how often it's
    being called and by what transactions.
    """
    return LearningContext.objects.values_list('context_key', flat=True)


@function_trace('learning_sequences.api.get_course_outline')
def get_course_outline(course_key: CourseKey) -> CourseOutlineData:
    """
    Get the outline of a course run.

    There is no user-specific data or permissions applied in this function.

    See the definition of CourseOutlineData for details about the data returned.
    """
    # Record the course separately from the course_id usually done in views,
    # to make sure we get useful Span information if we're invoked by things
    # like management commands, where it may iterate through many courses.
    set_custom_attribute('learning_sequences.api.course_id', str(course_key))
    course_context = _get_course_context_for_outline(course_key)

    # Check to see if it's in the cache.
    cache_key = "learning_sequences.api.get_course_outline.v2.{}.{}".format(  # noqa: UP032
        course_context.learning_context.context_key, course_context.learning_context.published_version
    )
    outline_cache_result = TieredCache.get_cached_response(cache_key)
    if outline_cache_result.is_found:
        return outline_cache_result.value

    # Fetch model data, and remember that empty Sections should still be
    # represented (so query CourseSection explicitly instead of relying only on
    # select_related from CourseSectionSequence).
    section_models = CourseSection.objects \
        .prefetch_related('new_user_partition_groups') \
        .filter(course_context=course_context) \
        .order_by('ordering')
    section_sequence_models = CourseSectionSequence.objects \
        .prefetch_related('new_user_partition_groups') \
        .filter(course_context=course_context) \
        .order_by('ordering') \
        .select_related('sequence', 'exam')

    # Build mapping of section.id keys to sequence lists.
    sec_ids_to_sequence_list = defaultdict(list)

    for sec_seq_model in section_sequence_models:
        sequence_model = sec_seq_model.sequence

        try:
            exam_data = ExamData(
                is_practice_exam=sec_seq_model.exam.is_practice_exam,
                is_proctored_enabled=sec_seq_model.exam.is_proctored_enabled,
                is_time_limited=sec_seq_model.exam.is_time_limited
            )
        except CourseSequenceExam.DoesNotExist:
            exam_data = ExamData()

        sequence_data = CourseLearningSequenceData(
            usage_key=sequence_model.usage_key,
            title=sequence_model.title,
            inaccessible_after_due=sec_seq_model.inaccessible_after_due,
            visibility=VisibilityData(
                hide_from_toc=sec_seq_model.hide_from_toc,
                visible_to_staff_only=sec_seq_model.visible_to_staff_only,
            ),
            exam=exam_data,
            user_partition_groups=_get_user_partition_groups_from_qset(
                sec_seq_model.new_user_partition_groups.all()
            ),
        )
        sec_ids_to_sequence_list[sec_seq_model.section_id].append(sequence_data)

    sections_data = [
        CourseSectionData(
            usage_key=section_model.usage_key,
            title=section_model.title,
            sequences=sec_ids_to_sequence_list[section_model.id],
            visibility=VisibilityData(
                hide_from_toc=section_model.hide_from_toc,
                visible_to_staff_only=section_model.visible_to_staff_only,
            ),
            user_partition_groups=_get_user_partition_groups_from_qset(
                section_model.new_user_partition_groups.all()
            ),
        )
        for section_model in section_models
    ]

    outline_data = CourseOutlineData(
        course_key=course_context.learning_context.context_key,
        title=course_context.learning_context.title,
        published_at=course_context.learning_context.published_at,
        published_version=course_context.learning_context.published_version,
        days_early_for_beta=course_context.days_early_for_beta,
        entrance_exam_id=course_context.entrance_exam_id,
        sections=sections_data,
        self_paced=course_context.self_paced,
        course_visibility=CourseVisibility(course_context.course_visibility),
    )
    TieredCache.set_all_tiers(cache_key, outline_data, 300)

    return outline_data


def _get_user_partition_groups_from_qset(upg_qset) -> Dict[int, FrozenSet[int]]:  # noqa: UP006
    """
    Given a QuerySet of UserPartitionGroup, return a mapping of UserPartition
    IDs to the set of Group IDs for each UserPartition.
    """
    user_part_groups = defaultdict(set)
    for upg in upg_qset:
        user_part_groups[upg.partition_id].add(upg.group_id)

    return {
        partition_id: frozenset(group_ids)
        for partition_id, group_ids in user_part_groups.items()
    }


def _get_course_context_for_outline(course_key: CourseKey) -> CourseContext:
    """
    Get Course Context for given param:course_key
    """
    if course_key.deprecated:
        raise ValueError(
            "Learning Sequence API does not support Old Mongo courses: {}"  # noqa: UP032
            .format(course_key),
        )
    try:
        course_context = (
            LearningContext.objects
                           .select_related('course_context')
                           .get(context_key=course_key)
                           .course_context
        )
    except LearningContext.DoesNotExist:
        # Could happen if it hasn't been published.
        raise CourseOutlineData.DoesNotExist(  # pylint: disable=raise-missing-from  # noqa: B904
            f"No CourseOutlineData for {course_key}"
        )
    return course_context


def get_content_errors(course_key: CourseKey) -> List[ContentErrorData]:  # noqa: UP006
    """
    Get ContentErrors created in the most recent publish of this Course run.
    """
    try:
        learning_context = (
            LearningContext.objects.select_related('publish_report')
                                   .get(context_key=course_key)
        )
        publish_report = learning_context.publish_report
    except (LearningContext.DoesNotExist, PublishReport.DoesNotExist):
        return []

    return [
        ContentErrorData(usage_key=error.usage_key, message=error.message)
        for error in publish_report.content_errors.all().order_by('id')
    ]


@function_trace('learning_sequences.api.get_user_course_outline')
def get_user_course_outline(course_key: CourseKey,
                            user: types.User,
                            at_time: datetime) -> UserCourseOutlineData:
    """
    Get an outline customized for a particular user at a particular time.

    `user` is a Django User object (including the AnonymousUser)
    `at_time` should be a UTC datetime.datetime object.

    See the definition of UserCourseOutlineData for details about the data
    returned.
    """
    user_course_outline, _ = _get_user_course_outline_and_processors(course_key, user, at_time)
    return user_course_outline


@function_trace('learning_sequences.api.get_user_course_outline_details')
def get_user_course_outline_details(course_key: CourseKey,
                                    user: types.User,
                                    at_time: datetime) -> UserCourseOutlineDetailsData:
    """
    Get an outline with supplementary data like scheduling information.

    See the definition of UserCourseOutlineDetailsData for details about the
    data returned.
    """
    user_course_outline, processors = _get_user_course_outline_and_processors(
        course_key, user, at_time
    )
    with function_trace('learning_sequences.api.get_user_course_outline_details.schedule'):
        schedule_processor = processors['schedule']
        schedule = schedule_processor.schedule_data(user_course_outline)

    with function_trace('learning_sequences.api.get_user_course_outline_details.special_exams'):
        special_exams_processor = processors['special_exams']
        special_exam_attempts = special_exams_processor.exam_data(user_course_outline)

    return UserCourseOutlineDetailsData(
        outline=user_course_outline,
        schedule=schedule,
        special_exam_attempts=special_exam_attempts,
    )


def _get_user_course_outline_and_processors(course_key: CourseKey,  # pylint: disable=missing-function-docstring
                                            user: types.User,
                                            at_time: datetime):
    """
    Helper function that runs the outline processors.

    This function returns a UserCourseOutlineData and a dict of outline
    processors that have executed their data loading and returned which
    sequences to remove and which to mark as inaccessible.
    """
    # Record the user separately from the standard user_id that views record,
    # because it's possible to ask for views as other users if you're global
    # staff. Performance is going to vary based on the user we're asking the
    # outline for, not the user who is initiating the request.
    set_custom_attribute('learning_sequences.api.user_id', user.id)

    full_course_outline = get_course_outline(course_key)
    user_can_see_all_content = can_see_all_content(user, course_key)

    # These are processors that alter which sequences are visible to students.
    # For instance, certain sequences that are intentionally hidden or not yet
    # released. These do not need to be run for staff users. This is where we
    # would add in pluggability for OutlineProcessors down the road.
    processor_classes = [
        ('content_gating', ContentGatingOutlineProcessor),
        ('milestones', MilestonesOutlineProcessor),
        ('schedule', ScheduleOutlineProcessor),
        ('special_exams', SpecialExamsOutlineProcessor),
        ('visibility', VisibilityOutlineProcessor),
        ('enrollment', EnrollmentOutlineProcessor),
        ('enrollment_track_partitions', EnrollmentTrackPartitionGroupsOutlineProcessor),
        ('cohorts_partitions', CohortPartitionGroupsOutlineProcessor),
        ('teams_partitions', TeamPartitionGroupsOutlineProcessor),
    ]

    # Run each OutlineProcessor in order to figure out what items we have to
    # remove from the CourseOutline.
    processors = {}
    usage_keys_to_remove = set()
    inaccessible_sequences = set()
    for name, processor_cls in processor_classes:
        # Future optimization: This should be parallelizable (don't rely on a
        # particular ordering).
        processor = processor_cls(course_key, user, at_time)
        processors[name] = processor
        processor.load_data(full_course_outline)
        if not user_can_see_all_content:
            # function_trace lets us see how expensive each processor is being.
            with function_trace(f'learning_sequences.api.outline_processors.{name}'):
                processor_usage_keys_removed = processor.usage_keys_to_remove(full_course_outline)
                processor_inaccessible_sequences = processor.inaccessible_sequences(full_course_outline)
                usage_keys_to_remove |= processor_usage_keys_removed
                inaccessible_sequences |= processor_inaccessible_sequences

    # Open question: Does it make sense to remove a Section if it has no Sequences in it?
    trimmed_course_outline = full_course_outline.remove(usage_keys_to_remove)
    accessible_sequences = frozenset(set(trimmed_course_outline.sequences) - inaccessible_sequences)

    user_course_outline = UserCourseOutlineData(
        base_outline=full_course_outline,
        user=user,
        at_time=at_time,
        accessible_sequences=accessible_sequences,
        **{
            name: getattr(trimmed_course_outline, name)
            for name in [
                'course_key',
                'title',
                'published_at',
                'published_version',
                'entrance_exam_id',
                'sections',
                'self_paced',
                'course_visibility',
                'days_early_for_beta',
            ]
        }
    )

    return user_course_outline, processors


@function_trace('learning_sequences.api.replace_course_outline')
def replace_course_outline(course_outline: CourseOutlineData,
                           content_errors: Optional[List[ContentErrorData]] = None):  # noqa: UP006, UP045
    """
    Replace the model data stored for the Course Outline with the contents of
    course_outline (a CourseOutlineData). Record any content errors.

    This isn't particularly optimized at the moment.
    """
    log.info(
        "Replacing CourseOutline for %s (version %s, %d sequences)",
        course_outline.course_key, course_outline.published_version, len(course_outline.sequences)
    )
    set_custom_attribute('learning_sequences.api.course_id', str(course_outline.course_key))

    if content_errors is None:
        content_errors = []

    with transaction.atomic():
        # Update or create the basic CourseContext...
        course_context = _update_course_context(course_outline)

        # Wipe out the CourseSectionSequences join+ordering table
        course_context.section_sequences.all().delete()

        _update_sections(course_outline, course_context)
        _update_sequences(course_outline, course_context)
        _update_course_section_sequences(course_outline, course_context)
        _update_publish_report(course_outline, content_errors, course_context)


def _update_course_context(course_outline: CourseOutlineData):
    """
    Update CourseContext with given param:course_outline data.
    """
    learning_context, _ = LearningContext.objects.update_or_create(
        context_key=course_outline.course_key,
        defaults={
            'title': course_outline.title,
            'published_at': course_outline.published_at,
            'published_version': course_outline.published_version,
        }
    )
    course_context, created = CourseContext.objects.update_or_create(
        learning_context=learning_context,
        defaults={
            'course_visibility': course_outline.course_visibility.value,
            'days_early_for_beta': course_outline.days_early_for_beta,
            'self_paced': course_outline.self_paced,
            'entrance_exam_id': course_outline.entrance_exam_id,
        }
    )
    if created:
        log.info("Created new CourseContext for %s", course_outline.course_key)
    else:
        log.info("Found CourseContext for %s, updating...", course_outline.course_key)

    return course_context


def _update_sections(course_outline: CourseOutlineData, course_context: CourseContext):
    """
    Add/Update relevant sections
    """
    for ordering, section_data in enumerate(course_outline.sections):
        sec_model, _created = CourseSection.objects.update_or_create(
            course_context=course_context,
            usage_key=section_data.usage_key,
            defaults={
                'title': section_data.title,
                'ordering': ordering,
                'hide_from_toc': section_data.visibility.hide_from_toc,
                'visible_to_staff_only': section_data.visibility.visible_to_staff_only,
            }
        )
        # clear out any user partition group mappings, and remake them...
        _update_user_partition_groups(section_data.user_partition_groups, sec_model)

    # Delete sections that we don't want any more
    section_usage_keys_to_keep = [
        section_data.usage_key for section_data in course_outline.sections
    ]
    CourseSection.objects \
        .filter(course_context=course_context) \
        .exclude(usage_key__in=section_usage_keys_to_keep) \
        .delete()


def _update_sequences(course_outline: CourseOutlineData, course_context: CourseContext):
    """
    Add/Update relevant sequences
    """
    for section_data in course_outline.sections:
        for sequence_data in section_data.sequences:
            LearningSequence.objects.update_or_create(
                learning_context=course_context.learning_context,
                usage_key=sequence_data.usage_key,
                defaults={'title': sequence_data.title}
            )
    LearningSequence.objects \
        .filter(learning_context=course_context.learning_context) \
        .exclude(usage_key__in=course_outline.sequences) \
        .delete()


def _update_course_section_sequences(course_outline: CourseOutlineData, course_context: CourseContext):
    """
    Add/Update relevant course section and sequences
    """
    section_models = {
        section_model.usage_key: section_model
        for section_model
        in CourseSection.objects.filter(course_context=course_context).all()
    }
    sequence_models = {
        sequence_model.usage_key: sequence_model
        for sequence_model
        in LearningSequence.objects.filter(learning_context=course_context.learning_context).all()
    }

    ordering = 0
    for section_data in course_outline.sections:
        for sequence_data in section_data.sequences:
            course_section_sequence, _ = CourseSectionSequence.objects.update_or_create(
                course_context=course_context,
                section=section_models[section_data.usage_key],
                sequence=sequence_models[sequence_data.usage_key],
                defaults={
                    'ordering': ordering,
                    'inaccessible_after_due': sequence_data.inaccessible_after_due,
                    'hide_from_toc': sequence_data.visibility.hide_from_toc,
                    'visible_to_staff_only': sequence_data.visibility.visible_to_staff_only,
                },
            )
            ordering += 1

            # If a sequence is an exam, update or create an exam record
            if bool(sequence_data.exam):
                CourseSequenceExam.objects.update_or_create(
                    course_section_sequence=course_section_sequence,
                    defaults={
                        'is_practice_exam': sequence_data.exam.is_practice_exam,
                        'is_proctored_enabled': sequence_data.exam.is_proctored_enabled,
                        'is_time_limited': sequence_data.exam.is_time_limited,
                    },
                )
            else:
                # Otherwise, delete any exams associated with it
                CourseSequenceExam.objects.filter(course_section_sequence=course_section_sequence).delete()

            # clear out any user partition group mappings, and remake them...
            _update_user_partition_groups(sequence_data.user_partition_groups, course_section_sequence)


def _update_user_partition_groups(upg_data: Dict[int, FrozenSet[int]],  # noqa: UP006
                                  model_obj: Union[CourseSection, CourseSectionSequence]):  # noqa: UP007
    """
    Replace UserPartitionGroups associated with this content with `upg_data`.
    """
    model_obj.new_user_partition_groups.all().delete()
    if upg_data:
        for partition_id, group_ids in upg_data.items():
            for group_id in group_ids:
                upg, _ = UserPartitionGroup.objects.get_or_create(
                    partition_id=partition_id, group_id=group_id
                )
                model_obj.new_user_partition_groups.add(upg)


def _update_publish_report(course_outline: CourseOutlineData,
                           content_errors: List[ContentErrorData],  # noqa: UP006
                           course_context: CourseContext):
    """
    Record ContentErrors for this course publish. Deletes previous errors.
    """
    set_custom_attribute('learning_sequences.api.num_content_errors', len(content_errors))
    learning_context = course_context.learning_context
    try:
        # Normal path if we're updating a PublishReport
        publish_report = learning_context.publish_report
        publish_report.num_errors = len(content_errors)
        publish_report.num_sections = len(course_outline.sections)
        publish_report.num_sequences = len(course_outline.sequences)
        publish_report.content_errors.all().delete()
    except PublishReport.DoesNotExist:
        # Case where we're creating it for the first time.
        publish_report = PublishReport(
            learning_context=learning_context,
            num_errors=len(content_errors),
            num_sections=len(course_outline.sections),
            num_sequences=len(course_outline.sequences),
        )

    publish_report.save()
    publish_report.content_errors.bulk_create([
        ContentError(
            publish_report=publish_report,
            usage_key=error_data.usage_key,
            message=error_data.message,
        )
        for error_data in content_errors
    ])
