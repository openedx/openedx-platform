"""
API v1 views.
"""
import logging

import edx_api_doc_tools as apidocs
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from user_tasks.models import UserTaskStatus
from user_tasks.views import StatusViewSet

from cms.djangoapps.modulestore_migrator import api as migrator_api
from common.djangoapps.student import auth
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser

from ...data import (
    CompositionLevel, RepeatHandlingStrategy,
)
from .serializers import (
    BulkModulestoreMigrationSerializer,
    ModulestoreMigrationSerializer,
    StatusWithModulestoreMigrationsSerializer,
)

log = logging.getLogger(__name__)


_error_responses = {
    400: "Request malformed.",
    401: "Requester is not authenticated.",
    403: "Permission denied",
}


@apidocs.schema_for(
    "list",
    """
    List all migration and bulk-migration tasks started by the current user.

    The response is a paginated series of migration task status objects, ordered
    by the time at which the migration was started, newest first.
    See `POST /api/modulestore_migrator/v1/migrations` for details of each object's schema.
    """,
)
@apidocs.schema_for(
    "retrieve",
    """
    Get the status of particular migration or bulk-migration task by its UUID.

    The response is a migration task status object.
    See `POST /api/modulestore_migrator/v1/migrations` for details on its schema.
    """,
)
class MigrationViewSet(StatusViewSet):
    """
    JSON HTTP API to create and check on ModuleStore-to-Learning-Core migration tasks.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    serializer_class = StatusWithModulestoreMigrationsSerializer

    # DELETE is not allowed, as we want to preserve all task status objects.
    # Instead, users can POST to /cancel to cancel running tasks.
    http_method_names = ["get", "post"]

    lookup_field = "uuid"

    def get_queryset(self):
        """
        Override the default queryset to filter by the migration event and user.
        """
        return StatusViewSet.queryset.filter(
            migrations__isnull=False,
            # The filter for `user` here is essentially the auth strategy for the /list and /retreive
            # endpoints. Basically: you can view migrations if and only if you started them.
            # Future devs: If you ever refactor this view to remove the user filter, be sure to enforce
            # permissions some other way.
            user=self.request.user
        ).distinct().order_by("-created")

    @apidocs.schema()
    @action(detail=True, methods=['post'])
    def cancel(self, request, *args, **kwargs):
        """
        Cancel a particular migration or bulk-migration task.

        The response is a migration task status object.
        See `POST /api/modulestore_migrator/v1/migrations` for details on its schema.

        This endpoint is currently reserved for site-wide administrators.
        """
        # TODO: This should check some sort of "allowed to cancel/migrations" permission
        #       rather than directly looking at the GlobalStaff role.
        #       https://github.com/openedx/edx-platform/issues/37791
        if not request.user.is_staff:
            raise PermissionDenied("Only site administrators can cancel migration tasks.")
        return super().cancel(request, *args, **kwargs)

    @apidocs.schema(
        body=ModulestoreMigrationSerializer,
        responses={
            201: StatusWithModulestoreMigrationsSerializer,
            **_error_responses,
        },
    )
    def create(self, request, *args, **kwargs):
        """
        Begin a transfer of content from course or legacy library into a content library.

        Required parameters:
        * A **source** key, which identifies the course or legacy library containing the items be migrated.
        * A **target** key, which identifies the content library to which items will be migrated.

        Optional parameters:

        * The **target_collection_slug**, which identifies an *existing* collection within the target that
          should hold the migrated items. If not specified, items will be added to the target library without
          any collection, unless:
          * If this source was previously migrated to a collection and the **repeat_handling_strategy** (described
            below) is not set to *fork*, then that same collection will be re-used.
          * If **create_collection** is specified as *true*, then the items will be added to a new collection, with
            a name and slug based on the source's title, but not conflicting with any existing collection.
        * The **composition_level** (*component*, *unit*, *subsection*, *section*) indicates the highest level of
          hierarchy to be transferred. Default is *component*. To maximally preserve the source structure,
          specify *section*.
        * The **repeat_handling_strategy** specifies how the system should handle source items which have
          previously been migrated to the target.
          * Specify *skip* to prefer the existing target item. This is the default.
          * Specify *update* to update the existing target item with the latest source content.
          * Specify *fork* to create a new target item with the source content.
        * Specify **preserve_url_slugs** as *true* in order to use the source-provided block IDs
          (a.k.a. "URL slugs", "url_names").  Otherwise, the system will use each source item's title
          to auto-generate an ID in the target context.
        * Specify **forward_source_to_target** as *true* in order to establish a mapping from the source items to the
          target items (specifically, the mapping is stored by the *forwarded* field of the
          *modulestore_migrator_modulestoresource* and *modulestore_migrator_modulestoreblocksource* database tables).
          * **Example**: Specify *true* if you are permanently migrating legacy library content into a content
            library, and want course references to the legacy library content to automatically be mapped to new
            content library.
          * **Example**: Specify *false* if you are migrating legacy library content into a content
            library, but do *not* want course references to the legacy library content to be mapped to new
            content library.
          * **Example**: Specify *false* if you are copying course content into a content library, but do not
            want to persist a link between the source source content and destination library contenet.
          * **Defaults** to *false* if the source has already been mapped to a target by a successful migration,
            and defaults to *true* if not. In other words, by default, establish the mapping only if it wouldn't
            override an existing mapping.

        Example request:
        ```json
        {
            "source": "course-v1:MyOrganization+MyCourse+MyRun",
            "target": "lib-collection:MyOrganization:MyUlmoLibrary",
            "target_collection_slug": "MyCollection",
            "create_collection": true,
            "composition_level": "unit",
            "repeat_handling_strategy": "update",
            "preserve_url_slugs": true
        }
        ```

        The migration task will take anywhere from seconds to minutes, depending on the size of the source
        content. This API's response will tell the initial status of the migration task, including:

        * The **state**, whose values include:
          * _Pending_: The migration task is waiting to be picked up by a Studio worker process.
          * _Succeeded_: The migration task finished without fatal errors.
          * _Failed_: A fatal error occured during the migration task.
          * _Canceled_: An administrator canceled the migration task.
          * Any other **state** value indicates the migration is actively in progress.
        * The **state_text**, the localized version of **state**.
        * The **artifacts**, a list of URLs pointing to additional diagnostic information created
          during that task. In the current version of this API, successful migrations will have zero
          artifacts and failed migrations will have one artifact.
        * The **uuid**, which be used to retrieve an updated status on this migration later, via
          *GET /api/modulestore_migrator/v1/migrations/$uuid*
        * The **parameters**, a singleton list whose elemenet indicates the parameters which were
          used to start this migration task.

        Example response:
        ```json
        {
            "state": "Parsing staged OLX",
            "state_text": "Parsing staged OLX",
            "completed_steps": 4,
            "total_steps": 12,
            "attempts": 1,
            "created": "2025-05-14T22:24:37.048539Z",
            "modified": "2025-05-14T22:24:59.128068Z",
            "artifacts": [],
            "uuid": "3de23e5d-fd34-4a6f-bf02-b183374120f0",
            "parameters": [
                {
                    "source": "course-v1:MyOrganization+MyCourse+MyRun",
                    "target": "lib:MyOrganization:MyUlmoLibrary",
                    "composition_level": "unit",
                    "repeat_handling_strategy": "update",
                    "preserve_url_slugs": true
                }
            ]
        }
        ```

        This API requires that the requester have author access on both the source and target.
        """
        serializer_data = ModulestoreMigrationSerializer(data=request.data)
        serializer_data.is_valid(raise_exception=True)
        validated_data = serializer_data.validated_data
        if not auth.has_studio_write_access(request.user, validated_data['source']):
            raise PermissionDenied("Requester is not an author on the source.")
        lib_api.require_permission_for_library_key(
            validated_data['target'],
            request.user,
            lib_api.permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        task = migrator_api.start_migration_to_library(
            user=request.user,
            source_key=validated_data['source'],
            target_library_key=validated_data['target'],
            target_collection_slug=validated_data['target_collection_slug'],
            composition_level=CompositionLevel(validated_data['composition_level']),
            create_collection=validated_data['create_collection'],
            repeat_handling_strategy=RepeatHandlingStrategy(validated_data['repeat_handling_strategy']),
            preserve_url_slugs=validated_data['preserve_url_slugs'],
            forward_source_to_target=validated_data['forward_source_to_target'],
        )
        task_status = UserTaskStatus.objects.get(task_id=task.id)
        serializer = self.get_serializer(task_status)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BulkMigrationViewSet(StatusViewSet):
    """
    JSON HTTP API to bulk-create ModuleStore-to-Learning-Core migration tasks.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    serializer_class = StatusWithModulestoreMigrationsSerializer

    # Because bulk-migration tasks are just special migration tasks, they are listed as part of:
    #   GET .../migrations/
    # To avoid having 2 APIs that do (mostly) the same thing, we nix this endpoint:
    #   GET .../bulk_migration/<uuid>/cancel
    # which we inherited from StatusViewSet.
    # Furthermore, DELETE is not allowed, as we want to preserve all task status objects.
    # That just leaves us with POST.
    http_method_names = ["post"]

    @apidocs.schema(
        body=BulkModulestoreMigrationSerializer,
        responses={
            201: StatusWithModulestoreMigrationsSerializer,
            **_error_responses,
        },
    )
    def create(self, request, *args, **kwargs):
        """
        Transfer content from multiple courses or legacy libraries into a content library.

        Create a migration task to import multiple courses or legacy libraries into a single content library.
        This is bulk version of `POST /api/modulestore_migrator/v1/migrations`. See that endpoint's documentation
        for details on the meanings of the request and response values.

        **Request body**:
        ```json
        {
            "sources": ["<source_course_key_1>", "<source_course_key_2>"],
            "target": "<target_library>",
            "composition_level": "<composition_level>",  # Optional, defaults to "component"
            "target_collection_slugs": ["<target_collection_slug_1>", "<target_collection_slug_1>"],  # Optional
            "create_collections": "<boolean>"  # Optional, defaults to false
            "repeat_handling_strategy": "<repeat_handling_strategy>"  # Optional, defaults to Skip
            "preserve_url_slugs": "<boolean>"  # Optional, defaults to true
        }
        ```

        **Example request:**
        ```json
        {
            "sources": ["course-v1:edX+DemoX+2014_T1", "course-v1:edX+DemoX+2014_T2"],
            "target": "library-v1:org1+lib_1",
            "composition_level": "unit",
            "repeat_handling_strategy": "update",
            "preserve_url_slugs": true,
            "create_collections": true
        }
        ```

        **Example response:**
        ```json
        {
            "state": "Succeeded",
            "state_text": "Succeeded",  # Translation into the current language of the current state
            "completed_steps": 11,
            "total_steps": 11,
            "attempts": 1,
            "created": "2025-05-14T22:24:37.048539Z",
            "modified": "2025-05-14T22:24:59.128068Z",
            "artifacts": [],
            "uuid": "3de23e5d-fd34-4a6f-bf02-b183374120f0",
            "parameters": [
                {
                    "source": "course-v1:edX+DemoX+2014_T1",
                    "composition_level": "unit",
                    "repeat_handling_strategy": "update",
                    "preserve_url_slugs": true
                },
                {
                    "source": "course-v1:edX+DemoX+2014_T2",
                    "composition_level": "unit",
                    "repeat_handling_strategy": "update",
                    "preserve_url_slugs": true
                },
            ]
        }
        ```

        This API requires that the requester have author access on both the source and target.
        """
        serializer_data = BulkModulestoreMigrationSerializer(data=request.data)
        serializer_data.is_valid(raise_exception=True)
        validated_data = serializer_data.validated_data
        for source_key in validated_data['sources']:
            if not auth.has_studio_write_access(request.user, source_key):
                raise PermissionDenied(
                    f"Requester is not an author on the source: {source_key}. No migrations performed."
                )
        lib_api.require_permission_for_library_key(
            validated_data['target'],
            request.user,
            lib_api.permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        task = migrator_api.start_bulk_migration_to_library(
            user=request.user,
            source_key_list=validated_data['sources'],
            target_library_key=validated_data['target'],
            target_collection_slug_list=validated_data['target_collection_slug_list'],
            create_collections=validated_data['create_collections'],
            composition_level=CompositionLevel(validated_data['composition_level']),
            repeat_handling_strategy=RepeatHandlingStrategy(validated_data['repeat_handling_strategy']),
            preserve_url_slugs=validated_data['preserve_url_slugs'],
            forward_source_to_target=validated_data['forward_source_to_target'],
        )

        task_status = UserTaskStatus.objects.get(task_id=task.id)
        serializer = self.get_serializer(task_status)

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def cancel(self, request, *args, **kwargs):
        """
        Remove the `POST .../cancel` endpoint, which was inherited from StatusViewSet.

        Bulk-migration tasks and migration tasks, under the hood, are the same thing.
        So, bulk-migration tasks can be cancelled with:
           POST .../migrations/<uuid>/cancel

        We disable this endpoint to avoid confusion.
        """
        raise NotImplementedError
