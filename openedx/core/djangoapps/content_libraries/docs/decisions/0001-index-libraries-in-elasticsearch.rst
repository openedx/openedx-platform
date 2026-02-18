1. Index libraries in elasticsearch
###################################

Status
******

**Revoked**

In Dec 2023, we decided to remove the code supporting this decision, because:

* The index is disabled on edx.org, which will initially be the only user
  of Content Libraries V2.
* As we migrate libraries from Modulestore to Blockstore and then from
  Blockstore to openedx_content, the unused indexing code increases complexity
  and decreases certainty.
* With the decision to migrate from Blockstore-the-service to an in-process
  storage backend (that is: Blockstore-the-app or openedx_content), it seems
  that we will be able to simply use Django ORM in order to filter/sort/search
  Content Library V2 metadata for the library listing page.
* Searching Content Library V2 *block* content would still require indexing,
  but we would rather implement that in openedx_content than use the current
  implementation in the content_libraries app, which is untested, library-
  specific, and doesn't take into account library versioning. It always uses
  the latest draft, which is good for Library Authoring purposes, but not good for
  Course Authoring purposes.

It is possible that we will end up re-instating a modified version of this ADR
future. If that happens, we may re-use and adapt the original library index
code.


Context
*******

The new content libraries reside in blockstore instead of edx-platform's models,
which means that we are no longer able to query databases to get complete
metadata quickly about one or more libraries/xblock anymore. Blockstore can't
index them either because most of the data resides on the filesystem, S3 or
other non-queryable stores. The current method to get the metadata of a library
involves requesting blockstore for the metadata, which in turn reads metadata
from files stored in the above mentioned storage systems. This process is
repeated for every library if data is required for a list of libraries. A
similar process is followed for XBlocks too.

This is a very inefficient way to fetch metadata for a list of
libraries/xblocks, and makes it even harder to filter/query them.

Decision
********

Index the libraries and xblocks in elasticsearch to make them queryable. These
indexes are updated whenever a library or XBlock is updated through the studio.
A management command ``redindex_content_library`` is also added for clearing
indexes or reindex libraries manually.

Given that elasticsearch hasn't been a required dependency of studio till now,
fallbacks have been implemented in case elastic is down or hasn't been enabled
yet.

Consequences
************

List APIs are significantly faster and are able to support filtering and
searching now that the metadata can be queried using elasticsearch. This also
means that if the indexes are empty or outdated, the API results would be too.

Signal handlers update the indexes whenever libraries and xblocks are created,
updated or deleted through the studio. But if they are modified directly at the
source or without using the studio APIs, then the indexes will get out of date
too until reindexing is performed using management commands or another
modification operation causes a reindex.

The fallback method described above returns only a subset of the complete
response usually returned by Elastic. Attributes which require scanning through
multiple files are exempted from this minimal response.

We use schema versions to avoid querying old indexes, which could otherwise
result in unforeseen errors. This version is incremented every time the
structure of the schema changes. This also means that a reindexing will be
needed after any upgrade which changes an index schema.
