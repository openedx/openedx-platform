Dual Code Paths During Course-Authoring AuthZ Migration
#########################################################

**Status**: Accepted
**Date**: 2026-07-07

-----

Context
*******

``AUTHZ_COURSE_AUTHORING_FLAG`` gates the migration from legacy ``CourseAccessRole`` to openedx-authz. That migration is not finished yet since only some legacy roles have an authz equivalent so far (see ``LEGACY_COURSE_ROLE_EQUIVALENCES`` in openedx-authz and its ADR 0011). ``course_creator_group`` and ``org_course_creator_group`` don't have one yet.

When first implemented, the code deciding whether to use authz or legacy only looked at the waffle flag. It never checked whether the specific role being handled had actually been migrated. With the flag on, this broke course creation for those two roles. We observed it two ways:

- Granting course creator access through Django admin crashed with a 500 (`openedx-authz#353 <https://github.com/openedx/openedx-authz/issues/353>`_).
- A user with an existing legacy course creator grant got a 403 on course creation (`openedx-authz#354 <https://github.com/openedx/openedx-authz/issues/354>`_).

Both trace back to the same root cause - the code was trying to use authz for a role that doesn't have an authz equivalent yet.

Decision
********

``enable_authz_course_authoring`` now takes an optional ``role`` argument to contextualize the decision. If a role is passed in, the function checks whether that role has an authz equivalent:

- When that role has no migrated authz equivalent, the function returns false and sticks to the legacy path, no matter what the flag says.
- When that role has an authz equivalent, the function returns the flag's value, as before.

Callers that act on a specific role, like granting or revoking it, should pass that role in to get the correct behavior. Callers that don't care about a specific role, like generic permission checks, can leave it out and just follow the flag as before.

The role check only runs when the flag is on, since the function returns early otherwise. When the flag is off, it always returns false and never evaluates the role.

Consequences
************

- Roles without an authz equivalent keep working once the flag is on, instead of crashing or being denied access. Callers that don't need role-level distinction don't pay any extra cost, since ``role`` is optional and defaults to the old behavior.

- The main risk is a future caller that acts on a specific role but forgets to pass it in. That would quietly bring back the exact bug this decision fixes, so new callers touching a specific role need to remember this.

- This is meant as a stopgap, not a permanent shape for the code. Once every legacy role has an authz equivalent, the role check, and eventually the flag itself, can be removed.

- Course creation itself (``is_content_creator``) skips this check entirely and always uses the course creator role directly, since neither role has an authz equivalent yet. It'll need updating once openedx-authz supports the role.

References
**********

* `openedx-authz ADR 0011 <https://docs.openedx.org/projects/openedx-authz/en/latest/decisions/0011-course-authoring-migration-process.html>`_ (role mapping table).
* ``common/djangoapps/student/roles.py``: ``enable_authz_course_authoring``, ``get_authz_role_from_legacy_role``.
