Instructor Dashboard AuthZ
##########################

Integration of the Instructor Dashboard (``/api/instructor/v2``) with ``openedx-authz``.

What it contains
****************

- ``constants.py``: groups of permission identifiers (all the dashboard permissions, and the ones of each tab). The permissions themselves come from the ``constants`` module of each ``openedx_authz.applets`` package; use their ``.identifier`` and do not repeat the strings in the code.
- ``api.py``: ``user_has_instructor_permission`` and ``user_has_any_instructor_permission``.
- ``permissions.py``: ``InstructorAuthzPermission``, the DRF permission class for the views.
- ``legacy.py``: the pre-AuthZ check equivalent to each permission.
- ``compat.py``: the legacy roles that keep their legacy access with AuthZ enabled.
- ``tests/mixins.py``: ``InstructorAuthzTestMixin`` to assign AuthZ roles in tests.

Behavior
********

It is driven by the ``authz.enable_course_authoring`` waffle flag, evaluated per course, with the same semantics as Studio:

- **Flag enabled:** only AuthZ is consulted. Legacy roles are ignored (openedx-authz already grants the global staff and superuser bypass).
- **Flag disabled:** only the legacy check runs, so the previous behavior is unchanged.

The exception are the legacy roles that openedx-authz maps without dashboard permissions (``course_limited_staff``, ``course_data_researcher`` and ``course_beta_tester``). They only hold a compatibility permission (``courses.legacy_*_permissions``), so with the flag enabled, when AuthZ denies, they are evaluated with the legacy check, the same one used with the flag disabled. They keep exactly the access their legacy role had. To cover another legacy role, add its compatibility permission to ``LEGACY_ROLE_COMPAT_PERMISSIONS`` in ``compat.py``. ``course_admin`` and ``course_staff`` are not in it: they hold dashboard permissions, so a denial is final.

Every decision is logged with the path that resolved it (``authz`` or ``legacy``) in ``access_path``.

Migrating a view
****************

Replace ``InstructorPermission`` by ``InstructorAuthzPermission`` and declare the AuthZ permission next to the legacy one. ``course_id`` must be a URL kwarg: the permission class reads it from ``view.kwargs`` and returns a ``404`` if it is not a valid course key. If the view's URL pattern names that kwarg something else (e.g. the cohorts API's ``course_key_string``), set ``course_id_url_kwarg`` on the view to match. Pick the case that matches the view:

Case 1: Same permissions for every HTTP method
==============================================

``authz_permissions`` is always a collection, like ``permission_classes``, even for a single permission. Unlike ``permission_classes``, where all the classes must pass (AND), holding **any one** of the permissions is enough (OR). Declare it with the legacy bridgekeeper permission the view already used:

.. code-block:: python

    from openedx_authz.applets.enrollments.constants import COURSES_VIEW_ENROLLMENTS
    from lms.djangoapps.instructor.authz import constants as authz_constants
    from lms.djangoapps.instructor.authz.permissions import InstructorAuthzPermission

    class MyView(APIView):
        permission_classes = (IsAuthenticated, InstructorAuthzPermission)
        authz_permissions = (COURSES_VIEW_ENROLLMENTS.identifier,)
        # The legacy bridgekeeper permission.
        permission_name = permissions.VIEW_ENROLLMENTS

A bare string raises ``ImproperlyConfigured``. With several permissions and ``authz.enable_course_authoring`` disabled for the course, the legacy check of the view decides, evaluated once:

.. code-block:: python

    class MyView(APIView):
        permission_classes = (IsAuthenticated, InstructorAuthzPermission)
        authz_permissions = authz_constants.INSTRUCTOR_DASHBOARD_PERMISSIONS
        permission_name = permissions.VIEW_DASHBOARD

Case 2: Different permissions per HTTP method
=============================================

Use a mapping when reading and changing need different permissions. Each value is a collection of permissions. A method missing from the mapping is denied (``403``) with ``authz.enable_course_authoring`` enabled for the course, so a method is never left on the legacy rule by mistake. With it disabled, the legacy check decides as usual:

.. code-block:: python

    class MyView(APIView):
        permission_classes = (IsAuthenticated, InstructorAuthzPermission)
        authz_permissions = {
            "GET": (COURSES_VIEW_ENROLLMENTS.identifier,),
            "POST": (COURSES_ENROLL_LEARNERS.identifier,),
        }
        permission_name = permissions.CAN_ENROLL

Case 3: Legacy rule that is not a bridgekeeper permission
=========================================================

Define ``legacy_permission_check(user, course_key)`` on the view. It replaces ``permission_name`` and receives the same arguments for every method. Reuse the checks of ``legacy.py`` when one matches:

.. code-block:: python

    from lms.djangoapps.instructor.authz import legacy as authz_legacy

    class MyView(APIView):
        permission_classes = (IsAuthenticated, InstructorAuthzPermission)
        authz_permissions = {
            "GET": (COURSES_VIEW_COHORTS.identifier,),
            "POST": (COURSES_MANAGE_COHORTS.identifier,),
        }

        @staticmethod
        def legacy_permission_check(user, course_key):
            return authz_legacy.has_cohort_manage_access(user, course_key)

Without ``@staticmethod`` the method would receive ``self`` as the first argument. The legacy check does not receive the request, so it cannot vary by HTTP method. If the legacy rule differs per method, call ``user_has_instructor_permission`` in the handler with the legacy check of each case.

Checking a permission in other code
===================================

Use ``user_has_instructor_permission`` inside a handler, a serializer or any other code. The legacy check is optional and defaults to the one registered in ``legacy.LEGACY_PERMISSION_CHECKS``:

.. code-block:: python

    from opaque_keys.edx.keys import CourseKey
    from lms.djangoapps.instructor.authz.api import user_has_instructor_permission

    def post(self, request, course_id):
        course_key = CourseKey.from_string(course_id)
        if user_has_instructor_permission(request.user, COURSES_MANAGE_COHORTS.identifier, course_key):
            ...

        # With an explicit legacy check, used only when ``authz.enable_course_authoring`` is disabled.
        user_has_instructor_permission(
            request.user,
            COURSES_VIEW_ENROLLMENTS.identifier,
            course_key,
            authz_legacy.has_instructor_access,
        )

``CourseMetadataView`` returns ``authz_enabled``, whether AuthZ is enabled for the course. The MFE passes it as ``featureEnabled`` to the shared ``usePermissions`` hook of ``frontend-base``, so it checks the permissions through AuthZ only when it is enabled.

Errors
======

- No ``authz_permissions`` on the view, or a bare string instead of a collection: ``ImproperlyConfigured``.
- Invalid ``course_id``: ``404``.
- Denied by the active path (AuthZ with ``authz.enable_course_authoring`` enabled, legacy otherwise): ``403``.
