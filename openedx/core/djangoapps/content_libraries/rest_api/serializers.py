"""
Serializers for the content libraries REST API
"""
# pylint: disable=abstract-method
import json
import logging

from django.core.validators import validate_unicode_slug
from opaque_keys import InvalidKeyError, OpaqueKey
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryUsageLocatorV2
from openedx_content.models_api import Collection, LearningPackage
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from user_tasks.models import UserTaskStatus

from openedx.core.djangoapps.content_libraries import api
from openedx.core.djangoapps.content_libraries.constants import ALL_RIGHTS_RESERVED, LICENSE_OPTIONS
from openedx.core.djangoapps.content_libraries.models import (
    ContentLibrary,
    ContentLibraryPermission,
)
from openedx.core.djangoapps.content_libraries.tasks import LibraryRestoreTask

from .. import permissions

DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

log = logging.getLogger(__name__)


class ContentLibraryMetadataSerializer(serializers.Serializer):
    """
    Serializer for ContentLibraryMetadata
    """
    # We rename the primary key field to "id" in the REST API since API clients
    # often implement magic functionality for fields with that name, and "key"
    # is a reserved prop name in React. This 'id' field is a string that
    # begins with 'lib:'. (The numeric ID of the ContentLibrary object in MySQL
    # is not exposed via this API.)
    id = serializers.CharField(source="key", read_only=True)
    org = serializers.SlugField(source="key.org")
    slug = serializers.CharField(source="key.slug", validators=(validate_unicode_slug, ))
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    learning_package = serializers.PrimaryKeyRelatedField(queryset=LearningPackage.objects.all(), required=False)
    num_blocks = serializers.IntegerField(read_only=True)
    last_published = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    published_by = serializers.CharField(read_only=True)
    last_draft_created = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    last_draft_created_by = serializers.CharField(read_only=True)
    allow_public_learning = serializers.BooleanField(default=False)
    allow_public_read = serializers.BooleanField(default=False)
    has_unpublished_changes = serializers.BooleanField(read_only=True)
    has_unpublished_deletes = serializers.BooleanField(read_only=True)
    license = serializers.ChoiceField(choices=LICENSE_OPTIONS, default=ALL_RIGHTS_RESERVED)
    can_edit_library = serializers.SerializerMethodField()
    created = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    updated = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)

    def get_can_edit_library(self, obj):
        """
        Verifies if the user in request has permission
        to edit a library.
        """
        request = self.context.get('request', None)
        if request is None:
            return False

        user = request.user

        if not user:
            return False

        library_obj = ContentLibrary.objects.get_by_key(obj.key)
        return api.user_has_permission_across_lib_authz_systems(
            user, permissions.CAN_EDIT_THIS_CONTENT_LIBRARY, library_obj)


class ContentLibraryUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating an existing content library
    """
    # These are the only fields that support changes:
    title = serializers.CharField()
    description = serializers.CharField()
    allow_public_learning = serializers.BooleanField()
    allow_public_read = serializers.BooleanField()
    license = serializers.ChoiceField(choices=LICENSE_OPTIONS)


class ContentLibraryPermissionLevelSerializer(serializers.Serializer):
    """
    Serializer for the "Access Level" of a ContentLibraryPermission object.

    This is used when updating a user or group's permissions re some content
    library.
    """
    access_level = serializers.ChoiceField(choices=ContentLibraryPermission.ACCESS_LEVEL_CHOICES)


class ContentLibraryAddPermissionByEmailSerializer(serializers.Serializer):
    """
    Serializer for adding a new user and granting their access level via their email address.
    """
    access_level = serializers.ChoiceField(choices=ContentLibraryPermission.ACCESS_LEVEL_CHOICES)
    email = serializers.EmailField()


class ContentLibraryPermissionSerializer(ContentLibraryPermissionLevelSerializer):
    """
    Serializer for a ContentLibraryPermission object, which grants either a user
    or a group permission to view a content library.
    """
    email = serializers.EmailField(source="user.email", read_only=True, default=None)
    username = serializers.CharField(source="user.username", read_only=True, default=None)
    group_name = serializers.CharField(source="group.name", allow_null=True, allow_blank=False, default=None)


class ContentLibraryFilterSerializer(serializers.Serializer):
    """
    Base serializer for filtering listings on the content library APIs.
    """
    text_search = serializers.CharField(default=None, required=False)
    org = serializers.CharField(default=None, required=False)
    order = serializers.CharField(default=None, required=False)


class CollectionMetadataSerializer(serializers.Serializer):
    """
    Serializer for CollectionMetadata
    """
    key = serializers.CharField()
    title = serializers.CharField()


class PublishableItemSerializer(serializers.Serializer):
    """
    Serializer for any PublishableItem in a library (XBlock, Container, etc.)
    """
    id = serializers.SerializerMethodField()
    display_name = serializers.CharField()
    published_display_name = serializers.CharField(required=False)
    tags_count = serializers.IntegerField(read_only=True)
    last_published = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    published_by = serializers.CharField(read_only=True)
    last_draft_created = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    last_draft_created_by = serializers.CharField(read_only=True)
    has_unpublished_changes = serializers.BooleanField(read_only=True)
    created = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    created_by = serializers.CharField(read_only=True, allow_null=True)
    modified = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)

    # When creating a new XBlock in a library, the slug becomes the ID part of
    # the definition key and usage key:
    slug = serializers.CharField(write_only=True)

    collections = CollectionMetadataSerializer(many=True, required=False)
    can_stand_alone = serializers.BooleanField(read_only=True)

    # Fields that are _sometimes_ set, depending on the subclass:
    block_type = serializers.CharField(source="usage_key.block_type", required=False)
    container_type = serializers.CharField(source="container_key.block_type", required=False)

    def get_id(self, obj) -> str:
        """ Get a unique ID for this PublishableItem """
        if hasattr(obj, "usage_key"):
            return str(obj.usage_key)
        elif hasattr(obj, "container_key"):
            return str(obj.container_key)
        return ""


class LibraryXBlockMetadataSerializer(PublishableItemSerializer):
    """
    Serializer for LibraryXBlockMetadata
    """
    block_type = serializers.CharField(source="usage_key.block_type")


class LibraryHistoryContributorSerializer(serializers.Serializer):
    """
    Serializer for a contributor in a publish history group.
    """
    username = serializers.CharField(read_only=True)
    profile_image_urls = serializers.DictField(child=serializers.CharField(), read_only=True)


class LibraryHistoryEntrySerializer(serializers.Serializer):
    """
    Serializer for a single entry in the history of a library item.
    """
    contributor = LibraryHistoryContributorSerializer(allow_null=True, read_only=True)
    changed_at = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    title = serializers.CharField(read_only=True)
    item_type = serializers.CharField(read_only=True)
    action = serializers.CharField(read_only=True)
    old_version = serializers.IntegerField(read_only=True)
    new_version = serializers.IntegerField(read_only=True, allow_null=True)


class UsageKeyV2Serializer(serializers.BaseSerializer):
    """
    Serializes a library Component (XBlock) key.
    """
    def to_representation(self, value: LibraryUsageLocatorV2) -> str:
        """
        Returns the LibraryUsageLocatorV2 value as a string.
        """
        return str(value)

    def to_internal_value(self, value: str) -> LibraryUsageLocatorV2:
        """
        Returns a LibraryUsageLocatorV2 from the string value.

        Raises ValidationError if invalid LibraryUsageLocatorV2.
        """
        try:
            return LibraryUsageLocatorV2.from_string(value)
        except InvalidKeyError as err:
            raise ValidationError from err


class OpaqueKeySerializer(serializers.BaseSerializer):
    """
    Serializes a OpaqueKey with the correct class.
    """
    def to_representation(self, value: OpaqueKey) -> str:
        """
        Returns the OpaqueKey value as a string.
        """
        return str(value)

    def to_internal_value(self, value: str) -> OpaqueKey:
        """
        Returns a LibraryUsageLocatorV2 or a LibraryContainerLocator from the string value.

        Raises ValidationError if invalid UsageKeyV2 or LibraryContainerLocator.
        """
        try:
            return LibraryUsageLocatorV2.from_string(value)
        except InvalidKeyError:
            try:
                return LibraryContainerLocator.from_string(value)
            except InvalidKeyError as err:
                raise ValidationError from err


class DirectPublishedEntitySerializer(serializers.Serializer):
    """
    Serializer for one entity the user directly requested to publish (direct=True).
    """
    entity_key = OpaqueKeySerializer(read_only=True)
    title = serializers.CharField(read_only=True)
    entity_type = serializers.CharField(read_only=True)


class LibraryPublishHistoryGroupSerializer(serializers.Serializer):
    """
    Serializer for a publish event summary in the publish history of a library item.
    """
    publish_log_uuid = serializers.UUIDField(read_only=True)
    published_by = serializers.SerializerMethodField()
    published_at = serializers.DateTimeField(format=DATETIME_FORMAT, read_only=True)
    contributors = LibraryHistoryContributorSerializer(many=True, read_only=True)
    direct_published_entities = DirectPublishedEntitySerializer(many=True, read_only=True)
    scope_entity_key = OpaqueKeySerializer(read_only=True, allow_null=True)

    def get_published_by(self, obj) -> str | None:
        return obj.published_by.username if obj.published_by else None


class LibraryXBlockTypeSerializer(serializers.Serializer):
    """
    Serializer for LibraryXBlockType
    """
    block_type = serializers.CharField()
    display_name = serializers.CharField()


class LibraryXBlockCreationSerializer(serializers.Serializer):
    """
    Serializer for adding a new XBlock to a content library
    """
    # Parent block: optional usage key of an existing block to add this child
    # block to. TODO: Remove this, because we don't support it.
    parent_block = serializers.CharField(required=False)

    block_type = serializers.CharField()

    # TODO: Rename to ``block_id`` or ``slug``. The openedx_content XBlock runtime
    # doesn't use definition_ids, but this field is really just about requesting
    # a specific block_id, e.g. the "best_tropical_vacation_spots" portion of a
    # problem with UsageKey:
    #   lb:Axim:VacationsLib:problem:best_tropical_vacation_spots
    #
    # It doesn't look like the frontend actually uses this to put meaningful
    # slugs at the moment, but hopefully we can change this soon.
    definition_id = serializers.CharField(validators=(validate_unicode_slug, ))

    # Optional param specified when pasting data from clipboard instead of
    # creating new block from scratch
    staged_content = serializers.CharField(required=False)

    # Optional param defaults to True, set to False if block is being created under a container.
    can_stand_alone = serializers.BooleanField(required=False, default=True)


class LibraryXBlockOlxSerializer(serializers.Serializer):
    """
    Serializer for representing an XBlock's OLX
    """
    olx = serializers.CharField()
    version_num = serializers.IntegerField(read_only=True, required=False)


class LibraryXBlockStaticFileSerializer(serializers.Serializer):
    """
    Serializer representing a static file associated with an XBlock

    Serializes a LibraryXBlockStaticFile (or a BundleFile)
    """
    path = serializers.CharField()
    # Publicly accessible URL where the file can be downloaded.
    # Must be an absolute URL.
    url = serializers.URLField()
    size = serializers.IntegerField(min_value=0)


class LibraryXBlockStaticFilesSerializer(serializers.Serializer):
    """
    Serializer representing a static file associated with an XBlock

    Serializes a LibraryXBlockStaticFile (or a BundleFile)
    """
    files = LibraryXBlockStaticFileSerializer(many=True)


class LibraryContainerMetadataSerializer(PublishableItemSerializer):
    """
    Serializer for Containers like Sections, Subsections, Units

    Converts from ContainerMetadata to JSON-compatible data
    """
    container_type_code = serializers.CharField(source="container_key.container_type")
    # Deprecated (ambiguously named). Identical to container_type_code.
    container_type = serializers.CharField(source="container_key.container_type", required=False)

    # When creating a new container in a library, the slug becomes the ID part of
    # the definition key and usage key:
    slug = serializers.CharField(write_only=True, required=False)

    def to_internal_value(self, data: dict):
        """
        Convert JSON-ish data back to native python types.
        Returns a dictionary, not a ContainerMetadata instance.
        """
        if "container_type_code" not in data:
            data["container_type_code"] = data.get("container_type")  # Support deprecated field name
        validated_data = super().to_internal_value(data)
        # If creating a new container, the above function results in:
        # {'display_name': '...', 'container_key': {'container_type': 'unit'}}
        # Fix that:
        validated_data["container_type_code"] = validated_data["container_key"]["container_type"]
        del validated_data["container_key"]
        return validated_data


class LibraryContainerUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating metadata for Containers like Sections, Subsections, Units
    """
    display_name = serializers.CharField()



class ContentLibraryCollectionSerializer(serializers.ModelSerializer):
    """
    Serializer for a Content Library Collection
    """
    # Expose Collection.collection_code as "key" to preserve the REST API field name.
    # https://github.com/openedx/openedx-platform/issues/38406
    key = serializers.CharField(source='collection_code')

    class Meta:
        model = Collection
        exclude = ['collection_code']


class ContentLibraryCollectionUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating a Collection in a Content Library
    """

    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True)


class ContentLibraryItemContainerKeysSerializer(serializers.Serializer):
    """
    Serializer for adding/removing items to/from a Container.
    """

    usage_keys = serializers.ListField(child=OpaqueKeySerializer(), allow_empty=False)


class ContentLibraryItemKeysSerializer(serializers.Serializer):
    """
    Serializer for adding/removing items to/from a Collection.
    """

    usage_keys = serializers.ListField(child=OpaqueKeySerializer(), allow_empty=False)


class ContentLibraryItemCollectionsUpdateSerializer(serializers.Serializer):
    """
    Serializer for adding/removing Collections to/from a Library Item (component, unit, etc..).
    """

    collection_keys = serializers.ListField(child=serializers.CharField(), allow_empty=True)


class UnionLibraryMetadataSerializer(serializers.Serializer):
    """
    Union serializer for swagger api response.
    """

    type_a = LibraryXBlockMetadataSerializer(many=True, required=False)
    type_b = LibraryContainerMetadataSerializer(many=True, required=False)


class ContainerHierarchyMemberSerializer(serializers.Serializer):
    """
    Serializer for the members of a hierarchy, which can be either Components or Containers.
    """
    id = OpaqueKeySerializer()
    display_name = serializers.CharField()
    has_unpublished_changes = serializers.BooleanField()


class ContainerHierarchySerializer(serializers.Serializer):
    """
    Serializer which represents the full hierarchy of containers and components that contain and are contained by a
    given library container or library block.
    """
    sections = serializers.ListField(child=ContainerHierarchyMemberSerializer(), allow_empty=True)
    subsections = serializers.ListField(child=ContainerHierarchyMemberSerializer(), allow_empty=True)
    units = serializers.ListField(child=ContainerHierarchyMemberSerializer(), allow_empty=True)
    components = serializers.ListField(child=ContainerHierarchyMemberSerializer(), allow_empty=True)
    object_key = OpaqueKeySerializer()


class LibraryBackupResponseSerializer(serializers.Serializer):
    """
    Serializer for the response after requesting a backup of a content library.
    """
    task_id = serializers.CharField()


class LibraryBackupTaskStatusSerializer(serializers.Serializer):
    """
    Serializer for checking the status of a library backup task.
    """
    state = serializers.CharField()
    url = serializers.FileField(source='file', allow_null=True, use_url=True)


class LibraryRestoreFileSerializer(serializers.Serializer):
    """
    Serializer for restoring a library from a backup file.
    """
    # input only fields
    file = serializers.FileField(write_only=True, help_text="A ZIP file containing a library backup.")

    # output only fields
    task_id = serializers.UUIDField(read_only=True)

    def validate_file(self, value):
        """
        Validate that the uploaded file is a ZIP file.
        """
        if value.content_type != 'application/zip':
            raise serializers.ValidationError("Only ZIP files are allowed.")
        return value


class LibraryRestoreTaskRequestSerializer(serializers.Serializer):
    """
    Serializer for requesting the status of a library restore task.
    """
    task_id = serializers.UUIDField(write_only=True, help_text="The ID of the restore task to check.")


class RestoreSuccessDataSerializer(serializers.Serializer):
    """
    Serializer for the data returned upon successful restoration of a library.
    """
    learning_package_id = serializers.IntegerField(source="lp_restored_data.id")
    title = serializers.CharField(source="lp_restored_data.title")
    org = serializers.SerializerMethodField()
    slug = serializers.SerializerMethodField()

    # The `package_ref` is a unique temporary key assigned to the learning
    # package during the restore process, whereas the `archive_package_ref` is
    # the original key of the learning package from the backup.  The temporary
    # learning package_ref is replaced with a standard key once it is added to a
    # content library.
    key = serializers.CharField(source="lp_restored_data.package_ref")
    archive_key = serializers.CharField(source="lp_restored_data.archive_package_ref")

    containers = serializers.IntegerField(source="lp_restored_data.num_containers")
    components = serializers.IntegerField(source="lp_restored_data.num_components")
    collections = serializers.IntegerField(source="lp_restored_data.num_collections")
    sections = serializers.IntegerField(source="lp_restored_data.num_sections")
    subsections = serializers.IntegerField(source="lp_restored_data.num_subsections")
    units = serializers.IntegerField(source="lp_restored_data.num_units")

    created_on_server = serializers.CharField(source="backup_metadata.original_server", required=False)
    created_at = serializers.DateTimeField(source="backup_metadata.created_at", format=DATETIME_FORMAT)
    created_by = serializers.SerializerMethodField()

    def get_org(self, obj) -> str:
        """
        The org code/slug, as parsed from archive_package_ref, or "unknown" if unparseable.
        """
        return obj["lp_restored_data"]["archive_org_code"] or "unknown"

    def get_slug(self, obj) -> str:
        """
        The library code/slug, as parsed from archive_package_ref, or "unknown" if unparseable.
        """
        return obj["lp_restored_data"]["archive_package_code"] or "unknown"

    def get_created_by(self, obj):
        """
        Get the user information of the archive creator, if available.

        The information is stored in the backup metadata of the archive and references
        a user that may not exist in the system where the restore is being performed.
        """
        username = obj["backup_metadata"].get("created_by")
        email = obj["backup_metadata"].get("created_by_email")
        return {"username": username, "email": email}


class LibraryRestoreTaskResultSerializer(serializers.Serializer):
    """
    Serializer for the result of a library restore task.
    """
    state = serializers.CharField()
    result = RestoreSuccessDataSerializer(required=False, allow_null=True, default=None)
    error = serializers.CharField(required=False, allow_blank=True, default=None)
    error_log = serializers.FileField(source='error_log_url', allow_null=True, use_url=True, default=None)

    @classmethod
    def from_task_status(cls, task_status, request):
        """Build serializer input from task status object."""

        # If the task did not complete, just return the state.
        if task_status.state not in {UserTaskStatus.SUCCEEDED, UserTaskStatus.FAILED}:
            return cls({
                "state": task_status.state,
            })

        artifact_name = LibraryRestoreTask.ARTIFACT_NAMES.get(task_status.state, '')
        artifact = task_status.artifacts.filter(name=artifact_name).first()

        # If the task failed, include the log artifact if it exists
        if task_status.state == UserTaskStatus.FAILED:
            return cls({
                "state": UserTaskStatus.FAILED,
                "error": "Library restore failed. See error log for details.",
                "error_log_url": artifact.file if artifact else None,
            }, context={'request': request})

        if task_status.state == UserTaskStatus.SUCCEEDED:
            input_data = {
                "state": UserTaskStatus.SUCCEEDED,
            }
            try:
                result = json.loads(artifact.text) if artifact else {}
                input_data["result"] = result
            except json.JSONDecodeError:
                log.error("Failed to decode JSON from artifact (%s): %s", artifact.id, artifact.text)
                input_data["error"] = f'Could not decode artifact JSON. Artifact Text: {artifact.text}'

            return cls(input_data)
