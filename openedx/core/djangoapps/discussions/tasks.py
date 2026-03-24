"""
Tasks for discussions
"""
import logging

from celery import shared_task
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey
from openedx_events.learning.data import CourseDiscussionConfigurationData, DiscussionTopicContext
from openedx_events.learning.signals import COURSE_DISCUSSIONS_CHANGED

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from .config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS

from .models import DiscussionsConfiguration, Provider, DiscussionTopicLink
from .utils import get_accessible_discussion_xblocks_by_course_id

log = logging.getLogger(__name__)


@shared_task
@set_code_owner_attribute
def update_discussions_settings_from_course_task(course_key_str: str, discussable_units=None):
    """
    Celery task that creates or updates discussions settings for a course.

    Args:
        course_key_str (str): course key string
        discussable_units (List[UsageKey]): list of discussable units
    """
    course_key = CourseKey.from_string(course_key_str)
    config_data = update_discussions_settings_from_course(course_key, discussable_units)
    # .. event_implemented_name: COURSE_DISCUSSIONS_CHANGED
    # .. event_type: org.openedx.learning.discussions.configuration.changed.v1
    COURSE_DISCUSSIONS_CHANGED.send_event(configuration=config_data)


def update_discussions_settings_from_course(
    course_key: CourseKey,
    discussable_units=None
) -> CourseDiscussionConfigurationData:
    """
    When there are changes to a course, construct a new data structure containing all the context needed to update the
    course's discussion settings in the database.

    Args:
        course_key (CourseKey): The course that was recently updated.
        discussable_units (List[UsageKey]): list of discussable units

    Returns:
        (CourseDiscussionConfigurationData): structured discussion configuration data.
    """
    log.info(f"Updating discussion settings for course: {course_key}")
    store = modulestore()

    discussions_config = DiscussionsConfiguration.get(course_key)
    supports_in_context = discussions_config.supports_in_context_discussions()
    provider_type = discussions_config.provider_type

    with store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
        course = store.get_course(course_key)
        enable_in_context = discussions_config.enable_in_context
        provider_config = discussions_config.plugin_configuration
        unit_level_visibility = discussions_config.unit_level_visibility
        enable_graded_units = discussions_config.enable_graded_units

        contexts = []
        if supports_in_context:
            sorted_topics = sorted(
                course.discussion_topics.items(),
                key=lambda item: item[1].get("sort_key", item[0]),
            )
            contexts = []
            for idx, (topic_name, topic_config) in enumerate(sorted_topics):
                if topic_config.get('id', None):
                    context = DiscussionTopicContext(
                        title=topic_name,
                        external_id=topic_config.get('id', None),
                        ordering=idx
                    )
                    contexts.append(context)
            if enable_in_context:
                discussable_units = get_discussable_units(
                    course,
                    enable_graded_units,
                    discussable_units
                )
                contexts.extend(list(discussable_units))

        # Derive enabled from the discussion tab's is_hidden state.
        # When a course is imported or rerun, course.tabs are copied verbatim
        # from the source but DiscussionsConfiguration is not, so we pass the
        # tab state through config_data for the handler to reconcile.
        enabled = True
        if course:
            for tab in course.tabs:
                if getattr(tab, 'tab_id', None) == 'discussion':
                    enabled = not tab.is_hidden
                    break

        config_data = CourseDiscussionConfigurationData(
            course_key=course_key,
            enabled=enabled,
            enable_in_context=enable_in_context,
            enable_graded_units=enable_graded_units,
            unit_level_visibility=unit_level_visibility,
            provider_type=provider_type,
            plugin_configuration=provider_config,
            contexts=contexts,
        )
    return config_data


def get_discussable_units(course, enable_graded_units, discussable_units=None):
    """
    Get all the units in the course that are discussable.
    """
    idx = 99
    store = modulestore()
    for section in get_sections(course):
        for subsection in get_subsections(section):
            with store.bulk_operations(course.id, emit_signals=False):
                for unit in get_units(subsection):
                    idx += 1
                    if not is_discussable_unit(unit, store, enable_graded_units, subsection):
                        if unit.discussion_enabled:
                            log.info(f"Unit {unit.location} of course {course.id} has discussion enabled "
                                     f"but is not discussable")
                        continue
                    # check if discussable_units is type of list and discussable_units is empty
                    # it means if discussable_units is empty then we should not create any topic
                    if isinstance(discussable_units, list) and not discussable_units:
                        continue

                    if isinstance(discussable_units, list) and (str(unit.location) not in discussable_units):
                        continue

                    yield DiscussionTopicContext(
                        usage_key=unit.location,
                        title=unit.display_name,
                        group_id=None,
                        ordering=idx,
                        context={
                            "section": section.display_name,
                            "subsection": subsection.display_name,
                            "unit": unit.display_name,
                        },
                    )


def get_sections(course):
    """
    Get sections for given course
    """
    for section in course.get_children():
        if section.location.block_type == "chapter":
            yield section


def get_subsections(section):
    """
    Get subsections for given section
    """
    for subsection in section.get_children():
        if subsection.location.block_type == "sequential":
            yield subsection


def get_units(subsection):
    """
    Get units for given subsection
    """
    for unit in subsection.get_children():
        if unit.location.block_type == 'vertical':
            yield unit


def is_discussable_unit(unit, store, enable_graded_units, subsection):
    """
    Check if unit should have discussion's topic
    """
    if not store.has_published_version(unit):
        return False

    if not getattr(unit, "discussion_enabled", False):
        return False

    if subsection.graded and not enable_graded_units:
        return False

    if subsection.is_practice_exam or subsection.is_proctored_enabled or subsection.is_time_limited:
        return False

    return True


def update_unit_discussion_state_from_discussion_blocks(
    course_key: CourseKey,
    user_id: int,
    force=False,
    async_topics=True
) -> None:
    """
    Migrate existing courses to the new mechanism for linking discussion to units.

    This will iterate over an existing course's discussion xblocks and mark the units
    they are in as discussable.

    Args:
        course_key (CourseKey): CourseKey for course.
        user_id (int): User id for the user performing this operation.
        force (bool): Force migration of data even if not using legacy provider
        async_topics (bool): If True, run the task asynchronously.
    """
    store = modulestore()
    course = store.get_course(course_key)
    # The provider information has been written to both `provider_type` and `provider`.
    # Both of these serve the same purpose and this is an accident of early development.
    # The `provider_type` key is now treated as read-only to allow existing values
    # to be respected while moving to the `provider` key in the future.
    provider = course.discussions_settings.get(
        'provider_type',
        course.discussions_settings.get('provider', None),
    )
    # Only migrate to the new discussion provider if the current provider is the legacy provider.
    log.info(f"Current provider for {course_key} is {provider}")
    if provider is not None and provider != Provider.LEGACY and not force:
        return

    log.info(f"Migrating legacy discussion config for {course_key}")

    with store.bulk_operations(course_key):
        discussion_blocks = get_accessible_discussion_xblocks_by_course_id(course_key, include_all=True)
        discussable_units = {
            discussion_block.parent
            for discussion_block in discussion_blocks
            if discussion_block.parent.block_type == 'vertical'
        }
        log.info(f"Found {len(discussable_units)} discussable unit(s) in {course_key}")
        verticals = store.get_items(course_key, qualifiers={'block_type': 'vertical'})
        graded_subsections = {
            block.location
            for block in store.get_items(
                course_key,
                qualifies={'block_type': 'sequential'},
                settings={'graded': True}
            )
        }
        subsections_with_discussions = set()
        for vertical in verticals:
            if vertical.location in discussable_units:
                vertical.discussion_enabled = True
                subsections_with_discussions.add(vertical.parent)
            else:
                vertical.discussion_enabled = False
            store.update_item(vertical, user_id)

    # There should be no existing topics before this job runs.
    # When jobs run out of sync topics for all are created.
    # Delete all discussion topic links for the course.
    DiscussionTopicLink.objects.filter(context_key=course_key).delete()

    # If there are any graded subsections that have discussion units,
    # then enable discussions for graded subsections for the course
    enable_graded_subsections = bool(graded_subsections & subsections_with_discussions)

    # If the new discussions experience is enabled globally,
    # then also set up the new provider for the course.
    if ENABLE_NEW_STRUCTURE_DISCUSSIONS.is_enabled():
        log.info(f"New structure is enabled, also updating {course_key} to use new provider")
        course = store.get_course(course_key)
        provider = Provider.OPEN_EDX
        course.discussions_settings['provider'] = provider
        course.discussions_settings['provider_type'] = provider
        course.discussions_settings['enable_graded_units'] = enable_graded_subsections
        course.discussions_settings['unit_level_visibility'] = True
        store.update_item(course, user_id)
        discussion_config = DiscussionsConfiguration.get(course_key)
        discussion_config.provider_type = provider
        discussion_config.enable_graded_units = enable_graded_subsections
        discussion_config.unit_level_visibility = True
        discussion_config.save()

    if async_topics:
        # added delay of 30 minutes to allow for the course to be published
        update_discussions_settings_from_course_task.apply_async(
            args=[str(course_key), [str(unit) for unit in discussable_units]],
            countdown=1800,
        )
    else:
        update_discussions_settings_from_course_task(
            str(course_key),
            [str(unit) for unit in discussable_units],
        )
