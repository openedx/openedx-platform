"""Stream full library documents into bounded, individually awaited engine batches."""

from openedx.core.djangoapps.content_libraries import api as lib_api


def index_library_in_batches(library_key, batch_size=100):
    """Reuse existing document construction and dual-index write behavior.

    Each source queryset uses iterator() to avoid Django's full result cache.
    No persistent per-batch checkpoint is used: interrupted runs replay current
    authoritative content, including batches the engine may already have accepted.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    from . import api  # pylint: disable=import-outside-toplevel

    def documents():
        for component in lib_api.get_library_components(library_key).iterator(chunk_size=batch_size):
            metadata = lib_api.LibraryXBlockMetadata.from_component(library_key, component)
            yield api.searchable_doc_for_library_block(metadata)
        for container in lib_api.get_library_containers(library_key).iterator(chunk_size=batch_size):
            container_key = lib_api.library_container_locator(library_key, container)
            yield api.searchable_doc_for_container(container_key)
        for collection in lib_api.get_library_collections(library_key).iterator(chunk_size=batch_size):
            collection_key = lib_api.library_collection_locator(library_key, collection.collection_code)
            yield api.searchable_doc_for_collection(collection_key, collection=collection)

    batch = []
    for doc in documents():
        batch.append(doc)
        if len(batch) == batch_size:
            api._update_index_docs(api.STUDIO_LIBRARY_INDEX_NAME, batch)  # pylint: disable=protected-access
            batch = []
    if batch:
        api._update_index_docs(api.STUDIO_LIBRARY_INDEX_NAME, batch)  # pylint: disable=protected-access
