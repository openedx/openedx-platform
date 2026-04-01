"""
Content library signal handlers.
"""

import logging

from django.conf import settings
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from opaque_keys import InvalidKeyError, OpaqueKey
from opaque_keys.edx.locator import LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_events.content_authoring.data import ContentObjectChangedData, LibraryCollectionData
from openedx_events.content_authoring.signals import (
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED,
    LIBRARY_COLLECTION_CREATED,
    LIBRARY_COLLECTION_DELETED,
    LIBRARY_COLLECTION_UPDATED
)
from openedx_content.api import get_components, get_containers
from openedx_content.models_api import Collection, CollectionPublishableEntity, PublishableEntity

from lms.djangoapps.grades.api import signals as grades_signals

from .api import library_collection_locator, library_component_usage_key, library_container_locator
from .models import ContentLibrary, LtiGradedResource

log = logging.getLogger(__name__)


@receiver(grades_signals.PROBLEM_WEIGHTED_SCORE_CHANGED)
def score_changed_handler(sender, **kwargs):  # pylint: disable=unused-argument
    """
    Match the score event to an LTI resource and update.
    """

    lti_enabled = (settings.FEATURES.get('ENABLE_CONTENT_LIBRARIES')
                   and settings.FEATURES.get('ENABLE_CONTENT_LIBRARIES_LTI_TOOL'))
    if not lti_enabled:
        return

    modified = kwargs.get('modified')
    usage_id = kwargs.get('usage_id')
    user_id = kwargs.get('user_id')
    weighted_earned = kwargs.get('weighted_earned')
    weighted_possible = kwargs.get('weighted_possible')

    if None in (modified, usage_id, user_id, weighted_earned, weighted_possible):
        log.debug("LTI 1.3: Score Signal: Missing a required parameters, "
                  "ignoring: kwargs=%s", kwargs)
        return
    try:
        usage_key = LibraryUsageLocatorV2.from_string(usage_id)
    except InvalidKeyError:
        log.debug("LTI 1.3: Score Signal: Not a content libraries v2 usage key, "
                  "ignoring: usage_id=%s", usage_id)
        return
    try:
        resource = LtiGradedResource.objects.get_from_user_id(
            user_id, usage_key=usage_key
        )
    except LtiGradedResource.DoesNotExist:
        log.debug("LTI 1.3: Score Signal: Unknown resource, ignoring: kwargs=%s",
                  kwargs)
    else:
        resource.update_score(weighted_earned, weighted_possible, modified)
        log.info("LTI 1.3: Score Signal: Grade upgraded: resource; %s",
                 resource)


@receiver(post_save, sender=Collection, dispatch_uid="library_collection_saved")
def library_collection_saved(sender, instance, created, **kwargs):
    """
    Raises LIBRARY_COLLECTION_CREATED if the Collection is new,
    or LIBRARY_COLLECTION_UPDATED if updated an existing Collection.
    """
    try:
        library = ContentLibrary.objects.get(learning_package_id=instance.learning_package_id)
    except ContentLibrary.DoesNotExist:
        log.error("{instance} is not associated with a content library.")
        return

    if created:
        # .. event_implemented_name: LIBRARY_COLLECTION_CREATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.created.v1
        LIBRARY_COLLECTION_CREATED.send_event(
            library_collection=LibraryCollectionData(
                collection_key=library_collection_locator(
                    library_key=library.library_key,
                    collection_key=instance.key,
                ),
            )
        )
    else:
        # .. event_implemented_name: LIBRARY_COLLECTION_UPDATED
        # .. event_type: org.openedx.content_authoring.content_library.collection.updated.v1
        LIBRARY_COLLECTION_UPDATED.send_event(
            library_collection=LibraryCollectionData(
                collection_key=library_collection_locator(
                    library_key=library.library_key,
                    collection_key=instance.key,
                ),
            )
        )


@receiver(post_delete, sender=Collection, dispatch_uid="library_collection_deleted")
def library_collection_deleted(sender, instance, **kwargs):
    """
    Raises LIBRARY_COLLECTION_DELETED for the deleted Collection.
    """
    try:
        library = ContentLibrary.objects.get(learning_package_id=instance.learning_package_id)
    except ContentLibrary.DoesNotExist:
        log.error("{instance} is not associated with a content library.")
        return

    # .. event_implemented_name: LIBRARY_COLLECTION_DELETED
    # .. event_type: org.openedx.content_authoring.content_library.collection.deleted.v1
    LIBRARY_COLLECTION_DELETED.send_event(
        library_collection=LibraryCollectionData(
            collection_key=library_collection_locator(
                library_key=library.library_key,
                collection_key=instance.key,
            ),
        )
    )


def _library_collection_entity_changed(
    publishable_entity: PublishableEntity,
    library_key: LibraryLocatorV2 | None = None,
) -> None:
    """
    Sends a CONTENT_OBJECT_ASSOCIATIONS_CHANGED event for the entity.
    """
    if not library_key:
        try:
            library = ContentLibrary.objects.get(
                learning_package_id=publishable_entity.learning_package_id,
            )
        except ContentLibrary.DoesNotExist:
            log.error("{publishable_entity} is not associated with a content library.")
            return

        library_key = library.library_key

    assert library_key

    opaque_key: OpaqueKey

    if hasattr(publishable_entity, 'component'):
        opaque_key = library_component_usage_key(
            library_key,
            publishable_entity.component,
        )
    elif hasattr(publishable_entity, 'container'):
        opaque_key = library_container_locator(
            library_key,
            publishable_entity.container,
        )
    else:
        log.error("Unknown publishable entity type: %s", publishable_entity)
        return

    # .. event_implemented_name: CONTENT_OBJECT_ASSOCIATIONS_CHANGED
    # .. event_type: org.openedx.content_authoring.content.object.associations.changed.v1
    CONTENT_OBJECT_ASSOCIATIONS_CHANGED.send_event(
        content_object=ContentObjectChangedData(
            object_id=str(opaque_key),
            changes=["collections"],
        ),
    )


@receiver(post_save, sender=CollectionPublishableEntity, dispatch_uid="library_collection_entity_saved")
def library_collection_entity_saved(sender, instance, created, **kwargs):
    """
    Sends a CONTENT_OBJECT_ASSOCIATIONS_CHANGED event for components added to a collection.
    """
    if created:
        _library_collection_entity_changed(instance.entity)


@receiver(post_delete, sender=CollectionPublishableEntity, dispatch_uid="library_collection_entity_deleted")
def library_collection_entity_deleted(sender, instance, **kwargs):
    """
    Sends a CONTENT_OBJECT_ASSOCIATIONS_CHANGED event for components removed from a collection.
    """
    # Only trigger component updates if CollectionPublishableEntity was cascade deleted due to deletion of a collection.
    if isinstance(kwargs.get('origin'), Collection):
        _library_collection_entity_changed(instance.entity)


@receiver(m2m_changed, sender=CollectionPublishableEntity, dispatch_uid="library_collection_entities_changed")
def library_collection_entities_changed(sender, instance, action, pk_set, **kwargs):
    """
    Sends a CONTENT_OBJECT_ASSOCIATIONS_CHANGED event for components added/removed/cleared from a collection.
    """
    if action not in ["post_add", "post_remove", "post_clear"]:
        return

    try:
        library = ContentLibrary.objects.get(
            learning_package_id=instance.learning_package_id,
        )
    except ContentLibrary.DoesNotExist:
        log.error("{instance} is not associated with a content library.")
        return

    if isinstance(instance, PublishableEntity):
        _library_collection_entity_changed(instance, library.library_key)
        return

    # When action=="post_clear", pk_set==None
    # Since the collection instance now has an empty entities set,
    # we don't know which ones were removed, so we need to update associations for all library
    # components and containers.
    components = get_components(instance.learning_package_id)
    containers = get_containers(instance.learning_package_id)
    if pk_set:
        components = components.filter(pk__in=pk_set)
        containers = containers.filter(pk__in=pk_set)

    for entity in list(components.all()) + list(containers.all()):
        _library_collection_entity_changed(entity.publishable_entity, library.library_key)
