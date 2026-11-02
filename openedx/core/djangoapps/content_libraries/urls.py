"""
URL configuration for Studio's Content Libraries REST API
"""

from django.urls import include, path, re_path, register_converter
from rest_framework import routers

from .rest_api import blocks, collections, containers, libraries, url_converters

# Django application name.

app_name = 'openedx.core.djangoapps.content_libraries'

# URL converters

register_converter(url_converters.LibraryContainerLocatorConverter, "lib_container_key")

# Router for importing blocks from courseware.

import_blocks_router = routers.DefaultRouter()
import_blocks_router.register(r'tasks', libraries.LibraryImportTaskViewSet, basename='import-block-task')

library_collections_router = routers.DefaultRouter()
library_collections_router.register(
    r'collections', collections.LibraryCollectionsView, basename="library-collections"
)

# These URLs are only used in Studio. The LMS already provides all the
# API endpoints needed to serve XBlocks from content libraries using the
# standard XBlock REST API (see openedx.core.django_apps.xblock.rest_api.urls)

urlpatterns = [
    path('api/libraries/v2/', include([
        # list of libraries / create a library:
        path('', libraries.LibraryRootView.as_view()),
        path('restore/', libraries.LibraryRestoreView.as_view()),
        path('<str:lib_key_str>/', include([
            # get data about a library, update a library, or delete a library:
            path('', libraries.LibraryDetailsView.as_view()),
            # Get the list of XBlock types that can be added to this library
            path('block_types/', libraries.LibraryBlockTypesView.as_view()),
            # Get the list of XBlocks in this library, or add a new one:
            path('blocks/', blocks.LibraryBlocksView.as_view()),
            # Add a new container (unit etc.) to this library:
            path('containers/', containers.LibraryContainersView.as_view()),
            # Publish (POST) or revert (DELETE) all pending changes to this library:
            path('commit/', libraries.LibraryCommitView.as_view()),
            # Get the list of users/groups who have permission to view/edit/administer this library:
            path('team/', libraries.LibraryTeamView.as_view()),
            # Add/Edit (PUT) or remove (DELETE) a user's permission to use this library
            path('team/user/<str:username>/', libraries.LibraryTeamUserView.as_view()),
            # Add/Edit (PUT) or remove (DELETE) a group's permission to use this library
            path('team/group/<str:group_name>/', libraries.LibraryTeamGroupView.as_view()),
            # Import blocks into this library.
            path('import_blocks/', include(import_blocks_router.urls)),
            # Paste contents of clipboard into library
            path('paste_clipboard/', libraries.LibraryPasteClipboardView.as_view()),
            # Start a backup task for this library
            path('backup/', libraries.LibraryBackupView.as_view()),
            # Library Collections
            path('', include(library_collections_router.urls)),
        ])),
        path('blocks/<str:usage_key_str>/', include([
            # Get metadata about a specific XBlock in this library, or delete the block:
            path('', blocks.LibraryBlockView.as_view()),
            # Restore a soft-deleted block
            path('restore/', blocks.LibraryBlockRestore.as_view()),
            # Update collections for a given component
            path('collections/', blocks.LibraryBlockCollectionsView.as_view(), name='update-collections'),
            # Get the full hierarchy that the block belongs to
            path('hierarchy/', blocks.LibraryBlockHierarchy.as_view()),
            # Get the LTI URL of a specific XBlock
            path('lti/', blocks.LibraryBlockLtiUrlView.as_view(), name='lti-url'),
            # Get the OLX source code of the specified block:
            path('olx/', blocks.LibraryBlockOlxView.as_view()),
            # CRUD for static asset files associated with a block in the library:
            path('assets/', blocks.LibraryBlockAssetListView.as_view()),
            path('assets/<path:file_path>', blocks.LibraryBlockAssetView.as_view()),
            path('publish/', blocks.LibraryBlockPublishView.as_view()),
            # Future: discard changes for just this one block
        ])),
        # Containers are Sections, Subsections, and Units
        path('containers/<lib_container_key:container_key>/', include([
            # Get metadata about a specific container in this library, update or delete the container:
            path('', containers.LibraryContainerView.as_view()),
            # update components under container
            path('children/', containers.LibraryContainerChildrenView.as_view()),
            # Get the full hierarchy that the container belongs to
            path('hierarchy/', containers.LibraryContainerHierarchy.as_view()),
            # Restore a soft-deleted container
            path('restore/', containers.LibraryContainerRestore.as_view()),
            # Update collections for a given container
            path('collections/', containers.LibraryContainerCollectionsView.as_view(), name='update-collections-ct'),
            # Publish a container (or reset to last published)
            path('publish/', containers.LibraryContainerPublishView.as_view()),
            path('copy/', containers.LibraryContainerCopyView.as_view()),
        ])),
        re_path(r'^lti/1.3/', include([
            path('login/', libraries.LtiToolLoginView.as_view(), name='lti-login'),
            path('launch/', libraries.LtiToolLaunchView.as_view(), name='lti-launch'),
            path('pub/jwks/', libraries.LtiToolJwksView.as_view(), name='lti-pub-jwks'),
        ])),
    ])),
    path('library_assets/', include([
        path(
            'component_versions/<uuid:component_version_uuid>/<path:asset_path>',
            blocks.LibraryComponentAssetView.as_view(),
            name='library-assets',
        ),
        path(
            'blocks/<usage_v2:usage_key>/<path:asset_path>',
            blocks.LibraryComponentDraftAssetView.as_view(),
            name='library-draft-assets',
        ),
    ])
    ),
]
