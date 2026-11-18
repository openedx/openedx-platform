"""
A ``ContentStore`` backend that stores course "Files" as ``openedx_content``
Assets.

Course Files are being moved out of the legacy MongoDB/GridFS contentstore and
into ``openedx_content`` (see openedx-core ADR 0016, "Replacing Contentstore").
:class:`~.store.VersionedContentStoreShim` implements the legacy
``ContentStore`` API on top of the new Assets API, so that the rest of the
platform can keep using the ``ContentStore`` API without knowing which backend
holds a given course's files.
"""
