"""
API v1 views.
"""
import logging
from uuid import UUID

import edx_api_doc_tools as apidocs
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocatorV2, CourseLocator, LibraryLocator
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError, PermissionDenied, ValidationError
from rest_framework.fields import BooleanField
from rest_framework.mixins import ListModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet
from user_tasks.models import UserTaskStatus
from user_tasks.views import StatusViewSet

from cms.djangoapps.modulestore_migrator import api as migrator_api
from common.djangoapps.student import auth
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser

from ... import models
from ...data import (
    SourceContextKey, ModulestoreMigration, ModulestoreBlockMigrationResult,
    CompositionLevel, RepeatHandlingStrategy,
)
from .serializers import (
    BlockMigrationInfoSerializer,
    BulkModulestoreMigrationSerializer,
    LibraryMigrationCourseSerializer,
    MigrationInfoResponseSerializer,
    ModulestoreMigrationSerializer,
    StatusWithModulestoreMigrationsSerializer,
    PreviewMigrationSerializer,
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
    JSON HTTP API to create and check on ModuleStore-to-openedx_content migration tasks.
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
    JSON HTTP API to bulk-create ModuleStore-to-openedx_content migration tasks.
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


class MigrationInfoViewSet(APIView):
    """
    Retrieve migration information for a list of source courses or libraries.

    It returns the target library information associated with each successfully migrated source.

    API Endpoints
    -------------
    GET /api/modulestore_migrator/v1/migration-info/
        Retrieve migration details for one or more sources.

        Query parameters:
            source_keys (list[str]): List of course or library keys to check.
                Example: ?source_keys=course-v1:edX+DemoX+2024_T1&source_keys=library-v1:orgX+lib_2

        Example request:
            GET /api/modulestore_migrator/v1/migration-info/?source_keys=course-v1:edX+DemoX+2024_T1

        Example response:
            {
                "course-v1:edX+DemoX+2024_T1": [
                    {
                        "target_key": "library-v1:orgX+lib_2",
                        "target_title": "Demo Library",
                        "target_collection_key": "col-v2:1234abcd",
                        "target_collection_title": "Default Collection",
                        "source_key": "course-v1:edX+DemoX+2024_T1"
                    }
                ],
                "library-v1:orgX+lib_2": [
                    {
                        "target_key": "library-v1:orgX+lib_2",
                        "target_title": "Demo Library",
                        "target_collection_key": "col-v2:1234abcd",
                        "target_collection_title": "Default Collection",
                        "source_key": "course-v1:edX+DemoX+2024_T1"
                    },
                    {
                        "target_key": "library-v1:orgX+lib_2",
                        "target_title": "Demo Library",
                        "target_collection_key": "col-v2:1234abcd",
                        "target_collection_title": "Default Collection",
                        "source_key": "course-v1:edX+DemoX+2024_T1"
                    }
                ]
            }
    """

    permission_classes = (IsAuthenticated,)
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "source_keys",
                apidocs.ParameterLocation.QUERY,
                description="List of source keys to consult",
            ),
        ],
        responses={
            200: MigrationInfoResponseSerializer,
            400: "Missing required parameter: source_keys",
            401: "The requester is not authenticated.",
        },
    )
    def get(self, request):
        """
        Handle the migration info `GET` request
        """
        source_keys = request.query_params.getlist("source_keys")

        if not source_keys:
            return Response(
                {"detail": "Missing required parameter: source_keys"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check permissions for each source_key:
        # Skip the source if the key is invalid or if the user doesn't have permissions
        source_keys_validated = []
        for source_key in source_keys:
            try:
                key = CourseKey.from_string(source_key)
                if auth.has_studio_read_access(request.user, key):
                    source_keys_validated.append(key)
            except InvalidKeyError:
                continue

        data = {
            source_key: migrator_api.get_migrations(source_key, is_failed=False)
            for source_key in source_keys_validated
        }
        serializer = MigrationInfoResponseSerializer(data)
        return Response(serializer.data)


@apidocs.schema_for(
    "list",
    "List all course migrations to a library.",
    responses={
        201: LibraryMigrationCourseSerializer,
        401: "The requester is not authenticated.",
        403: "The requester does not have permission to access the library.",
    },
)
class LibraryCourseMigrationViewSet(GenericViewSet, ListModelMixin):
    """
    Show infomation about migrations related to a destination library.
    """

    serializer_class = LibraryMigrationCourseSerializer
    pagination_class = None
    queryset = models.ModulestoreMigration.objects.all().select_related('target_collection', 'target', 'task_status')

    def get_serializer_context(self):
        """
        Add course name list to the serializer context.

        We need to display the course names in the migration view, and we get all of
        them here to avoid futher queries.
        """
        context = super().get_serializer_context()
        queryset = self.get_queryset()
        course_keys = queryset.values_list('source__key', flat=True)
        courses = CourseOverview.get_all_courses(course_keys=course_keys)
        context['course_names'] = dict((str(course.id), course.display_name) for course in courses)
        return context

    def get_queryset(self):
        """
        Override the default queryset to filter by the library key and check permissions.
        """
        queryset = super().get_queryset()
        lib_key_str = self.kwargs['lib_key_str']
        try:
            library_key = LibraryLocatorV2.from_string(lib_key_str)
        except InvalidKeyError as exc:
            raise ParseError(detail=f"Malformed library key: {lib_key_str}") from exc
        lib_api.require_permission_for_library_key(
            library_key,
            self.request.user,
            lib_api.permissions.CAN_VIEW_THIS_CONTENT_LIBRARY
        )
        queryset = queryset.filter(target__key=library_key, source__key__startswith='course-v1')

        return queryset


class BlockMigrationInfo(APIView):
    """
    Retrieve migration blocks information given task_uuid, source_key or target_key.

    It returns the migration block information for each block migrated by a specific task.

    API Endpoints
    -------------
    GET /api/modulestore_migrator/v1/migration_blocks/
        Retrieve migration blocks info for given task_uuid, source_key or target_key.

        Query parameters:
            task_uuid (str): task uuid
                Example: ?task_uuid=dfe72eca-c54f-4b43-b53b-7996031f2102
            source_key (str): Source content key
                Example: ?source_key=course-v1:UNIX+UX1+2025_T3
            target_key (str): target content key
                Example: ?target_key=lib:UNIX:CIT1
            is_failed (boolean): has the block failed to migrate/import
                Example: ?is_failed=true

        Example request:
            GET /api/modulestore_migrator/v1/migration_blocks/?task_uuid=dfe72eca-c54f-4b43-b53b&is_failed=true

        Example response:
    """

    permission_classes = (IsAuthenticated,)
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "target_key",
                apidocs.ParameterLocation.QUERY,
                description="Filter blocks by target key",
            ),
            apidocs.string_parameter(
                "source_key",
                apidocs.ParameterLocation.QUERY,
                description="Filter blocks by source key",
            ),
            apidocs.string_parameter(
                "target_collection_key",
                apidocs.ParameterLocation.QUERY,
                description="Filter blocks by target_collection_key",
            ),
            apidocs.string_parameter(
                "task_uuid",
                apidocs.ParameterLocation.QUERY,
                description="Filter blocks by task_uuid",
            ),
            apidocs.string_parameter(
                "is_failed",
                apidocs.ParameterLocation.QUERY,
                description="Filter blocks based on its migration status",
            ),
        ],
        responses={
            200: MigrationInfoResponseSerializer,
            400: "Missing required parameter: target_key",
            401: "The requester is not authenticated.",
        },
    )
    def get(self, request: Request):
        """
        Handle the migration info `GET` request
        """
        target_key: LibraryLocatorV2 | None
        if target_key_param := request.query_params.get("target_key"):
            try:
                target_key = LibraryLocatorV2.from_string(target_key_param)
            except InvalidKeyError:
                return Response({"error": f"Bad target_key: {target_key_param}"}, status=400)
        else:
            return Response({"error": "Target key cannot be blank."}, status=400)
        target_collection_key = request.query_params.get("target_collection_key")
        source_key: SourceContextKey | None = None
        if source_key_param := request.query_params.get("source_key"):
            try:
                source_key = CourseLocator.from_string(source_key_param)
            except InvalidKeyError:
                try:
                    source_key = LibraryLocator.from_string(source_key_param)
                except InvalidKeyError:
                    return Response({"error": f"Bad source: {source_key_param}"}, status=400)
        task_uuid: UUID | None = None
        if task_uuid_param := request.query_params.get("task_uuid"):
            try:
                task_uuid = UUID(task_uuid_param)
            except ValueError:
                return Response({"error": f"Bad task_uuid: {task_uuid_param}"}, status=400)
        is_failed: bool | None = None  # None means unspecified -- include both successful and failed.
        if (is_failed_param := request.query_params.get("is_failed")) is not None:
            try:
                is_failed = BooleanField().to_internal_value(is_failed_param)
            except ValidationError:
                return Response({"error": f"Bad is_failed value: {is_failed_param}"}, status=400)
        lib_api.require_permission_for_library_key(
            target_key,
            request.user,
            lib_api.permissions.CAN_VIEW_THIS_CONTENT_LIBRARY
        )
        migrations: list[ModulestoreMigration] = list(
            migrator_api.get_migrations(
                source_key=source_key,
                target_key=target_key,
                target_collection_slug=target_collection_key,
                task_uuid=task_uuid,
            )
        )
        data: list[ModulestoreBlockMigrationResult] = [
            block_migration
            for migration in migrations
            for block_migration in migrator_api.get_migration_blocks(migration.pk).values()
            # Include the block iff...
            if is_failed in [
                None,  # we're not filtering on success, or
                block_migration.is_failed,  # we are filtering on success, and this matches.
            ]
        ]
        serializer = BlockMigrationInfoSerializer(data, many=True)
        return Response(serializer.data)


class PreviewMigration(APIView):
    """
    Retrieve the summary preview of the migration given a source key and a target key

    It returns the migration block information for each block migrated by a specific task.

    API Endpoints
    -------------
    GET /api/modulestore_migrator/v1/migration_preview/
        Retrieve the summary preview of the migration given a source key and a target key

        Query parameters:
            source_key (str): Source content key
                Example: ?source_key=course-v1:UNIX+UX1+2025_T3
            target_key (str): target content key
                Example: ?target_key=lib:UNIX:CIT1

        Example request:
            GET /api/modulestore_migrator/v1/migration_blocks/?source_key=course_key&target_key=library_key

        Example response:
    """

    permission_classes = (IsAuthenticated,)
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                "target_key",
                apidocs.ParameterLocation.QUERY,
                description="Target key of the migration",
            ),
            apidocs.string_parameter(
                "source_key",
                apidocs.ParameterLocation.QUERY,
                description="Source key of the migration",
            ),
        ],
        responses={
            200: PreviewMigrationSerializer,
            400: "Missing required parameter: target_key/source_key",
            401: "The requester is not authenticated.",
        },
    )
    def get(self, request: Request):
        """
        Handle the migration info `GET` request
        """
        target_key: LibraryLocatorV2 | None
        if target_key_param := request.query_params.get("target_key"):
            try:
                target_key = LibraryLocatorV2.from_string(target_key_param)
            except InvalidKeyError:
                return Response({"error": f"Bad target_key: {target_key_param}"}, status=400)
        else:
            return Response({"error": "Target key cannot be blank."}, status=400)
        source_key: SourceContextKey | None = None
        if source_key_param := request.query_params.get("source_key"):
            try:
                source_key = CourseLocator.from_string(source_key_param)
            except InvalidKeyError:
                try:
                    source_key = LibraryLocator.from_string(source_key_param)
                except InvalidKeyError:
                    return Response({"error": f"Bad source: {source_key_param}"}, status=400)
        else:
            return Response({"error": "Source key cannot be blank."}, status=400)

        lib_api.require_permission_for_library_key(
            target_key,
            request.user,
            lib_api.permissions.CAN_EDIT_THIS_CONTENT_LIBRARY,
        )
        result = migrator_api.preview_migration(source_key, target_key)

        serializer = PreviewMigrationSerializer(result)

        return Response(serializer.data)
