"""Bounded indexing with real CMS source models and serializers."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from organizations.models import Organization

from openedx.core.djangoapps.content_libraries import api as library_api
from openedx.core.djangolib.testing.utils import skip_unless_cms

from .. import api
from ..library_indexing_batches import index_library_in_batches
from ..models import SearchAccess


@skip_unless_cms
class LibraryIndexingCMSIntegrationTests(TestCase):
    """Keep source reads real and replace only the external write boundary."""

    def test_real_library_documents_are_batched(self):
        with override_settings(MEILISEARCH_ENABLED=False):
            org = Organization.objects.create(name="Async validation", short_name="asyncvalidation")
            library = library_api.create_library(org=org, slug="bounded", title="Bounded library")
            blocks = [library_api.create_library_block(library.key, "html", f"block{n}") for n in range(3)]
            library_api.create_library_collection(
                library.key, collection_key="collection", title="A collection", created_by=None,
            )
        SearchAccess.objects.get_or_create(context_key=library.key)
        with patch("openedx.core.djangoapps.content.search.api._update_index_docs") as write:
            index_library_in_batches(library.key, batch_size=2)
        assert [len(call.args[1]) for call in write.call_args_list] == [2, 2]
        documents = [doc for call in write.call_args_list for doc in call.args[1]]
        for block in blocks:
            assert api.searchable_doc_for_library_block(block) in documents
        assert all(doc["context_key"] == str(library.key) for doc in documents)
