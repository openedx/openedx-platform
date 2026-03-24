=============================
Content Staging and Clipboard
=============================

This django app provides APIs to temporarily store content and then retrieve it
for use. The content must be detached (not yet part of any course/library) and
read-only (this is not a workspace, and the content cannot be edited while
staged).

The primary use case, which is also integrated into this django app, is a
per-user clipboard in the CMS (Studio), which can be used to copy and paste
components (XBlocks) between courses. At the moment, we only support leaf
XBlocks in courses but the goal is to soon support larger pieces of content as
well as content libraries.

As this app is designed only for ephemeral use cases, API consumers should not
expect that staged content will be stored for longer than 24 hours.

This app is part of the CMS and is not intended to work with the LMS. It may be
moved from the CMS into the future openedx_content project.

---------------
Clipboard Usage
---------------

* When a user initiates a "Copy" action, the usage key of the component that
  they wish to copy is POSTed to this app's ``copy`` REST API endpoint.
* This app will then use the ``olx_rest_api`` to get the OLX of the item in
  question, as well as other required data like metadata, static assets, etc.
* If the copying action succeeded or is still in progress, a clipboard ID will
  be returned. There is also an API available to query the "current" clipboard
  ID of the user. Clipboard are always user-specific and private to a user.
