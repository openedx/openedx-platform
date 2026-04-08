Open edX ADR 0026: Standardize Permission Classes Across APIs
=============================================================

:Status: Proposed
:Date: 2026-03-18
:Deciders: API Working Group
:Technical Story: Open edX REST API Standards - Permission standardization for security consistency

Context
-------

Permissions are inconsistently applied across Open edX apps using custom decorators, inline role-based checks, and embedded authorization logic within views. This creates security gaps, makes it difficult for external systems to reliably determine access, and leads to duplicate authorization logic across multiple views.

Decision
--------

We will standardize all Open edX REST APIs to use **DRF permission_classes** as the primary authorization mechanism.

This ADR standardizes the **DRF integration surface** for authorization, not the underlying policy engine. DRF
permission classes may delegate to legacy authorization checks or newer policy engines (such as Casbin) during
phased migrations.

Implementation requirements:

* Use DRF permission_classes for all authorization logic instead of custom decorators.
* Create reusable permission classes for common authorization patterns (course staff, global staff, etc.).
* Replace inline role-based checks with explicit permission classes.
* Ensure permission classes are properly documented and tested.
* Maintain consistent permission patterns across similar endpoint types.
* Keep the permission backend pluggable so DRF endpoints can migrate from legacy checks to newer policy engines
  without changing endpoint-level authorization structure.

Relevance in edx-platform
-------------------------

Current patterns that should be migrated:

* **Enrollment API** (``/api/enrollment/v1/enrollment/{username},{course_id}``) uses custom inline role checks.
* **User Tours API** (``/api/user_tours/v1/{username}``) mixes inline checks and permission_classes.
* **Course orphan endpoints** (``^orphan/{settings.COURSE_KEY_PATTERN}$``) use functional views with inline permission logic.

Code example (target permission usage)
--------------------------------------

**Example permission classes and APIView using DRF best practices:**

.. code-block:: python

   # permissions.py
   from rest_framework.permissions import BasePermission

   class IsCourseStaff(BasePermission):
       """
       Allows access only to course staff members.
       """
       def has_permission(self, request, view):
           return request.user.is_authenticated and request.user.is_staff

   class IsEnrollmentOwnerOrStaff(BasePermission):
       """
       Allows access to enrollment data for the user themselves or course staff.
       """
       def has_object_permission(self, request, view, obj):
           return (
               obj.user == request.user or
               request.user.is_staff or
               request.user.has_perm('course_staff', obj.course)
           )

   # views.py
   from rest_framework.views import APIView
   from rest_framework.response import Response
   from rest_framework.permissions import IsAuthenticated
   from .permissions import IsCourseStaff

   class EnrollmentAPIView(APIView):
       permission_classes = [IsAuthenticated, IsCourseStaff]

       def get(self, request):
           return Response({"detail": "Access granted"})

Consequences
------------

Positive
~~~~~~~~

* Improves security consistency across all APIs.
* Enhances predictability for external integrations.
* Ensures reusable, testable authorization logic.
* Simplifies security audits and permission reviews.
* Enables centralized permission management.
* Supports backend evolution (legacy checks to Casbin or other engines) without changing DRF endpoint contracts.

Negative / Trade-offs
~~~~~~~~~~~~~~~~~~~~~

* Requires refactoring existing views with inline permission logic.
* May need to create custom permission classes for complex authorization scenarios.
* Initial development effort to identify and standardize permission patterns.
* Requires careful bridging during migration so permission classes and underlying engines stay behaviorally
  compatible while feature flags are in use.

Alternatives Considered
-----------------------

* **Keep mixed permission approaches**: rejected due to security inconsistencies and maintenance burden.
* **Use only decorators**: rejected because DRF permission_classes provide better integration with the framework.

Rollout Plan
------------

1. Audit existing endpoints to identify inconsistent permission patterns.
2. Create a library of standard permission classes for common use cases.
3. Migrate high-security endpoints first (enrollment, user data, course management).
4. Add comprehensive tests for permission classes and their usage.
5. Update API documentation to clearly specify permission requirements.

References
----------

* Open edX REST API Standards: "Permissions" recommendations for security consistency.
